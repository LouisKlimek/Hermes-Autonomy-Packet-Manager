"""Apply a preset to a profile with a whitelisted config merge (HAPM FR-4).

This module is a pure filesystem library (no network, no FastAPI) so it can be
unit-tested in isolation and reused by the dashboard route handlers in
``plugin_api.py``. It builds on the FR-7 reversibility engine
(:mod:`dashboard.hapm.backup` / :mod:`dashboard.hapm.state`) so every apply is
backed up first and can be reverted byte-exactly.

A **preset** (see ``presets/SCHEMA.md``) contributes three things to a target
profile:

* ``SOUL.md``  — overwritten from the preset's ``SOUL.md``.
* ``skills/``  — overwritten from the preset's ``skills/`` tree.
* a whitelisted merge fragment (``config.fragment.yaml``) that is *merged*
  (never full-overwritten) into ``profiles/<profile>/config.yaml``.

Config merge is guarded by the OQ-2 whitelist (CEO-confirmed 2026-07-07): only a
narrow, role-defining set of keys may be touched. If a preset fragment contains
*any* non-whitelisted key the whole apply is rejected before anything is
written, so a malformed/hostile preset can never leak secrets, swap the model,
or clobber platform tokens.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backup import BackupStore
from .state import LockState, read_lock, write_lock

# Artifacts a preset apply touches inside a profile. These are exactly the
# targets snapshotted before an apply so a revert is a true rollback.
PROFILE_SOUL = "SOUL.md"
PROFILE_SKILLS = "skills"
PROFILE_CONFIG = "config.yaml"

PRESET_MANIFEST = "manifest.json"
PRESET_SOUL = "SOUL.md"
PRESET_SKILLS = "skills"
PRESET_FRAGMENT = "config.fragment.yaml"


# --- OQ-2 config whitelist ---------------------------------------------------
# Kept in lockstep with presets/SCHEMA.md and scripts/validate_presets.py.

# Top-level mapping keys a fragment may contain at all.
ALLOWED_TOP_LEVEL = {"toolsets", "delegation", "kanban", "approvals", "agent"}
# Parents under which ANY sub-key is allowed (dotted wildcard).
ALLOWED_WILDCARD_PARENTS = {"delegation"}
# Fully-qualified dotted keys allowed under scoped parents.
ALLOWED_DOTTED = {
    "agent.max_turns",
    "agent.reasoning_effort",
    "agent.disabled_toolsets",
    "kanban.default_assignee",
    "approvals.mode",
    "toolsets",
}
# Explicitly forbidden top-level parents (redundant with the allowlist but
# spelled out so error messages are unambiguous and future keys stay safe).
FORBIDDEN_TOP_LEVEL = {
    "model", "security", "telegram", "discord", "slack", "matrix",
    "mattermost", "whatsapp", "web", "terminal", "dashboard",
}


class ApplyError(Exception):
    """Raised when a preset cannot be applied (bad preset, whitelist, IO)."""


class WhitelistError(ApplyError):
    """Raised when a preset config fragment contains a non-whitelisted key."""


# --- YAML helpers ------------------------------------------------------------

def _require_yaml():
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ApplyError(
            "PyYAML is required to merge a preset config fragment but is not "
            "installed in the dashboard environment."
        ) from exc
    return yaml


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    """Load a YAML file as a mapping. Missing/empty file -> empty mapping."""
    if not path.exists():
        return {}
    yaml = _require_yaml()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ApplyError(f"{path}: expected a YAML mapping at the top level")
    return data


def dump_yaml_mapping(path: Path, data: dict[str, Any]) -> None:
    """Atomically write a mapping back to a YAML file."""
    yaml = _require_yaml()
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --- whitelist validation ----------------------------------------------------

def _flatten_keys(obj: Any, prefix: str = ""):
    """Yield every dotted key path in a nested mapping (dicts only)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}{k}"
            yield path
            if isinstance(v, dict):
                yield from _flatten_keys(v, prefix=f"{path}.")


