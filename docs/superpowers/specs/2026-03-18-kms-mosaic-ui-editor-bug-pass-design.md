# KMS Mosaic UI/Editor Bug Pass Design

**Date:** 2026-03-18
**Status:** Design Approved
**Scope:** Unraid plugin UI/editor bug-fix and cleanup pass

## Overview

This spec defines the next bug-fix pass for the Unraid plugin page after the larger workspace refactor.

The goal of this pass is to keep the approved workspace direction:

- `Service Status & Controls`
- `Live View`
- `Pane Layout`
- `Selected Pane`

while fixing the current regressions and removing leftover structure that no longer belongs on the page.

This is not a playback-gap investigation. Playback-gap work remains out of scope.

## Problem Statement

The current deployed page still has several usability and behavior regressions:

- pane tiles in `Pane Layout` carry too much text and are overlapping or unreadable
- clicking a pane can break the live preview
- queue thumbnails and hover previews are far too large
- the queue list does not clamp to a practical visible height
- the immediate visibility controls are in the wrong place and currently hide the wrong pane sets
- visible panes do not reflow to fill freed space after visibility-mode changes
- the bottom `Configuration Editor` panel is now redundant and should be removed
- the live preview and pane board are still not centered horizontally in their panels

The page needs a focused cleanup pass that simplifies the board and corrects the remaining behavior bugs without changing the broader approved workspace model.

## Design Goals

- Keep the page centered around `Live View`, `Pane Layout`, and `Selected Pane`
- Make pane tiles readable by reducing them to only the information needed on the board
- Keep detailed pane configuration in `Selected Pane`, not on the board
- Keep queue editing inline for selected `mpv` panes only
- Move immediate visibility controls to the top service area above the main apply action
- Make visibility changes apply immediately
- Ensure remaining visible panes expand to fill available layout space after a visibility change
- Remove the now-redundant bottom editor panel and duplicated controls
- Preserve safe Unraid plugin-installer deployment and permission normalization

## Page Structure

### Top-Level Layout

The page should be reduced to two effective layers:

1. `Service Status & Controls`
2. Workspace panels:
   - `Live View`
   - `Pane Layout`
   - `Selected Pane`

The bottom `Configuration Editor` panel is removed completely.

The duplicated controls that were left in that panel are also removed, because their surviving counterparts already exist in the service area.

### Visibility Controls Placement

`Neither`, `No Video`, and `No Terminal` live in `Service Status & Controls` directly above the main `Apply Settings` action.

They are not inside `Advanced`.

They apply immediately when clicked rather than waiting for a later submit action.

### Centering

The preview surface inside `Live View` and the board surface inside `Pane Layout` must be horizontally centered within their panel bodies.

This must be explicit shell behavior. The layout should not depend on incidental inherited block flow.

## Pane Layout Board

### Minimal Tile Content

Each pane tile is reduced to:

- pane name
- pane type
- editable size marker

Everything else moves into `Selected Pane`.

This means the board should no longer attempt to show long summaries, duplicate pane-local configuration, or other metadata that causes text collisions in small tiles.

### Selection Behavior

Clicking a pane selects it and updates `Selected Pane`.

That interaction must not break, reset, or reconnect the live preview.

If board selection currently causes preview DOM replacement, preview transport restart, or any other side effect beyond selection-state updates, that coupling must be removed.

## Selected Pane

`Selected Pane` remains the only detailed pane editor.

- When nothing is selected, show the compact empty state.
- When an `mpv` pane is selected, show pane-local controls plus inline queue editing.
- When a terminal pane is selected, show only terminal pane-local controls with no queue section.

No pane detail that can live here should remain crammed into the board tiles.

## Queue Behavior

### Queue Sizing

Queue thumbnails should be visibly smaller than the current deployed version.

The queue should show at most five visible items before the list body scrolls.

That scroll behavior belongs to the list itself, not to the entire `Selected Pane` panel.

### Hover Preview Bounds

Hover previews must never exceed the viewport.

They should be clamped to a bounded overlay size and must not grow larger than the browser window.

### Queue Styling

The relocated queue inside `Selected Pane` must retain complete playlist styling after relocation out of `#kmsUiMount`.

The shell-level selected-pane styles need to fully cover queue rows, thumbnails, buttons, and hover affordances for the relocated subtree.

## Visibility Modes

### Naming

Rename `No Panes` to `No Terminal`.

### Immediate Behavior

The visibility controls apply immediately when clicked.

### Correctness Contract

`Neither`
- show both video panes and terminal panes

`No Video`
- hide all video panes
- keep terminal panes visible

`No Terminal`
- hide all terminal panes
- keep video panes visible

### Layout Reflow

After a visibility change, the remaining visible panes should stretch to fill the available visual space.

The system must not leave dead gaps where hidden panes used to be.

That means visibility mode must feed the same effective layout computation used by both the board and the preview, rather than merely toggling display state for individual pane outputs.

## Implementation Boundaries

### In `KMSMosaic.page`

[KMSMosaic.page](/Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page) owns:

- removal of the bottom `Configuration Editor` panel
- placement of immediate visibility controls above the main apply action
- shell-level horizontal centering for `Live View` and `Pane Layout`
- relocated selected-pane queue styling overrides
- any wrapper cleanup for controls or event wiring that no longer exist

### In `tools/kms_mosaic_web.py`

[tools/kms_mosaic_web.py](/Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py) owns:

- pane-tile content simplification
- board selection behavior
- queue row sizing and hover-preview bounds
- five-row queue scroll behavior
- visibility mode naming and behavior corrections
- visible-pane reflow logic
- removal of any embedded-editor remnants that only existed to support the deleted bottom panel

## Error Handling

- If no pane is selected, `Selected Pane` shows its compact empty state rather than disappearing.
- If a pane selection cannot be applied cleanly, the page should preserve the current live preview instead of tearing it down.
- If queue preview media is unavailable, the queue row should degrade to a bounded placeholder rather than producing oversized hover behavior.
- If a visibility-mode update fails, the page should report the failure without leaving the board and preview out of sync about which panes are visible.

## Verification

Implementation is complete only when all of the following are verified:

- pane tiles no longer overlap and remain readable at practical workspace widths
- selecting a pane does not break the live preview
- the queue shows at most five visible rows before scrolling
- queue thumbnails are smaller and hover previews stay bounded to the viewport
- `Neither`, `No Video`, and `No Terminal` appear above the main apply action
- visibility controls apply immediately
- `No Video` affects all video panes only
- `No Terminal` affects all terminal panes only
- remaining visible panes reflow to fill freed space after a visibility change
- the bottom `Configuration Editor` panel is gone
- the live preview surface is horizontally centered in `Live View`
- the board surface is horizontally centered in `Pane Layout`
- plugin deployment still goes through the Unraid plugin installer path
- installed files still land with normalized Unraid-side ownership and permissions rather than macOS-local metadata

## Out of Scope

- playback-gap analysis between videos
- compositor/runtime performance tuning beyond what is required to stop pane-selection preview breakage
- broader redesign of service-management flows
