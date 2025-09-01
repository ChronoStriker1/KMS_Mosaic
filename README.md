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
- Binary name: `kms_mosaic`
- Examples:
- `./kms_mosaic --video /path/to/video.mp4`
- `./kms_mosaic --video /path/to/video.mp4 --connector HDMI-A-1 --mode 1080x1920@60 --rotate 90`
- `./kms_mosaic --no-config --smooth --loop --video-rotate 270 --panscan 1 --layout 2x1 --video /path/to/movie.mp4`
- `./kms_mosaic --cmd-fifo /tmp/mosaic.fifo --video /path/to/video.mp4`
- `./kms_mosaic --no-video --pane-a "btop" --pane-b "journalctl -f" --font-size 22`
- `./kms_mosaic --playlist-extended mylist.txt --loop-playlist --shuffle`
- `./kms_mosaic --config /path/profile.conf`
- `./kms_mosaic --save-config /path/profile.conf`
- `./kms_mosaic --save-config-default`
- With `--cmd-fifo PATH`, send commands from another terminal, e.g.:
  `echo panscan > /tmp/mosaic.fifo` toggles video panscan
  `echo quit > /tmp/mosaic.fifo` quits the compositor
- `./kms_mosaic --playlist-fifo /tmp/playlist.fifo`
  - create fifo: `mkfifo /tmp/playlist.fifo` then `echo /path/video.mp4 > /tmp/playlist.fifo`
  - playlist loops back to the first entry when the last video ends

-Controls
- Ctrl+Q: quit compositor (always active)
- Ctrl+E: toggle Control Mode. While active, compositor consumes layout/role keys.
- Tab (in Control Mode): cycle focus among C/A/B (video, pane A, pane B)
- l / L (in Control Mode): cycle layouts forward/back
- t (in Control Mode): swap the focused pane with the next pane
- r / R (in Control Mode): rotate roles among (C video, A, B) / reverse
- o (in Control Mode): toggle OSD showing video title and layout
- ? (in Control Mode): help overlay
- f (in Control Mode): force pane surface rebuild (refresh from vterm screen)
- s (in Control Mode): save current configuration as default
- Arrows (in Control Mode): resize column/row splits (L-shaped layouts)
- z (in Control Mode): toggle fullscreen for the focused pane
- n / p (in Control Mode while fullscreen): show next/previous pane
- a (in Control Mode while fullscreen): toggle auto rotation of panes
- c (in Control Mode): toggle panscan for the video
  - The focused pane is outlined with a cyan border while in Control Mode.
- Outside Control Mode: all keys go to the focused pane; when focus is video, keys are forwarded to mpv (space/pause, n/p next/prev, arrows, ASCII)
  - Video focus key support: ASCII, Space/Enter/Tab, arrows, Home/End, PgUp/PgDn, Ins/Del, F1–F12, Esc, Backspace; plus fallbacks for space (pause), n/p (next/prev)


Layouts
- Portrait (90/270):
  - stack: 3 rows in 1 column (Top=C, Middle=A, Bottom=B by default)
  - 2over1: two columns in first row (C | A), second row single column (B)
  - 1over2: one column in first row (C), second row two columns (A | B)
- Landscape (0/180):
  - stack: 3 rows in 1 column
  - row: 1 row in 3 columns
  - 2x1: 2 rows in left column, right column full height
  - 1x2: left column full height, right column split into 2 rows
- Pane role assignment (C=video, A, B) is a permutation over the 3 slots and can be rotated/swapped at runtime via r/R/t.

Planned TODOs
- Improve Unicode/box drawing coverage and performance. [in-progress]
- Make connector/mode selection configurable.
- Implement atomic modesetting + nonblocking pageflips. [in-progress]

Atomic modesetting (experimental)
- Enable with `--atomic`. If supported by the GPU/driver, the compositor uses DRM atomic to set the initial mode and flip via the primary plane. Falls back to legacy KMS if unavailable.
- Optional: `--atomic-nonblock` enables event-driven nonblocking atomic flips (default is blocking for maximum robustness).
- Optional: `--gl-finish` forces `glFinish()` before flips to serialize GPU work on stacks requiring explicit sync.

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
- --playlist-fifo PATH: watch a named pipe for newline-delimited video paths; use `mkfifo PATH` then write file paths to it to append.
  Playlist automatically loops back to the first entry when reaching the end.
