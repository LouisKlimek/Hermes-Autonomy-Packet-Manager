"""Canonical, default-deny repository policy and legacy GitHub reconciliation.

The policy contains repository identifiers only.  GitHub operations must call
``require_repository_allowed`` before resolving or contacting GitHub.  Legacy
migration is explicit: only approved repositories found in active deprecated
addon state are copied into the canonical policy.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Iterable

POLICY_VERSION = 1
POLICY_DIRNAME = "hapm_policies"
POLICY_FILENAME = "repo_allowlist.json"
GITHUB_ADDON_ID = "github-agent"
LEGACY_GITHUB_ADDON_IDS = frozenset({
    "repository-scope",
    "github-pr-automation",
    "github-automerge-governance",
    "github-manager-repository-scope",
})
_REPOSITORY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_LEGACY_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)(?![A-Za-z0-9_.-])"
)


class RepositoryPolicyError(ValueError):
    """A policy, authorization request, or migration state is invalid."""


class RepositoryNotAllowedError(RepositoryPolicyError):
    """A GitHub operation targeted a repository denied by policy."""


def canonical_repository(repository: str) -> str:
    """Validate and return an exact ``owner/repository`` identifier."""
    if not isinstance(repository, str) or not _REPOSITORY_RE.fullmatch(repository):
        raise RepositoryPolicyError("repository must be a canonical owner/repository identifier")
    return repository


def default_policy_path(hermes_home: str | Path) -> Path:
    return Path(hermes_home) / POLICY_DIRNAME / POLICY_FILENAME


def _decode(path: Path) -> list[str]:
    if not path.exists():
        return []  # Missing is intentionally default-deny.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepositoryPolicyError(f"invalid repository policy: {path}") from exc
    if not isinstance(data, dict) or data.get("version") != POLICY_VERSION:
        raise RepositoryPolicyError("repository policy has an unsupported schema")
    repositories = data.get("repositories")
    if not isinstance(repositories, list) or not all(isinstance(x, str) for x in repositories):
        raise RepositoryPolicyError("repository policy repositories must be a string list")
    normalized = [canonical_repository(x) for x in repositories]
    if len(set(normalized)) != len(normalized):
        raise RepositoryPolicyError("repository policy may not contain duplicates")
    return sorted(normalized)


def list_repositories(path: str | Path) -> list[str]:
    return _decode(Path(path))


def _atomic_bytes(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise RepositoryPolicyError(f"unable to write repository policy: {path}") from exc


def _atomic_write(path: Path, repositories: Iterable[str]) -> list[str]:
    values = sorted({canonical_repository(x) for x in repositories})
    payload = (json.dumps({"version": POLICY_VERSION, "repositories": values}, indent=2) + "\n").encode()
    _atomic_bytes(path, payload)
    return values


def replace_repositories(path: str | Path, repositories: Iterable[str]) -> list[str]:
    """Replace the allowlist after validation; an empty list remains default-deny."""
    return _atomic_write(Path(path), repositories)


def add_repository(path: str | Path, repository: str) -> list[str]:
    policy = Path(path)
    return _atomic_write(policy, [*list_repositories(policy), canonical_repository(repository)])


def remove_repository(path: str | Path, repository: str) -> list[str]:
    policy = Path(path)
    wanted = canonical_repository(repository)
    return _atomic_write(policy, [r for r in list_repositories(policy) if r != wanted])


def profile_is_github_enabled(profile_dir: str | Path) -> bool:
    """Only profiles with the unified addon can consume the central policy."""
    from .toggle import list_active_addons  # avoid a package import cycle

    return any(a.addon_id == GITHUB_ADDON_ID for a in list_active_addons(profile_dir))


def is_repository_allowed(profile_dir: str | Path, policy_path: str | Path, repository: str) -> bool:
    """Return false unless unified GitHub is enabled and the repo is listed."""
    try:
        return profile_is_github_enabled(profile_dir) and canonical_repository(repository) in list_repositories(policy_path)
    except Exception:  # fail closed for malformed profile lock or policy
        return False


def require_repository_allowed(profile_dir: str | Path, policy_path: str | Path, repository: str) -> str:
    """Fail closed before a runtime GitHub enablement/resolution/API operation."""
    canonical = canonical_repository(repository)
    if not is_repository_allowed(profile_dir, policy_path, canonical):
        raise RepositoryNotAllowedError(f"GitHub access to {canonical!r} is not authorized for this profile")
    return canonical


def _legacy_repositories(sources: Iterable[Path]) -> set[str]:
    discovered: set[str] = set()
    for source in sources:
        if source.is_file():
            discovered.update(_LEGACY_RE.findall(source.read_text(encoding="utf-8", errors="ignore")))
    return discovered


def migrate_legacy_allowlists(policy_path: str | Path, legacy_sources: Iterable[str | Path]) -> dict:
    """Compatibility-only policy seeding; reconciliation should be used at runtime.

    This keeps the former public helper non-destructive.  It never creates an
    unencrypted rollback copy; callers needing profile migration use
    ``reconcile_legacy_github_addons`` with an encryption key.
    """
    policy = Path(policy_path)
    existing = list_repositories(policy)
    discovered = _legacy_repositories(Path(source) for source in legacy_sources)
    merged = sorted(set(existing) | discovered)
    changed = merged != existing or not policy.exists()
    if changed:
        _atomic_write(policy, merged)
    return {"repositories": merged, "changed": changed, "sources": len(discovered)}


def _fernet(key: str | bytes):
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # fail closed: no plaintext fallback exists
        raise RepositoryPolicyError("encrypted rollback requires the cryptography package") from exc
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (TypeError, ValueError) as exc:
        raise RepositoryPolicyError("rollback encryption key is invalid") from exc


def _capture_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): base64.b64encode(path.read_bytes()).decode("ascii")
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _restore_tree(root: Path, files: dict[str, str]) -> None:
    if root.exists():
        shutil.rmtree(root)
    if not files:
        return
    for relative, encoded in files.items():
        destination = (root / relative).resolve()
        if root.resolve() not in destination.parents:
            raise RepositoryPolicyError("encrypted rollback contains an invalid path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_bytes(destination, base64.b64decode(encoded))


def _create_encrypted_backup(policy: Path, profiles: Iterable[Path], key: str | bytes) -> tuple[str, Path]:
    state = {
        "policy": base64.b64encode(policy.read_bytes()).decode("ascii") if policy.exists() else None,
        "profiles": {
            profile.name: {
                "SOUL.md": base64.b64encode((profile / "SOUL.md").read_bytes()).decode("ascii") if (profile / "SOUL.md").exists() else None,
                "hapm.lock": base64.b64encode((profile / "hapm.lock").read_bytes()).decode("ascii") if (profile / "hapm.lock").exists() else None,
                ".hapm": _capture_tree(profile / ".hapm"),
            }
            for profile in profiles
        },
    }
    backup_id = uuid.uuid4().hex
    backup = policy.parent / "rollback_backups" / f"{backup_id}.fernet"
    _atomic_bytes(backup, _fernet(key).encrypt(json.dumps(state, separators=(",", ":")).encode()))
    return backup_id, backup


def _restore_encrypted_backup(policy: Path, profiles: dict[str, Path], backup: Path, key: str | bytes) -> None:
    try:
        state = json.loads(_fernet(key).decrypt(backup.read_bytes()).decode())
    except Exception as exc:  # ciphertext/key errors must not continue migration
        raise RepositoryPolicyError("encrypted rollback backup cannot be restored") from exc
    policy_data = state.get("policy")
    if policy_data is None:
        policy.unlink(missing_ok=True)
    else:
        _atomic_bytes(policy, base64.b64decode(policy_data))
    for name, captured in state.get("profiles", {}).items():
        profile = profiles[name]
        for filename in ("SOUL.md", "hapm.lock"):
            data = captured.get(filename)
            destination = profile / filename
            if data is None:
                destination.unlink(missing_ok=True)
            else:
                _atomic_bytes(destination, base64.b64decode(data))
        _restore_tree(profile / ".hapm", captured.get(".hapm", {}))


def reconcile_legacy_github_addons(
    policy_path: str | Path,
    profiles: Iterable[str | Path],
    addons_root: str | Path,
    approved_repositories: Iterable[str],
    rollback_key: str | bytes,
) -> dict:
    """Atomically replace active legacy GitHub addons with ``github-agent``.

    Only explicitly approved repositories found in active deprecated addon
    sources are migrated.  A new encrypted, owner-only backup is created before
    every first or changed reconciliation; any failure restores policy and all
    affected profile state from that backup.
    """
    from .toggle import disable_addon, enable_addon

    policy = Path(policy_path)
    addon_root = Path(addons_root)
    profile_paths = [Path(item).resolve() for item in profiles]
    if len({profile.name for profile in profile_paths}) != len(profile_paths):
        raise RepositoryPolicyError("profile names must be unique for reconciliation")
    approved = {canonical_repository(repo) for repo in approved_repositories}
    active: dict[Path, list[str]] = {}
    source_paths: list[Path] = []
    for profile in profile_paths:
        if not profile.is_dir():
            raise RepositoryPolicyError(f"profile directory does not exist: {profile}")
        from .toggle import list_active_addons
        legacy = [state.addon_id for state in list_active_addons(profile) if state.addon_id in LEGACY_GITHUB_ADDON_IDS]
        if legacy:
            active[profile] = legacy
            source_paths.extend(addon_root / addon_id / "soul_block.md" for addon_id in legacy)
    discovered = _legacy_repositories(source_paths)
    explicitly_migrated = sorted(discovered & approved)
    existing = list_repositories(policy)
    desired = sorted(set(existing) | set(explicitly_migrated))
    needs_policy = desired != existing or not policy.exists()
    needs_profiles = bool(active) or any(not profile_is_github_enabled(profile) for profile in profile_paths)
    if not needs_policy and not needs_profiles:
        return {"changed": False, "repositories": desired, "migrated_profiles": [], "inventory": sorted(discovered), "backup_id": None}

    backup_id, backup = _create_encrypted_backup(policy, profile_paths, rollback_key)
    profile_map = {profile.name: profile for profile in profile_paths}
    migrated: list[str] = []
    try:
        if needs_policy:
            _atomic_write(policy, desired)
        for profile in profile_paths:
            for addon_id in active.get(profile, []):
                disable_addon(profile, addon_root / addon_id)
            if not profile_is_github_enabled(profile):
                enable_addon(profile, addon_root / GITHUB_ADDON_ID, target=profile.name, on_conflict="raise")
            migrated.append(profile.name)
    except Exception as exc:
        _restore_encrypted_backup(policy, profile_map, backup, rollback_key)
        raise RepositoryPolicyError("legacy GitHub reconciliation failed; encrypted rollback restored prior state") from exc
    return {"changed": True, "repositories": desired, "migrated_profiles": migrated, "inventory": sorted(discovered), "backup_id": backup_id, "backup_path": str(backup)}
