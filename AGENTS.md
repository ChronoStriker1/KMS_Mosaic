# AGENTS.md (Project Guidance)

The role of this file is to describe common mistakes and confusion points that agents might encounter as they work in this project. If you ever encounter something in the project that surprises you please alert the developer working with you and indicate this is the case in the AgentMD file to help prevent future agents from having the same issue.

## Surprises / gotchas
- Restarting `kms_mosaic` on the Unraid host is sensitive to stop/start ordering. Do not stop and start in parallel. `pkill -f` can also match the remote shell command line and race badly. Use an exact-name stop like `pkill -x kms_mosaic.bin`, wait briefly, then start the userscript.
- The active config file is now watched at runtime. A change triggers an in-process self-reexec with the original argv, so touching the config is enough to reload it and the PID stays the same.
- Even with the correct sequential flow, a fast restart can still hit `drmModeAtomicCommit (modeset): Permission denied` on the Unraid host. If that happens, wait a few seconds and start it again.
- The containerized packaging path is the most reliable validation route on non-Linux hosts. If you need a Linux build locally, prefer `scripts/macos_build_pkg.sh` over the host `make` path.
- On the Unraid host, launching `/usr/local/bin/kms_mosaic.bin` directly can miss packaged shared-library resolution. Use the `/usr/local/bin/kms_mosaic` wrapper or the userscript path, not the bare binary, when validating remotely.
- The repository architecture described in older docs may be stale. The current compositor is already split across `app`, `display`, `media`, `render_gl`, `frame`, `panes`, `layout`, `options`, `runtime`, and `ui` modules.
- The compositor’s live runtime, option parsing, and layout output now allocate pane/role storage dynamically from `pane_count`. The main remaining legacy assumptions are mostly user-facing naming/help shortcuts (`--pane-a` through `--pane-d`) and related labels, not fixed-cap live buffers.
- The companion web UI is now a tree-first editor rather than just a static form. It streams framed live BMP snapshots over `/api/live.bin`, saves `--split-tree` specs, and demotes legacy scene/raw config fields into Advanced sections.
- The pane inspector now has structured per-pane mpv controls layered on top of pane-local raw mpv options. If you add another pane-local mpv setting, update both the structured parser/builder in `tools/kms_mosaic_web.py` and the raw fallback textarea handling so they stay in sync.
- The web API's `/api/state` response is now shaped as `{ config_path, raw_config, state }`. If you smoke-check it manually, don't expect pane/layout fields at the top level.
- Compositor snapshot files are now written via temp-file rename. If the web preview starts showing torn frames again, treat that as a compositor-side regression first, not just a browser/UI bug.
- `tools/kms_mosaic_web.py` may have live user edits that have not been committed yet. Check the local diff before changing it and patch surgically instead of assuming the repo version is authoritative.
- The remaining explicit user follow-ups are now narrower: improve the preview toward a truer 1:1 mirror, add direct drag/resize handles in the Layout Studio, and expose more common mpv options as structured controls so fewer edits need the raw advanced text.
