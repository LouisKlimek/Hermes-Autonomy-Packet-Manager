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
assert.match(source, /type: "search"/);
assert.match(source, /"aria-label": "Search profiles"/);
assert.match(source, /filteredProfiles\.map/);

console.log("responsive profile selector UI contract passed");
