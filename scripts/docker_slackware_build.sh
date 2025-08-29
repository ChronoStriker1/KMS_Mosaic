#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
mkdir -p "$DIST_DIR"

IMAGE="slackware:15.0"
CONTAINER_NAME="kmsmpv_build_$$"

cat >"$ROOT_DIR/scripts/_slack_build_inside.sh" <<'EOS'
#!/bin/sh
set -e
echo "Using Slackware container. You may need to install deps manually (libdrm, mesa, mpv, libvterm, freetype, fontconfig)."
echo "Attempting to build with existing system libraries..."
cd /work
make || { echo "Build failed. Install missing -devel packages and retry."; exit 1; }
/work/scripts/make_slackpkg.sh || true
EOS
chmod +x "$ROOT_DIR/scripts/_slack_build_inside.sh"

docker run --rm -v "$ROOT_DIR":/work --name "$CONTAINER_NAME" "$IMAGE" /work/scripts/_slack_build_inside.sh || {
  echo "Docker build container run failed. Ensure docker is installed and the slackware image is available."; exit 1;
}

echo "Build complete. Check dist/ for package."

