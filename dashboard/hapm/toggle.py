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
  :class:`AddonConflictError` rather than silently corrupting the file.
* **Addon↔addon conflicts (FR-7 v1.1)**: an addon may declare
  ``conflicts_with`` (addon ids that must be inactive first). Enabling such an
  addon while a conflict is active returns a structured :class:`ConflictResult`
  by default (report-only, PRD v1 Non-Goal Z.72-74) and mutates nothing. A
  confirmed, opt-in guided resolution (:func:`resolve_and_enable_addon`)
  disables the colliders **through the same FR-7 reversible mechanics** and
  then enables the target atomically, rolling back on partial failure.

The engine is a pure filesystem operation — no network, no dashboard process —
so it is unit-testable in isolation and reused by the FR-6 API routes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


class AddonAddonConflictError(ToggleError):
    """Enabling an addon blocked by an active declared ``conflicts_with`` peer.

    This is the *hard* variant raised only when the caller opts into strict
    enforcement (``on_conflict="raise"``). The default enable path instead
    returns a structured :class:`ConflictResult` so the API/frontend can offer
    guided resolution (FR-7 v1.1). The colliding addon ids are attached as
    :attr:`conflicts` so a programmatic caller can act on them.
    """

    def __init__(self, message: str, conflicts: "list[str] | None" = None) -> None:
        super().__init__(message)
        self.conflicts: list[str] = list(conflicts or [])


class ResolutionError(ToggleError):
    """A guided conflict resolution failed and could not be fully rolled back.

    Raised by :func:`resolve_and_enable_addon` only in the rare case where the
    target enable failed *and* re-enabling a previously-disabled colliding
    addon during rollback also failed, leaving the profile in a partially
    resolved state. The message names what could not be restored so a human can
    recover from the FR-7 backups.
    """


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


@dataclass
class AddonConflict:
    """One active addon that collides with the addon being enabled (FR-7 v1.1).

    Attributes:
        addon_id: The active, colliding addon's id (present in the target's
            ``conflicts_with`` and currently enabled on the profile).
        mode: The colliding addon's active mode, or ``None``/"" if non-modal.
        reason: Human-readable explanation the frontend popup can render.
    """

    addon_id: str
    mode: str | None
    reason: str

    def to_dict(self) -> dict:
        return {
            "addon_id": self.addon_id,
            "mode": self.mode,
            "reason": self.reason,
        }


