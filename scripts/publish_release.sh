#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO="ChronoStriker1/KMS_Mosaic"
VERSION="$(tr -d '[:space:]' < "$ROOT_DIR/VERSION")"
MANIFEST="$ROOT_DIR/unraid-plugin/kms.mosaic.plg"
ASSETS=(
  "$ROOT_DIR/dist/kms_mosaic-${VERSION}-x86_64-1.txz"
  "$ROOT_DIR/dist/kms.mosaic-${VERSION}.tgz"
)

asset_url() {
  echo "https://github.com/${REPO}/releases/download/${VERSION}/$(basename "$1")"
}

if [[ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=no)" ]]; then
  echo "Commit the release first; the tag must point at the source of these packages." >&2
  exit 1
fi

for asset in "${ASSETS[@]}"; do
  if [[ ! -f "$asset" ]]; then
    echo "Missing $asset. Build it with scripts/macos_build_pkg.sh and scripts/build_unraid_plugin.sh." >&2
    exit 1
  fi
  if ! grep -qF "<URL>$(asset_url "$asset")</URL>" "$MANIFEST"; then
    echo "$MANIFEST does not download $(asset_url "$asset")" >&2
    exit 1
  fi
done

notes="$(awk -v heading="###${VERSION}" '$0 == heading {on = 1; next} /^###/ {on = 0} on && NF' "$MANIFEST")"
if [[ -z "$notes" ]]; then
  echo "No CHANGES entry for ${VERSION} in $MANIFEST" >&2
  exit 1
fi

if ! git -C "$ROOT_DIR" rev-parse -q --verify "refs/tags/${VERSION}" >/dev/null; then
  git -C "$ROOT_DIR" tag "$VERSION" HEAD
fi
if [[ "$(git -C "$ROOT_DIR" rev-parse "${VERSION}^{commit}")" != "$(git -C "$ROOT_DIR" rev-parse HEAD)" ]]; then
  echo "Tag ${VERSION} already points at another commit." >&2
  exit 1
fi
git -C "$ROOT_DIR" push origin "refs/tags/${VERSION}"

if gh release view "$VERSION" --repo "$REPO" >/dev/null 2>&1; then
  gh release upload "$VERSION" "${ASSETS[@]}" --repo "$REPO" --clobber
else
  gh release create "$VERSION" "${ASSETS[@]}" --repo "$REPO" --verify-tag \
    --title "KMS Mosaic ${VERSION}" --notes "$notes"
fi

for asset in "${ASSETS[@]}"; do
  published="$(curl -fsSL "$(asset_url "$asset")" | sha256sum | cut -d' ' -f1)"
  built="$(sha256sum "$asset" | cut -d' ' -f1)"
  if [[ "$published" != "$built" ]]; then
    echo "$(asset_url "$asset") does not match $asset" >&2
    exit 1
  fi
  echo "Verified $(asset_url "$asset")"
done

echo "Release ${VERSION} is published. Push main so Unraid sees the new manifest."
