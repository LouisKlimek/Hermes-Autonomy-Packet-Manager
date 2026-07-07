"""Addon registry reader (FR-6, on top of the FR-5 registry format).

The addon registry lives under ``addons/<addon-slug>/`` in this repo (see
``addons/SCHEMA.md``). This module loads an addon's ``manifest.json``, exposes
its compatibility whitelist, and resolves the *effective contribution* of a
given mode (which SOUL.md block file to insert, which skill dirs to copy).

It is a thin, read-only view over the registry so the FR-6 toggle engine
(:mod:`hapm.toggle`) and the API layer can answer:

* "is this addon compatible with profile/preset X?" (FR-5 whitelist), and
* "for the selected mode, what SOUL block content and which skills does it
  contribute?"

Nothing here mutates a profile; :mod:`hapm.toggle` does that using the FR-7
primitives.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "manifest.json"
SKILLS_DIRNAME = "skills"
# Wildcard token in ``compatible_profiles_or_presets`` meaning "any target".
COMPAT_ANY = "*"

_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+")


class RegistryError(Exception):
    """Raised when an addon manifest is missing, malformed, or inconsistent."""


@dataclass
class AddonMode:
    """One mutually-exclusive addon mode (FR-5 ``modes[]``)."""

    id: str
    name: str
    description: str
    soul_block: bool
    skills: bool
    default: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AddonMode":
        contributes = data.get("contributes") or {}
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", data["id"])),
            description=str(data.get("description", "")),
            soul_block=bool(contributes.get("soul_block", False)),
            skills=bool(contributes.get("skills", False)),
            default=bool(data.get("default", False)),
        )


@dataclass
class Addon:
    """A loaded addon manifest plus its on-disk directory.

    Attributes:
        id: Stable addon slug (used in ``HAPM:addon:<id>`` markers).
        name / description / version: manifest metadata.
        soul_block / skills: addon-level maximum contribution surface.
        compatible: whitelist of profile names / preset slugs (or ``["*"]``).
        modes: mutually-exclusive modes, or empty for single-behaviour addons.
        directory: absolute path to ``addons/<id>/``.
    """

    id: str
    name: str
    description: str
    version: str
    soul_block: bool
    skills: bool
    compatible: list[str]
    modes: list[AddonMode]
    directory: Path

    # -- compatibility (FR-5 whitelist) --------------------------------

    def is_compatible_with(self, target: str) -> bool:
        """True if this addon may activate on ``target`` (profile or preset).

        ``target`` is matched against ``compatible_profiles_or_presets``; the
        ``"*"`` wildcard matches anything.
        """
        if COMPAT_ANY in self.compatible:
            return True
        return target in self.compatible

    # -- mode resolution -----------------------------------------------

    def default_mode(self) -> AddonMode | None:
        """Return the mode flagged ``default``, or the first mode, or ``None``."""
        if not self.modes:
            return None
        for m in self.modes:
            if m.default:
                return m
        return self.modes[0]

    def get_mode(self, mode_id: str | None) -> AddonMode | None:
        """Resolve a mode by id.

        For a modal addon, ``None`` resolves to the default mode. For a
        non-modal addon there are no modes and this returns ``None`` (the
        caller uses the addon-level contribution).
        """
        if not self.modes:
            return None
        if mode_id is None:
            return self.default_mode()
        for m in self.modes:
            if m.id == mode_id:
                return m
        raise RegistryError(
            f"addon {self.id!r} has no mode {mode_id!r} "
            f"(available: {[m.id for m in self.modes]})"
        )

    def effective_contribution(self, mode_id: str | None) -> tuple[bool, bool]:
        """Return ``(soul_block, skills)`` effective for the selected mode.

        For a modal addon this is the active mode's ``contributes``; for a
        non-modal addon it is the addon-level ``contributes``.
        """
        mode = self.get_mode(mode_id)
        if mode is None:
            return self.soul_block, self.skills
        return mode.soul_block, mode.skills

    # -- contribution source resolution --------------------------------

    def soul_block_path(self, mode_id: str | None) -> Path:
        """Path to the SOUL block content file for the selected mode.

        Prefers a per-mode file ``soul_block.<mode>.md``; falls back to the
        single-mode ``soul_block.md``. Raises if neither exists.
        """
        mode = self.get_mode(mode_id)
        candidates: list[Path] = []
        if mode is not None:
            candidates.append(self.directory / f"soul_block.{mode.id}.md")
        candidates.append(self.directory / "soul_block.md")
        for c in candidates:
            if c.is_file():
                return c
        raise RegistryError(
            f"addon {self.id!r} declares a SOUL block for mode "
            f"{(mode.id if mode else None)!r} but no content file was found "
            f"(looked for: {[c.name for c in candidates]})"
        )

    def skill_contributions(self) -> dict[str, Path]:
        """Map skill destination name -> source dir under ``skills/``.

        Each immediate sub-entry of the addon's ``skills/`` directory is one
        skill (dir or file) to copy into the profile's ``skills/`` tree under
        the same name.
        """
        skills_root = self.directory / SKILLS_DIRNAME
        if not skills_root.is_dir():
            raise RegistryError(
                f"addon {self.id!r} declares skills but has no "
                f"{SKILLS_DIRNAME}/ directory"
            )
        out: dict[str, Path] = {}
        for entry in sorted(skills_root.iterdir()):
            # Skip registry bookkeeping files that are not skills.
            if entry.name in {"README.md", ".gitkeep"}:
                continue
            out[entry.name] = entry
        if not out:
            raise RegistryError(
                f"addon {self.id!r} declares skills but {SKILLS_DIRNAME}/ is "
                f"empty"
            )
        return out


def _validate_slug(slug: str) -> None:
    if not slug or not _SLUG_RE.fullmatch(slug):
        raise RegistryError(f"invalid addon id {slug!r}: use [A-Za-z0-9._-]+")


def load_addon(addon_dir: str | Path) -> Addon:
    """Load a single addon from its directory.

    Raises :class:`RegistryError` if the manifest is missing or malformed.
    """
    directory = Path(addon_dir)
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise RegistryError(f"addon manifest not found: {manifest_path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(
            f"corrupt addon manifest at {manifest_path}: {exc}"
        ) from exc

    try:
        addon_id = str(data["id"])
        name = str(data["name"])
        description = str(data["description"])
        version = str(data["version"])
        contributes = data["contributes"]
        compatible = list(data["compatible_profiles_or_presets"])
    except KeyError as exc:
        raise RegistryError(
            f"addon manifest {manifest_path} missing required field: {exc}"
        ) from exc

    _validate_slug(addon_id)
    soul_block = bool(contributes.get("soul_block", False))
    skills = bool(contributes.get("skills", False))
    if not (soul_block or skills):
        raise RegistryError(
            f"addon {addon_id!r} contributes neither soul_block nor skills"
        )

    modes = [AddonMode.from_dict(m) for m in (data.get("modes") or [])]
    defaults = [m for m in modes if m.default]
    if len(defaults) > 1:
        raise RegistryError(
            f"addon {addon_id!r} has more than one default mode"
        )

    return Addon(
        id=addon_id,
        name=name,
        description=description,
        version=version,
        soul_block=soul_block,
        skills=skills,
        compatible=[str(c) for c in compatible],
        modes=modes,
        directory=directory,
    )


def load_registry(addons_root: str | Path) -> list[Addon]:
    """Load every addon under ``addons_root``.

    An addon directory is any immediate sub-dir containing a ``manifest.json``.
    Sub-dirs without a manifest are ignored so registry-level files
    (``SCHEMA.md``, ``validate.py``) never break the scan.
    """
    root = Path(addons_root)
    if not root.is_dir():
        raise RegistryError(f"addons registry directory not found: {root}")
    addons: list[Addon] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / MANIFEST_FILENAME).is_file():
            continue
        addons.append(load_addon(entry))
    return addons


def compatible_addons(addons_root: str | Path, target: str) -> list[Addon]:
    """Return addons whose whitelist admits ``target`` (a profile or preset)."""
    return [a for a in load_registry(addons_root) if a.is_compatible_with(target)]
