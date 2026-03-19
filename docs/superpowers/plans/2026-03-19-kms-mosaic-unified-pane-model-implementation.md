# KMS Mosaic Unified Pane Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the legacy special video slot and make every visible region a normal typed pane across the editor, saved config, and native runtime.

**Architecture:** Convert legacy configs into a unified pane-indexed model at load time, persist only the unified model on save, and refactor the editor and runtime to address panes by pane index rather than `video slot 0 + panes 1..N`. Keep migration logic explicit so existing terminal and `mpv` panes survive the transition with their current content and layout as closely as possible.

**Tech Stack:** Python, vanilla JavaScript embedded by `tools/kms_mosaic_web.py`, C, Bash, Unraid plugin packaging, Playwright

---

## File Structure

**Primary implementation files:**
- `tools/kms_mosaic_web.py`
  Unified pane editor state, legacy-config conversion, selected-pane inspector, queue behavior, remove-last-pane safeguards, and save-path normalization.
- `src/options.h`
  Role-count constants and option model definitions that currently distinguish the video slot from pane slots.
- `src/options.c`
  Config parsing, legacy role semantics, pane count logic, saved config writing, and compatibility translation for old configs.
- `src/layout.c`
  Layout generation and split-tree application that currently reserve slot `0` as video and tile panes from offset `1`.
- `src/app.c`
  Scene storage, role layout copying, overlay/fullscreen bookkeeping, and live runtime assumptions about slot `0`.
- `src/frame.c`
  Render dispatch paths that currently treat main video and pane-local media separately by slot identity.
- `src/ui.c`
  Focus/control-mode handling that still treats video as a non-pane peer.

**Verification files:**
- `tests/test_kms_mosaic_web.py`
  Extend for legacy-config conversion and unified save-path assertions.
- `tests/integration.test.html`
  Extend the manual/browser verification notes for every-pane `Pane Type`, remove-last-pane behavior, and old-config migration expectations.
- `tests/test_render_targets.py`
  Extend only if native role-count changes need source-level regression checks around render assumptions.

**Deployment verification tools:**
- `/tmp/codex-playwright/check_kms_ui.js`
  Reuse or replace with focused checks after plugin-page deploy; verify the live Unraid page rather than trusting file copies.

**Reference documents:**
- `docs/superpowers/specs/2026-03-19-kms-mosaic-unified-pane-model-design.md`

---

## Tasks

### Task 1: Add Unified Pane State Conversion In The Web Editor

**Files:**
- Modify: `tools/kms_mosaic_web.py`
- Test: `tests/test_kms_mosaic_web.py`

**Goal:** Make the web editor load old configs into a unified pane model where `pane_count` means total panes and pane `0` inherits the old global media role.

- [ ] **Step 1: Write the failing conversion test**

Add a test to `tests/test_kms_mosaic_web.py` that feeds `parse_config_text(...)` an old-style config containing:
- global `--video` / `--mpv-opt` / `--panscan`
- pane-local `--pane-video 3 ...`
- terminal pane commands
- a legacy split-tree with leaf ids in the old mixed role space
- legacy role order / permutation state
- a saved fullscreen or focus target
- a saved selected-pane/editor target if one exists in the web state path

