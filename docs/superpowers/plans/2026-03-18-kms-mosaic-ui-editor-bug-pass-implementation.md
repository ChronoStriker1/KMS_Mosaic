# KMS Mosaic UI/Editor Bug Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the remaining Unraid plugin UI/editor regressions by simplifying the pane board, stabilizing preview selection, correcting immediate visibility behavior, constraining queue presentation, removing the redundant bottom panel, and preserving safe Unraid-side plugin installation.

**Architecture:** Keep the current split between the plugin shell and the embedded editor. [KMSMosaic.page](/Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page) owns outer panel structure, top-level controls, relocated styling, and shell-level centering; [tools/kms_mosaic_web.py](/Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py) owns pane-card content, selected-pane behavior, queue rendering, preview interaction wiring, and visibility-mode semantics. Verification must include live Unraid plugin install plus Playwright/browser interaction checks.

**Tech Stack:** PHP, HTML, CSS, vanilla JavaScript embedded in Python, Bash packaging/install scripts, Unraid plugin installer, Playwright/browser automation

---

## File Structure

**Primary implementation files:**
- `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`
  Remove the bottom panel, relocate immediate visibility controls above apply, keep horizontal centering explicit, and carry the relocated selected-pane queue styling.
- `tools/kms_mosaic_web.py`
  Simplify board tiles, stop selection from breaking preview, clamp queue sizing/hover behavior, enforce five-row queue scrolling, rename and fix visibility-mode logic, and make visible panes reflow to fill freed space.

**Verification/support files:**
- `tests/integration.test.html`
  Update the manual/live verification checklist so it covers the bug-pass acceptance criteria instead of the earlier queue-panel layout.

**Packaging/install files to reuse during verification:**
- `scripts/build_unraid_plugin.sh`
- `unraid-plugin/kms.mosaic.plg`

**Reference spec:**
- `docs/superpowers/specs/2026-03-18-kms-mosaic-ui-editor-bug-pass-design.md`

---

## Tasks

### Task 1: Remove Redundant Page Shell UI And Reposition Global Visibility Controls

**Files:**
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`
- Modify: `tools/kms_mosaic_web.py`
- Modify: `tests/integration.test.html`

**Goal:** Remove the bottom `Configuration Editor` section, place `Neither`, `No Video`, and `No Terminal` above the main apply action, clean up embedded-editor remnants that only existed to support the deleted bottom panel, and update the verification checklist accordingly.

- [ ] **Step 1: Capture the current page-shell structure and visibility-control placement**

Run:
```bash
sed -n '560,760p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
sed -n '320,560p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/integration.test.html
```

Expected: You can identify the remaining `Configuration Editor` section, the current service-action area, and the existing integration checklist assumptions.

- [ ] **Step 2: Write the failing checklist updates first**

Update `tests/integration.test.html` so the live checklist explicitly covers:
- no bottom `Configuration Editor` panel
- immediate visibility buttons above the main apply action
- `No Terminal` naming
- correct queue placement only for selected `mpv` panes

Do not claim these checks pass yet. This step is the red phase for the page-shell behavior.

- [ ] **Step 3: Remove the bottom panel and relocate the visibility controls**

Update `KMSMosaic.page` so:
- the bottom `Configuration Editor` panel markup is removed
- leftover lower-page action buttons are removed
- `Neither`, `No Video`, and `No Terminal` are rendered in `Service Status & Controls` directly above the main apply action
- the page no longer contains stale shell wiring for removed controls

- [ ] **Step 4: Remove embedded-editor remnants that only existed for the deleted bottom panel**

Update `tools/kms_mosaic_web.py` so the embedded editor no longer renders, relies on, or keeps dead code paths for controls that were only present to support the removed bottom page panel.

This includes any stale markup, query selectors, rendering paths, or status copy that no longer make sense once detailed pane editing lives in `Selected Pane` and the bottom shell panel is gone.

- [ ] **Step 5: Run the local syntax checks**

Run:
```bash
python3 -m py_compile /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py
docker run --rm -v /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace:/work -w /work php:8.3-cli php -l unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
```

Expected: Python compile succeeds and PHP lint reports `No syntax errors detected`

- [ ] **Step 6: Commit the page-shell cleanup**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/integration.test.html
git commit -m "feat: simplify plugin shell and move visibility controls"
```

### Task 2: Simplify Pane Tiles And Stop Selection From Breaking Preview

