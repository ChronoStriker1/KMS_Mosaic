# Unraid Monitor Control Plugin Design

Date: 2026-03-21
Status: Approved in chat
Scope: New standalone Unraid plugin project for monitor control

## Goal

Create a new standalone Unraid plugin project under `/Users/chronostriker1/git` that controls the monitor attached to the Unraid host. The plugin must not depend on `kms_mosaic`, but it should be able to use the same display-identification signals that `kms_mosaic` relies on, such as DRM connector and EDID information, to choose the likely active monitor.

The target control surface is broader than power alone. The plugin should aim to expose power, brightness, contrast, RGB gains, color presets, volume, mute, input selection, and other monitor settings when the connected display and transport actually support them.

## Key Findings

- The previous host-side monitor-control path was recovered from the disabled Unraid userscript at `/boot/config/plugins/user.scripts/scripts/Start kms_mosaic.disabled-by-kms-mosaic-plugin/script`.
- That logic was not CEC-based. It used inline Python to send raw DDC/CI bytes over `/dev/i2c-3` to slave address `0x37`.
- The current Unraid host at `192.168.2.4` does not currently expose `ddcutil`, `cec-client`, `cec-ctl`, `i2cget`, or `i2cset` on `PATH`.
- No installed package entries matching `ddc`, `cec`, `libcec`, or similar were found in `/var/log/packages`.
- The current `kms.mosaic` plugin manifest does not install any monitor-control tooling.

These findings mean the new plugin should treat raw DDC/CI as a first-class fallback, not an emergency hack.

## Requirements

### Functional

- Detect the most likely target monitor automatically.
- Allow manual override of backend, I2C bus, DDC address, and connector choice.
- Prefer DDC/CI for monitor settings.
- Fall back to CEC for commands that may be available there, especially power, input, mute, and volume.
- Show only controls that the selected monitor/backend pair actually supports.
- Allow probing and refreshing supported features without reinstalling the plugin.
- Keep the plugin standalone, with no runtime dependency on `kms_mosaic`.

### Non-Functional

- Follow the existing Unraid plugin packaging style already used in `KMS_Mosaic`.
- Be resilient on hosts that do not have `ddcutil` or `cec-client` installed.
- Make hardware/debug state visible enough that the plugin can be diagnosed from the UI.
- Degrade gracefully when monitor control is partially supported.

## Chosen Approach

Use a capability-driven monitor-control service with three backends:

1. `ddcutil` backend when `ddcutil` is available and functional.
2. Raw DDC/CI backend using direct I2C access when `ddcutil` is absent or insufficient.
3. CEC backend as a narrower fallback for supported commands.

Backend selection should be automatic but overridable per monitor target.

This is the preferred approach because it provides the broadest useful control surface, works on the current host even without preinstalled helper tools, and still leaves room for cleaner integrations when `ddcutil` or CEC utilities are available.

## Alternatives Considered

### Manual-only bus/address configuration

Pros:
- Lowest implementation cost.
- Fewest moving parts in detection.

Cons:
- Fragile after cable, GPU, or monitor changes.
- Pushes too much hardware knowledge onto the user.
- Poor fit for a reusable Unraid plugin.

### `ddcutil`-only backend

Pros:
- Cleaner command surface.
- Easier to reason about when the tool is available.

Cons:
- Does not match the current host reality.
- Risks making the plugin unusable until extra tooling is installed.
- Ignores the already-proven raw DDC/CI path.

## Architecture

The new project will be created as its own repository under:

- `/Users/chronostriker1/git/unraid-monitor-control`

The repository should use an Unraid-native structure parallel to the existing `KMS_Mosaic` plugin:

- `unraid-plugin/<plugin>.plg`
- `unraid-plugin/package-root/usr/local/emhttp/plugins/<plugin>/...`
- `scripts/build_unraid_plugin.sh`
- `dist/`
- `tests/`

The runtime should be split into focused pieces:

- A monitor-discovery layer
- A backend abstraction for `ddcutil`, raw DDC, and CEC
- A capability model that describes what the selected monitor can do
- A command executor that normalizes reads/writes across backends
- A Unraid plugin page and action layer for configuration, probing, and control

## Components

### Discovery

Responsibilities:
- Inspect DRM connector state and EDID details when available.
- Enumerate `/dev/i2c-*` buses and candidate DDC addresses.
- Probe for likely matches between connector/EDID data and DDC targets.
- Persist a preferred target, but allow manual override.

Expected output:
- A list of candidate monitors
- Confidence metadata for each match
- A selected monitor target

### Backend Interface

Common operations:
- `probe_target`
- `read_capabilities`
- `read_setting`
- `write_setting`
- `power_on`
- `power_off`
- `set_input`
- `set_volume`
- `mute`

Each backend must also expose:
- Health status
- Backend-specific error details
- Which controls are supported, unsupported, or unverified

### `ddcutil` Backend

Responsibilities:
- Use `ddcutil` when present for VCP discovery and monitor control.
- Parse command output into normalized capability/setting models.

