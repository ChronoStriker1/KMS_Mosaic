# Live View & Layout Studio Frame — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the fixed 600px Live View & Layout Studio frame layout to a responsive flex-based layout with proportional gap, equal heights, and no content overflow.

**Architecture:** Modify only the CSS in the KMSMosaic.page file to change the `.kms-live-studio` container from grid to flex, adjust frame sizing with `flex: 1`, and use percentage-based gap for responsiveness. Content sizing (preview video and studio board) already uses aspect-ratio; CSS changes ensure they fit without overflow.

**Tech Stack:** HTML/CSS only (no JavaScript changes needed)

---

## File Structure

**Single file to modify:**
- `/Users/chronostriker1/git/KMS_Mosaic/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`
  - Lines 42–82: `.kms-live-studio` and related frame styles
  - No new files needed; purely CSS-based changes

---

## Implementation Tasks

### Task 1: Update Container Layout (`.kms-live-studio`)

**Files:**
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page:42-51`

**Current CSS:**
```css
.kms-live-studio {
  display: grid !important;
  grid-template-columns: 1fr 1fr !important;
  gap: 0 !important;
  padding: 12px !important;
  align-items: stretch !important;
  align-content: stretch !important;
  height: 600px !important;
  min-height: 600px !important;
}
```

- [ ] **Step 1: Replace container display from grid to flex**

Replace lines 42–51 with:
```css
.kms-live-studio {
  display: flex !important;
  flex-direction: row !important;
  gap: 2% !important;
  padding: 0 !important;
  align-items: stretch !important;
  justify-content: space-between !important;
  min-height: 60vh !important;
  height: auto !important;
}
```

**Rationale:**
- `display: flex` + `flex-direction: row` arranges frames horizontally with equal distribution
- `gap: 2%` scales proportionally with container width (responsive)
- `padding: 0` removes fixed padding; gap handles spacing
- `min-height: 60vh` ties height to viewport, not fixed pixels
- `height: auto` lets content determine height (no overflow forcing)
- Removed `grid-template-columns`, `align-content`, and fixed `height` properties

- [ ] **Step 2: Verify CSS syntax and structure**

The CSS block should now have 7 properties (no grid properties remaining).

---

### Task 2: Update Frame Sizing (`.kms-preview-frame` & `.kms-studio-frame`)

**Files:**
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page:52-64`

**Current CSS:**
```css
.kms-preview-frame,
.kms-studio-frame {
  padding: 0 !important;
  margin: 0 !important;
  min-height: 0 !important;
  height: 100% !important;
  width: 100% !important;
  display: flex !important;
  flex-direction: column !important;
  align-items: stretch !important;
  justify-content: flex-start !important;
  overflow: hidden !important;
}
```

- [ ] **Step 1: Add flex: 1 to frames and adjust sizing**

After line 52 (`.kms-studio-frame {`), add the following before the closing `}`  at line 64:
```css
.kms-preview-frame,
.kms-studio-frame {
  padding: 0 !important;
  margin: 0 !important;
  min-height: 0 !important;
  height: 100% !important;
  width: auto !important;
  flex: 1 !important;
  display: flex !important;
  flex-direction: column !important;
  align-items: stretch !important;
  justify-content: flex-start !important;
  overflow: hidden !important;
}
```

**Changes:**
- `width: auto` (was `100%`) — let flex determine width
- Added `flex: 1` — equal width distribution between frames
- Keep `height: 100%` — frames stretch to container height (ensures equal heights)
- Keep `overflow: hidden` — prevents content spill

- [ ] **Step 2: Verify frame widths are no longer fixed**

Both frames should now use `width: auto` and `flex: 1`.

---

### Task 3: Remove Preview Frame Border Separator

**Files:**
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page:65-71`

**Current CSS:**
```css
.kms-preview-frame {
  border-right: 1px solid var(--box_border, rgba(0,0,0,.12));
  margin-right: 6px;
}
.kms-studio-frame {
  margin-left: 6px;
}
```

- [ ] **Step 1: Remove border and margin overrides from preview/studio**

Replace lines 65–71 with:
```css
.kms-preview-frame {
  border: none !important;
  margin: 0 !important;
}
.kms-studio-frame {
  border: none !important;
  margin: 0 !important;
}
```

**Rationale:**
- Remove `border-right` — gap (container's 2%) now provides visual separation
- Remove `margin-right` and `margin-left` (both 6px) — gap handles spacing responsively
- Set `border: none` and `margin: 0` explicitly to override any conflicting rules

- [ ] **Step 2: Verify no borders or margins remain**

Both frame selectors should have only `border: none` and `margin: 0`.

---

### Task 4: Add Responsive Breakpoints (Tablet & Mobile)

**Files:**
- Modify: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page:72-82` (append after media queries)

