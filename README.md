KMS Mosaic
==========

Direct-to-KMS video + terminal compositor for the Linux console.

It uses DRM/KMS + GBM + EGL/GLES2 for scanout, libmpv for video rendering, and
libvterm for terminal panes. The compositor keeps one video slot plus a
configurable list of terminal panes.

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
- Automatic config-file reload by self-reexec when the active config file changes
- Bounded hash-backed terminal glyph cache
- libvterm damage callbacks for pane redraw tracking
- Indexed pane-array plumbing through `app`, `frame`, and `panes` instead of separate A/B argument chains
- Slot-indexed layout output through `layout`, `app`, and `frame` instead of named `video` / `pane_a` / `pane_b` layout fields
- Indexed pane pollfd handling through `runtime` instead of dedicated pane-A/pane-B poll slots
- User-facing variable terminal pane counts
- Runtime pane, scene, UI, and pollfd storage now allocates from the configured pane count instead of fixed-cap live buffers
- Option parsing and layout output now allocate pane/role storage dynamically, including generic `--pane N "CMD"` support
- DRM atomic modesetting with optional nonblocking flips
- Containerized Linux build path from macOS and other non-Linux hosts
- Unraid deployment workflow using the existing userscript launcher

Still not implemented:

- Richer layout families for very large pane counts
- More polished naming/help text for panes beyond the legacy A/B/C/D shortcuts
- Further terminal rendering/performance tuning under heavy Unicode and scroll load

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
./kms_mosaic --pane-media 2 --pane-video 2 /mnt/user/video/clip.mp4
./kms_mosaic --pane-media 3 --pane-playlist 3 /boot/config/pane3.m3u
./kms_mosaic --pane-media 2 --pane-playlist-fifo 2 /tmp/pane2.fifo --pane-media 3 --pane-playlist-fifo 3 /tmp/pane3.fifo
./kms_mosaic --pane-media 2 --pane-mpv-opt 2 mute=yes --pane-mpv-opt 2 video-rotate=90
./kms_mosaic --playlist-extended mylist.txt --loop-playlist --shuffle
./kms_mosaic --playlist-fifo /tmp/mosaic.fifo --mpv-out /tmp/mpv.log
./kms_mosaic --config /path/profile.conf
./kms_mosaic --save-config-default
./kms_mosaic --layout overlay /path/to/video.mp4
```

Web UI
------

There is also a companion web control surface:

```sh
python3 tools/kms_mosaic_web.py --config /boot/config/kms_mosaic.conf --host 0.0.0.0 --port 8787
```

That service:

- reads the active `kms_mosaic.conf`
- streams live compositor frames into the browser preview over a framed binary HTTP stream
- exposes a pane-oriented "Layout Studio" that edits the saved `--split-tree` when present
- lets you add/remove panes, switch panes between terminal and mpv, and edit each mpv pane's media queue from an explicit playlist target bar instead of tying queue edits to the selected studio pane
- lets you attach pane-local mpv options to mpv panes so each media pane can override the global mpv defaults
- lets each mpv pane keep its own playlist file or playlist FIFO so multiple mpv panes do not share one live queue
- exposes the same structured mpv controls on the video pane and extra mpv panes so playlist, playlist-extended, fifo, mute, loop-file, audio, video-only/audio-only mode, shader settings, mpv log output, panscan, and video rotation stay consistent across all media panes
- gives the Media section its own pane target bar, so the same per-mpv-pane fields are editable there instead of only through the selected pane inspector
- exposes structured pane-local mpv controls in the pane inspector for audio, mute, loop-file, video-only/audio-only mode, and shader stacks while preserving a smaller raw per-pane options box for anything else
- rotates playlist preview thumbnails to match the effective KMS rotation plus pane-specific video rotation instead of always rendering them upright
- sizes playlist thumbnails from the selected pane's actual layout geometry and panscan behavior, including portrait-pane treatments
- keeps playlist thumbnails fitted inside their preview frame, overlays video durations on the preview, and moves bulk queue editing into a collapsed section under the playlist editor
- keeps the terminal-pane command editor focused on terminal panes only, so mpv panes are configured through the playlist/media surfaces instead of the Panes section
- lets you split the selected pane vertically or horizontally from the studio itself
- treats the old layout presets as starter suggestions instead of the main editing surface
- moves scene rules, raw config, and raw mpv option text under Advanced
- exposes common global mpv controls such as audio mode and shader stack as structured fields
- shows inline playlist previews in the queue editor using browser-decoded media frames served from the Unraid host
- writes config changes back atomically
- relies on the compositor's file-watch reload path to apply changes live

It is intentionally separate from the KMS compositor process so web serving
does not destabilize scanout or input handling.

Current limitation:

- The web UI now edits split trees, but the studio is still a button-driven split editor rather than a full drag-handle tree designer.
- The browser preview is materially better than the older snapshot-reload path, but it is still fed by compositor snapshots rather than a true encoded display mirror.

Defaults:

- Pane A default command: `btop --utf-force`
- Pane B default command: `tail -F /var/log/syslog -n 500`
  - Fallbacks: `journalctl -f`, then `/var/log/messages`
- Supported pane count range: `1+` via `--pane-count N`
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

When the active config file changes on disk, the running process detects the
mtime change and reloads by re-execing itself with the original command-line
arguments. That keeps the launch path stable while applying the updated config
without needing an external restart.

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

- Add more intentional layouts for higher pane counts instead of relying mostly on split-and-tile behavior
- Add direct drag/resize editing to the split-tree studio instead of only button-driven splits
- Add richer per-pane playlist management in the web UI, including drag-and-drop reorder
- Keep tightening terminal performance under heavy Unicode and scroll loads
- Simplify pane naming/help text so higher-count configurations read more naturally in the UI and saved configs

Requested follow-up UI changes
------------------------------

- Improve the live preview transport further so it behaves closer to a true 1:1 mirror
- Add drag handles for split movement and resizing directly in the Layout Studio
- Expand the structured mpv controls beyond audio/shader fields so fewer users need the raw advanced text
