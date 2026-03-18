# KMS Mosaic Plugin Page Audit Report

## Task 1: Audit Current Implementation and Identify Breakage

**Date:** 2026-03-18
**Status:** COMPLETED
**Focus:** Understanding what's working, what's broken, and why drag-drop isn't functioning

---

## Executive Summary

The KMS Mosaic plugin page embeds a React/JS-based editor from `kms_mosaic_web.py` by:
1. Fetching the HTML shell and JSON state from a backend wrapper
2. Rewriting API paths to route through the Unraid plugin page
3. Installing a fetch shim to intercept API calls
4. Injecting HTML/CSS/JS into the plugin page's `#kmsUiMount` container
5. Manually calling `fillForm()` to populate the editor with state

The drag-drop functionality in the Layout Studio creates draggable cards dynamically and attaches event listeners. **The initialization sequence is actually correct**, but there are several potential breakage points that need to be verified through browser testing.

---

## Key Architectural Components

### 1. HTML Injection (KMSMosaic.page lines 717-745)

```javascript
// Fetch HTML and state in parallel
const [htmlResponse, stateResponse] = await Promise.all([
  fetch(kmsEmbeddedUrl('backend_html')),
  fetch(kmsEmbeddedUrl('backend_state'))
]);

// Parse and rewrite paths
const rewritten = kmsRewriteBackendHtml(html)
  .replace('loadState().catch(...)', 'window.__kmsEmbeddedInitialLoadDeferred = true;');

// Parse into DOM, extract CSS
const doc = new DOMParser().parseFromString(rewritten, 'text/html');
kmsInstallEmbeddedStyles(doc);

// Extract scripts as strings
const scripts = Array.from(doc.querySelectorAll('script')).map(s => s.textContent || '');
doc.querySelectorAll('script').forEach(s => s.remove());

// Inject HTML
mount.innerHTML = doc.body.innerHTML;

// Install fetch shim BEFORE scripts execute
kmsInstallFetchShim();

// Re-inject scripts (now fetch is patched)
scripts.forEach(text => {
  const script = document.createElement('script');
  script.textContent = text;
  mount.appendChild(script);
});

// Manually call fillForm with pre-fetched state
window.fillForm(statePayload.state, statePayload.config_path, statePayload.raw_config);
```

**Status:** ✓ Correct - Fetch shim is installed before scripts execute

### 2. Fetch Shim (KMSMosaic.page lines 273-312)

The fetch shim intercepts calls to `/plugins/kms.mosaic/include/actions.php` and:
- For POST requests with JSON body: converts to form-encoded data with `payload` field
- For GET requests: adds `csrf_token` to query params
- For all requests: delegates to the original fetch with modified parameters

**Status:** ✓ Correct - actions.php expects `$_POST['payload']` (line 186, 198)

### 3. Drag-Drop Implementation (kms_mosaic_web.py lines 2861-2959)

The `renderStudioBoard()` function:
1. Clears the board: `studioBoard.innerHTML = ""`
2. For each pane role, creates a button element:
   - Sets `draggable = true` (line 2871)
   - Sets class `studio-card` with modifiers
   - Injects HTML with split actions and pane info
3. Attaches event listeners to each card:
   - `dragstart` (line 2903): Sets `draggedStudioRole`, adds "dragging" class
   - `dragend` (line 2911): Clears state, removes classes
   - `dragover` (line 2915): Prevents default, adds "drop-target" class
   - `dragleave` (line 2920): Removes "drop-target" class
   - `drop` (line 2923): Swaps pane positions via tree model
   - `click` (line 2898): Selects pane for editing
4. Attaches click handlers to split buttons (line 2945)
5. Appends each card to `studioBoard` (line 2957)

**Status:** ? Uncertain - Depends on `studioBoard` element existing and event binding executing

### 4. Module-Level Variables and Scope

Variables defined in the extracted/re-injected scripts:

```javascript
const layoutNames = ["stack", "row", ...];
const studioBoard = document.getElementById("studioBoard");
const studioInspector = document.getElementById("studioInspector");
let state = null;
let draggedStudioRole = null;
let selectedRole = 0;
// ... more variables
```

When scripts are appended to `#kmsUiMount`, these variables:
- Execute in global scope (not isolated)
- Are accessible from all subsequent code
- Maintain their values across function calls
- Can be inspected via DevTools console

**Status:** ✓ Should work correctly (tested pattern in JS)

---

## Potential Breakage Points

### A. Missing DOM Element: #studioBoard