Use cases:
- Standard VCP brightness/contrast/gain controls
- Presets and input selection when supported
- More reliable capability enumeration than ad hoc guessing

### Raw DDC Backend

Responsibilities:
- Talk directly to `/dev/i2c-*` using Python and `fcntl.ioctl` when needed.
- Support known DDC/CI framing and checksums.
- Implement a targeted subset first, then expand from probed capability support.

Bootstrap knowledge from the recovered userscript:
- Bus: `/dev/i2c-3`
- Address: `0x37`
- Known prior writes exist for brightness, contrast, preset/input-related commands, and power-related behavior

This backend is critical because it preserves compatibility with the host behavior that already worked before the new plugin existed.

### CEC Backend

Responsibilities:
- Use `cec-client` or other usable CEC tooling only if present.
- Focus on power, input selection, mute, and volume.
- Avoid promising settings such as RGB gain or contrast unless the toolchain and monitor clearly support them.

### Capability Model

The UI must not assume every monitor supports every control.

Capability groups should include:
- Power
- Brightness
- Contrast
- RGB gain
- Color preset
- Input source
- Audio volume
- Audio mute
- Miscellaneous supported VCP/transport-specific features

Each capability should carry:
- Readable name
- Internal key
- Read support
- Write support
- Allowed range or enum values
- Source backend
- Confidence or verification state

## Data Flow

1. Plugin loads and reads saved configuration.
2. Discovery scans connectors, EDID data, and I2C buses.
3. The system chooses the best target monitor candidate.
4. Backends probe the selected target in priority order:
   - `ddcutil`
   - raw DDC
   - CEC
5. The capability model is built from the first successful backend data, with supplemental fallback capabilities optionally merged in when safe.
6. The plugin page renders only the verified control groups.
7. User actions invoke backend commands through a normalized action API.
8. Readbacks update the displayed current values and health state.

## UI Design

The Unraid plugin page should have four sections:

### 1. Target

- Auto-detected monitor summary
- EDID/manufacturer/model details when available
- Connector details
- Current backend
- Manual override controls for backend, bus, address, and connector

### 2. Status

- Probe state
- Last command result
- Backend health
- Supported capability summary
- Raw diagnostic snippets for advanced troubleshooting

### 3. Controls

Grouped panels that appear only when supported:
- Power
- Picture
- Color
- Input
- Audio
- Advanced

### 4. Probe and Diagnostics

- Rescan hardware
- Re-read capabilities
- Test read/write actions
- Show raw monitor metadata
- Show transport/backend errors

## Error Handling

Expected failure classes:
- No matching monitor found
- I2C device exists but access fails
- DDC target responds partially
- `ddcutil` binary missing
- CEC tooling missing
- Monitor supports fewer controls than requested
- Connector and DDC target cannot be confidently matched

Handling rules:
- Never render unsupported controls as active controls.
- Return structured errors to the UI with backend and transport details.
- Keep manual override available whenever auto-detection is uncertain.
- Preserve last-known-good target selection when fresh probes fail.
- Make probe results explicit rather than silently falling back.

## Testing Strategy

### Local Structure Tests

- Validate plugin manifest shape
- Validate package-root file presence and permissions
- Validate action-layer request routing
- Validate capability-model normalization
- Validate raw DDC frame generation and checksum logic

### Backend Unit Tests

- `ddcutil` output parsing
- Raw DDC packet encoding/decoding
- Capability merging and filtering
- Manual override resolution

### Unraid Smoke Tests

- Plugin installs cleanly
- Plugin page loads
- Auto-detection finds the current target monitor
- Manual override can force `/dev/i2c-3` and `0x37`
- Raw DDC backend can reproduce a known-good host command path
- Unsupported controls are hidden or disabled with clear explanation

## Initial Milestones

### Milestone 1

- Scaffold standalone Unraid plugin repository
- Implement plugin shell, config file, and build script
- Implement discovery stubs and target persistence

### Milestone 2

- Implement raw DDC backend
- Reproduce known host behavior from recovered userscript
- Add probe and diagnostics UI

### Milestone 3

- Add `ddcutil` backend
- Normalize capabilities and read/write flows
- Render dynamic control groups

### Milestone 4

- Add optional CEC backend
- Add richer monitor status and fallback reporting
- Verify live behavior on the Unraid host

## Open Assumptions

- The current target monitor is still reachable through a DDC/CI path similar to the recovered `/dev/i2c-3` route.
- The monitor supports at least a subset of standard DDC/CI VCP-style settings.
- CEC support, if present, will be narrower than DDC/CI support and should be presented that way.

## Recommendation

Proceed by creating the new standalone repository at `/Users/chronostriker1/git/unraid-monitor-control`, using auto-detection with manual override, raw DDC as a guaranteed fallback path, `ddcutil` as the preferred higher-level backend when available, and CEC as a limited fallback transport rather than the primary monitor-control mechanism.
