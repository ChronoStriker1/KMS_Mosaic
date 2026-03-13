KMS Mosaic
==========

Direct-to-KMS video + terminal compositor for the Linux console.

It uses DRM/KMS + GBM + EGL/GLES2 for scanout, libmpv for video rendering, and
libvterm for terminal panes. The current product model is still a fixed
three-slot mosaic:

- Slot C: video
- Slot A: terminal pane A
- Slot B: terminal pane B

The runtime is modular now. The old single-file compositor has been split into:

- `src/kms_mosaic.c`: process entrypoint and signal wiring
- `src/app.c`: application lifecycle, startup, loop, cleanup
- `src/display.c`: DRM/GBM/EGL setup and page flips
- `src/media.c`: libmpv setup, wakeups, playlist FIFO handling
- `src/render_gl.c`: GL render-target and blit helpers
- `src/frame.c`: per-frame composition and presentation
- `src/panes.c`: terminal-pane creation, font sizing, layout sync
- `src/layout.c`: geometric layout computation
- `src/options.c`: CLI/config parsing and config save path
- `src/runtime.c`: pollfd/runtime state helpers
- `src/ui.c`: control-mode and input handling
- `src/term_pane.c`: libvterm terminal emulation and texture updates

Status
------

Implemented:

- Event-driven PTY polling through the compositor `poll(2)` loop
- Bounded hash-backed terminal glyph cache
- libvterm damage callbacks for pane redraw tracking
- Indexed pane-array plumbing through `app`, `frame`, and `panes` instead of separate A/B argument chains
- Slot-indexed layout output through `layout`, `app`, and `frame` instead of named `video` / `pane_a` / `pane_b` layout fields
- Indexed pane pollfd handling through `runtime` instead of dedicated pane-A/pane-B poll slots
- User-facing variable terminal pane counts up to four panes
- Runtime pane, scene, UI, and pollfd storage now allocates from the configured pane count instead of fixed-cap live buffers
- Option parsing and layout output now allocate pane/role storage dynamically, including generic `--pane N "CMD"` support
- DRM atomic modesetting with optional nonblocking flips
- Containerized Linux build path from macOS and other non-Linux hosts
- Unraid deployment workflow using the existing userscript launcher

Still not implemented:

- Truly unbounded pane counts end-to-end; legacy named pane shortcuts are still documented around the first four panes
- Generalized pane-role naming beyond the legacy A/B plus numeric extension model
- Full removal of the remaining legacy first-four-pane shortcuts from the config/UI surface

Build
-----

Native build on Linux:

```sh
make
```

Required development packages:

- `libdrm`
- `gbm`
- `egl`
- `glesv2`
- `libmpv`
- `libvterm`
- `freetype2`
- `fontconfig`
- `pkg-config`
- C toolchain

Preferred validation/build path on macOS or other non-Linux hosts:

```sh
scripts/macos_build_pkg.sh
```

That path builds inside Docker and produces a Linux binary plus a Slackware-style
package under `dist/`.

Run
---

Examples:

```sh
./kms_mosaic --video /path/to/video.mp4
./kms_mosaic --video /path/to/video.mp4 --connector HDMI-A-1 --mode 1080x1920@60 --rotate 90
./kms_mosaic --no-config --smooth --loop --video-rotate 270 --panscan 1 --layout 2x1 --video /path/to/movie.mp4
./kms_mosaic --no-video --pane-a "btop" --pane-b "journalctl -f" --font-size 22
./kms_mosaic --pane-count 4 --pane-c "htop" --pane-d "watch sensors"
./kms_mosaic --pane-count 6 --pane 5 "watch -n1 sensors" --pane 6 "iftop"
./kms_mosaic --playlist-extended mylist.txt --loop-playlist --shuffle
./kms_mosaic --playlist-fifo /tmp/mosaic.fifo --mpv-out /tmp/mpv.log
./kms_mosaic --config /path/profile.conf
./kms_mosaic --save-config-default
./kms_mosaic --layout overlay /path/to/video.mp4
```

Defaults:

- Pane A default command: `btop --utf-force`
- Pane B default command: `tail -F /var/log/syslog -n 500`
  - Fallbacks: `journalctl -f`, then `/var/log/messages`
- Supported pane count range: `1` to `4`
- Single-video runs auto-enable loop mode unless a playlist is in use

Controls
--------

- `Ctrl+E`: toggle Control Mode
- `Ctrl+Q`: quit while in Control Mode
- `Ctrl+P`: toggle mpv panscan
- `Tab`: cycle focus among the video slot and all active pane slots while in Control Mode
- `l` / `L`: cycle layouts
- `r` / `R`: rotate role assignment across the active slots
- `t`: swap focused pane with the next slot
- `o`: toggle OSD
- `f`: force pane surface rebuild
- `s`: save current config
- `z`: fullscreen focused pane
- `n` / `p`: next / previous fullscreen pane
- `c`: cycle fullscreen panes
- arrow keys: resize split layouts while in Control Mode

Outside Control Mode, input is forwarded to the focused target. For video focus,
the compositor forwards common keys to mpv.

Layouts
-------

- `stack`: three rows
- `row`: three columns
- `2x1`: left column split, right full-height
- `1x2`: left full-height, right column split
- `2over1`: top row split, bottom full-width
- `1over2`: top full-width, bottom row split
- `overlay`: full-screen video with both panes alpha-blended on top

Atomic modesetting
------------------

- `--atomic`: enable DRM atomic modesetting
- `--atomic-nonblock`: enable nonblocking atomic flips
- `--gl-finish`: force `glFinish()` before flips

If atomic init fails, the compositor falls back to legacy KMS.

Configuration
-------------

Default config path:

- Unraid: `/boot/config/kms_mosaic.conf`
- Elsewhere: `$XDG_CONFIG_HOME/kms_mosaic.conf`
- Fallback: `~/.config/kms_mosaic.conf`

Config files use the same CLI flags as the command line and support quoting plus
`#` comments.

Debugging
---------

- `KMS_MPV_DEBUG=1`: verbose logs
- `KMS_MPV_DIRECT=1`: direct mpv-to-default-FB path
- `KMS_MPV_DIRECT_FBO=1`: direct mode via an intermediate FBO
- `KMS_MPV_DIRECT_TEST=1`: diagnostic direct-mode red frame path
- `KMS_MPV_FLIPY=1`: flip mpv direct rendering vertically

Unraid notes
------------

The current deployment workflow has been validated against an Unraid host started
via the existing userscript launcher.

Important operational note:

- Stop and start must be sequential.
- Do not use a parallel stop/start restart pattern.
- Prefer `pkill -x kms_mosaic.bin`, wait briefly, then launch the userscript.
- A very fast restart can still lose DRM master and fail with `drmModeAtomicCommit (modeset): Permission denied`; a delayed second restart has been sufficient on the current Unraid host.

Roadmap
-------

High-value remaining work:

- Remove the remaining first-four-pane shortcut assumptions from help/config/UI and treat all panes uniformly
- Replace the remaining bounded config/layout slot arrays with fully dynamic storage
- Keep tightening terminal performance under heavy Unicode and scroll loads
- Simplify the remaining legacy role/label compatibility paths in config and UI
