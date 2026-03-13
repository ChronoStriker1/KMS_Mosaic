# Changelog

## Unreleased

- Split the former monolithic compositor into dedicated app, display, media,
  render, frame, pane, layout, options, runtime, and UI modules.
- Changed pane A's default command to `btop --utf-force`.
- Moved PTY handling into the main `poll(2)` loop instead of reading panes every frame.
- Added a bounded hash-backed terminal glyph cache.
- Switched terminal redraw tracking to libvterm damage callbacks.
- Fixed the containerized package build path so it completes reliably on macOS/Docker.
- Documented the Unraid restart requirement that stop/start must be sequential.
- Converted internal terminal-pane runtime storage from explicit `tp_a` / `tp_b`
  fields to indexed pane slots as groundwork for variable pane counts.
- Converted role, focus, fullscreen, and layout-permutation bookkeeping to shared
  slot-count constants instead of hardcoded three-entry arrays.
- Replaced the main app/frame/pane A/B argument chains with indexed pane-layout
  and pane-font arrays to reduce two-pane coupling in the render path.
- Replaced named layout-role output fields with slot-indexed layout arrays through
  the layout/app/frame path.
- Replaced dedicated pane-A/pane-B runtime poll slots with indexed pane pollfd
  handling.
- Added user-facing support for configuring variable terminal pane counts, with
  `--pane-count`, legacy `--pane-a`..`--pane-d` shortcuts, generic `--pane N`
  options, and generalized multi-pane layout tiling.
- Replaced fixed-cap live runtime buffers for panes, scene storage, UI role state,
  and pollfds with allocations sized from the configured pane count.
- Replaced fixed-cap option/layout pane and role storage with dynamic allocations,
  including generic `--pane N "CMD"` configuration support.
- Updated the README and project guidance docs to reflect the current modular
  architecture, build path, and remaining roadmap.
