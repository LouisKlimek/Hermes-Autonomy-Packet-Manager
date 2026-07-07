# Preset Registry Schema (HAPM FR-3)

A **preset** is a base profile template. Applying a preset to a Hermes profile
sets that profile's `SOUL.md`, its `skills/`, and a whitelisted subset of its
General Config (`profiles/<profile>/config.yaml`). See PRD FR-3 / FR-4.

## Folder layout

Every preset lives in its own folder under `presets/`:

```
presets/<preset-slug>/
├── manifest.json          # required — registry metadata (schema below)
├── SOUL.md                # required — role definition applied to the profile
├── config.fragment.yaml   # required — whitelisted General-Config merge fragment
└── skills/                # required — skills contributed by the preset
    └── .gitkeep           # keep the dir tracked when the preset has no skills yet
```

`<preset-slug>` is a lowercase, hyphenated slug (e.g. `fullstack-developer`).
It MUST equal `manifest.json.slug`.

## manifest.json

```jsonc
{
  "slug": "fullstack-developer",     // string, required. Matches folder name. lowercase-hyphen.
  "name": "Fullstack Developer",      // string, required. Human-readable display name.
  "description": "…",                 // string, required. One-line summary shown in the UI.
  "version": "1.0.0",                 // string, required. Semver of the preset content.
  "compatibleAddons": ["*"],          // array<string>, required. Addon slugs allowed on
                                       //   profiles using this preset. "*" = allow all.
                                       //   Empty array = no addons compatible.
  "markers": ["engineering"]          // array<string>, optional. Free-form capability
                                       //   tags used for addon compatibility matching.
}
```

### Field rules

| Field              | Type            | Required | Notes                                              |
| ------------------ | --------------- | -------- | -------------------------------------------------- |
| `slug`             | string          | yes      | Must equal the folder name.                        |
| `name`             | string          | yes      | Display name.                                      |
| `description`      | string          | yes      | Short, one sentence.                               |
| `version`          | string (semver) | yes      | Bump when preset content changes.                  |
| `compatibleAddons` | array<string>   | yes      | Addon slugs, or `["*"]` for all, or `[]` for none. |
| `markers`          | array<string>   | no       | Capability tags for addon matching.                |

## config.fragment.yaml — General-Config whitelist (OQ-2, CEO-confirmed 2026-07-07)

A preset MUST only set a narrow, role-defining whitelist, applied as a
**merge/patch fragment** (never a full-file overwrite). This keeps FR-7
reversibility clean and prevents secret/environment loss.

**Allowed keys (role-defining) — ONLY these:**

- `agent.max_turns`
- `agent.reasoning_effort`
- `toolsets`
- `agent.disabled_toolsets`
- `delegation.*`
- `kanban.default_assignee`
- `approvals.mode`

**Forbidden (environment / secrets / infra — a preset never touches these):**

- `model.*` (provider, base_url, api_key, …)
- any `*.api_key`
- `security.*`
- platform tokens: `telegram.*`, `discord.*`, `slack.*`, `matrix.*`,
  `mattermost.*`, `whatsapp.*`
- `web.*`
- `terminal.*`
- `dashboard.*`

A validation step (`scripts/validate_presets.py`) enforces this whitelist and
the required folder layout for every preset. Run it before opening a PR that
adds or changes a preset.

## Adding a new preset

1. Create `presets/<slug>/` with the four required entries above.
2. Fill `manifest.json` per the schema; set `slug` to the folder name.
3. Put only whitelisted keys in `config.fragment.yaml`.
4. Write a coherent `SOUL.md` role definition.
5. Run `python3 scripts/validate_presets.py` — it must pass.
