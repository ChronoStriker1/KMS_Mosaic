# Changelog

## Unreleased

- Split the former monolithic compositor into dedicated app, display, media,
  render, frame, pane, layout, options, runtime, and UI modules.
- Changed pane A's default command to `btop --utf-force`.
- Added automatic config-file reload by detecting config mtime changes and
  re-execing the process with the original arguments.
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
- Added a companion web control surface that can stream live compositor frames,
  edit the full raw config, and provide pane-oriented controls for the current
  preset layout model.
- Added pane-studio controls in the web UI for selecting panes, adding/removing
  terminal panes, and reordering the current global video queue with explicit
  groundwork for the future split-tree editor.
- Added pane-local mpv support so non-primary panes can now be configured as
  media panes with their own playlist or video inputs.
- Added pane-local playlist FIFO support so multiple mpv panes can now follow
  different live queues instead of sharing the primary `--playlist-fifo`.
- Added pane-local mpv option support so each mpv pane can now override the
  global `--mpv-opt` set with its own repeatable `--pane-mpv-opt N K=V` flags.
- Wired pane-local mpv wakeups into the compositor poll loop and disabled the
  direct-render shortcut when pane-local media is active, avoiding multi-pane
  media regressions.
- Added compositor-side `--split-tree` layout parsing so explicit split trees
  can override the legacy preset-layout geometry.
- Updated the web queue editor so it follows the currently selected pane,
  allowing pane-local mpv queues to be edited directly instead of only through
  raw text fields.
- Expanded the web pane inspector with structured pane-local mpv controls for
  common overrides like audio, mute, loop-file, video disable, and shader
  stacks, while keeping raw per-pane mpv options for everything else.
- Added inline playlist previews in the web queue editor by serving media files
  through the web app and letting the browser extract still frames for list
  items.
- Reworked the web UI around a split-tree-first Layout Studio, with live split
  buttons, rotation-aware studio geometry, layout suggestions, Advanced
  demotion for legacy scene/raw fields, and structured global mpv controls for
  audio mode and shader stacks.
- Switched the web preview transport to a framed binary live stream and made
  compositor snapshot writes atomic so the browser stops seeing partially
  written frames.
