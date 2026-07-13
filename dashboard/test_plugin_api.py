"""Unit tests for the HAPM profile-discovery endpoint (FR-2).

These tests exercise the ``list_profiles`` route handler directly (no HTTP
server / TestClient required), so they run with only ``fastapi`` installed —
the same dependency the dashboard already provides to the plugin.

Covered scenarios (from the task acceptance criteria):
  - empty profiles dir      -> 200, empty list
  - multiple profiles       -> 200, sorted list with name + path, no contents
  - isolated profile home   -> 200, its parent profiles collection is listed
  - missing profiles dir    -> 404 structured error
  - profiles path is a file -> 400 structured error
  - unreadable profiles dir -> 403 structured error (skipped when running as
                               root, where chmod 000 does not block reads)

Run with:  pytest dashboard/test_plugin_api.py
     or:   python dashboard/test_plugin_api.py   (no pytest needed)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plugin_api  # noqa: E402


def _call_profiles(monkeypatch_home: Path):
    """Point HERMES_HOME at a temp dir and invoke the route handler.

    Returns (status_code, body_dict). For the success path FastAPI would
    serialize the returned dict as-is (status 200); for error paths the
    handler returns a JSONResponse we decode here.
    """
    os.environ["HERMES_HOME"] = str(monkeypatch_home)
    result = plugin_api.list_profiles()
    # JSONResponse (error paths) vs plain dict (success path)
    if hasattr(result, "body"):
        return result.status_code, json.loads(result.body)
    return 200, result


def test_empty_profiles_dir(tmp_path: Path):
    (tmp_path / "profiles").mkdir()
    status, body = _call_profiles(tmp_path)
    assert status == 200
    assert body["profiles"] == []
    assert body["profiles_dir"] == str(tmp_path / "profiles")


def test_multiple_profiles(tmp_path: Path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    for name in ("work", "personal", "research"):
        p = profiles / name
        p.mkdir()
        # Files that must NOT leak into the listing:
        (p / "SOUL.md").write_text("secret soul contents")
        (p / "config.yaml").write_text("secret: config")
    # A stray file at the profiles root should be ignored (only dirs count).
    (profiles / "README.txt").write_text("not a profile")

    status, body = _call_profiles(tmp_path)
    assert status == 200
    names = [entry["name"] for entry in body["profiles"]]
    # sorted, only directories, stray file excluded
    assert names == ["personal", "research", "work"]
    for entry in body["profiles"]:
        assert set(entry.keys()) == {"name", "path"}
        assert entry["path"] == str(profiles / entry["name"])
        # No file contents anywhere in the payload.
        assert "secret" not in json.dumps(entry)


def test_isolated_profile_home_uses_parent_profiles_collection(tmp_path: Path):
    profiles = tmp_path / "profiles"
    isolated_home = profiles / "ceo-orchestrator"
    isolated_home.mkdir(parents=True)
    (profiles / "fullstack-developer").mkdir()

    status, body = _call_profiles(isolated_home)

    assert status == 200
    assert body["profiles_dir"] == str(profiles)
    assert [entry["name"] for entry in body["profiles"]] == [
        "ceo-orchestrator",
        "fullstack-developer",
    ]


def test_missing_profiles_dir(tmp_path: Path):
    # tmp_path has no "profiles" subdir.
    status, body = _call_profiles(tmp_path)
    assert status == 404
    assert body["error"] == "profiles_dir_missing"
    assert "profiles_dir" in body


def test_profiles_path_is_file(tmp_path: Path):
    (tmp_path / "profiles").write_text("i am a file, not a dir")
    status, body = _call_profiles(tmp_path)
    assert status == 400
    assert body["error"] == "profiles_dir_not_a_directory"


def test_unreadable_profiles_dir(tmp_path: Path):
    if os.geteuid() == 0:  # pragma: no cover - root bypasses perms
        return  # chmod 000 does not block root; skip.
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    profiles.chmod(0o000)
    try:
        status, body = _call_profiles(tmp_path)
        assert status == 403
        assert body["error"] == "profiles_dir_unreadable"
    finally:
        profiles.chmod(0o755)  # restore so tmp cleanup works


# --- minimal no-pytest runner -------------------------------------------------

def _run_standalone() -> int:
    import tempfile
    import shutil

    tests = [
        test_empty_profiles_dir,
        test_multiple_profiles,
        test_isolated_profile_home_uses_parent_profiles_collection,
        test_missing_profiles_dir,
        test_profiles_path_is_file,
        test_unreadable_profiles_dir,
    ]
    saved_home = os.environ.get("HERMES_HOME")
    failures = 0
    for t in tests:
        d = Path(tempfile.mkdtemp())
        try:
            t(d)
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
