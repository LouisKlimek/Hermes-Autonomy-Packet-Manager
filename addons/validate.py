#!/usr/bin/env python3
"""Structural validator for the HAPM Addon Registry (FR-5).

Validates every addon under addons/<slug>/ against the format defined in
addons/SCHEMA.md. Checks manifest field presence/types, contributes/on-disk
consistency, and mode rules. Exits non-zero on any violation.

Usage:
    python3 addons/validate.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ADDONS_DIR = Path(__file__).resolve().parent

REQUIRED_MANIFEST_FIELDS = {
    "id": str,
    "name": str,
    "description": str,
    "version": str,
    "contributes": dict,
    "compatible_profiles_or_presets": list,
}


def _err(errors: list[str], slug: str, msg: str) -> None:
    errors.append(f"[{slug}] {msg}")


def _validate_contributes(errors, slug, contributes, where):
    for key in ("soul_block", "skills"):
        if key not in contributes:
            _err(errors, slug, f"{where}.contributes missing '{key}'")
        elif not isinstance(contributes[key], bool):
            _err(errors, slug, f"{where}.contributes.{key} must be bool")


def _has_soul_block_file(addon_dir: Path, modes) -> bool:
    if (addon_dir / "soul_block.md").is_file():
        return True
    # per-mode soul block files
    if modes:
        for m in modes:
            mid = m.get("id") if isinstance(m, dict) else None
            if mid and (addon_dir / f"soul_block.{mid}.md").is_file():
                return True
    return False


def validate_addon(addon_dir: Path, errors: list[str]) -> None:
    slug = addon_dir.name
    manifest_path = addon_dir / "manifest.json"
    if not manifest_path.is_file():
        _err(errors, slug, "missing manifest.json")
        return
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        _err(errors, slug, f"manifest.json is not valid JSON: {exc}")
        return

    for field, typ in REQUIRED_MANIFEST_FIELDS.items():
        if field not in manifest:
            _err(errors, slug, f"manifest missing required field '{field}'")
        elif not isinstance(manifest[field], typ):
            _err(errors, slug, f"manifest.{field} must be {typ.__name__}")

    if manifest.get("id") not in (None, slug):
        _err(errors, slug, f"manifest.id '{manifest.get('id')}' != folder '{slug}'")

    # conflicts_with (FR-7 v1.1): optional declarative addon↔addon
    # incompatibility. Must be a list of addon-id strings; an addon may not
    # list itself. Referenced ids need not be installed (a conflict target may
    # legitimately be absent), so we validate structure only.
    conflicts = manifest.get("conflicts_with")
    if conflicts is not None:
        if not isinstance(conflicts, list):
            _err(errors, slug, "manifest.conflicts_with, if present, must be a list")
        else:
            for i, c in enumerate(conflicts):
                if not isinstance(c, str):
                    _err(errors, slug, f"conflicts_with[{i}] must be a string")
                elif c == manifest.get("id", slug):
                    _err(errors, slug, "conflicts_with may not include the addon itself")

    contributes = manifest.get("contributes")
    if isinstance(contributes, dict):
        _validate_contributes(errors, slug, contributes, "manifest")
        if contributes.get("soul_block") is False and contributes.get("skills") is False:
            _err(errors, slug, "at least one of contributes.{soul_block,skills} must be true")

    modes = manifest.get("modes")
    if modes is not None:
        if not isinstance(modes, list) or not modes:
            _err(errors, slug, "manifest.modes, if present, must be a non-empty array")
            modes = None
        else:
            seen_ids = set()
            default_count = 0
            for i, m in enumerate(modes):
                if not isinstance(m, dict):
                    _err(errors, slug, f"modes[{i}] must be an object")
                    continue
                for field in ("id", "name", "description", "contributes"):
                    if field not in m:
                        _err(errors, slug, f"modes[{i}] missing '{field}'")
                mid = m.get("id")
                if mid in seen_ids:
                    _err(errors, slug, f"duplicate mode id '{mid}'")
                seen_ids.add(mid)
                if isinstance(m.get("contributes"), dict):
                    _validate_contributes(errors, slug, m["contributes"], f"modes[{i}]")
                if m.get("default") is True:
                    default_count += 1
            if default_count > 1:
                _err(errors, slug, "at most one mode may set default=true")

    # contributes/on-disk consistency: consider addon-level OR any mode
    def _any_contributes(key: str) -> bool:
        if isinstance(contributes, dict) and contributes.get(key):
            return True
        if modes:
            return any(isinstance(m, dict) and isinstance(m.get("contributes"), dict)
                       and m["contributes"].get(key) for m in modes)
        return False

    if _any_contributes("soul_block") and not _has_soul_block_file(addon_dir, modes):
        _err(errors, slug, "contributes.soul_block=true but no soul_block.md / soul_block.<mode>.md found")

    if _any_contributes("skills") and not (addon_dir / "skills").is_dir():
        _err(errors, slug, "contributes.skills=true but addons/<slug>/skills/ missing")


def main() -> int:
    errors: list[str] = []
    addon_dirs = sorted(
        d for d in ADDONS_DIR.iterdir()
        if d.is_dir() and (d / "manifest.json").exists()
    )
    if not addon_dirs:
        print("No addons found under addons/ (nothing to validate).")
        return 0

    for addon_dir in addon_dirs:
        validate_addon(addon_dir, errors)

    if errors:
        print("Addon registry validation FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"Addon registry OK: {len(addon_dirs)} addon(s) validated "
          f"({', '.join(d.name for d in addon_dirs)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
