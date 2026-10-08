const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

// Execute the actual browser rendering functions without bootstrapping the DOM.
const source = fs.readFileSync(path.join(__dirname, "../src/reservoir_data_translator/ui/app.js"), "utf8");
const context = vm.createContext({});
for (const [start, end] of [
  ["function escapeHtml(", "function formatBytes("],
  ["function reviewState(", "function renderReview("],
  ["function hasExcludedEvidence(", "function renderResult("],
]) {
  vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end)), context);
}

test("semantic evidence displays its source magnitude and source unit", () => {
  const html = context.renderMapping({
    status: "MAPPED", source_block_id: "block_0001", value: 5.076,
    source_unit: "psi", canonical_unit: "bar", confidence: .98,
    ontology_concept: "physical.capillary_pressure",
    context: {scope: "relative_permeability", phase_system: "oil_water"},
    canonical_path: "scal.relative_permeability[X-12].points[0].pcow",
  }, 0);
  assert.match(html, /5\.076 psi/);
  assert.doesNotMatch(html, /5\.076 bar/);
  assert.match(html, /relative_permeability · oil_water/);
});

test("excluding a table alone keeps the result incomplete", () => {
  assert.equal(context.hasExcludedEvidence({excluded_count: 0, excluded_table_count: 1}), true);
  assert.equal(context.hasExcludedEvidence({excluded_count: 1, excluded_table_count: 0}), true);
  assert.equal(context.hasExcludedEvidence({excluded_count: 0, excluded_table_count: 0}), false);
  assert.equal(context.hasExcludedEvidence(null), false);
});
