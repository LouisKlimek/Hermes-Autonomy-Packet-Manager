# HAPM Addon Registry — Format & Schema

This document defines the **Addon Registry** format for the Hermes Autonomy
Packet Manager (HAPM). It is intentionally **separate from the Preset Registry**
(`presets/<preset-slug>/`): presets replace a profile's whole SOUL.md + skills +
general config, whereas addons contribute a scoped, reversible *increment*
(a SOUL.md block and/or skills) on top of whatever preset/profile is active.

> Scope of this document (FR-5): it defines the on-repo **registry format** only.
> The backend apply/toggle logic and reversibility engine are defined by FR-6 /
> FR-7, and the concrete YAGNI addon content by FR-8. Here we ship the format
> plus one worked-example skeleton (`addons/yagni/`) to validate the shape.

## Directory layout

```
addons/
  SCHEMA.md                     # this file
  validate.py                   # structural validator (see below)
  <addon-slug>/
    manifest.json               # required — declares the addon (schema below)
    soul_block.md               # required IFF a mode/addon contributes soul_block
                                #   (or soul_block.<mode>.md for per-mode content)
    skills/                     # required IFF a mode/addon contributes skills
      <skill-name>/
        SKILL.md
```

- Each addon lives in its own folder `addons/<addon-slug>/`, distinct from
  `presets/<preset-slug>/`.
- `<addon-slug>` is a lowercase, hyphen/underscore-safe identifier and SHOULD
  equal `manifest.id`.
- An addon contributes a SOUL.md block, skills, or both — both are optional and
  combinable, but at least one MUST be true.

## `manifest.json` schema

| Field                          | Type              | Required | Description |
|--------------------------------|-------------------|----------|-------------|
| `id`                           | string            | yes      | Stable unique slug. Used in SOUL block markers (`HAPM:addon:<id>`). Matches the folder name. |
| `name`                         | string            | yes      | Human-readable display name. |
| `description`                  | string            | yes      | Short description shown in the UI. |
| `version`                      | string            | yes      | Semver (e.g. `0.1.0`). |
| `contributes`                  | object            | yes      | `{ "soul_block": bool, "skills": bool }`. At least one must be `true`. |
| `contributes.soul_block`       | bool              | yes      | `true` if the addon inserts a SOUL.md block from `soul_block.md`. |
| `contributes.skills`           | bool              | yes      | `true` if the addon ships skills under `skills/`. |
| `compatible_profiles_or_presets` | string[]        | yes      | Whitelist of profile names and/or preset slugs this addon may activate on. `"*"` means "any". The UI only offers the addon where this matches. |
| `modes`                        | mode[]            | no       | Optional list of mutually-exclusive modes (see below). Omit for single-behaviour addons. |

### `modes[]` (optional)

Some addons expose multiple mutually-exclusive behaviours (e.g. YAGNI:
Ponytail / Prompt / Off). When present, `modes` is a non-empty array of:

| Field         | Type    | Required | Description |
|---------------|---------|----------|-------------|
| `id`          | string  | yes      | Stable mode slug (e.g. `prompt`, `ponytail`, `off`). |
| `name`        | string  | yes      | Human-readable mode label. |
| `description` | string  | yes      | What this mode does. |
| `contributes` | object  | yes      | Per-mode `{ "soul_block": bool, "skills": bool }`. Overrides the addon-level `contributes` for that mode (e.g. `off` contributes nothing). |
| `default`     | bool    | no       | Marks the default mode. At most one mode may set this. |

Rules:
- Modes are **mutually exclusive** — exactly one is active at a time.
- An `off` mode (contributing nothing) is the conventional way to represent
  "addon present but inactive".
- When `modes` is present, the effective contribution for an active addon is the
  active mode's `contributes`, not the addon-level `contributes` (the addon-level
  value describes the addon's *maximum* surface for UI/compat purposes).

## SOUL.md block contribution

If a mode (or the addon) sets `contributes.soul_block = true`, the block content
lives in `addons/<slug>/soul_block.md` (single-mode) or
`addons/<slug>/soul_block.<mode>.md` (per-mode). When applied, the FR-7
state/lock engine inserts that content into the target profile's `SOUL.md`
wrapped in uniquely-marked delimiters so it can be removed cleanly on
deactivation without disturbing user-authored text:

```
<!-- HAPM:addon:<id> START -->
...contents of soul_block[.<mode>].md...
<!-- HAPM:addon:<id> END -->
```

- The markers use `manifest.id`. Multi-mode addons MAY namespace by the active
  mode (e.g. `HAPM:addon:<id>:<mode>`) — the final marker convention is owned by
  FR-7. This registry only guarantees the block *content* file exists.
- `soul_block[.<mode>].md` contains raw markdown/prose (no markers) — the engine
  adds the markers at apply time.

## Skills contribution

If a mode (or the addon) sets `contributes.skills = true`, the addon's skills
live under `addons/<slug>/skills/<skill-name>/SKILL.md`, following the standard
Hermes skill layout. The FR-7 engine copies these into the profile's skills on
activation and removes them on deactivation, backing up any pre-existing
same-named skill so it is restored on removal.

## Validation

`addons/validate.py` performs structural validation of every addon against this
schema (manifest fields/types, `contributes` consistency with on-disk files,
mode rules). Run:

```
python3 addons/validate.py
```

It exits non-zero on any violation and is safe to wire into CI.
