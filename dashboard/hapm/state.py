"""Per-profile HAPM state/lock record (``hapm.lock``).

The lock is a small JSON document written to ``profiles/<profile>/hapm.lock``.
It records what HAPM has done to a profile so it can be rolled back:

* the active preset (if any) and the backup id captured before it was applied,
* the active addons with their chosen mode and the backup id captured before
  each addon was activated,
* enough backup/restore markers to fully revert.

The dataclasses below are intentionally plain: they serialize to/from JSON
verbatim so the on-disk format is human-inspectable and diff-friendly.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HAPM_LOCK_FILENAME = "hapm.lock"

# Bump when the on-disk schema changes in a backward-incompatible way.
LOCK_SCHEMA_VERSION = 1


@dataclass
class AddonState:
    """One active addon recorded in the lock.

    Attributes:
        addon_id: Stable addon identifier (matches the SOUL block marker id).
        mode: The selected addon mode (e.g. ``"prompt"``, ``"off"``, ``"full"``).
        backup_id: Backup captured *before* this addon was activated, used to
            restore the pre-activation state on disable. ``None`` if the addon
            made no destructive change that needed a backup.
        soul_block: Whether this addon contributed a marked SOUL.md block.
        skill_paths: Profile-relative paths (POSIX) of skill files/dirs this
            addon added, tracked so disabling removes exactly what it added.
    """

    addon_id: str
    mode: str
    backup_id: str | None = None
    soul_block: bool = False
    skill_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AddonState":
        return cls(
            addon_id=str(data["addon_id"]),
            mode=str(data.get("mode", "")),
            backup_id=data.get("backup_id"),
            soul_block=bool(data.get("soul_block", False)),
            skill_paths=list(data.get("skill_paths", []) or []),
        )


@dataclass
class LockState:
    """The full per-profile HAPM state record.

    Attributes:
        profile: Profile name (folder name under ``profiles/``).
        active_preset: Name of the applied preset, or ``None``.
        preset_backup_id: Backup captured before the preset was applied.
        addons: Active addons keyed by ``addon_id`` (kept as a list for stable
            ordering / clean diffs).
        schema_version: On-disk schema version.
    """

    profile: str
    active_preset: str | None = None
    preset_backup_id: str | None = None
    addons: list[AddonState] = field(default_factory=list)
    schema_version: int = LOCK_SCHEMA_VERSION

    # -- addon helpers -------------------------------------------------

    def get_addon(self, addon_id: str) -> AddonState | None:
        for a in self.addons:
            if a.addon_id == addon_id:
                return a
        return None

    def set_addon(self, addon: AddonState) -> None:
        """Insert or replace an addon entry (by ``addon_id``)."""
        for i, a in enumerate(self.addons):
            if a.addon_id == addon.addon_id:
                self.addons[i] = addon
                return
        self.addons.append(addon)

    def remove_addon(self, addon_id: str) -> AddonState | None:
        for i, a in enumerate(self.addons):
            if a.addon_id == addon_id:
                return self.addons.pop(i)
        return None

    @property
    def is_active(self) -> bool:
        """True if HAPM currently manages any state on this profile."""
        return self.active_preset is not None or bool(self.addons)

    # -- serialization -------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "active_preset": self.active_preset,
            "preset_backup_id": self.preset_backup_id,
            "addons": [asdict(a) for a in self.addons],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LockState":
        return cls(
            profile=str(data.get("profile", "")),
            active_preset=data.get("active_preset"),
            preset_backup_id=data.get("preset_backup_id"),
            addons=[AddonState.from_dict(a) for a in data.get("addons", []) or []],
            schema_version=int(data.get("schema_version", LOCK_SCHEMA_VERSION)),
        )


def lock_path(profile_dir: str | os.PathLike[str]) -> Path:
    """Return the ``hapm.lock`` path inside a profile directory."""
    return Path(profile_dir) / HAPM_LOCK_FILENAME


def read_lock(profile_dir: str | os.PathLike[str]) -> LockState | None:
    """Read the lock for a profile, or ``None`` if no lock exists.

    Raises:
        ValueError: if the lock file exists but is not valid JSON.
    """
    path = lock_path(profile_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt hapm.lock at {path}: {exc}") from exc
    return LockState.from_dict(data)


def write_lock(profile_dir: str | os.PathLike[str], state: LockState) -> Path:
    """Atomically write the lock for a profile and return its path.

    If the state is not active (no preset, no addons) the lock file is removed
    instead of being written, so a fully-reverted profile leaves no residue.
    """
    path = lock_path(profile_dir)
    if not state.is_active:
        if path.exists():
            path.unlink()
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return path
