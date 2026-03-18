# KMS Mosaic Plugin Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the KMS Mosaic Unraid plugin configuration page with fully functional Layout Studio controls, proper visual hierarchy matching Unraid patterns, and responsive design that displays live preview prominently.

**Architecture:** The page restructures existing functionality (service controls, embedded editor, layout studio from kms_mosaic_web.py) into a vertically-stacked, single-column layout. The embedded editor (loaded from backend via actions.php) provides the Layout Studio UI with drag-drop pane repositioning, border resizing, and button controls. All interactions are preserved from the standalone web UI; the plugin page simply reorganizes them with better spacing/styling to match Unraid's visual patterns.

**Tech Stack:** PHP (page generation), HTML5, CSS3 (Unraid design vars), JavaScript (event handling and state management via existing fetch shim)

---

## File Structure

**Primary file to modify:**
- `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page` — The Unraid plugin settings page containing PHP backend integration, HTML layout, CSS styling, and JavaScript event handlers

**Reference/context:**
- `tools/kms_mosaic_web.py` — Contains the standalone web UI with working drag-drop logic (lines 2870-2950); this is the source of truth for Layout Studio functionality
- `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/include/actions.php` — Backend integration for loading editor HTML and state
- Git history (commits 8396b2e onwards) — Shows the transition to plugin embedding

**No new files created** — pure refactoring and fixing of the existing KMSMosaic.page file

---

## Tasks

### Task 1: Audit Current Implementation and Identify Breakage

**Files:**
- Review: `KMSMosaic.page` (all)
- Review: `tools/kms_mosaic_web.py` (lines 2850-2950 for drag-drop reference)
- Review: `include/actions.php` (integration points)

**Goal:** Understand what's working, what's broken, and why the embedded editor's drag-drop isn't functioning.

- [ ] **Step 1: Check browser console**

