"""Unit tests for addon↔addon conflicts + guided resolution (FR-7 v1.1).

Covers the ``conflicts_with`` manifest field, the report-only conflict check
(PRD v1 Non-Goal Z.72-74 default), and the confirmed guided-resolution path
that disables colliding addons via the FR-7 reversible mechanics before
enabling the target — including atomic rollback on partial failure.

Run with: ``pytest dashboard/tests`` from the repo root, or
``python dashboard/tests/test_addon_conflicts.py`` for a stdlib-only run
without pytest installed.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Make the hapm package importable whether run via pytest from repo root or
# directly as a script.
_HERE = Path(__file__).resolve()
_DASHBOARD = _HERE.parent.parent  # .../dashboard
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

from hapm import (  # noqa: E402
    AddonAddonConflictError,
    ConflictResult,
    RegistryError,
    ResolutionResult,
    ToggleResult,
    check_conflicts,
    enable_addon,
    list_active_addons,
    load_addon,
    resolve_and_enable_addon,
)
from hapm.soul_blocks import addon_block_markers  # noqa: E402


# ---------------------------------------------------------------------------
# fixture builders (no pytest fixtures so the file also runs stdlib-only)
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _soul_addon(root: Path, addon_id: str, conflicts=None, compatible=None) -> None:
    """A minimal soul-block-only addon compatible with '*' by default."""
    manifest = {
        "id": addon_id,
        "name": addon_id.title(),
        "description": f"{addon_id} soul block addon",
        "version": "0.1.0",
        "contributes": {"soul_block": True, "skills": False},
        "compatible_profiles_or_presets": compatible or ["*"],
    }
    if conflicts is not None:
        manifest["conflicts_with"] = conflicts
    _write(root / addon_id / "manifest.json", json.dumps(manifest))
    _write(root / addon_id / "soul_block.md", f"{addon_id.upper()} contribution\n")


def _make_profile(root: Path, soul: str = "") -> Path:
    profile = root / "profiles" / "demo"
    profile.mkdir(parents=True, exist_ok=True)
    _write(profile / "SOUL.md", soul)
    return profile


# ---------------------------------------------------------------------------
# registry: conflicts_with parsing + validation
# ---------------------------------------------------------------------------


def test_registry_parses_conflicts_with() -> None:
    with tempfile.TemporaryDirectory() as td:
        reg = Path(td) / "reg"
        _soul_addon(reg, "beta", conflicts=["alpha"])
        addon = load_addon(reg / "beta")
        assert addon.conflicts_with == ["alpha"]


def test_registry_defaults_conflicts_with_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        reg = Path(td) / "reg"
        _soul_addon(reg, "alpha")
        addon = load_addon(reg / "alpha")
        assert addon.conflicts_with == []


def test_registry_rejects_self_conflict() -> None:
    with tempfile.TemporaryDirectory() as td:
        reg = Path(td) / "reg"
        _soul_addon(reg, "alpha", conflicts=["alpha"])
        try:
            load_addon(reg / "alpha")
            raise AssertionError("expected RegistryError for self-conflict")
        except RegistryError:
            pass


def test_registry_rejects_non_list_conflicts() -> None:
    with tempfile.TemporaryDirectory() as td:
        reg = Path(td) / "reg"
        _write(
            reg / "alpha" / "manifest.json",
            json.dumps(
                {
                    "id": "alpha",
                    "name": "Alpha",
                    "description": "x",
                    "version": "0.1.0",
                    "contributes": {"soul_block": True, "skills": False},
                    "compatible_profiles_or_presets": ["*"],
                    "conflicts_with": "beta",  # not a list
                }
            ),
        )
        _write(reg / "alpha" / "soul_block.md", "ALPHA\n")
        try:
            load_addon(reg / "alpha")
            raise AssertionError("expected RegistryError for non-list conflicts_with")
        except RegistryError:
            pass


def test_conflicting_active_helper() -> None:
    with tempfile.TemporaryDirectory() as td:
        reg = Path(td) / "reg"
        _soul_addon(reg, "beta", conflicts=["alpha", "gamma"])
        addon = load_addon(reg / "beta")
        assert addon.conflicting_active({"alpha"}) == ["alpha"]
        assert addon.conflicting_active({"alpha", "gamma"}) == ["alpha", "gamma"]
        assert addon.conflicting_active(set()) == []
        assert addon.conflicting_active({"delta"}) == []


# ---------------------------------------------------------------------------
# check_conflicts (report-only) + enable_addon default behaviour
# ---------------------------------------------------------------------------


def test_check_conflicts_none_when_collider_inactive() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reg = root / "reg"
        _soul_addon(reg, "beta", conflicts=["alpha"])
        profile = _make_profile(root, "base\n")
        res = check_conflicts(profile, reg / "beta", target="demo")
        assert isinstance(res, ConflictResult)
        assert res.has_conflict is False
        assert res.conflicts == []


def test_enable_reports_conflict_and_does_not_mutate() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reg = root / "reg"
        _soul_addon(reg, "alpha")
        _soul_addon(reg, "beta", conflicts=["alpha"])
        base = "USER BASE\n"
        profile = _make_profile(root, base)

        # Enable alpha first.
        enable_addon(profile, reg / "alpha", target="demo")
        soul_after_alpha = (profile / "SOUL.md").read_text(encoding="utf-8")

        # Enabling beta (conflicts_with alpha) must REPORT, not mutate.
        res = enable_addon(profile, reg / "beta", target="demo")
        assert isinstance(res, ConflictResult), type(res)
        assert res.has_conflict is True
        assert res.conflicting_ids == ["alpha"]
        assert res.conflicts[0].reason  # human-readable

        # Nothing changed: beta not active, SOUL untouched.
        active = {a.addon_id for a in list_active_addons(profile)}
        assert active == {"alpha"}, active
        assert (profile / "SOUL.md").read_text(encoding="utf-8") == soul_after_alpha
        start, _ = addon_block_markers("beta")
        assert start not in (profile / "SOUL.md").read_text(encoding="utf-8")


def test_enable_on_conflict_raise() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reg = root / "reg"
        _soul_addon(reg, "alpha")
        _soul_addon(reg, "beta", conflicts=["alpha"])
        profile = _make_profile(root, "base\n")
        enable_addon(profile, reg / "alpha", target="demo")
        try:
            enable_addon(
                profile, reg / "beta", target="demo", on_conflict="raise"
            )
            raise AssertionError("expected AddonAddonConflictError")
        except AddonAddonConflictError as exc:
            assert exc.conflicts == ["alpha"]


def test_enable_no_conflict_when_collider_absent_returns_toggle() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reg = root / "reg"
        _soul_addon(reg, "beta", conflicts=["alpha"])  # alpha never enabled
        profile = _make_profile(root, "base\n")
        res = enable_addon(profile, reg / "beta", target="demo")
        assert isinstance(res, ToggleResult)
        assert res.enabled is True
        assert {a.addon_id for a in list_active_addons(profile)} == {"beta"}


# ---------------------------------------------------------------------------
# resolve_and_enable_addon (confirmed guided resolution)
# ---------------------------------------------------------------------------


def test_resolve_disables_collider_then_enables_target() -> None:
    """Headline FR-7 v1.1 acceptance test.

    Enable alpha, then guided-resolve beta (conflicts_with alpha): alpha is
    disabled via the FR-7 reversible mechanics (its SOUL block byte-exactly
    removed) and beta is enabled. The lock ends with only beta active.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reg = root / "reg"
        _soul_addon(reg, "alpha")
        _soul_addon(reg, "beta", conflicts=["alpha"])
        base = "USER BASE LINE\n"
        profile = _make_profile(root, base)

        enable_addon(profile, reg / "alpha", target="demo")
        a_start, a_end = addon_block_markers("alpha")
        assert a_start in (profile / "SOUL.md").read_text(encoding="utf-8")

        resolution = resolve_and_enable_addon(
            profile, reg / "beta", target="demo", addons_root=reg
        )
        assert isinstance(resolution, ResolutionResult)
        assert resolution.disabled == ["alpha"]
        assert resolution.result.enabled is True

        soul = (profile / "SOUL.md").read_text(encoding="utf-8")
        # alpha's block reversibly removed, beta's block present.
        assert a_start not in soul and a_end not in soul
        b_start, _ = addon_block_markers("beta")
        assert b_start in soul
        # user text preserved.
        assert base.strip() in soul

        active = {a.addon_id for a in list_active_addons(profile)}
        assert active == {"beta"}, active


