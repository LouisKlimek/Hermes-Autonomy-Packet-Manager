/**
 * HAPM — Hermes Autonomy Packet Manager (dashboard plugin frontend).
 *
 * No build step: a plain IIFE that consumes the Hermes Plugin SDK globals
 * (window.__HERMES_PLUGIN_SDK__) and registers a single sidebar-tab view via
 * window.__HERMES_PLUGINS__.register(...).
 *
 * This build combines two HAPM frontend tasks in one file (single-file plugin):
 *   - t_a380191e: left-hand profile selector + right-hand preset switcher panel
 *     (§2 layout, §3 profile selector, §4 preset section + confirmation dialog,
 *      §6.1/§6.2 error states, §7 restart notice). Delivered on the base branch.
 *   - t_8b337378 (THIS task): §5 addon toggle/mode UI (compatible-addon list,
 *     on/off toggles, segmented mode control) + §8 FR-9 status view (active
 *     preset + all active addons with mode). Layered on top of the base branch.
 *
 * UI language is German (de-DE) per the designer's OQ-4 decision. NOTE: the
 * designer's HAPM_UX_SPEC.md artifact was lost to scratch-workspace resets and
 * could not be re-read for this task; the §5/§8 German copy strings below follow
 * the designer's documented decisions (German, YAGNI 3-segment Ponytail/Prompt/
 * Aus control with Ponytail disabled per the Human Gate) and the house style of
 * the already-merged §2–§4/§6/§7 copy. Reviewers should reconcile the exact §5/§8
 * wording against the spec if it is recovered.
 *
 * YAGNI Modus A ("Ponytail") is human-gated (kanban t_f321af09) and NOT
 * implemented: it renders as a visibly DISABLED segment and is NEVER wired to a
 * backend call. The backend manifest does not return it; it is appended locally,
 * greyed, purely presentational.
 *
 * Backend contracts consumed (mounted at /api/plugins/hapm/):
 *   GET  /profiles                      -> { profiles_dir, profiles:[{name,path}] }
 *   GET  /presets                       -> { presets_dir, presets:[{slug,name,description,version,path}] }
 *   GET  /profiles/{profile}/status     -> { profile, profile_dir, lock_present,
 *                                            active_preset, addons:[{addon_id,mode}] }
 *   POST /apply  {profile,preset}       -> 200 { status:"applied", profile, preset,
 *                                            backup_id, config_keys_merged }
 *                                          or structured error bodies
 *                                          (400 missing_field, 404 unknown_profile,
 *                                           422 whitelist_violation, 400 apply_failed).
 *   GET  /addons?target=<p>&profile=<p>  -> { target, addons_root, addons:[{ id,
 *                                            name, description, version,
 *                                            contributes, modes:[{id,name,
 *                                            description,contributes,default}],
 *                                            compatible_profiles_or_presets,
 *                                            enabled }] }  (FR-6a)
 *   POST /addons/enable  {profile,addon,target?,mode?} -> {...enabled:true...}
 *                                          err: not_compatible(409), conflict(409),
 *                                          already_enabled(409), *_not_found(404).
 *   POST /addons/disable {profile,addon}  -> {...enabled:false...}
 *                                          err: not_enabled(409), *_not_found(404).
 *
 * NOTE: /presets and /apply (FR-4) are provided by a sibling backend PR that
 * mounts them at these paths; this frontend is written against that documented
 * contract. Until that PR is merged those two routes may 404 — the UI degrades
 * gracefully (preset picker shows a load error with a retry) rather than
 * breaking.
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
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useCallback = React.useCallback;
  var useRef = React.useRef;

  var API = "/api/plugins/hapm";

  // Inject the plugin's small CSS payload once (spinner keyframes + the
  // mobile stacking rule for the conflict dialog footer). Done here rather
  // than inline because @keyframes and media queries can't be expressed via
  // React inline styles.
  (function injectStyles() {
    if (typeof document === "undefined") return;
    if (document.getElementById("hapm-plugin-styles")) return;
    var css =
      "@keyframes hapm-spin{to{transform:rotate(360deg)}}" +
      "@media (max-width:560px){" +
      ".hapm-conflict-footer{flex-direction:column-reverse;align-items:stretch}" +
      ".hapm-conflict-footer>button,.hapm-detail-footer>button{width:100%}" +
      "}";
    try {
      var el = document.createElement("style");
      el.id = "hapm-plugin-styles";
      el.textContent = css;
      (document.head || document.documentElement).appendChild(el);
    } catch (e) {
      /* non-fatal: spinner still shows, just without rotation */
    }
  })();

  // ---------------------------------------------------------------------------
  // German UI copy (verbatim from HAPM_UX_SPEC.md — do not paraphrase).
  // ---------------------------------------------------------------------------
  var COPY = {
    tabTitle: "Autonomy Packet Manager",
    tabIntro:
      "Apply a base preset to a Hermes profile and manage the current state.",
    profilesHeader: "Profile selection",
    noPresetBadge: "No Preset",
    profilesLoadError: "Profiles could not be loaded.",
    profilesLoadErrorSub:
      "Please reload the page. If the error persists, check the permissions on `$HERMES_HOME/profiles/`.",
    profilesEmpty: "No profiles found.",
    profilesEmptySub:
      "No profiles were found under `$HERMES_HOME/profiles/`. Check the Hermes installation.",
    chooseProfile: "Select a profile on the left.",
    retry: "Retry",
    presetHeader: "Active Preset",
    presetEmpty: "No preset applied — choose a template",
    presetEmptySub:
      "Choose a preset from the list below to set this profile's SOUL.md, skills, and configuration.",
    presetPickerLabel: "Preset",
    applyButton: "Apply Preset",
    applying: "Applying …",
    cancel: "Cancel",
    dismiss: "Close",
    details: "Details",
    detailsDialogLabel: "Item details",
    detailsPurpose: "Purpose",
    detailsEffects: "Effects and capabilities",
    detailsCompatibility: "Availability and compatibility",
    detailsDescriptionFallback: "No description is available from this registry entry.",
    detailsNoEffects: "This registry entry does not declare user-visible effects.",
    presetDetailEffect:
      "Applying this preset changes the selected profile's SOUL.md, skills, and allowed configuration.",
    presetDetailActive: "This is the active preset for the selected profile.",
    presetDetailInactive: "This preset is available to apply to the selected profile.",
    addonDetailActive: "This addon is active for the selected profile.",
    addonDetailAvailable: "This addon is available for the selected profile.",
    addonDetailNoPresetAvailable:
      "No active preset is selected. This compatible addon remains available through its profile controls.",
    addonDetailControlsBusy:
      "Its enable controls are temporarily unavailable while another change is in progress.",
    addonDetailCompatibility: "Compatible with: ",
    addonDetailNoCompatibility: "No compatibility information is available from this registry entry.",
    dialogTitle: "Apply preset?",
    statusHeaderPrefix: "Status: ",
    activePresetLabel: "Active preset: ",
    noPresetApplied: "— (no preset applied)",
    presetsLoadError: "Presets could not be loaded",
    presetsLoadErrorSub: "Please try again.",
    applyFailedTitle: "Preset could not be applied",
    applyUnknownError: "Unknown error while applying the preset.",
    // Restart notice §7.1 (post-install, routes not mounted)
    restartHardTitle: "Restart required",
    restartHardBody:
      "The Autonomy Packet Manager was installed or updated. Backend routes are only loaded when `hermes dashboard` starts — restart the dashboard so all functionality becomes available.",
    // Restart notice §7.2 (post-action soft toast)
    restartSoftBody:
      "Change saved. Takes effect per normal Hermes profile-reload semantics; if the behavior doesn't take effect as expected, restart the affected agent session.",
    // Error §6.1 repo unreachable
    repoUnreachableTitle: "Repository unreachable",
    repoUnreachableBody:
      "Presets and addons could not be loaded because the repository `LouisKlimek/Hermes-Autonomy-Packet-Manager` is currently unreachable. Check your internet connection or GitHub credentials.",
    // Error §6.2 profile not writable
    notWritableTitle: "Profile not writable",

    // --- §5 Addon section ------------------------------------------------
    addonsHeader: "Addons",
    addonsIntro:
      "Reversible behavior addons for this profile. Only addons compatible with the active preset or profile are shown.",
    addonsLoading: "Loading addons …",
    addonsEmpty: "No compatible addons for this profile.",
    addonsEmptySub:
      "No compatible addons are currently available for this profile's active preset.",
    addonsLoadError: "Addons could not be loaded",
    addonsLoadErrorSub: "Please try again.",
    addonOn: "On",
    addonOff: "Off",
    addonModeLabel: "Mode",
    // Error: addon incompatible (backend `not_compatible`, 409) §6.3
    addonIncompatibleTitle: "Addon not compatible",
    // Error: addon conflict (backend `conflict`, 409) §6.4
    addonConflictTitle: "Addon conflict",
    addonToggleUnknownError:
      "The addon could not be toggled (unknown error).",
    // YAGNI Modus A placeholder — Human Gate (t_f321af09): shown, disabled,
    // never wired to a backend call.
    ponytailLabel: "Ponytail",
    ponytailDisabledHint:
      "Coming soon — pending approval from Louis.",

    // --- v1.1 Addon↔Addon Conflict Resolution Dialog (t_3a0434b2) -------
    // ENGLISH copy per the UX spec (HAPM_V1_1_CONFLICT_RESOLUTION_POPUP_SPEC.md,
    // §Copy Strings). This guided dialog is the primary path for
    // Addon↔Addon `conflicts_with` collisions; the flat v1 error banners
    // above stay as the fallback for FR-5 incompatibility and for the
    // "conflict check unavailable" (network) case.
    conflictDialogTitle: "Addon Conflict",
    conflictReversibilityNote:
      "Deactivated addons can be restored exactly as they were — nothing is lost.",
    conflictReasonFallbackPrefix: "This addon is not compatible with ",
    conflictButtonPrimary: "Deactivate Conflicting & Activate This One",
    conflictButtonSecondary: "Cancel — Keep Current Addons",
    conflictLoading: "Applying changes…",
    conflictRollbackError:
      "Couldn't apply the change. Nothing was deactivated or activated — your current setup is unchanged.",
    conflictCheckUnavailable:
      "Couldn't check for addon conflicts right now. Try again in a moment.",

    // --- §8 Status view --------------------------------------------------
    statusViewHeader: "Current state",
    statusActivePresetLabel: "Active Preset",
    statusNoPreset: "No preset applied",
    statusActiveAddonsLabel: "Active Addons",
    statusNoAddons: "No active addons",
    statusModePrefix: "Mode: ",
  };

  function addonIncompatibleBody(addonName, target) {
    return (
      "The addon \"" +
      addonName +
      "\" is not compatible with \"" +
      target +
      "\" and cannot be activated for this profile. " +
      "Compatible addons are determined by each addon's whitelist."
    );
  }

  function addonConflictBody(addonName) {
    return (
      "The addon \"" +
      addonName +
      "\" conflicts with an already-active addon or an existing SOUL block and was not activated. " +
      "Disable the conflicting addon and try again."
    );
  }

  // Build the correct §6.3/§6.4 error banner for a failed addon toggle. `Banner`
  // is defined below; this returns an element that references it lazily at
  // render time, so definition order does not matter.
  function addonToggleErrorNode(err, addon, target) {
    var name = (addon && (addon.name || addon.id)) || "";
    if (isNetworkFailure(err)) {
      return h(
        Banner,
        { variant: "danger", title: COPY.repoUnreachableTitle },
        COPY.repoUnreachableBody
      );
    }
    var code = err && err.body && err.body.error;
    if (code === "not_compatible") {
      return h(
        Banner,
        { variant: "warn", title: COPY.addonIncompatibleTitle },
        addonIncompatibleBody(name, target)
      );
    }
    if (code === "conflict") {
      return h(
        Banner,
        { variant: "warn", title: COPY.addonConflictTitle },
        addonConflictBody(name)
      );
    }
    var msg =
      (err && err.body && (err.body.message || err.body.error)) ||
      COPY.addonToggleUnknownError;
    return h(
      Banner,
      { variant: "danger", title: COPY.addonsLoadError },
      msg
    );
  }

  function presetNotWritableBody(profile) {
    return (
      "Profile \"" +
      profile +
      "\" could not be modified — the files are not writable. Check the file permissions under `$HERMES_HOME/profiles/" +
      profile +
      "/`."
    );
  }

  function dialogBody(presetName, profileName) {
    return [
      "Applying \"" +
        presetName +
        "\" will overwrite SOUL.md, skills, and the allowed configuration fields of profile \"" +
        profileName +
        "\".",
      "The current state is automatically backed up beforehand and can be restored by reverting this preset.",
    ];
  }

  // ---------------------------------------------------------------------------
  // Theme helpers — read from CSS variables the dashboard exposes, with sane
  // fallbacks so the plugin looks native without hardcoding a full palette.
  // ---------------------------------------------------------------------------
  var C = {
    text: "var(--hermes-text, #e6e6e6)",
    textDim: "var(--hermes-text-dim, rgba(230,230,230,0.65))",
    bg: "var(--hermes-bg, #1a1b1e)",
    panel: "var(--hermes-panel, #232428)",
    border: "var(--hermes-border, rgba(255,255,255,0.10))",
    accent: "var(--hermes-accent, #6ea8fe)",
    danger: "#e5534b",
    dangerText: "#fff",
    warnBg: "rgba(229,150,75,0.14)",
    warnBorder: "rgba(229,150,75,0.5)",
    okBg: "rgba(110,168,254,0.12)",
    okBorder: "rgba(110,168,254,0.45)",
  };

  // ---------------------------------------------------------------------------
  // Fetch helpers. Distinguish a real network failure ("repo unreachable" —
  // §6.1) from a structured backend error (JSON body with an `error` field).
  // ---------------------------------------------------------------------------
  function isNetworkFailure(err) {
    return err && (err.__network === true || err instanceof TypeError);
  }

  function tagError(res, body) {
    var err = new Error((body && body.error) || "http_" + res.status);
    err.status = res.status;
    err.body = body;
    return err;
  }

  async function apiGet(path) {
    var res;
    try {
      res = await fetch(API + path, { headers: { Accept: "application/json" } });
    } catch (e) {
      var ne = new Error("network");
      ne.__network = true;
      throw ne;
    }
    var body = null;
    try {
      body = await res.json();
    } catch (e) {
      body = null;
    }
    if (!res.ok) throw tagError(res, body);
    return body;
  }

  async function apiPost(path, payload) {
    var res;
    try {
      res = await fetch(API + path, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      var ne = new Error("network");
      ne.__network = true;
      throw ne;
    }
    var body = null;
    try {
      body = await res.json();
    } catch (e) {
      body = null;
    }
    if (!res.ok) throw tagError(res, body);
    return body;
  }

  // ---------------------------------------------------------------------------
  // Presentational primitives.
  // ---------------------------------------------------------------------------
  function Banner(props) {
    var v = props.variant || "warn"; // warn | info | danger
    var bg = v === "info" ? C.okBg : C.warnBg;
    var border = v === "info" ? C.okBorder : C.warnBorder;
    if (v === "danger") {
      bg = "rgba(229,83,75,0.14)";
      border = "rgba(229,83,75,0.55)";
    }
    return h(
      "div",
      {
        role: "alert",
        style: {
          background: bg,
          border: "1px solid " + border,
          borderRadius: 8,
          padding: "12px 14px",
          margin: "0 0 14px",
          fontSize: 13,
          lineHeight: 1.5,
        },
      },
      props.title
        ? h(
            "div",
            { style: { fontWeight: 700, marginBottom: props.children ? 4 : 0 } },
            props.title
          )
        : null,
      props.children
        ? h("div", { style: { opacity: 0.9 } }, props.children)
        : null,
      props.actions
        ? h(
            "div",
            { style: { marginTop: 10, display: "flex", gap: 8 } },
            props.actions
          )
        : null
    );
  }

  function Button(props) {
    var kind = props.kind || "secondary"; // primary | danger | secondary
    var base = {
      fontSize: 13,
      fontWeight: 600,
      padding: "8px 14px",
      borderRadius: 7,
      cursor: props.disabled ? "not-allowed" : "pointer",
      border: "1px solid " + C.border,
      opacity: props.disabled ? 0.55 : 1,
      background: "transparent",
      color: C.text,
      fontFamily: "inherit",
    };
    if (kind === "primary") {
      base.background = C.accent;
      base.color = "#0b1220";
      base.border = "1px solid transparent";
    } else if (kind === "danger") {
      base.background = C.danger;
      base.color = C.dangerText;
      base.border = "1px solid transparent";
    }
    return h(
      "button",
      {
        type: "button",
        ref: props.buttonRef || undefined,
        disabled: !!props.disabled,
        "aria-label": props["aria-label"] || undefined,
        onClick: props.onClick,
        style: Object.assign(base, props.style || {}),
      },
      props.children
    );
  }

  // ---------------------------------------------------------------------------
  // Read-only item-detail dialog. It derives all copy from the already-loaded
  // registry/API summaries and intentionally has no API action handlers.
  // ---------------------------------------------------------------------------
  function DetailDialog(props) {
    var titleId = "hapm-detail-title";
    var closeRef = React.useRef(null);
    var dialogRef = React.useRef(null);
    var triggerRef = React.useRef(null);
    var item = props.item || {};
    var effects = props.effects || [];

    useEffect(function () {
      triggerRef.current =
        (typeof document !== "undefined" && document.activeElement) || null;
      if (closeRef.current) closeRef.current.focus();
      return function () {
        var trigger = triggerRef.current;
        if (trigger && typeof trigger.focus === "function") trigger.focus();
      };
    }, []);

    function focusableElements() {
      if (!dialogRef.current) return [];
      return Array.prototype.slice.call(
        dialogRef.current.querySelectorAll(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
      );
    }

    function onKeyDown(e) {
      if (e.key === "Escape" || e.key === "Esc") {
        e.preventDefault();
        props.onClose();
        return;
      }
      if (e.key !== "Tab") return;
      var focusable = focusableElements();
      if (!focusable.length) {
        e.preventDefault();
        if (dialogRef.current) dialogRef.current.focus();
        return;
      }
      var active = typeof document !== "undefined" ? document.activeElement : null;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (e.shiftKey && (!dialogRef.current.contains(active) || active === first)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (!dialogRef.current.contains(active) || active === last)) {
        e.preventDefault();
        first.focus();
      }
    }

    function section(label, content) {
      return h(
        "div",
        { style: { marginTop: 14 } },
        h("div", { style: { fontSize: 12, fontWeight: 700, opacity: 0.65 } }, label),
        h(
          "div",
          { style: { fontSize: 13, lineHeight: 1.5, marginTop: 4, overflowWrap: "anywhere" } },
          content
        )
      );
    }

    return h(
      "div",
      {
        role: "presentation",
        style: { position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 10000, padding: 16 },
        onClick: function (e) { if (e.target === e.currentTarget) props.onClose(); },
      },
      h(
        "div",
        {
          role: "dialog",
          ref: dialogRef,
          tabIndex: -1,
          "aria-modal": "true",
          "aria-labelledby": titleId,
          onKeyDown: onKeyDown,
          style: { background: C.panel, color: C.text, border: "1px solid " + C.border, borderRadius: 12, maxWidth: 560, width: "100%", maxHeight: "85vh", overflowY: "auto", padding: "20px 22px", boxShadow: "0 20px 60px rgba(0,0,0,0.45)" },
        },
        h(
          "div",
          { style: { display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 } },
          h("h2", { id: titleId, style: { fontSize: 17, fontWeight: 700, margin: 0, overflowWrap: "anywhere" } }, item.name || item.id || item.slug),
          h(Button, { buttonRef: closeRef, kind: "secondary", onClick: props.onClose, "aria-label": COPY.dismiss, style: { flex: "0 0 auto" } }, COPY.dismiss)
        ),
        section(COPY.detailsPurpose, item.description || COPY.detailsDescriptionFallback),
        section(
          COPY.detailsEffects,
          effects.length
            ? h("ul", { style: { margin: "4px 0 0", paddingLeft: 20 } }, effects.map(function (effect, index) { return h("li", { key: index, style: { marginBottom: 4 } }, effect); }))
            : COPY.detailsNoEffects
        ),
        section(COPY.detailsCompatibility, props.availability || COPY.addonDetailNoCompatibility),
        h(
          "div",
          { className: "hapm-detail-footer", style: { display: "flex", justifyContent: "flex-end", marginTop: 20 } },
          h(Button, { kind: "secondary", onClick: props.onClose }, COPY.dismiss)
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Confirmation dialog (§4.3) — destructive preset apply.
  // ---------------------------------------------------------------------------
  function ConfirmDialog(props) {
    // props: presetName, profileName, busy, errorNode, onCancel, onConfirm
    var bodyLines = dialogBody(props.presetName, props.profileName);
    var titleId = "hapm-preset-confirm-title";
    var dialogRef = React.useRef(null);
    var cancelRef = React.useRef(null);
    var triggerRef = React.useRef(null);

    // Focus the safe action first and return focus to the Apply trigger on close.
    useEffect(function () {
      triggerRef.current =
        (typeof document !== "undefined" && document.activeElement) || null;
      if (cancelRef.current) {
        try {
          cancelRef.current.focus();
        } catch (e) {}
      }
      return function () {
        var trigger = triggerRef.current;
        if (trigger && typeof trigger.focus === "function") {
          try {
            trigger.focus();
          } catch (e) {}
        }
      };
    }, []);

    function onKeyDown(e) {
      if (e.key === "Escape" || e.key === "Esc") {
        if (!props.busy) {
          e.preventDefault();
          props.onCancel();
        }
        return;
      }
      if (e.key !== "Tab") return;
      var root = dialogRef.current;
      if (!root) return;
      var focusable = root.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), ' +
          '[tabindex]:not([tabindex="-1"])'
      );
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      var active = document.activeElement;
      if (e.shiftKey ? active === first || !root.contains(active) : active === last) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    }
    return h(
      "div",
      {
        style: {
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.55)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 9999,
          padding: 16,
        },
        onClick: function (e) {
          if (e.target === e.currentTarget && !props.busy) props.onCancel();
        },
        onKeyDown: onKeyDown,
      },
      h(
        "div",
        {
          ref: dialogRef,
          role: "dialog",
          "aria-modal": "true",
          "aria-labelledby": titleId,
          style: {
            background: C.panel,
            color: C.text,
            border: "1px solid " + C.border,
            borderRadius: 12,
            maxWidth: 520,
            width: "100%",
            padding: "20px 22px",
            boxShadow: "0 20px 60px rgba(0,0,0,0.45)",
          },
        },
        h(
          "div",
          { style: { display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 12 } },
          h(
            "h2",
            { id: titleId, style: { fontSize: 17, fontWeight: 700, margin: 0 } },
            COPY.dialogTitle
          ),
          h(
            "button",
            {
              type: "button",
              "aria-label": COPY.dismiss,
              // Keep a focusable modal target while requests are in flight.
              // It is intentionally aria-disabled rather than disabled so the
              // dialog's Tab containment never degenerates to zero targets.
              "aria-disabled": !!props.busy,
              onClick: function () {
                if (!props.busy) props.onCancel();
              },
              style: { background: "transparent", border: "none", color: C.text, fontSize: 20, lineHeight: 1, cursor: props.busy ? "not-allowed" : "pointer", padding: "0 4px" },
            },
            "\u00d7"
          )
        ),
        bodyLines.map(function (line, i) {
          return h(
            "p",
            {
              key: i,
              style: {
                fontSize: 13.5,
                lineHeight: 1.55,
                margin: "0 0 10px",
                opacity: 0.9,
              },
            },
            line
          );
        }),
        props.errorNode || null,
        h(
          "div",
          {
            style: {
              display: "flex",
              justifyContent: "flex-end",
              gap: 10,
              flexWrap: "wrap",
              marginTop: 16,
            },
          },
          h(
            Button,
            {
              kind: "secondary",
              buttonRef: cancelRef,
              disabled: props.busy,
              onClick: props.onCancel,
            },
            COPY.cancel
          ),
          h(
            Button,
            {
              kind: "danger",
              disabled: props.busy,
              onClick: props.onConfirm,
            },
            props.busy ? COPY.applying : COPY.applyButton
          )
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // v1.1 Addon↔Addon Conflict Resolution Dialog (t_3a0434b2).
  //
  // Guided, opt-in dialog shown when the user toggles an addon ON and the
  // backend /addons/enable check returns `addon_conflict` (409) — i.e. the
  // target's manifest `conflicts_with` overlaps currently-active addons. It
  // lists the colliding active addons + reasons, and offers exactly two
  // actions: (a) confirm → POST /addons/resolve (one atomic call), or
  // (b) cancel → no API call, no state change. See
  // HAPM_V1_1_CONFLICT_RESOLUTION_POPUP_SPEC.md for the full contract.
  //
  // props:
  //   targetAddonName  string  — display name of the addon being activated
  //   conflicts        array   — [{ name, reason }] colliding ACTIVE addons
  //   busy             bool    — true while the resolve call is in flight
  //   errorNode        node    — inline rollback banner (or null)
  //   onCancel         fn      — close, no API call, no state change
  //   onConfirm        fn      — fire the atomic guided-resolution call
  // ---------------------------------------------------------------------------
  function ConflictDialog(props) {
    var conflicts = props.conflicts || [];
    var titleId = "hapm-conflict-title";
    var dialogRef = React.useRef(null);
    var cancelRef = React.useRef(null);
    var triggerRef = React.useRef(null);

    // Remember what had focus before the dialog opened, and move initial focus
    // to the Cancel (secondary) button so an accidental Enter does not confirm
    // a state-changing action. On unmount, restore focus to the trigger.
    useEffect(function () {
      triggerRef.current =
        (typeof document !== "undefined" && document.activeElement) || null;
      // Focus the Cancel button once rendered.
      if (cancelRef.current) {
        try {
          cancelRef.current.focus();
        } catch (e) {}
      }
      return function () {
        var t = triggerRef.current;
        if (t && typeof t.focus === "function") {
          try {
            t.focus();
          } catch (e) {}
        }
      };
    }, []);

    // Keyboard handling: Esc = Cancel (disabled while busy); Tab/Shift+Tab
    // trap focus within the dialog.
    function onKeyDown(e) {
      if (e.key === "Escape" || e.key === "Esc") {
        if (!props.busy) {
          e.preventDefault();
          props.onCancel();
        }
        return;
      }
      if (e.key !== "Tab") return;
      var root = dialogRef.current;
      if (!root) return;
      var focusable = root.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), ' +
          '[tabindex]:not([tabindex="-1"])'
      );
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      var active = document.activeElement;
      if (e.shiftKey) {
        if (active === first || !root.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    var introText =
      "'" +
      (props.targetAddonName || "") +
      "' conflicts with " +
      conflicts.length +
      " currently active addon(s):";

    return h(
      "div",
      {
        style: {
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.55)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 9999,
          padding: 16,
        },
        // Backdrop click does NOT dismiss — this is a decision dialog.
        onClick: function (e) {
          if (e.target === e.currentTarget) {
            /* intentionally no-op */
          }
        },
        onKeyDown: onKeyDown,
      },
      h(
        "div",
        {
          ref: dialogRef,
          role: "dialog",
          "aria-modal": "true",
          "aria-labelledby": titleId,
          style: {
            background: C.panel,
            color: C.text,
            border: "1px solid " + C.border,
            borderRadius: 12,
            maxWidth: 520,
            width: "100%",
            maxHeight: "85vh",
            display: "flex",
            flexDirection: "column",
            padding: "20px 22px",
            boxShadow: "0 20px 60px rgba(0,0,0,0.45)",
          },
        },
        // Header: title + close ("x") icon (equivalent to Cancel).
        h(
          "div",
          {
            style: {
              display: "flex",
              alignItems: "flex-start",
              justifyContent: "space-between",
              gap: 12,
              marginBottom: 12,
            },
          },
          h(
            "h2",
            {
              id: titleId,
              style: { fontSize: 17, fontWeight: 700, margin: 0 },
            },
            COPY.conflictDialogTitle
          ),
          h(
            "button",
            {
              type: "button",
              "aria-label": COPY.conflictButtonSecondary,
              disabled: !!props.busy,
              onClick: function () {
                if (!props.busy) props.onCancel();
              },
              style: {
                background: "transparent",
                border: "none",
                color: C.text,
                fontSize: 20,
                lineHeight: 1,
                cursor: props.busy ? "not-allowed" : "pointer",
                opacity: props.busy ? 0.4 : 0.7,
                padding: "0 4px",
                fontFamily: "inherit",
              },
            },
            "\u00d7"
          )
        ),
        // Body region 1: framing sentence.
        h(
          "p",
          {
            style: {
              fontSize: 13.5,
              lineHeight: 1.55,
              margin: "0 0 12px",
              opacity: 0.92,
            },
          },
          introText
        ),
        // Body region 2: scrollable conflict list (each row: name + reason).
        h(
          "div",
          {
            style: {
              overflowY: "auto",
              margin: "0 0 14px",
              // keep long lists from growing the modal past a sane fraction
              maxHeight: "38vh",
            },
          },
          conflicts.map(function (c, i) {
            var reasonId = "hapm-conflict-reason-" + i;
            return h(
              "div",
              {
                key: i,
                style: {
                  padding: "10px 12px",
                  border: "1px solid " + C.border,
                  borderRadius: 8,
                  background: "rgba(255,255,255,0.02)",
                  marginBottom: 8,
                },
              },
              h(
                "div",
                {
                  style: { fontSize: 13.5, fontWeight: 700 },
                  "aria-describedby": reasonId,
                },
                c.name
              ),
              h(
                "div",
                {
                  id: reasonId,
                  style: {
                    fontSize: 12.5,
                    opacity: 0.8,
                    marginTop: 3,
                    lineHeight: 1.5,
                    // Safety-relevant: wrap, never truncate.
                    whiteSpace: "normal",
                    wordBreak: "break-word",
                  },
                },
                c.reason
              )
            );
          })
        ),
        // Body region 3: reversibility note (icon + text, not color-only),
        // always present, not dismissible.
        h(
          "div",
          {
            style: {
              display: "flex",
              alignItems: "flex-start",
              gap: 8,
              padding: "10px 12px",
              borderRadius: 8,
              background: C.okBg,
              border: "1px solid " + C.okBorder,
              margin: "0 0 4px",
            },
          },
          h(
            "span",
            { "aria-hidden": "true", style: { fontSize: 14, lineHeight: 1.5 } },
            "\u21ba"
          ),
          h(
            "span",
            { style: { fontSize: 12.5, lineHeight: 1.5, opacity: 0.95 } },
            COPY.conflictReversibilityNote
          )
        ),
        // Inline rollback / failure banner (appears above footer on error).
        props.errorNode
          ? h("div", { style: { marginTop: 12 } }, props.errorNode)
          : null,
        // Footer: either the two actions, or the loading indicator.
        props.busy
          ? h(
              "div",
              {
                style: {
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "flex-end",
                  gap: 10,
                  marginTop: 16,
                },
              },
              h("span", {
                "aria-hidden": "true",
                style: {
                  width: 16,
                  height: 16,
                  borderRadius: "50%",
                  border: "2px solid " + C.border,
                  borderTopColor: C.accent,
                  display: "inline-block",
                  animation: "hapm-spin 0.8s linear infinite",
                },
              }),
              h(
                "span",
                {
                  role: "status",
                  style: { fontSize: 13, opacity: 0.85 },
                },
                COPY.conflictLoading
              )
            )
          : h(
              "div",
              {
                className: "hapm-conflict-footer",
                style: {
                  display: "flex",
                  justifyContent: "flex-end",
                  gap: 10,
                  marginTop: 16,
                  flexWrap: "wrap",
                },
              },
              h(
                Button,
                {
                  kind: "secondary",
                  buttonRef: cancelRef,
                  onClick: props.onCancel,
                },
                COPY.conflictButtonSecondary
              ),
              h(
                Button,
                {
                  kind: "primary",
                  onClick: props.onConfirm,
                },
                COPY.conflictButtonPrimary
              )
            )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Left panel — Profilauswahl (§3).
  // ---------------------------------------------------------------------------
  function ProfileList(props) {
    // props: profiles, statuses, selected, onSelect, loading, error, onRetry
    if (props.loading) {
      return h(
        "div",
        { style: { padding: 8 } },
        [0, 1, 2, 3].map(function (i) {
          return h("div", {
            key: i,
            style: {
              height: 34,
              margin: "0 0 8px",
              borderRadius: 7,
              background: "rgba(255,255,255,0.06)",
            },
          });
        })
      );
    }
    if (props.error) {
      return h(
        "div",
        { style: { padding: 8 } },
        h(
          Banner,
          {
            variant: "warn",
            title: COPY.profilesLoadError,
            actions: [
              h(
                Button,
                { key: "r", kind: "secondary", onClick: props.onRetry },
                COPY.retry
              ),
            ],
          },
          COPY.profilesLoadErrorSub
        )
      );
    }
    if (!props.profiles || props.profiles.length === 0) {
      return h(
        "div",
        { style: { padding: 8 } },
        h(
          Banner,
          { variant: "warn", title: COPY.profilesEmpty },
          COPY.profilesEmptySub
        )
      );
    }
    return h(
      "div",
      { style: { padding: 4 } },
      props.profiles.map(function (p) {
        var isSel = props.selected === p.name;
        var st = props.statuses[p.name];
        var hasPreset = !!(st && st.active_preset);
        var presetLabel = hasPreset ? st.active_preset : COPY.noPresetBadge;
        return h(
          "div",
          {
            key: p.name,
            role: "button",
            tabIndex: 0,
            onClick: function () {
              props.onSelect(p.name);
            },
            onKeyDown: function (e) {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                props.onSelect(p.name);
              }
            },
            style: {
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 8,
              padding: "9px 11px",
              margin: "0 0 6px",
              borderRadius: 8,
              cursor: "pointer",
              background: isSel ? "rgba(110,168,254,0.14)" : "transparent",
              borderLeft: "3px solid " + (isSel ? C.accent : "transparent"),
              transition: "background 0.12s",
            },
          },
          h(
            "span",
            {
              style: {
                fontSize: 13.5,
                fontWeight: isSel ? 600 : 500,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              },
            },
            p.name
          ),
          h(
            "span",
            {
              style: {
                fontSize: 11,
                fontWeight: 600,
                padding: "2px 8px",
                borderRadius: 999,
                whiteSpace: "nowrap",
                color: hasPreset ? C.text : C.textDim,
                background: hasPreset
                  ? "rgba(110,168,254,0.18)"
                  : "rgba(255,255,255,0.07)",
              },
            },
            presetLabel
          )
        );
      })
    );
  }

  // ---------------------------------------------------------------------------
  // Right panel (a) — Preset section (§4).
  // ---------------------------------------------------------------------------
  function PresetSection(props) {
    // props: profile, status, presets, presetsError, onApplied, onRetryPresets
    var status = props.status;
    var activePreset =
      status && status.active_preset ? status.active_preset : null;

    var initialSlug =
      activePreset ||
      (props.presets && props.presets.length ? props.presets[0].slug : "");
    var selSlugState = useState(initialSlug);
    var selSlug = selSlugState[0];
    var setSelSlug = selSlugState[1];

    // Keep the dropdown selection sensible when the profile / preset list
    // changes underneath us.
    useEffect(
      function () {
        setSelSlug(
          activePreset ||
            (props.presets && props.presets.length ? props.presets[0].slug : "")
        );
      },
      // eslint-disable-next-line
      [props.profile, activePreset, (props.presets || []).length]
    );

    var dialogState = useState(false);
    var dialogOpen = dialogState[0];
    var setDialogOpen = dialogState[1];

    var detailsState = useState(false);
    var detailsOpen = detailsState[0];
    var setDetailsOpen = detailsState[1];

    var busyState = useState(false);
    var busy = busyState[0];
    var setBusy = busyState[1];
    var applyInFlightRef = React.useRef(false);

    var dialogErrState = useState(null);
    var dialogErr = dialogErrState[0];
    var setDialogErr = dialogErrState[1];

    function selectedPresetName() {
      var found = (props.presets || []).filter(function (p) {
        return p.slug === selSlug;
      })[0];
      return found ? found.name : selSlug;
    }

    function openConfirm() {
      setDialogErr(null);
      setDialogOpen(true);
    }

    function applyErrorNode(err) {
      if (isNetworkFailure(err)) {
        return h(
          Banner,
          { variant: "danger", title: COPY.repoUnreachableTitle },
          COPY.repoUnreachableBody
        );
      }
      var msg =
        (err.body && (err.body.message || err.body.error)) ||
        COPY.applyUnknownError;
      // 403 or an apply_failed message that hints at a permission / read-only
      // problem maps to the "profile not writable" copy (§6.2).
      if (
        err.status === 403 ||
        /permission|writ|read-only|readonly|denied|beschreibbar/i.test(msg)
      ) {
        return h(
          Banner,
          { variant: "danger", title: COPY.notWritableTitle },
          presetNotWritableBody(props.profile)
        );
      }
      return h(
        Banner,
        { variant: "danger", title: COPY.applyFailedTitle },
        msg
      );
    }

    async function doApply() {
      if (applyInFlightRef.current) return;
      applyInFlightRef.current = true;
      setBusy(true);
      setDialogErr(null);
      try {
        var res = await apiPost("/apply", {
          profile: props.profile,
          preset: selSlug,
        });
        setBusy(false);
        setDialogOpen(false);
        props.onApplied(res);
      } catch (err) {
        setBusy(false);
        setDialogErr(applyErrorNode(err));
      } finally {
        applyInFlightRef.current = false;
      }
    }

    var children = [];

    // Preset registry unreachable (§6.1) — blocks the picker.
    if (props.presetsError) {
      if (isNetworkFailure(props.presetsError)) {
        children.push(
          h(
            Banner,
            {
              key: "repoerr",
              variant: "danger",
              title: COPY.repoUnreachableTitle,
              actions: [
                h(
                  Button,
                  { key: "r", kind: "secondary", onClick: props.onRetryPresets },
                  COPY.retry
                ),
              ],
            },
            COPY.repoUnreachableBody
          )
        );
      } else {
        children.push(
          h(
            Banner,
            {
              key: "preseterr",
              variant: "warn",
              title: COPY.presetsLoadError,
              actions: [
                h(
                  Button,
                  { key: "r", kind: "secondary", onClick: props.onRetryPresets },
                  COPY.retry
                ),
              ],
            },
            (props.presetsError.body && props.presetsError.body.message) ||
              COPY.presetsLoadErrorSub
          )
        );
      }
    }

    // Header / empty-state (§4.1 / §4.2).
    if (activePreset) {
      children.push(
        h(
          "div",
          { key: "hdr", style: { marginBottom: 14 } },
          h(
            "div",
            { style: { fontSize: 12, fontWeight: 600, opacity: 0.6 } },
            COPY.presetHeader
          ),
          h(
            "div",
            { style: { fontSize: 18, fontWeight: 700, marginTop: 2 } },
            activePreset
          )
        )
      );
    } else {
      children.push(
        h(
          "div",
          {
            key: "empty",
            style: {
              marginBottom: 14,
              padding: "14px 16px",
              border: "1px dashed " + C.border,
              borderRadius: 10,
              background: "rgba(255,255,255,0.03)",
            },
          },
          h(
            "div",
            { style: { fontSize: 14, fontWeight: 600 } },
            COPY.presetEmpty
          ),
          h(
            "div",
            {
              style: {
                fontSize: 12.5,
                opacity: 0.7,
                marginTop: 4,
                lineHeight: 1.5,
              },
            },
            COPY.presetEmptySub
          )
        )
      );
    }

    // Preset switcher + apply button (§4.1).
    var hasPresets = props.presets && props.presets.length > 0;
    var canApply = !!selSlug && hasPresets && !props.presetsError;
    children.push(
      h(
        "div",
        {
          key: "picker",
          style: {
            display: "flex",
            gap: 10,
            alignItems: "flex-end",
            flexWrap: "wrap",
          },
        },
        h(
          "label",
          {
            style: {
              display: "flex",
              flexDirection: "column",
              gap: 4,
              flex: 1,
              minWidth: 220,
            },
          },
          h(
            "span",
            { style: { fontSize: 12, fontWeight: 600, opacity: 0.7 } },
            COPY.presetPickerLabel
          ),
          h(
            "select",
            {
              value: selSlug,
              disabled: !canApply,
              onChange: function (e) {
                setSelSlug(e.target.value);
              },
              style: {
                fontSize: 13.5,
                padding: "9px 10px",
                borderRadius: 8,
                border: "1px solid " + C.border,
                background: C.bg,
                color: C.text,
                fontFamily: "inherit",
                width: "100%",
              },
            },
            (props.presets || []).map(function (p) {
              var label =
                p.name +
                (p.version ? " · v" + p.version : "") +
                (p.slug === activePreset ? "  ✓" : "");
              return h("option", { key: p.slug, value: p.slug }, label);
            })
          )
        ),
        h(
          Button,
          { kind: "secondary", disabled: !selSlug, onClick: function () { setDetailsOpen(true); } },
          COPY.details
        ),
        h(
          Button,
          { kind: "primary", disabled: !canApply, onClick: openConfirm },
          COPY.applyButton
        )
      )
    );

    // Selected preset description (context under the picker).
    var selDesc = (props.presets || []).filter(function (p) {
      return p.slug === selSlug;
    })[0];
    if (selDesc && selDesc.description) {
      children.push(
        h(
          "div",
          {
            key: "desc",
            style: {
              fontSize: 12.5,
              opacity: 0.7,
              marginTop: 8,
              lineHeight: 1.5,
            },
          },
          selDesc.description
        )
      );
    }

    if (detailsOpen && selDesc) {
      children.push(
        h(DetailDialog, {
          key: "preset-details",
          item: selDesc,
          effects: [COPY.presetDetailEffect],
          availability:
            selDesc.slug === activePreset
              ? COPY.presetDetailActive
              : COPY.presetDetailInactive,
          onClose: function () { setDetailsOpen(false); },
        })
      );
    }

    if (dialogOpen) {
      children.push(
        h(ConfirmDialog, {
          key: "dialog",
          presetName: selectedPresetName(),
          profileName: props.profile,
          busy: busy,
          errorNode: dialogErr,
          onCancel: function () {
            if (!busy) {
              setDialogOpen(false);
              setDialogErr(null);
            }
          },
          onConfirm: doApply,
        })
      );
    }

    return h("div", null, children);
  }

  // ---------------------------------------------------------------------------
  // Small read-only status summary (active-preset line only; the full FR-9
  // status view with per-addon controls is owned by sibling task t_8b337378).
  // ---------------------------------------------------------------------------
  // (StatusSummary was replaced by the fuller §8 StatusView above.)

  // ---------------------------------------------------------------------------
  // §5 Addon section — compatible-addon list with on/off toggle and (for
  // multi-mode addons like YAGNI) a segmented mode control. Wired to the FR-6
  // /addons listing + /addons/enable + /addons/disable endpoints.
  //
  // Backend contract (FR-6):
  //   GET  /addons?target=<profile>&profile=<profile>
  //        -> { target, addons_root, addons: [{ id, name, description, version,
  //             contributes, modes:[{id,name,description,contributes,default}],
  //             compatible_profiles_or_presets, enabled }] }
  //   POST /addons/enable  { profile, addon, target?, mode? }
  //   POST /addons/disable { profile, addon }
  //
  // YAGNI Modus A ("Ponytail") is human-gated (t_f321af09) and NOT implemented:
  // it is rendered as a visibly disabled segment and is NEVER wired to a backend
  // call. The backend manifest does not return it; we append it locally, greyed.
  // ---------------------------------------------------------------------------

  // Addons for which the spec places a disabled future-mode placeholder. Keyed
  // by addon id -> { label, hint }. Purely presentational; never sent to the API.
  var DISABLED_MODE_PLACEHOLDERS = {
    yagni: { id: "ponytail", label: COPY.ponytailLabel, hint: COPY.ponytailDisabledHint },
  };

  function SegmentedControl(props) {
    // props: segments [{key,label,disabled,hint,active}], onSelect(key), busy
    return h(
      "div",
      {
        role: "group",
        "aria-label": COPY.addonModeLabel,
        style: {
          display: "inline-flex",
          border: "1px solid " + C.border,
          borderRadius: 8,
          overflow: "hidden",
        },
      },
      props.segments.map(function (seg, i) {
        var isActive = seg.active;
        return h(
          "button",
          {
            key: seg.key,
            type: "button",
            disabled: !!seg.disabled || props.busy || isActive,
            title: seg.hint || undefined,
            aria: seg.disabled ? "true" : undefined,
            "aria-disabled": seg.disabled ? "true" : undefined,
            "aria-pressed": isActive ? "true" : "false",
            onClick: function () {
              if (seg.disabled || props.busy || isActive) return;
              props.onSelect(seg.key);
            },
            style: {
              fontSize: 12.5,
              fontWeight: 600,
              padding: "6px 12px",
              border: "none",
              borderLeft: i === 0 ? "none" : "1px solid " + C.border,
              cursor: seg.disabled
                ? "not-allowed"
                : isActive || props.busy
                ? "default"
                : "pointer",
              background: isActive ? C.accent : "transparent",
              color: isActive
                ? "#0b1220"
                : seg.disabled
                ? C.textDim
                : C.text,
              opacity: seg.disabled ? 0.5 : 1,
              fontFamily: "inherit",
            },
          },
          seg.label
        );
      })
    );
  }

  function AddonRow(props) {
    // props: addon, profile, target, busy, activeMode, onEnable(addon, modeId),
    //        onDisable(addon)
    var addon = props.addon;
    var enabled = !!addon.enabled;
    var modes = addon.modes || [];
    var placeholder = DISABLED_MODE_PLACEHOLDERS[addon.id];
    var detailsState = useState(false);
    var detailsOpen = detailsState[0];
    var setDetailsOpen = detailsState[1];

    function addonEffects() {
      var effects = [];
      var contributes = addon.contributes || {};
      if (contributes.soul_block) effects.push("Adds a reversible SOUL.md behavior block.");
      if (contributes.skills) effects.push("Adds reversible skills to the selected profile.");
      modes.forEach(function (mode) {
        if (mode && mode.description) effects.push((mode.name || mode.id) + ": " + mode.description);
      });
      return effects;
    }

    function addonAvailability() {
      var compatibility = addon.compatible_profiles_or_presets;
      var source = compatibility && compatibility.length
        ? COPY.addonDetailCompatibility + compatibility.join(", ") + "."
        : COPY.addonDetailNoCompatibility;
      var state = enabled
        ? COPY.addonDetailActive
        : props.activePreset
        ? COPY.addonDetailAvailable + " Active preset: " + props.activePreset + "."
        : COPY.addonDetailAvailable + " " + COPY.addonDetailNoPresetAvailable;
      if (props.busy) state += " " + COPY.addonDetailControlsBusy;
      return state + " " + source;
    }

    // "Real" (selectable) non-off modes, e.g. YAGNI's "Prompt".
    var realModes = modes.filter(function (m) {
      return m && m.id && m.id !== "off";
    });
    // Whether this addon has an explicit "off" mode declared in its manifest.
    var hasOffMode = modes.some(function (m) {
      return m && m.id === "off";
    });

    // A "modal" addon is one the user picks a MODE for via a segmented control
    // (rather than a bare on/off switch). That's the case when there's more than
    // one selectable mode, or exactly one selectable mode plus a disabled
    // placeholder segment (e.g. YAGNI: Ponytail[disabled]/Prompt/Aus).
    var isModal = realModes.length > 1 || (realModes.length >= 1 && !!placeholder);

    // Current mode: prefer the live status-provided active mode, else the addon's
    // default mode, else the first real mode.
    var defaultMode =
      (realModes.filter(function (m) {
        return m.default;
      })[0] || realModes[0] || {}).id;
    var activeMode = props.activeMode || defaultMode;

    var children = [
      // Header: name + description. The on/off switch is only shown for simple
      // (non-modal) addons; modal addons are driven by the segmented control
      // below (its "Aus" segment is the off state).
      h(
        "div",
        {
          key: "hdr",
          style: {
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: 12,
          },
        },
        h(
          "div",
          { style: { flex: 1, minWidth: 0 } },
          h(
            "div",
            { style: { fontSize: 14, fontWeight: 600, display: "flex", gap: 7, alignItems: "center", flexWrap: "wrap" } },
            addon.name || addon.id,
            addon.custom
              ? h("span", { style: { fontSize: 11, padding: "2px 6px", borderRadius: 999, background: C.okBg, border: "1px solid " + C.okBorder }, "aria-label": "Custom addon" }, "Custom")
              : null
          ),
          addon.description
            ? h(
                "div",
                {
                  style: {
                    fontSize: 12.5,
                    opacity: 0.7,
                    marginTop: 3,
                    lineHeight: 1.45,
                  },
                },
                addon.description
              )
            : null,
          h(
            Button,
            { kind: "secondary", onClick: function () { setDetailsOpen(true); }, style: { marginTop: 9 } },
            COPY.details
          )
        ),
        // On/off toggle switch (simple addons only).
        !isModal
          ? h(
              "button",
              {
                type: "button",
                role: "switch",
                "aria-checked": enabled ? "true" : "false",
                "aria-label":
                  (addon.name || addon.id) +
                  " " +
                  (enabled ? COPY.addonOn : COPY.addonOff),
                disabled: props.busy,
                onClick: function () {
                  if (props.busy) return;
                  if (enabled) props.onDisable(addon);
                  else props.onEnable(addon, undefined);
                },
                style: {
                  flex: "0 0 auto",
                  width: 52,
                  height: 28,
                  borderRadius: 999,
                  border: "1px solid " + C.border,
                  background: enabled ? C.accent : "rgba(255,255,255,0.10)",
                  position: "relative",
                  cursor: props.busy ? "default" : "pointer",
                  opacity: props.busy ? 0.6 : 1,
                  transition: "background 0.15s",
                  padding: 0,
                },
              },
              h("span", {
                style: {
                  position: "absolute",
                  top: 2,
                  left: enabled ? 26 : 2,
                  width: 22,
                  height: 22,
                  borderRadius: "50%",
                  background: "#fff",
                  transition: "left 0.15s",
                  boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
                },
              })
            )
          : null
      ),
    ];

    // Segmented mode control for modal addons (e.g. YAGNI). Segments are, in
    // order: the disabled placeholder (if any, e.g. "Ponytail"), each real mode
    // (e.g. "Prompt"), and finally "Aus" (off). Selecting a real mode enables
    // the addon in that mode; selecting "Aus" disables it. The placeholder is
    // always disabled and NEVER triggers a backend call (Human Gate).
    if (isModal) {
      var segments = [];
      if (placeholder) {
        segments.push({
          key: "__placeholder__" + placeholder.id,
          label: placeholder.label,
          disabled: true,
          hint: placeholder.hint,
          active: false,
        });
      }
      realModes.forEach(function (m) {
        segments.push({
          key: m.id,
          label: m.name || m.id,
          disabled: false,
          active: enabled && m.id === activeMode,
        });
      });
      // "Aus" / off segment — active when the addon is currently disabled.
      segments.push({
        key: "__off__",
        label: COPY.addonOff,
        disabled: false,
        active: !enabled,
      });

      children.push(
        h(
          "div",
          {
            key: "modes",
            style: {
              marginTop: 12,
              display: "flex",
              alignItems: "center",
              gap: 10,
              flexWrap: "wrap",
            },
          },
          h(
            "span",
            { style: { fontSize: 12, fontWeight: 600, opacity: 0.7 } },
            COPY.addonModeLabel
          ),
          h(SegmentedControl, {
            segments: segments,
            busy: props.busy,
            onSelect: function (key) {
              if (props.busy) return;
              if (key === "__off__") {
                if (enabled) props.onDisable(addon);
                return;
              }
              // Real mode selected -> enable (or re-enable with new mode).
              props.onEnable(addon, key);
            },
          }),
          placeholder
            ? h(
                "span",
                {
                  style: {
                    fontSize: 11.5,
                    opacity: 0.6,
                    fontStyle: "italic",
                  },
                },
                placeholder.hint
              )
            : null
        )
      );
    }

if (detailsOpen) {
      children.push(
        h(DetailDialog, {
          key: "addon-details",
          item: addon,
          effects: addonEffects(),
          availability: addonAvailability(),
          onClose: function () { setDetailsOpen(false); },
        })
      );
    }

    if (addon.custom) {
      children.push(
        h(
          "div",
          { key: "custom-actions", style: { marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" } },
          h(Button, { kind: "secondary", onClick: function () { window.location.assign(API + "/custom-addons/" + encodeURIComponent(addon.id) + "/download"); } }, "Download ZIP")
        )
      );
    }

    return h(
      "div",
      {
        style: {
          padding: "14px 14px",
          border: "1px solid " + C.border,
          borderRadius: 10,
          background: "rgba(255,255,255,0.02)",
          marginBottom: 10,
        },
      },
      children
    );
  }

  function AddonSection(props) {
    // props: profile, target, activePreset, addons, loading, error, errorNode, busyAddonId,
    //        activeModes (id->mode), onEnable, onDisable, onRetry
    var activePreset = props.activePreset || null;
    var children = [
      h(
        "div",
        {
          key: "hdr",
          style: {
            fontSize: 13,
            fontWeight: 700,
            marginBottom: 4,
            marginTop: 4,
          },
        },
        COPY.addonsHeader
      ),
      h(
        "div",
        {
          key: "intro",
          style: {
            fontSize: 12.5,
            opacity: 0.7,
            marginBottom: 12,
            lineHeight: 1.5,
          },
        },
        COPY.addonsIntro
      ),
    ];

    // Toggle error banner (incompatibility / conflict / generic).
    if (props.errorNode) {
      children.push(h("div", { key: "toggleerr" }, props.errorNode));
    }

    if (props.loading) {
      children.push(
        h(
          "div",
          {
            key: "loading",
            style: { fontSize: 13, opacity: 0.6, padding: "8px 2px" },
          },
          COPY.addonsLoading
        )
      );
    } else if (props.error) {
      if (isNetworkFailure(props.error)) {
        children.push(
          h(
            Banner,
            {
              key: "neterr",
              variant: "danger",
              title: COPY.repoUnreachableTitle,
              actions: [
                h(
                  Button,
                  { key: "r", kind: "secondary", onClick: props.onRetry },
                  COPY.retry
                ),
              ],
            },
            COPY.repoUnreachableBody
          )
        );
      } else {
        children.push(
          h(
            Banner,
            {
              key: "loaderr",
              variant: "warn",
              title: COPY.addonsLoadError,
              actions: [
                h(
                  Button,
                  { key: "r", kind: "secondary", onClick: props.onRetry },
                  COPY.retry
                ),
              ],
            },
            (props.error.body && props.error.body.message) ||
              COPY.addonsLoadErrorSub
          )
        );
      }
    } else if (!props.addons || props.addons.length === 0) {
      children.push(
        h(
          "div",
          {
            key: "empty",
            style: {
              padding: "14px 16px",
              border: "1px dashed " + C.border,
              borderRadius: 10,
              background: "rgba(255,255,255,0.03)",
            },
          },
          h(
            "div",
            { style: { fontSize: 13.5, fontWeight: 600 } },
            COPY.addonsEmpty
          ),
          h(
            "div",
            { style: { fontSize: 12.5, opacity: 0.7, marginTop: 4 } },
            COPY.addonsEmptySub
          )
        )
      );
    } else {
      props.addons.forEach(function (addon) {
        children.push(
          h(AddonRow, {
            key: addon.id,
            addon: addon,
            profile: props.profile,
            target: props.target,
            activePreset: activePreset,
            activeMode: props.activeModes ? props.activeModes[addon.id] : null,
            busy: props.busyAddonId === addon.id,
            onEnable: props.onEnable,
            onDisable: props.onDisable,
          })
        );
      });
    }

    return h(
      "div",
      {
        style: {
          marginTop: 22,
          paddingTop: 18,
          borderTop: "1px solid " + C.border,
        },
      },
      children
    );
  }

  // ---------------------------------------------------------------------------
  // §8 Status view (FR-9) — active preset + all active addons (with mode).
  // Refreshed after every apply/toggle (the parent re-fetches status).
  // ---------------------------------------------------------------------------
  function StatusView(props) {
    var status = props.status;
    var activePreset =
      status && status.active_preset ? status.active_preset : null;
    var addons = (status && status.addons) || [];
    var detailState = useState(null);
    var detail = detailState[0];
    var setDetail = detailState[1];

    function presetDetails() {
      var preset = (props.presets || []).filter(function (p) {
        return p && p.slug === activePreset;
      })[0] || { id: activePreset, name: activePreset };
      setDetail({
        item: preset,
        effects: [COPY.presetDetailEffect],
        availability: COPY.presetDetailActive,
      });
    }

    function addonDetails(statusAddon) {
      var addon = (props.addons || []).filter(function (candidate) {
        return candidate && candidate.id === statusAddon.addon_id;
      })[0] || { id: statusAddon.addon_id, name: statusAddon.addon_id };
      var effects = [];
      var contributes = addon.contributes || {};
      if (contributes.soul_block) effects.push("Adds a reversible SOUL.md behavior block.");
      if (contributes.skills) effects.push("Adds reversible skills to the selected profile.");
      (addon.modes || []).forEach(function (mode) {
        if (mode && mode.description) effects.push((mode.name || mode.id) + ": " + mode.description);
      });
      var compatibility = addon.compatible_profiles_or_presets;
      var availability = COPY.addonDetailActive +
        (statusAddon.mode ? " " + COPY.statusModePrefix + statusAddon.mode + "." : "") +
        " " +
        (compatibility && compatibility.length
          ? COPY.addonDetailCompatibility + compatibility.join(", ") + "."
          : COPY.addonDetailNoCompatibility);
      setDetail({ item: addon, effects: effects, availability: availability });
    }

    return h(
      "div",
      {
        style: {
          marginTop: 22,
          paddingTop: 18,
          borderTop: "1px solid " + C.border,
        },
      },
      h(
        "div",
        {
          style: { fontSize: 13, fontWeight: 700, marginBottom: 10 },
        },
        COPY.statusViewHeader + " · " + props.profile
      ),
      // Active preset line.
      h(
        "div",
        { style: { marginBottom: 12 } },
        h(
          "div",
          { style: { fontSize: 11.5, fontWeight: 600, opacity: 0.6 } },
          COPY.statusActivePresetLabel
        ),
        h(
          "div",
          { style: { display: "flex", alignItems: "center", gap: 8, marginTop: 2 } },
          h(
            "div",
            {
              style: {
                fontSize: 14,
                fontWeight: 600,
                opacity: activePreset ? 1 : 0.6,
              },
            },
            activePreset || COPY.statusNoPreset
          ),
          activePreset
            ? h(
                Button,
                { key: "status-preset-details", kind: "secondary", onClick: presetDetails },
                COPY.details
              )
            : null
        )
      ),
      // Active addons list.
      h(
        "div",
        null,
        h(
          "div",
          { style: { fontSize: 11.5, fontWeight: 600, opacity: 0.6 } },
          COPY.statusActiveAddonsLabel
        ),
        addons.length === 0
          ? h(
              "div",
              {
                style: {
                  fontSize: 13,
                  opacity: 0.6,
                  marginTop: 4,
                },
              },
              COPY.statusNoAddons
            )
          : h(
              "div",
              { style: { marginTop: 6, display: "flex", flexWrap: "wrap", gap: 8 } },
              addons.map(function (a, i) {
                var mode = a.mode;
                return h(
                  "div",
                  {
                    key: (a.addon_id || "addon") + "_" + i,
                    style: { display: "flex", alignItems: "center", gap: 6 },
                  },
                  h(
                    "div",
                    {
                      style: {
                        fontSize: 12.5,
                        fontWeight: 600,
                        padding: "5px 11px",
                        borderRadius: 999,
                        background: "rgba(110,168,254,0.16)",
                        border: "1px solid " + C.okBorder,
                      },
                    },
                    (a.addon_id || "—") +
                      (mode ? "  ·  " + COPY.statusModePrefix + mode : "")
                  ),
                  h(
                    Button,
                    { key: "status-addon-details", kind: "secondary", onClick: function () { addonDetails(a); } },
                    COPY.details
                  )
                );
              })
            )
      ),
      detail
        ? h(DetailDialog, {
            key: "status-details-dialog",
            item: detail.item,
            effects: detail.effects,
            availability: detail.availability,
            onClose: function () { setDetail(null); },
          })
        : null
    );
  }

  // ---------------------------------------------------------------------------
  // Root page.
  // ---------------------------------------------------------------------------
  function HapmPage() {
    var profilesState = useState([]);
    var profiles = profilesState[0];
    var setProfiles = profilesState[1];

    var profilesLoadingState = useState(true);
    var profilesLoading = profilesLoadingState[0];
    var setProfilesLoading = profilesLoadingState[1];

    var profilesErrState = useState(null);
    var profilesErr = profilesErrState[0];
    var setProfilesErr = profilesErrState[1];

    var selectedState = useState(null);
    var selected = selectedState[0];
    var setSelected = selectedState[1];

    var statusesState = useState({}); // name -> status object
    var statuses = statusesState[0];
    var setStatuses = statusesState[1];

    var presetsState = useState([]);
    var presets = presetsState[0];
    var setPresets = presetsState[1];

    var presetsErrState = useState(null);
    var presetsErr = presetsErrState[0];
    var setPresetsErr = presetsErrState[1];

    // Restart notice §7.1 — backend routes not mounted (network failure right
    // after install → routes unreachable).
    var restartHardState = useState(false);
    var restartHard = restartHardState[0];
    var setRestartHard = restartHardState[1];

    // Restart notice §7.2 — transient success toast.
    var toastState = useState(false);
    var toast = toastState[0];
    var setToast = toastState[1];

    // §5 Addon list state (per selected profile).
    var addonsState = useState([]);
    var addons = addonsState[0];
    var setAddons = addonsState[1];

    var addonsLoadingState = useState(false);
    var addonsLoading = addonsLoadingState[0];
    var setAddonsLoading = addonsLoadingState[1];

    var addonsErrState = useState(null);
    var addonsErr = addonsErrState[0];
    var setAddonsErr = addonsErrState[1];

    // Only the newest addon request may update this panel's state.
    var addonRequestSequence = useRef(0);

    // In-flight addon id (disables that row's controls while toggling).
    var busyAddonState = useState(null);
    var busyAddon = busyAddonState[0];
    var setBusyAddon = busyAddonState[1];

    // Addon toggle error banner node (incompatibility / conflict / generic).
    var addonToggleErrState = useState(null);
    var addonToggleErr = addonToggleErrState[0];
    var setAddonToggleErr = addonToggleErrState[1];

    // v1.1 Addon↔Addon conflict-resolution dialog state (t_3a0434b2).
    // `conflictDialog` is null when closed, else:
    //   { addon, target, mode, conflicts:[{name,reason}] }
    var conflictDialogState = useState(null);
    var conflictDialog = conflictDialogState[0];
    var setConflictDialog = conflictDialogState[1];
    // Whether the guided /addons/resolve call is currently in flight.
    var conflictBusyState = useState(false);
    var conflictBusy = conflictBusyState[0];
    var setConflictBusy = conflictBusyState[1];
    // Inline rollback/error banner node shown *inside* the dialog (or null).
    var conflictErrState = useState(null);
    var conflictErr = conflictErrState[0];
    var setConflictErr = conflictErrState[1];
    // Success snackbar after a resolved conflict: null, or
    //   { target:<name>, deactivated:[<name>,...] }.
    var conflictToastState = useState(null);
    var conflictToast = conflictToastState[0];
    var setConflictToast = conflictToastState[1];

    var fetchStatus = useCallback(function (name) {
      return apiGet("/profiles/" + encodeURIComponent(name) + "/status")
        .then(function (st) {
          setStatuses(function (prev) {
            var next = Object.assign({}, prev);
            next[name] = st;
            return next;
          });
        })
        .catch(function () {
          // Per-profile status failure is non-fatal — leave the badge as
          // "Kein Preset". (Status route may not be mounted pre-restart.)
        });
    }, []);

    var loadProfiles = useCallback(
      function () {
        setProfilesLoading(true);
        setProfilesErr(null);
        return apiGet("/profiles")
          .then(function (data) {
            var list = (data && data.profiles) || [];
            setProfiles(list);
            setProfilesLoading(false);
            setRestartHard(false);
            if (list.length > 0) {
              setSelected(function (cur) {
                return cur || list[0].name;
              });
              list.forEach(function (p) {
                fetchStatus(p.name);
              });
            }
          })
          .catch(function (err) {
            setProfilesLoading(false);
            if (isNetworkFailure(err)) {
              // Routes unreachable → most likely not mounted post-install.
              setRestartHard(true);
            } else {
              setProfilesErr(err);
            }
          });
      },
      [fetchStatus]
    );

    var loadPresets = useCallback(function () {
      setPresetsErr(null);
      return apiGet("/presets")
        .then(function (data) {
          setPresets((data && data.presets) || []);
        })
        .catch(function (err) {
          setPresetsErr(err);
        });
    }, []);

    var loadAddons = useCallback(function (profileName) {
      var requestSequence = ++addonRequestSequence.current;
      if (!profileName) {
        setAddons([]);
        setAddonsLoading(false);
        setAddonsErr(null);
        return Promise.resolve();
      }
      setAddonsLoading(true);
      setAddonsErr(null);
      var q =
        "/addons?target=" +
        encodeURIComponent(profileName) +
        "&profile=" +
        encodeURIComponent(profileName);
      return apiGet(q)
        .then(function (data) {
          if (requestSequence !== addonRequestSequence.current) return;
          setAddons((data && data.addons) || []);
          setAddonsLoading(false);
        })
        .catch(function (err) {
          if (requestSequence !== addonRequestSequence.current) return;
          setAddonsLoading(false);
          setAddons([]);
          setAddonsErr(err);
        });
    }, []);

    useEffect(
      function () {
        loadProfiles();
        loadPresets();
      },
      [loadProfiles, loadPresets]
    );

    // (Re)load the addon list whenever the selected profile changes.
    useEffect(
      function () {
        if (selected) loadAddons(selected);
      },
      [selected, loadAddons]
    );

    var onApplied = useCallback(
      function () {
        // Refresh the selected profile's status + addon list and show the soft
        // toast (§7.2). Applying a preset can change addon compatibility.
        if (selected) {
          fetchStatus(selected);
          loadAddons(selected);
        }
        setToast(true);
        setTimeout(function () {
          setToast(false);
        }, 7000);
      },
      [selected, fetchStatus, loadAddons]
    );

    // Map an addon id to its human-readable display name using the loaded
    // addon list; falls back to the id itself if it isn't in the list (e.g. a
    // colliding addon that isn't shown as a compatible row). Used to render
    // conflicting-addon names + the success toast (the backend conflict object
    // carries ids, not display names).
    var addonDisplayName = useCallback(
      function (addonId) {
        if (!addonId) return "";
        for (var i = 0; i < addons.length; i++) {
          if (addons[i] && addons[i].id === addonId) {
            return addons[i].name || addonId;
          }
        }
        return addonId;
      },
      [addons]
    );

    // §5 addon enable handler. `modeId` is passed for modal addons (e.g. YAGNI).
    var onEnableAddon = useCallback(
      function (addon, modeId) {
        if (!selected) return;
        setBusyAddon(addon.id);
        setAddonToggleErr(null);
        var payload = { profile: selected, addon: addon.id, target: selected };
        if (modeId) payload.mode = modeId;
        apiPost("/addons/enable", payload)
          .then(function () {
            setBusyAddon(null);
            // Refresh from the source of truth after the toggle (§5 AC).
            fetchStatus(selected);
            loadAddons(selected);
            setToast(true);
            setTimeout(function () {
              setToast(false);
            }, 7000);
          })
          .catch(function (err) {
            setBusyAddon(null);
            // v1.1: Addon↔Addon `conflicts_with` collision → open the guided
            // resolution dialog instead of the flat error banner. Every other
            // error (not_compatible, SOUL `conflict`, network "check
            // unavailable", generic) keeps its existing v1 flat-error path.
            var code = err && err.body && err.body.error;
            var conflictObj = err && err.body && err.body.conflict;
            if (code === "addon_conflict" && conflictObj) {
              var rows = (conflictObj.conflicts || []).map(function (c) {
                return {
                  name: addonDisplayName(c.addon_id),
                  reason:
                    (c.reason && String(c.reason)) ||
                    COPY.conflictReasonFallbackPrefix +
                      "'" +
                      (addon.name || addon.id) +
                      "'.",
                };
              });
              setConflictErr(null);
              setConflictBusy(false);
              setConflictDialog({
                addon: addon,
                target: selected,
                mode: modeId || null,
                conflicts: rows,
              });
              return;
            }
            setAddonToggleErr(addonToggleErrorNode(err, addon, selected));
          });
      },
      [selected, fetchStatus, loadAddons, addonDisplayName]
    );

    // v1.1: confirmed guided resolution. Fires exactly ONE atomic call to
    // /addons/resolve (deactivate colliding addons + activate target). On
    // success the dialog auto-closes and a success toast is shown; on
    // failure/rollback the dialog stays open with the inline rollback banner
    // and the two buttons are restored for retry.
    var onConfirmConflict = useCallback(
      function () {
        if (!conflictDialog || !selected) return;
        var addon = conflictDialog.addon;
        setConflictBusy(true);
        setConflictErr(null);
        var payload = {
          profile: selected,
          addon: addon.id,
          target: conflictDialog.target || selected,
        };
        if (conflictDialog.mode) payload.mode = conflictDialog.mode;
        apiPost("/addons/resolve", payload)
          .then(function (res) {
            setConflictBusy(false);
            setConflictDialog(null);
            // Success toast (§Copy Strings toast.success).
            var deactivated =
              (res && res.disabled && res.disabled.length
                ? res.disabled
                : conflictDialog.conflicts.map(function (c) {
                    return c.name;
                  })
              ).map(function (d) {
                return addonDisplayName(d);
              });
            setConflictToast({
              target: addon.name || addon.id,
              deactivated: deactivated,
            });
            setTimeout(function () {
              setConflictToast(null);
            }, 5000);
            // Re-render addon list + status from the source of truth.
            fetchStatus(selected);
            loadAddons(selected);
          })
          .catch(function () {
            // Atomic backend guarantees nothing was applied on failure.
            setConflictBusy(false);
            setConflictErr(
              h(
                Banner,
                { variant: "danger" },
                COPY.conflictRollbackError
              )
            );
          });
      },
      [conflictDialog, selected, fetchStatus, loadAddons, addonDisplayName]
    );

    // v1.1: cancel — close the dialog, no API call, no state change.
    var onCancelConflict = useCallback(function () {
      setConflictDialog(null);
      setConflictErr(null);
      setConflictBusy(false);
    }, []);

    // §5 addon disable handler.
    var onDisableAddon = useCallback(
      function (addon) {
        if (!selected) return;
        setBusyAddon(addon.id);
        setAddonToggleErr(null);
        apiPost("/addons/disable", { profile: selected, addon: addon.id })
          .then(function () {
            setBusyAddon(null);
            fetchStatus(selected);
            loadAddons(selected);
            setToast(true);
            setTimeout(function () {
              setToast(false);
            }, 7000);
          })
          .catch(function (err) {
            setBusyAddon(null);
            setAddonToggleErr(addonToggleErrorNode(err, addon, selected));
          });
      },
      [selected, fetchStatus, loadAddons]
    );

    var selectedStatus = selected ? statuses[selected] : null;

    // Map active addon -> its current mode (from live status) so the segmented
    // control reflects reality after a refresh.
    var activeModes = {};
    if (selectedStatus && selectedStatus.addons) {
      selectedStatus.addons.forEach(function (a) {
        if (a && a.addon_id) activeModes[a.addon_id] = a.mode;
      });
    }

    return h(
      "div",
      {
        style: {
          padding: "20px 24px",
          fontFamily: "inherit",
          color: C.text,
          maxWidth: 1200,
          margin: "0 auto",
        },
      },
      h(
        "h1",
        { style: { fontSize: 20, fontWeight: 700, margin: "0 0 4px" } },
        COPY.tabTitle
      ),
      h(
        "p",
        {
          style: {
            fontSize: 13,
            opacity: 0.7,
            margin: "0 0 18px",
            lineHeight: 1.5,
          },
        },
        COPY.tabIntro
      ),

      // §7.1 hard restart notice.
      restartHard
        ? h(
            Banner,
            { variant: "warn", title: COPY.restartHardTitle },
            COPY.restartHardBody
          )
        : null,

      // §7.2 soft success toast.
      toast ? h(Banner, { variant: "info" }, COPY.restartSoftBody) : null,

      // v1.1 conflict-resolution success snackbar (§Copy Strings toast.success).
      conflictToast
        ? h(
            Banner,
            { variant: "info" },
            "'" +
              conflictToast.target +
              "' is now active. Deactivated: " +
              (conflictToast.deactivated || []).join(", ") +
              "."
          )
        : null,

      // Two-column layout (§2). flex-wrap gives the responsive collapse.
      h(
        "div",
        {
          style: {
            display: "flex",
            gap: 20,
            alignItems: "flex-start",
            flexWrap: "wrap",
          },
        },
        // Left panel (§3).
        h(
          "div",
          {
            style: {
              flex: "0 0 300px",
              minWidth: 240,
              background: C.panel,
              border: "1px solid " + C.border,
              borderRadius: 12,
              padding: 10,
              alignSelf: "stretch",
            },
          },
          h(
            "div",
            {
              style: {
                fontSize: 12,
                fontWeight: 700,
                opacity: 0.6,
                textTransform: "uppercase",
                letterSpacing: 0.4,
                padding: "4px 8px 10px",
              },
            },
            COPY.profilesHeader
          ),
          h(ProfileList, {
            profiles: profiles,
            statuses: statuses,
            selected: selected,
            onSelect: setSelected,
            loading: profilesLoading,
            error: profilesErr,
            onRetry: loadProfiles,
          })
        ),

        // Right panel.
        h(
          "div",
          {
            style: {
              flex: "1 1 480px",
              minWidth: 300,
              background: C.panel,
              border: "1px solid " + C.border,
              borderRadius: 12,
              padding: "18px 20px",
            },
          },
          selected
            ? h(
                React.Fragment,
                null,
                h(PresetSection, {
                  profile: selected,
                  status: selectedStatus,
                  presets: presets,
                  presetsError: presetsErr,
                  onApplied: onApplied,
                  onRetryPresets: loadPresets,
                }),
                h(AddonSection, {
                  profile: selected,
                  target: selected,
                  activePreset: selectedStatus && selectedStatus.active_preset,
                  addons: addons,
                  loading: addonsLoading,
                  error: addonsErr,
                  errorNode: addonToggleErr,
                  busyAddonId: busyAddon,
                  activeModes: activeModes,
                  onEnable: onEnableAddon,
                  onDisable: onDisableAddon,
                  onRetry: function () {
                    if (selected) loadAddons(selected);
                  },
                }),
                h(StatusView, {
                  profile: selected,
                  status: selectedStatus,
                  presets: presets,
                  addons: addons,
                })
              )
            : h(
                "div",
                { style: { fontSize: 13, opacity: 0.6, padding: "8px 2px" } },
                restartHard ? COPY.restartHardBody : COPY.chooseProfile
              )
        )
      ),

      // v1.1 Addon↔Addon conflict-resolution dialog (t_3a0434b2). Rendered
      // last so it overlays the whole tab; null when there's no active conflict.
      conflictDialog
        ? h(ConflictDialog, {
            targetAddonName:
              (conflictDialog.addon &&
                (conflictDialog.addon.name || conflictDialog.addon.id)) ||
              "",
            conflicts: conflictDialog.conflicts,
            busy: conflictBusy,
            errorNode: conflictErr,
            onCancel: onCancelConflict,
            onConfirm: onConfirmConflict,
          })
        : null
    );
  }

  window.__HERMES_PLUGINS__.register("hapm", HapmPage);
})();
