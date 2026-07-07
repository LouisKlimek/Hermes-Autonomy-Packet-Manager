"""Unit tests for the HAPM addon enable/disable engine (FR-6).

Run with: ``pytest dashboard/tests`` (or ``python -m pytest``) from the repo
root, or ``python dashboard/tests/test_addon_toggle.py`` for a stdlib-only run
without pytest installed.

Central acceptance criterion (PRD FR-6 / task): enable two independent addons,
disable one, and confirm the other's SOUL block + skills remain byte-identical
while the disabled addon's contribution is byte-exactly removed
(``test_two_independent_addons_disable_one_leaves_other_untouched``).
"""

from __future__ import annotations

import json
import os
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
    AddonAlreadyEnabledError,
    AddonConflictError,
    AddonNotCompatibleError,
    AddonNotEnabledError,
    compatible_addons,
    disable_addon,
    enable_addon,
    list_active_addons,
    load_addon,
)
from hapm.soul_blocks import addon_block_markers  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures builders (no pytest fixtures so the file also runs stdlib-only)
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_registry(root: Path) -> None:
    """Create a small addon registry with two independent addons.

    * ``alpha``: soul_block-only (single mode, no ``modes``), compatible with
      profile ``demo``.
    * ``beta``: skills-only, compatible with ``*``.
    """
    # alpha: soul-block-only addon
    _write(
        root / "alpha" / "manifest.json",
        json.dumps(
            {
                "id": "alpha",
                "name": "Alpha",
                "description": "Alpha soul block addon",
                "version": "0.1.0",
                "contributes": {"soul_block": True, "skills": False},
                "compatible_profiles_or_presets": ["demo"],
            }
        ),
    )
    _write(root / "alpha" / "soul_block.md", "ALPHA soul contribution line\n")

    # beta: skills-only addon
    _write(
        root / "beta" / "manifest.json",
        json.dumps(
            {
                "id": "beta",
                "name": "Beta",
                "description": "Beta skills addon",
                "version": "0.1.0",
                "contributes": {"soul_block": False, "skills": True},
                "compatible_profiles_or_presets": ["*"],
            }
        ),
    )
    _write(
        root / "beta" / "skills" / "beta-skill" / "SKILL.md",
        "# Beta Skill\nbeta content\n",
    )

    # gamma: incompatible with 'demo' (whitelist restricts to other-preset)
    _write(
        root / "gamma" / "manifest.json",
        json.dumps(
            {
                "id": "gamma",
                "name": "Gamma",
                "description": "Gamma addon (incompatible with demo)",
                "version": "0.1.0",
                "contributes": {"soul_block": True, "skills": False},
                "compatible_profiles_or_presets": ["other-preset"],
            }
        ),
    )
    _write(root / "gamma" / "soul_block.md", "GAMMA line\n")


def _make_profile(root: Path, soul: str = "") -> Path:
    profile = root / "profiles" / "demo"
    profile.mkdir(parents=True, exist_ok=True)
    _write(profile / "SOUL.md", soul)
    return profile


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_list_compatible_filters_by_whitelist() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_registry(root / "reg")
        got = {a.id for a in compatible_addons(root / "reg", "demo")}
        # alpha (demo) + beta (*) are compatible; gamma (other-preset) is not.
        assert got == {"alpha", "beta"}, got


def test_enable_incompatible_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_registry(root / "reg")
        profile = _make_profile(root, "base soul\n")
        addon = load_addon(root / "reg" / "gamma")
        try:
            enable_addon(profile, addon, target="demo")
            raise AssertionError("expected AddonNotCompatibleError")
        except AddonNotCompatibleError:
            pass


def test_enable_soul_block_inserts_marked_block() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_registry(root / "reg")
        base = "user authored line\n"
        profile = _make_profile(root, base)
        enable_addon(profile, root / "reg" / "alpha", target="demo")
        soul = (profile / "SOUL.md").read_text(encoding="utf-8")
        start, end = addon_block_markers("alpha")
        assert start in soul and end in soul
        assert "ALPHA soul contribution line" in soul
        assert soul.startswith(base)  # user text preserved at top
        # lock records the addon
        active = {a.addon_id for a in list_active_addons(profile)}
        assert active == {"alpha"}


