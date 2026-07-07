"""Unit tests for the HAPM state/lock + backup/restore engine (FR-7).

Run with: ``pytest dashboard/tests`` (or ``python -m pytest``) from the repo
root, or ``python dashboard/tests/test_state_engine.py`` for a stdlib-only run
without pytest installed.

The central acceptance criterion (PRD FR-7 / task): *apply then fully revert
produces byte-identical SOUL.md / config.yaml / skills to the pre-change state.*
``test_full_apply_then_revert_is_byte_identical`` proves exactly that.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

# Make the hapm package importable whether run via pytest from repo root or
# directly as a script.
_HERE = Path(__file__).resolve()
_DASHBOARD = _HERE.parent.parent  # .../dashboard
if str(_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD))

from hapm import (  # noqa: E402
    AddonState,
    BackupStore,
    CentralIndex,
    LockState,
    add_addon_skills,
    addon_block_markers,
    default_index_path,
    has_addon_block,
    list_addon_blocks,
    read_lock,
    remove_addon_block,
    remove_addon_skills,
    upsert_addon_block,
    write_lock,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _tree_digest(root: Path) -> dict[str, str]:
    """Map of relative-path -> sha256 for every file under ``root``.

    Used to assert byte-identical state before/after a revert. Ignores the
    ``.hapm`` backup dir and ``hapm.lock`` since those are HAPM bookkeeping,
    not user content.
    """
    digest: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".hapm") or rel == "hapm.lock":
            continue
        if p.is_file():
            digest[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif p.is_dir():
            digest[rel + "/"] = "<dir>"
    return digest


def _make_profile(tmp_path: Path) -> Path:
    """Create a realistic pre-change profile fixture."""
    prof = tmp_path / "profiles" / "fullstack-developer"
    (prof / "skills" / "existing-skill").mkdir(parents=True)
    (prof / "SOUL.md").write_text(
        "# SOUL\n\nUser-authored line one.\nUser-authored line two.\n",
        encoding="utf-8",
    )
    (prof / "config.yaml").write_text(
        "agent:\n  max_turns: 40\ntoolsets:\n  - web\n",
        encoding="utf-8",
    )
    (prof / "skills" / "existing-skill" / "SKILL.md").write_text(
        "pre-existing skill content\n", encoding="utf-8"
    )
    return prof


# --------------------------------------------------------------------------
# lock state
# --------------------------------------------------------------------------

def test_lock_roundtrip(tmp_path: Path) -> None:
    prof = tmp_path / "p"
    prof.mkdir()
    assert read_lock(prof) is None

    state = LockState(profile="p", active_preset="Fullstack Developer",
                      preset_backup_id="bk1")
    state.set_addon(AddonState(addon_id="yagni", mode="prompt", soul_block=True))
    write_lock(prof, state)

    loaded = read_lock(prof)
    assert loaded is not None
    assert loaded.active_preset == "Fullstack Developer"
    assert loaded.preset_backup_id == "bk1"
    assert loaded.get_addon("yagni").mode == "prompt"
    assert loaded.get_addon("yagni").soul_block is True


def test_lock_removed_when_inactive(tmp_path: Path) -> None:
    prof = tmp_path / "p"
    prof.mkdir()
    state = LockState(profile="p")
    state.set_addon(AddonState(addon_id="yagni", mode="prompt"))
    write_lock(prof, state)
    assert (prof / "hapm.lock").exists()

    # Removing the only addon makes it inactive -> lock file deleted.
    state.remove_addon("yagni")
    write_lock(prof, state)
    assert not (prof / "hapm.lock").exists()
    assert read_lock(prof) is None


# --------------------------------------------------------------------------
# backup / restore
# --------------------------------------------------------------------------

def test_backup_restore_file_and_dir(tmp_path: Path) -> None:
    prof = _make_profile(tmp_path)
    store = BackupStore(prof)
    before = _tree_digest(prof)

    bk = store.create(["SOUL.md", "skills", "config.yaml"])

    # Mutate everything destructively.
    (prof / "SOUL.md").write_text("clobbered\n", encoding="utf-8")
    (prof / "config.yaml").write_text("clobbered\n", encoding="utf-8")
    import shutil
    shutil.rmtree(prof / "skills")
    (prof / "skills").mkdir()
    (prof / "skills" / "injected").mkdir()

    store.restore(bk)
    assert _tree_digest(prof) == before


def test_backup_restore_absent_target_is_removed(tmp_path: Path) -> None:
    prof = _make_profile(tmp_path)
    store = BackupStore(prof)

    # config-extra does not exist at backup time.
    bk = store.create(["config-extra.yaml"])
    (prof / "config-extra.yaml").write_text("added later\n", encoding="utf-8")

    store.restore(bk)
    assert not (prof / "config-extra.yaml").exists()


def test_backup_rejects_path_escape(tmp_path: Path) -> None:
    prof = _make_profile(tmp_path)
    store = BackupStore(prof)
    from hapm import BackupError
    raised = False
    try:
        store.create(["../../etc/passwd"])
    except BackupError:
        raised = True
    assert raised, "path escape should have raised BackupError"


# --------------------------------------------------------------------------
# SOUL.md marked blocks
# --------------------------------------------------------------------------

def test_soul_block_insert_and_remove_byte_identical() -> None:
    original = "# SOUL\n\nUser line A.\nUser line B.\n"
    start, end = addon_block_markers("yagni")

    injected = upsert_addon_block(original, "yagni", "YAGNI: keep it simple.")
    assert start in injected and end in injected
    assert has_addon_block(injected, "yagni")
    assert "User line A." in injected and "User line B." in injected

    removed = remove_addon_block(injected, "yagni")
    assert removed == original  # byte-identical round trip


def test_soul_block_upsert_replaces_in_place() -> None:
    original = "# SOUL\n\nkeep me\n"
    v1 = upsert_addon_block(original, "yagni", "mode one")
    v2 = upsert_addon_block(v1, "yagni", "mode two")
    assert v2.count("HAPM:addon:yagni START") == 1
    assert "mode two" in v2 and "mode one" not in v2
    assert remove_addon_block(v2, "yagni") == original


def test_soul_block_remove_preserves_other_addons() -> None:
    base = "# SOUL\n\nuser text\n"
    a = upsert_addon_block(base, "yagni", "y")
    ab = upsert_addon_block(a, "other", "o")
    assert set(list_addon_blocks(ab)) == {"yagni", "other"}
    only_other = remove_addon_block(ab, "yagni")
    assert not has_addon_block(only_other, "yagni")
    assert has_addon_block(only_other, "other")
    assert "user text" in only_other


def test_soul_block_remove_missing_is_noop() -> None:
    text = "# SOUL\nno blocks here\n"
    assert remove_addon_block(text, "yagni") == text


# --------------------------------------------------------------------------
# skills tracker (added vs shadowed)
# --------------------------------------------------------------------------

def test_skills_added_then_removed(tmp_path: Path) -> None:
    prof = _make_profile(tmp_path)
    before = _tree_digest(prof)

    # A source skill dir the addon contributes.
    src = tmp_path / "registry" / "yagni-skill"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("yagni skill\n", encoding="utf-8")

    contrib = add_addon_skills(prof, {"yagni": src})
    assert contrib.added_paths == ["skills/yagni"]
    assert contrib.shadowed_paths == []
    assert (prof / "skills" / "yagni" / "SKILL.md").exists()

    remove_addon_skills(prof, contrib)
    assert not (prof / "skills" / "yagni").exists()
    assert _tree_digest(prof) == before


def test_skills_shadow_preexisting_then_restore(tmp_path: Path) -> None:
    prof = _make_profile(tmp_path)
    before = _tree_digest(prof)

    # Addon contributes a skill with the SAME name as the pre-existing one.
    src = tmp_path / "registry" / "existing-skill"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("ADDON version of skill\n", encoding="utf-8")

    contrib = add_addon_skills(prof, {"existing-skill": src})
    assert contrib.added_paths == []
    assert contrib.shadowed_paths == ["skills/existing-skill"]
    assert contrib.shadow_backup_id is not None
    # The addon version now shadows the original.
    assert (prof / "skills" / "existing-skill" / "SKILL.md").read_text() == \
        "ADDON version of skill\n"

    remove_addon_skills(prof, contrib)
    # Pre-existing skill restored byte-exactly, addon version gone.
    assert _tree_digest(prof) == before
    assert (prof / "skills" / "existing-skill" / "SKILL.md").read_text() == \
        "pre-existing skill content\n"


# --------------------------------------------------------------------------
# central index
# --------------------------------------------------------------------------

def test_central_index_tracks_active_profiles(tmp_path: Path) -> None:
    idx = CentralIndex(tmp_path / "hapm_index.json")
    assert idx.active_profiles() == []

    s = LockState(profile="dev", active_preset="Fullstack Developer")
    s.set_addon(AddonState(addon_id="yagni", mode="prompt"))
    idx.update_from_lock(s)
    assert idx.active_profiles() == ["dev"]
    entry = idx.get("dev")
    assert entry["preset"] == "Fullstack Developer"
    assert entry["addons"][0]["addon_id"] == "yagni"

    # Reverting removes it from the index.
    empty = LockState(profile="dev")
    idx.update_from_lock(empty)
    assert idx.active_profiles() == []


def test_default_index_path_uses_hermes_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermeshome"))
    p = default_index_path()
    assert p == tmp_path / "hermeshome" / "hapm_index.json"


# --------------------------------------------------------------------------
# integration: full apply -> full revert == byte identical
# --------------------------------------------------------------------------

def test_full_apply_then_revert_is_byte_identical(tmp_path: Path) -> None:
    """End-to-end FR-7 acceptance: preset apply + addon toggles, then full
    revert, leaves SOUL.md / config.yaml / skills byte-identical to the start.
    """
    prof = _make_profile(tmp_path)
    before = _tree_digest(prof)
    index = CentralIndex(tmp_path / "hapm_index.json")
    store = BackupStore(prof)
    lock = LockState(profile="fullstack-developer")

    # --- 1. "apply preset": back up + overwrite SOUL/config/skills --------
    preset_bk = store.create(["SOUL.md", "config.yaml", "skills"])
    (prof / "SOUL.md").write_text("# PRESET SOUL\n\npreset body\n", encoding="utf-8")
    (prof / "config.yaml").write_text("agent:\n  max_turns: 80\n", encoding="utf-8")
    lock.active_preset = "Fullstack Developer"
    lock.preset_backup_id = preset_bk
    write_lock(prof, lock)
    index.update_from_lock(lock)

    # --- 2. enable addon "yagni" mode B (prompt): SOUL block --------------
    soul = (prof / "SOUL.md").read_text(encoding="utf-8")
    (prof / "SOUL.md").write_text(
        upsert_addon_block(soul, "yagni", "YAGNI: build only what is needed."),
        encoding="utf-8",
    )
    lock.set_addon(AddonState(addon_id="yagni", mode="prompt", soul_block=True))
    write_lock(prof, lock)
    index.update_from_lock(lock)

    # --- 3. enable addon "extra": contributes a skill (shadows existing) --
    src = tmp_path / "registry" / "existing-skill"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("addon skill body\n", encoding="utf-8")
    contrib = add_addon_skills(prof, {"existing-skill": src}, backup_store=store)
    lock.set_addon(AddonState(
        addon_id="extra", mode="on",
        backup_id=contrib.shadow_backup_id,
        skill_paths=contrib.all_paths,
    ))
    write_lock(prof, lock)
    index.update_from_lock(lock)

    assert index.active_profiles() == ["fullstack-developer"]

    # ================= now fully revert, newest first ====================

    # revert addon "extra"
    remove_addon_skills(prof, contrib, backup_store=store)
    lock.remove_addon("extra")
    write_lock(prof, lock)

    # revert addon "yagni" (remove SOUL block)
    soul = (prof / "SOUL.md").read_text(encoding="utf-8")
    (prof / "SOUL.md").write_text(remove_addon_block(soul, "yagni"), encoding="utf-8")
    lock.remove_addon("yagni")
    write_lock(prof, lock)

    # revert preset (restore original SOUL/config/skills)
    store.restore(preset_bk)
    lock.active_preset = None
    lock.preset_backup_id = None
    write_lock(prof, lock)
    index.update_from_lock(lock)

    # --- assertions -------------------------------------------------------
    assert _tree_digest(prof) == before, "profile not byte-identical after revert"
    assert not (prof / "hapm.lock").exists(), "lock should be gone when inactive"
    assert index.active_profiles() == [], "index should be empty after revert"


# --------------------------------------------------------------------------
# stdlib-only runner (no pytest required)
# --------------------------------------------------------------------------

def _run_without_pytest() -> int:
    import tempfile
    import traceback

    class _MonkeyPatch:
        def __init__(self) -> None:
            self._env: list[str] = []

        def setenv(self, k: str, v: str) -> None:
            self._env.append(k)
            os.environ[k] = v

        def undo(self) -> None:
            for k in self._env:
                os.environ.pop(k, None)

    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    passed = failed = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mp = _MonkeyPatch()
            argnames = fn.__code__.co_varnames[: fn.__code__.co_argcount]
            kwargs = {}
            if "tmp_path" in argnames:
                kwargs["tmp_path"] = tmp
            if "monkeypatch" in argnames:
                kwargs["monkeypatch"] = mp
            try:
                fn(**kwargs)
                passed += 1
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
            finally:
                mp.undo()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_without_pytest())
