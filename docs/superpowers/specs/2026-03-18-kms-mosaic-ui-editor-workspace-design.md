# KMS Mosaic UI/Editor Workspace Design

**Date:** 2026-03-18
**Status:** Design Approved
**Scope:** Unraid plugin UI/editor workflow only

## Overview

This spec defines the next UI/editor pass for the Unraid plugin page. The goal is to make the page feel like a coherent Unraid-native editing workspace while fixing the current layout friction:

- the live preview and pane board need stable alignment and a deliberate gap
- the queue editor needs to move closer to the pane being edited
- pane resizing needs to work directly on the board
- the existing width x height fields need to become functional precision controls
- queue thumbnails and numbering need a cleaner, denser presentation

The playback gap between videos is intentionally out of scope and will be handled in a separate follow-up spec.

## Problem Statement

The current plugin page mixes three related editing tasks across disconnected areas:

1. `Live View` is visually important but competes with the layout board inside a combined frame.
2. The queue editor sits lower in the configuration editor, away from the pane selection workflow.
3. Pane size markers exist, but border dragging and size-field edits are not wired into real layout updates.

This creates a page where the user can see the right concepts, but the page structure does not support a tight "select pane -> resize pane -> adjust that pane's queue -> watch preview update" loop.

## Design Goals

- Keep `Live View` persistently visible during editing.
- Make `Pane Layout` the primary direct-manipulation surface.
- Show the queue only for the selected `mpv` pane.
- Collapse queue UI completely for non-`mpv` pane selection.
- Preserve a panel-based layout that feels normal inside Unraid's web interface.
- Use one geometry update path for border dragging and width x height field edits.
- Improve queue density and clarity without turning the page into a custom dashboard unrelated to the surrounding plugin UI.

## Page Structure

### Desktop Layout

The main editing workspace becomes a three-panel layout:

- `Live View`
- `Pane Layout`
- `Selected Pane Queue`

These panels are peers in the same workspace region instead of a combined `Live View & Layout Studio` frame plus a separate queue editor lower on the page.

`Live View` remains continuously visible so the user can evaluate changes immediately.

`Pane Layout` is the primary editing surface and contains:

- the pane board
- selection state
- pane drag/resize affordances
- size indicators and precision fields

`Selected Pane Queue` is contextual and only appears when the currently selected pane is an `mpv` pane.

### Responsive Behavior

- At widths above `1200px`, all three panels may sit in one row.
- At widths from `769px` through `1200px`, `Live View` and `Pane Layout` remain side by side and the queue panel drops below first.
- At widths `768px` and below, panels stack vertically in this order:
  - `Live View`
  - `Pane Layout`
  - `Selected Pane Queue` when applicable

If the selected pane is not `mpv`, the queue panel collapses entirely so the workspace does not reserve dead space for disabled controls.

### Naming

Rename `Layout Studio` to `Pane Layout`.

This is shorter, more literal, and matches the board-first interaction model more accurately than `Layout Studio`.

## Interaction Design

### Pane Selection

Selecting a pane on the board drives the contextual editing state for the workspace.

- If the selected pane is `mpv`, the queue panel opens for that pane.
- If the selected pane is terminal, the queue panel collapses.
- Queue editing never changes board selection implicitly.

The queue panel header should reflect the active pane, for example `Selected Pane Queue: Pane 2`.

### Pane Resizing

Direct manipulation on the board is the primary resizing workflow.

Each pane receives visible drag affordances on shared borders and corners. Dragging a handle updates pane geometry live on the board.

The width x height controls remain available as precision inputs, but they become a secondary workflow rather than the main one.

The numeric contract for those controls is:

- values are integer percentages of the current board extent, not pixels
- displayed values stay in the existing `5` to `95` range
- the width field represents the selected pane's effective width percentage after applying the current split tree
- the height field represents the selected pane's effective height percentage after applying the current split tree

Both interactions must write through the same underlying layout update path. The implementation should not create one code path for border dragging and another separate geometry model for numeric edits.

Because the current layout system is split-tree driven, resize behavior should modify the effective split-tree geometry rather than introducing a second ad hoc coordinate model.

The persistence contract is:

- border dragging mutates the nearest enclosing split-tree ancestor that actually controls the dragged edge
- dragging a vertical divider updates the nearest enclosing `col` node's `pct`
- dragging a horizontal divider updates the nearest enclosing `row` node's `pct`
- width-field edits update that same nearest enclosing `col` ancestor for the selected pane
- height-field edits update that same nearest enclosing `row` ancestor for the selected pane
- after each edit, the editor reserializes `state.splitTreeModel` back into `state.split_tree`

