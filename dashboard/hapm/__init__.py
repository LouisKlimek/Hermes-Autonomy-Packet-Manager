"""HAPM (Hermes Autonomy Packet Manager) core state engine.

This package holds the reversibility engine described in the HAPM PRD FR-7:
the per-profile state/lock record (``hapm.lock``), the backup/restore
primitives that let preset apply (FR-4) and addon toggle (FR-6) be rolled back
byte-exactly, the SOUL.md marked-block editor, addon skill tracking, and the
optional central index used for cross-profile status queries (OQ-3).

Nothing here touches the network or the dashboard process; it is a pure
filesystem library so it can be unit tested in isolation and reused by the
later FR-4 / FR-6 endpoints.
"""

from __future__ import annotations

from .state import (
    HAPM_LOCK_FILENAME,
    AddonState,
    LockState,
    read_lock,
    write_lock,
)
from .backup import (
    BackupError,
    BackupStore,
)
from .soul_blocks import (
    SoulBlockError,
    addon_block_markers,
    has_addon_block,
    list_addon_blocks,
    remove_addon_block,
    upsert_addon_block,
)
from .skills_tracker import (
    SkillTrackError,
    SkillContribution,
    add_addon_skills,
    remove_addon_skills,
)
from .index import (
    CentralIndex,
    default_index_path,
)

__all__ = [
    # state
    "HAPM_LOCK_FILENAME",
    "AddonState",
    "LockState",
    "read_lock",
    "write_lock",
    # backup
    "BackupError",
    "BackupStore",
    # soul blocks
    "SoulBlockError",
    "addon_block_markers",
    "has_addon_block",
    "list_addon_blocks",
    "remove_addon_block",
    "upsert_addon_block",
    # skills tracker
    "SkillTrackError",
    "SkillContribution",
    "add_addon_skills",
    "remove_addon_skills",
    # index
    "CentralIndex",
    "default_index_path",
]

__version__ = "0.1.0"
