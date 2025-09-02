#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST_DIR="$SCRIPT_DIR/../shaders/anime4k"
mkdir -p "$DEST_DIR"
BASE_URL="https://raw.githubusercontent.com/bloc97/Anime4K/master/GLSL_Mods"
FILES=(
  "Anime4K_Clamp_Highlights.glsl"
  "Anime4K_Restore_CNN_M.glsl"
  "Anime4K_Upscale_CNN_x2_S.glsl"
  "Anime4K_Auto_Downscale_Pre_x4.glsl"
)
for f in "${FILES[@]}"; do
  echo "Fetching $f"
  curl -fsSL "$BASE_URL/$f" -o "$DEST_DIR/$f"
done
echo "Anime4K shaders downloaded to $DEST_DIR"
