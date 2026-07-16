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
  - GET  /api/plugins/hapm/profiles/{profile}/status
                                    per-profile active preset + active addons
                                    (with mode), read live from that profile's
                                    ``hapm.lock`` (FR-9)
  - GET  /api/plugins/hapm/addons   list addons compatible with a given
                                    profile/preset target (FR-6a)
  - POST /api/plugins/hapm/addons/enable   enable an addon on a profile (FR-6b)
  - POST /api/plugins/hapm/addons/disable  disable an addon on a profile (FR-6c)
  - GET  /api/plugins/hapm/presets  list available presets from the registry
                                    (FR-4)
  - POST /api/plugins/hapm/apply    apply a preset to a target profile with a
                                    whitelisted config merge, backing up first
                                    (FR-4)
  - POST /api/plugins/hapm/revert   revert the last preset apply, restoring the
                                    pre-apply state byte-exactly (FR-4/FR-7)

IMPORTANT: plugin API routes are mounted only when the dashboard process
starts. After installing or updating this plugin you must restart
``hermes dashboard`` for these routes to load — a browser refresh or a plugin
rescan alone will NOT mount them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, Response

# The HAPM engine lives in the ``hapm`` package next to this module. Make
# it importable whether the dashboard adds ``dashboard/`` to sys.path or not.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from hapm import (  # noqa: E402
    AddonAlreadyEnabledError,
    AddonConflictError,
    AddonNotCompatibleError,
    AddonNotEnabledError,
    ConflictResult,
    RegistryError,
    ResolutionError,
    ToggleError,
    compatible_addons,
    disable_addon,
    enable_addon,
    list_active_addons,
    load_addon,
    resolve_and_enable_addon,
)
from hapm import apply as hapm_apply  # noqa: E402
from hapm.apply import ApplyError, WhitelistError  # noqa: E402
from hapm.builder_drafts import DraftError, DraftStore, drafts_root  # noqa: E402
from hapm.builder_pr import BuilderPRError, open_addon_pr  # noqa: E402
from hapm.builder_sanitize import (  # noqa: E402
    SanitizeError,
    check_addon,
)

from hapm.custom_addons import (  # noqa: E402
    CustomAddonError,
    CustomAddonStore,
    addon_zip_bytes,
)
from hapm.repository_scope import (  # noqa: E402
    ADDON_ID as REPOSITORY_SCOPE_ADDON_ID,
    RepositoryScopeError,
    load_repositories,
    render_soul_block,
    update_repositories,
)

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


def _profiles_dir() -> Path:
    """Return the explicit Hermes profile collection for this dashboard home.

    Hermes normally uses ``$HERMES_HOME/profiles``. In isolated dashboard mode,
    however, ``HERMES_HOME`` is one profile directory directly beneath the
    collection (for example ``/opt/data/profiles/ceo-orchestrator``). Detect
    only that documented shape; this intentionally does not search the
    filesystem for other possible profile roots.
    """
    hermes_home = _hermes_home()
    return (
        hermes_home.parent
        if hermes_home.parent.name == "profiles"
        else hermes_home / "profiles"
    )


def _addons_root() -> Path:
    """Return the addon registry root (``addons/`` at the repo top level).

    This module lives at ``dashboard/plugin_api.py``, so the registry is one
    directory up. ``$HAPM_ADDONS_ROOT`` overrides for tests/custom installs.
    """
    val = os.environ.get("HAPM_ADDONS_ROOT", "").strip()
    if val:
        return Path(val)
    return _HERE.parent / "addons"


def _custom_store() -> CustomAddonStore:
    """Custom packages live only in the user-owned HAPM boundary."""
    return CustomAddonStore(hermes_home=_hermes_home())


def _addon_directory(addon_id: str) -> Path:
    """Resolve immutable shipped addons before user-owned custom packages."""
    shipped = _addons_root() / addon_id
    if (shipped / "manifest.json").is_file():
        return shipped
    try:
        return _custom_store().load(addon_id).addon.directory
    except CustomAddonError:
        return shipped