@dataclass
class ConflictResult:
    """Structured outcome of a conflict check for an enable attempt (FR-7 v1.1).

    Returned by :func:`check_conflicts` and by :func:`enable_addon` when the
    default (report-only, PRD v1 Non-Goal Z.72-74) path detects that enabling
    would collide with one or more already-active addons declared in the
    target's ``conflicts_with``. The API layer serializes this into the popup
    payload the frontend consumes; guided resolution is opt-in via a confirmed
    call to :func:`resolve_and_enable_addon`.

    Attributes:
        addon_id: The addon the caller tried to enable.
        target: The whitelist target used for the enable attempt.
        mode: The requested mode for the target addon (``None`` = default).
        conflicts: The colliding active addons (empty => no conflict).
    """

    addon_id: str
    target: str
    mode: str | None
    conflicts: list[AddonConflict] = field(default_factory=list)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)

    @property
    def conflicting_ids(self) -> list[str]:
        return [c.addon_id for c in self.conflicts]

    def to_dict(self) -> dict:
        return {
            "addon_id": self.addon_id,
            "target": self.target,
            "mode": self.mode,
            "has_conflict": self.has_conflict,
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


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


def check_conflicts(
    profile_dir: str | Path,
    addon: Addon | str | Path,
    mode_id: str | None = None,
    target: str | None = None,
) -> ConflictResult:
    """Report addon↔addon conflicts for enabling ``addon`` (FR-7 v1.1).

    Pure, read-only: inspects the addon's declarative ``conflicts_with`` list
    against the addons currently active in the profile's ``hapm.lock`` and
    returns a structured :class:`ConflictResult`. It mutates nothing — this is
    the report-only path (PRD v1 Non-Goal Z.72-74) the frontend popup consumes
    before offering opt-in guided resolution.

    Args:
        profile_dir: Target profile directory.
        addon: A loaded :class:`Addon` or a path to its registry directory.
        mode_id: The mode the caller intends to enable (echoed back only).
        target: The whitelist target (echoed back only); defaults to the
            profile directory name.

    Returns:
        A :class:`ConflictResult`; ``has_conflict`` is ``False`` when nothing
        currently active collides.
    """
    profile = Path(profile_dir).resolve()
    if not isinstance(addon, Addon):
        addon = load_addon(addon)
    resolved_target = target if target is not None else profile.name

    lock = _load_lock(profile)
    active_by_id = {a.addon_id: a for a in lock.addons}
    colliding_ids = addon.conflicting_active(set(active_by_id))

    conflicts: list[AddonConflict] = []
    for cid in colliding_ids:
        state = active_by_id.get(cid)
        active_mode = (state.mode or None) if state is not None else None
        conflicts.append(
            AddonConflict(
                addon_id=cid,
                mode=active_mode,
                reason=(
                    f"addon {addon.id!r} declares a conflict with {cid!r}, "
                    f"which is currently active on profile {profile.name!r}; "
                    f"it must be disabled before {addon.id!r} can be enabled"
                ),
            )
        )

    return ConflictResult(
        addon_id=addon.id,
        target=resolved_target,
        mode=mode_id,
        conflicts=conflicts,
    )


def enable_addon(
    profile_dir: str | Path,
    addon: Addon | str | Path,
    target: str,
    mode_id: str | None = None,
    on_conflict: str = "report",
) -> "ToggleResult | ConflictResult":
    """Enable ``addon`` on the profile at ``profile_dir``.

    Args:
        profile_dir: Target profile directory (contains ``SOUL.md``, ``skills/``).
        addon: A loaded :class:`Addon`, or a path to its registry directory.
        target: The profile name or preset slug the compatibility whitelist is
            checked against (per FR-5).
        mode_id: The selected mode for a modal addon (``None`` = default mode).
        on_conflict: How to handle an addon↔addon ``conflicts_with`` collision
            with an already-active addon (FR-7 v1.1):

            * ``"report"`` (default): mutate nothing and return a structured
              :class:`ConflictResult`. This preserves the PRD v1 Non-Goal
              (Z.72-74) report-only default — guided resolution is opt-in.
            * ``"raise"``: raise :class:`AddonAddonConflictError`.
            * ``"ignore"``: skip the addon↔addon check entirely (used
              internally by :func:`resolve_and_enable_addon` after it has
              already disabled the colliding addons).

    Returns:
        A :class:`ToggleResult` on success, or — when ``on_conflict="report"``
        and a conflict is detected — a :class:`ConflictResult` describing the
        collision (no mutation performed).

    Raises:
        AddonNotCompatibleError: target not in the addon's whitelist.
        AddonAlreadyEnabledError: addon already active on this profile.
        AddonConflictError: SOUL block would collide with a foreign block.
        AddonAddonConflictError: ``conflicts_with`` collision and
            ``on_conflict="raise"``.
        ValueError: ``on_conflict`` is not one of the accepted values.
    """
    if on_conflict not in ("report", "raise", "ignore"):
        raise ValueError(
            f"on_conflict must be 'report', 'raise', or 'ignore', "
            f"got {on_conflict!r}"
        )
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

    # --- addon↔addon conflict check (FR-7 v1.1) -----------------------
    # Declarative ``conflicts_with`` vs. currently-active addons. In the
    # default report-only mode this returns a structured result and mutates
    # nothing; guided resolution (disable colliders, then enable) is opt-in via
    # resolve_and_enable_addon which calls back in with on_conflict="ignore".
    if on_conflict != "ignore":
        conflict = check_conflicts(
            profile, addon, mode_id=mode_id, target=target
        )
        if conflict.has_conflict:
            if on_conflict == "raise":
                raise AddonAddonConflictError(
                    f"cannot enable {addon.id!r}: conflicting addon(s) active: "
                    f"{conflict.conflicting_ids}",
                    conflicts=conflict.conflicting_ids,
                )
            return conflict

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


@dataclass
class ResolutionResult:
    """Outcome of a confirmed guided conflict resolution (FR-7 v1.1).

    Attributes:
        addon_id: The target addon that was ultimately enabled.
        disabled: Addon ids that were disabled (via FR-7 mechanics) to clear
            the conflict, in the order they were disabled.
        result: The :class:`ToggleResult` from enabling the target addon.
    """

    addon_id: str
    disabled: list[str]
    result: ToggleResult


def resolve_and_enable_addon(
    profile_dir: str | Path,
    addon: Addon | str | Path,
    target: str,
    addons_root: str | Path,
    mode_id: str | None = None,
) -> ResolutionResult:
    """Guided, confirmed conflict resolution (FR-7 v1.1).

    This is the *opt-in* path invoked only after the user has explicitly
    confirmed (in the frontend popup) that the colliding addons may be
    disabled. It:

    1. Recomputes the addon↔addon conflict set for ``addon``.
    2. Disables each colliding active addon **via the FR-7-backed**
       :func:`disable_addon` — the same marked-block / backup / lock mechanics
       used everywhere else, so every deactivation is exactly reversible. No
       bypass.
    3. Enables the target ``addon`` (with the conflict check skipped, since the
       colliders are now gone).

    Atomicity: if the target enable fails after some colliders were disabled,
    every disabled addon is re-enabled (rolled back) to its prior mode using
    the same engine, restoring the pre-call state. If a rollback re-enable also
    fails, a :class:`ResolutionError` is raised naming what could not be
    restored (recoverable from the FR-7 backups).

    Args:
        profile_dir: Target profile directory.
        addon: The addon to enable (loaded :class:`Addon` or registry path).
        target: The FR-5 whitelist target (profile name / preset slug).
        addons_root: The addon registry root, used to reload each colliding
            addon's manifest so it can be disabled/re-enabled.
        mode_id: The selected mode for the target addon (``None`` = default).

    Returns:
        A :class:`ResolutionResult` describing what was disabled and the final
        enable result.

    Raises:
        AddonNotCompatibleError / AddonAlreadyEnabledError / AddonConflictError:
            propagated from the underlying enable of the target addon.
        ResolutionError: the target enable failed and rollback could not fully
            restore a previously-disabled addon.
    """
    profile = Path(profile_dir).resolve()
    registry_root = Path(addons_root)
    if not isinstance(addon, Addon):
        addon = load_addon(addon)

    conflict = check_conflicts(profile, addon, mode_id=mode_id, target=target)

    # No conflict: behave exactly like a normal enable (still honours all the
    # hard checks inside enable_addon).
    if not conflict.has_conflict:
        result = enable_addon(
            profile, addon, target=target, mode_id=mode_id,
            on_conflict="ignore",
        )
        assert isinstance(result, ToggleResult)  # ignore never returns Conflict
        return ResolutionResult(
            addon_id=addon.id, disabled=[], result=result
        )

    # Capture each colliding addon's active mode BEFORE disabling so rollback
    # can restore it exactly. (id -> mode-or-None). Also pre-load each colliding
    # addon and compute a whitelist-valid target for a potential rollback
    # re-enable (the lock does not record the original enable target, so we
    # derive one the addon admits).
    lock = _load_lock(profile)
    prior_modes: dict[str, str | None] = {}
    restore_targets: dict[str, str] = {}
    for cid in conflict.conflicting_ids:
        st = lock.get_addon(cid)
        prior_modes[cid] = (st.mode or None) if st is not None else None
        collider = load_addon(registry_root / cid)
        if collider.is_compatible_with(profile.name):
            restore_targets[cid] = profile.name
        elif collider.compatible:
            # First concrete whitelist entry the addon admits.
            restore_targets[cid] = collider.compatible[0]
        else:
            restore_targets[cid] = profile.name

    disabled: list[str] = []
    try:
        for cid in conflict.conflicting_ids:
            disable_addon(profile, registry_root / cid)
            disabled.append(cid)

        # Colliders cleared: enable the target (skip the now-moot check).
        result = enable_addon(
            profile, addon, target=target, mode_id=mode_id,
            on_conflict="ignore",
        )
        assert isinstance(result, ToggleResult)
    except Exception as enable_exc:  # noqa: BLE001 - re-raised after rollback
        # Roll back: re-enable everything we disabled, in reverse order, to its
        # prior mode. Any failure here is unrecoverable automatically.
        failed_restore: list[str] = []
        for cid in reversed(disabled):
            try:
                enable_addon(
                    profile, registry_root / cid,
                    target=restore_targets.get(cid, profile.name),
                    mode_id=prior_modes.get(cid), on_conflict="ignore",
                )
            except Exception:  # noqa: BLE001 - collected, reported below
                failed_restore.append(cid)
        if failed_restore:
            raise ResolutionError(
                f"guided resolution for {addon.id!r} failed and rollback could "
                f"not restore addon(s) {failed_restore}; recover from the FR-7 "
                f"backups under the profile's .hapm/backups/. Original error: "
                f"{enable_exc}"
            ) from enable_exc
        # Fully rolled back to the pre-call state: re-raise the original cause.
        raise

    return ResolutionResult(
        addon_id=addon.id, disabled=disabled, result=result
    )


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
