"""Unit tests for the HAPM per-profile status endpoint (FR-9).

These tests exercise the ``profile_status`` route handler directly (no HTTP
server / TestClient required), so they run with only ``fastapi`` installed —
the same dependency the dashboard already provides to the plugin.

Covered scenarios (from the task acceptance criteria):
  - no hapm.lock (never touched)   -> 200 well-defined empty state (no error)
  - preset + addons applied        -> 200 reflects lock exactly
  - lock changes between calls      -> status updates (no stale reads),
                                       including the documented FR-9 verification
                                       (apply preset + 2 addons, disable 1)
  - unknown profile                 -> 404 structured error
  - path-traversal profile name     -> 400 structured error
  - corrupt lock JSON               -> 500 structured error (never a stack trace)

The lock JSON written here matches the state engine's on-disk schema
(``dashboard/hapm/state.py``: ``LockState.to_dict`` / ``AddonState``), so this
endpoint is verified against the same single source of truth FR-4/FR-6 write.

Run with:  pytest dashboard/test_status_endpoint.py
     or:   python dashboard/test_status_endpoint.py   (no pytest needed)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plugin_api  # noqa: E402


def _call_status(home: Path, profile: str):
    """Point HERMES_HOME at a temp dir and invoke the status handler.

    Returns (status_code, body_dict). Success returns a plain dict (status 200);
    error paths return a JSONResponse we decode here.
    """
    os.environ["HERMES_HOME"] = str(home)
    result = plugin_api.profile_status(profile)
    if hasattr(result, "body"):
        return result.status_code, json.loads(result.body)
    return 200, result


def _make_profile(home: Path, name: str) -> Path:
    p = home / "profiles" / name
    p.mkdir(parents=True)
    return p


def _write_lock(profile_dir: Path, *, active_preset, addons) -> None:
    """Write a hapm.lock in the state-engine on-disk schema."""
    payload = {
        "schema_version": 1,
        "profile": profile_dir.name,
        "active_preset": active_preset,
        "preset_backup_id": "bk-preset" if active_preset else None,
        "addons": [
            {
                "addon_id": a["addon_id"],
                "mode": a["mode"],
                "backup_id": a.get("backup_id"),
                "soul_block": a.get("soul_block", False),
                "skill_paths": a.get("skill_paths", []),
            }
            for a in addons
        ],
    }
    (profile_dir / "hapm.lock").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def test_no_lock_empty_state(tmp_path: Path):
    _make_profile(tmp_path, "work")
    status, body = _call_status(tmp_path, "work")
    assert status == 200
    assert body["lock_present"] is False
    assert body["active_preset"] is None
    assert body["addons"] == []


def test_isolated_profile_home_resolves_status_from_parent_collection(tmp_path: Path):
    isolated_home = _make_profile(tmp_path, "ceo-orchestrator")
    work = _make_profile(tmp_path, "work")

    status, body = _call_status(isolated_home, "work")

    assert status == 200
    assert body["profile_dir"] == str(work)


def test_preset_and_addons(tmp_path: Path):
    prof = _make_profile(tmp_path, "work")
    _write_lock(
        prof,
        active_preset="fullstack-developer",
        addons=[
            {"addon_id": "yagni", "mode": "prompt"},
            {"addon_id": "tdd", "mode": "full"},
        ],
    )
    status, body = _call_status(tmp_path, "work")
    assert status == 200
    assert body["lock_present"] is True
    assert body["active_preset"] == "fullstack-developer"
    # Each addon surfaces exactly addon_id + mode (nothing else leaks).
    assert body["addons"] == [
        {"addon_id": "yagni", "mode": "prompt"},
        {"addon_id": "tdd", "mode": "full"},
    ]
    # Backup ids / internal bookkeeping must not leak into the status payload.
    assert "backup_id" not in json.dumps(body)
    assert "preset_backup_id" not in json.dumps(body)


def test_no_stale_reads_documented_verification(tmp_path: Path):
    """FR-9 documented verification: apply preset + 2 addons, verify status;
    disable 1 addon, verify status updated (lock re-read live, no caching)."""
    prof = _make_profile(tmp_path, "work")

    # Apply a preset + enable 2 addons.
    _write_lock(
        prof,
        active_preset="fullstack-developer",
        addons=[
            {"addon_id": "yagni", "mode": "prompt"},
            {"addon_id": "tdd", "mode": "full"},
        ],
    )
    status, body = _call_status(tmp_path, "work")
    assert status == 200
    assert body["active_preset"] == "fullstack-developer"
    assert [a["addon_id"] for a in body["addons"]] == ["yagni", "tdd"]

    # Disable one addon (rewrite the lock, as FR-6 toggle would).
    _write_lock(
        prof,
        active_preset="fullstack-developer",
        addons=[{"addon_id": "yagni", "mode": "prompt"}],
    )
    status2, body2 = _call_status(tmp_path, "work")
    assert status2 == 200
    # Status reflects the change immediately — no stale read.
    assert [a["addon_id"] for a in body2["addons"]] == ["yagni"]
    assert body2["addons"] == [{"addon_id": "yagni", "mode": "prompt"}]


def test_unknown_profile(tmp_path: Path):
    (tmp_path / "profiles").mkdir()
    status, body = _call_status(tmp_path, "does-not-exist")
    assert status == 404
    assert body["error"] == "profile_not_found"


def test_path_traversal_rejected(tmp_path: Path):
    (tmp_path / "profiles").mkdir()
    for bad in ("../secret", "..", "a/b", "/etc"):
        status, body = _call_status(tmp_path, bad)
        assert status == 400, f"expected 400 for {bad!r}"
        assert body["error"] == "invalid_profile_name"


def test_corrupt_lock(tmp_path: Path):
    prof = _make_profile(tmp_path, "work")
    (prof / "hapm.lock").write_text("{ not valid json", encoding="utf-8")
    status, body = _call_status(tmp_path, "work")
    assert status == 500
    assert body["error"] == "corrupt_hapm_lock"
    # Structured body, not a raw stack trace.
    assert "lock_path" in body


# --- minimal no-pytest runner -------------------------------------------------

def _run_standalone() -> int:
    import tempfile
    import shutil

    tests = [
        test_no_lock_empty_state,
        test_isolated_profile_home_resolves_status_from_parent_collection,
        test_preset_and_addons,
        test_no_stale_reads_documented_verification,
        test_unknown_profile,
        test_path_traversal_rejected,
        test_corrupt_lock,
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