- --roles XYZ: set initial slot order of panes (e.g., CAB); saved with `--save-config`.
- --playlist-extended FILE: custom playlist with per-line options (each line: "path | key=val,key=val").
- --no-video: disable the video region and use full width for the text panes.
- --loop-file: loop the current file indefinitely.
- --loop: shorthand for --loop-file (infinite). Note: if you provide exactly one video and no playlist, looping is assumed by default.
- --loop-playlist: loop the playlist indefinitely.
- --shuffle: randomize playlist order (alias: --randomize).
- --mpv-opt K=V: set global mpv option (repeatable), e.g., --mpv-opt keepaspect=yes.
- --cmd-fifo PATH: read newline-delimited commands from FIFO for runtime control (e.g., panscan, quit).
- --font-size PX: terminal font size in pixels (default 18).
- --right-frac PCT: percent of screen width used by right column (10..80, default 33).
- --video-frac PCT: percent of screen width for the video region (overrides --right-frac).
- --pane-split PCT: percent of right column height for top pane (10..90, default 50).
- --pane-a "CMD": shell command for top-right pane (default: btop).
- --pane-b "CMD": shell command for bottom-right pane (default: tail -f /var/log/syslog).
- --fs-cycle-sec SEC: seconds between fullscreen pane rotations when auto mode enabled (0 disables).
- --list-connectors: print connectors and first 8 modes then exit.
- --config FILE: load flags from a config file (supports quotes, comments with #).
- --save-config FILE: write the current configuration as flags to a file.
- --save-config-default: write the configuration to the default config path.
 - --atomic: use DRM atomic modesetting (falls back to legacy automatically).
 - --atomic-nonblock: use nonblocking atomic flips (event-driven).
 - --gl-finish: call `glFinish()` before flips (serialize GPU if needed).

- --no-config: do not auto-load the default config
- --smooth: balanced playback preset (display-resample, no interp, linear tscale, early-flush, no shader cache)
- --layout stack|row|2x1|1x2|1over2|2over1: select tiling mode (applies in any rotation)

Runtime focus and input
- Focus targets: C=video, A=btop (by default), B=syslog (by default). Use Tab in Control Mode to select.
- Outside Control Mode, keypresses go to the focused target. This keeps underlying programs (btop, shell, mpv) fully interactive.

OSD
- Default off for a clean display. Toggle in Control Mode with 'o' to show current video title and layout, or '?' for help overlay.
- Long OSD lines wrap automatically to the viewport width.
- Status line shows the current layout (stack, row, 2x1, 1x2, 1over2, 2over1).

Behavioral defaults
- Single-video auto-loop: if only one file is given and no playlist, looping is enabled automatically.

Debugging
- Enable verbose logging: set `KMS_MOSAIC_DEBUG=1` to print layout changes, mpv events, and GL checkpoints.
- Isolate GL state issues: set `KMS_MOSAIC_DISABLE=1` to skip mpv and render only panes/OSD; set `KMS_MOSAIC_DIRECT_TEST=1` to draw diagnostic color frames.
- If panes go black while video is fine, it may be stale GL state (e.g., scissor) left by mpv. The compositor now resets GL state before drawing panes and OSD.

Default config path
- On Unraid: `/boot/config/kms_mosaic.conf` (persistent across reboots)
- Elsewhere: `$XDG_CONFIG_HOME/kms_mosaic.conf` or `~/.config/kms_mosaic.conf`

Unraid build
- Recommended: build inside a Slackware 15 container, then install the resulting .txz on Unraid.
- Direct host build requires dev toolchain and libraries (libdrm, mesa-gbm/EGL/GLES2, libmpv, libvterm, freetype, fontconfig).

Steps (containerized)
- Ensure Docker is enabled on Unraid.
- From this repo root:
  - scripts/docker_slackware_build.sh  # builds binary and .txz package
  - The package appears under `dist/` as `kms_mosaic-<ver>-x86_64-1.txz`.
  - Install on Unraid host: `installpkg dist/kms_mosaic-<ver>-x86_64-1.txz`

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
  - Result: `dist/kms_mosaic-<date>-x86_64-1.txz`
- Install on Unraid: copy the `.txz` to your server and run `installpkg`.
- Note: If you prefer a Slackware build environment, set `BASE_IMAGE=slackware:15.0` before running and ensure the image exists locally.