Open the plugin page in a browser and open DevTools (F12). Look for JavaScript errors related to:
- Event binding failures
- Missing DOM elements (like #studioBoard, buttons with data-studio-split)
- Scope/context issues in the embedded editor

Document any errors in a text file for reference.

- [ ] **Step 2: Inspect current page structure**

Right-click → Inspect Element on the Layout Studio section. Verify:
- Is the HTML being injected into #kmsUiMount correctly?
- Are studio-card elements present with draggable="true"?
- Are button click handlers bound?
- Are there DOM id conflicts (e.g., duplicate button IDs between plugin controls and embedded editor)?

Reference the AGENTS.md file item about not reusing DOM ids like saveBtn/reloadBtn.

- [ ] **Step 3: Compare standalone vs embedded behavior**

Run the standalone kms_mosaic_web (if possible) and verify drag-drop works there. Compare the HTML structure and event binding between standalone and embedded versions. Document differences.

- [ ] **Step 4: Review the fetch shim and HTML rewrite**

In KMSMosaic.page, examine:
- Lines 264-271: kmsRewriteBackendHtml() — rewrites API endpoints
- Lines 515-523: kmsInstallEmbeddedStyles() — installs CSS
- Lines 666-722: kmsEnsureEmbeddedLoaded() — loads HTML/state and injects scripts

Look for any regex replacements or DOM manipulations that might be stripping or breaking interactive elements.

- [ ] **Step 5: Commit audit findings**

No code changes yet. If you discovered issues, document them in a comment at the top of KMSMosaic.page like:

```php
// AUDIT FINDINGS:
// - Issue: [description]
// - Root cause: [why it's broken]
// - Fix: [how we'll address it]
```

Commit with message:
```
chore: audit plugin page embedding and drag-drop functionality

Document current breakage points and reference solutions from standalone UI.
```

---

### Task 2: Fix DOM ID Conflicts

**Files:**
- Modify: `KMSMosaic.page:242-243` (Save Config / Reload From Disk buttons)

**Goal:** Ensure the plugin page button IDs don't conflict with the embedded editor's button IDs.

Per AGENTS.md line 30: "The plugin-hosted settings page must not reuse DOM ids from the embedded editor. Reusing ids like saveBtn/reloadBtn makes the embedded JS bind to the wrong controls and silently breaks config save/reload from the page."

- [ ] **Step 1: Search for ID conflicts**

Search KMSMosaic.page for IDs and check tools/kms_mosaic_web.py for matching IDs:
- Look for: `id="save`, `id="reload"`, `id="studio`, `id="config"`, etc.
- If `#kmsSaveConfigBtn` and `#kmsReloadConfigBtn` exist in both, they're fine (kms prefix prevents collision)
- But watch for any IDs without the `kms` prefix that appear in embedded editor

Run:
```bash
grep -o 'id="[^"]*"' unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page | sort | uniq -c
grep -o 'id="[^"]*"' tools/kms_mosaic_web.py | sort | uniq -c
```

Compare the lists and note any exact matches (except those starting with "kms" or containing "Kms").

- [ ] **Step 2: Rename conflicting IDs in plugin page**

If conflicts found, rename the plugin page button IDs with the `kms` prefix. Example:

```php
// Old:
<button type="button" id="saveBtn">Save Config</button>

// New (if it conflicts):
<button type="button" id="kmsSaveConfigBtn">Save Config</button>
```

Update the corresponding JavaScript event listeners to match:

```javascript
document.getElementById('kmsSaveConfigBtn').addEventListener('click', kmsSaveConfigState);
```

- [ ] **Step 3: Verify no conflicts remain**

Re-run the grep commands and confirm no unintentional ID overlaps exist.

- [ ] **Step 4: Commit**

```bash
git add unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
git commit -m "fix: prevent DOM ID conflicts between plugin and embedded editor

Rename button IDs to use kms prefix, ensuring embedded JS binds to correct controls.
Fixes silent failures in config save/reload.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 3: Verify HTML Injection and Event Binding

**Files:**
- Review: `KMSMosaic.page:666-722` (kmsEnsureEmbeddedLoaded function)
- Reference: `tools/kms_mosaic_web.py` (standalone HTML structure)

**Goal:** Ensure embedded editor HTML is being injected cleanly and scripts are executing in the right order to bind drag-drop handlers.

- [ ] **Step 1: Test embedded HTML loading**

Open the plugin page, wait for editor to load (should show studio board). In DevTools console, run:

```javascript
console.log(document.querySelector('#kmsUiMount .studio-board'));
console.log(document.querySelector('#kmsUiMount .studio-card'));
console.log(window.renderStudioBoard);
```

Expected output: The first two should return DOM elements (not null), the third should be a function. If any are null/undefined, the HTML isn't injecting correctly.

- [ ] **Step 2: Verify script execution order**

The kmsEnsureEmbeddedLoaded function (line 697-701) extracts scripts and re-injects them after DOM insertion. Verify they're executing after DOM ready by adding a console.log at the start of the injected scripts. Look in DevTools console to confirm the order:

1. HTML injected
2. Styles scoped
3. Scripts execute
4. Handlers bound
5. fillForm called

If any step is out of order, the handlers won't bind.

- [ ] **Step 3: Check for timing issues**

The promise-based loading (line 668) might have a race condition. Verify that by the time the scripts execute, the DOM elements they need are present. In DevTools, add breakpoints in the injected scripts to confirm DOM elements exist.

If there's a timing issue, we may need to add a microtask delay or use event-based ready signals.

- [ ] **Step 4: No code changes yet**

If everything loads correctly, document your findings. If there's a timing issue, note it for Task 4.

- [ ] **Step 5: Commit findings**

```bash
git commit --allow-empty -m "chore: verify HTML injection and script execution order

Found: [describe what works/breaks]
No changes needed yet - proceeding to restructure layout.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 4: Restructure HTML Layout

**Files:**
- Modify: `KMSMosaic.page:186-249` (main page HTML structure)

**Goal:** Reorganize page sections in the correct order: Service Controls → Live Preview → Layout Studio → Configuration Editor → Advanced.

Current structure (lines 186-249):
```
Section 1 (KMS UI Shell):
  - Service status
  - Service settings grid
  - Service control buttons
  - Visibility mode
  - #kmsUiMount (embedded editor)
```

New structure:
```
Section 1 (Service Status & Controls):
  - Live status display
  - Plugin settings grid
  - Control buttons + visibility mode

Section 2 (Live Preview):
  - Extracted from embedded editor, positioned prominently

Section 3 (Layout Studio):
  - Layout control buttons
  - Studio board (from embedded editor)
  - Save/Reload buttons

Section 4 (Configuration Editor):
  - Playlists
  - Pane inspector
  - Raw config

Section 5 (Advanced - Collapsible):
  - Raw configuration textarea (if not in editor)
```

- [ ] **Step 1: Create new section shells**

In KMSMosaic.page, replace the entire HTML structure (lines 186-249) with:

```php
<div class="kms-page">
  <!-- Section 1: Service Status & Controls -->
  <section class="kms-panel">
    <div class="kms-panel-head">
      <h2>Service</h2>
    </div>
    <div class="kms-panel-body">
      <!-- Status display (from current lines 192-199) -->
      <div class="kms-service-meta">
        <div id="kmsStatus" class="kms-status pending">
          Checking service state…
        </div>
        <div id="kmsMeta" class="kms-meta">
          Refreshing live service status and editor state.
        </div>
      </div>

      <!-- Settings grid (from current lines 201-226) -->
      <div class="kms-service-grid">
        <!-- Same fields as before -->
      </div>

      <!-- Control buttons (restructured from current lines 228-244) -->
      <div class="kms-actions service-actions">
        <!-- Buttons here, no grid-column span yet -->
      </div>
    </div>
  </section>

  <!-- Section 2: Live Preview (to be extracted from embedded editor) -->
  <section class="kms-panel">
    <div class="kms-panel-head">
      <h2>Live Preview</h2>
    </div>
    <div class="kms-panel-body">
      <div id="kmsPreviewPlaceholder" class="kms-ui-loading">
        Loading live preview…
      </div>
    </div>
  </section>

  <!-- Section 3: Layout Studio (to be extracted from embedded editor) -->
  <section class="kms-panel">
    <div class="kms-panel-head">
      <h2>Layout Studio</h2>
    </div>
    <div class="kms-panel-body">
      <div id="kmsStudioPlaceholder" class="kms-ui-loading">
        Loading layout studio…
      </div>
      <div class="kms-actions" style="margin-top: 12px;">
        <button type="button" class="orange" id="kmsSaveConfigBtn">Save Config</button>
        <button type="button" id="kmsReloadConfigBtn">Reload From Disk</button>
      </div>
    </div>
  </section>

  <!-- Section 4: Configuration Editor (embedded editor main content) -->
  <section class="kms-panel">
    <div class="kms-panel-head">
      <h2>Configuration</h2>
    </div>
    <div class="kms-panel-body">
      <div id="kmsUiMount" class="kms-ui-loading">
        Loading editor…
      </div>
    </div>
  </section>

  <!-- Section 5: Advanced (Collapsible) -->
  <section class="kms-panel">
    <div class="kms-panel-head">
      <details>
        <summary style="cursor: pointer; font-weight: 700;">Advanced Settings</summary>
      </details>
    </div>
    <div class="kms-panel-body" id="kmsAdvancedContent" style="display: none;">
      <!-- Raw config textarea or advanced options here -->
    </div>
  </section>
</div>
```

- [ ] **Step 2: Copy control buttons**

Move the control buttons (Start, Stop, Restart, Refresh, Visibility) from their current location into the Service section's action buttons div. Keep the same onclick handlers.

- [ ] **Step 3: Update CSS for new layout**

Update the `.kms-service-grid` styles to remove the `grid-column: 1 / -1;` hack from the actions row, since we're no longer nesting it in a grid. Adjust `.kms-actions` to have proper spacing.

- [ ] **Step 4: Verify structure**

Reload the page in browser. You should see:
- Service section with status, settings, buttons
- Live Preview section (placeholder text)
- Layout Studio section (placeholder text)
- Configuration section (#kmsUiMount loading the editor)
- Advanced section (collapsed)

The embedded editor will still load into #kmsUiMount, but we've created placeholders for the preview and studio board to be extracted from it.

- [ ] **Step 5: Commit restructuring**

```bash
git add unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
git commit -m "refactor: restructure plugin page layout into separate sections

New section order: Service → Live Preview → Layout Studio → Configuration → Advanced

Reorganize HTML to match Unraid design patterns with proper visual hierarchy.
Live preview and studio board placeholders ready for extraction from embedded editor.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 5: Extract and Position Live Preview

**Files:**
- Modify: `KMSMosaic.page:600-700` (kmsEnsureEmbeddedLoaded and CSS)

**Goal:** Move the live preview from buried in the embedded editor to prominent display in Section 2.

The embedded editor (from tools/kms_mosaic_web.py) renders a `.preview-wrap` div that shows the live video output. We need to:
1. Extract this preview HTML when loading the embedded editor
2. Insert it into the Live Preview section
3. Keep the existing styling and WebRTC video feed working

- [ ] **Step 1: Identify preview HTML structure**

In tools/kms_mosaic_web.py (around lines 1530-1540), the preview HTML is:

```html
<div class="preview-layout">
  <div class="preview-wrap">
    <div class="preview-stage" data-preview-status="idle">
      <video class="preview-video" id="preview-video"></video>
    </div>
  </div>
</div>
```

This is the element we need to extract and move.

- [ ] **Step 2: Modify kmsEnsureEmbeddedLoaded**

After parsing the embedded HTML (line 689), before inserting into #kmsUiMount:

```javascript
// Extract preview wrap
const previewWrap = doc.querySelector('.preview-wrap');
const previewLayout = doc.querySelector('.preview-layout');
if (previewWrap && previewLayout) {
  const previewContainer = document.getElementById('kmsPreviewPlaceholder');
  previewContainer.className = '';
  previewContainer.innerHTML = previewLayout.outerHTML;
  // Remove from embedded doc so it doesn't appear twice
  previewLayout.remove();
}
```

- [ ] **Step 3: Update CSS overrides**

In kmsEmbeddedOverrides() (around line 401), the preview styles scope to #kmsUiMount. Since we're moving the preview out, we need to also apply styles to the preview in its new location (#kmsPreviewPlaceholder). Add:

```javascript
  #kmsPreviewPlaceholder .preview-layout { display: block; }
  #kmsPreviewPlaceholder .preview-wrap {
    margin-inline: auto;
    background: #111 !important;
    border-color: rgba(0,0,0,.22) !important;
    box-shadow: inset 0 0 0 1px rgba(255,255,255,.03), 0 3px 10px rgba(0,0,0,.10) !important;
  }
  #kmsPreviewPlaceholder .preview-video {
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: #000;
  }
