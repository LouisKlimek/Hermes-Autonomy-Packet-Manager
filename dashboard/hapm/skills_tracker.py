"""Addon skill tracking with shadow-safe add/remove (FR-7).

When an addon contributes skills into ``profiles/<profile>/skills/`` we must be
able to disable the addon later and remove *exactly* what it added — while
restoring any pre-existing same-named skill it may have shadowed.

Strategy:
* :func:`add_addon_skills` copies each contributed skill (file or directory)
  into the profile's ``skills/`` tree. For every destination path it records
  whether that path was *newly created* by the addon or *shadowed* an existing
  file/dir. Shadowed originals are backed up (via a
  :class:`~hapm.backup.BackupStore`) before overwrite.
* :func:`remove_addon_skills` deletes the paths the addon created and, for
  shadowed paths, relies on the backup restore to bring the original back. It
  removes only tracked paths, never sibling user content.

The :class:`SkillContribution` returned by ``add_addon_skills`` is what a caller
persists in the addon's :class:`~hapm.state.AddonState` (``skill_paths`` plus
the shadow backup id), so removal is fully deterministic.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .backup import BackupStore

SKILLS_DIRNAME = "skills"


class SkillTrackError(Exception):
    """Raised when a skill add/remove cannot be completed safely."""


@dataclass
class SkillContribution:
    """Record of skills an addon added to a profile.

    Attributes:
        added_paths: Profile-relative POSIX paths that the addon newly created
            (removed verbatim on disable).
        shadowed_paths: Profile-relative POSIX paths that already existed and
            were overwritten (restored from ``shadow_backup_id`` on disable).
        shadow_backup_id: Backup id capturing the shadowed originals, or
            ``None`` if nothing was shadowed.
    """

    added_paths: list[str] = field(default_factory=list)
    shadowed_paths: list[str] = field(default_factory=list)
    shadow_backup_id: str | None = None

    @property
    def all_paths(self) -> list[str]:
        return list(self.added_paths) + list(self.shadowed_paths)

    def to_dict(self) -> dict:
        return {
            "added_paths": list(self.added_paths),
            "shadowed_paths": list(self.shadowed_paths),
            "shadow_backup_id": self.shadow_backup_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "SkillContribution":
        return cls(
            added_paths=list(data.get("added_paths", []) or []),
            shadowed_paths=list(data.get("shadowed_paths", []) or []),
            shadow_backup_id=data.get("shadow_backup_id"),
        )


def _rel(profile_dir: Path, path: Path) -> str:
    return path.relative_to(profile_dir).as_posix()


def add_addon_skills(
    profile_dir: str | os.PathLike[str],
    contributions: Mapping[str, str | os.PathLike[str]],
    backup_store: BackupStore | None = None,
) -> SkillContribution:
    """Copy addon-contributed skills into a profile's ``skills/`` tree.

    Args:
        profile_dir: The target profile directory.
        contributions: Mapping of ``skills/``-relative destination name to the
            source file/dir path to copy in. E.g.
            ``{"yagni": "/registry/addons/yagni/skills/yagni"}`` places the
            skill at ``profiles/<p>/skills/yagni``.
        backup_store: Store used to back up shadowed originals. If omitted a
            default store rooted at ``profile_dir`` is used.

    Returns:
        A :class:`SkillContribution` describing exactly what changed, for later
        deterministic removal.
    """
    profile = Path(profile_dir).resolve()
    skills_root = profile / SKILLS_DIRNAME
    store = backup_store or BackupStore(profile)

    # Determine which destinations already exist (would be shadowed).
    dests: dict[str, Path] = {}
    shadowed_rel: list[str] = []
    added_rel: list[str] = []
    for dest_name, src in contributions.items():
        dest = (skills_root / dest_name).resolve()
        if skills_root != dest and skills_root not in dest.parents:
            raise SkillTrackError(
                f"skill destination escapes skills dir: {dest_name!r}"
            )
        if not Path(src).exists():
            raise SkillTrackError(f"skill source does not exist: {src}")
        dests[dest_name] = dest
        rel = _rel(profile, dest)
        if dest.exists():
            shadowed_rel.append(rel)
        else:
            added_rel.append(rel)

    # Back up shadowed originals in one snapshot before any overwrite.
    shadow_backup_id: str | None = None
    if shadowed_rel:
        shadow_backup_id = store.create(shadowed_rel)

    # Copy everything in.
    skills_root.mkdir(parents=True, exist_ok=True)
    for dest_name, src in contributions.items():
        dest = dests[dest_name]
        src_path = Path(src)
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        elif dest.exists() or dest.is_symlink():
            dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src_path.is_dir():
            shutil.copytree(src_path, dest, symlinks=True)
        else:
            shutil.copy2(src_path, dest, follow_symlinks=False)

    return SkillContribution(
        added_paths=added_rel,
        shadowed_paths=shadowed_rel,
        shadow_backup_id=shadow_backup_id,
    )


def remove_addon_skills(
    profile_dir: str | os.PathLike[str],
    contribution: SkillContribution,
    backup_store: BackupStore | None = None,
) -> None:
    """Undo :func:`add_addon_skills` for one addon.

    Newly-added paths are deleted; shadowed paths are restored from the shadow
    backup so the pre-existing same-named skill is preserved. Only tracked
    paths are touched.
    """
    profile = Path(profile_dir).resolve()
    store = backup_store or BackupStore(profile)

    # Delete addon-created paths.
    for rel in contribution.added_paths:
        target = (profile / rel).resolve()
        if profile != target and profile not in target.parents:
            raise SkillTrackError(f"tracked path escapes profile: {rel!r}")
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()

    # Restore shadowed originals.
    if contribution.shadowed_paths:
        if not contribution.shadow_backup_id:
            raise SkillTrackError(
                "contribution has shadowed paths but no shadow_backup_id"
            )
        store.restore(contribution.shadow_backup_id)
