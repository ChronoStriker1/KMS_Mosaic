#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
PLUGIN_DIR="$ROOT_DIR/unraid-plugin"
STAGE="$(mktemp -d)"
VERSION="2026.03.18"
PLUGIN_NAME="kms.mosaic"
export COPYFILE_DISABLE=1
export COPY_EXTENDED_ATTRIBUTES_DISABLE=1

cleanup() {
  rm -rf "$STAGE"
}
trap cleanup EXIT

mkdir -p "$DIST_DIR"

if [[ ! -f "$DIST_DIR/kms_mosaic-${VERSION}-x86_64-1.txz" ]]; then
  echo "Missing binary package dist/kms_mosaic-${VERSION}-x86_64-1.txz"
  echo "Build it first with scripts/macos_build_pkg.sh"
  exit 1
fi

cp -R "$PLUGIN_DIR/package-root/." "$STAGE/"
install -m 0755 "$ROOT_DIR/tools/kms_mosaic_web.py" "$STAGE/usr/local/bin/kms_mosaic_web.py"

# Normalize ownership and modes so the archive does not carry host-local metadata.
find "$STAGE" -exec chown 0:0 {} \; 2>/dev/null || true
find "$STAGE" -type d -exec chmod 0755 {} \;
find "$STAGE" -type f -exec chmod 0644 {} \;

chmod 0755 \
  "$STAGE/usr/local/bin/kms_mosaic_web.py" \
  "$STAGE/usr/local/emhttp/plugins/kms.mosaic/event/started" \
  "$STAGE/usr/local/emhttp/plugins/kms.mosaic/event/stopping_svcs" \
  "$STAGE/usr/local/emhttp/plugins/kms.mosaic/scripts/kms_mosaic-service"

PLUGIN_BUNDLE="$DIST_DIR/${PLUGIN_NAME}-${VERSION}.tgz"
(
  cd "$STAGE"
  # Archive only usr/... so extraction cannot apply staging-root metadata to /.
  tar --owner=0 --group=0 -czf "$PLUGIN_BUNDLE" usr
)

cp "$PLUGIN_DIR/kms.mosaic.plg" "$DIST_DIR/kms.mosaic.plg"

echo "Created:"
echo "  $PLUGIN_BUNDLE"
echo "  $DIST_DIR/kms.mosaic.plg"
