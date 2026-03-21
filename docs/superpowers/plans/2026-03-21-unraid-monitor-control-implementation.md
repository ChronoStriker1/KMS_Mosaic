# Unraid Monitor Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a new standalone Unraid plugin repository at `/Users/chronostriker1/git/unraid-monitor-control` that auto-detects the likely active monitor, exposes a capability-driven monitor control UI, and ships an initial raw DDC backend with room for `ddcutil` and CEC backends.

**Architecture:** Build a small Python monitor-control service behind an Unraid plugin page. The service owns discovery, backend selection, capability normalization, and monitor actions; the plugin page owns target selection, status, controls, and diagnostics. Start with raw DDC and deterministic host-target overrides first, then layer higher-level backends onto the same interface.

**Tech Stack:** Python 3, PHP, vanilla JavaScript, Bash, Unraid plugin packaging, `pytest`

---

## Planned File Structure

### Repository Root

- Create: `/Users/chronostriker1/git/unraid-monitor-control/.gitignore`
  Ignore Python caches, build artifacts, `dist/`, and local test outputs.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/README.md`
  Project overview, local development, plugin packaging, and host deployment notes.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/VERSION`
  Plugin/package version source.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/pyproject.toml`
  Python packaging and test configuration for the monitor-control service.

### Service Code

- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/__init__.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/models.py`
  Dataclasses for monitor targets, capabilities, settings, probe results, and command responses.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/discovery.py`
  Connector, EDID, and I2C candidate discovery plus target auto-selection.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/service.py`
  Backend selection, high-level orchestration, and API-ready responses.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/backends/__init__.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/backends/base.py`
  Backend interface shared by raw DDC, `ddcutil`, and CEC implementations.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/backends/raw_ddc.py`
  Raw DDC packet encoding, checksum logic, read/write helpers, and known-host fallback defaults.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/backends/ddcutil.py`
  Optional `ddcutil` command adapter and output parser.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/backends/cec.py`
  Optional CEC adapter for narrower fallback operations.

### Tool Entry Point

- Create: `/Users/chronostriker1/git/unraid-monitor-control/tools/monitor_control_web.py`
  Small HTTP/CLI bridge used by the Unraid plugin page for probe, state, and action endpoints.

### Unraid Plugin

- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/monitor.control.plg`
  Plugin manifest and install/remove actions.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/README.md`
  Installed plugin summary for the Plugins page.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/VERSION`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/MonitorControl.page`
  Unraid settings page shell and JavaScript UI.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/include/actions.php`
  Plugin-side action proxy and config persistence.
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/bin/monitor_control_web`
  Wrapper script for launching the Python service entry point.

### Packaging

- Create: `/Users/chronostriker1/git/unraid-monitor-control/scripts/build_unraid_plugin.sh`
  Build the plugin `.tgz` and copy the `.plg` into `dist/`.

### Tests

- Create: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_models.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_discovery.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_raw_ddc.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_service.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_unraid_actions_proxy.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_unraid_plugin_metadata.py`

## Task 1: Create The Repository Skeleton

**Files:**
- Create: `/Users/chronostriker1/git/unraid-monitor-control/.gitignore`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/README.md`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/VERSION`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/pyproject.toml`

- [ ] **Step 1: Create the repository and feature branch**

Run:
```bash
mkdir -p /Users/chronostriker1/git/unraid-monitor-control
cd /Users/chronostriker1/git/unraid-monitor-control
git init
git checkout -b codex/initial-scaffold
```

Expected: empty git repository on branch `codex/initial-scaffold`

- [ ] **Step 2: Write the failing metadata/config test**

Create `/Users/chronostriker1/git/unraid-monitor-control/tests/test_unraid_plugin_metadata.py`:

```python
from pathlib import Path


def test_project_root_files_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "README.md").exists()
    assert (root / "VERSION").exists()
    assert (root / "pyproject.toml").exists()
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
python3 -m pytest tests/test_unraid_plugin_metadata.py -v
```

Expected: FAIL because the root files do not exist yet

- [ ] **Step 4: Write minimal repository files**

Add:
- `.gitignore` with Python caches, `.pytest_cache/`, `dist/`, and `__pycache__/`
- `README.md` with project name and short purpose
- `VERSION` with `0.1.0`
- `pyproject.toml` with minimal `pytest` config

- [ ] **Step 5: Run test to verify it passes**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
python3 -m pytest tests/test_unraid_plugin_metadata.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
git add .gitignore README.md VERSION pyproject.toml tests/test_unraid_plugin_metadata.py
git commit -m "feat: scaffold monitor control repository"
```

## Task 2: Add Core Models And Raw DDC Packet Logic

**Files:**
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/__init__.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/models.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/backends/__init__.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/backends/base.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/backends/raw_ddc.py`
- Test: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_models.py`
- Test: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_raw_ddc.py`

- [ ] **Step 1: Write the failing model and checksum tests**