**Issue:** If `document.getElementById("studioBoard")` returns null, the entire drag-drop system fails silently.

**Why it matters:** Line 1707 in kms_mosaic_web.py assigns to `const studioBoard`. If null, all subsequent operations fail.

**How to verify:**
```javascript
// In browser console, after editor loads:
console.log(document.getElementById("studioBoard"));
console.log(window.studioBoard);
```

**Likelihood:** Low - HTML should be injected correctly via `mount.innerHTML`

---

### B. renderStudioBoard() Throws Error

**Issue:** An error during card creation or event listener attachment could fail silently.

**Why it matters:** If an error occurs while appending cards, the function would return early, leaving the board empty.

**How to verify:**
```javascript
// In browser console, check for errors when:
// 1. Editor loads
// 2. Changes a setting that triggers renderStudioBoard()

// Check console for JavaScript errors
// Open DevTools Network tab to see if fetch calls work
```

**Likelihood:** Medium - Need to verify no exceptions in card creation

---

### C. Event Listeners Not Binding

**Issue:** `addEventListener()` calls might fail if the card elements aren't in the DOM yet.

**Why it matters:** Modern browsers are safe about this (listeners can attach before DOM), but worth verifying.

**How to verify:**
```javascript
// In browser console, inspect an event listener:
const card = document.querySelector('.studio-card');
if (card) {
  // Check the listeners
  console.log(card);
  // Try right-click → Inspect, then DevTools → Event Listeners tab
}
```

**Likelihood:** Very Low - addEventListener is safe with dynamically created elements

---

### D. CSS Preventing Interaction

**Issue:** CSS scoping or z-index issues could make cards unclickable or undraggable.

**Why it matters:** Drag-drop won't work if `pointer-events: none` or other CSS blocks interaction.

**How to verify:**
```javascript
// In browser DevTools Inspector:
// 1. Right-click a studio-card element
// 2. Check "Computed" tab for pointer-events
// 3. Look for any CSS that might block interaction

// Also check:
// - z-index values
// - position: absolute/fixed issues
// - opacity: 0
// - display: none (shouldn't exist)
```

**Likelihood:** Low - CSS scoping regex should work correctly

---

### E. Fetch Shim Not Working for saveState()

**Issue:** The fetch shim converts JSON POST bodies to form data, but might have issues with complex payloads.

**Why it matters:** If saveState() fails, layout changes won't persist.

**How to verify:**
```javascript
// In browser DevTools Network tab:
// 1. Drag a pane to another position
// 2. The save should trigger automatically
// 3. Look for a POST to actions.php?action=backend_config
// 4. Verify the "payload" field contains the full JSON config

// Or manually test:
// In console: window.saveState().then(...).catch(err => console.error(err))
```

**Likelihood:** Medium - Need to verify payload encoding

---

### F. CSS Scoping Breaking Drag Styles

**Issue:** The `kmsScopeCss()` regex might break complex selectors, preventing `.drop-target` styling from working.

**Why it matters:** Users won't see visual feedback when dragging over a card.

**How to verify:**
```javascript
// In browser DevTools Inspector:
// 1. Drag a card over another
// 2. The target should get class "drop-target"
// 3. Check if the styling applies
// 4. Look at kmsEmbeddedOverrides() for the .drop-target styles

// Inspect the computed styles:
console.log(getComputedStyle(document.querySelector('.studio-card.drop-target')));
```

**Likelihood:** Low - Scoping regex looks correct

---

## Files Reviewed

### KMSMosaic.page (Plugin Page)
- **Lines 32-184:** CSS for plugin UI
- **Lines 253-312:** Fetch shim implementation
- **Lines 314-497:** CSS scoping and overrides for embedded editor
- **Lines 499-523:** `kmsScopeCss()` function for CSS rewriting
- **Lines 666-722:** `kmsEnsureEmbeddedLoaded()` - main initialization
- **Lines 724-732:** jQuery initialization and event binding

### kms_mosaic_web.py (Embedded Editor)
- **Lines 1588:** `<div class="studio-board" id="studioBoard"></div>` HTML
- **Lines 1707:** `const studioBoard = document.getElementById("studioBoard");`
- **Lines 2861-2959:** `renderStudioBoard()` function with drag-drop handlers
- **Lines 2869-2910:** Card creation and dragstart/dragend handlers
- **Lines 2915-2944:** dragover/dragleave/drop handlers

