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

  var API = "/api/plugins/hapm";

  // ---------------------------------------------------------------------------
  // German UI copy (verbatim from HAPM_UX_SPEC.md — do not paraphrase).
  // ---------------------------------------------------------------------------
  var COPY = {
    tabTitle: "Autonomy Packet Manager",
    tabIntro:
      "Basis-Preset auf ein Hermes-Profil anwenden und den aktuellen Zustand verwalten.",
    profilesHeader: "Profilauswahl",
    noPresetBadge: "Kein Preset",
    profilesLoadError: "Profile konnten nicht geladen werden.",
    profilesLoadErrorSub:
      "Bitte Seite neu laden. Falls der Fehler bestehen bleibt, prüfe die Berechtigungen von `$HERMES_HOME/profiles/`.",
    profilesEmpty: "Keine Profile gefunden.",
    profilesEmptySub:
      "Es wurden keine Profile unter `$HERMES_HOME/profiles/` gefunden. Prüfe die Hermes-Installation.",
    chooseProfile: "Wähle links ein Profil.",
    retry: "Erneut versuchen",
    presetHeader: "Aktives Preset",
    presetEmpty: "Kein Preset angewendet — wähle ein Template",
    presetEmptySub:
      "Wähle unten ein Preset aus der Liste, um SOUL.md, Skills und Konfiguration dieses Profils zu setzen.",
    presetPickerLabel: "Preset",
    applyButton: "Preset anwenden",
    applying: "Wird angewendet …",
    cancel: "Abbrechen",
    dismiss: "Schließen",
    dialogTitle: "Preset anwenden?",
    statusHeaderPrefix: "Status: ",
    activePresetLabel: "Aktives Preset: ",
    noPresetApplied: "— (kein Preset angewendet)",
    presetsLoadError: "Presets konnten nicht geladen werden",
    presetsLoadErrorSub: "Bitte erneut versuchen.",
    applyFailedTitle: "Preset konnte nicht angewendet werden",
    applyUnknownError: "Unbekannter Fehler beim Anwenden des Presets.",
    // Restart notice §7.1 (post-install, routes not mounted)
    restartHardTitle: "Neustart erforderlich",
    restartHardBody:
      "Der Autonomy Packet Manager wurde installiert oder aktualisiert. Backend-Routen werden nur beim Start von `hermes dashboard` geladen — starte das Dashboard neu, damit alle Funktionen verfügbar sind.",
    // Restart notice §7.2 (post-action soft toast)
    restartSoftBody:
      "Änderung gespeichert. Wirkt gemäß normaler Hermes-Profil-Reload-Semantik; falls das Verhalten nicht wie erwartet greift, starte die betroffene Agenten-Session neu.",
    // Error §6.1 repo unreachable
    repoUnreachableTitle: "Repository nicht erreichbar",
    repoUnreachableBody:
      "Presets und Addons konnten nicht geladen werden, da das Repository `LouisKlimek/Hermes-Autonomy-Packet-Manager` gerade nicht erreichbar ist. Prüfe deine Internetverbindung oder GitHub-Zugangsdaten.",
    // Error §6.2 profile not writable
    notWritableTitle: "Profil nicht beschreibbar",

    // --- §5 Addon section ------------------------------------------------
    addonsHeader: "Addons",
    addonsIntro:
      "Umkehrbare Verhaltens-Addons für dieses Profil. Es werden nur Addons angezeigt, die mit dem aktiven Preset bzw. Profil kompatibel sind.",
    addonsLoading: "Addons werden geladen …",
    addonsEmpty: "Keine kompatiblen Addons für dieses Profil.",
    addonsEmptySub:
      "Für das aktive Preset dieses Profils sind derzeit keine kompatiblen Addons verfügbar.",
    addonsLoadError: "Addons konnten nicht geladen werden",
    addonsLoadErrorSub: "Bitte erneut versuchen.",
    addonOn: "An",
    addonOff: "Aus",
    addonModeLabel: "Modus",
    // Error: addon incompatible (backend `not_compatible`, 409) §6.3
    addonIncompatibleTitle: "Addon nicht kompatibel",
    // Error: addon conflict (backend `conflict`, 409) §6.4
    addonConflictTitle: "Addon-Konflikt",
    addonToggleUnknownError:
      "Das Addon konnte nicht umgeschaltet werden (unbekannter Fehler).",
    // YAGNI Modus A placeholder — Human Gate (t_f321af09): shown, disabled,
    // never wired to a backend call.
    ponytailLabel: "Ponytail",
    ponytailDisabledHint:
      "„Ponytail“ (Modus A) ist noch nicht verfügbar und für dieses Release deaktiviert.",

    // --- §8 Status view --------------------------------------------------
    statusViewHeader: "Aktueller Zustand",
    statusActivePresetLabel: "Aktives Preset",
    statusNoPreset: "Kein Preset angewendet",
    statusActiveAddonsLabel: "Aktive Addons",
    statusNoAddons: "Keine aktiven Addons",
    statusModePrefix: "Modus: ",
  };

  function addonIncompatibleBody(addonName, target) {
    return (
      "Das Addon „" +
      addonName +
      "\" ist nicht mit „" +
      target +
      "\" kompatibel und kann für dieses Profil nicht aktiviert werden. " +
      "Kompatible Addons richten sich nach der Whitelist des jeweiligen Addons."
    );
  }

  function addonConflictBody(addonName) {
    return (
      "Das Addon „" +
      addonName +
      "\" steht im Konflikt mit einem bereits aktiven Addon oder einem vorhandenen SOUL-Block und wurde nicht aktiviert. " +
      "Deaktiviere das kollidierende Addon und versuche es erneut."
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
      "Das Profil „" +
      profile +
      "\" konnte nicht geändert werden — die Dateien sind nicht beschreibbar. Prüfe die Dateiberechtigungen unter `$HERMES_HOME/profiles/" +
      profile +
      "/`."
    );
  }

  function dialogBody(presetName, profileName) {
    return [
      "Das Anwenden von „" +
        presetName +
        "\" überschreibt SOUL.md, Skills und die zulässigen Konfigurationsfelder des Profils „" +
        profileName +
        "\". Der aktuelle Zustand wird vorher automatisch gesichert und kann jederzeit wiederhergestellt werden, solange keine weiteren Änderungen vorgenommen wurden.",
      "Aktive Addons dieses Profils bleiben nach Möglichkeit erhalten, sofern sie mit dem neuen Preset kompatibel sind. Inkompatible Addons werden automatisch deaktiviert.",
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
        disabled: !!props.disabled,
        onClick: props.onClick,
        style: Object.assign(base, props.style || {}),
      },
      props.children
    );
  }

  // ---------------------------------------------------------------------------
  // Confirmation dialog (§4.3) — destructive preset apply.
  // ---------------------------------------------------------------------------
  function ConfirmDialog(props) {
    // props: presetName, profileName, busy, errorNode, onCancel, onConfirm
    var bodyLines = dialogBody(props.presetName, props.profileName);
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
      },
      h(
        "div",
        {
          role: "dialog",
          "aria-modal": "true",
          "aria-label": COPY.dialogTitle,
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
          "h2",
          { style: { fontSize: 17, fontWeight: 700, margin: "0 0 12px" } },
          COPY.dialogTitle
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
              marginTop: 16,
            },
          },
          h(
            Button,
            {
              kind: "secondary",
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

    var busyState = useState(false);
    var busy = busyState[0];
    var setBusy = busyState[1];

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
            { style: { fontSize: 14, fontWeight: 600 } },
            addon.name || addon.id
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
            : null
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
    // props: profile, target, addons, loading, error, errorNode, busyAddonId,
    //        activeModes (id->mode), onEnable, onDisable, onRetry
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
          {
            style: {
              fontSize: 14,
              fontWeight: 600,
              marginTop: 2,
              opacity: activePreset ? 1 : 0.6,
            },
          },
          activePreset || COPY.statusNoPreset
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
                );
              })
            )
      )
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

    // In-flight addon id (disables that row's controls while toggling).
    var busyAddonState = useState(null);
    var busyAddon = busyAddonState[0];
    var setBusyAddon = busyAddonState[1];

    // Addon toggle error banner node (incompatibility / conflict / generic).
    var addonToggleErrState = useState(null);
    var addonToggleErr = addonToggleErrState[0];
    var setAddonToggleErr = addonToggleErrState[1];

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
      if (!profileName) {
        setAddons([]);
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
          setAddons((data && data.addons) || []);
          setAddonsLoading(false);
        })
        .catch(function (err) {
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
            setAddonToggleErr(addonToggleErrorNode(err, addon, selected));
          });
      },
      [selected, fetchStatus, loadAddons]
    );

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
                h(StatusView, { profile: selected, status: selectedStatus })
              )
            : h(
                "div",
                { style: { fontSize: 13, opacity: 0.6, padding: "8px 2px" } },
                restartHard ? COPY.restartHardBody : COPY.chooseProfile
              )
        )
      )
    );
  }

  window.__HERMES_PLUGINS__.register("hapm", HapmPage);
})();
