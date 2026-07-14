"""Tests for HAPM preset apply / revert with whitelisted config merge (FR-4).

These exercise the pure-filesystem engine in ``dashboard/hapm/apply.py`` and
the route handlers in ``dashboard/plugin_api.py`` directly (no HTTP server), so
they run with only ``fastapi`` + ``pyyaml`` installed.

Covered acceptance criteria:
  - apply overwrites SOUL.md and skills/ from the preset
  - apply MERGES only whitelisted keys into config.yaml, leaving secrets /
    model / tokens byte-identical
  - a preset fragment with a non-whitelisted key is REJECTED, nothing written
  - revert restores SOUL.md / skills / config.yaml byte-exactly
  - list_presets reads the registry
  - route handlers return structured errors for bad input

Run with:  pytest dashboard/tests/test_preset_apply.py
     or:   python dashboard/tests/test_preset_apply.py   (no pytest needed)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_DASHBOARD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_DASHBOARD))

from hapm import apply as hapm_apply  # noqa: E402
from hapm.apply import ApplyError, WhitelistError  # noqa: E402
from hapm.state import read_lock  # noqa: E402


# --- fixtures builders (plain functions so the no-pytest runner works) --------

WHITELISTED_FRAGMENT = """\
agent:
  max_turns: 40
  reasoning_effort: medium
  disabled_toolsets: []
toolsets:
  - file
  - terminal
delegation:
  enabled: false
kanban:
  default_assignee: fullstack-developer
approvals:
  mode: auto
"""

FORBIDDEN_FRAGMENT = """\
agent:
  max_turns: 40
model:
  api_key: sekret-should-never-be-touched
"""

# A pre-existing profile config that carries secrets/model/tokens the preset
# must never disturb.
EXISTING_CONFIG = """\
model:
  provider: anthropic
  api_key: SUPER-SECRET-KEY
telegram:
  token: tg-secret-token
agent:
  max_turns: 10
  reasoning_effort: low
security:
  sandbox: true
