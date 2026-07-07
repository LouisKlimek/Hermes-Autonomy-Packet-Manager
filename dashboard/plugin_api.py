"""HAPM (Hermes Autonomy Packet Manager) dashboard plugin — backend.

Scaffold shell only. This module exposes a FastAPI ``router`` that the Hermes
dashboard imports and mounts at ``/api/plugins/hapm/`` (the mount prefix is
derived from the plugin ``name`` in ``dashboard/manifest.json``), mirroring the
mounting pattern used by the Hermes-Tasklist-Plugin's ``plugin_api.py``.

No business logic yet: only a minimal health/ping route so the mount can be
verified end-to-end. Later tasks add the real endpoints (profile discovery,
preset registry, addon registry, apply/revert state management — see the HAPM
PRD FR-2..FR-9).

IMPORTANT: plugin API routes are mounted only when the dashboard process
starts. After installing or updating this plugin you must restart
``hermes dashboard`` for these routes to load — a browser refresh or a plugin
rescan alone will NOT mount them.
"""

from __future__ import annotations

from fastapi import APIRouter

# The dashboard mounts this router at /api/plugins/hapm/ at process start.
router = APIRouter()


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