**Current media queries:**
```css
@media (max-width: 1200px) {
  .kms-live-studio {
    grid-template-columns: 1fr;
  }
  .kms-preview-frame {
    border-right: none;
    border-bottom: 1px solid var(--box_border, rgba(0,0,0,.12));
  }
}
```

- [ ] **Step 1: Update max-width 1200px breakpoint for flex layout**

Replace the existing `@media (max-width: 1200px)` block with:
```css
@media (max-width: 1200px) {
  .kms-live-studio {
    flex-direction: column !important;
    gap: 2% !important;
  }
  .kms-preview-frame {
    border: none !important;
    margin-bottom: 0 !important;
  }
}
```

**Rationale:**
- Switch `flex-direction: column` to stack frames vertically on tablets
- Keep `gap: 2%` but now it's vertical gap (2% of container height)
- Remove border and margin rules (no longer needed)

- [ ] **Step 2: Add max-width 768px breakpoint for mobile**

After the `@media (max-width: 1200px)` block, add:
```css
@media (max-width: 768px) {
  .kms-live-studio {
    flex-direction: column !important;
    min-height: 90vh !important;
    gap: 2% !important;
  }
  .kms-preview-frame,
  .kms-studio-frame {
    height: 40vh !important;
  }
}
```

**Rationale:**
- Ensure mobile devices use stacked layout
- Give each frame a fixed 40vh height on mobile for predictable behavior
- Increase total container height to accommodate two stacked frames
- Maintain 2% gap between them

- [ ] **Step 3: Verify all media query ranges are covered**

Should have:
- Default (no breakpoint): desktop flex layout
- 768px–1200px: tablet stacked layout
- < 768px: mobile stacked layout with explicit heights

---

### Task 5: Commit CSS Changes

**Files:**
- Modified: `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`

- [ ] **Step 1: Review changes before commit**

Run:
```bash
git diff unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
```

Expected: Should show 4 CSS blocks modified (container, frames, borders/margins, media queries). No other changes.

- [ ] **Step 2: Stage the file**

```bash
git add unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
```

- [ ] **Step 3: Commit with message**

```bash
git commit -m "fix: convert Live View & Layout Studio frame to responsive flex layout

- Change .kms-live-studio from grid (height: 600px) to flex (min-height: 60vh)
- Use gap: 2% for proportional spacing instead of fixed padding
- Set .kms-preview-frame and .kms-studio-frame to flex: 1 for equal heights
- Remove border-right separator; gap now provides visual separation
- Add responsive breakpoints: tablet (flex-column), mobile (explicit heights)
- Frames now scale smoothly with browser resize, no overflow or cutoff

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

---

### Task 6: Test on Local Browser (if available)

**Files:**
- Test: Visual inspection in browser

- [ ] **Step 1: Start local web server (if applicable)**

If you have a local Unraid instance or can serve the page:
```bash
# Option A: Simple Python server (if serving from repo)
python3 -m http.server 8000 --directory .

# Then open: http://localhost:8000/unraid-plugin/package-root/...
```

- [ ] **Step 2: Test at desktop width (1200px+)**

Open browser DevTools (F12) → resize to desktop width
- [ ] Preview frame and studio frame are side-by-side
- [ ] Both frames are same height
- [ ] Gap between frames is visible and proportional
- [ ] No overflow at bottom
- [ ] Content (video and board) visible without clipping

- [ ] **Step 3: Test at tablet width (768px–1199px)**

Resize browser to 900px width
- [ ] Frames stack vertically
- [ ] Gap between frames is proportional
- [ ] Both frames fill width
- [ ] No overflow

- [ ] **Step 4: Test at mobile width (< 768px)**

Resize browser to 375px width
- [ ] Frames stack vertically
- [ ] Each frame is 40vh height
- [ ] Gap between frames maintains proportion
- [ ] Content readable

---

### Task 7: Test on Unraid Server

**Files:**
- Test: Live deployment on Unraid server (root@192.168.2.4)

- [ ] **Step 1: Build and package the plugin**

From repo root:
```bash
make clean
make
```

Expected output: `kms_mosaic` binary and plugin package generated in `dist/` or `unraid-plugin/`

- [ ] **Step 2: SSH to Unraid server and backup current plugin**

```bash
ssh root@192.168.2.4

