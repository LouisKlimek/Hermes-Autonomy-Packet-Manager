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

IMPORTANT: plugin API routes are mounted only when the dashboard process
starts. After installing or updating this plugin you must restart
``hermes dashboard`` for these routes to load — a browser refresh or a plugin
rescan alone will NOT mount them.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

# The dashboard mounts this router at /api/plugins/hapm/ at process start.
router = APIRouter()


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