def test_enable_skills_copies_and_tracks() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_registry(root / "reg")
        profile = _make_profile(root, "")
        res = enable_addon(profile, root / "reg" / "beta", target="demo")
        skill = profile / "skills" / "beta-skill" / "SKILL.md"
        assert skill.is_file()
        assert "beta content" in skill.read_text(encoding="utf-8")
        assert any("beta-skill" in p for p in res.skill_paths)


def test_double_enable_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_registry(root / "reg")
        profile = _make_profile(root, "base\n")
        enable_addon(profile, root / "reg" / "alpha", target="demo")
        try:
            enable_addon(profile, root / "reg" / "alpha", target="demo")
            raise AssertionError("expected AddonAlreadyEnabledError")
        except AddonAlreadyEnabledError:
            pass


def test_disable_not_enabled_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_registry(root / "reg")
        profile = _make_profile(root, "base\n")
        try:
            disable_addon(profile, root / "reg" / "alpha")
            raise AssertionError("expected AddonNotEnabledError")
        except AddonNotEnabledError:
            pass


def test_conflict_on_foreign_untracked_block() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_registry(root / "reg")
        # Pre-seed a foreign 'alpha' block that is NOT tracked in hapm.lock.
        start, end = addon_block_markers("alpha")
        foreign = f"top\n\n{start}\nforeign junk\n{end}\n"
        profile = _make_profile(root, foreign)
        try:
            enable_addon(profile, root / "reg" / "alpha", target="demo")
            raise AssertionError("expected AddonConflictError")
        except AddonConflictError:
            pass


def test_two_independent_addons_disable_one_leaves_other_untouched() -> None:
    """PRD FR-6 headline acceptance test.

    Enable alpha (soul block) + beta (skills), snapshot both contributions,
    disable alpha, and prove:
      * beta's skill + hapm.lock entry are byte-identical,
      * alpha's SOUL block is byte-exactly removed (SOUL.md back to pre-alpha),
      * a fresh alpha-enable reproduces the same bytes (idempotent round trip).
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_registry(root / "reg")
        base_soul = "USER LINE ONE\nUSER LINE TWO\n"
        profile = _make_profile(root, base_soul)

        # Enable both.
        enable_addon(profile, root / "reg" / "alpha", target="demo")
        enable_addon(profile, root / "reg" / "beta", target="demo")

        soul_after_alpha = (profile / "SOUL.md").read_text(encoding="utf-8")
        beta_skill = profile / "skills" / "beta-skill" / "SKILL.md"
        beta_bytes = beta_skill.read_bytes()

        active = {a.addon_id for a in list_active_addons(profile)}
        assert active == {"alpha", "beta"}, active

        # Disable alpha only.
        disable_addon(profile, root / "reg" / "alpha")

        # beta untouched: skill bytes identical, still tracked.
        assert beta_skill.read_bytes() == beta_bytes
        active = {a.addon_id for a in list_active_addons(profile)}
        assert active == {"beta"}, active

        # alpha's SOUL block byte-exactly removed => back to base soul.
        soul_now = (profile / "SOUL.md").read_text(encoding="utf-8")
        assert soul_now == base_soul, repr(soul_now)
        start, end = addon_block_markers("alpha")
        assert start not in soul_now and end not in soul_now

        # Re-enabling alpha reproduces the exact same SOUL bytes as before.
        enable_addon(profile, root / "reg" / "alpha", target="demo")
        assert (profile / "SOUL.md").read_text(encoding="utf-8") == soul_after_alpha


def test_disable_removes_skills_and_restores_clean_tree() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_registry(root / "reg")
        profile = _make_profile(root, "")
        enable_addon(profile, root / "reg" / "beta", target="demo")
        assert (profile / "skills" / "beta-skill").exists()
        disable_addon(profile, root / "reg" / "beta")
        # addon-created skill dir removed
        assert not (profile / "skills" / "beta-skill").exists()
        # no lock residue when fully reverted
        assert list_active_addons(profile) == []


def test_disable_restores_shadowed_preexisting_skill() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_registry(root / "reg")
        profile = _make_profile(root, "")
        # Pre-existing user skill with the SAME name beta ships.
        user_skill = profile / "skills" / "beta-skill" / "SKILL.md"
        _write(user_skill, "ORIGINAL USER SKILL\n")
        original = user_skill.read_bytes()

        enable_addon(profile, root / "reg" / "beta", target="demo")
        # addon shadowed the original
        assert user_skill.read_bytes() != original

        disable_addon(profile, root / "reg" / "beta")
        # original restored byte-exactly
        assert user_skill.read_bytes() == original


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