```

- [ ] **Step 4: Test preview display**

Reload the page. The Live Preview section should now show the video feed (or idle state if no video). Verify:
- Preview is visible and responsive
- Video feeds into it correctly when a pane is playing
- Aspect ratio matches the display orientation

- [ ] **Step 5: Commit extraction**

```bash
git add unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
git commit -m "feat: extract and position live preview prominently

Move preview-wrap from embedded editor into dedicated Live Preview section.
Update CSS scoping to style preview in new location.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 6: Extract and Position Layout Studio Board

**Files:**
- Modify: `KMSMosaic.page:600-700` (kmsEnsureEmbeddedLoaded)

**Goal:** Extract the studio board and layout controls from the embedded editor and place them in Section 3 for prominence.

Similar to the preview extraction, the layout studio is buried in the embedded editor. The HTML we need to extract includes:
- `.studio-grid` container with buttons
- `.studio-board` (the draggable pane cards)
- `.studio-inspector` (pane details panel)
- All associated button handlers for split/add/remove operations

- [ ] **Step 1: Identify studio HTML**

In tools/kms_mosaic_web.py (around lines 1575-1600), the structure is:

```html
<div class="studio-grid">
  <div class="studio-section">
    <!-- Layout/split control buttons here -->
    <div class="studio-board" id="studioBoard"></div>
  </div>
  <div class="studio-inspector" id="studioInspector">...</div>
</div>
```