def _profile_dir(profile: str) -> Path:
    return _profiles_dir() / profile


def _err(status: int, error: str, message: str, **extra) -> JSONResponse:
    body = {"error": error, "message": message}
    body.update(extra)
    return JSONResponse(status_code=status, content=body)


def _preset_application_contents(preset_path: str) -> dict:
    """Return the exact registry files a preset would copy into a profile.

    This reads the immutable preset package, never the selected profile, so the
    detail UI can preview the pending SOUL, skills list, and config fragment
    without exposing profile-specific content.
    """
    preset_dir = Path(preset_path)
    skills_dir = preset_dir / "skills"
    skills = []
    if skills_dir.is_dir():
        skills = [
            path.relative_to(skills_dir).as_posix()
            for path in sorted(skills_dir.rglob("*"))
            if path.is_file() and path.name != ".gitkeep"
        ]
    return {
        "soul_markdown": (preset_dir / "SOUL.md").read_text(encoding="utf-8"),
        "skills": skills,
        "config_fragment": (preset_dir / "config.fragment.yaml").read_text(
            encoding="utf-8"
        ),
    }


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

    Reachable at ``GET /api/plugins/hapm/profiles``. Scans the resolved Hermes
    profile collection and returns each immediate sub-directory as a profile,
    so the UI can present a profile picker. The collection is
    ``$HERMES_HOME/profiles`` normally, or the parent ``profiles`` directory
    when HERMES_HOME is an isolated profile home.

    Each entry contains only the profile ``name`` and absolute ``path`` — no
    file contents (SOUL.md / config.yaml) are read or returned by this
    listing endpoint.

    Errors are returned as a structured JSON body (never a 500 stack trace):
      - ``profiles_dir_missing`` (404) when the resolved collection does not
        exist.
      - ``profiles_dir_not_a_directory`` (400) when the path exists but is not
        a directory.
      - ``profiles_dir_unreadable`` (403) when the directory cannot be read
        (e.g. permission denied).
    """
    profiles_dir = _profiles_dir()

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
        "custom": addon.directory.parent == _custom_store().root,
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
        addons.extend(
            custom.addon
            for custom in _custom_store().list()
            if custom.addon.is_compatible_with(target)
        )
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
      - ``addon_conflict`` (409) when enabling would collide with an active
        addon declared in ``conflicts_with`` (FR-7 v1.1). The body carries the
        structured conflict object (list of colliding active addons + reason)
        the frontend popup consumes; nothing is mutated. Guided resolution is
        opt-in via ``POST /addons/resolve``.
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

    addon_dir = _addon_directory(addon_id)
    if not (addon_dir / "manifest.json").is_file():
        return _err(
            404,
            "addon_not_found",
            f"No addon with id {addon_id!r} in the registry.",
            addon=addon_id,
        )

    try:
        addon = load_addon(addon_dir)
        scope_content = (
            render_soul_block(load_repositories(_hermes_home()))
            if addon_id == REPOSITORY_SCOPE_ADDON_ID
            else None
        )
        result = enable_addon(
            pdir, addon, target=target, mode_id=mode_id, soul_block_content=scope_content
        )
    except RepositoryScopeError as exc:
        return _err(400, "repository_scope_invalid", str(exc), addon=addon_id)
    except AddonNotCompatibleError as exc:
        return _err(409, "not_compatible", str(exc), addon=addon_id, target=target)
    except AddonConflictError as exc:
        return _err(409, "conflict", str(exc), addon=addon_id)
    except AddonAlreadyEnabledError as exc:
        return _err(409, "already_enabled", str(exc), addon=addon_id)
    except (RegistryError, ToggleError) as exc:
        return _err(400, "enable_failed", str(exc), addon=addon_id)

    # Report-only default (FR-7 v1.1): a conflict returns a structured object
    # and mutates nothing. Surface it as a 409 the frontend popup consumes.
    if isinstance(result, ConflictResult):
        return _err(
            409,
            "addon_conflict",
            f"Enabling {addon_id!r} conflicts with active addon(s): "
            f"{result.conflicting_ids}. Confirm guided resolution via "
            f"POST /addons/resolve to disable them (reversibly) and enable "
            f"{addon_id!r}.",
            addon=addon_id,
            target=target,
            conflict=result.to_dict(),
        )

    return {
        "profile": profile,
        "addon": result.addon_id,
        "mode": result.mode,
        "enabled": result.enabled,
        "soul_block": result.soul_block,
        "skill_paths": result.skill_paths,
        "lock_path": result.lock_path,
    }


@router.post("/addons/resolve")
def resolve_addon_route(payload: dict = Body(...)):
    """Confirmed guided conflict resolution (FR-7 v1.1).

    This is the **opt-in** counterpart to ``/addons/enable``: it is called only
    after the user has confirmed (in the frontend popup) that the colliding
    addons may be disabled. It disables each colliding active addon **via the
    FR-7 reversible mechanics** and then enables the target addon — atomically,
    with rollback on partial failure.

    Body (JSON):
      - ``profile`` (required): target profile name under ``$HERMES_HOME``.
      - ``addon`` (required): addon id to enable.
      - ``target`` (optional): whitelist match target; defaults to ``profile``.
      - ``mode`` (optional): selected mode id for a modal addon.

    Returns the final activation state plus the list of addons that were
    disabled to clear the conflict, or a structured error:
      - ``bad_request`` (400) for missing fields.
      - ``profile_not_found`` (404) when the profile dir is missing.
      - ``addon_not_found`` (404) when the addon slug is unknown.
      - ``not_compatible`` (409) when the target is not in the whitelist.
      - ``conflict`` (409) when the SOUL block collides (FR-6 conflict rule).
      - ``already_enabled`` (409) when the addon is already active.
      - ``resolution_failed`` (500) when the target enable failed and rollback
        could not fully restore a previously-disabled addon (recover from the
        FR-7 backups; the message names what could not be restored).
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

    addons_root = _addons_root()
    addon_dir = _addon_directory(addon_id)
    if not (addon_dir / "manifest.json").is_file():
        return _err(
            404,
            "addon_not_found",
            f"No addon with id {addon_id!r} in the registry.",
            addon=addon_id,
        )

    try:
        addon = load_addon(addon_dir)
        resolution = resolve_and_enable_addon(
            pdir, addon, target=target, addons_root=addons_root, mode_id=mode_id
        )
    except AddonNotCompatibleError as exc:
        return _err(409, "not_compatible", str(exc), addon=addon_id, target=target)
    except AddonConflictError as exc:
        return _err(409, "conflict", str(exc), addon=addon_id)
    except AddonAlreadyEnabledError as exc:
        return _err(409, "already_enabled", str(exc), addon=addon_id)
    except ResolutionError as exc:
        return _err(500, "resolution_failed", str(exc), addon=addon_id)
    except (RegistryError, ToggleError) as exc:
        return _err(400, "resolve_failed", str(exc), addon=addon_id)

    result = resolution.result
    return {
        "profile": profile,
        "addon": result.addon_id,
        "mode": result.mode,
        "enabled": result.enabled,
        "soul_block": result.soul_block,
        "skill_paths": result.skill_paths,
        "lock_path": result.lock_path,
        "disabled": resolution.disabled,
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


@router.get("/repository-scope")
def get_repository_scope():
    """Return the single shared allowlist used by Repository Scope."""
    try:
        return {"repositories": load_repositories(_hermes_home())}
    except RepositoryScopeError as exc:
        return _err(500, "repository_scope_unavailable", str(exc))


@router.put("/repository-scope")
def update_repository_scope(payload: dict = Body(...)):
    """Update the shared allowlist and refresh every active scope addon."""
    try:
        return update_repositories(
            _hermes_home(), _profiles_dir(), payload.get("repositories")
        )
    except RepositoryScopeError as exc:
        return _err(400, "repository_scope_invalid", str(exc))


# ---------------------------------------------------------------------------
# FR-9: per-profile status endpoint
# ---------------------------------------------------------------------------


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

    profile_dir = _profile_dir(profile)
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


# ---------------------------------------------------------------------------
# FR-4: preset apply/revert endpoints
# ---------------------------------------------------------------------------


def _resolve_profile_dir(profile: str) -> Path | None:
    """Resolve a profile name within the explicit profile collection.

    Returns the directory ``Path`` when it exists, or ``None`` when the profile
    name is invalid or the directory is absent. The name is validated to be a
    single path segment so a request can never escape the profiles root.
    """
    if not profile or "/" in profile or "\\" in profile or profile in (".", ".."):
        return None
    candidate = _profile_dir(profile)
    return candidate if candidate.is_dir() else None


@router.get("/presets")
def list_presets():
    """List available presets from the registry (FR-4).

    Reachable at ``GET /api/plugins/hapm/presets``. Returns each preset's
    registry metadata (slug, name, description, version, path) so the UI can
    present a preset picker. No profile is touched by this listing.
    """
    presets = []
    for preset in hapm_apply.list_presets():
        summary = preset.to_dict()
        try:
            summary["application"] = _preset_application_contents(preset.path)
        except OSError:
            # Preserve the existing best-effort registry listing when a package
            # becomes unreadable between manifest discovery and serialization.
            summary["application"] = None
        presets.append(summary)
    return {
        "presets_dir": str(hapm_apply.default_presets_root()),
        "presets": presets,
    }


@router.post("/apply")
def apply_preset(payload: dict = Body(...)):
    """Apply a preset to a target profile (FR-4).

    Reachable at ``POST /api/plugins/hapm/apply`` with a JSON body::

        {"profile": "<profile-name>", "preset": "<preset-slug>"}

    Overwrites the profile's ``SOUL.md`` and ``skills/`` from the preset and
    merges only the OQ-2-whitelisted keys into its ``config.yaml`` — after
    backing up the prior state via the FR-7 engine so the change is fully
    reversible. The pre-apply backup id and active preset are recorded in the
    profile's ``hapm.lock``.

    Errors are structured JSON (never a 500 stack trace):
      - ``missing_field`` (400) when profile/preset is absent.
      - ``unknown_profile`` (404) when the profile directory does not exist.
      - ``whitelist_violation`` (422) when the preset fragment touches a
        non-whitelisted config key (nothing is written).
      - ``apply_failed`` (400) for any other apply error (unknown preset, bad
        layout, IO).
    """
    profile = str(payload.get("profile", "")).strip()
    preset = str(payload.get("preset", "")).strip()
    if not profile or not preset:
        return JSONResponse(
            status_code=400,
            content={
                "error": "missing_field",
                "message": "both 'profile' and 'preset' are required.",
            },
        )

    profile_dir = _resolve_profile_dir(profile)
    if profile_dir is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "unknown_profile",
                "message": f"profile not found: {profile!r}",
                "profile": profile,
            },
        )

    try:
        result = hapm_apply.apply_preset(profile_dir, preset)
    except WhitelistError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "error": "whitelist_violation",
                "message": str(exc),
                "profile": profile,
                "preset": preset,
            },
        )
    except ApplyError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": "apply_failed",
                "message": str(exc),
                "profile": profile,
                "preset": preset,
            },
        )

    return {"status": "applied", **result.to_dict()}


