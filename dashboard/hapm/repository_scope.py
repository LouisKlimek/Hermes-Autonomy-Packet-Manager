"""Shared, editable repository allowlist for the Repository Scope addon.

The setting lives once under ``$HERMES_HOME`` and is rendered into every profile
where the addon is active.  It deliberately updates only HAPM-owned marker
blocks, never arbitrary profile text.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .soul_blocks import has_addon_block, upsert_addon_block
from .state import read_lock

ADDON_ID = "repository-scope"
SETTINGS_FILENAME = "hapm_repository_scope.json"
DEFAULT_REPOSITORIES = [
    "LouisKlimek/Hermes-Tasklist-Plugin",
    "LouisKlimek/Hermes-Autonomy-Packet-Manager",
]
_REPOSITORY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")


class RepositoryScopeError(ValueError):
    """Raised when a repository-scope request is malformed or cannot be applied."""


def settings_path(hermes_home: Path) -> Path:
    return hermes_home / SETTINGS_FILENAME


def validate_repositories(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RepositoryScopeError("'repositories' must be a non-empty list of GitHub owner/repository names.")
    if len(value) > 100:
        raise RepositoryScopeError("At most 100 repositories may be allowed.")
    repositories: list[str] = []
    for raw in value:
        repo = raw.strip() if isinstance(raw, str) else ""
        if not _REPOSITORY_RE.fullmatch(repo):
            raise RepositoryScopeError(
                f"Invalid repository {raw!r}; use a GitHub owner/repository name."
            )
        if repo not in repositories:
            repositories.append(repo)
    return repositories


def load_repositories(hermes_home: Path) -> list[str]:
    path = settings_path(hermes_home)
    if not path.exists():
        return list(DEFAULT_REPOSITORIES)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepositoryScopeError(f"Could not read repository scope settings: {exc}") from exc
    return validate_repositories(data.get("repositories") if isinstance(data, dict) else None)


def render_soul_block(repositories: list[str]) -> str:
    lines = ["## Repository Scope", "", "Work only in these repositories:", ""]
    lines.extend(f"- `{repository}`" for repository in repositories)
    lines.extend(["", "Block work in any other repository unless explicitly authorized.", ""])
    return "\n".join(lines)


def _write_settings(hermes_home: Path, repositories: list[str]) -> None:
    path = settings_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"repositories": repositories}, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def update_repositories(hermes_home: Path, profiles_dir: Path, value: object) -> dict:
    """Persist an allowlist and synchronize every active Repository Scope addon.

    Writes are all-or-nothing for the profile SOUL files: any failed write rolls
    back the files already updated, and settings are saved only after sync works.
    """
    repositories = validate_repositories(value)
    content = render_soul_block(repositories)
    updates: list[tuple[Path, str, str]] = []
    try:
        entries = sorted(profiles_dir.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise RepositoryScopeError(f"Could not read profiles directory: {exc}") from exc

    for profile_dir in entries:
        if not profile_dir.is_dir():
            continue
        try:
            lock = read_lock(profile_dir)
        except Exception as exc:  # corrupt locks must not be silently ignored
            raise RepositoryScopeError(f"Could not read HAPM state for {profile_dir.name!r}: {exc}") from exc
        if lock is None or lock.get_addon(ADDON_ID) is None:
            continue
        soul_path = profile_dir / "SOUL.md"
        try:
            old = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
        except OSError as exc:
            raise RepositoryScopeError(f"Could not read SOUL.md for {profile_dir.name!r}: {exc}") from exc
        if not has_addon_block(old, ADDON_ID):
            raise RepositoryScopeError(
                f"Repository Scope is active for {profile_dir.name!r} but its managed SOUL block is missing."
            )
        updates.append((soul_path, old, upsert_addon_block(old, ADDON_ID, content)))

    written: list[tuple[Path, str]] = []
    try:
        for path, old, new in updates:
            path.write_text(new, encoding="utf-8")
            written.append((path, old))
        _write_settings(hermes_home, repositories)
    except OSError as exc:
        for path, old in reversed(written):
            try:
                path.write_text(old, encoding="utf-8")
            except OSError:
                pass
        raise RepositoryScopeError(f"Could not save repository scope settings: {exc}") from exc

    return {"repositories": repositories, "updated_profiles": [path.parent.name for path, _, _ in updates]}