Create `/Users/chronostriker1/git/unraid-monitor-control/tests/test_models.py`:

```python
from monitor_control.models import Capability, MonitorTarget


def test_monitor_target_defaults_to_ddc_address_0x37():
    target = MonitorTarget(bus_path="/dev/i2c-3")
    assert target.ddc_address == 0x37


def test_capability_range_payload_round_trips():
    cap = Capability(key="brightness", label="Brightness", readable=True, writable=True, value_type="range", minimum=0, maximum=100)
    assert cap.minimum == 0
    assert cap.maximum == 100
```

Create `/Users/chronostriker1/git/unraid-monitor-control/tests/test_raw_ddc.py`:

```python
from monitor_control.backends.raw_ddc import build_ddc_write


def test_build_ddc_write_matches_recovered_userscript_power_frame():
    assert build_ddc_write([0x51, 0x81, 0x0C]) == bytes([0x51, 0x81, 0x0C, 0x92])


def test_build_ddc_write_matches_recovered_userscript_brightness_frame():
    assert build_ddc_write([0x51, 0x84, 0x03, 0x10, 0x00, 0x64]) == bytes([0x51, 0x84, 0x03, 0x10, 0x00, 0x64, 0xA8])
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
PYTHONPATH=src python3 -m pytest tests/test_models.py tests/test_raw_ddc.py -v
```

Expected: FAIL because `monitor_control` modules do not exist

- [ ] **Step 3: Write minimal models and raw DDC helpers**

Implement:
- `MonitorTarget`
- `Capability`
- raw checksum function matching the recovered userscript logic
- `build_ddc_write(payload: list[int]) -> bytes`

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
PYTHONPATH=src python3 -m pytest tests/test_models.py tests/test_raw_ddc.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
git add src tests/test_models.py tests/test_raw_ddc.py
git commit -m "feat: add monitor models and raw ddc helpers"
```

## Task 3: Implement Discovery And Backend Selection

**Files:**
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/discovery.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/service.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/backends/ddcutil.py`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/src/monitor_control/backends/cec.py`
- Test: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_discovery.py`
- Test: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_service.py`

- [ ] **Step 1: Write the failing discovery and backend-priority tests**

Create `/Users/chronostriker1/git/unraid-monitor-control/tests/test_discovery.py`:

```python
from monitor_control.discovery import pick_default_target
from monitor_control.models import MonitorTarget


def test_pick_default_target_prefers_connected_target_with_highest_confidence():
    chosen = pick_default_target(
        [
            MonitorTarget(bus_path="/dev/i2c-1", connector="HDMI-A-1", confidence=0.2),
            MonitorTarget(bus_path="/dev/i2c-3", connector="HDMI-A-2", confidence=0.9),
        ]
    )
    assert chosen.bus_path == "/dev/i2c-3"
```

Create `/Users/chronostriker1/git/unraid-monitor-control/tests/test_service.py`:

```python
from monitor_control.models import MonitorTarget
from monitor_control.service import choose_backend


def test_choose_backend_prefers_ddcutil_then_raw_then_cec():
    target = MonitorTarget(bus_path="/dev/i2c-3")
    available = {"ddcutil": False, "raw_ddc": True, "cec": True}
    backend = choose_backend(target, available)
    assert backend == "raw_ddc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
PYTHONPATH=src python3 -m pytest tests/test_discovery.py tests/test_service.py -v
```

Expected: FAIL because the functions are not implemented

- [ ] **Step 3: Implement minimal discovery and backend selection**

Implement:
- `pick_default_target(targets)`
- `choose_backend(target, availability)`
- backend availability probes that are safe when tools are absent

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
PYTHONPATH=src python3 -m pytest tests/test_discovery.py tests/test_service.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
git add src tests/test_discovery.py tests/test_service.py
git commit -m "feat: add monitor discovery and backend selection"
```

## Task 4: Add The Web/CLI Service Entry Point

**Files:**
- Create: `/Users/chronostriker1/git/unraid-monitor-control/tools/monitor_control_web.py`
- Test: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_service.py`

- [ ] **Step 1: Write the failing service-state test**

Append to `/Users/chronostriker1/git/unraid-monitor-control/tests/test_service.py`:

```python
from monitor_control.service import MonitorControlService


def test_service_state_includes_selected_target_and_backend():
    service = MonitorControlService()
    state = service.build_state()
    assert "selected_target" in state
    assert "backend" in state
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
PYTHONPATH=src python3 -m pytest tests/test_service.py::test_service_state_includes_selected_target_and_backend -v
```

Expected: FAIL because `MonitorControlService` or `build_state` is missing

- [ ] **Step 3: Implement minimal service class and entry point**

Implement:
- `MonitorControlService.build_state()`
- CLI entry point that can print current state JSON

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
PYTHONPATH=src python3 -m pytest tests/test_service.py::test_service_state_includes_selected_target_and_backend -v
```

Expected: PASS

