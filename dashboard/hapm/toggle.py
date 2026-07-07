"""Generic addon enable/disable (toggle) engine — HAPM FR-6.

This is the orchestration layer that turns an addon (loaded from the FR-5
registry via :mod:`hapm.registry`) into a *reversible* change on a target
profile, using the FR-7 primitives (:mod:`hapm.state`, :mod:`hapm.backup`,
:mod:`hapm.soul_blocks`, :mod:`hapm.skills_tracker`).

Guarantees (PRD FR-6):

* **Enable** inserts the addon's SOUL.md contribution wrapped in
  ``<!-- HAPM:addon:<id> START/END -->`` markers and/or copies its skills into
  the profile, recording everything it did in ``hapm.lock`` so it can be undone.
* **Disable** removes *exactly* that addon's marked SOUL block and the skills it
  added (restoring any shadowed pre-existing skill), leaving the rest of the
  file/dir tree byte-identical.
* Multiple addons are independent: enabling/disabling one never touches
  another's block or skills.
* **Whitelist enforcement**: enabling an addon whose
  ``compatible_profiles_or_presets`` does not admit the target is rejected with
  a clear error (:class:`AddonNotCompatibleError`), never silently ignored.
* **Conflict detection**: if enabling would collide with an already-active
  addon's SOUL block (same marker id), it is rejected with
  :class:`AddonConflictError` rather than silently corrupting the file. v1
  detects and reports; it does not auto-resolve (PRD Non-Goal).

The engine is a pure filesystem operation — no network, no dashboard process —
so it is unit-testable in isolation and reused by the FR-6 API routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .backup import BackupStore
from .registry import Addon, load_addon
from .skills_tracker import (
    SkillContribution,
    add_addon_skills,
    remove_addon_skills,
)
from .soul_blocks import (
    has_addon_block,
    list_addon_blocks,
    remove_addon_block,
    upsert_addon_block,
)
from .state import AddonState, LockState, read_lock, write_lock

SOUL_FILENAME = "SOUL.md"


class ToggleError(Exception):
    """Base error for addon enable/disable failures."""


class AddonNotCompatibleError(ToggleError):
    """Enabling an addon whose whitelist does not admit the target."""


class AddonConflictError(ToggleError):
    """Enabling an addon whose SOUL block collides with an active addon's."""


class AddonAlreadyEnabledError(ToggleError):
    """Enabling an addon that is already active on the profile."""


class AddonNotEnabledError(ToggleError):
    """Disabling an addon that is not active on the profile."""


@dataclass
class ToggleResult:
    """Outcome of an enable/disable operation.

    Attributes:
        addon_id: The addon acted on.
        mode: The active mode (or ``None`` for a non-modal addon).
        enabled: Final activation state after the operation.
        soul_block: Whether a SOUL block is now present for this addon.
        skill_paths: Profile-relative skill paths currently owned by the addon
            (empty after a disable).
        lock_path: Path to the updated ``hapm.lock`` (may not exist if the
            profile is fully reverted).
    """

    addon_id: str
    mode: str | None
    enabled: bool
    soul_block: bool
    skill_paths: list[str]
    lock_path: str


def _load_lock(profile_dir: Path) -> LockState:
    lock = read_lock(profile_dir)
    if lock is None:
        lock = LockState(profile=profile_dir.name)
    return lock


def _soul_text(profile_dir: Path) -> str:
    soul = profile_dir / SOUL_FILENAME
    return soul.read_text(encoding="utf-8") if soul.exists() else ""


def _write_soul(profile_dir: Path, text: str) -> None:
    (profile_dir / SOUL_FILENAME).write_text(text, encoding="utf-8")


