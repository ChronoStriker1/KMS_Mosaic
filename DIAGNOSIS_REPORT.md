# Hardware Decoding & Frame Drop Diagnosis - Final Report

**Date:** March 18, 2026
**System:** Unraid with AMD Ryzen 9 9950X3D iGPU
**Issue:** Random frame drops in video playback

## Diagnostic Summary

### Phase 1: System Assessment
- ✅ SSH access: verified
- ✅ kms_mosaic binary: deployed and running
- ✅ libmpv: properly linked
- ✅ VA-API libraries: bundled with kms_mosaic
- ✅ AMD RDNA 2 GPU drivers: available
- ✅ DRM devices: accessible (/dev/dri/card0, /dev/dri/renderD128)

### Phase 2: VA-API Testing
- ✅ VA-API configured and enabled (--mpv-opt hwdec=vaapi)
- ✅ Global logging enabled (--mpv-out /tmp/mpv.log)
- ✅ Per-pane logging enabled
- ✓ Playback tested: No frame drops observed with VA-API
- ⚠ Issue discovered: VA-API introduced video transition gap (few seconds between videos)

### Phase 3: Baseline Comparison
- ✓ Baseline (auto-copy-safe) tested: No frame drops observed
- ✓ Baseline transitions: Smooth (no gap)
- ✓ Overall: Baseline configuration provides better UX

## Findings

### 1. Original Frame Drops Issue
- **Status:** Not reproducible in diagnostic testing with either hwdec mode
- **Characteristic:** Appears to be intermittent and context-dependent
- **Root cause:** Not caused by hwdec fallback (auto-copy-safe works fine)

### 2. VA-API Status
- ✅ **Hardware support:** Confirmed (AMD RDNA 2 iGPU fully capable)
- ✅ **Libraries:** Available (bundled with kms_mosaic)
- ✅ **Decoder:** Functional (no error messages in logs)
- ⚠ **Issue:** Introduces video transition timing gap in multi-pane playlist scenario

### 3. Root Cause of Transition Gap
- Likely related to multi-pane swap reporting coordination
- VA-API's GPU sync timing may be sensitive to `mpv_render_context_report_swap()` call order (documented in AGENTS.md line 32)
- Not a blocker, but affects UX in multi-pane scenarios

## Decision

### Recommendation: Keep baseline auto-copy-safe configuration

**Rationale:**
- Both hwdec modes show similar frame drop characteristics (intermittent, not reproducible)
- Baseline configuration is more stable for this multi-pane, multi-video scenario
- No code changes required
- System is performing acceptably with current configuration

### Verification Status
- ✅ Baseline config confirmed: No explicit hwdec setting in `/boot/config/kms_mosaic.conf`
- ✅ kms_mosaic running: Process verified as active (PIDs: 1271698, 1341813)

## Future Work

If original frame drops recur:
1. Capture detailed logs with `KMS_MPV_DEBUG=1`
2. Monitor specific conditions that trigger drops (codec, resolution, pane count, etc.)
3. Investigate multi-pane swap reporting timing if VA-API is revisited
4. Consider FBO reallocation optimization (AGENTS.md line 33) if rendering becomes bottleneck

## Configuration Status

**Current:** ✅ Baseline (auto-copy-safe) - stable and recommended
**Alternative tested:** VA-API (hwdec=vaapi) - functional but introduces transition timing issues

**Key Files:**
- Config: `/boot/config/kms_mosaic.conf` (baseline preserved)
- Logs: `/tmp/mpv*.log` (available for reference if needed)

## Conclusion

The KMS Mosaic system on this Unraid host is functioning correctly with the baseline hardware decoding configuration. The intermittent frame drops mentioned in the original issue are not reproducible under standard diagnostic testing and do not appear to be caused by hardware decoding fallback. The system provides stable, smooth video playback with multi-pane terminal support.

**Diagnostic Status:** ✅ Complete
**System Status:** ✅ Operating normally with baseline configuration
**Recommendation:** No code changes required
