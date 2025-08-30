KMS Mosaic — Tiled Video + Terminal Panes (KMS/GBM/EGL)

Overview
- Single-binary KMS compositor for the Linux console (no X/Wayland).
- Uses DRM/KMS + GBM + EGL/GLES2 to present directly to the display.
- Embeds libmpv (render API) to draw video; embeds libvterm for terminal panes.
- Dynamically tiles three panes (“mosaic”): Pane C (Video), Pane A (btop), Pane B (syslog tail).

Why this approach
- Only one DRM master can drive a KMS display at a time. This compositor provides
  a video pane + text panes without X/Wayland by compositing all panes in a single
  scanout surface.

Dependencies (build-time)
- libdrm (drm, drm_mode), gbm
- EGL + OpenGL ES 2.0
- libmpv (>= 0.33 with render_gl API)
- libvterm (terminal emulation)
- FreeType + Fontconfig (text rendering)
- pkg-config, a C compiler

On Unraid, install the above in your build container or dev environment.

Build
- make

Run
- Switch to an unused VT/TTY. Stop any services occupying DRM (X/Wayland).
- Binary names:
- `kms_mosaic` (new alias)
  - `kms_mpv_compositor` (kept for compatibility)
- Examples:
- `./kms_mosaic --video /path/to/video.mp4`
- `./kms_mosaic --video /path/to/video.mp4 --connector HDMI-A-1 --mode 1080x1920@60 --rotate 90`
- `./kms_mosaic --no-config --smooth --loop --video-rotate 270 --panscan 1 --portrait-layout 2x1 --video /path/to/movie.mp4`
- `./kms_mosaic --no-video --pane-a "btop" --pane-b "journalctl -f" --font-size 22`
- `./kms_mosaic --playlist-extended mylist.txt --loop-playlist --shuffle`
- `./kms_mosaic --config /path/profile.conf`
- `./kms_mosaic --save-config /path/profile.conf`
- `./kms_mosaic --save-config-default`

Controls
- Tab: switch focus between panes A/B
- n / p: next / previous video
- Space: pause/resume video
- o: toggle OSD on/off
- l / L: cycle layouts forward/back (portrait: 3 modes; landscape: 4 modes)
- t: swap terminal panes A and B
- r / R: rotate roles among (C video, A, B) / reverse
- Ctrl+Q: quit compositor
- OSD: title and status in top-left


Layouts
- Portrait (90/270):
  - stack3: 3 rows in 1 column (Top=C, Middle=A, Bottom=B by default)
  - 2x1: two columns in first row (C | A), second row single column (B)
  - 1x2: one column in first row (C), second row two columns (A | B)
- Landscape (0/180):
  - stack3: 3 rows in 1 column
  - row3: 1 row in 3 columns
  - 2x1: 2 rows in left column, right column full height
  - 1x2: left column full height, right column split into 2 rows
- Pane role assignment (C=video, A, B) is a permutation over the 3 slots and can be rotated/swapped at runtime via r/R/t.

Planned TODOs
- Improve Unicode/box drawing coverage and performance.
- Make connector/mode selection configurable.
- Implement atomic modesetting + nonblocking pageflips.

Notes
- This program becomes DRM master and will blank fbcon while active.
- If you see permission errors, run as root or grant DRM access.

Flags
- --connector ID|NAME: choose output (e.g., 42 or HDMI-A-1, DP-1). Default: first connected.
- --mode WxH[@Hz]: choose resolution/refresh (e.g., 1080x1920@60). Default: preferred.
- --rotate 0|90|180|270: rotate presentation. For portrait HD, use --mode 1080x1920@60 --rotate 90.
- --video PATH: path to media file for the left pane. Optional.
- --video-opt K=V: apply mpv option to the most recent --video (repeatable per item).
- --playlist FILE: load an mpv playlist file (m3u, one path per line).
- --playlist-extended FILE: custom playlist with per-line options (each line: "path | key=val,key=val").
- --no-video: disable the video region and use full width for the text panes.
- --loop-file: loop the current file indefinitely.
- --loop: shorthand for --loop-file (infinite)
- --loop-playlist: loop the playlist indefinitely.
- --shuffle: randomize playlist order (alias: --randomize).
- --mpv-opt K=V: set global mpv option (repeatable), e.g., --mpv-opt keepaspect=yes.
- --font-size PX: terminal font size in pixels (default 18).
- --right-frac PCT: percent of screen width used by right column (10..80, default 33).
- --video-frac PCT: percent of screen width for the video region (overrides --right-frac).
- --pane-split PCT: percent of right column height for top pane (10..90, default 50).
- --pane-a "CMD": shell command for top-right pane (default: btop).
- --pane-b "CMD": shell command for bottom-right pane (default: tail -f /var/log/syslog).
- --list-connectors: print connectors and first 8 modes then exit.
- --config FILE: load flags from a config file (supports quotes, comments with #).
- --save-config FILE: write the current configuration as flags to a file.
- --save-config-default: write the configuration to the default config path.

- --no-config: do not auto-load the default config
- --smooth: balanced playback preset (display-resample, no interp, linear tscale, early-flush, no shader cache)
- --portrait-layout stack3|2x1|1x2: select portrait tiling mode
- --landscape-layout stack3|row3|2x1|1x2: select landscape tiling mode

Default config path
- On Unraid: `/boot/config/kms_mpv_compositor.conf` (persistent across reboots)
- Elsewhere: `$XDG_CONFIG_HOME/kms_mpv_compositor.conf` or `~/.config/kms_mpv_compositor.conf`

Unraid build
- Recommended: build inside a Slackware 15 container, then install the resulting .txz on Unraid.
- Direct host build requires dev toolchain and libraries (libdrm, mesa-gbm/EGL/GLES2, libmpv, libvterm, freetype, fontconfig).

Steps (containerized)
- Ensure Docker is enabled on Unraid.
- From this repo root:
  - scripts/docker_slackware_build.sh  # builds binary and .txz package
  - The package appears under `dist/` as `kms_mpv_compositor-<ver>-x86_64-1.txz`.
  - Install on Unraid host: `installpkg dist/kms_mpv_compositor-<ver>-x86_64-1.txz`

Steps (host build)
- Install dev tools (e.g., via NerdTools/DevTools): `gcc`, `make`, `pkg-config`.
- Install libraries: `libdrm`, `mesa` (EGL/GLES2/gbm), `libmpv`, `libvterm`, `freetype`, `fontconfig`.
- Build: `make`
- Optional: create Slackware package: `scripts/make_slackpkg.sh`

macOS build + package
- Prerequisite: Docker Desktop for Mac installed and running.
- From this repo root:
  - scripts/macos_build_pkg.sh
  - This launches an Ubuntu container, installs build deps, compiles the Linux binary, and creates a Slackware-style `.txz` (without `makepkg`).
  - Result: `dist/kms_mpv_compositor-<date>-x86_64-1.txz`
- Install on Unraid: copy the `.txz` to your server and run `installpkg`.
- Note: If you prefer a Slackware build environment, set `BASE_IMAGE=slackware:15.0` before running and ensure the image exists locally.
