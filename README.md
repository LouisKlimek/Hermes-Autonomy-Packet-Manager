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

## License

MIT.
