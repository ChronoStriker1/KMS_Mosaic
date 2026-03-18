# Hardware Decoding & Frame Drop Diagnosis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify whether VA-API hardware decoding is active on the AMD 9950X3D iGPU and diagnose the root cause of random frame drops.

**Architecture:** Three sequential diagnostic phases: (1) assess current logging infrastructure and hwdec status on Unraid, (2) enable explicit VA-API and compare frame drop patterns with baseline, (3) analyze results to determine if fix is a config change (hwdec default) or rendering optimization (FBO reallocation).

**Tech Stack:** libmpv, VA-API, Linux DRM/KMS, AMD RDNA 2 iGPU, Unraid OS

---

## File Structure

**Remote system (Unraid):**
- `/boot/config/kms_mosaic.conf` - main config (will be updated for Phase 2)
- `/tmp/mpv.log` - global mpv log (Phase 2, 3)
- `/tmp/mpv-pane-*.log` - per-pane mpv logs (Phase 2, 3)

**Local codebase (conditional changes):**
- `src/media.c:90` - hwdec default (change only if VA-API proves better)
- `src/frame.c:199` - FBO reallocation (change only if rendering is bottleneck)

---

## Phase 1: Logging Infrastructure & Current State Assessment

### Task 1: Access Unraid system and verify SSH connectivity

**Files:** None (remote inspection only)

- [ ] **Step 1: SSH into Unraid**

```bash
ssh root@192.168.2.4
```

Expected: SSH connection successful, you have shell access to Unraid

- [ ] **Step 2: Verify kms_mosaic binary exists and identify version**

```bash
ls -la /usr/local/bin/kms_mosaic*
```

Expected: See `/usr/local/bin/kms_mosaic` wrapper and `/usr/local/bin/kms_mosaic.bin` binary

- [ ] **Step 3: Check if kms_mosaic is currently running**

```bash
pkill -x kms_mosaic.bin -0 && echo "Running" || echo "Not running"
```

Expected: "Running" or "Not running" (either is fine for now)

---

### Task 2: Locate and inspect current config file

**Files:** None (inspection only)

- [ ] **Step 1: Find active config path**

```bash
cat /boot/config/kms_mosaic.conf | head -20
```

Expected: Config file with CLI flags (one per line or space-separated)

- [ ] **Step 2: Check current hwdec setting**

```bash
grep -i "hwdec" /boot/config/kms_mosaic.conf
```

Expected: Either shows `hwdec` line, or empty (meaning default `auto-copy-safe` in code)

- [ ] **Step 3: Check for existing mpv-out log path**

```bash
grep -i "mpv-out" /boot/config/kms_mosaic.conf
```

Expected: Either shows path, or empty (logs may not be enabled)

- [ ] **Step 4: List any existing mpv log files**

```bash
ls -la /tmp/mpv*.log 2>/dev/null || echo "No mpv logs found"
```

Expected: Shows any previous logs or "No mpv logs found"

---

### Task 3: Verify mpv library and VA-API support on system

**Files:** None (inspection only)

- [ ] **Step 1: Check libmpv version**

```bash
ldd /usr/local/bin/kms_mosaic.bin | grep -i mpv
```

Expected: See `libmpv.so.1` or similar, confirming libmpv is linked

- [ ] **Step 2: Verify VA-API libraries are available**

```bash
ldconfig -p | grep -i va
```

Expected: See entries like `libva.so`, `libva-x11.so`, or `libva-drm.so` (confirms VA-API libraries installed)

- [ ] **Step 3: Check GPU driver support for VA-API**

```bash
ls -la /usr/lib*/libva-amdgpu* 2>/dev/null || echo "Checking alternative paths..."
dpkg -l | grep -i libva
```

Expected: See VA-API packages installed (e.g., `libva`, `libva-amdgpu`, or similar AMD driver package)

- [ ] **Step 4: Verify DRM device is accessible**

```bash
ls -la /dev/dri/
```

Expected: See device nodes like `card0`, `renderD128` (needed for VA-API)

---

### Task 4: Document current hwdec status by inspecting running process

**Files:** None (inspection only)

- [ ] **Step 1: Check if kms_mosaic is running and get its PID**

