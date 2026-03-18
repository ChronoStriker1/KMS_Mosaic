# KMS Mosaic UI/Editor Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the Unraid plugin page into a three-panel UI/editor workspace with persistent live view, pane-first editing, selected-pane queue placement, split-tree-driven pane resizing, and safe Unraid-side permission normalization during plugin installation.

**Architecture:** Keep the existing responsibility split: [KMSMosaic.page](/Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page) owns the outer plugin-page shell, panel placement, responsive breakpoints, and relocation targets; [tools/kms_mosaic_web.py](/Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py) owns the embedded editor HTML, queue rendering, split-tree geometry, and pane interactions. Reuse the current preview/studio relocation pattern, add a third queue relocation target, and make both drag handles and numeric size edits mutate the same split-tree ancestors before serializing back to `state.split_tree`.

**Tech Stack:** PHP, HTML, CSS, vanilla JavaScript embedded by `tools/kms_mosaic_web.py`, Bash packaging/install scripts, Unraid plugin installer

---

## File Structure

**Primary implementation files:**
- `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`
  Outer workspace shell, panel headers, responsive layout, placeholder mounts, embedded-editor relocation glue, queue-panel collapse classes, and Unraid-scoped styling overrides.
- `tools/kms_mosaic_web.py`
  Embedded editor HTML, queue markup, queue copy, pane-selection state, split-tree helpers, resize handles, numeric size-field behavior, and queue thumbnail/index presentation.

**Verification/support files:**
- `tests/integration.test.html`
  Manual browser smoke-check checklist for the plugin page. Extend it only if it helps verify the new three-panel workspace and resize/queue behaviors.

**Packaging/install files to review and update if needed:**
- `scripts/build_unraid_plugin.sh`
  Staging-tree mode normalization before archiving.
- `unraid-plugin/kms.mosaic.plg`
  Install-time extraction, ownership correction, executable modes, and plugin-installer path validation on Unraid.

**Reference-only files:**
- `docs/superpowers/specs/2026-03-18-kms-mosaic-ui-editor-workspace-design.md`
- `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/include/actions.php`
- `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/scripts/kms_mosaic-service`

---

## Tasks

### Task 1: Rebuild the Plugin-Page Workspace Shell

**Files:**
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`

**Goal:** Replace the current combined `Live View & Layout Studio` section with a three-panel workspace shell that keeps `Live View` visible, renames `Layout Studio` to `Pane Layout`, and introduces a dedicated `Selected Pane Queue` placeholder that can drop below first at narrower widths.

- [ ] **Step 1: Capture the current shell structure before edits**

Run:
```bash
sed -n '360,1160p' /Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
```

Expected: You can identify the current combined panel markup, the preview/studio placeholders, and the relocation helpers around `kmsRelocateLiveStudioPanels()`.

- [ ] **Step 2: Write the failing shell layout change**

Update `KMSMosaic.page` so the page has:
- one `Live View` panel
- one `Pane Layout` panel
- one `Selected Pane Queue` panel
- a separate `Configuration Editor` area for the remaining embedded-editor controls

The queue panel must exist in the page shell with its own placeholder, but start hidden/collapsed until the embedded editor reports an `mpv` selection.

- [ ] **Step 3: Add the responsive breakpoint rules**

Implement the exact breakpoint behavior from the spec:
- above `1200px`: allow all three panels in one row
- `769px` through `1200px`: keep `Live View` and `Pane Layout` side by side and drop queue below
- `768px` and below: stack `Live View`, `Pane Layout`, then queue

Keep a visible gap between panels and remove any leftover styling that makes the old combined frame look misaligned.

- [ ] **Step 4: Update the relocation targets and shell state hooks**

Extend the existing relocation code so `kmsEnsureEmbeddedLoaded()` can move:
- the preview subtree into the live-view placeholder
- the studio subtree into the pane-layout placeholder
- the queue subtree into the selected-queue placeholder

Add shell-level classes or data attributes so the page can collapse the queue panel cleanly when the embedded editor reports a non-`mpv` selection.

- [ ] **Step 5: Run a quick syntax smoke check**

Run:
```bash
php -l /Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
```

Expected: `No syntax errors detected`

- [ ] **Step 6: Commit the shell refactor**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
git commit -m "feat: add three-panel plugin workspace shell"
```

### Task 2: Move Queue Editing Into the Selected-Pane Panel

