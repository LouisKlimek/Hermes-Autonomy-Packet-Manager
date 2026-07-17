"""Restricted, value-safe per-profile ``.env`` management for HAPM.

The dashboard may obtain only field names, presence, and masking state. Values,
including ``GH_TOKEN``, stay inside the profile file and encrypted rollback
artifacts; no return type, audit entry, or exception includes a value.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*?)(\r?\n)?$")
_ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_SECRET_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "PRIVATE_KEY")
GITHUB_ADDON_ID = "github-agent"
GIT_IDENTITY_KEYS = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)
GIT_IDENTITY_DEFAULTS = {
    "ceo-orchestrator": {
        "GIT_AUTHOR_NAME": "Hermes CEO Orchestrator",
        "GIT_AUTHOR_EMAIL": "ceo-orchestrator@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Hermes CEO Orchestrator",
        "GIT_COMMITTER_EMAIL": "ceo-orchestrator@users.noreply.github.com",
    },
    "fullstack-developer": {
        "GIT_AUTHOR_NAME": "Hermes Fullstack Developer",
        "GIT_AUTHOR_EMAIL": "fullstack-developer@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Hermes Fullstack Developer",
        "GIT_COMMITTER_EMAIL": "fullstack-developer@users.noreply.github.com",
    },
    "pr-reviewer": {
        "GIT_AUTHOR_NAME": "Hermes PR Reviewer",
        "GIT_AUTHOR_EMAIL": "pr-reviewer@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Hermes PR Reviewer",
        "GIT_COMMITTER_EMAIL": "pr-reviewer@users.noreply.github.com",
    },
    "github-manager": {
        "GIT_AUTHOR_NAME": "Hermes GitHub Manager",
        "GIT_AUTHOR_EMAIL": "github-manager@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "Hermes GitHub Manager",
        "GIT_COMMITTER_EMAIL": "github-manager@users.noreply.github.com",
    },
}


class ProfileEnvError(ValueError):
    """A profile environment operation could not be safely completed."""


def _fernet():
    key = os.environ.get("HAPM_ENV_BACKUP_KEY", "")
    if not key:
        raise ProfileEnvError("encrypted environment backups require HAPM_ENV_BACKUP_KEY")
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode())
    except ImportError as exc:
        raise ProfileEnvError("encrypted environment backups require the cryptography package") from exc
    except (TypeError, ValueError) as exc:
        raise ProfileEnvError("environment backup encryption key is invalid") from exc


def _is_secret(key: str) -> bool:
    return key == "GH_TOKEN" or any(marker in key for marker in _SECRET_MARKERS)


def _validate_key(key: object) -> str:
    if not isinstance(key, str) or not _ENV_KEY.fullmatch(key):
        raise ProfileEnvError("environment keys must be uppercase shell variable names")
    return key


def _validate_value(value: object) -> str:
    if not isinstance(value, str) or "\x00" in value or "\n" in value or "\r" in value:
        raise ProfileEnvError("environment values must be single-line strings")
    return value


def _env_path(profile_dir: str | Path) -> Path:
    return Path(profile_dir).resolve() / ".env"


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError) as exc:
        raise ProfileEnvError("profile .env could not be read") from exc


def _entries(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        match = _ENV_LINE.match(line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def status(profile_dir: str | Path) -> dict:
    """Return live field metadata only; no environment values are exposed."""
    values = _entries(_read_lines(_env_path(profile_dir)))
    fields = [
        {"key": key, "present": True, "secret": _is_secret(key), "masked": "••••" if _is_secret(key) else None}
        for key in sorted(values)
    ]
    return {"fields": fields, "github_token_present": bool(values.get("GH_TOKEN", ""))}


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise ProfileEnvError("profile .env could not be atomically written") from exc


def _roots(hermes_home: str | Path) -> tuple[Path, Path]:
    root = Path(hermes_home) / "hapm_env"
    backups, audits = root / "backups", root / "audit"
    for directory in (root, backups, audits):
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    return backups, audits


def _backup(hermes_home: str | Path, profile: str, prior: bytes) -> str:
    backups, _ = _roots(hermes_home)
    backup_id = uuid.uuid4().hex
    payload = json.dumps(
        {"profile": profile, "env": base64.b64encode(prior).decode("ascii")}, separators=(",", ":")
    ).encode()
    target = backups / f"{backup_id}.fernet"
    _atomic_write(target, _fernet().encrypt(payload))
    return backup_id


def _audit(hermes_home: str | Path, actor: str, profile: str, action: str, keys: list[str], backup_id: str) -> None:
    _, audits = _roots(hermes_home)
    record = {
        "id": uuid.uuid4().hex,
        "at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "profile": profile,
        "action": action,
        "keys": sorted(keys),
        "backup_id": backup_id,
    }
    # A separate write-once value-free record avoids a mutable aggregate log.
    target = audits / f"{record['id']}.json"
    _atomic_write(target, (json.dumps(record, separators=(",", ":")) + "\n").encode())
    os.chmod(target, 0o400)


def update(profile_dir: str | Path, hermes_home: str | Path, profile: str, actor: str, updates: Mapping[str, object]) -> dict:
    """Atomically apply explicit fields and retain an encrypted rollback snapshot."""
    if not isinstance(updates, Mapping) or not updates:
        raise ProfileEnvError("at least one environment field is required")
    normalized = {_validate_key(key): _validate_value(value) for key, value in updates.items()}
    path = _env_path(profile_dir)
    prior = path.read_bytes() if path.exists() else b""
    lines = _read_lines(path)
    remaining = set(normalized)
    rendered: list[str] = []
    for line in lines:
        match = _ENV_LINE.match(line)
        if match and match.group(1) in normalized:
            key = match.group(1)
            rendered.append(f"{key}={normalized[key]}\n")
            remaining.remove(key)
        else:
            rendered.append(line)
    if rendered and not rendered[-1].endswith(("\n", "\r")):
        rendered[-1] += "\n"
    rendered.extend(f"{key}={normalized[key]}\n" for key in sorted(remaining))
    backup_id = _backup(hermes_home, profile, prior)
    try:
        _atomic_write(path, "".join(rendered).encode("utf-8"))
        _audit(hermes_home, actor, profile, "update", list(normalized), backup_id)
    except Exception:
        # A failed audit is a failed mutation; restore the exact original bytes.
        _atomic_write(path, prior)
        raise
    return {"updated": sorted(normalized), "backup_id": backup_id, **status(profile_dir)}


def rollback(profile_dir: str | Path, hermes_home: str | Path, profile: str, actor: str, backup_id: object) -> dict:
    if not isinstance(backup_id, str) or not re.fullmatch(r"[0-9a-f]{32}", backup_id):
        raise ProfileEnvError("invalid environment backup id")
    backups, _ = _roots(hermes_home)
    backup = backups / f"{backup_id}.fernet"
    try:
        stored = json.loads(_fernet().decrypt(backup.read_bytes()).decode())
        if stored.get("profile") != profile:
            raise ProfileEnvError("environment backup belongs to a different profile")
        prior = base64.b64decode(stored["env"], validate=True)
    except ProfileEnvError:
        raise
    except Exception as exc:
        raise ProfileEnvError("environment backup cannot be restored") from exc
    path = _env_path(profile_dir)
    before = path.read_bytes() if path.exists() else b""
    current_backup = _backup(hermes_home, profile, before)
    try:
        _atomic_write(path, prior)
        _audit(hermes_home, actor, profile, "rollback", [], backup_id)
    except Exception:
        _atomic_write(path, before)
        raise
    return {"restored_backup_id": backup_id, "rollback_backup_id": current_backup, **status(profile_dir)}


def initialize_github(profile_dir: str | Path, hermes_home: str | Path, profile: str, actor: str) -> dict:
    """Check token presence and add only missing approved Git identity fields."""
    if profile not in GIT_IDENTITY_DEFAULTS:
        raise ProfileEnvError("GitHub onboarding is not approved for this profile")
    existing = _entries(_read_lines(_env_path(profile_dir)))
    missing = {key: value for key, value in GIT_IDENTITY_DEFAULTS[profile].items() if key not in existing}
    result = status(profile_dir)
    if missing:
        result = update(profile_dir, hermes_home, profile, actor, missing)
    return {"github_token_present": result["github_token_present"], "initialized": sorted(missing), "fields": result["fields"]}