- [ ] **Step 5: Commit**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
git add src tools tests/test_service.py
git commit -m "feat: add monitor control service entry point"
```

## Task 5: Scaffold The Unraid Plugin Shell

**Files:**
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/monitor.control.plg`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/README.md`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/VERSION`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/MonitorControl.page`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/include/actions.php`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/bin/monitor_control_web`
- Create: `/Users/chronostriker1/git/unraid-monitor-control/scripts/build_unraid_plugin.sh`
- Test: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_unraid_actions_proxy.py`
- Test: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_unraid_plugin_metadata.py`

- [ ] **Step 1: Write the failing plugin metadata and action proxy tests**

Create `/Users/chronostriker1/git/unraid-monitor-control/tests/test_unraid_actions_proxy.py`:

```python
from pathlib import Path


def test_actions_proxy_contains_default_config_keys():
    actions = (Path(__file__).resolve().parents[1] / "unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/include/actions.php").read_text()
    assert "TARGET_MODE" in actions
    assert "TARGET_BUS" in actions
    assert "TARGET_ADDRESS" in actions
```

Append to `/Users/chronostriker1/git/unraid-monitor-control/tests/test_unraid_plugin_metadata.py`:

```python
def test_plugin_manifest_exists():
    root = Path(__file__).resolve().parents[1]
    assert (root / "unraid-plugin/monitor.control.plg").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
python3 -m pytest tests/test_unraid_plugin_metadata.py tests/test_unraid_actions_proxy.py -v
```

Expected: FAIL because the plugin files do not exist

- [ ] **Step 3: Implement minimal plugin shell**

Implement:
- plugin manifest
- plugin README and version file
- basic settings page skeleton
- `actions.php` with default config keys and request handling placeholders
- wrapper script for launching the Python tool
- build script that packages the plugin bundle into `dist/`

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
python3 -m pytest tests/test_unraid_plugin_metadata.py tests/test_unraid_actions_proxy.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
git add unraid-plugin scripts tests/test_unraid_actions_proxy.py tests/test_unraid_plugin_metadata.py
git commit -m "feat: scaffold unraid monitor control plugin"
```

## Task 6: Wire The Initial Target And Diagnostics UI

**Files:**
- Modify: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/MonitorControl.page`
- Modify: `/Users/chronostriker1/git/unraid-monitor-control/unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/include/actions.php`
- Modify: `/Users/chronostriker1/git/unraid-monitor-control/tools/monitor_control_web.py`
- Test: `/Users/chronostriker1/git/unraid-monitor-control/tests/test_unraid_actions_proxy.py`

- [ ] **Step 1: Write the failing UI contract test**

Append to `/Users/chronostriker1/git/unraid-monitor-control/tests/test_unraid_actions_proxy.py`:

```python
def test_plugin_page_contains_target_and_diagnostics_sections():
    page = (Path(__file__).resolve().parents[1] / "unraid-plugin/package-root/usr/local/emhttp/plugins/monitor.control/MonitorControl.page").read_text()
    assert "Target Monitor" in page
    assert "Diagnostics" in page
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
python3 -m pytest tests/test_unraid_actions_proxy.py::test_plugin_page_contains_target_and_diagnostics_sections -v
```

Expected: FAIL because the page shell does not expose those sections yet

- [ ] **Step 3: Implement the minimal UI and proxy wiring**

Implement:
- target monitor section
- status/diagnostics section
- action endpoints for state and probe
- JavaScript fetch flow from page to actions proxy

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
python3 -m pytest tests/test_unraid_actions_proxy.py::test_plugin_page_contains_target_and_diagnostics_sections -v
```

Expected: PASS

- [ ] **Step 5: Commit**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
git add unraid-plugin tools tests/test_unraid_actions_proxy.py
git commit -m "feat: add monitor target and diagnostics ui"
```

## Task 7: Run Full Verification And Package Build

**Files:**
- Review: `/Users/chronostriker1/git/unraid-monitor-control`

- [ ] **Step 1: Run the full test suite**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
PYTHONPATH=src python3 -m pytest tests -v
```

Expected: all tests PASS

- [ ] **Step 2: Build the plugin bundle**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
./scripts/build_unraid_plugin.sh
```

Expected: `dist/monitor.control.plg` and plugin bundle created without errors

- [ ] **Step 3: Record working-tree status**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
git status --short
```

Expected: clean tree or only expected untracked build artifacts ignored by git

- [ ] **Step 4: Commit**

Run:
```bash
cd /Users/chronostriker1/git/unraid-monitor-control
git add .
git commit -m "feat: build initial unraid monitor control plugin"
```

## Notes For Execution

- The first implementation slice should stop at a working standalone repo with:
  - repository scaffold
  - raw DDC packet generation and host-default target support
  - backend selection
  - service state output
  - Unraid plugin shell
  - target/diagnostics UI shell
- Do not attempt complete VCP read/write coverage or full CEC integration before the scaffold and raw DDC baseline are verified.
- Use TDD strictly for each created behavior.