# On Unraid:
cp -r /usr/local/emhttp/plugins/kms.mosaic /usr/local/emhttp/plugins/kms.mosaic.backup
```

- [ ] **Step 3: Deploy updated plugin**

From local machine:
```bash
# Copy updated KMSMosaic.page to Unraid
scp unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page \
  root@192.168.2.4:/usr/local/emhttp/plugins/kms.mosaic/
```

- [ ] **Step 4: View plugin UI via headless-shell**

Open browser to: **http://192.168.2.4:9222**
- Unraid should have the headless-shell running; this provides a remote browser
- Navigate to Utilities → KMS Mosaic in the Unraid UI
- Verify layout changes are applied

- [ ] **Step 5: Test responsive scaling**

In headless-shell, open browser DevTools and test at:
- Desktop width (1200px+): frames side-by-side, equal height
- Tablet width (900px): frames stacked, visible gap
- Mobile width (375px): frames stacked, proportional spacing

- [ ] **Step 6: Test functionality**

- [ ] Start/stop service buttons work
- [ ] Visibility mode radio buttons work
- [ ] Preview frame displays video (if video is playing)
- [ ] Studio board displays pane cards
- [ ] No console errors in DevTools

- [ ] **Step 7: Verify plugin permissions on Unraid**

Check file permissions (should not change with this CSS-only update):
```bash
ssh root@192.168.2.4
ls -la /usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page
```

Expected: `rw-r--r--` or similar (not `------`)

- [ ] **Step 8: Rollback if needed**

If issues occur:
```bash
ssh root@192.168.2.4
cp -r /usr/local/emhttp/plugins/kms.mosaic.backup /usr/local/emhttp/plugins/kms.mosaic
```

---

### Task 8: Final Verification & Cleanup

**Files:**
- Verify: Git status clean, no uncommitted changes

- [ ] **Step 1: Check git status**

```bash
git status
```

Expected: No untracked or modified files (all changes committed)

- [ ] **Step 2: Verify commit history**

```bash
git log --oneline -3
```

Expected: Latest commit shows "fix: convert Live View & Layout Studio frame to responsive flex layout"

- [ ] **Step 3: Document results**

If testing was successful, document:
- Layout renders correctly at all breakpoints
- No overflow or cutoff
- Preview and studio frames are equal height and aligned
- Gap scales proportionally
- Plugin deploys without permission issues

---

## Testing Checklist

- [ ] CSS syntax valid (no errors in browser DevTools console)
- [ ] Desktop (1200px+): frames side-by-side, equal heights, proportional gap
- [ ] Tablet (768px–1199px): frames stacked vertically, gap visible
- [ ] Mobile (< 768px): frames stacked, readable, 40vh each
- [ ] Window resize: layout scales smoothly, no jumping or reflowing
- [ ] Content fit: preview video and studio board scale without overflow
- [ ] Unraid deployment: plugin loads without errors
- [ ] Functionality: all buttons and controls still work
- [ ] Permissions: file permissions unchanged on Unraid server

---

## Success Criteria

✅ No vertical or horizontal overflow in preview/studio frames
✅ Preview and studio frames are equal height, vertically aligned at top
✅ Gap between frames is visible and proportional (2%)
✅ Layout scales smoothly when browser is resized
✅ All spacing is percentage-based (no fixed pixels)
✅ Works on desktop, tablet, and mobile viewports
✅ Plugin deploys to Unraid without permission issues
✅ All existing functionality (start/stop, visibility mode, etc.) remains intact

---

## Next Phase (After This Is Complete)

Once layout frame is fixed and tested, proceed with:
1. Move MPV Queues Section (item 1)
2. Rename "Layout Studio" Section (item 2)
3. Pane Size Markers editable (item 3)
4. Video Number Positioning (item 4)
5. Thumbnail Height (item 5)

Border dragging (item 6) will be implemented last.
