# Live View & Layout Studio Frame — Responsive Layout Design

**Date:** 2026-03-18
**Status:** Draft
**Scope:** Fix layout, spacing, and vertical alignment of the Live View & Layout Studio frame in the Unraid plugin UI

---

## Problem Statement

The Live View & Layout Studio frame (the dual-panel section showing live preview and layout studio board) has three interconnected issues:

1. **Vertical Overflow & Cutoff** — Content in both the preview frame and studio frame is being clipped at the bottom because the fixed 600px container height is too small
2. **Vertical Misalignment** — The studio frame appears lower than the preview frame, creating visual imbalance
3. **No Visual Gap** — The frames are separated only by a 1px border; there's no breathing room between them
4. **Fixed Pixel Sizing** — Current gap/padding is fixed in pixels, so it doesn't scale with browser window resizing

## Design Goals

- **Responsive & Fluid** — All sizing scales proportionally with viewport; no fixed pixel gaps
- **No Overflow** — Content fits within frames without clipping
- **Balanced Layout** — Preview and studio frames are equal height, equally aligned
- **Visual Clarity** — Clear separation between frames with proportional gap
- **Adaptive** — Works at any browser window size

---

## Solution: Responsive Flex Layout

### Container Changes (`.kms-live-studio`)

**Current State:**
```css
.kms-live-studio {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 !important;
  height: 600px !important;
  padding: 12px !important;
}
```

**New State:**
```css
.kms-live-studio {
  display: flex;
  flex-direction: row;
  gap: 2%;  /* Responsive gap scaled to container width */
  min-height: 60vh;  /* Responsive height relative to viewport */
  padding: 0 !important;  /* Remove padding; let gap control spacing */
  align-items: stretch;
  justify-content: space-between;
}
```

**Rationale:**
- `flex` + `flex-direction: row` ensures frames share width equally and stay aligned
- `gap: 2%` scales with container width; maintains proportional spacing regardless of window size
- `min-height: 60vh` ties container height to viewport, not fixed pixels
- `align-items: stretch` ensures both frames are exactly the same height
- Removed padding; the gap handles frame separation

---

### Frame Changes (`.kms-preview-frame` & `.kms-studio-frame`)

**Current State:**
```css
.kms-preview-frame,
.kms-studio-frame {
  height: 100%;
  width: 100%;
  margin-right: 6px;  /* preview only */
  margin-left: 6px;   /* studio only */
  border-right: 1px solid ...;  /* preview only */
}
```

**New State:**
```css
.kms-preview-frame,
.kms-studio-frame {
  flex: 1;  /* Equal width distribution */
  height: 100%;
  width: auto;  /* Let flex determine width */
  margin: 0 !important;  /* Remove margin; gap handles spacing */
  border: none !important;  /* Remove separator border */
  padding: 0 !important;
  overflow: hidden;  /* Prevent content spillover */
}
```

**Rationale:**
- `flex: 1` makes frames split available width equally
- `height: 100%` keeps frames aligned to container height (no vertical offset)
- `overflow: hidden` clips content that exceeds frame bounds (prevents bottom cutoff appearance while layout adjusts)
- Removing margin/border simplifies spacing model (handled by container gap)

---

### Content Fit (Preview & Studio Board)

**Preview Frame Content:**
- Video element uses `aspect-ratio: 9/16`
- `width: 100%`, `height: 100%`, `object-fit: contain`
- Background: dark (#000 or gradient)
- Centered with flexbox

**Studio Board Content:**
- Grid board uses `aspect-ratio: 9/16`
- Scales to fill available frame space
- Pane cards positioned absolutely within board, respecting proportional layout
- Overflow: hidden to prevent spillover

---

## Responsive Breakpoints

### Desktop (1200px+)
- Gap: 2% of container width
- Frames: 49% width each (2% gap between)
- Height: 60vh

### Tablet (768px–1199px)
- Gap: 2% of container width
- Frames: 49% width each
- Height: 50vh

### Mobile (< 768px)
- Switch to single-column layout (frames stack vertically)
- Gap: 2% of container height (now vertical)
- Each frame: 100% width, 40vh height

---

## Implementation Details

### Files to Modify
- `/unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page`
  - Update `.kms-live-studio` CSS (container)
  - Update `.kms-preview-frame` and `.kms-studio-frame` CSS (frames)
  - Remove inline `margin-right`/`margin-left` overrides
  - Remove `border-right` for preview frame

### CSS Priority
- Use `!important` flags to override any conflicting styles from inherited rules

### Testing Considerations
- Resize browser window at multiple breakpoints — verify frames scale smoothly
- Verify no horizontal/vertical cutoff at any size
- Check that preview and studio frames remain aligned
- Confirm gap scales proportionally
- Test on Unraid server (SSH to root@192.168.2.4) and view via headless-shell Docker at 192.168.2.4:9222

---

## Next Steps

After layout frame is fixed:
1. Move MPV Queues Section (item 1)
2. Rename "Layout Studio" Section (item 2)
3. Pane Size Markers editable (item 3)
4. Video Number Positioning (item 4)
5. Thumbnail Height (item 5)

Border dragging (item 6) will be implemented last.

---

## Acceptance Criteria

✓ No vertical or horizontal overflow in preview/studio frames
✓ Preview and studio frames are equal height, vertically aligned
✓ Gap between frames is visible and proportional
✓ Layout scales smoothly when browser is resized
✓ No fixed-pixel gaps or padding; all spacing is relative
✓ Works on desktop, tablet, and mobile viewports
✓ Plugin deploys without permission issues on Unraid server
