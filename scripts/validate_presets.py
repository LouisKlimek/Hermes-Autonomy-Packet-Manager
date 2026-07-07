#!/usr/bin/env python3
"""Validate HAPM preset registry: folder layout, manifest schema, and the
General-Config whitelist (OQ-2, CEO-confirmed 2026-07-07).

Exit code 0 = all presets valid; non-zero = at least one violation.
Uses only the Python standard library so it can run anywhere.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PRESETS_DIR = Path(__file__).resolve().parent.parent / "presets"

REQUIRED_FILES = ["manifest.json", "SOUL.md", "config.fragment.yaml"]
REQUIRED_MANIFEST_FIELDS = {
    "slug": str,
    "name": str,
    "description": str,
    "version": str,
    "compatibleAddons": list,
}
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")

# OQ-2 whitelist. Top-level keys allowed in a preset config fragment.
ALLOWED_TOP_LEVEL = {"toolsets", "delegation", "kanban", "approvals", "agent"}
# Fully-qualified dotted keys allowed under scoped parents.
ALLOWED_DOTTED = {
    "agent.max_turns",
    "agent.reasoning_effort",
    "agent.disabled_toolsets",
    "kanban.default_assignee",
    "approvals.mode",
    "toolsets",
}
# Parents under which ANY sub-key is allowed.
ALLOWED_WILDCARD_PARENTS = {"delegation"}
# Explicitly forbidden top-level parents (also caught by the allowlist).
FORBIDDEN_TOP_LEVEL = {
    "model", "security", "telegram", "discord", "slack", "matrix",
    "mattermost", "whatsapp", "web", "terminal", "dashboard",
}


def load_yaml(path: Path):
    """Prefer PyYAML; fall back to a tiny parser recovering key structure."""
    try:
        import yaml  # type: ignore

        with path.open() as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        return _tiny_yaml(path.read_text())


def _tiny_yaml(text: str):
    """Parse indentation-based mappings into nested dicts (subset of YAML)."""
    root: dict = {}
    stack = [(-1, root)]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if stripped.startswith("- "):
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = val
    return root


def flatten_keys(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}{k}"
            yield path
            if isinstance(v, dict):
                yield from flatten_keys(v, prefix=f"{path}.")


def validate_fragment(path: Path, errors: list):
    data = load_yaml(path)
    if not isinstance(data, dict):
        errors.append(f"{path}: fragment must be a mapping")
        return
    for top in data:
        if top in FORBIDDEN_TOP_LEVEL:
            errors.append(f"{path}: forbidden top-level key '{top}'")
        elif top not in ALLOWED_TOP_LEVEL:
            errors.append(f"{path}: key '{top}' is not in the OQ-2 whitelist")
    for dotted in flatten_keys(data):
        parent = dotted.split(".", 1)[0]
        if parent in ALLOWED_WILDCARD_PARENTS or parent == "toolsets":
            continue
        if parent in ("agent", "kanban", "approvals"):
            if "." in dotted and dotted not in ALLOWED_DOTTED:
                errors.append(
                    f"{path}: key '{dotted}' not allowed under '{parent}' (OQ-2 whitelist)"
                )


def validate_manifest(path: Path, slug: str, errors: list):
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
        return
    for field, typ in REQUIRED_MANIFEST_FIELDS.items():
        if field not in data:
            errors.append(f"{path}: missing required field '{field}'")
        elif not isinstance(data[field], typ):
            errors.append(f"{path}: field '{field}' must be {typ.__name__}")
    if data.get("slug") != slug:
        errors.append(f"{path}: slug '{data.get('slug')}' must equal folder name '{slug}'")
    if isinstance(data.get("slug"), str) and not SLUG_RE.match(data["slug"]):
        errors.append(f"{path}: slug must be lowercase-hyphen")
    if isinstance(data.get("version"), str) and not SEMVER_RE.match(data["version"]):
        errors.append(f"{path}: version must be semver (x.y.z)")
    if "markers" in data and not isinstance(data["markers"], list):
        errors.append(f"{path}: 'markers' must be an array when present")


def main() -> int:
    if not PRESETS_DIR.is_dir():
        print(f"ERROR: presets dir not found: {PRESETS_DIR}", file=sys.stderr)
        return 2
    errors: list = []
    preset_dirs = sorted(p for p in PRESETS_DIR.iterdir() if p.is_dir())
    if not preset_dirs:
        print("ERROR: no preset folders found", file=sys.stderr)
        return 2
    for preset in preset_dirs:
        slug = preset.name
        for fname in REQUIRED_FILES:
            if not (preset / fname).is_file():
                errors.append(f"{preset}: missing required file '{fname}'")
        if not (preset / "skills").is_dir():
            errors.append(f"{preset}: missing required 'skills/' directory")
        manifest = preset / "manifest.json"
        if manifest.is_file():
            validate_manifest(manifest, slug, errors)
        fragment = preset / "config.fragment.yaml"
        if fragment.is_file():
            validate_fragment(fragment, errors)

    if errors:
        print("Preset validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"Preset validation OK: {len(preset_dirs)} preset(s) valid.")
    for p in preset_dirs:
        print(f"  - {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