```bash
pgrep -f "kms_mosaic.bin" || echo "Not running"
```

Expected: Shows PID if running, or "Not running"

- [ ] **Step 2: If running, check environment variables**

```bash
ps -e -o pid,cmd | grep kms_mosaic | grep -v grep
cat /proc/$(pgrep -f kms_mosaic.bin | head -1)/environ | tr '\0' '\n' | grep -i mpv
```

Expected: Shows `KMS_MPV_DEBUG` status or empty (not set)

- [ ] **Step 3: Summary - Document Phase 1 findings**

Record the following for Phase 2:
- Current hwdec setting (from config or assumed default `auto-copy-safe`)
- Whether mpv logs are currently enabled
- Whether VA-API libraries are installed
- DRM device availability

Expected: All checks pass; if any fail, note the issue for troubleshooting

---

## Phase 2: VA-API Hardware Decoding Verification

### Task 5: Backup current config and enable VA-API + logging

**Files:** `/boot/config/kms_mosaic.conf` (backup and modify)

- [ ] **Step 1: Backup current config**

```bash
cp /boot/config/kms_mosaic.conf /boot/config/kms_mosaic.conf.backup
echo "Backup saved to kms_mosaic.conf.backup"
```

Expected: Backup file created

- [ ] **Step 2: Enable VA-API and global mpv logging in config**

Add these lines to `/boot/config/kms_mosaic.conf` (use `nano` or `vi`):

```
--mpv-opt hwdec=vaapi
--mpv-out /tmp/mpv.log
```

Or if you need to update an existing `--mpv-opt` line, combine them:
```
--mpv-opt hwdec=vaapi --mpv-opt video-sync=display-resample
```

Execute on Unraid:
```bash
# Open editor (using nano for simplicity)
nano /boot/config/kms_mosaic.conf

# Or append if your config doesn't have these already:
echo "--mpv-opt hwdec=vaapi" >> /boot/config/kms_mosaic.conf
echo "--mpv-out /tmp/mpv.log" >> /boot/config/kms_mosaic.conf

# Verify the changes
cat /boot/config/kms_mosaic.conf | tail -5
```

Expected: Config file shows new `--mpv-opt hwdec=vaapi` and `--mpv-out /tmp/mpv.log` lines

- [ ] **Step 3: For multi-pane diagnosis, add per-pane mpv log outputs (if using panes)**

If config uses multi-pane media (e.g., `--pane-media 2`, `--pane-media 3`):

```bash
echo "--pane-mpv-out 2 /tmp/mpv-pane-2.log" >> /boot/config/kms_mosaic.conf
echo "--pane-mpv-out 3 /tmp/mpv-pane-3.log" >> /boot/config/kms_mosaic.conf
```

Expected: Per-pane logging lines added (only if your setup uses multiple media panes)

---

### Task 6: Stop kms_mosaic, clear old logs, and restart with new config

**Files:** None (process management only)

- [ ] **Step 1: Stop kms_mosaic gracefully**

```bash
pkill -x kms_mosaic.bin
sleep 2
pkill -9 -f kms_mosaic.bin 2>/dev/null || true
```

Expected: Process stops; no errors

- [ ] **Step 2: Clear old log files**

```bash
rm -f /tmp/mpv*.log
ls -la /tmp/mpv*.log 2>/dev/null || echo "Old logs cleared"
```

Expected: Old logs removed (or "Old logs cleared" message)

- [ ] **Step 3: Start kms_mosaic via plugin service or wrapper**

```bash
# Option A: Start via plugin service (preferred)
/usr/local/bin/kms_mosaic

# Or Option B: If plugin service available:
# systemctl start kms_mosaic  (if configured)
```

Wait for it to start:
```bash
sleep 3
pgrep -f "kms_mosaic.bin" && echo "Started successfully" || echo "Failed to start"
```

Expected: kms_mosaic starts and PID is visible

- [ ] **Step 4: Verify logging is active**

```bash
sleep 2  # Give it time to write logs
ls -la /tmp/mpv*.log 2>/dev/null
wc -l /tmp/mpv.log 2>/dev/null || echo "Log not yet created"
```

