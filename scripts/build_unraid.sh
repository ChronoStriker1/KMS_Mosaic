#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
mkdir -p "$DIST_DIR"

echo "[1/3] Building binary"
make -C "$ROOT_DIR"

echo "[2/3] Staging package root"
PKGROOT="/tmp/kms_mosaic-pkgroot"
rm -rf "$PKGROOT"
mkdir -p "$PKGROOT/usr/local/bin" "$PKGROOT/usr/local/share/doc/kms_mosaic"
install -m 0755 "$ROOT_DIR/kms_mosaic" "$PKGROOT/usr/local/bin/"
install -m 0644 "$ROOT_DIR/README.md" "$PKGROOT/usr/local/share/doc/kms_mosaic/"

echo "[3/3] Building Slackware package (.txz)"
VER="$(date +%Y.%m.%d)"
PKGFILE="$DIST_DIR/kms_mosaic-${VER}-x86_64-1.txz"
cd "$PKGROOT"
if command -v makepkg >/dev/null 2>&1; then
  makepkg -l y -c n "$PKGFILE"
  echo "Package created: $PKGFILE"
  echo "Install with: installpkg $PKGFILE"
else
  echo "makepkg not found. Install pkgtools or run scripts/docker_slackware_build.sh"
  exit 1
fi

