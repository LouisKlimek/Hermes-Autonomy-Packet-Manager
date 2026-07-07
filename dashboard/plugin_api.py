"""HAPM (Hermes Autonomy Packet Manager) dashboard plugin — backend.

This module exposes a FastAPI ``router`` that the Hermes dashboard imports and
mounts at ``/api/plugins/hapm/`` (the mount prefix is derived from the plugin
``name`` in ``dashboard/manifest.json``), mirroring the mounting pattern used
by the Hermes-Tasklist-Plugin's ``plugin_api.py``.

Routes:
  - GET /api/plugins/hapm/health   liveness probe
  - GET /api/plugins/hapm/ping     trivial ping
  - GET /api/plugins/hapm/profiles list locally available Hermes profiles
                                   under ``$HERMES_HOME/profiles/`` (FR-2)
  - GET /api/plugins/hapm/profiles/{profile}/status
                                   per-profile active preset + active addons
                                   (with mode), read live from that profile's
                                   ``hapm.lock`` (FR-9)

IMPORTANT: plugin API routes are mounted only when the dashboard process
starts. After installing or updating this plugin you must restart
``hermes dashboard`` for these routes to load — a browser refresh or a plugin
rescan alone will NOT mount them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

# The dashboard mounts this router at /api/plugins/hapm/ at process start.
router = APIRouter()

# Filename of the per-profile HAPM state/lock record (single source of truth
# for what HAPM currently manages on a profile). Kept in sync with the state
# engine (``dashboard/hapm/state.py``: ``HAPM_LOCK_FILENAME``). This endpoint
# reads it live on every request — no caching — so status never drifts from
# what a preset-apply (FR-4) or addon-toggle (FR-6) just wrote.
HAPM_LOCK_FILENAME = "hapm.lock"


def _hermes_home() -> Path:
    """Return the Hermes home directory.

    Resolves ``$HERMES_HOME`` the same way core Hermes does: respect the
    ``HERMES_HOME`` env var when set (and non-empty), otherwise fall back to
    the default ``~/.hermes``. This mirrors ``hermes_constants.get_hermes_home``
    and the resolution used by the sibling Hermes dashboard plugins.
    """
    val = os.environ.get("HERMES_HOME", "").strip()
    return Path(val) if val else (Path.home() / ".hermes")


@router.get("/health")
def health() -> dict:
    """Liveness probe for the HAPM backend mount.

    Reachable at ``GET /api/plugins/hapm/health`` once the dashboard has
    mounted the plugin. Returns a small static payload so the install can be
    verified without any real state.
    """
    return {"plugin": "hapm", "status": "ok", "version": "0.1.0"}


@router.get("/ping")
def ping() -> dict:
    """Trivial ping endpoint at ``GET /api/plugins/hapm/ping``."""
    return {"pong": True}


@router.get("/profiles")
def list_profiles():
    """List locally available Hermes profiles (FR-2).

    Reachable at ``GET /api/plugins/hapm/profiles``. Scans
    ``$HERMES_HOME/profiles/`` and returns each immediate sub-directory as a
    profile, so the UI can present a profile picker.

    Each entry contains only the profile ``name`` and absolute ``path`` — no
    file contents (SOUL.md / config.yaml) are read or returned by this
    listing endpoint.

    Errors are returned as a structured JSON body (never a 500 stack trace):
      - ``profiles_dir_missing`` (404) when ``$HERMES_HOME/profiles/`` does
        not exist.
      - ``profiles_dir_not_a_directory`` (400) when the path exists but is not
        a directory.
      - ``profiles_dir_unreadable`` (403) when the directory cannot be read
        (e.g. permission denied).
    """
    profiles_dir = _hermes_home() / "profiles"

    if not profiles_dir.exists():
        return JSONResponse(
            status_code=404,
            content={
                "error": "profiles_dir_missing",
                "message": (
                    "The Hermes profiles directory does not exist: "
                    f"{profiles_dir}"
                ),
                "profiles_dir": str(profiles_dir),
            },
        )

    if not profiles_dir.is_dir():
        return JSONResponse(
            status_code=400,
            content={
                "error": "profiles_dir_not_a_directory",
                "message": (
                    "The Hermes profiles path exists but is not a directory: "
                    f"{profiles_dir}"
                ),
                "profiles_dir": str(profiles_dir),
            },
        )

    try:
        entries = sorted(
            entry for entry in profiles_dir.iterdir() if entry.is_dir()
        )
    except (PermissionError, OSError) as exc:
        return JSONResponse(
            status_code=403,
            content={
                "error": "profiles_dir_unreadable",
                "message": (
                    "The Hermes profiles directory could not be read: "
                    f"{exc}"
                ),
                "profiles_dir": str(profiles_dir),
            },
        )

    profiles = [
        {"name": entry.name, "path": str(entry)} for entry in entries
    ]
    return {"profiles_dir": str(profiles_dir), "profiles": profiles}


def _empty_status(profile: str, profile_dir: Path) -> dict:
    """Well-defined empty state for a profile HAPM has never touched.

    Returned (with HTTP 200) when the profile exists but has no ``hapm.lock``:
    no preset applied and no addons active. This is a valid, expected state —
    not an error — so the UI can render "nothing applied" cleanly.
    """
    return {
        "profile": profile,
        "profile_dir": str(profile_dir),
        "lock_present": False,
        "active_preset": None,
        "addons": [],
    }


@router.get("/profiles/{profile}/status")
def profile_status(profile: str):
    """Per-profile HAPM status (FR-9).

    Reachable at ``GET /api/plugins/hapm/profiles/{profile}/status``. Reads the
    profile's ``hapm.lock`` **live on every call** (single source of truth — no
    caching that could drift) and returns:

      - ``active_preset``: the applied preset name, or ``None`` if none.
      - ``addons``: list of currently active addons, each ``{addon_id, mode}``,
        so the UI can show every addon with its current mode (FR-9).

    Because the lock is re-read on each request, status reflects reality
    immediately after any FR-4 preset-apply or FR-6 addon-toggle — there are no
    stale reads.

    Empty / error semantics (structured JSON bodies, never a 500 stack trace):
      - profile has **no** ``hapm.lock`` (never touched by HAPM) -> 200 with a
        well-defined empty state (``lock_present: false``, ``active_preset:
        null``, ``addons: []``) rather than an error.
      - ``invalid_profile_name`` (400) when the name is empty or contains path
        separators / traversal (``/``, ``\\``, ``..``) — the name must be a
        single profile directory, never a path.
      - ``profile_not_found`` (404) when ``$HERMES_HOME/profiles/{profile}`` is
        not an existing directory.
      - ``corrupt_hapm_lock`` (500) when the lock file exists but is not valid
        JSON / not an object.
    """
    # Reject anything that is not a bare profile directory name. This prevents
    # path traversal (``../../etc``) and absolute paths from escaping the
    # profiles root.
    if (
        not profile
        or "/" in profile
        or "\\" in profile
        or profile in (".", "..")
        or os.sep in profile
        or (os.altsep and os.altsep in profile)
    ):
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_profile_name",
                "message": (
                    "Profile must be a single directory name without path "
                    f"separators: {profile!r}"
                ),
                "profile": profile,
            },
        )

    profile_dir = _hermes_home() / "profiles" / profile
    if not profile_dir.is_dir():
        return JSONResponse(
            status_code=404,
            content={
                "error": "profile_not_found",
                "message": f"No such profile directory: {profile_dir}",
                "profile": profile,
                "profile_dir": str(profile_dir),
            },
        )

    lock_file = profile_dir / HAPM_LOCK_FILENAME
    if not lock_file.exists():
        # Never touched by HAPM -> well-defined empty state, not an error.
        return _empty_status(profile, profile_dir)

    try:
        data = json.loads(lock_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": "corrupt_hapm_lock",
                "message": f"Could not read/parse hapm.lock: {exc}",
                "profile": profile,
                "lock_path": str(lock_file),
            },
        )

    if not isinstance(data, dict):
        return JSONResponse(
            status_code=500,
            content={
                "error": "corrupt_hapm_lock",
                "message": "hapm.lock did not contain a JSON object.",
                "profile": profile,
                "lock_path": str(lock_file),
            },
        )

    # Project the lock into the status shape. We surface only what the UI needs
    # to render the status view (active preset + each active addon with its
    # mode); backup ids and internal bookkeeping stay in the lock.
    active_preset = data.get("active_preset")
    addons = []
    for entry in data.get("addons") or []:
        if not isinstance(entry, dict):
            continue
        addons.append(
            {
                "addon_id": entry.get("addon_id"),
                "mode": entry.get("mode"),
            }
        )

    return {
        "profile": profile,
        "profile_dir": str(profile_dir),
        "lock_present": True,
        "active_preset": active_preset,
        "addons": addons,
    }