**Files:**
- Modify: `tools/kms_mosaic_web.py`
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`
- Modify: `tests/integration.test.html`

**Goal:** Reduce board tiles to readable minimal content, preserve the compact `Selected Pane` empty state when nothing is selected, and decouple board selection from any preview-breaking side effect.

- [ ] **Step 1: Capture the current board-card rendering and preview relocation flow**

Run:
```bash
sed -n '3188,3308p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py
sed -n '1296,1360p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
```

Expected: You can see the current `studio-card` content and the shell relocation/selection hooks that move live preview and board content.

- [ ] **Step 2: Write the failing interaction and empty-state checks**

Update `tests/integration.test.html` so it explicitly verifies:
- clicking a pane preserves preview continuity and only updates selected-pane state
- `Selected Pane` remains visible with its compact empty state when no pane is selected

This is the failing behavior to fix. Do not bundle unrelated board restyling into this step.

- [ ] **Step 3: Reduce board-card content to the approved minimum**

Update `renderStudioBoard()` and related card markup so each pane tile shows only:
- pane name
- pane type
- size marker

All extra explanatory or overlapping detail text must be removed from the tile.

- [ ] **Step 4: Fix selection-state wiring so preview is not broken by pane clicks**

Trace the current click/select flow and remove any selection-triggered preview teardown, reconnection, DOM replacement, or other side effect beyond updating selected-pane/editor state.

The green result is:
- pane click updates `Selected Pane`
- preview remains visible and alive
- no-selection state still shows the compact `Selected Pane` empty state
- no stale selection side effects remain

- [ ] **Step 5: Re-run the local syntax check**

Run:
```bash
python3 -m py_compile /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py
docker run --rm -v /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace:/work -w /work php:8.3-cli php -l unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
```

Expected: Python compile succeeds and PHP lint reports no syntax errors.

- [ ] **Step 6: Commit the board simplification and preview-stability work**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/integration.test.html
git commit -m "fix: simplify pane cards and preserve preview on selection"
```

### Task 3: Constrain Queue Presentation In Selected Pane

**Files:**
- Modify: `tools/kms_mosaic_web.py`
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`
- Modify: `tests/integration.test.html`

**Goal:** Make the inline queue usable by reducing thumbnail size, clamping hover previews, and showing only five rows before scrolling.

- [ ] **Step 1: Capture the current queue CSS and queue-row rendering**

Run:
```bash
sed -n '1470,1785p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py
sed -n '436,510p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
sed -n '3640,3795p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py
```

Expected: You can see the playlist row sizing, hover preview behavior, and the relocated selected-pane queue styling.

- [ ] **Step 2: Write the failing queue acceptance checks**

Extend the manual checklist so it verifies:
- only five visible queue rows before scrolling
- thumbnails are smaller than the current oversized state
- hover previews remain bounded within the viewport
- queue appears only for selected `mpv` panes
- terminal panes render no queue section
- queue preview media falls back to a bounded placeholder instead of producing runaway hover behavior

- [ ] **Step 3: Shrink queue thumbnails and bound the hover preview**

Update the queue-row and hover-preview sizing so:
- row media cells are smaller
- hover preview size is clamped to a bounded overlay
- hover preview never exceeds the viewport

- [ ] **Step 4: Clamp the visible queue body to five items**

Add a scroll container or max-height rule around the queue list body so the selected-pane queue shows at most five visible rows before scrolling.

This must not make the whole `Selected Pane` panel scroll unnecessarily.

- [ ] **Step 5: Verify relocated queue styling still applies outside `#kmsUiMount`**

Ensure the selected-pane wrapper CSS covers the relocated queue rows, thumbnails, buttons, and hover affordances instead of relying on styles that only exist under the old editor mount.

- [ ] **Step 6: Commit the queue-presentation fix**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/integration.test.html
git commit -m "fix: constrain selected-pane queue presentation"
```

### Task 4: Correct Immediate Visibility Semantics And Visible-Pane Reflow

**Files:**
- Modify: `tools/kms_mosaic_web.py`
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`
- Modify: `tests/integration.test.html`

**Goal:** Make `Neither`, `No Video`, and `No Terminal` apply immediately, affect the correct pane sets, and reflow the remaining visible panes to fill freed space in both board and preview.

- [ ] **Step 1: Capture the current visibility-mode implementation**

Run:
```bash
sed -n '1824,2005p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py
sed -n '4464,4795p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py
```

Expected: You can see the current button labels, mode mapping, and how visibility flags currently feed the embedded state.

