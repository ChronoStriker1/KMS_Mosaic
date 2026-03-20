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

const context = {
  URL,
  URLSearchParams,
  encodeURIComponent,
  decodeURIComponent,
  console,
};

vm.runInNewContext(
  [
    extractFunction(html, "isRemoteMediaUrl"),
    extractFunction(html, "remoteMediaPathInfo"),
    extractFunction(html, "mediaUrl"),
    extractFunction(html, "isLikelyImagePath"),
    extractFunction(html, "isLikelyVideoPath"),
  ].join("\n\n"),
  context,
);

const remoteVideoUrl =
  "https://hydrusapi.cstriker.us/get_files/file?file_id=196226114&Hydrus-Client-API-Access-Key=908ffd8d0cd415215ecf7642e4d9440d9746a22b49a1e72a257943832daf8d67&download=false";

assert.equal(context.mediaUrl(remoteVideoUrl), remoteVideoUrl);
assert.equal(context.isLikelyVideoPath(remoteVideoUrl), true);
assert.equal(context.isLikelyImagePath(remoteVideoUrl), false);
assert.match(
  context.mediaUrl("/mnt/user/media/example.mp4"),
  /^\/api\/media\?path=/,
);

console.log("queue url helpers ok");
