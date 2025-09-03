#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  build-essential pkg-config binutils file binutils-aarch64-linux-gnu \
  libdrm-dev libgbm-dev libegl1-mesa-dev libgles2-mesa-dev \
  libmpv-dev libvterm-dev libfreetype6-dev libfontconfig1-dev \
  ca-certificates

cd /work
# Force a clean rebuild to avoid stale cross-arch artifacts
rm -f ./kms_mosaic || true
make clean || true
make -B

echo "Build machine arch: $(uname -m)"
echo "Built binary info:"
BIN_INFO=$(file -b ./kms_mosaic || true)
echo "$BIN_INFO"
ldd ./kms_mosaic || true

# Optionally strip to reduce size (non-fatal if strip unavailable)
# Optional stripping; default off to preserve symbols for debugging
if [ "${STRIP_BIN:-0}" = "1" ]; then
  if command -v strip >/dev/null 2>&1; then
    # Try native strip first
    strip -s ./kms_mosaic || true
    # If it's an aarch64 binary and native strip failed, try cross-strip
    if echo "$BIN_INFO" | grep -qi 'aarch64'; then
      if command -v aarch64-linux-gnu-strip >/dev/null 2>&1; then
        aarch64-linux-gnu-strip -s ./kms_mosaic || true
      fi
    fi
  fi
else
  echo "Skipping strip to preserve symbols (set STRIP_BIN=1 to enable)."
fi

# Stage package root
PKGROOT=/tmp/kms_mosaic_pkgroot
rm -rf "$PKGROOT"
mkdir -p "$PKGROOT/usr/local/bin" "$PKGROOT/usr/local/share/doc/kms_mosaic" "$PKGROOT/install"
install -m0755 ./kms_mosaic "$PKGROOT/usr/local/bin/kms_mosaic.bin"
install -m0644 ./README.md "$PKGROOT/usr/local/share/doc/kms_mosaic/"

# Collect and bundle non-core shared libraries next to the binary
LIBDIR="$PKGROOT/usr/local/lib/kms_mosaic"
mkdir -p "$LIBDIR"
exclude_lib() {
  case "$(basename "$1")" in
    linux-vdso.so.*|ld-linux*.so*|ld-musl*.so*) return 0 ;;
    libc.so.*|libm.so.*|libdl.so.*|librt.so.*|libpthread.so.*|libgcc_s.so.*|libstdc++.so.*) return 0 ;;
    # Keep system libdrm and libgbm to match kernel/DRM; allow bundling GLVND libs (libEGL/libGLESv2)
    libdrm.so.*|libgbm.so.*) return 0 ;;
    libX11.so.*|libXext.so.*|libxcb.so.*) return 0 ;;
  esac
  return 1
}

bundle_one() {
  local so="$1"
  [ -z "$so" ] && return 0
  [ ! -e "$so" ] && return 0
  if exclude_lib "$so"; then return 0; fi
  local base="$(basename "$so")"
  if [ ! -e "$LIBDIR/$base" ]; then
    cp -L "$so" "$LIBDIR/$base" || true
    # Recurse into this library's deps
    if command -v ldd >/dev/null 2>&1; then
      ldd "$LIBDIR/$base" | while read -r line; do
        dep="$(echo "$line" | awk '/=>/ {print $3} !/=>/ {print $1}')"
        [ "$dep" = "not" ] && dep=""
        bundle_one "$dep"
      done
    fi
  fi
}

if command -v ldd >/dev/null 2>&1; then
  echo "Bundling shared libraries recursively into $LIBDIR"
  ldd ./kms_mosaic | while read -r line; do
    so="$(echo "$line" | awk '/=>/ {print $3} !/=>/ {print $1}')"
    [ "$so" = "not" ] && so=""
    bundle_one "$so"
  done
fi

# Safety: ensure we never ship toolchain libs that might conflict with system drivers
rm -f "$LIBDIR"/libstdc++.so.* "$LIBDIR"/libgcc_s.so.* "$LIBDIR"/libc.so.* "$LIBDIR"/libm.so.* \
      "$LIBDIR"/libpthread.so.* "$LIBDIR"/librt.so.* "$LIBDIR"/libdl.so.* || true

# Create a wrapper to ensure our lib dir is used for dlopen() as well
cat >"$PKGROOT/usr/local/bin/kms_mosaic" <<'WRAP'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LIBDIR="$SCRIPT_DIR/../lib/kms_mosaic"
# Prepend our libdir if critical GL libs are missing system-wide; otherwise append (prefer system)
if ! ldconfig -p 2>/dev/null | grep -qE 'libGLESv2\.so|libEGL\.so'; then
  export LD_LIBRARY_PATH="$LIBDIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
else
  export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}$LIBDIR"
fi
exec "$SCRIPT_DIR/kms_mosaic.bin" "$@"
WRAP
chmod +x "$PKGROOT/usr/local/bin/kms_mosaic"


cat >"$PKGROOT/install/slack-desc" <<SLK
|-----handy-ruler------------------------------------------------------|
kms_mosaic: kms_mosaic - KMS compositor with tiled video + terminal panes
kms_mosaic:
kms_mosaic: Single-binary compositor for TTY using DRM/GBM/EGL.
kms_mosaic: Embeds libmpv for video and libvterm for terminals.
kms_mosaic: Portrait and landscape tiling layouts with runtime controls.
kms_mosaic:
kms_mosaic: Homepage: n/a
kms_mosaic:
kms_mosaic:
kms_mosaic:
SLK

# Create Slackware-style .txz without makepkg
VER=$(date +%Y.%m.%d)
# Detect binary arch from 'file' output for accurate packaging
case "$BIN_INFO" in
  *x86-64*) PKGARCH=x86_64 ;;
  *aarch64*) PKGARCH=aarch64 ;;
  *ARM*) PKGARCH=arm ;;
  *ppc64le*) PKGARCH=ppc64le ;;
  *) PKGARCH=$(uname -m) ;;
esac
PKGFILE="/work/dist/kms_mosaic-${VER}-${PKGARCH}-1.txz"
tar -C "$PKGROOT" -cJf "$PKGFILE" .
echo "Package created: $PKGFILE"
