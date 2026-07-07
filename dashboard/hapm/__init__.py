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
from .registry import (
    COMPAT_ANY,
    Addon,
    AddonMode,
    RegistryError,
    compatible_addons,
    load_addon,
    load_registry,
)
from .toggle import (
    AddonAddonConflictError,
    AddonAlreadyEnabledError,
    AddonConflict,
    AddonConflictError,
    AddonNotCompatibleError,
    AddonNotEnabledError,
    ConflictResult,
    ResolutionError,
    ResolutionResult,
    ToggleError,
    ToggleResult,
    check_conflicts,
    disable_addon,
    enable_addon,
    list_active_addons,
    resolve_and_enable_addon,
)
from .apply import (
    ApplyError,
    ApplyResult,
    PresetInfo,
    WhitelistError,
    apply_preset,
    default_presets_root,
    deep_merge,
    list_presets,
    resolve_preset,
    revert_preset,
    validate_fragment_whitelist,
)
from .builder_sanitize import (
    CURATED_SKILL_SOURCES,
    FORBIDDEN_SOUL_HEADINGS,
    SanitizeError,
    SanitizeResult,
    Violation,
    assert_target_allowed,
    check_addon,
    check_content,
    check_forbidden_soul_headings,
    enumerate_targets,
    make_addon_id,
    slugify,
    validate_addon_id,
    validate_curated_skill,
    validate_inline_skill,
)
from .builder_drafts import (
    Draft,
    DraftError,
    DraftStore,
    drafts_root,
)
from .builder_pr import (
    BuilderPRError,
    PRResult,
    branch_for,
    build_manifest,
    materialize_addon,
    open_addon_pr,
    planned_files,
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
    # registry (FR-6)
    "Addon",
    "AddonMode",
    "COMPAT_ANY",
    "RegistryError",
    "compatible_addons",
    "load_addon",
    "load_registry",
    # toggle engine (FR-6)
    "AddonAlreadyEnabledError",
    "AddonConflictError",
    "AddonNotCompatibleError",
    "AddonNotEnabledError",
    "ToggleError",
    "ToggleResult",
    "disable_addon",
    "enable_addon",
    "list_active_addons",
    # apply (FR-4)
    "ApplyError",
    "ApplyResult",
    "PresetInfo",
    "WhitelistError",
    "apply_preset",
    "default_presets_root",
    "deep_merge",
    "list_presets",
    "resolve_preset",
    "revert_preset",
    "validate_fragment_whitelist",
<<<<<<< HEAD
    # v1.2 builder — sanitizing / structural whitelist (Req 1, §4)
    "CURATED_SKILL_SOURCES",
    "FORBIDDEN_SOUL_HEADINGS",
    "SanitizeError",
    "SanitizeResult",
    "Violation",
    "assert_target_allowed",
    "check_addon",
    "check_content",
    "check_forbidden_soul_headings",
    "enumerate_targets",
    "make_addon_id",
    "slugify",
    "validate_addon_id",
    "validate_curated_skill",
    "validate_inline_skill",
    # v1.2 builder — local draft store (Req 2, §5)
    "Draft",
    "DraftError",
    "DraftStore",
    "drafts_root",
    # v1.2 builder — PR activation path (Req 2/FR-7, §5/§6)
    "BuilderPRError",
    "PRResult",
    "branch_for",
    "build_manifest",
    "materialize_addon",
    "open_addon_pr",
    "planned_files",
=======
    # addon↔addon conflicts + guided resolution (FR-7 v1.1)
    "AddonAddonConflictError",
    "AddonConflict",
    "ConflictResult",
    "ResolutionError",
    "ResolutionResult",
    "check_conflicts",
    "resolve_and_enable_addon",
>>>>>>> origin/main
]

__version__ = "0.1.0"