def validate_fragment_whitelist(fragment: dict[str, Any]) -> None:
    """Reject a config fragment that touches any non-whitelisted key (OQ-2).

    Raises :class:`WhitelistError` listing every violation. This runs *before*
    any file is written so a bad preset makes no partial change.
    """
    if not isinstance(fragment, dict):
        raise WhitelistError("config fragment must be a mapping")

    violations: list[str] = []
    for top in fragment:
        if top in FORBIDDEN_TOP_LEVEL:
            violations.append(f"forbidden top-level key '{top}'")
        elif top not in ALLOWED_TOP_LEVEL:
            violations.append(f"key '{top}' is not in the OQ-2 whitelist")

    for dotted in _flatten_keys(fragment):
        parent = dotted.split(".", 1)[0]
        if parent in ALLOWED_WILDCARD_PARENTS or parent == "toolsets":
            continue
        if parent in ("agent", "kanban", "approvals"):
            if "." in dotted and dotted not in ALLOWED_DOTTED:
                violations.append(
                    f"key '{dotted}' not allowed under '{parent}' (OQ-2 whitelist)"
                )

    if violations:
        raise WhitelistError(
            "preset config fragment violates the OQ-2 whitelist: "
            + "; ".join(violations)
        )


# --- config merge ------------------------------------------------------------

def deep_merge(base: dict[str, Any], fragment: dict[str, Any]) -> dict[str, Any]:
    """Merge ``fragment`` into ``base`` in-place and return ``base``.

    Nested mappings are merged recursively; every other value (scalars, lists)
    is replaced wholesale by the fragment's value. Keys present only in
    ``base`` are preserved untouched — this is a patch/merge, never a
    full-file overwrite, so non-whitelisted config (secrets, model, tokens)
    stays exactly as it was.
    """
    for key, val in fragment.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(val, dict)
        ):
            deep_merge(base[key], val)
        else:
            base[key] = val
    return base


# --- preset registry ---------------------------------------------------------

def default_presets_root() -> Path:
    """Locate the bundled ``presets/`` registry.

    The registry ships at the repo root (``presets/``), two levels up from this
    file (``dashboard/hapm/apply.py``). ``HAPM_PRESETS_DIR`` overrides it (used
    by tests and alternate deployments).
    """
    override = os.environ.get("HAPM_PRESETS_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "presets"


@dataclass
class PresetInfo:
    """Registry metadata for one preset (from its ``manifest.json``)."""

    slug: str
    name: str
    description: str
    version: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "path": self.path,
        }


def list_presets(presets_root: str | os.PathLike[str] | None = None) -> list[PresetInfo]:
    """List available presets from the registry, sorted by slug.

    A directory is treated as a preset only when it has a readable
    ``manifest.json``. Malformed manifests are skipped rather than aborting the
    whole listing.
    """
    root = Path(presets_root) if presets_root is not None else default_presets_root()
    if not root.is_dir():
        return []
    out: list[PresetInfo] = []
    for entry in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = entry / PRESET_MANIFEST
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(
            PresetInfo(
                slug=str(data.get("slug", entry.name)),
                name=str(data.get("name", entry.name)),
                description=str(data.get("description", "")),
                version=str(data.get("version", "")),
                path=str(entry),
            )
        )
    return out


def resolve_preset(slug: str, presets_root: str | os.PathLike[str] | None = None) -> Path:
    """Return the directory for a preset slug, validating its layout.

    Raises :class:`ApplyError` if the preset is unknown or missing a required
    file (``manifest.json``, ``SOUL.md``, ``config.fragment.yaml``, ``skills/``).
    """
    if not slug or "/" in slug or "\\" in slug or slug in (".", ".."):
        raise ApplyError(f"invalid preset slug: {slug!r}")
    root = Path(presets_root) if presets_root is not None else default_presets_root()
    preset_dir = root / slug
    if not preset_dir.is_dir():
        raise ApplyError(f"unknown preset: {slug!r}")
    for required in (PRESET_MANIFEST, PRESET_SOUL, PRESET_FRAGMENT):
        if not (preset_dir / required).is_file():
            raise ApplyError(f"preset {slug!r} is missing required file {required!r}")
    if not (preset_dir / PRESET_SKILLS).is_dir():
        raise ApplyError(f"preset {slug!r} is missing required 'skills/' directory")
    return preset_dir


# --- apply / revert ----------------------------------------------------------

@dataclass
class ApplyResult:
    """Outcome of applying a preset to a profile."""

    profile: str
    preset: str
    backup_id: str
    config_keys_merged: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "preset": self.preset,
            "backup_id": self.backup_id,
            "config_keys_merged": self.config_keys_merged,
        }