- [ ] **Step 2: Write the failing behavior checklist**

Make the verification list explicit:
- `No Video` hides all video panes only
- `No Terminal` hides all terminal panes only
- remaining visible panes expand into open space
- behavior applies immediately without waiting for a later save/apply cycle
- if a visibility-mode update fails, board and preview do not silently drift out of sync

- [ ] **Step 3: Rename the terminal-only mode**

Rename the UI text and any user-facing copy from `No Panes` to `No Terminal`.

- [ ] **Step 4: Fix the mode semantics at the source**

Trace how visibility mode currently maps to flags/state and correct the logic so:
- `Neither` shows all panes
- `No Video` hides all `mpv`/video panes only
- `No Terminal` hides all terminal panes only

Do not stop at the button layer. Fix the root cause in the shared state/update path.

- [ ] **Step 5: Make remaining visible panes reflow to fill freed space**

Update the effective layout computation used by board and preview so hidden pane classes are removed from the active geometry and the remaining visible panes stretch into the available area.

This should be a layout-model fix, not a CSS-only hide/show patch.

- [ ] **Step 6: Commit the visibility fix**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/integration.test.html
git commit -m "fix: correct immediate visibility modes and pane reflow"
```

### Task 5: Recenter Live View And Pane Layout, Then Rebuild And Verify On Unraid

**Files:**
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`
- Verify: `scripts/build_unraid_plugin.sh`
- Verify: `unraid-plugin/kms.mosaic.plg`

**Goal:** Make centering explicit, rebuild the plugin bundle, reinstall it through the Unraid plugin manager, and verify the live page with browser interaction checks.

- [ ] **Step 1: Capture the current shell-centering rules**

Run:
```bash
sed -n '250,380p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
sed -n '940,1058p' /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
```

Expected: You can identify the current preview and board centering wrappers and any remaining asymmetry.

- [ ] **Step 2: Make horizontal centering explicit for preview and board**

Update the page shell so:
- the preview surface is centered inside `Live View`
- the board surface is centered inside `Pane Layout`
- centering does not depend on the removed bottom panel or on stale width constraints

- [ ] **Step 3: Run local lint/compile checks**

Run:
```bash
python3 -m py_compile /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py
docker run --rm -v /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace:/work -w /work php:8.3-cli php -l unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
```

Expected: both checks pass.

- [ ] **Step 4: Rebuild the plugin bundle**

Run:
```bash
cd /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace
scripts/build_unraid_plugin.sh
```

Expected: updated `dist/kms.mosaic-2026.03.18.tgz` and `dist/kms.mosaic.plg` are produced from the current worktree files.

- [ ] **Step 5: Reinstall through the Unraid plugin manager path**

Use the plugin-installer flow, not ad hoc extraction. If the cached plugin bundle on the host is stale, replace it first and reinstall from a local-asset `.plg` so the live host consumes the rebuilt `.tgz` and `.txz`.

After install, verify:
```bash
ssh root@192.168.2.4 'stat -c "%U:%G %a %n" /usr/local/bin/kms_mosaic_web /usr/local/bin/kms_mosaic_web.py /usr/local/emhttp/plugins/kms.mosaic /usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page /usr/local/emhttp/plugins/kms.mosaic/scripts/kms_mosaic-service /usr/local/emhttp/plugins/kms.mosaic/event/started'
```

Expected: `root:root` ownership and expected `755`/`644` modes, not macOS-local metadata.

- [ ] **Step 6: Run the live browser smoke check with Playwright or the headless Chrome endpoint**

Verify on the live Unraid page:
- no `Configuration Editor` panel
- `Neither`, `No Video`, and `No Terminal` above apply
- board tiles are readable and minimal
- pane selection does not break preview
- queue appears only for selected `mpv` panes
- `Selected Pane` stays visible with its compact empty state when nothing is selected
- queue scroll clamps after five visible items
- hover preview remains bounded
- queue preview fallback stays bounded when preview media is unavailable
- immediate visibility behavior affects the correct pane classes
- remaining visible panes expand to use open space
- failed visibility updates do not leave board and preview showing different effective pane sets
- preview and board are horizontally centered

If one browser path is flaky, use the other. Do not claim completion without live-page evidence.

- [ ] **Step 7: Commit the final deployment-ready bug pass**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tools/kms_mosaic_web.py /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/integration.test.html
git commit -m "fix: complete ui editor bug pass"
```
