# Hardware Decoding & Frame Drop Diagnosis Design

**Date:** 2026-03-18
**Status:** Draft
**Scope:** Verify VA-API hardware decoding on AMD 9950X3D iGPU and diagnose random frame drops

## Problem Statement

KMS Mosaic experiences random frame drops during video playback. Interestingly, drops are *less frequent* under high CPU load, suggesting a timing/synchronization issue rather than pure performance constraint. The hwdec setting is currently `auto-copy-safe`, and it's unclear whether the AMD 9950X3D iGPU is actually performing hardware decoding or falling back to CPU decode.

## Success Criteria

1. Confirm whether VA-API hardware decoding is actually being used
2. Compare frame drop frequency with explicit `hwdec=vaapi` vs. current `auto-copy-safe`
3. Identify root cause (hwdec fallback vs. rendering/sync issue)
4. Provide actionable fix (hwdec config update or render path fix)

## Approach

### Phase 1: Logging Infrastructure & Current State Assessment

**Goal:** Understand how mpv logging works in KMS Mosaic and what the current hwdec status is.

**Steps:**
1. **Code inspection** - Find mpv log configuration in the codebase:
   - Search for `mpv-out` parameter handling in `src/options.c` and `src/media.c`
   - Identify where `KMS_MPV_DEBUG` environment variable is used
   - Determine default log path and what messages are available

2. **Remote system check** - SSH to Unraid and inspect current state:
   - Check config file at `/boot/config/kms_mosaic.conf` for current hwdec and mpv-out settings
   - Look for any existing mpv logs from previous runs
   - Verify the running kms_mosaic process and its effective hwdec setting

**Deliverable:** Understanding of logging infrastructure and baseline hwdec status

### Phase 2: VA-API Hardware Decoding Verification

**Goal:** Test whether VA-API can be successfully enabled and used for decoding.

**Steps:**
1. **Enable VA-API explicitly** - Update Unraid config to force VA-API:
   - Set `hwdec=vaapi` in `/boot/config/kms_mosaic.conf` (or via `--mpv-opt hwdec=vaapi`)
   - Enable mpv logging: `--mpv-out /tmp/mpv.log`
   - Restart kms_mosaic

2. **Verify decoder activation** - Check mpv logs for VA-API initialization:
   - Look for patterns like "Using codec: h264 (h264 (VAAPI))" or similar decoder confirmation
   - Check for fallback warnings (e.g., "hwdec: VAAPI not supported")
   - Note any decode errors or unsupported codec messages

3. **Baseline frame drop testing** - Compare drop patterns:
   - Play the same test video(s) for 5+ minutes with VA-API enabled
   - Monitor CPU/GPU usage (via `top`, `watch sensors`, or Unraid dashboard)
   - Document frame drop frequency (visual observation or via `mpv --term-statusline=yes`)
   - Compare with baseline behavior from `auto-copy-safe` setting

**Deliverable:** Confirmation of VA-API working state + frame drop comparison data

### Phase 3: Root Cause Identification & Fix

**Goal:** Determine whether frame drops are caused by hwdec fallback or rendering/synchronization issues.

**Decision points:**

| Scenario | Finding | Next Action |
|----------|---------|-------------|
| VA-API works + drops still occur | Render/sync issue | Inspect `src/frame.c` for FBO reallocation and swap reporting issues |
| VA-API works + drops decrease significantly | hwdec was limiting factor | Update default hwdec to `vaapi`, document in code |
| VA-API fails to initialize | Fallback chain issue | Test fallback options (`vaapi-copy`, driver support), investigate why VAAPI isn't available |
| VA-API initialization indeterminate | Logging insufficient | Enable `KMS_MPV_DEBUG=1` for more verbose output |

**Implementation:**
- If fix is hwdec config: Update `src/media.c` line 90 to use `vaapi` instead of `auto-copy-safe`
- If fix is rendering: Address FBO reallocation in `src/frame.c` for multi-pane scenarios (documented in AGENTS.md line 33)
- If fix is complex: Create follow-up task for deeper GPU sync investigation

**Deliverable:** Actionable code fix or diagnostic report for further investigation

## Technical Details

### MPV Logging Paths
- **Default config:** `/boot/config/kms_mosaic.conf` (Unraid) or `~/.config/kms_mosaic.conf` (other systems)
- **Log output:** Controlled by `--mpv-out <path>` flag, written to file at runtime
- **Debug mode:** `KMS_MPV_DEBUG=1` environment variable enables verbose compositor logging (separate from mpv's own logs)

### Hardware Context
- **GPU:** AMD Ryzen 9 9950X3D with integrated RDNA 2 iGPU
- **Expected hwdec support:** VA-API via `libva` and `libva-amdgpu` or similar AMD driver
- **Current default:** `hwdec=auto-copy-safe` (safe but potentially suboptimal for this hardware)
- **Test target:** `hwdec=vaapi` (direct VA-API, may be faster but less safe if driver has issues)

### Known Issues (from AGENTS.md)
- Multi-pane mpv rendering reuses a single scratch FBO across differently-sized panes, causing repeated reallocations and GPU stalls
- This could contribute to frame drops when multiple media panes are active
- Resolution would require per-pane video FBO allocation strategy

## Rollback Plan

- If VA-API change causes regressions, revert to `auto-copy-safe` via config file (non-destructive)
- If code changes are needed, keep git history for easy revert
- No system-level changes required; changes are app-level only

## Out of Scope

- Shader optimization or upscaling quality improvements
- Terminal pane performance tuning
- Layout studio UI improvements
- Full GPU profiling infrastructure (can be added as follow-up)