@router.post("/revert")
def revert_preset(payload: dict = Body(...)):
    """Revert the last preset apply on a profile (FR-4/FR-7).

    Reachable at ``POST /api/plugins/hapm/revert`` with a JSON body::

        {"profile": "<profile-name>"}

    Restores ``SOUL.md``, ``skills/`` and ``config.yaml`` byte-exactly from the
    backup captured at apply time, clears the active preset from ``hapm.lock``
    and deletes the consumed backup.

    Errors are structured JSON:
      - ``missing_field`` (400) when profile is absent.
      - ``unknown_profile`` (404) when the profile directory does not exist.
      - ``revert_failed`` (400) when there is no active preset / no backup.
    """
    profile = str(payload.get("profile", "")).strip()
    if not profile:
        return JSONResponse(
            status_code=400,
            content={
                "error": "missing_field",
                "message": "'profile' is required.",
            },
        )

    profile_dir = _resolve_profile_dir(profile)
    if profile_dir is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "unknown_profile",
                "message": f"profile not found: {profile!r}",
                "profile": profile,
            },
        )

    try:
        result = hapm_apply.revert_preset(profile_dir)
    except ApplyError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": "revert_failed",
                "message": str(exc),
                "profile": profile,
            },
        )

    return {"status": "reverted", **result}


