"use strict";

// Behavioral coverage for the dependency-free dashboard IIFE. This harness
// captures its private components with a minimal React hook/element runtime,
// then fires the same handlers used by the Apply confirmation UI.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const sourcePath = path.join(__dirname, "..", "dist", "index.js");
let source = fs.readFileSync(sourcePath, "utf8");
source = source.replace(
  'window.__HERMES_PLUGINS__.register("hapm", HapmPage);',
  "window.__HAPM_CONFIRM_TEST__ = { PresetSection: PresetSection, ConfirmDialog: ConfirmDialog };"
);

function createHarness(fetchImpl) {
  let state = [];
  let refs = [];
  let cursor = 0;
  const React = {
    createElement(type, props, ...children) {
      return { type, props: Object.assign({}, props || {}, { children }) };
    },
    useState(initial) {
      const index = cursor++;
      if (state[index] === undefined) state[index] = initial;
      return [state[index], (value) => { state[index] = value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!refs[index]) refs[index] = { current: initial };
      return refs[index];
    },
    useEffect() {},
    useCallback(callback) { return callback; },
  };
  const sandbox = {
    window: { __HERMES_PLUGIN_SDK__: { React } },
    fetch: fetchImpl,
    console,
    Promise,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(source, sandbox, { filename: sourcePath });
  const components = sandbox.window.__HAPM_CONFIRM_TEST__;
  return {
    components,
    renderPreset(props) {
      cursor = 0;
      return components.PresetSection(props);
    },
    renderDialog(props) {
      cursor = 0;
      return components.ConfirmDialog(props);
    },
  };
}

function walk(node, predicate) {
  if (!node) return null;
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = walk(child, predicate);
      if (found) return found;
    }
    return null;
  }
  if (typeof node !== "object") return null;
  if (predicate(node)) return node;
  return walk(node.props && node.props.children, predicate);
}

function expand(node) {
  if (!node || typeof node !== "object" || typeof node.type !== "function") return node;
  return expand(node.type(node.props));
}

function findTextAction(tree, text) {
  const virtual = walk(tree, (node) => node.props && node.props.children.includes(text));
  assert.ok(virtual, `missing action: ${text}`);
  const rendered = expand(virtual);
  assert.equal(rendered.type, "button");
  return rendered;
}

function dialogFromPreset(tree, harness) {
  const virtual = walk(tree, (node) => node.type === harness.components.ConfirmDialog);
  assert.ok(virtual, "confirmation dialog was not rendered");
  return { props: virtual.props, tree: harness.renderDialog(virtual.props) };
}

const props = {
  profile: "work",
  status: null,
  presets: [{ slug: "safe", name: "Safe", description: "test" }],
  presetsError: null,
  onApplied() {},
  onRetryPresets() {},
};

async function testPreConfirmAndCancelDoNotRequest() {
  let requests = 0;
  const harness = createHarness(() => { requests += 1; return Promise.resolve({ ok: true, json: async () => ({}) }); });
  let tree = harness.renderPreset(props);
  findTextAction(tree, "Apply Preset").props.onClick();
  assert.equal(requests, 0, "opening confirmation must not apply");
  const dialog = dialogFromPreset(harness.renderPreset(props), harness);
  findTextAction(dialog.tree, "Cancel").props.onClick();
  assert.equal(requests, 0, "cancel must not apply");
  assert.equal(walk(harness.renderPreset(props), (node) => node.type === harness.components.ConfirmDialog), null);
}

async function testConfirmIsSingleFlight() {
  let requests = 0;
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const harness = createHarness(() => { requests += 1; return pending; });
  findTextAction(harness.renderPreset(props), "Apply Preset").props.onClick();
  const dialog = dialogFromPreset(harness.renderPreset(props), harness);
  const confirm = findTextAction(dialog.tree, "Apply Preset");
  confirm.props.onClick();
  confirm.props.onClick();
  assert.equal(requests, 1, "confirm may issue exactly one in-flight request");
  const busyDialog = dialogFromPreset(harness.renderPreset(props), harness);
  const dismiss = walk(busyDialog.tree, (node) => node.props && node.props["aria-label"] === "Close");
  assert.equal(dismiss.props.disabled, undefined, "busy dialog must retain a focusable target");
  assert.equal(dismiss.props["aria-disabled"], true);
  resolve({ ok: true, json: async () => ({ status: "applied" }) });
  await Promise.resolve();
  await Promise.resolve();
}

async function testErrorKeepsDialogOpen() {
  const harness = createHarness(() => Promise.resolve({ ok: false, status: 500, json: async () => ({ message: "apply failed" }) }));
  findTextAction(harness.renderPreset(props), "Apply Preset").props.onClick();
  const dialog = dialogFromPreset(harness.renderPreset(props), harness);
  findTextAction(dialog.tree, "Apply Preset").props.onClick();
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  const afterError = dialogFromPreset(harness.renderPreset(props), harness);
  assert.ok(afterError.props.errorNode, "apply errors remain visible in the open dialog");
}

(async () => {
  await testPreConfirmAndCancelDoNotRequest();
  await testConfirmIsSingleFlight();
  await testErrorKeepsDialogOpen();
  console.log("3 behavioral confirmation tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