"""


def _make_preset(root: Path, slug: str, fragment: str, soul: str = "PRESET SOUL\n",
                 skill_files: dict | None = None) -> Path:
    d = root / slug
    (d / "skills").mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "slug": slug, "name": slug.title(), "description": "x",
        "version": "1.0.0", "compatibleAddons": ["*"],
    }), encoding="utf-8")
    (d / "SOUL.md").write_text(soul, encoding="utf-8")
    (d / "config.fragment.yaml").write_text(fragment, encoding="utf-8")
    for name, content in (skill_files or {"role.md": "preset skill\n"}).items():
        (d / "skills" / name).write_text(content, encoding="utf-8")
    return d


def _make_profile(root: Path, name: str = "work") -> Path:
    p = root / "profiles" / name
    (p / "skills").mkdir(parents=True)
    (p / "SOUL.md").write_text("ORIGINAL PROFILE SOUL\n", encoding="utf-8")
    (p / "skills" / "old.md").write_text("original skill\n", encoding="utf-8")
    (p / "config.yaml").write_text(EXISTING_CONFIG, encoding="utf-8")
    return p


# --- engine tests -------------------------------------------------------------

def test_apply_overwrites_soul_and_skills(tmp_path: Path):
    presets = tmp_path / "presets"
    _make_preset(presets, "fullstack-developer", WHITELISTED_FRAGMENT,
                 soul="FULLSTACK SOUL\n",
                 skill_files={"impl.md": "impl skill\n"})
    profile = _make_profile(tmp_path)

    hapm_apply.apply_preset(profile, "fullstack-developer", presets_root=presets)

    assert (profile / "SOUL.md").read_text() == "FULLSTACK SOUL\n"
    # skills/ fully replaced: preset skill present, original gone.
    assert (profile / "skills" / "impl.md").read_text() == "impl skill\n"
    assert not (profile / "skills" / "old.md").exists()


def test_apply_merges_only_whitelisted_config(tmp_path: Path):
    import yaml
    presets = tmp_path / "presets"
    _make_preset(presets, "fs", WHITELISTED_FRAGMENT)
    profile = _make_profile(tmp_path)

    hapm_apply.apply_preset(profile, "fs", presets_root=presets)

    cfg = yaml.safe_load((profile / "config.yaml").read_text())
    # Secrets / model / tokens untouched.
    assert cfg["model"]["api_key"] == "SUPER-SECRET-KEY"
    assert cfg["model"]["provider"] == "anthropic"
    assert cfg["telegram"]["token"] == "tg-secret-token"
    assert cfg["security"]["sandbox"] is True
    # Whitelisted keys merged/overridden.
    assert cfg["agent"]["max_turns"] == 40
    assert cfg["agent"]["reasoning_effort"] == "medium"
    assert cfg["kanban"]["default_assignee"] == "fullstack-developer"
    assert cfg["approvals"]["mode"] == "auto"
    assert cfg["delegation"]["enabled"] is False


def test_apply_records_lock(tmp_path: Path):
    presets = tmp_path / "presets"
    _make_preset(presets, "fs", WHITELISTED_FRAGMENT)
    profile = _make_profile(tmp_path)

    result = hapm_apply.apply_preset(profile, "fs", presets_root=presets)
    lock = read_lock(profile)
    assert lock is not None
    assert lock.active_preset == "fs"
    assert lock.preset_backup_id == result.backup_id


def test_forbidden_fragment_rejected_nothing_written(tmp_path: Path):
    presets = tmp_path / "presets"
    _make_preset(presets, "bad", FORBIDDEN_FRAGMENT)
    profile = _make_profile(tmp_path)
    soul_before = (profile / "SOUL.md").read_bytes()
    cfg_before = (profile / "config.yaml").read_bytes()

    try:
        hapm_apply.apply_preset(profile, "bad", presets_root=presets)
        raised = False
    except WhitelistError:
        raised = True
    assert raised, "expected WhitelistError for a forbidden fragment"
    # Nothing changed: no write happened before validation.
    assert (profile / "SOUL.md").read_bytes() == soul_before
    assert (profile / "config.yaml").read_bytes() == cfg_before
    assert (profile / "skills" / "old.md").exists()
    assert read_lock(profile) is None


def test_revert_restores_byte_identical(tmp_path: Path):
    presets = tmp_path / "presets"
    _make_preset(presets, "fs", WHITELISTED_FRAGMENT, soul="NEW SOUL\n",
                 skill_files={"new.md": "new\n"})
    profile = _make_profile(tmp_path)

    soul_before = (profile / "SOUL.md").read_bytes()
    cfg_before = (profile / "config.yaml").read_bytes()
    old_skill_before = (profile / "skills" / "old.md").read_bytes()

    hapm_apply.apply_preset(profile, "fs", presets_root=presets)
    # sanity: things actually changed
    assert (profile / "SOUL.md").read_bytes() != soul_before

    out = hapm_apply.revert_preset(profile)
    assert out["reverted_preset"] == "fs"

    # Byte-identical restoration of every managed artifact.
    assert (profile / "SOUL.md").read_bytes() == soul_before
    assert (profile / "config.yaml").read_bytes() == cfg_before
    assert (profile / "skills" / "old.md").read_bytes() == old_skill_before
    # Preset skill removed by the rollback.
    assert not (profile / "skills" / "new.md").exists()
    # Lock cleared.
    assert read_lock(profile) is None


def test_revert_without_apply_errors(tmp_path: Path):
    profile = _make_profile(tmp_path)
    try:
        hapm_apply.revert_preset(profile)
        raised = False
    except ApplyError:
        raised = True
    assert raised


def test_unknown_preset_errors(tmp_path: Path):
    presets = tmp_path / "presets"
    presets.mkdir()
    profile = _make_profile(tmp_path)
    try:
        hapm_apply.apply_preset(profile, "nope", presets_root=presets)
        raised = False
    except ApplyError:
        raised = True
    assert raised


def test_list_presets(tmp_path: Path):
    presets = tmp_path / "presets"
    _make_preset(presets, "alpha", WHITELISTED_FRAGMENT)
    _make_preset(presets, "beta", WHITELISTED_FRAGMENT)
    listing = hapm_apply.list_presets(presets_root=presets)
    slugs = [p.slug for p in listing]
    assert slugs == ["alpha", "beta"]


def test_validate_fragment_whitelist_negatives():
    for bad in (
        {"model": {"api_key": "x"}},
        {"terminal": {"cmd": "rm"}},
        {"agent": {"model": "gpt"}},          # non-whitelisted dotted under agent
        {"dashboard": {"port": 1}},
        {"whatsapp": {"token": "x"}},
    ):
        try:
            hapm_apply.validate_fragment_whitelist(bad)
            raised = False
        except WhitelistError:
            raised = True
        assert raised, f"expected rejection for {bad}"
    # Allowed fragments pass.
    hapm_apply.validate_fragment_whitelist({
        "agent": {"max_turns": 1, "reasoning_effort": "high", "disabled_toolsets": []},
        "toolsets": ["file"],
        "delegation": {"enabled": True, "max_children": 3},
        "kanban": {"default_assignee": "x"},
        "approvals": {"mode": "auto"},
    })


# --- route handler tests ------------------------------------------------------

def _import_plugin_api():
    import importlib
    if "plugin_api" in sys.modules:
        return importlib.reload(sys.modules["plugin_api"])
    import plugin_api  # noqa
    return plugin_api


def test_route_apply_and_revert(tmp_path: Path):
    presets = tmp_path / "presets"
    _make_preset(presets, "fs", WHITELISTED_FRAGMENT, soul="ROUTE SOUL\n")
    _make_profile(tmp_path, "work")
    os.environ["HERMES_HOME"] = str(tmp_path)
    os.environ["HAPM_PRESETS_DIR"] = str(presets)
    api = _import_plugin_api()

    soul_before = (tmp_path / "profiles" / "work" / "SOUL.md").read_bytes()

    listing = api.list_presets()
    details = next(p for p in listing["presets"] if p["slug"] == "fs")
    assert details["application"] == {
        "soul_markdown": "ROUTE SOUL\n",
        "skills": ["role.md"],
        "config_fragment": WHITELISTED_FRAGMENT,
    }

    applied = api.apply_preset({"profile": "work", "preset": "fs"})
    assert applied["status"] == "applied"
    assert (tmp_path / "profiles" / "work" / "SOUL.md").read_text() == "ROUTE SOUL\n"

    reverted = api.revert_preset({"profile": "work"})
    assert reverted["status"] == "reverted"
    assert (tmp_path / "profiles" / "work" / "SOUL.md").read_bytes() == soul_before

    os.environ.pop("HAPM_PRESETS_DIR", None)


def test_route_apply_errors(tmp_path: Path):
    presets = tmp_path / "presets"
    _make_preset(presets, "bad", FORBIDDEN_FRAGMENT)
    _make_profile(tmp_path, "work")
    os.environ["HERMES_HOME"] = str(tmp_path)
    os.environ["HAPM_PRESETS_DIR"] = str(presets)
    api = _import_plugin_api()

    # missing fields
    r = api.apply_preset({"profile": "work"})
    assert r.status_code == 400 and json.loads(r.body)["error"] == "missing_field"
    # unknown profile
    r = api.apply_preset({"profile": "ghost", "preset": "bad"})
    assert r.status_code == 404 and json.loads(r.body)["error"] == "unknown_profile"
    # whitelist violation -> 422, nothing written
    r = api.apply_preset({"profile": "work", "preset": "bad"})
    assert r.status_code == 422 and json.loads(r.body)["error"] == "whitelist_violation"
    assert read_lock(tmp_path / "profiles" / "work") is None

    os.environ.pop("HAPM_PRESETS_DIR", None)


# --- minimal no-pytest runner -------------------------------------------------

def _run_standalone() -> int:
    import tempfile
    import shutil

    tests = [
        test_apply_overwrites_soul_and_skills,
        test_apply_merges_only_whitelisted_config,
        test_apply_records_lock,
        test_forbidden_fragment_rejected_nothing_written,
        test_revert_restores_byte_identical,
        test_revert_without_apply_errors,
        test_unknown_preset_errors,
        test_list_presets,
        test_validate_fragment_whitelist_negatives,
        test_route_apply_and_revert,
        test_route_apply_errors,
    ]
    saved_home = os.environ.get("HERMES_HOME")
    failures = 0
    for t in tests:
        needs_tmp = t.__code__.co_argcount == 1
        d = Path(tempfile.mkdtemp())
        try:
            t(d) if needs_tmp else t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {t.__name__}: {exc!r}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    if saved_home is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = saved_home
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_standalone())