# ---------------------------------------------------------------------------
# Custom addons: private storage, safe edit, and package-only export.
# ---------------------------------------------------------------------------


@router.post("/custom-addons")
def create_custom_addon(payload: dict = Body(...)):
    try:
        addon = _custom_store().create(payload)
    except CustomAddonError as exc:
        return _err(422, "custom_addon_invalid", str(exc))
    return {"status": "created", "addon": _addon_summary(addon.addon, set())}


@router.get("/custom-addons/{addon_id}")
def get_custom_addon(addon_id: str):
    try:
        addon = _custom_store().load(addon_id)
    except CustomAddonError as exc:
        return _err(404, "custom_addon_not_found", str(exc), addon=addon_id)
    return {
        "addon": _addon_summary(addon.addon, set()),
        "soul_block": (addon.addon.directory / "soul_block.md").read_text(encoding="utf-8"),
    }


@router.put("/custom-addons/{addon_id}")
def update_custom_addon(addon_id: str, payload: dict = Body(...)):
    try:
        addon = _custom_store().update(addon_id, payload)
    except CustomAddonError as exc:
        return _err(422, "custom_addon_invalid", str(exc), addon=addon_id)
    return {"status": "updated", "addon": _addon_summary(addon.addon, set())}


@router.get("/custom-addons/{addon_id}/download")
def download_custom_addon(addon_id: str):
    try:
        addon = _custom_store().load(addon_id)
        content = addon_zip_bytes(addon)
    except CustomAddonError as exc:
        return _err(404, "custom_addon_download_failed", str(exc), addon=addon_id)
    filename = f"{addon.id}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# v1.2 In-UI Addon Builder (spec Req 1 §4, Req 2 §5, FR-7 §6)