**Files:**
- Modify: `tools/kms_mosaic_web.py`
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`

**Goal:** Make the queue panel strictly follow board selection, show only the selected `mpv` pane queue, and collapse completely for non-`mpv` panes.

- [ ] **Step 1: Identify the current queue markup and selection flow**

Run:
```bash
sed -n '1600,1765p' /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py
sed -n '3220,3465p' /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py
```

Expected: You can see the current queue section markup, `queueEditorTitle`, `queueEditorNote`, `playlistEditor`, and the `renderPlaylistEditor()` flow.

- [ ] **Step 2: Write the failing queue-panel behavior**

Refactor the embedded editor markup so the queue controls live in a single movable container that includes:
- queue header/title
- selected-pane note
- target indicator or selected-pane label
- queue list
- bulk add
- add-video actions

Do not keep separate queue-editing affordances lower in the general editor once the selected-pane panel is active.

- [ ] **Step 3: Wire selection to queue-panel visibility**

Update the embedded editor so selecting a pane:
- shows the queue panel only if the pane type is `mpv`
- collapses the queue panel if the pane type is terminal
- preserves queue content for an `mpv` pane with zero items by showing an empty state instead of hiding the panel
- preserves the current selected role across embedded-editor reloads or config refreshes when that role still exists
- falls back to no selection, not the wrong pane, when the previous role no longer exists after a reload or layout change

Use one explicit signal from the embedded editor to the page shell. A custom event dispatched on `window` or `document` is acceptable if it carries:
- selected role
- selected pane type
- whether the queue panel should be visible
- queue title text

- [ ] **Step 4: Verify queue relocation after embedded load**

Load the page in a browser and confirm:
- the queue subtree lands in the new queue placeholder
- it does not remain duplicated inside `#kmsUiMount`
- selecting a terminal pane collapses the page-shell queue panel
- selecting an `mpv` pane reopens it
- save/reload or config refresh preserves the selected pane when possible
- if the previously selected role disappears, the page falls back to no selection instead of pointing the queue at another pane

If a browser is available locally, use it. Otherwise note the checks for later Unraid verification.

- [ ] **Step 5: Commit the queue relocation work**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py /Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
git commit -m "feat: relocate queue editor to selected-pane panel"
```

### Task 3: Polish Queue Item Presentation

**Files:**
- Modify: `tools/kms_mosaic_web.py`
- Optionally modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`

**Goal:** Make queue rows denser and clearer by moving the index into the thumbnail region and making thumbnails fill the media-cell height.

- [ ] **Step 1: Capture the current queue row CSS and markup**

Run:
```bash
sed -n '1320,1515p' /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py
sed -n '3248,3305p' /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py
```

Expected: You can see `.playlist-thumb`, `.playlist-index`, `.playlist-row`, and the queue-item template used by `renderPlaylistEditor()`.

- [ ] **Step 2: Write the failing queue row layout**

Change the queue item template so the index badge is rendered inside the thumbnail region on the right side of the thumbnail area rather than as a separate first-column block.

Keep the row readable for both portrait-thumb and non-portrait-thumb states.

- [ ] **Step 3: Make the thumbnail own the media-cell height**

Adjust the queue-row CSS so the media cell and thumbnail stretch to the intended row height without leaving a short preview block inside a taller card.

Preserve:
- hover previews
- duration overlays
- image/video aspect handling
- portrait rotation handling

- [ ] **Step 4: Manually verify queue affordances still work**

Verify:
- item reorder drag/drop still works
- repeat controls still align
- remove buttons still operate
- hover previews still attach to the right thumbnail

- [ ] **Step 5: Commit the queue presentation changes**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py /Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
git commit -m "feat: refine selected-pane queue presentation"
```

### Task 4: Implement Split-Tree Resize Helpers And Numeric Size Editing

**Files:**
- Modify: `tools/kms_mosaic_web.py`

**Goal:** Make pane border dragging and width x height inputs mutate the same split-tree ancestors and serialize back to `state.split_tree`.

- [ ] **Step 1: Map the current split-tree helpers and size-input placeholders**

Run:
```bash
sed -n '2440,2765p' /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py
sed -n '2890,3045p' /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py
```

Expected: You can see `parseSplitTreeSpec`, `serializeSplitTree`, `splitTreeApplyRects`, and the current `.studio-size-input` placeholders that only emit the `coming soon` status.

- [ ] **Step 2: Add split-tree ancestor lookup helpers**

Implement helpers that can:
- walk the split tree and find the path to a selected role
- identify the nearest enclosing `col` ancestor
- identify the nearest enclosing `row` ancestor
- update a node `pct` with min/max clamping
- reserialize the updated model back into `state.split_tree`

Do not add a second geometry state model.

- [ ] **Step 3: Replace the numeric-input placeholder logic**

Update the width and height inputs so:
- width edits adjust the nearest `col` ancestor controlling that pane
- height edits adjust the nearest `row` ancestor controlling that pane
- values remain integer percentages in the `5` to `95` range
- invalid or inapplicable edits snap back to the last valid effective size

- [ ] **Step 4: Add border-handle interactions to the board**

Render visible handles only when useful for the selected pane. Hover-only or selected-only visibility is acceptable, but keep the board visually clean.

Dragging behavior must:
- update the same split-tree ancestor(s) used by the numeric inputs
- clamp minimum pane sizes
- support corner dragging only when both a `row` and `col` ancestor exist for the selected pane
- rerender the board and size inputs live during drag
- visually group the size markers and remove button with the selected or hovered pane controls so they no longer read like detached debug overlays

- [ ] **Step 5: Verify persistence behavior**

After a drag or numeric edit, confirm from the browser console that:
```javascript
window.state?.split_tree
window.state?.splitTreeModel
```
both reflect the new geometry and that a save/reload cycle restores the same layout.

If `state` is not globally exposed, use the nearest available debug hook or add a temporary console log during implementation, then remove it before commit.

- [ ] **Step 6: Verify control affordance polish**

Confirm in the browser that:
- size markers are visually attached to the selected or hovered pane controls
- remove buttons follow the same selected/hovered treatment
- inactive panes do not look cluttered with always-on debug-style controls

- [ ] **Step 7: Commit the resize implementation**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py
git commit -m "feat: implement split-tree pane resizing controls"
```