The buttons for split/add/remove are rendered dynamically by renderStudioBoard(), so the HTML just has the container divs.

- [ ] **Step 2: Extract studio HTML**

In kmsEnsureEmbeddedLoaded (after preview extraction), add:

```javascript
// Extract studio board
const studioGrid = doc.querySelector('.studio-grid');
if (studioGrid) {
  const studioContainer = document.getElementById('kmsStudioPlaceholder');
  studioContainer.className = '';
  studioContainer.innerHTML = studioGrid.outerHTML;
  // Keep in embedded doc too, or remove to avoid duplication
  // For now, keep it and let CSS hide the embedded version
}
```

- [ ] **Step 3: Hide embedded studio**

Add to kmsEmbeddedOverrides():

```javascript
  #kmsUiMount .studio-grid { display: none !important; }
```

This ensures the studio board only shows in Section 3, not buried in the editor.

- [ ] **Step 4: Verify event binding**

The studio board buttons (.studio-split-btn, split/add/remove actions) are bound in renderStudioBoard() (in the embedded JavaScript that runs after injection). Verify that:
- These buttons are being re-rendered in the extracted location
- Event listeners are being attached to the extracted buttons
- Drag-drop handlers on studio cards are working

To test:
1. Reload page
2. Layout Studio section should show the studio board with pane cards
3. Click the "Split V" or "Split H" buttons - they should work
4. Try dragging a pane card to another - should work

- [ ] **Step 5: Fix CSS scoping for studio**

Update kmsEmbeddedOverrides() to apply studio styles to both #kmsUiMount and #kmsStudioPlaceholder:

```javascript
  #kmsStudioPlaceholder .studio-grid { grid-template-columns: 1fr 280px; }
  #kmsStudioPlaceholder .studio-board { /* ... */ }
  #kmsStudioPlaceholder .studio-card { /* ... */ }
  // ... (copy relevant studio styles and scope to new location)
```

- [ ] **Step 6: Commit extraction**

```bash
git add unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
git commit -m "feat: extract and position layout studio board

Move studio-grid and studio-board from embedded editor into dedicated Layout Studio section.
Update CSS scoping and hide embedded version to avoid duplication.
Studio buttons (split, add, remove) and drag-drop functionality preserved.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 7: Fix Spacing and Styling

**Files:**
- Modify: `KMSMosaic.page:32-184` (CSS styles)

**Goal:** Adjust padding, margins, and gaps to eliminate cramping and wasted space. Ensure all panels match Unraid's visual patterns.

- [ ] **Step 1: Audit current spacing**

Open the page in browser. Look at:
- Vertical gap between sections (should be consistent, ~18px per line 35)
- Padding inside panel bodies (currently 16px per line 56)
- Button spacing and sizing (currently 8px gap per line 94)
- Grid gaps in service settings (12px 16px per line 67)

Identify cramped areas and wasted space. Screenshot or note them.

- [ ] **Step 2: Standardize section spacing**

Update `.kms-page` gap (line 35) to ensure consistent vertical spacing:

```css
.kms-page {
  display: grid;
  gap: 18px;  /* Keep consistent */
}
```

- [ ] **Step 3: Fix service grid responsiveness**

The `.kms-service-grid` (line 65-69) currently uses `repeat(4, minmax(180px, 1fr))`. This might cause cramping on smaller screens or when the preview is tall. Adjust breakpoints:

```css
.kms-service-grid {
  display: grid;
  gap: 12px 16px;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  align-items: end;
}
```

- [ ] **Step 4: Add responsive media queries**

Add/update media queries to ensure proper layout on tablet/mobile:

```css
@media (max-width: 1200px) {
  .kms-service-grid { grid-template-columns: repeat(2, 1fr); }
  .kms-panel-body { padding: 14px; }
}

