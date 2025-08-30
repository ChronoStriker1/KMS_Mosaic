#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
mkdir -p "$DIST_DIR"

if [[ ! -x "$ROOT_DIR/kms_mosaic" ]]; then
  echo "Binary not found. Run make first or use scripts/build_unraid.sh"
  exit 1
fi

PKGROOT="/tmp/kms_mosaic-pkgroot"
rm -rf "$PKGROOT"
mkdir -p "$PKGROOT/usr/local/bin" "$PKGROOT/usr/local/share/doc/kms_mosaic"
install -m 0755 "$ROOT_DIR/kms_mosaic" "$PKGROOT/usr/local/bin/"
install -m 0644 "$ROOT_DIR/README.md" "$PKGROOT/usr/local/share/doc/kms_mosaic/"

VER="$(date +%Y.%m.%d)"
PKGFILE="$DIST_DIR/kms_mosaic-${VER}-x86_64-1.txz"
cd "$PKGROOT"
if command -v makepkg >/dev/null 2>&1; then
  makepkg -l y -c n "$PKGFILE"
  echo "Package created: $PKGFILE"
else
  echo "makepkg not found. Please install Slackware pkgtools."
  exit 1
fi