def test_resolve_no_conflict_is_plain_enable() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reg = root / "reg"
        _soul_addon(reg, "beta", conflicts=["alpha"])  # alpha not active
        profile = _make_profile(root, "base\n")
        resolution = resolve_and_enable_addon(
            profile, reg / "beta", target="demo", addons_root=reg
        )
        assert resolution.disabled == []
        assert resolution.result.enabled is True
        assert {a.addon_id for a in list_active_addons(profile)} == {"beta"}


def test_resolve_rolls_back_on_target_enable_failure() -> None:
    """If enabling the target fails after disabling a collider, the collider is
    re-enabled (rollback) so the profile returns to its pre-call state.

    We force the target enable to fail by making beta's soul_block file missing
    at resolve time (registry error inside enable), after alpha is disabled.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reg = root / "reg"
        _soul_addon(reg, "alpha")
        _soul_addon(reg, "beta", conflicts=["alpha"])
        profile = _make_profile(root, "BASE\n")

        enable_addon(profile, reg / "alpha", target="demo")
        soul_with_alpha = (profile / "SOUL.md").read_text(encoding="utf-8")

        # Sabotage beta so enabling it raises after alpha gets disabled: remove
        # its soul_block content file (declared soul_block=true).
        (reg / "beta" / "soul_block.md").unlink()

        raised = False
        try:
            resolve_and_enable_addon(
                profile, reg / "beta", target="demo", addons_root=reg
            )
        except Exception:  # noqa: BLE001
            raised = True
        assert raised, "expected the target enable to fail"

        # Rollback restored alpha: still the only active addon, SOUL identical.
        active = {a.addon_id for a in list_active_addons(profile)}
        assert active == {"alpha"}, active
        assert (profile / "SOUL.md").read_text(encoding="utf-8") == soul_with_alpha


# ---------------------------------------------------------------------------
# stdlib-only runner
# ---------------------------------------------------------------------------


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {exc!r}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
