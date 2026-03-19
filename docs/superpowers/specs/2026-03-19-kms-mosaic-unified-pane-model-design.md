# KMS Mosaic Unified Pane Model Design

**Date:** 2026-03-19
**Status:** Design Approved
**Scope:** UI/editor model, config migration, and native runtime pane-role unification

## Overview

This spec removes the legacy "special video slot plus normal panes" model from KMS Mosaic and replaces it with a single pane system.

After this change:

- `pane_count` means total editable panes on screen
- every pane has a type
- no pane is a special hard-coded main video slot
- any pane can be `mpv` or terminal
- the last remaining pane cannot be removed

The current `Pane C` confusion is a symptom of the old model. The editor is still treating role `0` as a special main-video slot while labeling it as a normal pane. This spec fixes that at the data model boundary instead of patching around it in the UI.

## Problem Statement

The current system has two parallel concepts:

1. A special video slot stored outside the pane arrays.
2. A set of normal panes stored in `pane_*` arrays.

That split leaks into all layers:

- the editor has a dedicated role-`0` inspector path
- pane naming is misleading because the special slot is labeled as `Pane C`
- `pane_count` excludes the special slot
- split-tree roles and saved config semantics depend on `video slot 0 + panes 1..N`
- remove-pane rules rely on the special slot being immortal instead of explicitly enforcing "at least one pane remains"

This creates user-visible regressions and makes configuration behavior harder to reason about.

## Design Goals

- Make every visible region a normal pane with a type.
- Redefine `pane_count` to mean total panes, not "non-video panes."
- Preserve existing terminal and `mpv` content when old configs are loaded.
- Auto-convert old configs without requiring manual cleanup.
- Remove the special-case selected-pane editor path for the old main video role.
- Prevent removal when only one pane remains.
- Keep split-tree editing, queue editing, and pane resizing working after migration.
- Verify all configuration-page changes with Playwright after deployment.

## Unified Pane Model

The system should have one pane model across editor state, serialized config, and native runtime.

### Core Rules

- Pane indices are `0..pane_count-1`.
- Each pane has a type, currently `terminal` or `mpv`.
- Each pane owns its own command or media settings based on type.
- Queue editing is available for any pane whose type is `mpv`.
- No dedicated global video pane exists in the editor or runtime model.

### Persistence Rules

All pane-local media and command state should live in pane-indexed arrays or equivalent pane-scoped structures. The old global media fields remain supported only as legacy input during config conversion.

After conversion and save, the config should persist as a unified pane model instead of writing a parallel "global video plus pane arrays" representation back out.

## Legacy Config Migration

Existing saved configs must auto-convert.

### Conversion Contract

- The current legacy special video role becomes pane `0` in the unified model.
- Existing pane-local terminal and `mpv` panes remain present after it.
- Current media content, queue items, `mpv` options, panscan, rotation, output paths, and playlist state must be preserved as closely as possible.
- Current layout and screen placement must be preserved as closely as possible.

### Role Translation

Old configs currently encode:

- special video role as role `0`
- normal panes as roles `1..N`

The migration must translate:

- split-tree leaf ids
- role order / permutation state
- fullscreen/focus targets
- queue/editor selection restoration

from the old mixed role space into the new all-pane role space.

If conversion cannot preserve an exact old role mapping, it must preserve pane content first and fall back to a safe deterministic ordering rather than dropping pane state.

## Editor Behavior

The web editor should stop branching on a special main-video role.

### Board

- The board renders exactly `pane_count` panes.
- Pane titles are normal pane labels in order.
- No pane is labeled through a fake special-role alias like the current `Pane C`.

### Selected Pane

- Selecting any pane uses one inspector path.
- `Pane Type` is shown for every pane.
- Pane-local controls are rendered from pane type, not from whether the selected role equals `0`.
- `mpv` panes show pane-local media and queue controls.
- terminal panes show pane-local command controls and no queue section.

### Remove Rules

- If `pane_count === 1`, remove-pane controls are hidden or disabled.
- Removal attempts at one remaining pane must fail cleanly even if invoked indirectly.

### Queue Rules

- Any pane with type `mpv` can own a queue.
- Queue UI is tied to the selected pane's type.
- There is no special main queue editor path separate from pane-local `mpv` panes.

## Native Runtime Behavior

The native runtime must adopt the same pane model as the editor.

### Runtime Rules

- `pane_count` means total panes.
- Layout generation targets pane indices directly.
- Rendering chooses behavior from pane type, not from a reserved slot id.
- Focus and fullscreen behavior operate on pane indices directly.
- Overlay/layout code must stop assuming "video plus exactly two panes."

### High-Risk Native Areas

The migration must explicitly address:

- `KMS_MOSAIC_SLOT_VIDEO` special-slot assumptions
- `role_count = 1 + pane_count` invariants
- separate `slot_layouts` versus pane-only arrays
- saved `--roles` semantics that currently reserve `C`
- split-tree parsing and serialization that currently use old role ids
- overlay/fullscreen/focus code that assumes slot `0` is special

## Error Handling

- If legacy config conversion encounters incomplete data, preserve pane content and fall back to a deterministic pane order.
- If split-tree translation becomes invalid during conversion, fall back to a safe layout rather than dropping panes.
- If a pane type is unknown during conversion, preserve its raw settings and fall back to terminal behavior until corrected.
- If only one pane remains, remove operations must fail predictably without corrupting state.

## Verification

Implementation is complete only when all of the following are verified:

- Old configs load with all current terminal and `mpv` panes preserved.
- `pane_count` reflects total panes on screen.
- Every pane shows `Pane Type` in the selected-pane editor.
- Any pane can be switched between terminal and `mpv`.
- Queue editing appears for any selected `mpv` pane.
- Removing panes stops at one remaining pane.
- Split-tree resizing, add-pane, and remove-pane flows still work after migration.
- Saved configs round-trip through the new unified pane model.
- The live Unraid configuration page is checked with Playwright after deployment.
- Live runtime behavior on Unraid still renders and plays pane-local media correctly after conversion.

## Implementation Boundaries

### In `tools/kms_mosaic_web.py`

Own:

- unified pane editor state
- legacy config load conversion into unified pane state
- unified config save path
- selected-pane rendering
- pane type controls for all panes
- remove-last-pane safeguards in editor interactions

### In native runtime files under `src/`

Own:

- role and pane count semantics
- layout generation
- focus/fullscreen bookkeeping
- rendering dispatch by pane type
- legacy config/runtime compatibility during migration

### Out of Scope

- introducing new pane types beyond terminal and `mpv`
- redesigning the visual style of the page beyond what is necessary for the unified model
- unrelated playback optimizations not needed to keep current pane playback behavior intact after migration
