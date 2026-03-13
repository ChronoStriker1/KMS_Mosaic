# AGENTS.md (Project Guidance)

The role of this file is to describe common mistakes and confusion points that agents might encounter as they work in this project. If you ever encounter something in the project that surprises you please alert the developer working with you and indicate this is the case in the AgentMD file to help prevent future agents from having the same issue.

## Surprises / gotchas
- Restarting `kms_mosaic` on the Unraid host is sensitive to stop/start ordering. Do not stop and start in parallel. `pkill -f` can also match the remote shell command line and race badly. Use an exact-name stop like `pkill -x kms_mosaic.bin`, wait briefly, then start the userscript.
- Even with the correct sequential flow, a fast restart can still hit `drmModeAtomicCommit (modeset): Permission denied` on the Unraid host. If that happens, wait a few seconds and start it again.
- The containerized packaging path is the most reliable validation route on non-Linux hosts. If you need a Linux build locally, prefer `scripts/macos_build_pkg.sh` over the host `make` path.
- The repository architecture described in older docs may be stale. The current compositor is already split across `app`, `display`, `media`, `render_gl`, `frame`, `panes`, `layout`, `options`, `runtime`, and `ui` modules.
- The compositor’s live runtime, option parsing, and layout output now allocate pane/role storage dynamically from `pane_count`. The main remaining legacy assumptions are the first-four-pane named shortcuts (`--pane-a` through `--pane-d`) and the associated label semantics, not fixed-cap live buffers.