Expected: Log file(s) created with content (line count > 0)

---

### Task 7: Play test video and capture VA-API initialization messages

**Files:** None (testing/inspection only)

- [ ] **Step 1: Play a test video (5+ minutes)**

Start playing a video via the UI, config, or playlist. Ensure it runs for at least 5 minutes to gather adequate frame drop statistics.

```bash
# Option: Use the Unraid plugin web UI to start playback
# Or manually queue video if using a FIFO:
# echo "/path/to/test/video.mp4" > /path/to/playlist.m3u
```

Expected: Video plays for 5+ minutes

- [ ] **Step 2: While video is playing, check mpv log for VA-API confirmation**

In another SSH session (or after video finishes):

```bash
tail -50 /tmp/mpv.log | grep -i "vaapi\|hwdec\|codec"
```

Expected: Look for messages like:
- `"hwdec: vaapi"` (confirms VA-API selected)
- `"Using codec: h264 (h264 (VAAPI))"` or similar (confirms hardware decode)
- `"VAAPI not supported"` or fallback message (indicates failure)

- [ ] **Step 3: For multi-pane setups, check per-pane logs**

```bash
for f in /tmp/mpv-pane-*.log; do
  echo "=== $f ==="
  tail -20 "$f" | grep -i "vaapi\|hwdec\|codec" || echo "No VA-API matches"
done
```

Expected: Each pane log shows VA-API status (should be consistent across all panes)

- [ ] **Step 4: Record VA-API status**

Document:
- Is VA-API initialized? (yes/no/error)
- Which codec(s) use VA-API? (h264, h265, vp9, etc.)
- Any fallback messages or errors?

If VA-API fails to initialize, save full log for troubleshooting:
```bash
cp /tmp/mpv.log /tmp/mpv.log.vaapi-failed
```

---

### Task 8: Baseline frame drop comparison

**Files:** None (observation/metrics only)

- [ ] **Step 1: Visual observation of frame drops with VA-API**

During the 5+ minute test playback:
- Watch for visual stutter or juddering in the video
- Note if frame drops are frequent, rare, or absent
- Observe CPU/GPU load (use Unraid dashboard or SSH into another terminal and run `top`, `watch sensors`)

Document:
- Estimated drop frequency: (none / rare / occasional / frequent / constant)
- CPU load: (low / medium / high)
- GPU utilization (if visible): (low / medium / high)

- [ ] **Step 2: Compare with baseline `auto-copy-safe` behavior**

Temporarily revert to test baseline:

```bash
# Comment out VA-API line or restore backup
cp /boot/config/kms_mosaic.conf.backup /boot/config/kms_mosaic.conf
# Or manually remove the --mpv-opt hwdec=vaapi line

# Restart
pkill -x kms_mosaic.bin
sleep 2
/usr/local/bin/kms_mosaic
sleep 3

# Play same test video for 5+ minutes
```

Document:
- Estimated drop frequency with `auto-copy-safe`: (none / rare / occasional / frequent / constant)
- Compare to VA-API results

- [ ] **Step 3: Restore VA-API config**

```bash
# Put back VA-API config
# (Manually re-add lines or overwrite with updated version)
nano /boot/config/kms_mosaic.conf
# Re-add: --mpv-opt hwdec=vaapi
```

---

## Phase 3: Root Cause Identification & Decision

### Task 9: Analyze logs and determine fix type

**Files:** None (analysis only)

- [ ] **Step 1: Compare frame drop results**

Create a simple comparison table:

| Setting | Drop Frequency | CPU Load | Notes |
|---------|---|---|---|
| auto-copy-safe | [result] | [result] | baseline |
| hwdec=vaapi | [result] | [result] | test |

Expected: One of these patterns:
- A: VA-API works AND drops decrease → config fix
- B: VA-API works BUT drops persist → rendering issue
- C: VA-API fails → driver/compatibility issue

- [ ] **Step 2: If VA-API works and drops improve (Pattern A)**

VA-API is better. Proceed to Task 10 (config fix).

Decision: **Update default hwdec to `vaapi` in code**

- [ ] **Step 3: If VA-API works but drops persist (Pattern B)**

