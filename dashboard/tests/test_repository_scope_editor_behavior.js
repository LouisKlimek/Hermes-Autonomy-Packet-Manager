"use strict";

// Behavioral coverage for the dependency-free Repository Scope editor bundle.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "dist", "index.js");
let source = fs.readFileSync(sourcePath, "utf8");
source = source.replace(
  'window.__HERMES_PLUGINS__.register("hapm", HapmPage);',
  "window.__HAPM_REPOSITORY_SCOPE_TEST__ = { RepositoryScopeEditor: RepositoryScopeEditor, validateRepositoryScopeRows: validateRepositoryScopeRows, repositoryScopeErrorMessage: repositoryScopeErrorMessage };"
);

function createHarness(fetchImpl) {
  let state = [];
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
    useEffect(effect) { effect(); },
    useRef(initial) { return { current: initial }; },
    useCallback(callback) { return callback; },
  };
  const sandbox = {
    window: { __HERMES_PLUGIN_SDK__: { React } },
    fetch: fetchImpl,
    Promise,
    console,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(source, sandbox, { filename: sourcePath });
  return {
    components: sandbox.window.__HAPM_REPOSITORY_SCOPE_TEST__,
    render() {
      cursor = 0;
      return sandbox.window.__HAPM_REPOSITORY_SCOPE_TEST__.RepositoryScopeEditor();
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

function action(tree, text) {
  const virtual = walk(tree, (node) => node.props && node.props.children.includes(text));
  assert.ok(virtual, `missing action: ${text}`);
  return virtual;
}

async function flush() {
  await new Promise(setImmediate);
  await new Promise(setImmediate);
}

async function testLoadSuccessRendersOneAccessibleRowPerRepository() {
  const harness = createHarness(() => Promise.resolve({
    ok: true,
    json: async () => ({ repositories: ["Acme/One", "Acme/Two"] }),
  }));
  harness.render();
  await flush();
  const tree = harness.render();
  assert.ok(walk(tree, (node) => node.type === "input" && node.props.value === "Acme/One"));
  assert.ok(walk(tree, (node) => node.type === "input" && node.props.value === "Acme/Two"));
  assert.ok(walk(tree, (node) => node.type === "button" && node.props.children.includes("+ Add repository")));
  assert.ok(walk(tree, (node) => node.type === "button" && node.props["aria-label"] === "Remove repository 1"));
}

async function testRouteMismatchErrorIsSpecific() {
  const harness = createHarness(() => Promise.resolve({
    ok: false,
    status: 404,
    json: async () => ({ detail: "Not Found" }),
  }));
  harness.render();
  await flush();
  const tree = harness.render();
  const alert = walk(tree, (node) => node.props && node.props.role === "alert");
  assert.ok(alert, "load failure should be visible");
  assert.match(alert.props.children.join(""), /does not provide Repository Scope/);
}

async function testEditAddAndSaveUsesRowsThenShowsBackendNormalization() {
  const payloads = [];
  const harness = createHarness((url, options) => {
    if (options && options.method === "PUT") {
      payloads.push(JSON.parse(options.body));
      return Promise.resolve({ ok: true, json: async () => ({ repositories: ["Acme/Normalized"] }) });
    }
    return Promise.resolve({ ok: true, json: async () => ({ repositories: ["Acme/One"] }) });
  });
  harness.render();
  await flush();
  let tree = harness.render();
  walk(tree, (node) => node.type === "input" && node.props.value === "Acme/One").props.onChange({ target: { value: "Acme/Edited" } });
  tree = harness.render();
  action(tree, "+ Add repository").props.onClick();
  tree = harness.render();
  walk(tree, (node) => node.type === "input" && node.props.value === "").props.onChange({ target: { value: "Acme/Two" } });
  action(harness.render(), "Update allowed repositories").props.onClick();
  await flush();
  assert.deepEqual(payloads, [{ repositories: ["Acme/Edited", "Acme/Two"] }]);
  assert.ok(walk(harness.render(), (node) => node.type === "input" && node.props.value === "Acme/Normalized"));
}

async function testRemoveExcludesDeletedRowFromSavePayload() {
  const payloads = [];
  const harness = createHarness((url, options) => {
    if (options && options.method === "PUT") {
      payloads.push(JSON.parse(options.body));
      return Promise.resolve({ ok: true, json: async () => ({ repositories: ["Acme/Two"] }) });
    }
    return Promise.resolve({ ok: true, json: async () => ({ repositories: ["Acme/One", "Acme/Two"] }) });
  });
  harness.render();
  await flush();
  let tree = harness.render();
  walk(tree, (node) => node.type === "button" && node.props["aria-label"] === "Remove repository 1").props.onClick();
  action(harness.render(), "Update allowed repositories").props.onClick();
  await flush();
  assert.deepEqual(payloads, [{ repositories: ["Acme/Two"] }]);
}

async function testInvalidRowsNeverIssuePutRequest() {
  let requests = 0;
  const harness = createHarness((url, options) => {
    if (options && options.method === "PUT") requests += 1;
    return Promise.resolve({ ok: true, json: async () => ({ repositories: ["Acme/One"] }) });
  });
  harness.render();
  await flush();
  let tree = harness.render();
  const input = walk(tree, (node) => node.type === "input" && node.props.value === "Acme/One");
  input.props.onChange({ target: { value: "not a repository" } });
  tree = harness.render();
  const save = action(tree, "Update allowed repositories");
  save.props.onClick();
  await flush();
  assert.equal(requests, 0);
  const alert = walk(harness.render(), (node) => node.props && node.props.role === "alert");
  assert.match(alert.props.children.join(""), /owner\/repository/);
}

(async () => {
  await testLoadSuccessRendersOneAccessibleRowPerRepository();
  await testRouteMismatchErrorIsSpecific();
  await testEditAddAndSaveUsesRowsThenShowsBackendNormalization();
  await testRemoveExcludesDeletedRowFromSavePayload();
  await testInvalidRowsNeverIssuePutRequest();
  console.log("5 Repository Scope editor behavior tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