@media (max-width: 768px) {
  .kms-service-grid { grid-template-columns: 1fr; }
  .kms-page { gap: 14px; }
}
```

- [ ] **Step 5: Ensure button sizing is consistent**

Buttons should be clearly clickable. Check button styles (lines 96-102):

```css
.kms-actions button {
  color: #222;
  padding: 8px 14px;  /* Explicit padding */
  min-height: 36px;   /* Minimum touch target */
}
```

- [ ] **Step 6: Fix segment button styling**

The `.kms-segmented` buttons (line 115-150) look good. Ensure they're properly sized for the visibility mode controls.

- [ ] **Step 7: Add live preview responsive sizing**

Add CSS for the extracted preview to maintain aspect ratio:

```css
#kmsPreviewPlaceholder .preview-layout {
  width: 100%;
  max-width: 100%;
}

#kmsPreviewPlaceholder .preview-wrap {
  aspect-ratio: 16 / 9;
  width: 100%;
  max-width: 800px;
  margin: 0 auto;
}
```

- [ ] **Step 8: Test spacing on different screen sizes**

Reload page and test:
- Full desktop (1920+): All sections should have breathing room
- Tablet (768-1200): Service grid should go 2 columns, sections should stack nicely
- Mobile (< 768): Single column, buttons should be full width or wrapped

- [ ] **Step 9: Commit styling fixes**

```bash
git add unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
git commit -m "style: fix spacing and responsiveness to match Unraid patterns

- Standardize section gaps to 18px
- Make service grid responsive with auto-fit
- Add tablet/mobile media queries
- Ensure buttons have proper touch targets
- Fix preview aspect ratio maintenance

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 8: Test Layout Studio Drag-Drop Functionality

**Files:**
- Review: `KMSMosaic.page` (all JavaScript)
- Test: Browser testing (manual)

**Goal:** Verify that drag-drop pane repositioning and border resizing work correctly after restructuring.

- [ ] **Step 1: Set up test scenario**

Open the plugin page in browser. Configure a layout with multiple panes (e.g., 2x2 split with video + terminals). The Layout Studio section should show all panes as draggable cards.

- [ ] **Step 2: Test drag-drop pane swapping**

In the Layout Studio section:
1. Hover over a pane card → should show hover state
2. Click a pane card → should be selected (highlighted)
3. Drag one pane card onto another → should swap positions
4. Verify the preview updates to show new layout
5. Repeat with different pane combinations

Expected: Smooth drag-drop with visual feedback (dragging class, drop-target class)

- [ ] **Step 3: Test split/layout buttons**

In the Layout Studio, each pane card should have "Split V" and "Split H" buttons. Test:
1. Click "Split V" on a pane → should vertically split that pane
2. Click "Split H" on a pane → should horizontally split that pane
3. Verify studio board updates and preview reflects changes
4. Undo splits by dragging/reordering

Expected: Buttons respond, layout changes in real-time, preview updates

- [ ] **Step 4: Test button handlers in embedded editor**

Verify that buttons in the Configuration section still work:
1. Save Config → should save state to disk
2. Reload From Disk → should reload from file
3. Visibility Mode buttons (Neither, No Videos, No Terminals) → should change display

These are in the plugin page (not extracted), so they should work via the existing JavaScript.

- [ ] **Step 5: Console error check**

Open DevTools Console (F12). Reload the page and look for any JavaScript errors. Common issues:
- `Cannot read property 'addEventListener' of null` — missing DOM element
- `window.renderStudioBoard is not a function` — script injection failed
- `undefined` errors in drag handlers — event binding issue

If errors found, note them for the next task.

- [ ] **Step 6: Test on multiple devices**

If possible, test on:
- Desktop browser (1920+ resolution)
- Tablet browser (iPad, 768px)
- Mobile browser (phone, < 480px)

Verify drag-drop works on touch devices (may need to test with polyfill or fallback).

- [ ] **Step 7: Document findings**

Create a text file with test results:
```
DRAG-DROP TEST RESULTS:
✅ Pane card dragging works
✅ Drop target visual feedback appears
✅ Panes swap on drop
✅ Preview updates
✅ Split buttons work
⚠️ [any issues]

BUTTON TEST RESULTS:
✅ Save Config works
✅ Reload From Disk works
✅ Visibility Mode buttons work
⚠️ [any issues]

CONSOLE ERRORS:
[list any errors or 'none']

RESPONSIVE TESTING:
✅ Desktop layout good
⚠️ Tablet: [issue if any]
⚠️ Mobile: [issue if any]
```

- [ ] **Step 8: No code changes (yet)**

If everything works, proceed to next task. If issues found, troubleshoot:
- Check browser console for errors
- Inspect DOM to verify elements exist
- Check CSS is being applied (look for style rules)
- Verify event listeners are bound (add console.log to event handlers)