Assert that the parsed state:
- has `pane_count` equal to total visible panes
- represents the old global media role as pane `0`
- preserves existing pane-local terminal and `mpv` data
- translates split-tree leaves, role order, and fullscreen/focus targets into the unified pane space
- restores selection against translated pane ids when possible

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
python3 -m unittest /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/test_kms_mosaic_web.py
```

Expected: FAIL because the current parser still stores root media fields separately from `pane_*` arrays.

- [ ] **Step 3: Implement unified state normalization**

Refactor `tools/kms_mosaic_web.py` so:
- `empty_state()` produces pane-indexed state for all visible panes
- `ensure_panes()` treats `pane_count` as total panes
- `parse_config_text()` converts old global media fields into pane `0`
- legacy split-tree ids, role order, fullscreen/focus targets, and selection state are translated into pane indices during load
- `build_config_text()` writes the unified pane model instead of reviving the old parallel root media fields for newly saved configs

Keep explicit legacy-read support so existing configs still load.

- [ ] **Step 4: Run the tests to verify conversion works**

Run:
```bash
python3 -m unittest /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/test_kms_mosaic_web.py
```

Expected: PASS

- [ ] **Step 5: Write the failing round-trip and fallback tests**

Add tests to `tests/test_kms_mosaic_web.py` that:
- parse a legacy config, save it with `build_config_text()`, and parse the saved result again
- assert the saved config no longer revives the old special-slot model
- assert translated layout/order/fullscreen data survives the round trip
- feed incomplete or invalid legacy data and assert deterministic fallback:
  - invalid split-tree translation falls back to a safe layout without dropping panes
  - unknown pane types preserve raw settings and fall back safely

- [ ] **Step 6: Run the tests to verify they fail**

Run:
```bash
python3 -m unittest /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/test_kms_mosaic_web.py
```

Expected: FAIL until unified save-path and fallback behavior are implemented.

- [ ] **Step 7: Implement unified save round-tripping and deterministic fallback**

Extend `tools/kms_mosaic_web.py` so:
- migrated configs save back out in unified-pane form
- reparsing a freshly saved config preserves pane content and translated layout state
- invalid split-tree translation falls back to a deterministic safe layout
- unknown pane types preserve raw settings while defaulting to safe editor behavior

- [ ] **Step 8: Run the tests to verify round-tripping and fallback work**

Run:
```bash
python3 -m unittest /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/test_kms_mosaic_web.py
```

Expected: PASS

- [ ] **Step 9: Commit the conversion groundwork**

```bash
git -C /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace add tools/kms_mosaic_web.py tests/test_kms_mosaic_web.py
git -C /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace commit -m "feat: convert legacy configs into unified pane state"
```

### Task 2: Remove The Special Main-Pane Inspector Path

**Files:**
- Modify: `tools/kms_mosaic_web.py`
- Test: `tests/test_kms_mosaic_web.py`
- Verify live with Playwright

**Goal:** Make every pane use one selected-pane inspector path, show `Pane Type` for every pane, and block remove when only one pane remains.

- [ ] **Step 1: Write the failing UI/state test**

Add or extend a Python source-level regression test asserting that:
- the selected-pane rendering no longer branches on `selectedRole === 0` for a dedicated main-media inspector
- remove-pane logic explicitly blocks only when total pane count is `1`

- [ ] **Step 2: Run the targeted tests to verify failure**

Run:
```bash
python3 -m unittest /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/test_kms_mosaic_web.py
```

Expected: FAIL because the current inspector still has a dedicated role-0 branch and remove logic still relies on `selectedRole <= 0`.

- [ ] **Step 3: Implement the unified inspector**

Refactor `tools/kms_mosaic_web.py` so:
- pane naming uses normal pane labels with no fake `Pane C` special slot
- all pane selections flow through one inspector path
- every pane shows `Pane Type`
- pane-local `mpv` controls, including `Panscan`, appear for any `mpv` pane
- queue UI appears for any `mpv` pane
- terminal panes show command controls and no queue section
- remove actions are hidden/disabled when `pane_count === 1`

- [ ] **Step 4: Verify the page live in Playwright**

Use the Playwright login flow against the live Unraid page and confirm:
- clicking any pane shows a `Pane Type` control
- clicking any `mpv` pane shows a `Panscan` control in `Selected Pane`
- the formerly special `Pane C` card no longer behaves as a main-slot exception
- the last remaining pane cannot be removed
- selection restoration still targets the correct pane after reload when the translated pane id can be matched safely

If needed, update `/tmp/codex-playwright/check_kms_ui.js` or run a one-off `node` script, but verify the live page after deploy.

- [ ] **Step 5: Commit the editor unification**

```bash
git -C /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace add tools/kms_mosaic_web.py tests/test_kms_mosaic_web.py tests/integration.test.html
git -C /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace commit -m "feat: unify selected-pane editor across all panes"
```

### Task 3: Refactor Native Role Counts And Config Semantics

**Files:**
- Modify: `src/options.h`
- Modify: `src/options.c`
- Modify: `src/layout.c`
- Test: `tests/test_render_targets.py`

**Goal:** Make the native runtime treat `pane_count` as total panes and stop reserving a special video slot in config/load/save and layout code.

- [ ] **Step 1: Write the failing native regression test**

Extend `tests/test_render_targets.py` with source assertions that fail while:
- `KMS_MOSAIC_SLOT_VIDEO` remains part of the public role-count model
- `role_count` is still computed as `1 + pane_count`
- layout still tiles panes from `KMS_MOSAIC_SLOT_PANE_BASE`
- legacy layout-role translation has no explicit compatibility path

- [ ] **Step 2: Run the native regression test and confirm failure**

Run:
```bash
python3 -m unittest /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/test_render_targets.py
```

Expected: FAIL because the current source still encodes the special slot invariants.

- [ ] **Step 3: Implement the role-model refactor**

Update:
- `src/options.h` to redefine pane/role constants around pane indices
- `src/options.c` to parse/save old configs through a compatibility translation layer while persisting the new semantics
- `src/layout.c` to target pane indices directly and translate old split-tree ids during conversion

Keep old-config read compatibility explicit rather than silently reinterpreting old role numbers.

- [ ] **Step 4: Re-run the native regression test**

Run:
```bash
python3 -m unittest /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/test_render_targets.py
```

Expected: PASS

- [ ] **Step 5: Commit the role/layout refactor**

```bash
git -C /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace add src/options.h src/options.c src/layout.c tests/test_render_targets.py
git -C /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace commit -m "refactor: remove special video slot semantics from config and layout"
```

### Task 4: Update Runtime Rendering, Focus, And Remove-Last-Pane Semantics

**Files:**
- Modify: `src/app.c`
- Modify: `src/frame.c`
- Modify: `src/ui.c`
- Modify: `tools/kms_mosaic_web.py`
- Test: `tests/test_render_targets.py`

**Goal:** Make rendering, focus, fullscreen, and remove behavior operate on unified pane indices and type-driven media/terminal rendering.

- [ ] **Step 1: Write the failing regression assertions**

Extend `tests/test_render_targets.py` with assertions that fail while:
- fullscreen/focus still assume a reserved video role
- frame rendering still handles main video and pane-local media as different role classes

- [ ] **Step 2: Run the regression test to confirm failure**

Run:
```bash
python3 -m unittest /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/test_render_targets.py
```

Expected: FAIL on the old role-specific render/focus assumptions.

- [ ] **Step 3: Implement unified runtime behavior**

Refactor:
- `src/app.c` scene bookkeeping so pane layouts are authoritative
- `src/frame.c` rendering dispatch so pane type determines media rendering
- `src/ui.c` focus/fullscreen behavior so pane indices are peers and overlay logic no longer assumes `video + exactly two panes`
- `tools/kms_mosaic_web.py` remove/add/split logic so it matches the new total-pane semantics

- [ ] **Step 4: Run the regression test again**

Run:
```bash
python3 -m unittest /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/tests/test_render_targets.py
```

Expected: PASS

- [ ] **Step 5: Commit the runtime unification**

```bash
git -C /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace add src/app.c src/frame.c src/ui.c tools/kms_mosaic_web.py tests/test_render_targets.py
git -C /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace commit -m "refactor: unify runtime pane behavior across all roles"
```

### Task 5: Deploy, Verify On Unraid, And Recheck Playback

**Files:**
- Modify as needed from prior tasks
- Verify live on the Unraid host and in Playwright

**Goal:** Prove that old configs auto-convert, the config page behaves correctly, and pane-local playback still works after the unified-pane migration.

- [ ] **Step 1: Rebuild package artifacts**

Run:
```bash
./scripts/macos_build_pkg.sh
```

Expected: `dist/kms_mosaic-2026.03.19-x86_64-1.txz` is created successfully.

- [ ] **Step 2: Deploy to the Unraid host**

Run the existing safe install flow:
```bash
scp /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace/dist/kms_mosaic-2026.03.19-x86_64-1.txz root@192.168.2.4:/tmp/kms_mosaic-2026.03.19-x86_64-1.txz
ssh root@192.168.2.4 'upgradepkg --reinstall --install-new /tmp/kms_mosaic-2026.03.19-x86_64-1.txz'
```

Then restart `kms_mosaic` using the exact-name sequential stop/start pattern from `AGENTS.md`.

- [ ] **Step 3: Verify the configuration page with Playwright**

Check the live page and confirm:
- every pane shows `Pane Type`
- no pane is mislabeled as a fake special slot
- queue behavior follows pane type
- the last remaining pane cannot be removed
- a migrated old config preserves pane placement closely enough that the board shape matches the pre-migration layout
- reload after save keeps the same pane selection when the translated pane can be matched safely

- [ ] **Step 4: Verify runtime behavior on the live host**

Confirm:
- old config content survived auto-conversion
- old layout/screen placement survived auto-conversion closely enough to preserve the intended board arrangement
- terminal and `mpv` panes still render
- pane-local media plays correctly
- playback handoff did not regress during the migration

Use `/tmp/mpv.log` and pane-local logs as needed for `START_FILE -> FILE_LOADED` timing checks.

- [ ] **Step 5: Commit final verification-driven fixes**

```bash
git -C /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace add -A
git -C /Users/chronostriker1/git/KMS_Mosaic/.worktrees/codex-ui-editor-workspace commit -m "feat: unify pane model across editor and runtime"
```