def _detect_soul_conflict(
    soul_text: str, lock: LockState, addon_id: str
) -> None:
    """Raise :class:`AddonConflictError` if enabling would collide.

    A conflict is an addon block marker id already present in the SOUL file
    that is *not* recorded as this same addon in the lock — i.e. some other
    active addon (or foreign tooling) already owns that marker id. Since marker
    ids are the addon id, the practical collision is an existing block whose id
    equals ours while the lock has no matching addon entry (stale/foreign
    block), which we refuse to overwrite blindly.
    """
    existing_ids = set(list_addon_blocks(soul_text))
    if addon_id in existing_ids and lock.get_addon(addon_id) is None:
        raise AddonConflictError(
            f"a SOUL.md block for addon {addon_id!r} already exists but is not "
            f"tracked in hapm.lock; refusing to overwrite (resolve manually)"
        )


def enable_addon(
    profile_dir: str | Path,
    addon: Addon | str | Path,
    target: str,
    mode_id: str | None = None,
) -> ToggleResult:
    """Enable ``addon`` on the profile at ``profile_dir``.

    Args:
        profile_dir: Target profile directory (contains ``SOUL.md``, ``skills/``).
        addon: A loaded :class:`Addon`, or a path to its registry directory.
        target: The profile name or preset slug the compatibility whitelist is
            checked against (per FR-5).
        mode_id: The selected mode for a modal addon (``None`` = default mode).

    Returns:
        A :class:`ToggleResult` describing the new state.

    Raises:
        AddonNotCompatibleError: target not in the addon's whitelist.
        AddonAlreadyEnabledError: addon already active on this profile.
        AddonConflictError: SOUL block would collide with a foreign block.
    """
    profile = Path(profile_dir).resolve()
    if not profile.is_dir():
        raise ToggleError(f"profile directory does not exist: {profile}")
    if not isinstance(addon, Addon):
        addon = load_addon(addon)

    if not addon.is_compatible_with(target):
        raise AddonNotCompatibleError(
            f"addon {addon.id!r} is not compatible with {target!r} "
            f"(whitelist: {addon.compatible})"
        )

    lock = _load_lock(profile)
    if lock.get_addon(addon.id) is not None:
        raise AddonAlreadyEnabledError(
            f"addon {addon.id!r} is already enabled on profile "
            f"{profile.name!r}"
        )

    # Resolve the effective contribution for the chosen mode.
    mode = addon.get_mode(mode_id)
    resolved_mode = mode.id if mode is not None else None
    want_soul, want_skills = addon.effective_contribution(mode_id)

    store = BackupStore(profile)
    backup_id: str | None = None
    skill_contrib: SkillContribution | None = None

    # --- SOUL block ---------------------------------------------------
    if want_soul:
        soul_text = _soul_text(profile)
        _detect_soul_conflict(soul_text, lock, addon.id)
        # Back up SOUL.md before mutating so disable can restore byte-exactly
        # even in edge cases; the marked-block remove is the primary undo path.
        backup_id = store.create([SOUL_FILENAME])
        content = addon.soul_block_path(mode_id).read_text(encoding="utf-8")
        new_soul = upsert_addon_block(soul_text, addon.id, content)
        _write_soul(profile, new_soul)

    # --- skills -------------------------------------------------------
    if want_skills:
        contributions = addon.skill_contributions()
        skill_contrib = add_addon_skills(profile, contributions, store)

    # --- record in lock ----------------------------------------------
    skill_paths: list[str] = []
    shadow_backup_id: str | None = None
    if skill_contrib is not None:
        skill_paths = skill_contrib.all_paths
        shadow_backup_id = skill_contrib.shadow_backup_id

    state = AddonState(
        addon_id=addon.id,
        mode=resolved_mode or "",
        backup_id=backup_id,
        soul_block=want_soul,
        skill_paths=skill_paths,
    )
    # Persist the full skill contribution (added vs shadowed) so disable is
    # deterministic. We stash it on the addon-state via a private mapping kept
    # in the lock's addon entry, encoded in skill_paths + shadow marker.
    lock.set_addon(state)
    lock_path = write_lock(profile, lock)

    # The skills tracker needs the full contribution to reverse shadows; store
    # it alongside the lock as a sidecar keyed by addon id.
    if skill_contrib is not None:
        _write_skill_sidecar(profile, addon.id, skill_contrib)
    _ = shadow_backup_id  # captured inside the sidecar

    return ToggleResult(
        addon_id=addon.id,
        mode=resolved_mode,
        enabled=True,
        soul_block=want_soul,
        skill_paths=skill_paths,
        lock_path=str(lock_path),
    )