- [ ] **Step 9: Commit test results**

```bash
git commit --allow-empty -m "test: verify layout studio drag-drop functionality

Tested drag-drop pane swapping, split buttons, and embedded button handlers.
Result: [PASS / specific issues]

Drag-drop working correctly on [devices/resolutions tested].
Ready for final polish.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 9: Test Service Controls and Configuration

**Files:**
- Test: Browser testing (manual)
- Review: `KMSMosaic.page` (JavaScript event handlers)

**Goal:** Verify all service control buttons and settings work without errors.

- [ ] **Step 1: Test service control buttons**

In the Service section:
1. Click "Start" → kms_mosaic should start (status should change to "running")
2. Click "Stop" → kms_mosaic should stop (status should change to "stopped")
3. Click "Restart" → kms_mosaic should restart (PID should change)
4. Click "Refresh" → status should update without restarting

Check browser console for errors in each action.

- [ ] **Step 2: Test visibility mode**

In the Service section, visibility mode buttons:
1. Click "Neither" → both mpv and terminal panes visible
2. Click "No Videos" → mpv panes hidden, terminals visible
3. Click "No Terminals" → terminals hidden, mpv panes visible
4. Verify buttons show active state (highlighted)

- [ ] **Step 3: Test plugin settings**

In the Service section, settings grid:
1. Change "Start `kms_mosaic` at boot" → toggle yes/no
2. Change "Start preview service at boot" → toggle yes/no
3. Change "Preview service port" → enter different port (e.g., 8789)
4. Change "Config path" → modify path
5. Click "Apply" → settings should save without errors

Verify status updates after Apply.

- [ ] **Step 4: Test Save/Reload Config buttons**

In the Layout Studio section:
1. Make a layout change (e.g., drag panes)
2. Click "Save Config" → should save to disk, status should update
3. Modify layout manually (or reload)
4. Click "Reload From Disk" → layout should revert to saved state

- [ ] **Step 5: Console error check**

In DevTools console, look for errors like:
- `kmsSavePluginSettings is not a function`
- `Cannot reach backend: actions.php`
- Network failures in fetch calls
- Timeout errors

If errors found, check that:
- Event handlers are properly bound (lines 725-730)
- actions.php is accessible
- CSRF token is being passed (if required)

- [ ] **Step 6: Test error handling**

Try to trigger errors:
1. Stop the kms_mosaic service, then click "Start" → should succeed
2. Try to save with an invalid config path → should show error
3. Refresh when service is down → should show "bad" status

Verify error messages are clear and user-friendly.

- [ ] **Step 7: Document test results**

Create a test results file:
```
SERVICE CONTROL TEST RESULTS:
✅ Start button works
✅ Stop button works
✅ Restart button works
✅ Refresh button works
⚠️ [any issues]

VISIBILITY MODE TEST RESULTS:
✅ Neither mode works
✅ No Videos mode works
✅ No Terminals mode works
⚠️ [any issues]

PLUGIN SETTINGS TEST RESULTS:
✅ Boot enable toggle works
✅ Web service toggle works
✅ Port change works
✅ Config path change works
✅ Apply saves settings
⚠️ [any issues]

CONFIG SAVE/RELOAD TEST RESULTS:
✅ Save Config button works
✅ Reload From Disk button works
⚠️ [any issues]

ERROR HANDLING TEST RESULTS:
✅ Errors display clearly
✅ Service recovers after errors
⚠️ [any issues]

CONSOLE ERRORS:
[list any or 'none']
```

- [ ] **Step 8: No code changes (yet)**

If all tests pass, proceed to next task. If issues found:
- Check JavaScript event listeners (lines 725-730)
- Verify actions.php backend is responding
- Check network tab in DevTools for failed requests
- Inspect error messages for clues

- [ ] **Step 9: Commit test results**

```bash
git commit --allow-empty -m "test: verify service controls and configuration

Tested start/stop/restart/refresh buttons, visibility modes, plugin settings,
and save/reload config functionality.

Result: [PASS / specific issues]

All service controls responding correctly. Ready for final polish.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 10: Test Playlist Thumbnails and Hover Behavior

**Files:**
- Test: Browser testing (manual)
- Reference: `tools/kms_mosaic_web.py` (playlist hover logic)

**Goal:** Verify that playlist thumbnails are displaying and hover tooltips work correctly.

- [ ] **Step 1: Navigate to playlist section**

In the Configuration section (where the embedded editor is loaded), look for the playlists area. It should show:
- Playlist thumbnail grid (video preview images)
- Hover tooltips with video metadata (duration, filename, etc.)

- [ ] **Step 2: Test playlist thumbnails**

1. Hover over a playlist thumbnail → tooltip should appear with:
   - Video filename or title
   - Duration
   - Resolution (if available)
2. Move mouse away → tooltip should disappear
3. Click a thumbnail → should select/play that video

Expected: Thumbnails load and are clickable, hover provides useful info.