def _copy_tree_over(src: Path, dst: Path) -> None:
    """Replace ``dst`` directory contents with ``src`` (dst removed first)."""
    if dst.is_dir() and not dst.is_symlink():
        shutil.rmtree(dst)
    elif dst.exists() or dst.is_symlink():
        dst.unlink()
    shutil.copytree(src, dst, symlinks=True)


def apply_preset(
    profile_dir: str | os.PathLike[str],
    slug: str,
    presets_root: str | os.PathLike[str] | None = None,
) -> ApplyResult:
    """Apply preset ``slug`` to the profile at ``profile_dir`` (FR-4).

    Steps, in order:

    1. Resolve and validate the preset layout.
    2. Load the config fragment and validate it against the OQ-2 whitelist —
       aborting *before* any write if it touches a forbidden key.
    3. Back up the profile's ``SOUL.md``, ``skills/`` and ``config.yaml`` via
       the FR-7 :class:`BackupStore` so the apply is reversible.
    4. Overwrite ``SOUL.md`` and ``skills/`` from the preset.
    5. Merge only the whitelisted fragment keys into ``config.yaml``.
    6. Record the active preset and the pre-apply backup id in ``hapm.lock``.

    Returns an :class:`ApplyResult`. Raises :class:`WhitelistError` /
    :class:`ApplyError` on any problem (nothing is written on whitelist
    failure).
    """
    profile_dir = Path(profile_dir)
    if not profile_dir.is_dir():
        raise ApplyError(f"profile directory does not exist: {profile_dir}")

    preset_dir = resolve_preset(slug, presets_root)

    # Validate the fragment BEFORE touching anything on disk.
    fragment = load_yaml_mapping(preset_dir / PRESET_FRAGMENT)
    validate_fragment_whitelist(fragment)

    # 1) Backup the pre-apply state (byte-exact rollback point).
    store = BackupStore(profile_dir)
    backup_id = store.create([PROFILE_SOUL, PROFILE_SKILLS, PROFILE_CONFIG])

    # 2) Overwrite SOUL.md.
    shutil.copy2(preset_dir / PRESET_SOUL, profile_dir / PROFILE_SOUL)

    # 3) Overwrite skills/ from the preset.
    _copy_tree_over(preset_dir / PRESET_SKILLS, profile_dir / PROFILE_SKILLS)

    # 4) Merge whitelisted config keys (preserving everything else).
    config_path = profile_dir / PROFILE_CONFIG
    config = load_yaml_mapping(config_path)
    deep_merge(config, fragment)
    dump_yaml_mapping(config_path, config)

    # 5) Record active preset + backup id in the lock (FR-7 state record).
    lock = read_lock(profile_dir) or LockState(profile=profile_dir.name)
    lock.profile = profile_dir.name
    lock.active_preset = slug
    lock.preset_backup_id = backup_id
    write_lock(profile_dir, lock)

    return ApplyResult(
        profile=profile_dir.name,
        preset=slug,
        backup_id=backup_id,
        config_keys_merged=sorted(fragment.keys()),
    )


def revert_preset(profile_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Revert the last applied preset, restoring the pre-apply state (FR-4/FR-7).

    Restores ``SOUL.md``, ``skills/`` and ``config.yaml`` byte-exactly from the
    backup captured at apply time, clears the active preset from ``hapm.lock``
    and deletes the consumed backup. Raises :class:`ApplyError` if there is no
    active preset to revert.
    """
    profile_dir = Path(profile_dir)
    lock = read_lock(profile_dir)
    if lock is None or lock.active_preset is None:
        raise ApplyError("no active preset to revert on this profile")
    if not lock.preset_backup_id:
        raise ApplyError("active preset has no backup id recorded; cannot revert")

    store = BackupStore(profile_dir)
    backup_id = lock.preset_backup_id
    if not store.exists(backup_id):
        raise ApplyError(f"backup {backup_id!r} for the active preset is missing")

    restored = store.restore(backup_id)

    reverted_preset = lock.active_preset
    lock.active_preset = None
    lock.preset_backup_id = None
    write_lock(profile_dir, lock)
    store.delete(backup_id)

    return {
        "profile": profile_dir.name,
        "reverted_preset": reverted_preset,
        "restored": restored,
    }
