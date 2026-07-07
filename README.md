# Hermes Autonomy Packet Manager (HAPM)

A Hermes **dashboard plugin** that lets you personalize Hermes profiles from a
single dashboard tab — apply base **profile presets** (SOUL.md + skills +
general config) and toggle reversible behavior **addons** (e.g. YAGNI) — without
editing profile files by hand. Every activation is designed to be fully
reversible.

> **Status:** scaffold shell (FR-1). This repo currently ships only the
> installable, mountable plugin skeleton — a sidebar tab, an empty frontend
> view, and a health/ping backend. Profile discovery, preset/addon registries,
> apply/revert state management (PRD FR-2..FR-9) land in later tasks.

Built as a pure dashboard plugin, analogous to
[LouisKlimek/Hermes-Tasklist-Plugin](https://github.com/LouisKlimek/Hermes-Tasklist-Plugin):

```
Hermes-Autonomy-Packet-Manager/
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

## License

MIT.
