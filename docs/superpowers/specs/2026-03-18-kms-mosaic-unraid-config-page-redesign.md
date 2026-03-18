# KMS Mosaic Unraid Configuration Page Redesign

**Date:** 2026-03-18
**Status:** Design Approved

## Overview

The KMS Mosaic plugin configuration page requires functional improvements and visual/UX restructuring to match Unraid's web interface patterns and provide a cohesive, responsive user experience. The core issue is that the Layout Studio controls (drag-and-drop panes, resize borders, and action buttons) are non-functional due to integration issues introduced during the transition from standalone web UI to Unraid plugin embedding.

## Current State Problems

1. **Layout Studio is non-functional:**
   - Buttons for pane management don't respond
   - Drag-and-drop pane repositioning doesn't work
   - Border dragging for resizing doesn't work
   - Likely caused by JavaScript event binding issues in the embedded context

2. **Visual/UX issues:**
   - UI style doesn't match other Unraid settings pages
   - Spacing is inconsistent (cramped in some areas, wasted in others)
   - Frame styling doesn't match Unraid's design patterns
   - Service controls and layout tools aren't clearly organized

3. **Git context:**
   - The layout and drag-drop functionality worked in the pre-plugin version
   - Previous implementation likely exists in git history and can serve as reference

## Design Solution

### Page Structure

The configuration page will follow a single-column, section-based layout matching Unraid's established patterns (like the Shares page). Sections stack vertically with consistent spacing and collapsible advanced options.

### Section 1: Service Status & Controls

**Content:**
- Live status display showing:
  - kms_mosaic service: running/stopped with PID
  - Preview service: running/stopped with PID
  - Color-coded status indicator (green for running, red for stopped)

- Plugin settings grid (responsive: 2 columns desktop, 1 column mobile):
  - "Start `kms_mosaic` at boot" (enable/disable dropdown)
  - "Start preview service at boot" (enable/disable dropdown)
  - "Preview service port" (text input)
  - "Config path" (text input)
  - Apply button

- Control buttons row:
  - Start, Stop, Restart, Refresh (standard Unraid button sizes)
  - Visibility mode segmented control inline (Neither, No Videos, No Terminals)
  - Positioned to make logical flow

**Styling:**
- Standard Unraid `.kms-panel` with header and body
- Use existing color/spacing vars for consistency
- Responsive grid layout

### Section 2: Layout Studio

**Content:**
- Header: "Layout Studio"
- Full-width preview canvas where:
  - Panes are draggable to reposition
  - Borders are draggable to resize
  - Visual feedback on hover/selection
- Button toolbar below preview with all layout control buttons:
  - Button functionality should be self-explanatory from text labels
  - Buttons trigger pane add/remove/type-change operations
  - Restore functional state from pre-plugin codebase
- Action buttons at bottom:
  - Save Config (orange/primary styling)
  - Reload From Disk

**Styling:**
- Unraid `.kms-panel` wrapper
- Preview area has dark background (#111) with pane cards
- Buttons match Unraid button styling
- Responsive: full-width on all screen sizes

### Section 3: Configuration Editor

**Content:**
- Header: "Configuration"
- Embedded React/JS editor component loaded from backend
- Shows current configuration with live editing
- Integrates with existing fetch shim and state management

**Styling:**
- Unraid `.kms-panel` wrapper
- Embedded content scoped with `#kmsUiMount` selectors
- Existing color overrides applied
- Responsive to container

### Section 4: Advanced (Collapsible)

**Content:**
- `<details>` HTML element
- Labeled "Advanced Settings" or "Raw Configuration"
- Contains raw config textarea for manual editing
- Collapsed by default

**Styling:**
- Uses HTML5 `<details>` disclosure pattern
- Matches Unraid's advanced block styling
- Consistent spacing and typography

## Implementation Approach

1. **Fix Layout Studio functionality:**
   - Audit git history for pre-plugin implementation
   - Restore drag-and-drop event handlers for pane repositioning
   - Restore border-drag event handlers for resizing
   - Restore button click handlers for pane management
   - Test that all interactions respond correctly

2. **Restructure page layout:**
   - Reorganize sections into the stacked layout described above
   - Move settings grid into the status section
   - Separate Layout Studio into its own panel
   - Move editor into dedicated panel
   - Move advanced config to collapsible details

3. **Styling refinements:**
   - Ensure all Unraid panel styling is consistent
   - Fix spacing/padding to avoid cramping and wasted space
   - Remove or adjust any unnecessary empty space
   - Ensure responsive behavior on mobile/tablet
   - Match color palette and button styles to other Unraid pages

4. **Verification:**
   - All buttons functional and responsive
   - Drag-and-drop works smoothly
   - Pane resizing works
   - No console errors
   - Responsive on different screen sizes
   - Visually cohesive with Unraid interface

## Success Criteria

- ✅ All Layout Studio buttons functional and responsive
- ✅ Drag-and-drop pane repositioning works
- ✅ Border dragging for pane resizing works
- ✅ No JavaScript errors in console
- ✅ Page layout matches Unraid design patterns
- ✅ Spacing is balanced (no cramping, no wasted space)
- ✅ Responsive design works on desktop, tablet, mobile
- ✅ Service controls clearly organized
- ✅ Preview images for kms_mosaic and playlists are preserved
- ✅ All functionality from previous plugin version is restored

## Files to Modify

- `unraid-plugin/package-root/usr/local/emhttp/plugins/kms.mosaic/KMSMosaic.page` — Main configuration page (PHP/HTML/CSS/JS)

## Notes

- The previous working implementation likely exists in git history and should be consulted during discovery
- The embedded editor component is loaded from the backend via the fetch shim; styling overrides are applied via `#kmsUiMount` selectors
- Service controls must remain on the main plugin page (not split into separate settings pages)
- The page must behave as a complete standalone plugin surface, not like separate pages bolted together
