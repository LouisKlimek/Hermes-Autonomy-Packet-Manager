"""HAPM (Hermes Autonomy Packet Manager) dashboard plugin — backend.

This module exposes a FastAPI ``router`` that the Hermes dashboard imports and
mounts at ``/api/plugins/hapm/`` (the mount prefix is derived from the plugin
``name`` in ``dashboard/manifest.json``), mirroring the mounting pattern used
by the Hermes-Tasklist-Plugin's ``plugin_api.py``.

Routes:
  - GET  /api/plugins/hapm/health   liveness probe
  - GET  /api/plugins/hapm/ping     trivial ping
  - GET  /api/plugins/hapm/profiles list locally available Hermes profiles
                                    under ``$HERMES_HOME/profiles/`` (FR-2)
  - GET  /api/plugins/hapm/addons   list addons compatible with a given
                                    profile/preset target (FR-6a)
  - POST /api/plugins/hapm/addons/enable   enable an addon on a profile (FR-6b)
  - POST /api/plugins/hapm/addons/disable  disable an addon on a profile (FR-6c)

IMPORTANT: plugin API routes are mounted only when the dashboard process
starts. After installing or updating this plugin you must restart
``hermes dashboard`` for these routes to load — a browser refresh or a plugin
rescan alone will NOT mount them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

# The FR-6/FR-7 engine lives in the ``hapm`` package next to this module. Make
# it importable whether the dashboard adds ``dashboard/`` to sys.path or not.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from hapm import (  # noqa: E402
    AddonAlreadyEnabledError,
    AddonConflictError,
    AddonNotCompatibleError,
    AddonNotEnabledError,
    RegistryError,
    ToggleError,
    compatible_addons,
    disable_addon,
    enable_addon,
    list_active_addons,
    load_addon,
)

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


def _addons_root() -> Path:
    """Return the addon registry root (``addons/`` at the repo top level).

    This module lives at ``dashboard/plugin_api.py``, so the registry is one
    directory up. ``$HAPM_ADDONS_ROOT`` overrides for tests/custom installs.
    """
    val = os.environ.get("HAPM_ADDONS_ROOT", "").strip()
    if val:
        return Path(val)
    return _HERE.parent / "addons"


def _profile_dir(profile: str) -> Path:
    return _hermes_home() / "profiles" / profile


def _err(status: int, error: str, message: str, **extra) -> JSONResponse:
    body = {"error": error, "message": message}
    body.update(extra)
    return JSONResponse(status_code=status, content=body)


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
        return _err(
            404,
            "profiles_dir_missing",
            f"The Hermes profiles directory does not exist: {profiles_dir}",
            profiles_dir=str(profiles_dir),
        )

    if not profiles_dir.is_dir():
        return _err(
            400,
            "profiles_dir_not_a_directory",
            "The Hermes profiles path exists but is not a directory: "
            f"{profiles_dir}",
            profiles_dir=str(profiles_dir),
        )

    try:
        entries = sorted(
            entry for entry in profiles_dir.iterdir() if entry.is_dir()
        )
    except (PermissionError, OSError) as exc:
        return _err(
            403,
            "profiles_dir_unreadable",
            f"The Hermes profiles directory could not be read: {exc}",
            profiles_dir=str(profiles_dir),
        )

    profiles = [{"name": entry.name, "path": str(entry)} for entry in entries]
    return {"profiles_dir": str(profiles_dir), "profiles": profiles}


# ---------------------------------------------------------------------------
# FR-6: addon enable/disable engine
# ---------------------------------------------------------------------------


def _addon_summary(addon, active_ids: set[str]) -> dict:
    """Serialize an addon for the compatibility listing."""
    return {
        "id": addon.id,
        "name": addon.name,
        "description": addon.description,
        "version": addon.version,
        "contributes": {"soul_block": addon.soul_block, "skills": addon.skills},
        "modes": [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "contributes": {"soul_block": m.soul_block, "skills": m.skills},
                "default": m.default,
            }
            for m in addon.modes
        ],
        "compatible_profiles_or_presets": addon.compatible,
        "enabled": addon.id in active_ids,
    }


@router.get("/addons")
def list_addons(target: str = "", profile: str = ""):
    """List addons compatible with a given profile/preset target (FR-6a).

    Reachable at ``GET /api/plugins/hapm/addons?target=<name>``. Reads each
    addon manifest's ``compatible_profiles_or_presets`` whitelist (FR-5) and
    returns only the addons that admit ``target`` (``"*"`` matches any).

    Query params:
      - ``target`` (required): the profile name or preset slug to match the
        whitelist against.
      - ``profile`` (optional): a profile name whose ``hapm.lock`` is read so
        each returned addon carries an ``enabled`` flag. Defaults to ``target``
        when it names an existing profile.

    Errors:
      - ``target_required`` (400) when ``target`` is missing/empty.
      - ``registry_error`` (400) when the addon registry cannot be read.
    """
    if not target:
        return _err(
            400,
            "target_required",
            "Query parameter 'target' (profile name or preset slug) is required.",
        )

    addons_root = _addons_root()
    if not addons_root.is_dir():
        return _err(
            404,
            "addons_registry_missing",
            f"The addon registry directory does not exist: {addons_root}",
            addons_root=str(addons_root),
        )

    # Resolve which profile's lock to read for the enabled flag.
    lock_profile = profile or target
    active_ids: set[str] = set()
    pdir = _profile_dir(lock_profile)
    if pdir.is_dir():
        try:
            active_ids = {a.addon_id for a in list_active_addons(pdir)}
        except Exception:  # noqa: BLE001 - a corrupt lock must not 500 the list
            active_ids = set()

    try:
        addons = compatible_addons(addons_root, target)
    except RegistryError as exc:
        return _err(400, "registry_error", str(exc), addons_root=str(addons_root))

    return {
        "target": target,
        "addons_root": str(addons_root),
        "addons": [_addon_summary(a, active_ids) for a in addons],
    }


def _resolve_enable_inputs(payload: dict):
    """Validate a shared enable/disable payload; return (profile, addon_id)."""
    profile = str(payload.get("profile", "")).strip()
    addon_id = str(payload.get("addon", payload.get("addon_id", ""))).strip()
    return profile, addon_id


@router.post("/addons/enable")
def enable_addon_route(payload: dict = Body(...)):
    """Enable an addon on a profile (FR-6b).

    Body (JSON):
      - ``profile`` (required): target profile name under ``$HERMES_HOME``.
      - ``addon`` (required): addon id (folder slug under ``addons/``).
      - ``target`` (optional): whitelist match target; defaults to ``profile``.
      - ``mode`` (optional): selected mode id for a modal addon.

    Returns the resulting activation state, or a structured error:
      - ``bad_request`` (400) for missing fields.
      - ``profile_not_found`` (404) when the profile dir is missing.
      - ``addon_not_found`` (404) when the addon slug is unknown.
      - ``not_compatible`` (409) when the target is not in the whitelist.
      - ``conflict`` (409) when the SOUL block collides (FR-6 conflict rule).
      - ``already_enabled`` (409) when the addon is already active.
    """
    profile, addon_id = _resolve_enable_inputs(payload)
    if not profile or not addon_id:
        return _err(
            400,
            "bad_request",
            "Both 'profile' and 'addon' are required in the request body.",
        )
    target = str(payload.get("target", "")).strip() or profile
    mode = payload.get("mode")
    mode_id = str(mode).strip() if mode not in (None, "") else None

    pdir = _profile_dir(profile)
    if not pdir.is_dir():
        return _err(
            404,
            "profile_not_found",
            f"Profile directory not found: {pdir}",
            profile=profile,
        )

    addon_dir = _addons_root() / addon_id
    if not (addon_dir / "manifest.json").is_file():
        return _err(
            404,
            "addon_not_found",
            f"No addon with id {addon_id!r} in the registry.",
            addon=addon_id,
        )

    try:
        addon = load_addon(addon_dir)
        result = enable_addon(pdir, addon, target=target, mode_id=mode_id)
    except AddonNotCompatibleError as exc:
        return _err(409, "not_compatible", str(exc), addon=addon_id, target=target)
    except AddonConflictError as exc:
        return _err(409, "conflict", str(exc), addon=addon_id)
    except AddonAlreadyEnabledError as exc:
        return _err(409, "already_enabled", str(exc), addon=addon_id)
    except (RegistryError, ToggleError) as exc:
        return _err(400, "enable_failed", str(exc), addon=addon_id)

    return {
        "profile": profile,
        "addon": result.addon_id,
        "mode": result.mode,
        "enabled": result.enabled,
        "soul_block": result.soul_block,
        "skill_paths": result.skill_paths,
        "lock_path": result.lock_path,
    }


@router.post("/addons/disable")
def disable_addon_route(payload: dict = Body(...)):
    """Disable an addon on a profile (FR-6c).

    Body (JSON):
      - ``profile`` (required): target profile name under ``$HERMES_HOME``.
      - ``addon`` (required): addon id to disable.

    Removes exactly this addon's marked SOUL block and the skills it added
    (restoring any shadowed pre-existing skill), leaving everything else
    untouched. Errors:
      - ``bad_request`` (400) for missing fields.
      - ``profile_not_found`` (404) when the profile dir is missing.
      - ``not_enabled`` (409) when the addon is not currently active.
    """
    profile, addon_id = _resolve_enable_inputs(payload)
    if not profile or not addon_id:
        return _err(
            400,
            "bad_request",
            "Both 'profile' and 'addon' are required in the request body.",
        )

    pdir = _profile_dir(profile)
    if not pdir.is_dir():
        return _err(
            404,
            "profile_not_found",
            f"Profile directory not found: {pdir}",
            profile=profile,
        )

    try:
        result = disable_addon(pdir, addon_id)
    except AddonNotEnabledError as exc:
        return _err(409, "not_enabled", str(exc), addon=addon_id)
    except (RegistryError, ToggleError) as exc:
        return _err(400, "disable_failed", str(exc), addon=addon_id)

    return {
        "profile": profile,
        "addon": result.addon_id,
        "enabled": result.enabled,
        "lock_path": result.lock_path,
    }