### Task 5: Verify The Full Workspace End-To-End

**Files:**
- Modify if useful: `tests/integration.test.html`
- Review: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`
- Review: `tools/kms_mosaic_web.py`

**Goal:** Prove the new workspace behaves correctly before packaging it for Unraid.

- [ ] **Step 1: Decide whether `tests/integration.test.html` should be extended**

If the existing HTML smoke-check page is still useful, add checks for:
- `Pane Layout` section naming
- queue panel presence/collapse behavior
- selected `mpv` pane queue visibility
- drag-handle readiness

If it is stale or not helpful, leave it unchanged and document manual checks instead. Do not pad scope with a fake test harness.

- [ ] **Step 2: Run local browser/manual checks**

Verify all of the following in a browser:
- the queue panel drops below first between `769px` and `1200px`
- mobile stacking occurs at `768px` and below
- the queue panel collapses for terminal-pane selection
- selecting an `mpv` pane reopens the queue panel with the correct title
- thumbnails fill the row height and the queue index sits in the thumbnail region
- border dragging and numeric fields both resize the same pane geometry

- [ ] **Step 3: Run a final file-level sanity check**

Run:
```bash
php -l /Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
python3 -m py_compile /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py
```

Expected:
- PHP syntax check passes
- Python compilation succeeds with no output

- [ ] **Step 4: Commit verification-only support updates**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/tests/integration.test.html /Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page /Users/chronostriker1/git/KMS_Mosaic/tools/kms_mosaic_web.py
git commit -m "test: document workspace verification coverage"
```

If no support file changed, skip the commit and note why in the implementation log.

### Task 6: Rebuild The Plugin And Validate Unraid-Side Permissions

**Files:**
- Review/modify if needed: `scripts/build_unraid_plugin.sh`
- Review/modify if needed: `unraid-plugin/kms.mosaic.plg`

**Goal:** Rebuild the plugin bundle, install it through the plugin path, and verify Unraid-side ownership/modes do not inherit incompatible macOS metadata.

- [ ] **Step 1: Review the current normalization logic before changing it**

Run:
```bash
sed -n '1,220p' /Users/chronostriker1/git/KMS_Mosaic/scripts/build_unraid_plugin.sh
sed -n '1,220p' /Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/kms.mosaic.plg
```

Expected: You can identify the current `chmod`, `chown`, `tar --owner=0 --group=0`, and install-time extraction behavior.

- [ ] **Step 2: Tighten permission normalization only if needed**

If the new UI/editor files introduce any new install-time paths, explicitly normalize them in the staging tree and installer script.

Keep the outcome explicit:
- directories `0755`
- non-executable files `0644`
- wrappers/scripts/events executable where required
- ownership repaired to `root:root` on the Unraid side where the plugin already expects that

- [ ] **Step 3: Rebuild the plugin artifacts**

Run:
```bash
/Users/chronostriker1/git/KMS_Mosaic/scripts/build_unraid_plugin.sh
```

Expected: updated plugin bundle and `.plg` file in `dist/`

- [ ] **Step 4: Inspect staged archive metadata locally**

Run:
```bash
tar -tvzf /Users/chronostriker1/git/KMS_Mosaic/dist/kms.mosaic-2026.03.18.tgz | head -n 40
```

Expected: archive entries are rooted under `usr/`, with normalized modes and no `./` root entry.

- [ ] **Step 5: Install via the Unraid plugin path and verify runtime permissions**

Use the plugin installer flow on the Unraid host rather than ad hoc file copies. After install, verify over SSH:

```bash
ssh root@192.168.2.4 'stat -c "%U:%G %a %n" /usr/local/bin/kms_mosaic_web /usr/local/bin/kms_mosaic_web.py /usr/local/emhttp/plugins/kms.mosaic /usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page /usr/local/emhttp/plugins/kms.mosaic/scripts/kms_mosaic-service /usr/local/emhttp/plugins/kms.mosaic/event/started'
```

Expected: ownership and modes match the intended Unraid-side runtime contract and do not reflect macOS-specific metadata.

- [ ] **Step 6: Run the live plugin smoke check on Unraid**

After install, verify:
- the plugin page loads
- `Live View`, `Pane Layout`, and queue layout render correctly
- save/reload still work
- selecting panes updates the queue panel correctly

If browser automation is needed, use the headless-shell endpoint the user provided at `192.168.2.4:9222`.

- [ ] **Step 7: Commit packaging/install updates**

```bash
git add /Users/chronostriker1/git/KMS_Mosaic/scripts/build_unraid_plugin.sh /Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/kms.mosaic.plg
git commit -m "fix: normalize plugin install permissions for unraid"
```

Skip the commit if no packaging/install files changed, but record that the verification passed with the existing normalization logic.