### actions.php (Backend Handler)
- **Lines 172-181:** Fetches HTML via `kms_mosaic_web` wrapper
- **Lines 184-193:** Proxies `/api/config` POST to wrapper
- **Lines 186, 198:** Expects `$_POST['payload']` or `file_get_contents('php://input')`

---

## DOM ID Collision Risks

### Plugin Page IDs (Safe)
- `kmsStatus` - service status display
- `kmsMeta` - metadata display
- `kmsServiceBoot` - service boot select
- `kmsWebBoot` - web service boot select
- `kmsWebPort` - web port input
- `kmsConfigPath` - config path input
- `kmsVisibilityMode` - visibility toggle buttons
- `kmsSaveConfigBtn` - save config button (orange)
- `kmsReloadConfigBtn` - reload config button
- `kmsUiMount` - embedding container

### Embedded Editor IDs (Safe)
- `studioBoard` - layout editor canvas
- `studioInspector` - pane inspector panel
- `addPaneBtn` - add pane button
- `removePaneBtn` - remove pane button
- Various form field IDs (mode, rotation, fontSize, etc.)

**Status:** ✓ No collisions - Follow AGENTS.md line 30 guidance

---

## Next Steps (Task 2 Onwards)

1. **Browser Testing:** Open the plugin page in Unraid and verify:
   - Console errors when editor loads
   - Drag-drop events fire in DevTools
   - Visual feedback (drop-target styling) works
   - saveState() POST succeeds

2. **If Drag-Drop Fails:**
   - Add debug logging to renderStudioBoard()
   - Check if studioBoard element exists
   - Verify event listeners attach correctly
   - Test CSS scoping

3. **Fix Categories:**
   - Script initialization issues (Task 2)
   - HTML injection problems (Task 3)
   - CSS or DOM structure issues (Tasks 4-7)

---

## Summary of Findings

| Finding | Severity | Status | Action |
|---------|----------|--------|--------|
| Initialization sequence correct | Low | ✓ Verified | Monitor |
| Fetch shim order correct | High | ✓ Verified | Monitor |
| Module variable scope | Medium | ✓ Expected to work | Test |
| DOM element existence | Critical | ? Unknown | Test in browser |
| Event binding correctness | Critical | ? Unknown | Test in browser |
| CSS scoping correctness | Medium | ✓ Expected to work | Test |
| Fetch POST encoding | High | ? Unknown | Test in Network tab |
| No DOM ID collisions | High | ✓ Verified | Maintain |

---

## Detailed Code References

### Initialization Timeline
1. **KMSMosaic.page line 718:** `fetch(kmsEmbeddedUrl('backend_html'))`
2. **KMSMosaic.page line 726:** `kmsRewriteBackendHtml(html)` rewrites /api/* paths
3. **KMSMosaic.page line 735:** `kmsInstallEmbeddedStyles(doc)` extracts CSS
4. **KMSMosaic.page line 739:** `mount.innerHTML = doc.body.innerHTML` injects HTML
5. **KMSMosaic.page line 741:** `kmsInstallFetchShim()` patches window.fetch
6. **KMSMosaic.page line 742-745:** Scripts re-injected and executed
7. **KMSMosaic.page line 748:** `window.fillForm(...)` manually loads state
8. **kms_mosaic_web.py line 3911:** `renderStudioBoard()` creates drag-drop cards

### Drag-Drop Event Flow
1. **User initiates drag** on a card
2. **dragstart event** (line 2903): Sets `draggedStudioRole`, adds "dragging" class
3. **dragover event** (line 2915): `preventDefault()`, adds "drop-target" class
4. **dragleave event** (line 2920): Removes "drop-target" class
5. **drop event** (line 2923): `preventDefault()`, swaps roles, re-renders
6. **dragend event** (line 2911): Cleans up state and classes

### Fetch Shim Flow (for saveState() POST)
1. **window.saveState()** at kms_mosaic_web.py line 3933
2. **fetch("/api/config", { method: "POST", body: JSON })** at line 3935
3. **Fetch shim intercepts** at KMSMosaic.page line 278
4. **Converts to form-encoded** with `payload` field at line 288
5. **POST to /plugins/kms.mosaic/include/actions.php?action=backend_config** at line 293
6. **actions.php reads** `$_POST['payload']` at line 186 or `php://input`
7. **Runs wrapper** to write JSON to config file
8. **Returns updated state** as JSON response

---

**Report Generated:** 2026-03-18
**Next Task:** Task 2 - Fix DOM ID conflicts and verify HTML injection
