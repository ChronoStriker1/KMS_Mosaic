KMS Mosaic
==========

Direct-to-KMS video + terminal compositor for the Linux console.

It uses DRM/KMS + GBM + EGL/GLES2 for scanout, libmpv for video rendering, and
libvterm for terminal panes. Every pane uses the same indexed model and can be
configured as either a terminal or an independent mpv media pane.

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
- `src/ui.c`: OSD focus and fullscreen-cycle state
- `src/term_pane.c`: libvterm terminal emulation and texture updates

Status
------

Implemented:

- Event-driven PTY polling through the compositor `poll(2)` loop
- Event-driven config and control-file watching on Linux
- In-process reloads for layout, OSD, fullscreen, and other display-only changes
- Wrapper-based self-reexec when a config change requires media/process reconstruction
- Bounded hash-backed terminal glyph cache
- libvterm damage callbacks for pane redraw tracking
- Indexed pane-array plumbing through `app`, `frame`, and `panes` instead of separate A/B argument chains
- Slot-indexed layout output through `layout`, `app`, and `frame` instead of named `video` / `pane_a` / `pane_b` layout fields
- Indexed pane pollfd handling through `runtime` instead of dedicated pane-A/pane-B poll slots
- User-facing variable terminal pane counts
- Runtime pane, scene, UI, and pollfd storage now allocates from the configured pane count instead of fixed-cap live buffers
- Option parsing and layout output now allocate pane/role storage dynamically, including generic `--pane N "CMD"` support
- Consistent user-facing pane indices across config parsing, split trees, rendering, preview, and the Layout Studio
- Independent pane-local mpv render targets so differently sized video panes do not thrash one shared FBO
- Pane-local playback watchdogs, manual restart controls, and synchronization groups
- Config-driven OSD and fullscreen-pane selection; interactive console controls are intentionally removed
- DRM atomic modesetting with optional nonblocking flips
- Containerized Linux build path from macOS and other non-Linux hosts
- Unraid deployment workflow through a native Unraid plugin with start/stop and a native settings page that hosts the full editor through same-origin plugin proxies

Still not implemented:

- Richer layout families for very large pane counts
- More polished naming/help text for very high pane counts
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
python3 tools/kms_mosaic_web.py --config /boot/config/kms_mosaic.conf --host 0.0.0.0 --port 8788
```

That service:

- reads the active `kms_mosaic.conf`
- streams the live compositor preview into the browser over WebRTC
- mirrors the exact live preview stream beneath the Layout Studio pane outlines so the editor, preview, and physical output share the same content and coordinates
- exposes a pane-oriented Layout Studio with drag/drop placement, edge/corner resize handles, snapping, and undo/redo for the saved `--split-tree`
- lets you add/remove panes, switch panes between terminal and mpv, and edit each mpv pane's media queue from an explicit playlist target bar instead of tying queue edits to the selected studio pane
- lets you attach pane-local mpv options to mpv panes so each media pane can override the global mpv defaults
- lets each mpv pane keep its own playlist file or playlist FIFO so multiple mpv panes do not share one live queue
- exposes the same structured mpv controls on the video pane and extra mpv panes so playlist, playlist-extended, fifo, mute, loop-file, audio, video-only/audio-only mode, shader settings, mpv log output, panscan, and video rotation stay consistent across all media panes
- gives the Media section its own pane target bar, so the same per-mpv-pane fields are editable there instead of only through the selected pane inspector
- exposes structured pane-local mpv controls in the pane inspector for audio, mute, loop-file, video-only/audio-only mode, and shader stacks while preserving a smaller raw per-pane options box for anything else
- exposes pane-local watchdog, synchronization-group, and restart controls
- rotates playlist preview thumbnails to match the effective KMS rotation plus pane-specific video rotation instead of always rendering them upright
- sizes playlist thumbnails from the selected pane's actual layout geometry and panscan behavior, including portrait-pane treatments
- keeps playlist thumbnails fitted inside their preview frame, overlays video durations on the preview, and moves bulk queue editing into a collapsed section under the playlist editor
- keeps the terminal-pane command editor focused on terminal panes only, so mpv panes are configured through the playlist/media surfaces instead of the Panes section
- lets you split the selected pane vertically or horizontally from the studio itself
- treats the old layout presets as starter suggestions instead of the main editing surface
- moves scene rules, raw config, and raw mpv option text under Advanced
- saves, applies, schedules, and deletes named scenes
- reports compositor/web/GPU health, recent errors, and recovery state
- provides config history with diff and rollback actions
- provides DDC/CI monitor power, input, brightness, and contrast controls when supported by the display
- exposes playback OSD, OSD source pane, fullscreen pane, and fullscreen cycling on the config page
- exposes common global mpv controls such as audio mode and shader stack as structured fields
- shows inline playlist previews in the queue editor using browser-decoded media frames served from the Unraid host
- writes config changes back atomically
- relies on the compositor's file-watch reload path to apply changes live

It is intentionally separate from the KMS compositor process so web serving
does not destabilize scanout or the media render loop.

Current limitation:

- The browser preview is a true content mirror of compositor capture, but capture and encode are not yet a zero-copy path.

Defaults:

- Pane A default command: `btop --utf-force`
- Pane B default command: `tail -F /var/log/syslog -n 500`
  - Fallbacks: `journalctl -f`, then `/var/log/messages`
- Supported pane count range: `1-64` via `--pane-count N`
- Single-video runs auto-enable loop mode unless a playlist is in use

Runtime control
---------------

Runtime behavior is managed through the plugin config page. Layout changes,
pane selection, OSD, fullscreen behavior, media queues, pane restart, scenes,
monitor controls, and service actions do not require console keyboard access.
Options that cannot be represented through the config page are not exposed as
interactive runtime controls.

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

When the active config changes, layout/UI-only settings are applied in-process
without rebuilding media panes. Changes to pane types, commands, queues, or
other process-level settings trigger a self-reexec through the packaged wrapper
so shared-library resolution and launch arguments remain stable.

Debugging
---------

- `KMS_MPV_DEBUG=1`: verbose logs
- `KMS_MPV_DIRECT=1`: direct mpv-to-default-FB path
- `KMS_MPV_DIRECT_FBO=1`: direct mode via an intermediate FBO
- `KMS_MPV_DIRECT_TEST=1`: diagnostic direct-mode red frame path
- `KMS_MPV_FLIPY=1`: flip mpv direct rendering vertically

Unraid notes
------------

The preferred Unraid deployment path is now the native plugin under
`unraid-plugin/`, not the old userscript.

Plugin artifacts:

- plugin manifest: `dist/kms.mosaic.plg`
- plugin payload bundle: `dist/kms.mosaic-2026.07.23.tgz`
- Linux package: `dist/kms_mosaic-2026.07.23-x86_64-1.txz`

Build the plugin artifacts after building the Linux package:

```sh
scripts/build_unraid_plugin.sh
```

On the Unraid host, install the generated `.plg` through the Plugins page or
with the Unraid `plugin install` command. The plugin:

- installs the packaged `kms_mosaic` binary
- installs the web UI under `/usr/local/bin/kms_mosaic_web.py`
- adds an Unraid settings entry for `KMS Mosaic`, and the Plugins tab launches that same page directly
- proxies the standalone editor APIs through the plugin page so the full layout/media/playlist editor runs inside the Unraid settings page instead of an iframe
- manages boot/start/stop/restart for both `kms_mosaic` and the web UI
- stores plugin settings in `/boot/config/plugins/kms.mosaic/kms.mosaic.cfg`
- retires the old `Start kms_mosaic` userscript automatically so boot ownership does not race
- extracts plugin payloads without preserving foreign ownership metadata
- keeps config-watch reloads on the wrapper path so packaged library resolution survives live reexecs

Important operational note:

- Stop and start must be sequential.
- Do not use a parallel stop/start restart pattern.
- Prefer `pkill -x kms_mosaic.bin`, wait briefly, then launch the plugin service or `/usr/local/bin/kms_mosaic` wrapper.
- A very fast restart can still lose DRM master and fail with `drmModeAtomicCommit (modeset): Permission denied`; a delayed second restart has been sufficient on the current Unraid host.
- Do not re-enable the old userscript after installing the plugin, or both launch paths can race on boot.

Release workflow
----------------

For every program update:

1. Run the full automated test suite and build the Linux package.
2. Rebuild the Unraid plugin so its web payload and native-package reference match the current build.
3. Copy the completed artifacts to TOWER and install both the native package and plugin payload.
4. Restart services sequentially when required; never overlap stop and start.
5. Verify the installed hashes, service health, physical display, advancing compositor frames, WebRTC preview, and pane/content alignment on TOWER.
6. Commit and push to GitHub only after the deployed version passes those checks.

Roadmap
-------

High-value remaining work:

- Add more intentional layouts for higher pane counts instead of relying mostly on split-and-tile behavior
- Continue expanding structured mpv controls where a commonly used option still requires raw text
- Pursue lower-copy preview capture/encoding where supported by the host graphics and codec stack
- Keep tightening terminal performance under heavy Unicode and scroll loads
- Simplify pane naming/help text so higher-count configurations read more naturally in the UI and saved configs