def disable_addon(
    profile_dir: str | Path,
    addon: Addon | str | Path,
) -> ToggleResult:
    """Disable a previously-enabled addon, restoring the prior state.

    Removes exactly this addon's marked SOUL block and the skills it added
    (restoring any shadowed pre-existing skill), then updates ``hapm.lock``.
    Other addons' contributions are left untouched.

    Raises:
        AddonNotEnabledError: the addon is not active on this profile.
    """
    profile = Path(profile_dir).resolve()
    addon_id = addon.id if isinstance(addon, Addon) else load_addon(addon).id

    lock = _load_lock(profile)
    state = lock.get_addon(addon_id)
    if state is None:
        raise AddonNotEnabledError(
            f"addon {addon_id!r} is not enabled on profile {profile.name!r}"
        )

    store = BackupStore(profile)

    # --- remove SOUL block -------------------------------------------
    if state.soul_block:
        soul_text = _soul_text(profile)
        if has_addon_block(soul_text, addon_id):
            _write_soul(profile, remove_addon_block(soul_text, addon_id))

    # --- remove skills (restore shadows) -----------------------------
    contrib = _read_skill_sidecar(profile, addon_id)
    if contrib is not None:
        remove_addon_skills(profile, contrib, store)
        _delete_skill_sidecar(profile, addon_id)

    # --- drop backups this addon owned -------------------------------
    if state.backup_id and store.exists(state.backup_id):
        store.delete(state.backup_id)
    if contrib is not None and contrib.shadow_backup_id:
        if store.exists(contrib.shadow_backup_id):
            store.delete(contrib.shadow_backup_id)

    # --- update lock -------------------------------------------------
    lock.remove_addon(addon_id)
    lock_path = write_lock(profile, lock)

    return ToggleResult(
        addon_id=addon_id,
        mode=None,
        enabled=False,
        soul_block=False,
        skill_paths=[],
        lock_path=str(lock_path),
    )


def list_active_addons(profile_dir: str | Path) -> list[AddonState]:
    """Return the addon states currently recorded in the profile's lock."""
    lock = read_lock(Path(profile_dir))
    return list(lock.addons) if lock is not None else []


# ---------------------------------------------------------------------------
# Skill-contribution sidecar
#
# The FR-7 ``SkillContribution`` (added vs shadowed paths + shadow backup id) is
# richer than the lock's ``skill_paths`` list, and is required to reverse a
# shadow on disable. We persist it as a small JSON sidecar next to hapm.lock so
# the lock format itself stays as FR-7 defined it.
# ---------------------------------------------------------------------------

import json  # noqa: E402  (kept local to the sidecar helpers)

_SIDECAR_DIRNAME = ".hapm"
_SIDECAR_SUBDIR = "skill_contributions"


def _sidecar_path(profile: Path, addon_id: str) -> Path:
    return profile / _SIDECAR_DIRNAME / _SIDECAR_SUBDIR / f"{addon_id}.json"


def _write_skill_sidecar(
    profile: Path, addon_id: str, contrib: SkillContribution
) -> None:
    path = _sidecar_path(profile, addon_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(contrib.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_skill_sidecar(
    profile: Path, addon_id: str
) -> SkillContribution | None:
    path = _sidecar_path(profile, addon_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SkillContribution.from_dict(data)


def _delete_skill_sidecar(profile: Path, addon_id: str) -> None:
    path = _sidecar_path(profile, addon_id)
    if path.exists():
        path.unlink()
