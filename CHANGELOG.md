# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project aims to follow
Semantic Versioning when version numbers are introduced.

## [0.2.0] - 2025-08-30

### Added
- DRM atomic modesetting support with full plane updates; opt-in via `--atomic` (falls back automatically if unsupported).
- Optional nonblocking atomic flips via `--atomic-nonblock` (event-driven path).
- Optional GL pacing with `--gl-finish` to serialize GPU work before flips.
- Framebuffer creation prefers AddFB2 with modifiers (raw ioctl), falls back to AddFB2, then legacy AddFB.
- Control Mode visual border highlight around the focused pane.
- OSD line wrapping for long strings and status line includes current layout (stack/row/2x1/1x2).
- Terminal panes update incrementally: per-row hashing + partial `glTexSubImage2D` uploads.
- Extended Unicode box-drawing coverage (double/mixed line variants).

### Changed
- Removed pane-rect expansion logic so single-pane row/column always fills the computed slot after rotation/layout; rely on font auto-fit instead.

### Fixed
- C11 compatibility (removed GNU `typeof`, addressed misleading-indentation warnings).
- Improved reliability of atomic flips (blocking by default; nonblocking optional).
- Rotation/layout visual updates now apply consistently.

### Docs
- README updated for new flags (`--atomic`, `--atomic-nonblock`, `--gl-finish`), Control Mode border highlight, OSD wrapping, and layout name display.