Corner dragging is only enabled when the selected pane is bounded by both an adjustable `row` split and an adjustable `col` split. In that case, the interaction updates both split ancestors together. If the pane has only one adjustable split ancestor, the UI falls back to edge dragging only instead of inventing freeform rectangle resizing.

### Minimum Size Rules

If a drag or numeric edit would shrink a pane below a minimum usable size, the resize operation clamps at the limit instead of allowing overlap, invalid dimensions, or broken board geometry.

### Existing Pane Controls

Existing remove buttons and size markers remain, but they should visually belong to the selected/hovered pane controls instead of reading like detached debug overlays.

## Queue Panel Behavior

The queue panel shows only the currently selected `mpv` pane's queue.

It is not a multi-target queue switcher. Pane selection on the board is the single context selector.

The queue panel keeps the existing queue actions, but relocates them into the contextual panel:

- add video
- bulk add videos
- reorder items
- repeat count edits
- remove item

If the selected `mpv` pane has no queued entries, the queue panel still opens and shows a clean empty state. The panel only collapses for non-`mpv` pane selection.

## Queue Item Presentation

Queue items should become denser and visually clearer.

### Video Number Placement

Move the queue item index into the thumbnail region, aligned on the right side of the thumbnail area.

This keeps the queue number visually attached to the preview media instead of competing with the form controls.

### Thumbnail Height

Thumbnails should fill the usable height of their media cell rather than sitting inside a shorter visual block with unused slack.

The goal is a thumbnail-first queue row where the preview is readable at a glance without forcing each card to become overly tall.

## Implementation Boundaries

### In `KMSMosaic.page`

[KMSMosaic.page](/Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page) owns:

- the outer workspace shell
- panel ordering
- gaps and alignment
- responsive breakpoints
- relocation targets for `Live View`, `Pane Layout`, and `Selected Pane Queue`
- queue-panel collapse behavior at the page-shell layout level
- section renaming from `Layout Studio` to `Pane Layout`

### In `tools/kms_mosaic_web.py`

[tools/kms_mosaic_web.py](/Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py) owns:

- board internals
- selected-pane state
- queue rendering for the selected pane
- thumbnail and queue index layout
- border dragging
- width x height field wiring
- minimum-size enforcement within the editor model

The outer plugin shell should not become responsible for board geometry logic.

### Packaging And Install Safety

Any plugin packaging or install-script changes made to support this UI/editor work must normalize file and directory permissions for the Unraid target environment instead of preserving metadata from the macOS build machine.

The required contract is:

- install-time scripts must set the intended directory and file modes explicitly on the Unraid side
- packaging steps must avoid relying on macOS owner, group, or mode metadata being correct for the extracted plugin tree
- the installer path must leave plugin files owned and readable in the same way as a normal Unraid plugin install, regardless of the local workstation's defaults
- validation must include confirming that installed wrappers, page assets, PHP files, and service scripts are usable by the expected Unraid runtime users after installation

This requirement applies even if the UI changes are otherwise frontend-only, because the plugin is built on macOS and installed on a different permission model.

## Error Handling

- If queue data is unavailable for a selected `mpv` pane, show a clear empty/error state inside the queue panel.
- If a resize action cannot be applied because the split-tree mapping is invalid, reject the resize cleanly and preserve the current layout instead of leaving the board in a partially updated state.
- If the embedded editor reloads or the config refreshes, preserve the selected pane when possible. If that is not possible, fall back to no selection rather than binding the queue panel to the wrong pane.

## Verification

Implementation is complete only when all of the following are verified:

- `Live View`, `Pane Layout`, and `Selected Pane Queue` align cleanly with a visible gap at desktop widths.
- At medium widths, the queue panel drops below first while `Live View` and `Pane Layout` remain side by side.
- At narrow widths, the panels stack in the approved order.
- The queue panel collapses entirely for non-`mpv` pane selection.
- Border dragging updates visible pane geometry on the board.
- Width x height edits update the same geometry model used by dragging.
- Minimum size enforcement prevents broken pane layouts.
- Queue actions remain scoped to the selected `mpv` pane.
- The queue item index is visually attached to the thumbnail area.
- Thumbnails fill the intended media-cell height.
- Packaging and install validation continue to use the plugin installer path so file ownership and permission behavior remain safe for Unraid.
- Packaging and install validation confirm that extracted files and directories have the intended Unraid-side permissions and do not retain incompatible macOS ownership/mode assumptions.

## Out of Scope

- Playback gap investigation between videos
- Compositor/runtime performance changes
- WebRTC transport tuning
- Broader runtime restart/service management changes

## Files Expected To Change During Implementation

- [KMSMosaic.page](/Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page)
- [tools/kms_mosaic_web.py](/Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py)
- installer/package scripts only as needed to rebuild and validate the plugin safely on Unraid
