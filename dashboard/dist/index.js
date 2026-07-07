/**
 * HAPM — Hermes Autonomy Packet Manager (dashboard plugin frontend).
 *
 * Scaffold shell only. No build step: a plain IIFE that consumes the Hermes
 * Plugin SDK globals (window.__HERMES_PLUGIN_SDK__) and registers a single
 * sidebar-tab view via window.__HERMES_PLUGINS__.register(...).
 *
 * This renders an empty "Autonomy Packet Manager" shell. Later tasks fill in
 * the real UI (profile selection, preset picker, addon toggles — HAPM PRD
 * FR-2..FR-9). The backend health route lives at /api/plugins/hapm/health.
 *
 * NOTE: the backend (plugin_api.py) mounts its routes only when the dashboard
 * process starts, so a `hermes dashboard` restart is required after installing
 * or updating this plugin for the new tab and API routes to load.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var React = SDK.React;
  var h = React.createElement;

  var API = "/api/plugins/hapm";

  function HapmPage() {
    return h(
      "div",
      { style: { padding: "24px 28px", fontFamily: "inherit", maxWidth: 760 } },
      h(
        "h1",
        { style: { fontSize: 20, fontWeight: 700, margin: "0 0 8px" } },
        "Autonomy Packet Manager"
      ),
      h(
        "p",
        { style: { fontSize: 13.5, lineHeight: 1.5, opacity: 0.8, margin: "0 0 16px" } },
        "Apply base profile presets and toggle reversible behavior addons on your Hermes profiles. This is the scaffold shell — profile selection, presets and addon toggles arrive in later releases."
      ),
      h(
        "p",
        { style: { fontSize: 12.5, lineHeight: 1.5, opacity: 0.65, margin: 0 } },
        "Backend mounted at " + API + "/ (restart `hermes dashboard` after install/update to load new tabs and API routes)."
      )
    );
  }

  window.__HERMES_PLUGINS__.register("hapm", HapmPage);
})();