#
# Two-stage flow, server-side enforced end to end:
#   1. POST /builder/check         live sanitizing (client convenience mirror)
#   2. POST /builder/drafts        save a Local Draft (zero effect on any
#                                  profile until a PR is merged)
#   3. GET  /builder/drafts        list drafts
#   4. GET  /builder/drafts/{id}   fetch one draft
#   5. POST /builder/submit        final non-overridable sanitize + open PR
#                                  (branch + PR only; never push to main,
#                                   never auto-merge)
#
# The sanitizing gate (``check_addon``) is applied on BOTH the draft-save and
# the submit path, so a direct API call that bypasses the client cannot persist
# or submit content that violates §4 (acceptance criteria #1, #2, #3).
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Repo working tree used to materialize + open community-addon PRs.

    ``$HAPM_REPO_ROOT`` overrides; otherwise the repo root is the parent of the
    ``dashboard/`` directory this module lives in.
    """
    val = os.environ.get("HAPM_REPO_ROOT", "").strip()
    if val:
        return Path(val)
    return _HERE.parent


def _draft_store() -> DraftStore:
    return DraftStore(hermes_home=_hermes_home())


def _builder_inputs(payload: dict) -> dict:
    """Normalize a builder payload into the ``check_addon`` draft shape."""
    return {
        "name": str(payload.get("name", "")).strip(),
        "description": str(payload.get("description", "")).strip(),
        "soul": dict(payload.get("soul") or {}),
        "skill": dict(payload.get("skill") or {}),
    }


@router.post("/builder/check")
def builder_check(payload: dict = Body(...)):
    """Server-side sanitizing pass over builder inputs (spec §4.2).

    The client runs a live check for UX, but enforcement is here: this returns
    the exact same :class:`SanitizeResult` the draft-save / submit paths use, so
    the client's check can never diverge from (or override) the server's.
    """
    result = check_addon(_builder_inputs(payload))
    return {"ok": result.ok, "violations": [v.to_dict() for v in result.violations]}


@router.post("/builder/drafts")
def builder_save_draft(payload: dict = Body(...)):
    """Save a Local Draft (spec §5 hybrid — not activatable).

    Runs the non-overridable §4 sanitizing gate first; on any violation nothing
    is written and a 422 lists the blocking reasons. A saved draft lives in the
    HAPM draft store (outside every profile and the repo tree) and has zero
    effect on any real profile until its PR is merged.

    Body: ``{name, description, author, soul:{enabled,body}, skill:{...}}``.
    """
    author = str(payload.get("author", "")).strip()
    inputs = _builder_inputs(payload)
    if not inputs["name"]:
        return _err(400, "bad_request", "'name' is required.")
    if not author:
        return _err(400, "bad_request", "'author' (git username) is required.")

    gate = check_addon(inputs)
    if not gate.ok:
        return _err(
            422,
            "sanitizing_failed",
            "The draft cannot be saved until every flagged item is removed.",
            violations=[v.to_dict() for v in gate.violations],
        )

    try:
        store = _draft_store()
        draft = store.create(
            name=inputs["name"],
            description=inputs["description"],
            author=author,
            soul=inputs["soul"],
            skill=inputs["skill"],
        )
    except SanitizeError as exc:
        return _err(422, "sanitizing_failed", str(exc))
    except DraftError as exc:
        return _err(400, "draft_error", str(exc))

    return {"status": "saved", "draft": draft.to_dict()}


@router.get("/builder/drafts")
def builder_list_drafts():
    """List all saved local drafts (never activatable state)."""
    store = _draft_store()
    return {
        "drafts_root": str(drafts_root(_hermes_home())),
        "drafts": [d.to_dict() for d in store.list()],
    }


@router.get("/builder/drafts/{addon_id}")
def builder_get_draft(addon_id: str):
    """Fetch one saved draft by id."""
    store = _draft_store()
    try:
        draft = store.load(addon_id)
    except DraftError as exc:
        return _err(404, "draft_not_found", str(exc), addon_id=addon_id)
    return {"draft": draft.to_dict()}


@router.post("/builder/submit")
def builder_submit(payload: dict = Body(...)):
    """Open the community-addon PR for a saved draft (spec §5 activation path).

    Loads the draft, runs the final non-overridable §4 sanitizing gate again,
    materializes exactly the enumerated files under ``addons/<id>/`` and opens
    (or updates) the addon's dedicated PR branch. The service account only
    creates a branch + PR — it never pushes to ``main`` and never auto-merges;
    the addon is inert until a human / pr-reviewer merges the PR.

    Body: ``{addon_id: str, base?: str}``.
    """
    addon_id = str(payload.get("addon_id", "")).strip()
    if not addon_id:
        return _err(400, "bad_request", "'addon_id' is required.")
    base = str(payload.get("base", "main")).strip() or "main"

    store = _draft_store()
    try:
        draft = store.load(addon_id)
    except DraftError as exc:
        return _err(404, "draft_not_found", str(exc), addon_id=addon_id)

    # Final, non-overridable sanitize gate before any file is written / pushed.
    gate = check_addon(draft.to_dict())
    if not gate.ok:
        return _err(
            422,
            "sanitizing_failed",
            "The draft cannot be submitted until every flagged item is removed.",
            violations=[v.to_dict() for v in gate.violations],
        )

    try:
        result = open_addon_pr(draft, _repo_root(), base=base)
    except SanitizeError as exc:
        return _err(422, "sanitizing_failed", str(exc), addon_id=addon_id)
    except BuilderPRError as exc:
        return _err(400, "pr_failed", str(exc), addon_id=addon_id)

    return {
        "status": "pr_opened",
        "addon_id": addon_id,
        "branch": result.branch,
        "pr_url": result.pr_url,
        "pr_number": result.pr_number,
        "head_sha": result.head_sha,
        "files": result.files,
    }