VA-API initialization is fine, but rendering has issues. Likely culprits:
- FBO reallocation stalls in `src/frame.c:199` (see AGENTS.md line 33)
- Multi-pane GPU state corruption
- Missing or inefficient GPU sync

Decision: **Investigate rendering path in Task 11**

Enable detailed profiling:

```bash
# Add glFinish() timing to frame.c debug output
# Check FBO reallocation frequency in logs
KMS_MPV_DEBUG=1 /usr/local/bin/kms_mosaic 2>&1 | tee /tmp/kms-debug.log &
# Play video for 2-3 minutes
# Kill and analyze: grep "FBO\|glFinish\|render_gl_ensure" /tmp/kms-debug.log
```

- [ ] **Step 4: If VA-API fails to initialize (Pattern C)**

VA-API not available or not working. This is a driver/library issue.

Decision: **Document findings and recommend:**
- Check if libva-amdgpu is installed
- Test with fallback `hwdec=vaapi-copy` instead
- May need Mesa driver update on Unraid

For now, keep `auto-copy-safe` as safe fallback.

---

### Task 10: Config Fix (if Pattern A: VA-API works and improves drops)

**Files:** `src/media.c:90`

- [ ] **Step 1: Update default hwdec in code**

Read current code:

```bash
grep -A2 -B2 "hwdec.*auto-copy-safe" src/media.c
```

Expected: Shows line 90 with `mpv_set_option_string(m->mpv, "hwdec", "auto-copy-safe");`

- [ ] **Step 2: Change default to `vaapi`**

Edit `src/media.c`:

```c
// Old:
if (!user_set_hwdec) mpv_set_option_string(m->mpv, "hwdec", "auto-copy-safe");

// New:
if (!user_set_hwdec) mpv_set_option_string(m->mpv, "hwdec", "vaapi");
```

- [ ] **Step 3: Verify change**

```bash
grep -A2 -B2 "hwdec.*vaapi" src/media.c
```

Expected: Shows updated line with `"vaapi"`

- [ ] **Step 4: Rebuild for Unraid**

```bash
# Build the Linux package
make clean
make

# Or use container build
scripts/macos_build_pkg.sh

# Build Unraid plugin artifacts
scripts/build_unraid_plugin.sh
```

Expected: Binary and plugin artifacts created in `dist/`

- [ ] **Step 5: Test on Unraid**

Deploy new binary and test:

```bash
# Copy new binary to Unraid (or reinstall plugin)
scp dist/kms_mosaic-2026.03.18-x86_64-1.txz root@192.168.2.4:/tmp/
# SSH in and extract/install, or use plugin update path
```

Play test video again with new binary:

```bash
pkill -x kms_mosaic.bin
sleep 2
/usr/local/bin/kms_mosaic
```

Verify drops are still low (should be same or better as config test).

- [ ] **Step 6: Commit code change**

```bash
git add src/media.c
git commit -m "Default hwdec to vaapi for AMD RDNA 2 iGPU

AMD 9950X3D iGPU testing showed VA-API hwdec reduces frame drops
significantly vs. auto-copy-safe. Explicit vaapi is safe on this
hardware and provides better performance."
```

---

### Task 11: Rendering Fix (if Pattern B: VA-API works but drops persist)

**Files:** `src/frame.c:199`, `src/render_gl.c:146-150`

This task is conditional and requires deeper investigation.

- [ ] **Step 1: Inspect FBO reallocation frequency**

Add debug instrumentation to `src/render_gl.c`:

```c
// In render_gl_ensure_video_rt() at line 146:

void render_gl_ensure_video_rt(render_gl_ctx *ctx, int w, int h) {
    static int realloc_count = 0;  // Add this
    if (ctx->vid_tex && ctx->vid_w == w && ctx->vid_h == h) return;

    // Add debug output:
    if (ctx->vid_tex) {  // Only log if actually reallocating
        fprintf(stderr, "DEBUG: FBO realloc #%d: %dx%d -> %dx%d\n",
                ++realloc_count, ctx->vid_w, ctx->vid_h, w, h);
    }

    render_gl_delete_target(&ctx->vid_tex, &ctx->vid_fbo);
    ctx->vid_w = w;
    ctx->vid_h = h;
    // ... rest of function
}
```

