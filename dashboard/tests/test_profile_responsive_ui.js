const assert = require("assert");
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "dist", "index.js"),
  "utf8"
);

assert.match(source, /function MobileProfileSelect\(props\)/);
assert.match(source, /id: "hapm-mobile-profile-select"/);
assert.match(source, /className: "hapm-mobile-profile-select"/);
assert.match(source, /className: "hapm-desktop-profile-panel"/);
assert.match(source, /@media \(max-width:560px\)\{/);
assert.match(source, /\.hapm-desktop-profile-panel\{display:none\}/);
assert.match(source, /\.hapm-mobile-profile-select\{display:block/);
assert.match(
  source,
  /\.hapm-mobile-profile-select select\{background:var\(--hermes-panel, #232428\)!important;color:var\(--hermes-text, #e6e6e6\)!important;border:1px solid var\(--hermes-border, rgba\(255,255,255,0\.10\)\)!important\}/
);
assert.match(
  source,
  /\.hapm-mobile-profile-select select option\{background:var\(--hermes-panel, #232428\)!important;color:var\(--hermes-text, #e6e6e6\)!important\}/
);
assert.match(source, /type: "search"/);
assert.match(source, /"aria-label": "Search profiles"/);
assert.match(source, /filteredProfiles\.map/);

const mobileSelect = source.match(
  /function MobileProfileSelect\(props\) \{[\s\S]*?\n  \}\n\n  \/\/ ---------------------------------------------------------------------------\n  \/\/ Right panel/
);
assert.ok(mobileSelect, "MobileProfileSelect source should be present");
assert.match(mobileSelect[0], /background: C\.panel/);
assert.match(mobileSelect[0], /color: C\.text/);
assert.match(mobileSelect[0], /border: "1px solid " \+ C\.border/);

console.log("responsive profile selector UI contract passed");
