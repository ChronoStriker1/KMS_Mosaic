# AGENTS.md (Project Guidance)

The role of this file is to describe common mistakes and confusion points that agents might encounter as they work in this project. If you ever encounter something in the project that surprises you please alert the developer working with you and indicate this is the case in the AgentMD file to help prevent future agents from having the same issue.

## Surprises / gotchas
- Restarting `kms_mosaic` on the Unraid host is sensitive to stop/start ordering. Do not stop and start in parallel. `pkill -f` can also match the remote shell command line and race badly. Use an exact-name stop like `pkill -x kms_mosaic.bin`, wait briefly, then start the userscript.

## Surprises / Gotchas

- The containerized packaging path is the most reliable validation route on non-Linux hosts. If you need a Linux build locally, prefer `scripts/macos_build_pkg.sh` over the host `make` path.