- [ ] **Step 3: Verify thumbnail images**

The preview images for playlists are required (per spec). Verify:
1. Thumbnails display a preview image (not broken image)
2. If a pane has videos queued, thumbnails appear for each
3. If a pane has no videos, a placeholder or empty state is shown

If thumbnails are missing, it's likely a path/resolution issue in the embedded editor, not the plugin page itself.

- [ ] **Step 4: Test keyboard/touch interaction**

If possible, test on touch device:
1. Tap a thumbnail → should select/play
2. Long-press → should show tooltip or context menu (if applicable)

Verify that touch interactions work smoothly (drag-drop may need special handling).

- [ ] **Step 5: Test pane-specific playlists**

In the Configuration editor:
1. Click on a terminal pane in the Layout Studio
2. The pane inspector (right side) should show that pane's playlist
3. Thumbnails should be specific to that pane (not global)

- [ ] **Step 6: Console error check**

Look for errors related to playlists:
- `Failed to load image: /path/to/thumbnail.jpg`
- `Cannot find playlist element`
- `Thumbnail hover handler not defined`

If errors found, they likely originate in the embedded editor (tools/kms_mosaic_web.py), not the plugin page.

- [ ] **Step 7: Document findings**

```
PLAYLIST THUMBNAIL TEST RESULTS:
✅ Thumbnails display
✅ Hover tooltips appear
✅ Hover shows metadata
✅ Thumbnails are clickable
⚠️ [any issues]

PANE-SPECIFIC PLAYLISTS:
✅ Each pane shows its own playlist
✅ Thumbnails are pane-specific
⚠️ [any issues]

TOUCH INTERACTION:
✅ Tap selects/plays thumbnail
⚠️ [any issues or not tested]

CONSOLE ERRORS:
[list any or 'none']
```

- [ ] **Step 8: No code changes (yet)**

If playlists work correctly, proceed to final tasks. If thumbnails are missing:
- Check that the embedded editor is loading the playlist HTML correctly
- Verify image paths are correct (should be relative to backend, not plugin page)
- Check if a thumbnail cache or regeneration step is needed

- [ ] **Step 9: Commit test results**

```bash
git commit --allow-empty -m "test: verify playlist thumbnails and hover behavior

Tested playlist thumbnail display, hover tooltips, and pane-specific playlist selection.

Result: [PASS / specific issues]

Playlist functionality working as expected in embedded editor.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 11: Full Page Integration Test

**Files:**
- Test: Browser testing (manual)
- Review: `KMSMosaic.page` (all)

**Goal:** Verify the complete page works end-to-end with all sections functioning together.

- [ ] **Step 1: Full page reload test**

1. Load the plugin page from scratch
2. Wait for all sections to fully load:
   - ✅ Service status displays correctly
   - ✅ Live preview shows video feed
   - ✅ Layout Studio board displays panes
   - ✅ Configuration editor loads
3. No errors in console
4. Page is responsive (test window resize)

- [ ] **Step 2: Service → Layout → Save flow**

1. Modify a layout in Layout Studio (e.g., drag panes)
2. Click "Save Config" → saves to disk
3. Click "Start" (if not already running) → service should start
4. Verify preview updates to show new layout
5. Stop service, reload page
6. Click "Reload From Disk" → layout should match what was saved

- [ ] **Step 3: Cross-section interaction**

1. In Service section, change visibility mode → preview should update
2. In Layout Studio, add/remove panes → pane inspector in Configuration should update
3. In Configuration, modify a pane's mpv options → preview should reflect changes
4. All sections should stay in sync

- [ ] **Step 4: Responsive layout test**

Test on different viewport sizes:
1. Desktop 1920x1080 → 5 sections stack nicely, good spacing
2. Tablet 768x1024 → sections adapt, buttons don't overflow
3. Mobile 375x812 → single column, buttons full width or wrapped, scrolling works

- [ ] **Step 5: Performance check**

1. Open DevTools Performance tab
2. Reload page and measure:
   - Time to first paint (should be < 2s)
   - Time to interactive (should be < 5s)
   - Main thread activity (should not freeze)

If there are long tasks blocking the main thread, note them for optimization.

- [ ] **Step 6: Memory/stability check**

1. Leave page open for 5 minutes
2. Monitor DevTools Memory tab
3. Memory should not grow unboundedly
4. No memory leaks or accumulating event listeners

- [ ] **Step 7: Console cleanliness**

1. Reload page
2. Check console for errors, warnings, or verbose logs
3. Expected: No errors, minimal warnings
4. If there are logs from embedded editor, they should be informative (not spam)

- [ ] **Step 8: Browser compatibility (if time)**

If possible, test on:
- Chrome/Chromium latest
- Firefox latest
- Safari (if macOS available)
- Edge (if Windows available)

Check for any browser-specific issues (drag-drop, CSS, JavaScript).

- [ ] **Step 9: Accessibility basics**

1. Tab through the page → focus should be visible on buttons
2. Read with screen reader (if available) → controls should be labeled
3. Zoom page to 200% → layout should still be usable

- [ ] **Step 10: Document final findings**

```
FULL PAGE INTEGRATION TEST RESULTS:

Load Test:
✅ All sections load without errors
✅ Live preview displays
✅ Layout Studio responsive
✅ Configuration editor embeds
✅ No console errors

Cross-section Interaction:
✅ Service → Preview interaction works
✅ Layout changes sync across sections
✅ Configuration edits reflect in preview
✅ Save/Reload maintain state

Responsive Design:
✅ Desktop layout optimal
✅ Tablet layout adapts well
✅ Mobile layout usable
✅ Window resize works smoothly

Performance:
⏱️ First paint: [X]ms
⏱️ Time to interactive: [X]ms
✅ No long tasks blocking main thread

Stability:
✅ Memory stable over 5 minutes
✅ No memory leaks detected
✅ Event listeners cleaned up

Browser Compatibility:
✅ [Chrome/Firefox/Safari/Edge: tested or N/A]

Accessibility:
✅ Tab navigation works
✅ Controls labeled for screen readers
✅ Zoom to 200% usable

Overall Result: [PASS / issues to fix]
```

- [ ] **Step 11: Commit integration test**

```bash
git commit --allow-empty -m "test: full page integration and responsiveness

Tested end-to-end workflow, cross-section interactions, responsive design,
performance, stability, and browser compatibility.

Result: [PASS / issues]

Page fully functional with all sections working together. Ready for deployment.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 12: Final Polish and Deployment

**Files:**
- Modify: `KMSMosaic.page` (any last CSS tweaks or bug fixes)

**Goal:** Address any remaining visual or functional issues, then prepare for deployment.

- [ ] **Step 1: Address findings from Task 11**

Review the test results from Task 11. For any issues:
- Visual/spacing issues → update CSS
- Interaction issues → update JavaScript
- Performance issues → optimize code if critical
- Browser compatibility → add polyfills or fallbacks if needed

Each fix should be targeted and tested before moving on.

- [ ] **Step 2: Final visual polish**

1. Check all sections have consistent visual styling
2. Verify all text is readable (contrast, font size)
3. Check button states (normal, hover, active, disabled)
4. Verify colors match Unraid design system
5. Ensure smooth transitions/animations (not jarring)

Make any final CSS adjustments to match Unraid's aesthetic.

- [ ] **Step 3: Remove debug code**

If you added any console.log statements or debugging code during development:
- Remove or comment out debug logs
- Remove test code or breakpoints
- Clean up any temporary CSS or HTML

Keep only production-ready code.

- [ ] **Step 4: Update comments**

Ensure code comments are:
- Accurate and up-to-date
- Explain "why" not "what" (code shows what)
- Remove any stale comments from earlier iterations

- [ ] **Step 5: Final commit**

```bash
git add unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
git commit -m "polish: final visual and functional refinements

Final CSS adjustments to match Unraid design patterns.
Remove debug code and clean up comments.
All functionality tested and working.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Create a changelog entry**

If the project has a CHANGELOG.md or similar, add an entry:

```markdown
## [Unreleased] - 2026-03-18

### Changed
- Redesigned plugin configuration page for better UX
- Restructured page layout with proper visual hierarchy (Service → Preview → Studio → Config → Advanced)
- Improved spacing and responsiveness to match Unraid design patterns
- Live preview now displayed prominently
- Layout Studio controls fully functional with drag-drop pane repositioning

### Fixed
- Layout Studio drag-drop pane swapping restored
- Service control buttons now fully responsive
- Playlist thumbnails and hover behavior preserved
- Visual spacing issues addressed
```

- [ ] **Step 7: Verification before deployment**

Before this change is deployed:
1. Page loads without errors
2. All functionality works (service controls, drag-drop, save/reload)
3. Layout is responsive
4. No console errors or warnings
5. Tests pass (if applicable)

If any issue is found, circle back to address it before deployment.

- [ ] **Step 8: Mark completion**

The implementation is complete when:
- ✅ All tasks completed
- ✅ All tests pass
- ✅ Code is clean and documented
- ✅ Changes committed to git
- ✅ Page ready for deployment

Document this completion and prepare for handoff or deployment.

---

## Summary of Changes

| Task | Change | Impact |
|------|--------|--------|
| 1-3 | Audit and fix DOM conflicts | Embedded editor controls now bind correctly |
| 4 | Restructure HTML layout | Page sections properly organized |
| 5 | Extract live preview | Preview prominent in dedicated section |
| 6 | Extract layout studio | Studio board easily discoverable |
| 7 | Fix spacing/styling | Better visual hierarchy and responsiveness |
| 8-10 | Test functionality | All controls verified working |
| 11 | Integration testing | End-to-end workflow validated |
| 12 | Final polish | Production-ready page |

**Result:** A fully functional, visually cohesive KMS Mosaic plugin configuration page that matches Unraid's design patterns and provides an intuitive user experience for managing the compositor.

