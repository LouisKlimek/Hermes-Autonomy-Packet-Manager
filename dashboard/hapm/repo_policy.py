"""Canonical, default-deny repository policy for the GitHub addon.

The policy is deliberately outside profile directories at
``$HERMES_HOME/hapm_policies/repo_allowlist.json``.  It contains repository
names only; credentials and profile environment values are never read here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable

POLICY_VERSION = 1
POLICY_DIRNAME = "hapm_policies"
POLICY_FILENAME = "repo_allowlist.json"
GITHUB_ADDON_ID = "github-agent"
_REPOSITORY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_LEGACY_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)(?![A-Za-z0-9_.-])"
)


class RepositoryPolicyError(ValueError):
    """A policy file or requested repository name is invalid."""


def canonical_repository(repository: str) -> str:
    """Validate and return an exact ``owner/repository`` identifier."""
    if not isinstance(repository, str) or not _REPOSITORY_RE.fullmatch(repository):
        raise RepositoryPolicyError(
            "repository must be a canonical owner/repository identifier"
        )
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


def _atomic_write(path: Path, repositories: Iterable[str]) -> list[str]:
    values = sorted({canonical_repository(x) for x in repositories})
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": POLICY_VERSION, "repositories": values}, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise RepositoryPolicyError(f"unable to write repository policy: {path}") from exc
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
    """Return false unless the profile has GitHub enabled and the repo is listed."""
    try:
        return (
            profile_is_github_enabled(profile_dir)
            and canonical_repository(repository) in list_repositories(policy_path)
        )
    except Exception:  # fail closed if an untrusted profile lock is malformed
        return False


def migrate_legacy_allowlists(policy_path: str | Path, legacy_sources: Iterable[str | Path]) -> dict:
    """Idempotently seed the policy from legacy prose, preserving a rollback copy.

    Legacy source paths are recorded only as paths and SHA-256 digests; their
    contents are neither persisted nor returned.  A pre-existing policy is
    copied once to ``.bak`` before its first migration mutation.
    """
    policy = Path(policy_path)
    existing = list_repositories(policy)
    discovered: set[str] = set()
    source_records = []
    for source in legacy_sources:
        path = Path(source)
        if not path.is_file():
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="ignore")
        discovered.update(_LEGACY_RE.findall(text))
        source_records.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
    merged = sorted(set(existing) | discovered)
    changed = merged != existing
    backup = policy.with_suffix(policy.suffix + ".bak")
    if changed and policy.exists() and not backup.exists():
        backup.write_bytes(policy.read_bytes())
        os.chmod(backup, 0o600)
    if changed or not policy.exists():
        _atomic_write(policy, merged)
    return {"repositories": merged, "changed": changed, "backup": str(backup) if backup.exists() else None, "sources": source_records}
