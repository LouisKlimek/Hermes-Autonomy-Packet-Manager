# Hermes Autonomy Packet Manager (HAPM)

A Hermes **plugin** that lets you personalize Hermes profiles from a
single dashboard tab — apply base **profile presets** (SOUL.md + skills +
general config) and toggle reversible behavior **addons** (e.g. YAGNI) — without
editing profile files by hand. Every activation is designed to be fully
reversible.

HAPM is now a **full plugin** (agent half + dashboard half), structurally
analogous to the [Tasklist plugin](https://github.com/LouisKlimek/Hermes-Tasklist-Plugin):
a root `plugin.yaml` + `__init__.py` with `register(ctx)` make it discoverable
and enable-able by the agent plugin loader, alongside the existing `dashboard/`
UI half. In this version the agent half is a deliberate **no-op skeleton** —
`register()` runs no hooks and applies no profile mutation; it exists so HAPM is
recognized as a full agent plugin. Agent-tools / auto-hooks that apply
presets/addons to profiles are a separate, human-gated addition. Install/enable
works analogously to Tasklist.

> **Status:** scaffold shell (FR-1). This repo currently ships only the
> installable, mountable plugin skeleton — a sidebar tab, an empty frontend
> view, and a health/ping backend. Profile discovery, preset/addon registries,
> apply/revert state management (PRD FR-2..FR-9) land in later tasks.

Built with both plugin halves, analogous to
[LouisKlimek/Hermes-Tasklist-Plugin](https://github.com/LouisKlimek/Hermes-Tasklist-Plugin):

```
Hermes-Autonomy-Packet-Manager/
├── plugin.yaml              # agent-plugin metadata (name/version/description/author)
├── __init__.py              # register(ctx) — v1 no-op skeleton (no hooks, no mutation)
└── dashboard/
    ├── manifest.json        # plugin metadata + sidebar tab registration
    ├── plugin_api.py        # FastAPI backend, mounted at /api/plugins/hapm/
    └── dist/
        └── index.js         # frontend entry (IIFE, uses window.__HERMES_PLUGIN_SDK__)
```

## Install

### From the dashboard (no terminal)

Open the **Plugins** tab in the dashboard sidebar → **Install from GitHub /
Git URL**, paste the repo, and click **Install**:

```
https://github.com/LouisKlimek/Hermes-Autonomy-Packet-Manager
```

(the shorthand `LouisKlimek/Hermes-Autonomy-Packet-Manager` works too). The repo
root *is* the plugin — its `dashboard/manifest.json` sits at the top level — so
the bare URL is enough.

### Manual

```bash
git clone https://github.com/LouisKlimek/Hermes-Autonomy-Packet-Manager \
  ~/.hermes/plugins/hapm
```

The final layout must be `~/.hermes/plugins/hapm/dashboard/manifest.json` (no
extra nesting).

## Restart required

> **A `hermes dashboard` restart is required after installing or updating this
> plugin.** The plugin ships a backend (`plugin_api.py`), and plugin API routes
> are mounted **only when the dashboard process starts** — a browser refresh or
> a plugin rescan is **not** enough to load the new sidebar tab's backend
> routes. After restarting, hard-refresh the browser (Ctrl+Shift+R). The
> **Autonomy Packet Manager** tab then appears in the sidebar.

## Verify the mount

Once the dashboard is restarted, the backend answers at:

```
GET /api/plugins/hapm/health   ->  {"plugin": "hapm", "status": "ok", "version": "0.1.0"}
GET /api/plugins/hapm/ping     ->  {"pong": true}
```

## Addon enable/disable engine (FR-6)

The backend exposes a generic, reversible addon toggle engine on top of the
FR-5 addon registry (`addons/`) and the FR-7 state/lock + backup primitives
(`dashboard/hapm/`):

```
GET  /api/plugins/hapm/addons?target=<profile-or-preset>
     -> addons whose manifest whitelist (compatible_profiles_or_presets)
        admits <target> ("*" matches any); each carries an `enabled` flag
        read from the profile's hapm.lock.

POST /api/plugins/hapm/addons/enable
     body: {"profile": "<name>", "addon": "<id>", "mode": "<mode?>",
            "target": "<whitelist-target?>"}
     -> inserts the addon's SOUL.md block wrapped in
        `<!-- HAPM:addon:<id> START/END -->` and/or copies its skills into the
        profile, recording everything in hapm.lock for reversal.

POST /api/plugins/hapm/addons/disable
     body: {"profile": "<name>", "addon": "<id>"}
     -> removes exactly that addon's marked SOUL block and the skills it added
        (restoring any shadowed pre-existing skill), leaving the rest of the
        file/dir tree byte-identical.
```

Guarantees:

- **Independence** — multiple addons toggle without touching each other's block
  or skills.
- **Whitelist enforcement** — enabling on a non-whitelisted target returns
  `409 not_compatible` (never silently ignored).
- **Conflict detection** — an untracked/foreign SOUL block for the same addon id
  returns `409 conflict` rather than corrupting the file. v1 detects and reports
  conflicts; it does not auto-resolve (PRD Non-Goal).
- **Lock updated** on every enable/disable to reflect active addons + modes.

### Verifying the engine

```bash
python3 dashboard/tests/test_addon_toggle.py   # stdlib-only, no pytest needed
# or, with pytest installed:
python3 -m pytest -q dashboard/tests
```

The headline test
`test_two_independent_addons_disable_one_leaves_other_untouched` enables two
independent addons, disables one, and asserts the other's block/skills are
byte-identical while the disabled addon's contribution is byte-exactly removed.
## Per-profile status (FR-9)

```
GET /api/plugins/hapm/profiles/{profile}/status
```

Returns the profile's current HAPM state, read **live** from that profile's
`hapm.lock` on every request (single source of truth — no caching that could
drift from what an FR-4 preset-apply or FR-6 addon-toggle just wrote):

```json
{
  "profile": "fullstack-developer",
  "profile_dir": "/…/profiles/fullstack-developer",
  "lock_present": true,
  "active_preset": "fullstack-developer",
  "addons": [
    {"addon_id": "yagni", "mode": "prompt"},
    {"addon_id": "tdd",   "mode": "full"}
  ]
}
```

- A profile HAPM has never touched (no `hapm.lock`) returns **200** with a
  well-defined empty state (`lock_present: false`, `active_preset: null`,
  `addons: []`), not an error.
- Structured JSON errors (never a 500 stack trace): `invalid_profile_name`
  (400, path-traversal guard), `profile_not_found` (404), `corrupt_hapm_lock`
  (500).

Verification (documented in `dashboard/test_status_endpoint.py`): apply a
preset + enable 2 addons, call status and confirm it matches; disable 1 addon,
call status again, confirm it updated — proving reads are never stale.

## State engine (FR-7)

The reversibility engine lives in `dashboard/hapm/` as a pure filesystem
library (no network, no dashboard coupling) that the later apply/revert
endpoints (FR-4 / FR-6) build on:

- `state.py` — per-profile `hapm.lock` record (active preset, active addons +
  modes, backup markers) with atomic read/write.
- `backup.py` — `BackupStore`: snapshot/restore `SOUL.md`, `skills/`, and
  `config.yaml` byte-exactly under `profiles/<profile>/.hapm/backups/<id>/`.
- `soul_blocks.py` — insert/replace/remove addon SOUL.md contributions inside
  `<!-- HAPM:addon:<id> START/END -->` markers without touching surrounding
  user text.
- `skills_tracker.py` — track addon-added skills distinctly from pre-existing
  same-named skills it shadows, so disabling removes only what was added and
  restores any shadowed original.
- `index.py` — optional central index at `$HERMES_HOME/hapm_index.json` for
  cross-profile status queries (OQ-3) without scanning every profile.

Run the tests:

```bash
python -m pytest dashboard/tests           # with pytest installed
python dashboard/tests/test_state_engine.py  # stdlib-only fallback runner
```

The key guarantee is proven by
`test_full_apply_then_revert_is_byte_identical`: apply a preset, toggle addons,
then fully revert — `SOUL.md` / `config.yaml` / `skills/` return byte-identical
to the pre-change state.

## In-UI Addon Builder (v1.2)

Lets a user author a **community addon** (a SOUL.md block and/or an inline
skill) from the dashboard, without touching a profile directly. Backend routes:

```text
POST /api/plugins/hapm/builder/check          server-side sanitizing pass
POST /api/plugins/hapm/builder/drafts         save a Local Draft (not activatable)
GET  /api/plugins/hapm/builder/drafts         list drafts
GET  /api/plugins/hapm/builder/drafts/{id}    fetch one draft
POST /api/plugins/hapm/builder/submit         final sanitize + open a PR
```

Security model (all server-enforced — the client's live check is UX only):

- **Fixed write targets (§4.1).** The builder never accepts a free file path.
  A draft can only ever produce `addons/<id>/manifest.json`, an optional
  `soul_block.md`, and an optional `skills/hapm-addon-<id>/SKILL.md` — enforced
  by `builder_sanitize.assert_target_allowed`, so a direct API call that forces
  any other path is rejected regardless of the client.
- **Seven non-overridable deny-pattern rules (§4.2)** (secrets, forbidden
  config keys, exfiltration/bypass phrasing, path/env refs, executable
  code/shell, size limit, HTML/script tags) run on **every** draft save **and**
  again as a final gate before PR creation. No client-side override.
- **Skills are inline markdown only (§4.3)** — no `scripts/`/`references/`/
  `assets/` subfolders, no file smuggling; curated selections come from a fixed
  whitelist.
- **Local Draft + PR to activate (§5).** A saved draft lives in the HAPM draft
  store outside every profile and the repo tree, and has **zero effect** on any
  profile. The only activation path is opening a PR that lands the addon in the
  shared `addons/` registry; the service account may only create a branch and
  open a PR — it never pushes to `main` and never auto-merges. Merge stays
  human / pr-reviewer driven.
- **One manifest schema (FR-7 / §6).** Community addons use the *identical*
  manifest schema, `hapm.lock` schema, marker convention and enable/disable code
  path as core addons. `author`/`origin` are written under `_provenance` as
  audit metadata only and branch no code path. Deactivating a community addon
  restores `SOUL.md` byte-for-byte, same as core.

The seven Developer Acceptance Criteria are proven in
`dashboard/tests/test_builder.py` (runs under pytest or stdlib-only).

## License

MIT.
