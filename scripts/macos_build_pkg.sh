#!/usr/bin/env bash
# Build inside a Linux container on macOS and produce a Slackware-style .txz
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
mkdir -p "$DIST_DIR"

# Base image can be overridden; default to Ubuntu for easy deps
BASE_IMAGE="${BASE_IMAGE:-ubuntu:22.04}"

cat >"$ROOT_DIR/scripts/_container_build.sh" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  build-essential pkg-config \
  libdrm-dev libgbm-dev libegl1-mesa-dev libgles2-mesa-dev \
  libmpv-dev libvterm-dev libfreetype6-dev libfontconfig1-dev \
  ca-certificates

cd /work
make

# Stage package root
PKGROOT=/tmp/kms_mpv_pkgroot
rm -rf "$PKGROOT"
mkdir -p "$PKGROOT/usr/local/bin" "$PKGROOT/usr/local/share/doc/kms_mpv_compositor" "$PKGROOT/install"
install -m0755 ./kms_mpv_compositor "$PKGROOT/usr/local/bin/"
install -m0644 ./README.md "$PKGROOT/usr/local/share/doc/kms_mpv_compositor/"

cat >"$PKGROOT/install/slack-desc" <<SLK
|-----handy-ruler------------------------------------------------------|
kms_mpv_compositor: kms_mpv_compositor - KMS compositor with mpv + PTY panes
kms_mpv_compositor:
kms_mpv_compositor: Single-binary compositor for TTY using DRM/GBM/EGL.
kms_mpv_compositor: Embeds libmpv for video and renders two terminal panes
kms_mpv_compositor: via libvterm, with OSD and flexible configuration.
kms_mpv_compositor:
kms_mpv_compositor: Homepage: n/a
kms_mpv_compositor:
kms_mpv_compositor:
kms_mpv_compositor:
SLK

# Create Slackware-style .txz without makepkg
VER=$(date +%Y.%m.%d)
PKGFILE="/work/dist/kms_mpv_compositor-${VER}-x86_64-1.txz"
tar -C "$PKGROOT" -cJf "$PKGFILE" .
echo "Package created: $PKGFILE"
EOS
chmod +x "$ROOT_DIR/scripts/_container_build.sh"

echo "Launching container $BASE_IMAGE to build Linux binary and .txz..."
docker run --rm -v "$ROOT_DIR":/work "$BASE_IMAGE" /work/scripts/_container_build.sh
echo "Done. See dist/ for the package."

