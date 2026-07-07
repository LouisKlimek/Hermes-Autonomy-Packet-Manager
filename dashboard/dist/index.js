/**
 * HAPM — Hermes Autonomy Packet Manager (dashboard plugin frontend).
 *
 * No build step: a plain IIFE that consumes the Hermes Plugin SDK globals
 * (window.__HERMES_PLUGIN_SDK__) and registers a single sidebar-tab view via
 * window.__HERMES_PLUGINS__.register(...).
 *
 * This build implements HAPM task t_a380191e — the left-hand profile selector
 * and the right-hand "current preset + preset switcher" panel, per the
 * designer's UX spec (HAPM_UX_SPEC.md §2 layout, §3 profile selector, §4 preset
 * section + confirmation dialog, §6.1/§6.2 error states, §7 restart notice).
 *
 * UI language is German (de-DE) per the designer's OQ-4 decision — all
 * user-facing copy below is hardcoded German verbatim from the spec.
 *
 * The addon toggle/mode section (§5) and the full status view (§8) belong to a
 * sibling task (t_8b337378) and are intentionally NOT implemented here; a small
 * read-only "active preset" summary is shown as part of the preset panel so the
 * selected profile's state is visible.
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
  };

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
  function StatusSummary(props) {
    var status = props.status;
    var presetLine =
      status && status.active_preset
        ? status.active_preset
        : COPY.noPresetApplied;
    return h(
      "div",
      {
        style: {
          marginTop: 20,
          paddingTop: 16,
          borderTop: "1px solid " + C.border,
        },
      },
      h(
        "div",
        { style: { fontSize: 13, fontWeight: 700, marginBottom: 6 } },
        COPY.statusHeaderPrefix + props.profile
      ),
      h(
        "div",
        { style: { fontSize: 13, opacity: 0.85 } },
        COPY.activePresetLabel + presetLine
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

    useEffect(
      function () {
        loadProfiles();
        loadPresets();
      },
      [loadProfiles, loadPresets]
    );

    var onApplied = useCallback(
      function () {
        // Refresh the selected profile's status and show the soft toast (§7.2).
        if (selected) fetchStatus(selected);
        setToast(true);
        setTimeout(function () {
          setToast(false);
        }, 7000);
      },
      [selected, fetchStatus]
    );

    var selectedStatus = selected ? statuses[selected] : null;

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
                h(StatusSummary, { profile: selected, status: selectedStatus })
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
