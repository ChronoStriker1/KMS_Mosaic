# KMS Mosaic

KMS Mosaic displays video and terminal panes directly on a Linux console using DRM/KMS. It is useful for a dedicated monitor showing media, system statistics, and logs without a desktop session.

Each pane can run a terminal command or an independent mpv media queue. The companion web interface edits layouts and playlists, controls playback, and shows a live preview. An Unraid plugin integrates the controls into the server's Settings page.

## Features

- Configurable pane counts from 1 to 64, split layouts, overlays, and display rotation.
- Independent media panes with playlists, FIFOs, audio settings, and mpv options.
- Drag-and-resize Layout Studio with snapping and undo/redo.
- Named scenes and schedules, OSD, fullscreen selection, and fullscreen cycling.
- Playback watchdogs, pane restart controls, and synchronization groups.
- WebRTC preview, configuration history, and supported DDC/CI monitor controls.

## How it works

The C compositor uses DRM/KMS for display ownership, GBM and EGL/GLES2 for rendering, libmpv for video, and libvterm for terminal emulation. Terminal commands run in PTYs. An event-driven loop handles input from those processes, media updates, configuration changes, and display presentation.

The Python web service runs separately from the compositor. It writes configuration atomically and streams captured compositor frames over WebRTC. Layout and display-only changes can apply in process; changes that rebuild media or child processes use the packaged restart path.

## Requirements

- Linux with a working DRM/KMS graphics driver, an attached display, and permission to control the selected DRM device.
- Available DRM display ownership. An active desktop compositor can prevent KMS Mosaic from acquiring it.
- The commands used by terminal panes, such as `btop`, installed on the host.
- For source builds: a C compiler, Make, pkg-config, and the development libraries listed below.
- For the optional web preview: Python 3 with aiortc and PyAV.

macOS can build the Linux package through Docker, but cannot run the DRM/KMS compositor.

## Install on Unraid

In Unraid, open **Plugins → Install Plugin** and use this manifest URL:

```text
https://raw.githubusercontent.com/ChronoStriker1/KMS_Mosaic/main/unraid-plugin/kms.mosaic.plg
```

The manifest downloads its matching binary package and web payload. After installation, open **Settings → KMS Mosaic**, configure the display and panes, save, and start the service.

The plugin manages compositor and web-service startup. Do not run a second compositor or re-enable an older startup userscript alongside it.

Configuration lives at:

- `/boot/config/kms_mosaic.conf` for pane and display settings.
- `/boot/config/plugins/kms.mosaic/kms.mosaic.cfg` for plugin settings.

Use the plugin's controls to stop or restart. Stop and start must complete sequentially so the old process releases the display.

## Build from source

```sh
git clone https://github.com/ChronoStriker1/KMS_Mosaic.git
cd KMS_Mosaic
```

On Linux, install development packages providing these pkg-config modules:

```text
libdrm gbm egl glesv2 mpv vterm freetype2 fontconfig
```

Then:

```sh
make
```

For a containerized Linux build, start Docker and run:

```sh
scripts/macos_build_pkg.sh
```

This defaults to `linux/amd64` and produces a Linux binary and Slackware-style package under `dist/`. To build the Unraid payload after that package exists:

```sh
scripts/build_unraid_plugin.sh
```

The plugin builder uses GNU tar options. On macOS, install GNU tar and place it on PATH for that command, for example:

```sh
brew install gnu-tar
PATH="$(brew --prefix gnu-tar)/libexec/gnubin:$PATH" scripts/build_unraid_plugin.sh
```

## Run on Linux

Start from a console session with access to the DRM device. Use a media file that exists on that machine:

```sh
./kms_mosaic --no-config --video /path/to/video.mp4
```

Examples:

```sh
# Terminal panes without video
./kms_mosaic --no-config --no-video --pane-a "btop" --pane-b "journalctl -f"

# Portrait display
./kms_mosaic --video /path/to/video.mp4 --connector HDMI-A-1 --mode 1080x1920@60 --rotate 90

# A separate media queue in pane 2
./kms_mosaic --pane-media 2 --pane-playlist 2 /path/to/playlist.m3u

# More terminal panes
./kms_mosaic --pane-count 6 --pane 5 "watch -n1 sensors" --pane 6 "iftop"

# Read an explicit profile
./kms_mosaic --config /path/to/profile.conf
```

Replace connector names, modes, commands, and file paths to match the host. A single video loops automatically unless a playlist is in use. See `./kms_mosaic --help` for the available options.

For packaged Unraid installations, use `/usr/local/bin/kms_mosaic`, which sets up library paths, instead of the internal `kms_mosaic.bin`.

## Web interface

On Unraid, use the installed Settings page and managed web service. For a standalone Linux checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install aiortc av
.venv/bin/python tools/kms_mosaic_web.py --config "$HOME/.config/kms_mosaic.conf" --host 127.0.0.1 --port 8788
```

Open `http://127.0.0.1:8788`. Start the compositor with that same config path for changes to affect the display.

The web service can edit terminal commands and local media settings. Bind it to a trusted interface and restrict access if you expose it to other computers. Changing `--host` to `0.0.0.0` listens on all interfaces.

Preview requires a running compositor and the WebRTC dependencies. Capture and encoding are not zero-copy, so preview quality can add CPU cost.

## Configuration

Config files contain the same flags as the command line, with quoting and `#` comments. The default path is `/boot/config/kms_mosaic.conf` on Unraid, otherwise `$XDG_CONFIG_HOME/kms_mosaic.conf` or `~/.config/kms_mosaic.conf`.

Starter layouts include `stack`, `row`, `2x1`, `1x2`, `2over1`, `1over2`, and `overlay`. Layout Studio saves custom split trees for more detailed arrangements.

Atomic modesetting is available through `--atomic` and `--atomic-nonblock`; initialization falls back to legacy KMS if atomic setup fails.

## Troubleshooting

- **DRM permission or display-ownership error:** check device permissions and competing display processes. After stopping a previous instance, allow it to exit before restarting.
- **Missing shared library on Unraid:** launch through the installed wrapper and ensure the binary and plugin payload belong to the same package.
- **Blank preview:** confirm the compositor is producing frames and the web service uses a Python environment with aiortc/PyAV.
- **Empty terminal pane:** check that the configured command exists and runs as the service user.
- **Slow media transitions:** test without expensive shader stacks or forced hardware-decoding overrides.
- **DDC/CI controls unavailable:** the monitor, connection, and host I2C permissions must support them.

The Unraid supervisors retain recent application output in `/tmp/start_kms_mosaic.log` and `/tmp/kms_mosaic_web.log`, plus lifecycle events in `/boot/config/plugins/kms.mosaic/lifecycle.log`.

## Source layout

`src/` contains the compositor's display, media, layout, terminal, and rendering modules. `tools/kms_mosaic_web.py` contains the web editor and preview service. `unraid-plugin/` contains the plugin integration, and `tests/` contains automated checks.
