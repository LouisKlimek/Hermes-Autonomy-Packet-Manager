"""Backup/restore primitives for HAPM reversibility (FR-7).

Before HAPM overwrites any managed artifact in a profile (``SOUL.md``, the
``skills/`` tree, or ``config.yaml``), the prior content is captured into a
backup so it can later be restored *byte-exactly*. Backups live under
``profiles/<profile>/.hapm/backups/<backup_id>/`` and are self-describing via a
``manifest.json`` so a restore needs only the backup id.

Design notes:
* A backup captures a snapshot of a set of *targets*. Each target is either a
  single file (e.g. ``SOUL.md``) or a directory (e.g. ``skills/``).
* File bytes are copied verbatim (``shutil.copy2`` preserves mtime/mode).
* Restore recreates the exact target state at snapshot time, including deleting
  files that did not exist in the backup (so a revert is a true rollback, not a
  merge). A target that did not exist at backup time is recorded as absent and
  removed on restore.
* Backup ids are timestamp + short random suffix so multiple backups of the
  same profile never collide and sort chronologically.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

BACKUP_DIRNAME = ".hapm"
BACKUPS_SUBDIR = "backups"
MANIFEST_FILENAME = "manifest.json"
PAYLOAD_SUBDIR = "payload"


class BackupError(Exception):
    """Raised when a backup or restore operation cannot be completed."""


@dataclass
class _TargetRecord:
    """One backed-up target inside a backup manifest.

    Attributes:
        rel: Profile-relative POSIX path of the target.
        kind: ``"file"``, ``"dir"``, or ``"absent"`` (did not exist at backup).
    """

    rel: str
    kind: str


def _backups_root(profile_dir: Path) -> Path:
    return profile_dir / BACKUP_DIRNAME / BACKUPS_SUBDIR


def _new_backup_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"{ts}-{secrets.token_hex(3)}"


class BackupStore:
    """Manages backups for a single profile directory."""

    def __init__(self, profile_dir: str | os.PathLike[str]) -> None:
        self.profile_dir = Path(profile_dir).resolve()

    # -- path helpers --------------------------------------------------

    def _backup_dir(self, backup_id: str) -> Path:
        return _backups_root(self.profile_dir) / backup_id

    def _resolve_target(self, rel: str) -> Path:
        """Resolve a profile-relative path, refusing escapes outside profile."""
        rel_path = Path(rel)
        if rel_path.is_absolute():
            raise BackupError(f"target must be profile-relative: {rel!r}")
        resolved = (self.profile_dir / rel_path).resolve()
        # Guard against path traversal (e.g. "../../etc").
        if self.profile_dir != resolved and self.profile_dir not in resolved.parents:
            raise BackupError(f"target escapes profile dir: {rel!r}")
        return resolved

    # -- create --------------------------------------------------------

    def create(self, targets: Iterable[str]) -> str:
        """Snapshot the given profile-relative targets and return a backup id.

        Each target may be a file, a directory, or currently non-existent. The
        snapshot is sufficient to restore the exact state via :meth:`restore`.
        """
        target_list = list(dict.fromkeys(targets))  # dedupe, keep order
        if not target_list:
            raise BackupError("cannot create a backup with no targets")

        backup_id = _new_backup_id()
        backup_dir = self._backup_dir(backup_id)
        payload_dir = backup_dir / PAYLOAD_SUBDIR
        payload_dir.mkdir(parents=True, exist_ok=True)

        records: list[_TargetRecord] = []
        for rel in target_list:
            src = self._resolve_target(rel)
            dst = payload_dir / rel
            if src.is_dir():
                shutil.copytree(src, dst, symlinks=True)
                records.append(_TargetRecord(rel=rel, kind="dir"))
            elif src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst, follow_symlinks=False)
                records.append(_TargetRecord(rel=rel, kind="file"))
            else:
                records.append(_TargetRecord(rel=rel, kind="absent"))

        manifest = {
            "backup_id": backup_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_dir": str(self.profile_dir),
            "targets": [{"rel": r.rel, "kind": r.kind} for r in records],
        }
        (backup_dir / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return backup_id

    # -- restore -------------------------------------------------------

    def restore(self, backup_id: str) -> list[str]:
        """Restore a backup, returning the list of restored target paths.

        The restore is a true rollback: each target is reset to its snapshot
        state. A target that was ``absent`` at backup time is removed if it
        exists now.
        """
        backup_dir = self._backup_dir(backup_id)
        manifest_path = backup_dir / MANIFEST_FILENAME
        if not manifest_path.exists():
            raise BackupError(f"unknown backup id: {backup_id}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload_dir = backup_dir / PAYLOAD_SUBDIR

        restored: list[str] = []
        for entry in manifest.get("targets", []):
            rel = entry["rel"]
            kind = entry["kind"]
            dst = self._resolve_target(rel)
            src = payload_dir / rel

            # Remove whatever is currently at the target.
            if dst.is_dir() and not dst.is_symlink():
                shutil.rmtree(dst)
            elif dst.exists() or dst.is_symlink():
                dst.unlink()

            if kind == "absent":
                restored.append(rel)
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            if kind == "dir":
                shutil.copytree(src, dst, symlinks=True)
            else:  # file
                shutil.copy2(src, dst, follow_symlinks=False)
            restored.append(rel)

        return restored

    # -- housekeeping --------------------------------------------------

    def exists(self, backup_id: str) -> bool:
        return (self._backup_dir(backup_id) / MANIFEST_FILENAME).exists()

    def delete(self, backup_id: str) -> None:
        """Remove a backup's stored payload (e.g. after a successful revert)."""
        backup_dir = self._backup_dir(backup_id)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

    def list_backups(self) -> list[str]:
        root = _backups_root(self.profile_dir)
        if not root.exists():
            return []
        return sorted(
            p.name for p in root.iterdir()
            if (p / MANIFEST_FILENAME).exists()
        )
