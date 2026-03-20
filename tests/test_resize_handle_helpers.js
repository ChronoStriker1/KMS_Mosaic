const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const toolPath = path.join(root, "tools", "kms_mosaic_web.py");
const html = execFileSync("python3", [toolPath, "--print-html"], { encoding: "utf8" });

function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`Missing function ${name}`);
  let braceIndex = source.indexOf("{", start);
  let depth = 0;
  let end = braceIndex;
  for (; end < source.length; end += 1) {
    const ch = source[end];
    if (ch === "{") depth += 1;
    if (ch === "}") {
      depth -= 1;
      if (depth === 0) {
        end += 1;
        break;
      }
    }
  }
  return source.slice(start, end);
}

const context = {};
vm.runInNewContext(
  [
    extractFunction(html, "logicalResizeEdgeForSplit"),
    extractFunction(html, "normalizeStudioResizeCorner"),
    extractFunction(html, "studioResizeCornerName"),
  ].join("\n\n"),
  context,
);

assert.equal(
  context.logicalResizeEdgeForSplit(
    "col",
    { x: 0, y: 0, w: 40, h: 100 },
    { x: 0, y: 0, w: 100, h: 100 },
  ),
  "right",
);
assert.equal(
  context.logicalResizeEdgeForSplit(
    "col",
    { x: 60, y: 0, w: 40, h: 100 },
    { x: 0, y: 0, w: 100, h: 100 },
  ),
  "left",
);
assert.equal(
  context.logicalResizeEdgeForSplit(
    "row",
    { x: 0, y: 0, w: 100, h: 40 },
    { x: 0, y: 0, w: 100, h: 100 },
  ),
  "bottom",
);
assert.equal(
  context.logicalResizeEdgeForSplit(
    "row",
    { x: 0, y: 60, w: 100, h: 40 },
    { x: 0, y: 0, w: 100, h: 100 },
  ),
  "top",
);

assert.equal(
  context.studioResizeCornerName({
    colAncestor: { edge: "bottom" },
    rowAncestor: { edge: "right" },
  }),
  "bottom-right",
);

console.log("resize handle helpers ok");