- [ ] **Step 2: Rebuild and test with debug output**

```bash
make clean && make
```

Run kms_mosaic with multi-pane video playback:

```bash
KMS_MPV_DEBUG=1 /usr/local/bin/kms_mosaic 2>&1 | grep -E "FBO|render_gl" | head -30
```

Expected: See how many FBO reallocations occur during playback. High frequency = performance issue.

- [ ] **Step 3: If FBO reallocations are frequent (>1 per second)**

The issue is reallocation stalls. Consider optimization:

**Option A (Simple):** Skip reallocation if size change is small:

```c
// In render_gl_ensure_video_rt():
// Old: if (ctx->vid_tex && ctx->vid_w == w && ctx->vid_h == h) return;
// New: Add small tolerance
#define REALLOC_THRESHOLD 16  // pixels
if (ctx->vid_tex &&
    abs(ctx->vid_w - w) < REALLOC_THRESHOLD &&
    abs(ctx->vid_h - h) < REALLOC_THRESHOLD) {
    return;  // Skip reallocation for small size changes
}
```

**Option B (Better):** Allocate separate FBO per pane in multi-pane mode:

Modify `src/frame.c:199` loop to use pane-specific FBO allocation instead of shared `rg->vid_fbo`.

This requires more refactoring. Defer if Option A solves the issue.

- [ ] **Step 4: Test fix**

Rebuild, run multi-pane video test for 5+ minutes:

```bash
make clean && make
/usr/local/bin/kms_mosaic  # Start with optimized code
# Play multi-pane video
# Compare drop frequency
```

Expected: Drop frequency decreases or stays low.

- [ ] **Step 5: Commit optimization**

```bash
git add src/render_gl.c src/frame.c
git commit -m "Reduce FBO reallocation overhead in multi-pane rendering

Skip texture/FBO reallocation for small pane size changes (<16px),
reducing GPU stalls when pane geometry changes slightly during layout
or fullscreen transitions. Addresses frame drops in multi-pane scenarios."
```

---

### Task 12: Validation & Documentation

**Files:** None (testing/reporting)

- [ ] **Step 1: Final test with fix**

Deploy the fixed binary (either Task 10 or Task 11) to Unraid:

```bash
# Build and deploy
scripts/build_unraid_plugin.sh
# Deploy to Unraid via plugin install or manual binary update
```

Play 10+ minute test video in typical scenario:

```bash
# Observe for frame drops
# Monitor CPU/GPU (watch top, sensors)
```

Document:
- Drop frequency before fix: [baseline]
- Drop frequency after fix: [result]
- Improvement: [% reduction or "eliminated"]

- [ ] **Step 2: Test with different pane layouts**

If multi-pane was factor:

```bash
# Test single-pane: video only
# Test dual-pane: video + 1 terminal
# Test multi-pane: video + 3+ terminals
```

Verify drops are low in all configs.

- [ ] **Step 3: Update AGENTS.md if needed**

If fix addresses an issue, add a note to `AGENTS.md`:

```markdown
- Fixed: Reduced FBO reallocation stalls in multi-pane rendering by [method].
  Frame drops should no longer occur randomly; VA-API hwdec now default.
  Test video playback in multi-pane layouts if issues recur.
```

- [ ] **Step 4: Final commit and summary**

```bash
git log --oneline | head -5  # View commits made
```

Write summary:

```
## Diagnosis Summary

Root cause: [hwdec fallback / FBO reallocation / other]
Fix applied: [config change to vaapi / FBO optimization / none]
Result: Frame drops [eliminated / reduced X% / persist - needs further investigation]

Test: [duration] minutes of multi-pane playback
Drop frequency: [before] → [after]
```

---

## Success Criteria

✅ VA-API hardware decoding confirmed active (decoder messages in mpv log)
✅ Frame drop frequency measured before and after VA-API
✅ Root cause identified (hwdec fallback vs. rendering issue)
✅ Fix applied (config change and/or code optimization)
✅ Fix validated with 10+ minute test run
✅ Code changes committed with clear commit messages

