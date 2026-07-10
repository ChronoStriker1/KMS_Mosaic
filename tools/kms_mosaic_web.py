#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import difflib
import fcntl
from fractions import Fraction
import hashlib
import io
import json
import mimetypes
import os
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs, urlparse
from typing import Any

try:
    import av
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from aiortc.contrib.media import MediaRelay
    from aiortc.mediastreams import MediaStreamError
    from aiortc.rtcrtpsender import RTCRtpSender
except ImportError:  # pragma: no cover
    av = None
    RTCPeerConnection = None
    RTCSessionDescription = None
    VideoStreamTrack = object
    MediaRelay = None
    MediaStreamError = RuntimeError
    RTCRtpSender = None


LAYOUT_NAMES = ["stack", "row", "2x1", "1x2", "2over1", "1over2", "overlay"]
DEFAULT_PANE_COMMANDS = [
    "btop --utf-force",
    "tail -F /var/log/syslog -n 500",
]
KNOWN_PANE_TYPES = {"terminal", "mpv"}
WEB_STATE_PREFIX = "# kms_mosaic_web_state "
THUMB_CACHE_MAX_BYTES = 64 * 1024 * 1024
THUMB_CACHE_MAX_FILES = 512
THUMB_CACHE_MAX_AGE_SEC = 7 * 24 * 60 * 60
DDC_I2C_ADDRESS = 0x37
DDC_I2C_SLAVE_FORCE = 0x0706
DDC_CONTROL_CODES = {
    "brightness": (0x10, 0, 100),
    "contrast": (0x12, 0, 100),
    "input": (0x14, 1, 255),
    "power": (0xD6, 1, 5),
}


def default_config_path() -> str:
    if os.path.exists("/boot/config"):
        return "/boot/config/kms_mosaic.conf"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return str(Path(xdg) / "kms_mosaic.conf")
    home = os.environ.get("HOME", ".")
    return str(Path(home) / ".config" / "kms_mosaic.conf")


def read_raw_config_text(config_path: Path) -> str:
    try:
        return config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def read_state_from_config(config_path: Path) -> dict[str, Any]:
    return parse_config_text(read_raw_config_text(config_path))


def parse_connector_listing(text: str) -> list[dict[str, Any]]:
    connectors: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = re.match(r"^(\d+):\s+([^\s]+)\s+\((connected|disconnected)\)", line)
        if not match:
            continue
        if match.group(3) != "connected":
            continue
        connectors.append({
            "id": match.group(1),
            "name": match.group(2),
            "connected": True,
        })
    return connectors


def list_connectors() -> list[dict[str, Any]]:
    candidates = [
        shutil.which("kms_mosaic"),
        shutil.which("kms_mosaic.bin"),
        "/usr/local/bin/kms_mosaic",
        "/usr/local/bin/kms_mosaic.bin",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [candidate, "--list-connectors"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        output = "\n".join(part for part in [result.stdout, result.stderr] if part)
        connectors = parse_connector_listing(output)
        if connectors:
            return connectors
    return []


def _edid_monitor_name(data: bytes) -> str:
    for offset in range(54, min(len(data), 126), 18):
        descriptor = data[offset:offset + 18]
        if len(descriptor) == 18 and descriptor[:3] == b"\0\0\0" and descriptor[3] == 0xFC:
            return descriptor[5:18].decode("ascii", errors="ignore").strip(" \0\n\r")
    return ""


def list_ddc_monitors(
    sysfs_root: Path = Path("/sys/class/drm"),
    dev_root: Path = Path("/dev"),
) -> list[dict[str, Any]]:
    monitors: list[dict[str, Any]] = []
    for status_path in sorted(sysfs_root.glob("card*-*/status")):
        try:
            if status_path.read_text(encoding="utf-8").strip() != "connected":
                continue
        except OSError:
            continue
        connector_dir = status_path.parent
        connector = connector_dir.name.split("-", 1)[-1]
        ddc_path = connector_dir / "ddc"
        try:
            bus_name = Path(os.path.realpath(ddc_path)).name
        except OSError:
            continue
        if not re.fullmatch(r"i2c-\d+", bus_name):
            continue
        bus_path = dev_root / bus_name
        try:
            edid = (connector_dir / "edid").read_bytes()
        except OSError:
            edid = b""
        model = _edid_monitor_name(edid)
        monitors.append({
            "connector": connector,
            "model": model,
            "label": f"{connector} · {model}" if model else connector,
            "bus": str(bus_path),
            "available": bus_path.exists() and os.access(bus_path, os.R_OK | os.W_OK),
        })
    return monitors


def build_ddc_vcp_message(control: str, value: int) -> bytes:
    if control not in DDC_CONTROL_CODES:
        raise ValueError("Unsupported monitor control")
    code, minimum, maximum = DDC_CONTROL_CODES[control]
    value = int(value)
    if value < minimum or value > maximum:
        raise ValueError(f"{control.capitalize()} must be between {minimum} and {maximum}")
    if control == "power" and value not in (1, 4, 5):
        raise ValueError("Power must be on, off, or standby")
    payload = [0x51, 0x84, 0x03, code, (value >> 8) & 0xFF, value & 0xFF]
    checksum = 0x6E
    for byte in payload:
        checksum ^= byte
    return bytes(payload + [checksum])


def write_ddc_control(bus_path: str, control: str, value: int) -> None:
    if not re.fullmatch(r"/dev/i2c-\d+", bus_path):
        raise ValueError("Invalid DDC bus")
    message = build_ddc_vcp_message(control, value)
    fd = os.open(bus_path, os.O_RDWR)
    try:
        fcntl.ioctl(fd, DDC_I2C_SLAVE_FORCE, DDC_I2C_ADDRESS)
        if os.write(fd, message) != len(message):
            raise OSError("Incomplete DDC/CI write")
    finally:
        os.close(fd)


def write_text_atomic(target_path: Path, text: str) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=target_path.name + ".", dir=str(target_path.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, target_path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def empty_state() -> dict[str, Any]:
    return {
        "connector": "",
        "mode": "",
        "rotation": 0,
        "font_size": 18,
        "right_frac": 33,
        "video_frac": 0,
        "pane_split": 50,
        "split_tree": "",
        "pane_count": 2,
        "layout": "stack",
        "roles": "",
        "fs_cycle_sec": 5,
        "transition_ms": 0,
        "visibility_mode": "neither",
        "pane_types": ["terminal", "terminal"],
        "pane_type_raw": ["", ""],
        "pane_type_settings": [{}, {}],
        "pane_commands": DEFAULT_PANE_COMMANDS.copy(),
        "pane_playlists": ["", ""],
        "pane_playlist_extended": ["", ""],
        "pane_playlist_fifos": ["", ""],
        "pane_mpv_outs": ["", ""],
        "pane_video_rotate": ["", ""],
        "pane_panscan": ["", ""],
        "pane_watchdogs": [0, 0],
        "pane_sync_groups": ["", ""],
        "pane_video_paths": [[], []],
        "pane_mpv_opts": [[], []],
        "video_paths": [],
        "playlist": "",
        "playlist_extended": "",
        "playlist_fifo": "",
        "mpv_out": "",
        "video_rotate": "",
        "panscan": "",
        "flags": {
            "no_video": False,
            "no_panes": False,
            "smooth": False,
            "loop_file": False,
            "loop_playlist": False,
            "shuffle": False,
            "atomic": False,
            "atomic_nonblock": False,
            "gl_finish": False,
            "no_osd": False,
        },
        "mpv_opts": [],
        "focus_pane": -1,
        "fullscreen_pane": -1,
        "selected_pane": -1,
        "extra_lines": "",
    }


def visibility_mode_from_flags(flags: dict[str, Any]) -> str:
    if bool(flags.get("no_video")):
        return "no-video"
    if bool(flags.get("no_panes")):
        return "no-terminal"
    return "neither"


def normalize_visibility_mode(value: Any, default: str = "neither") -> str:
    mode = str(value or "").strip()
    if mode == "no-panes":
        return "no-terminal"
    if mode in {"neither", "no-video", "no-terminal"}:
        return mode
    return default


def ensure_panes(state: dict[str, Any]) -> None:
    pane_count = max(1, int(state.get("pane_count", 2)))
    state["pane_count"] = pane_count
    pane_commands = list(state.get("pane_commands", []))
    pane_types = list(state.get("pane_types", []))
    pane_type_raw = [str(value or "") for value in state.get("pane_type_raw", [])]
    pane_type_settings = [
        dict(value) if isinstance(value, dict) else {}
        for value in state.get("pane_type_settings", [])
    ]
    pane_playlists = list(state.get("pane_playlists", []))
    pane_playlist_extended = list(state.get("pane_playlist_extended", []))
    pane_playlist_fifos = list(state.get("pane_playlist_fifos", []))
    pane_mpv_outs = list(state.get("pane_mpv_outs", []))
    pane_video_rotate = list(state.get("pane_video_rotate", []))
    pane_panscan = list(state.get("pane_panscan", []))
    pane_watchdogs = list(state.get("pane_watchdogs", []))
    pane_sync_groups = list(state.get("pane_sync_groups", []))
    pane_video_paths = [list(paths) for paths in state.get("pane_video_paths", [])]
    pane_mpv_opts = [list(opts) for opts in state.get("pane_mpv_opts", [])]
    while len(pane_commands) < pane_count:
        pane_commands.append(DEFAULT_PANE_COMMANDS[0] if len(pane_commands) == 0 else "")
    while len(pane_types) < pane_count:
        pane_types.append("terminal")
    while len(pane_type_raw) < pane_count:
        pane_type_raw.append("")
    while len(pane_type_settings) < pane_count:
        pane_type_settings.append({})
    while len(pane_playlists) < pane_count:
        pane_playlists.append("")
    while len(pane_playlist_extended) < pane_count:
        pane_playlist_extended.append("")
    while len(pane_playlist_fifos) < pane_count:
        pane_playlist_fifos.append("")
    while len(pane_mpv_outs) < pane_count:
        pane_mpv_outs.append("")
    while len(pane_video_rotate) < pane_count:
        pane_video_rotate.append("")
    while len(pane_panscan) < pane_count:
        pane_panscan.append("")
    while len(pane_watchdogs) < pane_count:
        pane_watchdogs.append(0)
    while len(pane_sync_groups) < pane_count:
        pane_sync_groups.append("")
    while len(pane_video_paths) < pane_count:
        pane_video_paths.append([])
    while len(pane_mpv_opts) < pane_count:
        pane_mpv_opts.append([])
    state["pane_commands"] = pane_commands[:pane_count]
    state["pane_types"] = pane_types[:pane_count]
    state["pane_type_raw"] = pane_type_raw[:pane_count]
    state["pane_type_settings"] = pane_type_settings[:pane_count]
    state["pane_playlists"] = pane_playlists[:pane_count]
    state["pane_playlist_extended"] = pane_playlist_extended[:pane_count]
    state["pane_playlist_fifos"] = pane_playlist_fifos[:pane_count]
    state["pane_mpv_outs"] = pane_mpv_outs[:pane_count]
    state["pane_video_rotate"] = pane_video_rotate[:pane_count]
    state["pane_panscan"] = pane_panscan[:pane_count]
    state["pane_watchdogs"] = [max(0, _safe_int(value, 0)) for value in pane_watchdogs[:pane_count]]
    state["pane_sync_groups"] = [str(value or "") for value in pane_sync_groups[:pane_count]]
    state["pane_video_paths"] = pane_video_paths[:pane_count]
    state["pane_mpv_opts"] = pane_mpv_opts[:pane_count]


def _safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_saved_pane(value: Any, pane_count: int) -> int:
    pane = _safe_int(value, -1)
    return pane if 0 <= pane < pane_count else -1


def _parse_web_state_comment(stripped_line: str) -> dict[str, Any] | None:
    if not stripped_line.startswith(WEB_STATE_PREFIX):
        return None
    payload = stripped_line[len(WEB_STATE_PREFIX):].strip()
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _safe_metadata_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _pane_type_payload(state: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "type": state["pane_types"][index],
        "raw": state["pane_type_raw"][index],
        "settings": _safe_metadata_mapping(state["pane_type_settings"][index]),
        "command": state["pane_commands"][index],
        "playlist": state["pane_playlists"][index],
        "playlist_extended": state["pane_playlist_extended"][index],
        "playlist_fifo": state["pane_playlist_fifos"][index],
        "mpv_out": state["pane_mpv_outs"][index],
        "video_rotate": state["pane_video_rotate"][index],
        "panscan": state["pane_panscan"][index],
        "watchdog": state["pane_watchdogs"][index],
        "sync_group": state["pane_sync_groups"][index],
        "video_paths": list(state["pane_video_paths"][index]),
        "mpv_opts": list(state["pane_mpv_opts"][index]),
    }


def _write_pane_payload(state: dict[str, Any], index: int, payload: dict[str, Any]) -> None:
    state["pane_types"][index] = str(payload.get("type") or "terminal")
    state["pane_type_raw"][index] = str(payload.get("raw") or "")
    state["pane_type_settings"][index] = _safe_metadata_mapping(payload.get("settings"))
    state["pane_commands"][index] = str(payload.get("command") or "")
    state["pane_playlists"][index] = str(payload.get("playlist") or "")
    state["pane_playlist_extended"][index] = str(payload.get("playlist_extended") or "")
    state["pane_playlist_fifos"][index] = str(payload.get("playlist_fifo") or "")
    state["pane_mpv_outs"][index] = str(payload.get("mpv_out") or "")
    state["pane_video_rotate"][index] = str(payload.get("video_rotate") or "")
    state["pane_panscan"][index] = str(payload.get("panscan") or "")
    state["pane_watchdogs"][index] = max(0, _safe_int(payload.get("watchdog"), 0))
    state["pane_sync_groups"][index] = str(payload.get("sync_group") or "")
    state["pane_video_paths"][index] = list(payload.get("video_paths") or [])
    state["pane_mpv_opts"][index] = list(payload.get("mpv_opts") or [])


def _ensure_pane_media_slot(state: dict[str, Any], pane_index: int) -> None:
    state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
    ensure_panes(state)
    state["pane_types"][pane_index] = "mpv"
    state["pane_commands"][pane_index] = ""


def _normalize_pane_type(pane_type: Any) -> tuple[str, str]:
    raw_type = str(pane_type or "").strip()
    if not raw_type:
        return "terminal", ""
    if raw_type in KNOWN_PANE_TYPES:
        return raw_type, ""
    return "terminal", raw_type


def _translate_roles_string(roles: str, pane_count: int) -> str:
    text = str(roles or "").strip()
    if not text:
        return ""
    translated: list[str] = []
    used: set[int] = set()
    for char in text:
        role = -1
        if char in ("C", "c"):
            role = 0
        elif char in ("A", "a"):
            role = 1
        elif char in ("B", "b"):
            role = 2
        elif char in ("D", "d"):
            role = 3
        elif char in ("E", "e"):
            role = 4
        elif char.isdigit():
            role = int(char)
        if role < 0 or role >= pane_count or role in used:
            continue
        translated.append(str(role))
        used.add(role)
    if len(translated) != pane_count:
        return "".join(str(role) for role in range(pane_count))
    return "".join(translated)


def _split_tree_skip_ws(spec: str, index: int) -> int:
    while index < len(spec) and spec[index].isspace():
        index += 1
    return index


def _parse_split_tree_node(spec: str, start_index: int) -> tuple[dict[str, Any], int] | None:
    index = _split_tree_skip_ws(spec, start_index)
    if index >= len(spec):
        return None
    if spec[index].isdigit():
        end = index + 1
        while end < len(spec) and spec[end].isdigit():
            end += 1
        return ({"leaf": True, "role": int(spec[index:end])}, end)
    kind = None
    if spec.startswith("row", index):
        kind = "row"
        index += 3
    elif spec.startswith("col", index):
        kind = "col"
        index += 3
    else:
        return None
    index = _split_tree_skip_ws(spec, index)
    if index >= len(spec) or spec[index] != ":":
        return None
    index += 1
    index = _split_tree_skip_ws(spec, index)
    pct_end = index
    while pct_end < len(spec) and spec[pct_end].isdigit():
        pct_end += 1
    if pct_end == index:
        return None
    pct = int(spec[index:pct_end])
    index = _split_tree_skip_ws(spec, pct_end)
    if index >= len(spec) or spec[index] != "(":
        return None
    left = _parse_split_tree_node(spec, index + 1)
    if left is None:
        return None
    index = _split_tree_skip_ws(spec, left[1])
    if index >= len(spec) or spec[index] != ",":
        return None
    right = _parse_split_tree_node(spec, index + 1)
    if right is None:
        return None
    index = _split_tree_skip_ws(spec, right[1])
    if index >= len(spec) or spec[index] != ")":
        return None
    return ({
        "leaf": False,
        "kind": kind,
        "pct": pct,
        "first": left[0],
        "second": right[0],
    }, index + 1)


def _parse_split_tree_spec(spec: str) -> dict[str, Any] | None:
    text = str(spec or "").strip()
    if not text:
        return None
    parsed = _parse_split_tree_node(text, 0)
    if parsed is None:
        return None
    end = _split_tree_skip_ws(text, parsed[1])
    return parsed[0] if end == len(text) else None


def _split_tree_collect_roles(node: dict[str, Any] | None, out: list[int]) -> None:
    if not node:
        return
    if node.get("leaf"):
        out.append(int(node.get("role", -1)))
        return
    _split_tree_collect_roles(node.get("first"), out)
    _split_tree_collect_roles(node.get("second"), out)


def _serialize_split_tree(node: dict[str, Any] | None) -> str:
    if not node:
        return ""
    if node.get("leaf"):
        return str(int(node.get("role", 0)))
    return (
        f"{node.get('kind', 'col')}:{int(node.get('pct', 50))}"
        f"({_serialize_split_tree(node.get('first'))},{_serialize_split_tree(node.get('second'))})"
    )


def _balanced_split_tree(roles: list[int], prefer_rows: bool = False) -> dict[str, Any] | None:
    if not roles:
        return None
    if len(roles) == 1:
        return {"leaf": True, "role": roles[0]}
    mid = (len(roles) + 1) // 2
    return {
        "leaf": False,
        "kind": "row" if prefer_rows else "col",
        "pct": 50,
        "first": _balanced_split_tree(roles[:mid], not prefer_rows),
        "second": _balanced_split_tree(roles[mid:], not prefer_rows),
    }


def _safe_split_tree_spec(pane_count: int) -> str:
    roles = list(range(max(1, pane_count)))
    if len(roles) == 1:
        return "0"
    if len(roles) == 2:
        return "col:50(0,1)"
    return _serialize_split_tree({
        "leaf": False,
        "kind": "col",
        "pct": 50,
        "first": {"leaf": True, "role": 0},
        "second": _balanced_split_tree(roles[1:], False),
    })


def _normalize_split_tree_spec(spec: str, pane_count: int) -> str:
    text = str(spec or "").strip()
    if not text:
        return ""
    parsed = _parse_split_tree_spec(text)
    if parsed is None:
        return _safe_split_tree_spec(pane_count)
    roles: list[int] = []
    _split_tree_collect_roles(parsed, roles)
    if len(roles) != pane_count or sorted(roles) != list(range(pane_count)):
        return _safe_split_tree_spec(pane_count)
    return _serialize_split_tree(parsed)


def _payload_has_media(payload: dict[str, Any]) -> bool:
    return any([
        bool(str(payload.get("playlist", "")).strip()),
        bool(str(payload.get("playlist_extended", "")).strip()),
        bool(str(payload.get("playlist_fifo", "")).strip()),
        bool(str(payload.get("mpv_out", "")).strip()),
        bool(str(payload.get("video_rotate", "")).strip()),
        bool(str(payload.get("panscan", "")).strip()),
        bool(payload.get("video_paths")),
        bool(payload.get("mpv_opts")),
    ])


def _normalize_loaded_state(state: dict[str, Any], web_state: dict[str, Any]) -> dict[str, Any]:
    ensure_panes(state)
    root_media_present = any([
        bool(state.get("video_paths")),
        bool(str(state.get("playlist", "")).strip()),
        bool(str(state.get("playlist_extended", "")).strip()),
        bool(str(state.get("playlist_fifo", "")).strip()),
        bool(str(state.get("mpv_out", "")).strip()),
        bool(str(state.get("video_rotate", "")).strip()),
        bool(str(state.get("panscan", "")).strip()),
        bool(state.get("mpv_opts")),
    ])
    roles_text = str(state.get("roles", "")).strip()
    legacy_hint = any(char in roles_text for char in "CcAaBbDdEe")
    source_pane_count = max(1, int(state.get("pane_count", 2)))
    source_payloads = [_pane_type_payload(state, index) for index in range(source_pane_count)]
    split_roles: list[int] = []
    _split_tree_collect_roles(_parse_split_tree_spec(str(state.get("split_tree", ""))), split_roles)
    split_tree_legacy_hint = (
        not root_media_present
        and not legacy_hint
        and len(split_roles) == source_pane_count + 1
        and sorted(split_roles) == list(range(source_pane_count + 1))
        and source_payloads[0].get("type") != "mpv"
        and not _payload_has_media(source_payloads[0])
    )
    legacy_mode = root_media_present or legacy_hint or split_tree_legacy_hint

    normalized = empty_state()
    for key in (
        "connector", "mode", "rotation", "font_size", "right_frac", "video_frac",
        "pane_split", "layout", "fs_cycle_sec", "transition_ms", "extra_lines",
    ):
        normalized[key] = state.get(key, normalized.get(key))
    normalized["flags"] = dict(state.get("flags", {}))

    total_panes = source_pane_count + 1 if legacy_mode else source_pane_count
    normalized["pane_count"] = max(1, total_panes)
    ensure_panes(normalized)

    if legacy_mode:
        pane_zero = {
            "type": "mpv",
            "raw": "",
            "settings": {},
            "command": "",
            "playlist": str(state.get("playlist", "") or ""),
            "playlist_extended": str(state.get("playlist_extended", "") or ""),
            "playlist_fifo": str(state.get("playlist_fifo", "") or ""),
            "mpv_out": str(state.get("mpv_out", "") or ""),
            "video_rotate": str(state.get("video_rotate", "") or ""),
            "panscan": str(state.get("panscan", "") or ""),
            "video_paths": list(state.get("video_paths", []) or []),
            "mpv_opts": list(state.get("mpv_opts", []) or []),
        }
        _write_pane_payload(normalized, 0, pane_zero)
        normalized["pane_commands"][0] = ""
        for index, payload in enumerate(source_payloads, start=1):
            _write_pane_payload(normalized, index, payload)
    else:
        for index, payload in enumerate(source_payloads):
            _write_pane_payload(normalized, index, payload)

    metadata_types = web_state.get("pane_types")
    metadata_settings = web_state.get("pane_type_settings")
    for index in range(normalized["pane_count"]):
        candidate_type = None
        if isinstance(metadata_types, list) and index < len(metadata_types):
            candidate_type = metadata_types[index]
        else:
            candidate_type = normalized["pane_types"][index]
        pane_type, raw_type = _normalize_pane_type(candidate_type)
        normalized["pane_types"][index] = pane_type
        normalized["pane_type_raw"][index] = raw_type
        if isinstance(metadata_settings, dict):
            normalized["pane_type_settings"][index] = _safe_metadata_mapping(metadata_settings.get(str(index)))
        elif isinstance(metadata_settings, list) and index < len(metadata_settings):
            normalized["pane_type_settings"][index] = _safe_metadata_mapping(metadata_settings[index])

    normalized["split_tree"] = _normalize_split_tree_spec(
        str(state.get("split_tree", "")),
        normalized["pane_count"],
    )
    normalized["roles"] = _translate_roles_string(roles_text, normalized["pane_count"])
    normalized["focus_pane"] = _normalize_saved_pane(
        web_state.get("focus_pane", web_state.get("focused_role")),
        normalized["pane_count"],
    )
    normalized["fullscreen_pane"] = _normalize_saved_pane(
        web_state.get("fullscreen_pane", web_state.get("fullscreen_role")),
        normalized["pane_count"],
    )
    normalized["selected_pane"] = _normalize_saved_pane(
        web_state.get("selected_pane", web_state.get("selected_role")),
        normalized["pane_count"],
    )

    normalized["video_paths"] = []
    normalized["playlist"] = ""
    normalized["playlist_extended"] = ""
    normalized["playlist_fifo"] = ""
    normalized["mpv_out"] = ""
    normalized["video_rotate"] = ""
    normalized["panscan"] = ""
    normalized["mpv_opts"] = []
    normalized["visibility_mode"] = normalize_visibility_mode(
        web_state.get("visibility_mode"),
        visibility_mode_from_flags(normalized.get("flags", {})),
    )
    return normalized


def parse_config_text(text: str) -> dict[str, Any]:
    state = empty_state()
    extra_lines: list[str] = []
    web_state: dict[str, Any] = {}

    lines = text.splitlines()
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            if stripped:
                parsed_web_state = _parse_web_state_comment(stripped)
                if parsed_web_state is not None:
                    web_state.update(parsed_web_state)
                    continue
                extra_lines.append(raw_line)
            continue
        try:
            tokens = shlex.split(raw_line, comments=True, posix=True)
        except ValueError:
            extra_lines.append(raw_line)
            continue
        if not tokens:
            continue

        keep_line = False
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if tok == "--connector" and nxt is not None:
                state["connector"] = nxt
                i += 2
            elif tok == "--mode" and nxt is not None:
                state["mode"] = nxt
                i += 2
            elif tok == "--rotate" and nxt is not None:
                state["rotation"] = int(nxt)
                i += 2
            elif tok == "--font-size" and nxt is not None:
                state["font_size"] = int(nxt)
                i += 2
            elif tok == "--right-frac" and nxt is not None:
                state["right_frac"] = int(nxt)
                i += 2
            elif tok == "--video-frac" and nxt is not None:
                state["video_frac"] = int(nxt)
                i += 2
            elif tok == "--pane-split" and nxt is not None:
                state["pane_split"] = int(nxt)
                i += 2
            elif tok == "--split-tree" and nxt is not None:
                state["split_tree"] = nxt
                i += 2
            elif tok == "--pane-count" and nxt is not None:
                state["pane_count"] = max(1, int(nxt))
                i += 2
            elif tok == "--pane-a" and nxt is not None:
                ensure_panes(state)
                state["pane_commands"][0] = nxt
                i += 2
            elif tok == "--pane-b" and nxt is not None:
                state["pane_count"] = max(2, int(state["pane_count"]))
                ensure_panes(state)
                state["pane_commands"][1] = nxt
                i += 2
            elif tok == "--pane-c" and nxt is not None:
                state["pane_count"] = max(3, int(state["pane_count"]))
                ensure_panes(state)
                state["pane_commands"][2] = nxt
                i += 2
            elif tok == "--pane-d" and nxt is not None:
                state["pane_count"] = max(4, int(state["pane_count"]))
                ensure_panes(state)
                state["pane_commands"][3] = nxt
                i += 2
            elif tok == "--pane" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
                ensure_panes(state)
                state["pane_commands"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-media" and nxt is not None:
                pane_index = max(0, int(nxt) - 1)
                _ensure_pane_media_slot(state, pane_index)
                i += 2
            elif tok == "--pane-playlist" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                _ensure_pane_media_slot(state, pane_index)
                state["pane_playlists"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-playlist-extended" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                _ensure_pane_media_slot(state, pane_index)
                state["pane_playlist_extended"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-playlist-fifo" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                _ensure_pane_media_slot(state, pane_index)
                state["pane_playlist_fifos"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-mpv-out" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                _ensure_pane_media_slot(state, pane_index)
                state["pane_mpv_outs"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-video-rotate" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                _ensure_pane_media_slot(state, pane_index)
                state["pane_video_rotate"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-panscan" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                _ensure_pane_media_slot(state, pane_index)
                state["pane_panscan"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-watchdog" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                _ensure_pane_media_slot(state, pane_index)
                state["pane_watchdogs"][pane_index] = max(0, int(tokens[i + 2]))
                i += 3
            elif tok == "--pane-sync-group" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                _ensure_pane_media_slot(state, pane_index)
                state["pane_sync_groups"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-video" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                _ensure_pane_media_slot(state, pane_index)
                state["pane_video_paths"][pane_index].append(tokens[i + 2])
                i += 3
            elif tok == "--pane-mpv-opt" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                _ensure_pane_media_slot(state, pane_index)
                state["pane_mpv_opts"][pane_index].append(tokens[i + 2])
                i += 3
            elif tok == "--layout" and nxt is not None:
                state["layout"] = nxt
                i += 2
            elif tok == "--roles" and nxt is not None:
                state["roles"] = nxt
                i += 2
            elif tok == "--fs-cycle-sec" and nxt is not None:
                state["fs_cycle_sec"] = int(nxt)
                i += 2
            elif tok == "--transition-ms" and nxt is not None:
                state["transition_ms"] = max(0, min(5000, int(nxt)))
                i += 2
            elif tok == "--video" and nxt is not None:
                state["video_paths"].append(nxt)
                i += 2
            elif tok == "--playlist" and nxt is not None:
                state["playlist"] = nxt
                i += 2
            elif tok == "--playlist-extended" and nxt is not None:
                state["playlist_extended"] = nxt
                i += 2
            elif tok == "--playlist-fifo" and nxt is not None:
                state["playlist_fifo"] = nxt
                i += 2
            elif tok == "--mpv-out" and nxt is not None:
                state["mpv_out"] = nxt
                i += 2
            elif tok == "--mpv-opt" and nxt is not None:
                state["mpv_opts"].append(nxt)
                i += 2
            elif tok == "--video-rotate" and nxt is not None:
                state["video_rotate"] = nxt
                i += 2
            elif tok == "--panscan" and nxt is not None:
                state["panscan"] = nxt
                i += 2
            elif tok == "--visibility-mode" and nxt is not None:
                state["visibility_mode"] = normalize_visibility_mode(nxt)
                i += 2
            elif tok == "--no-video":
                state["flags"]["no_video"] = True
                i += 1
            elif tok == "--no-panes":
                state["flags"]["no_panes"] = True
                i += 1
            elif tok == "--smooth":
                state["flags"]["smooth"] = True
                i += 1
            elif tok in ("--loop", "--loop-file"):
                state["flags"]["loop_file"] = True
                i += 1
            elif tok == "--loop-playlist":
                state["flags"]["loop_playlist"] = True
                i += 1
            elif tok in ("--shuffle", "--randomize"):
                state["flags"]["shuffle"] = True
                i += 1
            elif tok == "--atomic":
                state["flags"]["atomic"] = True
                i += 1
            elif tok == "--atomic-nonblock":
                state["flags"]["atomic"] = True
                state["flags"]["atomic_nonblock"] = True
                i += 1
            elif tok == "--gl-finish":
                state["flags"]["gl_finish"] = True
                i += 1
            elif tok == "--no-osd":
                state["flags"]["no_osd"] = True
                i += 1
            else:
                keep_line = True
                break
        if keep_line:
            extra_lines.append(raw_line)

    state["extra_lines"] = "\n".join(extra_lines).strip()
    return _normalize_loaded_state(state, web_state)


def build_config_text(state: dict[str, Any]) -> str:
    ensure_panes(state)
    lines: list[str] = []

    def add_flag(name: str, enabled: bool) -> None:
        if enabled:
            lines.append(name)

    def add_opt(name: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str) and value == "":
            return
        lines.append(f"{name} {shlex.quote(str(value))}")

    def pane_has_media(index: int) -> bool:
        if str(pane_playlists[index]).strip():
            return True
        if str(pane_playlist_extended[index]).strip():
            return True
        if str(pane_playlist_fifos[index]).strip():
            return True
        if str(pane_mpv_outs[index]).strip():
            return True
        if str(pane_video_rotate[index]).strip():
            return True
        if str(pane_panscan[index]).strip():
            return True
        if int(pane_watchdogs[index] or 0) > 0:
            return True
        if str(pane_sync_groups[index]).strip():
            return True
        if pane_video_paths[index]:
            return True
        if pane_mpv_opts[index]:
            return True
        return False

    add_opt("--connector", state.get("connector", ""))
    add_opt("--mode", state.get("mode", ""))
    add_opt("--rotate", state.get("rotation", 0) or "")
    add_opt("--font-size", state.get("font_size", 18))
    if int(state.get("video_frac", 0) or 0) > 0:
        add_opt("--video-frac", state["video_frac"])
    else:
        add_opt("--right-frac", state.get("right_frac", 33))
    add_opt("--pane-split", state.get("pane_split", 50))
    add_opt("--split-tree", state.get("split_tree", ""))
    add_opt("--layout", state.get("layout", "stack"))
    roles = _translate_roles_string(str(state.get("roles", "")).strip(), int(state.get("pane_count", 2)))
    if roles:
        add_opt("--roles", roles)
    add_opt("--fs-cycle-sec", state.get("fs_cycle_sec", 5))
    if int(state.get("transition_ms", 0) or 0) > 0:
        add_opt("--transition-ms", min(5000, int(state["transition_ms"])))

    pane_count = int(state.get("pane_count", 2))
    if pane_count != 2:
        add_opt("--pane-count", pane_count)

    pane_commands = list(state.get("pane_commands", []))
    pane_types = list(state.get("pane_types", []))
    pane_type_raw = [str(value or "") for value in state.get("pane_type_raw", [])]
    pane_playlists = list(state.get("pane_playlists", []))
    pane_playlist_extended = list(state.get("pane_playlist_extended", []))
    pane_playlist_fifos = list(state.get("pane_playlist_fifos", []))
    pane_mpv_outs = list(state.get("pane_mpv_outs", []))
    pane_video_rotate = list(state.get("pane_video_rotate", []))
    pane_panscan = list(state.get("pane_panscan", []))
    pane_watchdogs = list(state.get("pane_watchdogs", []))
    pane_sync_groups = list(state.get("pane_sync_groups", []))
    pane_video_paths = [list(paths) for paths in state.get("pane_video_paths", [])]
    pane_mpv_opts = [list(opts) for opts in state.get("pane_mpv_opts", [])]
    for idx, cmd in enumerate(pane_commands):
        pane_type = pane_types[idx] if idx < len(pane_types) else "terminal"
        raw_type = pane_type_raw[idx] if idx < len(pane_type_raw) else ""
        if pane_type == "mpv" or (raw_type and pane_has_media(idx)):
            lines.append(f"--pane-media {idx + 1}")
            playlist = pane_playlists[idx] if idx < len(pane_playlists) else ""
            playlist_ext = pane_playlist_extended[idx] if idx < len(pane_playlist_extended) else ""
            playlist_fifo = pane_playlist_fifos[idx] if idx < len(pane_playlist_fifos) else ""
            pane_mpv_out = pane_mpv_outs[idx] if idx < len(pane_mpv_outs) else ""
            pane_rotate = pane_video_rotate[idx] if idx < len(pane_video_rotate) else ""
            pane_panscan_value = pane_panscan[idx] if idx < len(pane_panscan) else ""
            pane_watchdog = pane_watchdogs[idx] if idx < len(pane_watchdogs) else 0
            pane_sync_group = pane_sync_groups[idx] if idx < len(pane_sync_groups) else ""
            videos = pane_video_paths[idx] if idx < len(pane_video_paths) else []
            mpv_opts = pane_mpv_opts[idx] if idx < len(pane_mpv_opts) else []
            if playlist:
                lines.append(f"--pane-playlist {idx + 1} {shlex.quote(str(playlist))}")
            if playlist_ext:
                lines.append(f"--pane-playlist-extended {idx + 1} {shlex.quote(str(playlist_ext))}")
            if playlist_fifo:
                lines.append(f"--pane-playlist-fifo {idx + 1} {shlex.quote(str(playlist_fifo))}")
            if pane_mpv_out:
                lines.append(f"--pane-mpv-out {idx + 1} {shlex.quote(str(pane_mpv_out))}")
            if str(pane_rotate).strip():
                lines.append(f"--pane-video-rotate {idx + 1} {str(pane_rotate).strip()}")
            if str(pane_panscan_value).strip():
                lines.append(f"--pane-panscan {idx + 1} {shlex.quote(str(pane_panscan_value))}")
            if int(pane_watchdog or 0) > 0:
                lines.append(f"--pane-watchdog {idx + 1} {int(pane_watchdog)}")
            if str(pane_sync_group).strip():
                lines.append(f"--pane-sync-group {idx + 1} {shlex.quote(str(pane_sync_group).strip())}")
            for video_path in videos:
                if str(video_path).strip():
                    lines.append(f"--pane-video {idx + 1} {shlex.quote(str(video_path))}")
            for opt in mpv_opts:
                if str(opt).strip():
                    lines.append(f"--pane-mpv-opt {idx + 1} {shlex.quote(str(opt))}")
            continue
        if not cmd:
            continue
        if idx == 0:
            add_opt("--pane-a", cmd)
        elif idx == 1:
            add_opt("--pane-b", cmd)
        elif idx == 2:
            add_opt("--pane-c", cmd)
        elif idx == 3:
            add_opt("--pane-d", cmd)
        else:
            lines.append(f"--pane {idx + 1} {shlex.quote(str(cmd))}")

    flags = state.get("flags", {})
    add_flag("--smooth", bool(flags.get("smooth")))
    add_flag("--shuffle", bool(flags.get("shuffle")))
    add_flag("--no-osd", bool(flags.get("no_osd")))
    if flags.get("atomic_nonblock"):
        add_flag("--atomic-nonblock", True)
    else:
        add_flag("--atomic", bool(flags.get("atomic")))
    add_flag("--gl-finish", bool(flags.get("gl_finish")))

    extra_lines = str(state.get("extra_lines", "")).strip()
    if extra_lines:
        lines.append("")
        lines.extend(extra_lines.splitlines())

    metadata: dict[str, Any] = {}
    pane_count = int(state.get("pane_count", 2))
    selected_pane = _normalize_saved_pane(state.get("selected_pane"), pane_count)
    focus_pane = _normalize_saved_pane(state.get("focus_pane"), pane_count)
    fullscreen_pane = _normalize_saved_pane(state.get("fullscreen_pane"), pane_count)
    if selected_pane >= 0:
        metadata["selected_pane"] = selected_pane
    if focus_pane >= 0:
        metadata["focus_pane"] = focus_pane
    if fullscreen_pane >= 0:
        metadata["fullscreen_pane"] = fullscreen_pane
    visibility_mode = normalize_visibility_mode(
        state.get("visibility_mode"),
        visibility_mode_from_flags(flags),
    )
    if visibility_mode != "neither":
        lines.append(f"--visibility-mode {visibility_mode}")
    if visibility_mode != "neither":
        metadata["visibility_mode"] = visibility_mode
    raw_types = [str(value or "") for value in state.get("pane_type_raw", [])[:pane_count]]
    if any(raw_types):
        effective_types = []
        for index in range(pane_count):
            raw_type = raw_types[index] if index < len(raw_types) else ""
            pane_type = state["pane_types"][index] if index < len(state["pane_types"]) else "terminal"
            effective_types.append(raw_type or pane_type or "terminal")
        metadata["pane_types"] = effective_types
    pane_type_settings = [
        dict(value) if isinstance(value, dict) else {}
        for value in state.get("pane_type_settings", [])[:pane_count]
    ]
    if any(pane_type_settings):
        metadata["pane_type_settings"] = {
            str(index): value
            for index, value in enumerate(pane_type_settings)
            if value
        }
    if metadata:
        lines.append(f"{WEB_STATE_PREFIX}{json.dumps(metadata, sort_keys=True, separators=(',', ':'))}")

    return "\n".join(lines).strip() + "\n"


def serialize_config(state: dict[str, Any]) -> str:
    return build_config_text(state)


@dataclass
class WebConfig:
    config_path: Path
    host: str
    port: int
    snapshot_request_path: Path
    preview_lease_path: Path
    snapshot_output_path: Path
    thumb_cache_dir: Path
    scenes_path: Path | None = None
    control_request_path: Path = Path("/tmp/kms_mosaic_control.request")
    control_status_path: Path = Path("/tmp/kms_mosaic_control.status")
    verbose: bool = False


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".kms_mosaic.", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temp_path, path)


class ConfigHistory:
    MAX_ENTRIES = 20
    MAX_BYTES = 4 * 1024 * 1024

    def __init__(self, config_path: Path, history_dir: Path | None = None) -> None:
        self.config_path = config_path
        if history_dir is not None:
            self.path = history_dir
        elif str(config_path).startswith("/boot/config/"):
            self.path = Path("/boot/config/plugins/kms.mosaic/history")
        else:
            self.path = config_path.with_name(f".{config_path.name}.history")
        self.lock = threading.RLock()

    def _entry_path(self, entry_id: str) -> Path:
        if not re.fullmatch(r"\d+-[a-z-]+-[0-9a-f]{12}\.conf", entry_id):
            raise ValueError("Invalid history entry")
        path = self.path / entry_id
        if not path.is_file():
            raise ValueError("History entry not found")
        return path

    def _snapshot_unlocked(self, text: str, reason: str) -> None:
        if not text:
            return
        clean_reason = re.sub(r"[^a-z-]", "-", reason.lower()).strip("-") or "change"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        entry = self.path / f"{time.time_ns()}-{clean_reason}-{digest}.conf"
        write_text_atomic(entry, text)

    def _prune_unlocked(self) -> None:
        entries = sorted(self.path.glob("*.conf"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        retained_bytes = 0
        for index, entry in enumerate(entries):
            try:
                size = entry.stat().st_size
                if index >= self.MAX_ENTRIES or retained_bytes + size > self.MAX_BYTES:
                    entry.unlink(missing_ok=True)
                else:
                    retained_bytes += size
            except OSError:
                continue

    def write(self, text: str, reason: str = "editor") -> bool:
        with self.lock:
            current = read_raw_config_text(self.config_path)
            if current == text:
                return False
            self.path.mkdir(parents=True, exist_ok=True)
            self._snapshot_unlocked(current, reason)
            write_text_atomic(self.config_path, text)
            self._prune_unlocked()
            return True

    def entries(self) -> list[dict[str, Any]]:
        with self.lock:
            if not self.path.exists():
                return []
            result: list[dict[str, Any]] = []
            for entry in sorted(self.path.glob("*.conf"), key=lambda path: path.stat().st_mtime_ns, reverse=True):
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                match = re.fullmatch(r"\d+-([a-z-]+)-[0-9a-f]{12}\.conf", entry.name)
                result.append({
                    "id": entry.name,
                    "created": stat.st_mtime,
                    "size": stat.st_size,
                    "reason": match.group(1).replace("-", " ") if match else "change",
                })
            return result

    def diff(self, entry_id: str) -> str:
        with self.lock:
            previous = self._entry_path(entry_id).read_text(encoding="utf-8")
            current = read_raw_config_text(self.config_path)
        return "".join(difflib.unified_diff(
            previous.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile="saved snapshot",
            tofile="current config",
        ))

    def rollback(self, entry_id: str) -> bool:
        with self.lock:
            previous = self._entry_path(entry_id).read_text(encoding="utf-8")
            current = read_raw_config_text(self.config_path)
            if previous == current:
                return False
            self.path.mkdir(parents=True, exist_ok=True)
            self._snapshot_unlocked(current, "pre-rollback")
            write_text_atomic(self.config_path, previous)
            self._prune_unlocked()
            return True


class PaneTemplateManager:
    MAX_TEMPLATES = 50
    MAX_TEMPLATE_BYTES = 256 * 1024

    def __init__(self, app_config: WebConfig, path: Path | None = None) -> None:
        self.app_config = app_config
        if path is not None:
            self.path = path
        elif str(app_config.config_path).startswith("/boot/config/"):
            self.path = Path("/boot/config/plugins/kms.mosaic/pane-templates.json")
        else:
            self.path = app_config.config_path.with_suffix(".pane-templates.json")
        self.lock = threading.RLock()

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "templates": []}

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        templates = payload.get("templates") if isinstance(payload, dict) else []
        return {"version": 1, "templates": list(templates or [])[:self.MAX_TEMPLATES]}

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        write_text_atomic(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def _normalize_pane(self, pane: Any) -> dict[str, Any]:
        if not isinstance(pane, dict):
            raise ValueError("Pane template data is invalid")
        state = empty_state()
        _write_pane_payload(state, 0, pane)
        normalized = _pane_type_payload(state, 0)
        normalized["video_paths"] = [str(value) for value in normalized["video_paths"]]
        normalized["mpv_opts"] = [str(value) for value in normalized["mpv_opts"]]
        if len(json.dumps(normalized).encode("utf-8")) > self.MAX_TEMPLATE_BYTES:
            raise ValueError("Pane template is too large")
        return normalized

    def read(self) -> dict[str, Any]:
        with self.lock:
            return self._read_unlocked()

    def save(self, name: str, pane: Any, template_id: str = "") -> dict[str, Any]:
        clean_name = " ".join(str(name or "").split()).strip()
        if not clean_name or len(clean_name) > 80:
            raise ValueError("Template name must be 1 to 80 characters")
        normalized = self._normalize_pane(pane)
        with self.lock:
            payload = self._read_unlocked()
            existing = next((item for item in payload["templates"] if item.get("id") == template_id), None)
            if existing is None:
                if len(payload["templates"]) >= self.MAX_TEMPLATES:
                    raise ValueError("Pane template limit reached")
                existing = {"id": secrets.token_urlsafe(9)}
                payload["templates"].append(existing)
            existing.update({"name": clean_name, "pane": normalized, "updated": time.time()})
            self._write_unlocked(payload)
            return dict(existing)

    def delete(self, template_id: str) -> bool:
        with self.lock:
            payload = self._read_unlocked()
            before = len(payload["templates"])
            payload["templates"] = [item for item in payload["templates"] if item.get("id") != template_id]
            changed = len(payload["templates"]) != before
            if changed:
                self._write_unlocked(payload)
            return changed

class SceneManager:
    def __init__(self, app_config: WebConfig, history: ConfigHistory | None = None) -> None:
        self.app_config = app_config
        self.history = history or ConfigHistory(app_config.config_path)
        self.path = app_config.scenes_path or app_config.config_path.with_suffix(".scenes.json")
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.schedule_minute = ""
        self.applied_schedule_keys: set[str] = set()

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "scenes": [], "schedules": []}

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(payload, dict):
            return self._empty()
        return {
            "version": 1,
            "scenes": list(payload.get("scenes") or []),
            "schedules": list(payload.get("schedules") or []),
        }

    def read(self) -> dict[str, Any]:
        with self.lock:
            return self._read_unlocked()

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        write_text_atomic(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def save_scene(self, name: str, state: dict[str, Any], scene_id: str = "") -> dict[str, Any]:
        clean_name = " ".join(str(name or "").split()).strip()
        if not clean_name:
            raise ValueError("Scene name is required")
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with self.lock:
            payload = self._read_unlocked()
            existing = next((item for item in payload["scenes"] if item.get("id") == scene_id), None)
            if existing is None:
                existing = {
                    "id": secrets.token_urlsafe(9),
                    "created_at": now,
                }
                payload["scenes"].append(existing)
            existing.update({
                "name": clean_name,
                "updated_at": now,
                "config": serialize_config(state),
            })
            self._write_unlocked(payload)
            return dict(existing)

    def delete_scene(self, scene_id: str) -> bool:
        with self.lock:
            payload = self._read_unlocked()
            before = len(payload["scenes"])
            payload["scenes"] = [item for item in payload["scenes"] if item.get("id") != scene_id]
            payload["schedules"] = [item for item in payload["schedules"] if item.get("scene_id") != scene_id]
            changed = len(payload["scenes"]) != before
            if changed:
                self._write_unlocked(payload)
            return changed

    def apply_scene(self, scene_id: str) -> dict[str, Any]:
        with self.lock:
            payload = self._read_unlocked()
            scene = next((item for item in payload["scenes"] if item.get("id") == scene_id), None)
            if scene is None:
                raise ValueError("Scene not found")
            config_text = str(scene.get("config") or "")
            if not config_text.strip():
                raise ValueError("Scene has no configuration")
        self.history.write(config_text, "scene")
        return dict(scene)

    def save_schedule(self, scene_id: str, at_time: str, days: list[int], enabled: bool = True,
                      schedule_id: str = "") -> dict[str, Any]:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(at_time or "")):
            raise ValueError("Schedule time must use HH:MM")
        clean_days = sorted({int(day) for day in days if 0 <= int(day) <= 6})
        if not clean_days:
            raise ValueError("Select at least one schedule day")
        with self.lock:
            payload = self._read_unlocked()
            if not any(item.get("id") == scene_id for item in payload["scenes"]):
                raise ValueError("Scene not found")
            existing = next((item for item in payload["schedules"] if item.get("id") == schedule_id), None)
            if existing is None:
                existing = {"id": secrets.token_urlsafe(9)}
                payload["schedules"].append(existing)
            existing.update({
                "scene_id": scene_id,
                "time": at_time,
                "days": clean_days,
                "enabled": bool(enabled),
            })
            self._write_unlocked(payload)
            return dict(existing)

    def delete_schedule(self, schedule_id: str) -> bool:
        with self.lock:
            payload = self._read_unlocked()
            before = len(payload["schedules"])
            payload["schedules"] = [item for item in payload["schedules"] if item.get("id") != schedule_id]
            changed = len(payload["schedules"]) != before
            if changed:
                self._write_unlocked(payload)
            return changed

    def _scheduler_loop(self) -> None:
        while not self.stop_event.wait(5):
            now = time.localtime()
            minute = time.strftime("%Y-%m-%d %H:%M", now)
            current_time = time.strftime("%H:%M", now)
            if minute != self.schedule_minute:
                self.schedule_minute = minute
                self.applied_schedule_keys.clear()
            payload = self.read()
            for schedule in payload["schedules"]:
                key = f"{minute}:{schedule.get('id', '')}"
                if (
                    schedule.get("enabled", True)
                    and current_time == schedule.get("time")
                    and now.tm_wday in schedule.get("days", [])
                    and key not in self.applied_schedule_keys
                ):
                    try:
                        self.apply_scene(str(schedule.get("scene_id") or ""))
                        self.applied_schedule_keys.add(key)
                    except (OSError, ValueError):
                        pass

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._scheduler_loop, name="kms-mosaic-scenes", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None


class HealthMonitor:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.previous_cpu: dict[int, tuple[int, float]] = {}
        self.clock_ticks = int(os.sysconf("SC_CLK_TCK"))
        self.page_size = int(os.sysconf("SC_PAGE_SIZE"))
        self.started_at = time.monotonic()
        self.compositor_pid = -1

    def _process(self, pid: int) -> dict[str, Any] | None:
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            comm = stat_text[stat_text.index("(") + 1:stat_text.rindex(")")]
            fields = stat_text[stat_text.rindex(")") + 2:].split()
            ticks = int(fields[11]) + int(fields[12])
            ppid = int(fields[1])
            threads = int(fields[17])
            rss_bytes = int(fields[21]) * self.page_size
        except (OSError, ValueError, IndexError):
            return None
        now = time.monotonic()
        with self.lock:
            previous = self.previous_cpu.get(pid)
            self.previous_cpu[pid] = (ticks, now)
        cpu_percent = 0.0
        if previous and now > previous[1]:
            cpu_percent = max(0.0, (ticks - previous[0]) / self.clock_ticks / (now - previous[1]) * 100)
        return {
            "pid": pid,
            "ppid": ppid,
            "name": comm,
            "cpu_percent": round(cpu_percent, 1),
            "rss_bytes": rss_bytes,
            "threads": threads,
        }

    def _find_compositor_pid(self) -> int:
        if self.compositor_pid > 0:
            try:
                if Path(f"/proc/{self.compositor_pid}/comm").read_text(encoding="utf-8").strip() == "kms_mosaic.bin":
                    return self.compositor_pid
            except OSError:
                pass
        self.compositor_pid = -1
        entries = Path("/proc").iterdir() if Path("/proc").exists() else []
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                if (entry / "comm").read_text(encoding="utf-8").strip() == "kms_mosaic.bin":
                    self.compositor_pid = int(entry.name)
                    return self.compositor_pid
            except OSError:
                continue
        return -1

    def _children(self, pid: int) -> list[dict[str, Any]]:
        if pid <= 0:
            return []
        try:
            child_ids = (Path(f"/proc/{pid}/task/{pid}/children")
                         .read_text(encoding="utf-8").split())
        except OSError:
            child_ids = []
            entries = Path("/proc").iterdir() if Path("/proc").exists() else []
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                try:
                    stat_text = (entry / "stat").read_text(encoding="utf-8")
                    fields = stat_text[stat_text.rindex(")") + 2:].split()
                    if int(fields[1]) == pid:
                        child_ids.append(entry.name)
                except (OSError, ValueError, IndexError):
                    continue
        return [process for child in child_ids if (process := self._process(int(child))) is not None]

    def _gpu(self) -> list[dict[str, Any]]:
        devices: list[dict[str, Any]] = []
        for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
            device = card / "device"
            item: dict[str, Any] = {"name": card.name}
            for key, filename in (
                ("busy_percent", "gpu_busy_percent"),
                ("vram_used_bytes", "mem_info_vram_used"),
                ("vram_total_bytes", "mem_info_vram_total"),
            ):
                try:
                    item[key] = int((device / filename).read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    item[key] = None
            if any(value is not None for key, value in item.items() if key != "name"):
                devices.append(item)
        return devices

    def _recent_errors(self) -> list[str]:
        for candidate in (Path("/tmp/start_kms_mosaic.log"), Path("/tmp/kms_mosaic_web.log")):
            if not candidate.exists():
                continue
            try:
                lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
            except OSError:
                continue
            errors = [line[-500:] for line in lines if re.search(r"error|failed|fatal|denied", line, re.I)]
            if errors:
                return errors[-8:]
        return []

    def _recovery(self) -> dict[str, Any] | None:
        try:
            value = json.loads(Path("/tmp/kms_mosaic_control.status").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def snapshot(self, preview_peers: int = 0) -> dict[str, Any]:
        compositor_pid = self._find_compositor_pid()
        compositor = self._process(compositor_pid) if compositor_pid > 0 else None
        children = self._children(compositor_pid)
        web = self._process(os.getpid())
        return {
            "timestamp": time.time(),
            "uptime_sec": round(time.monotonic() - self.started_at, 1),
            "compositor": compositor,
            "web": web,
            "pane_processes": children,
            "gpu": self._gpu(),
            "preview_peers": preview_peers,
            "recent_errors": self._recent_errors(),
            "last_recovery": self._recovery(),
        }


def write_preview_lease(app_config: WebConfig, interval_ms: int) -> None:
    interval_ms = max(1, min(int(interval_ms), 1000))
    write_text_atomic(app_config.preview_lease_path, f"{interval_ms}\n{time.time_ns()}\n")


def read_latest_raw_preview_frame(app_config: WebConfig, last_mtime_ns: int = 0, interval_ms: int = 16,
                                  timeout_sec: float = 3.0) -> tuple[bytes, int]:
    output_path = app_config.snapshot_output_path
    now = time.monotonic()
    deadline = now + timeout_sec
    next_lease_refresh = now
    while now < deadline:
        if now >= next_lease_refresh:
            write_preview_lease(app_config, interval_ms)
            next_lease_refresh = now + 0.25
        if output_path.exists():
            st = output_path.stat()
            if st.st_size >= 8 and (last_mtime_ns <= 0 or st.st_mtime_ns > last_mtime_ns):
                return output_path.read_bytes(), st.st_mtime_ns
        time.sleep(0.01)
        now = time.monotonic()
    raise TimeoutError("Timed out waiting for kms_mosaic frame")


def decode_raw_preview_frame(frame_bytes: bytes) -> tuple[int, int, bytes]:
    if len(frame_bytes) < 8:
        raise ValueError("Preview frame payload too short")
    width = int.from_bytes(frame_bytes[0:4], "little")
    height = int.from_bytes(frame_bytes[4:8], "little")
    if width <= 0 or height <= 0:
        raise ValueError("Preview frame dimensions missing")
    expected = width * height * 4
    payload = frame_bytes[8:8 + expected]
    if len(payload) < expected:
        raise ValueError("Preview frame payload truncated")
    return width, height, payload


def pack_rgba_rows(rgba: bytes, width: int, height: int, line_size: int,
                   scratch: bytearray | None = None) -> bytes | bytearray:
    row_bytes = width * 4
    if line_size == row_bytes:
        return rgba
    required = line_size * height
    if scratch is None or len(scratch) != required:
        scratch = bytearray(required)
    for row in range(height):
        source_start = row * row_bytes
        dest_start = row * line_size
        scratch[dest_start:dest_start + row_bytes] = rgba[source_start:source_start + row_bytes]
    return scratch


def preview_encode_dimensions(width: int, height: int, max_edge: int) -> tuple[int, int]:
    scale = min(1.0, max_edge / max(width, height))
    scaled_w = max(2, int(round(width * scale)))
    scaled_h = max(2, int(round(height * scale)))
    return scaled_w - scaled_w % 2, scaled_h - scaled_h % 2


def boost_h264_bitrate_sdp(sdp: str, start_kbps: int = 8000, max_kbps: int = 12000, min_kbps: int = 2000) -> str:
    if not sdp:
        return sdp
    lines = sdp.splitlines()
    h264_payloads: list[str] = []
    for line in lines:
        match = re.match(r"a=rtpmap:(\d+)\s+H264/90000", line, re.IGNORECASE)
        if match:
            h264_payloads.append(match.group(1))
    if not h264_payloads:
        return sdp

    updated: list[str] = []
    seen_fmtp: set[str] = set()
    for line in lines:
        fmtp_match = re.match(r"a=fmtp:(\d+)\s+(.+)", line, re.IGNORECASE)
        if fmtp_match and fmtp_match.group(1) in h264_payloads:
            payload = fmtp_match.group(1)
            params = fmtp_match.group(2)
            if "x-google-start-bitrate" not in params:
                params += f";x-google-start-bitrate={start_kbps}"
            if "x-google-min-bitrate" not in params:
                params += f";x-google-min-bitrate={min_kbps}"
            if "x-google-max-bitrate" not in params:
                params += f";x-google-max-bitrate={max_kbps}"
            updated.append(f"a=fmtp:{payload} {params}")
            seen_fmtp.add(payload)
            continue
        updated.append(line)
        rtpmap_match = re.match(r"a=rtpmap:(\d+)\s+H264/90000", line, re.IGNORECASE)
        if rtpmap_match:
            payload = rtpmap_match.group(1)
            if payload not in seen_fmtp:
                updated.append(
                    f"a=fmtp:{payload} x-google-start-bitrate={start_kbps};x-google-min-bitrate={min_kbps};x-google-max-bitrate={max_kbps}"
                )
                seen_fmtp.add(payload)
    return "\r\n".join(updated) + "\r\n"


def codec_preference_key(codec: Any) -> tuple[int, str]:
    mime = str(getattr(codec, "mimeType", "")).lower()
    if mime == "video/h264":
        return (0, mime)
    if mime == "video/vp8":
        return (1, mime)
    if mime == "video/vp9":
        return (2, mime)
    if mime == "video/av1":
        return (3, mime)
    return (4, mime)


class RawPreviewVideoTrack(VideoStreamTrack):
    def __init__(self, app_config: WebConfig) -> None:
        super().__init__()
        self.app_config = app_config
        self.interval_ms = 16
        self.last_mtime_ns = 0
        self.last_frame: av.VideoFrame | None = None
        self.padded_rgba = bytearray()
        self.timestamp = 0
        self.time_base = Fraction(1, 90000)
        self.last_timestamp_time = time.monotonic()
        self.max_edge = 720

    def configure(self, profile: str) -> str:
        profiles = {
            "quality": (16, 720),
            "balanced": (33, 720),
            "economy": (100, 480),
        }
        selected = profile if profile in profiles else "balanced"
        self.interval_ms, self.max_edge = profiles[selected]
        return selected

    async def recv(self) -> av.VideoFrame:
        if self.readyState != "live":
            raise MediaStreamError
        try:
            frame_bytes, self.last_mtime_ns = await asyncio.to_thread(
                read_latest_raw_preview_frame,
                self.app_config,
                self.last_mtime_ns,
                self.interval_ms,
                2.0,
            )
            width, height, rgba = decode_raw_preview_frame(frame_bytes)
            frame = av.VideoFrame(width, height, "rgba")
            packed = pack_rgba_rows(rgba, width, height, frame.planes[0].line_size,
                                    self.padded_rgba)
            if isinstance(packed, bytearray):
                self.padded_rgba = packed
            frame.planes[0].update(packed)
            # H.264 and other YUV 4:2:0 encoders require even dimensions.
            # The compositor can produce odd portrait widths (for example 405px),
            # so normalize every frame rather than only downscaled frames.
            scaled_w, scaled_h = preview_encode_dimensions(width, height, self.max_edge)
            self.last_frame = frame.reformat(width=scaled_w, height=scaled_h, format="yuv420p")
        except MediaStreamError:
            raise
        except Exception:
            if self.last_frame is None:
                fallback = av.VideoFrame(16, 9, "rgba")
                fallback.planes[0].update(bytes(16 * 9 * 4))
                self.last_frame = fallback.reformat(format="yuv420p")
        frame_out = self.last_frame
        now = time.monotonic()
        elapsed = max(now - self.last_timestamp_time, 1 / 90000)
        self.last_timestamp_time = now
        frame_out.pts = self.timestamp
        frame_out.time_base = self.time_base
        self.timestamp += max(1, int(90000 * elapsed))
        return frame_out


class WebRTCBridge:
    def __init__(self, app_config: WebConfig) -> None:
        self.app_config = app_config
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.peers: set[RTCPeerConnection] = set()
        self.peer_ids: dict[str, RTCPeerConnection] = {}
        self.peer_last_seen: dict[str, float] = {}
        self.peer_expiry: dict[RTCPeerConnection, asyncio.Task[Any]] = {}
        self.preview_source: RawPreviewVideoTrack | None = RawPreviewVideoTrack(app_config) if self.available else None
        self.relay = MediaRelay() if self.available and MediaRelay is not None else None

    @property
    def available(self) -> bool:
        return RTCPeerConnection is not None and RTCSessionDescription is not None and av is not None

    def start(self) -> None:
        if not self.available or self.thread is not None:
            return
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, name="kms-mosaic-webrtc", daemon=True)
        self.thread.start()

    def _run_loop(self) -> None:
        assert self.loop is not None
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _wait_for_ice_complete(self, pc: RTCPeerConnection) -> None:
        if pc.iceGatheringState == "complete":
            return
        done = asyncio.Event()

        @pc.on("icegatheringstatechange")
        async def _on_ice_state() -> None:
            if pc.iceGatheringState == "complete":
                done.set()

        await asyncio.wait_for(done.wait(), timeout=5.0)

    async def _close_peer(self, pc: RTCPeerConnection) -> None:
        if pc in self.peers:
            self.peers.discard(pc)
        for peer_id, candidate in list(self.peer_ids.items()):
            if candidate is pc:
                self.peer_ids.pop(peer_id, None)
                self.peer_last_seen.pop(peer_id, None)
        expiry = self.peer_expiry.pop(pc, None)
        if expiry is not None and expiry is not asyncio.current_task():
            expiry.cancel()
        if pc.connectionState != "closed":
            await pc.close()
        if not self.peers and self.preview_source is not None:
            # aiortc's MediaRelay keeps polling its source after the last proxy
            # stops. End that worker and make a fresh lazy source for the next
            # viewer so compositor preview readback returns to idle immediately.
            self.preview_source.stop()
            self.preview_source = RawPreviewVideoTrack(self.app_config)
            self.relay = MediaRelay() if MediaRelay is not None else None

    async def _expire_peer(self, pc: RTCPeerConnection, peer_id: str) -> None:
        while self.peer_ids.get(peer_id) is pc:
            await asyncio.sleep(5)
            if time.monotonic() - self.peer_last_seen.get(peer_id, 0.0) > 20:
                await self._close_peer(pc)
                return

    async def _create_answer(self, offer_sdp: str, offer_type: str, preview_profile: str = "balanced") -> dict[str, str]:
        if not self.available:
            raise RuntimeError("WebRTC preview dependencies are not installed")
        pc = RTCPeerConnection()
        peer_id = secrets.token_urlsafe(18)
        self.peers.add(pc)
        self.peer_ids[peer_id] = pc
        self.peer_last_seen[peer_id] = time.monotonic()

        try:
            @pc.on("connectionstatechange")
            async def _on_connectionstatechange() -> None:
                if pc.connectionState in {"failed", "closed", "disconnected"}:
                    await self._close_peer(pc)

            await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
            if self.preview_source is None:
                raise RuntimeError("WebRTC preview source is unavailable")
            selected_profile = self.preview_source.configure(preview_profile)
            track = self.relay.subscribe(self.preview_source) if self.relay is not None else self.preview_source
            sender = pc.addTrack(track)
            transceiver = next(
                (candidate for candidate in pc.getTransceivers() if candidate.sender is sender),
                None,
            )
            if RTCRtpSender is not None:
                capabilities = RTCRtpSender.getCapabilities("video")
                codecs = list(capabilities.codecs) if capabilities else []
                if codecs and transceiver is not None:
                    preferred = sorted(codecs, key=codec_preference_key)
                    if preferred:
                        transceiver.setCodecPreferences(preferred)
            if sender is not None and hasattr(sender, "getParameters") and hasattr(sender, "setParameters"):
                params = sender.getParameters()
                if params.encodings:
                    for encoding in params.encodings:
                        encoding.maxBitrate = 50_000_000
                try:
                    await sender.setParameters(params)
                except Exception:
                    pass
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            await self._wait_for_ice_complete(pc)
            assert pc.localDescription is not None
            self.peer_expiry[pc] = asyncio.create_task(self._expire_peer(pc, peer_id))
            bitrate_profiles = {
                "quality": (8000, 12000, 2000),
                "balanced": (5000, 8000, 1200),
                "economy": (1800, 3000, 500),
            }
            start_kbps, max_kbps, min_kbps = bitrate_profiles[selected_profile]
            return {
                "sdp": boost_h264_bitrate_sdp(pc.localDescription.sdp, start_kbps, max_kbps, min_kbps),
                "type": pc.localDescription.type,
                "peer_id": peer_id,
                "preview_profile": selected_profile,
            }
        except Exception:
            await self._close_peer(pc)
            raise

    def create_answer(self, offer_sdp: str, offer_type: str, preview_profile: str = "balanced") -> dict[str, str]:
        if not self.available or self.loop is None:
            raise RuntimeError("WebRTC preview bridge is unavailable")
        future = asyncio.run_coroutine_threadsafe(
            self._create_answer(offer_sdp, offer_type, preview_profile), self.loop
        )
        return future.result(timeout=15.0)

    def close_peer(self, peer_id: str) -> None:
        if self.loop is None:
            return

        async def close_requested_peer() -> None:
            pc = self.peer_ids.get(peer_id)
            if pc is not None:
                await self._close_peer(pc)

        asyncio.run_coroutine_threadsafe(close_requested_peer(), self.loop).result(timeout=5.0)

    def keep_peer_alive(self, peer_id: str) -> bool:
        if self.loop is None:
            return False

        async def refresh_peer() -> bool:
            if peer_id not in self.peer_ids:
                return False
            self.peer_last_seen[peer_id] = time.monotonic()
            return True

        return bool(asyncio.run_coroutine_threadsafe(refresh_peer(), self.loop).result(timeout=5.0))

    async def _shutdown(self) -> None:
        peers = list(self.peers)
        for pc in peers:
            await self._close_peer(pc)
        if self.preview_source is not None:
            self.preview_source.stop()
            self.preview_source = None
        if self.loop is not None:
            self.loop.stop()

    def close(self) -> None:
        if self.loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
        try:
            future.result(timeout=10.0)
        finally:
            if self.thread is not None:
                self.thread.join(timeout=2.0)
            self.thread = None
            self.loop = None


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="light dark" />
  <title>KMS Mosaic — Config</title>
  <style>
    :root {
      --paper: rgba(255, 251, 244, 0.92);
      --ink: #1a1714;
      --muted: #72685e;
      --line: rgba(34, 31, 26, 0.11);
      --line-strong: rgba(34, 31, 26, 0.22);
      --accent: #b5532f;
      --accent-dark: #6d2f17;
      --danger: #b3412b;
      --shadow: 0 16px 40px rgba(54, 43, 32, 0.10), 0 2px 8px rgba(54, 43, 32, 0.06);
      --surface: rgba(255, 255, 255, 0.42);
      --surface-high: rgba(255, 255, 255, 0.68);
      --surface-input: rgba(255, 255, 255, 0.72);
      --surface-input-focus: #fffdf8;
      --r: 22px;
      --r-sm: 12px;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --paper: rgba(21, 17, 13, 0.94);
        --ink: #ede7dd;
        --muted: #847c72;
        --line: rgba(255, 240, 210, 0.09);
        --line-strong: rgba(255, 240, 210, 0.18);
        --accent: #cf7853;
        --accent-dark: #e8a07a;
        --danger: #d96b4e;
        --shadow: 0 16px 40px rgba(0, 0, 0, 0.50), 0 2px 8px rgba(0, 0, 0, 0.30);
        --surface: rgba(255, 255, 255, 0.05);
        --surface-high: rgba(255, 255, 255, 0.09);
        --surface-input: rgba(255, 255, 255, 0.07);
        --surface-input-focus: rgba(255, 255, 255, 0.11);
      }
      body {
        background:
          radial-gradient(ellipse at 20% 0%, rgba(181, 83, 47, 0.14) 0%, transparent 55%),
          radial-gradient(ellipse at 80% 100%, rgba(109, 47, 23, 0.10) 0%, transparent 50%),
          #0a0806;
      }
      #rawConfig {
        background: #0c0a08;
        border-color: rgba(255,255,255,0.10);
      }
      .app-header {
        background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.02));
        border-bottom-color: rgba(255,240,210,0.08);
      }
      .accent-bar { background: linear-gradient(180deg, rgba(207,120,83,0.8), rgba(181,83,47,0.6)); }
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html { scroll-behavior: smooth; }
    body {
      color: var(--ink);
      background:
        radial-gradient(ellipse at 20% 0%, rgba(181, 83, 47, 0.09) 0%, transparent 55%),
        radial-gradient(ellipse at 80% 100%, rgba(109, 47, 23, 0.07) 0%, transparent 50%),
        #f0ebe2;
      font-family: -apple-system, BlinkMacSystemFont, "Avenir Next", "Helvetica Neue", sans-serif;
      min-height: 100vh;
      font-size: 13px;
      line-height: 1.5;
    }
    /* ─── layout ─────────────────────────────────────── */
    .grain::before {
      content: "";
      position: fixed;
      inset: 0;
      background-image:
        linear-gradient(rgba(24,20,16,0.018) 1px, transparent 1px),
        linear-gradient(90deg, rgba(24,20,16,0.018) 1px, transparent 1px);
      background-size: 28px 28px;
      pointer-events: none;
    }
    .shell {
      width: min(1520px, calc(100vw - 24px));
      margin: 12px auto 24px;
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
      gap: 12px;
      align-items: start;
    }
    .card {
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: var(--r);
      box-shadow: var(--shadow);
      backdrop-filter: blur(20px);
      overflow: clip;
    }
    .left-rail { position: sticky; top: 12px; }
    /* ─── app header ──────────────────────────────────── */
    .app-header {
      display: flex;
      align-items: stretch;
      gap: 12px;
      padding: 0 14px 0 0;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255,255,255,0.55), rgba(255,255,255,0.25));
      min-height: 64px;
      overflow: clip;
    }
    .accent-bar {
      width: 4px;
      align-self: stretch;
      flex-shrink: 0;
      background: linear-gradient(180deg, rgba(181,83,47,0.9), rgba(109,47,23,0.7));
    }
    .app-title-block {
      min-width: 0;
      flex: 1;
      display: grid;
      align-content: center;
      gap: 2px;
      padding: 10px 0;
    }
    .app-name {
      font-weight: 700;
      font-size: 14px;
      letter-spacing: -0.02em;
      color: var(--ink);
      white-space: nowrap;
    }
    .config-path {
      font-family: "Menlo", "Consolas", monospace;
      font-size: 11px;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      min-width: 0;
      display: block;
    }
    /* ─── preview ─────────────────────────────────────── */
    .stage { padding: 12px; }
    .preview-layout { display: block; }
    .preview-layout.portrait .preview-wrap { aspect-ratio: 9 / 16; margin: 0 auto; }
    .preview-layout.landscape .preview-wrap { aspect-ratio: 16 / 9; }
    .preview-wrap {
      position: relative;
      width: 100%;
      aspect-ratio: 16 / 9;
      border-radius: 14px;
      background: linear-gradient(180deg, #1a1714, #0c0b09);
      border: 1px solid rgba(0,0,0,0.28);
      overflow: clip;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.04), 0 4px 12px rgba(0,0,0,0.18);
      margin: 0 auto;
    }
    .preview-stage {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .preview-video {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      object-position: center center;
      background: #070605;
    }
    .preview-bar {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 8px;
    }
    .preview-bar label {
      display: flex;
      align-items: center;
      gap: 7px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 500;
    }
    .preview-bar select {
      width: auto;
      padding: 5px 8px;
      font-size: 12px;
      border-radius: 8px;
    }
    /* ─── panel ───────────────────────────────────────── */
    .panel-body {
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 1px;
    }
    .panel-body > div,
    details.advanced-block {
      border: none;
      border-radius: 0;
      background: none;
      padding: 12px 14px;
      border-top: 1px solid var(--line);
    }
    .panel-body > div:first-child,
    details.advanced-block:first-child { border-top: none; }
    .panel-body > div.panel-wide { border-top: 1px solid var(--line); }
    .panel-wide { width: 100%; }
    /* ─── section headings ────────────────────────────── */
    .section-title {
      font-size: 10px;
      letter-spacing: 0.15em;
      text-transform: uppercase;
      color: var(--accent);
      font-family: "Menlo", "Consolas", monospace;
      margin-bottom: 9px;
    }
    /* ─── advanced block ──────────────────────────────── */
    details.advanced-block {
      overflow: clip;
    }
    details.advanced-block > summary {
      list-style: none;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-family: "Menlo", "Consolas", monospace;
      letter-spacing: 0.15em;
      text-transform: uppercase;
      color: var(--accent);
      font-size: 10px;
      padding: 0;
      margin: 0;
    }
    details.advanced-block > summary::-webkit-details-marker { display: none; }
    details.advanced-block > summary::after { content: "+"; font-size: 14px; letter-spacing: 0; }
    details.advanced-block[open] > summary::after { content: "−"; }
    .advanced-body {
      display: flex;
      flex-direction: column;
      gap: 1px;
      margin-top: 12px;
    }
    .advanced-body > div {
      padding: 12px 0 0;
      border-top: 1px solid var(--line);
    }
    /* ─── grid layouts ────────────────────────────────── */
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 9px;
    }
    .suggestion-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(148px, 1fr));
      gap: 9px;
      align-items: stretch;
    }
    .suggestion-btn {
      text-align: left;
      border-radius: var(--r-sm);
      border: 1px solid var(--line);
      background: var(--surface-high);
      padding: 10px 11px 9px;
      transition: transform 100ms ease, box-shadow 100ms ease;
      cursor: pointer;
      min-height: 92px;
      display: flex;
      flex-direction: column;
      justify-content: flex-start;
      gap: 4px;
      overflow: hidden;
    }
    .suggestion-btn:hover {
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(181,83,47,0.12);
    }
    .suggestion-btn strong {
      display: block;
      font-size: 13px;
      margin-bottom: 1px;
      line-height: 1.2;
      white-space: normal;
      word-break: break-word;
      overflow-wrap: break-word;
    }
    .suggestion-btn span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.42;
      white-space: normal;
      word-break: break-word;
      overflow-wrap: break-word;
    }
    /* ─── forms ───────────────────────────────────────── */
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 500;
      letter-spacing: 0.01em;
    }
    input, select, textarea {
      width: 100%;
      border-radius: var(--r-sm);
      border: 1px solid var(--line-strong);
      background: var(--surface-input);
      color: var(--ink);
      padding: 8px 11px;
      font: inherit;
      font-size: 13px;
      outline: none;
      transition: border-color 120ms ease, background 120ms ease, box-shadow 120ms ease;
    }
    textarea {
      min-height: 100px;
      resize: vertical;
      font-family: "Menlo", "Consolas", monospace;
      font-size: 12px;
      line-height: 1.6;
    }
    input:hover:not(:focus), select:hover:not(:focus), textarea:hover:not(:focus) {
      border-color: var(--line-strong);
    }
    input:focus, select:focus, textarea:focus {
      border-color: var(--accent);
      background: var(--surface-input-focus);
      box-shadow: 0 0 0 3px rgba(181,83,47,0.10);
    }
    /* ─── pane list ───────────────────────────────────── */
    .pane-list { display: grid; gap: 7px; }
    .pane-item {
      border: 1px solid var(--line);
      border-radius: var(--r-sm);
      background: var(--surface-high);
      padding: 10px 11px;
    }
    .pane-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 8px;
    }
    .pane-name {
      font-family: "Menlo", "Consolas", monospace;
      font-size: 10px;
      letter-spacing: 0.12em;
      color: var(--accent-dark);
      text-transform: uppercase;
    }
    .mini { color: var(--muted); font-size: 12px; }
    /* ─── checkboxes ──────────────────────────────────── */
    .checks {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 2px 12px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 5px 0;
      color: var(--ink);
      font-size: 13px;
    }
    .check input { width: 14px; height: 14px; margin: 0; accent-color: var(--accent); }
    .flag-mode-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
      flex-wrap: wrap;
    }
    .flag-mode-buttons {
      display: inline-flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .flag-mode-note {
      color: var(--muted);
      font-size: 11px;
      line-height: 1.4;
    }
    /* ─── buttons ─────────────────────────────────────── */
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .actions.tight { gap: 6px; }
    button {
      border: 1px solid transparent;
      border-radius: 7px;
      padding: 7px 13px;
      font: inherit;
      font-size: 12px;
      cursor: pointer;
      transition: transform 100ms ease, box-shadow 100ms ease;
      font-weight: 600;
      white-space: nowrap;
    }
    button:hover { transform: translateY(-1px); box-shadow: 0 3px 10px rgba(0,0,0,0.10); }
    button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    .primary {
      background: linear-gradient(135deg, var(--accent) 0%, #d4764e 100%);
      color: #fff9f4;
      border-color: rgba(109,47,23,0.18);
      box-shadow: 0 2px 6px rgba(181,83,47,0.22);
    }
    .primary:hover { box-shadow: 0 4px 14px rgba(181,83,47,0.32); }
    .secondary {
      background: var(--surface-high);
      color: var(--ink);
      border-color: var(--line-strong);
    }
    .status {
      min-height: 18px;
      color: var(--muted);
      font-size: 12px;
      padding: 2px 0 0;
    }
    .status.error { color: var(--danger); }
    .status.success { color: #4a8c5c; }
    @media (prefers-color-scheme: dark) { .status.success { color: #6abf82; } }
    /* ─── raw config ──────────────────────────────────── */
    #rawConfig {
      min-height: 280px;
      background: #161310;
      color: #f0ead9;
      border-color: rgba(255,255,255,0.07);
    }
    /* ─── studio ──────────────────────────────────────── */
    .studio-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(260px, 0.85fr);
      gap: 10px;
      align-items: start;
    }
    .scene-toolbar {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) minmax(180px, 1fr) auto;
      gap: 8px;
      align-items: end;
    }
    .scene-actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .scene-days { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
    .scene-day {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      color: var(--muted);
    }
    .scene-schedule-list { display: grid; gap: 6px; margin-top: 10px; }
    .scene-schedule-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      padding: 7px 9px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-high);
      font-size: 11px;
    }
    .health-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 8px;
    }
    .health-card {
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 9px;
      background: var(--surface-high);
    }
    .health-label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; }
    .health-value { margin-top: 4px; font-size: 17px; font-weight: 700; }
    .health-processes, .health-errors { margin-top: 10px; font: 11px/1.5 "Menlo", "Consolas", monospace; white-space: pre-wrap; }
    .health-ok { color: #4a8c5c; }
    .health-bad { color: var(--danger); }
    .monitor-controls { display: grid; gap: 10px; }
    .monitor-control-row {
      display: grid;
      grid-template-columns: minmax(150px, 1fr) auto;
      gap: 8px;
      align-items: end;
    }
    .monitor-power-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .remote-shell { display: none; max-width: 620px; margin: 0 auto; padding: 12px; }
    body.remote-mode .shell { display: none; }
    body.remote-mode .remote-shell { display: grid; gap: 12px; }
    .remote-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .remote-header h1 { margin: 0; font-size: 24px; }
    .remote-card {
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: var(--r);
      background: var(--surface);
      box-shadow: var(--shadow);
    }
    .remote-card h2 { margin: 0 0 10px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; }
    .remote-button-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .remote-button-grid button { min-height: 48px; font-size: 14px; }
    .remote-health { color: var(--muted); font: 12px/1.5 "Menlo", "Consolas", monospace; }
    .remote-link { color: var(--accent-dark); text-decoration: none; font: 11px/1.4 "Menlo", "Consolas", monospace; }
    .history-list { display: grid; gap: 7px; }
    .history-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-high);
    }
    .history-label { font: 11px/1.4 "Menlo", "Consolas", monospace; }
    .history-item-actions { display: flex; gap: 6px; }
    .history-diff { max-height: 280px; overflow: auto; white-space: pre; font: 10px/1.45 "Menlo", "Consolas", monospace; }
    .studio-board {
      position: relative;
      aspect-ratio: 16 / 9;
      border-radius: 16px;
      overflow: hidden;
      border: 1px solid var(--line);
      background:
        radial-gradient(circle at 25% 25%, rgba(207,120,83,0.18), transparent 50%),
        linear-gradient(160deg, #160f0b 0%, #0f0b09 100%);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.04);
    }
    .studio-board::before {
      content: "";
      position: absolute;
      inset: 0;
      background-image:
        linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
      background-size: 28px 28px;
      pointer-events: none;
    }
    .studio-board.resizing,
    .studio-board.resizing .studio-card {
      cursor: inherit;
    }
    .studio-guide {
      position: absolute;
      z-index: 8;
      pointer-events: none;
      background: rgba(255, 218, 150, 0.95);
      box-shadow: 0 0 0 1px rgba(93, 38, 18, 0.35), 0 0 12px rgba(255, 190, 106, 0.55);
    }
    .studio-guide.vertical { top: 0; bottom: 0; width: 1px; }
    .studio-guide.horizontal { left: 0; right: 0; height: 1px; }
    .studio-card {
      position: absolute;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.03), 0 12px 28px rgba(0,0,0,0.24);
      overflow: hidden;
      cursor: pointer;
      transition: border-color 120ms ease, box-shadow 120ms ease;
      display: flex;
      flex-direction: column;
      justify-content: flex-start;
      gap: 8px;
      padding: 10px;
      user-select: none;
    }
    .studio-card:hover { border-color: rgba(255,255,255,0.20); }
    .studio-card.dragging { opacity: 0.58; transform: scale(0.99); }
    .studio-card.drop-target {
      border-color: rgba(255,240,200,0.70);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.10), 0 0 0 2px rgba(207,120,83,0.28), 0 12px 28px rgba(0,0,0,0.30);
    }
    .studio-card.selected {
      border-color: rgba(255,240,200,0.70);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.10), 0 0 0 2px rgba(207,120,83,0.28), 0 12px 28px rgba(0,0,0,0.30);
    }
    .studio-card.video { background: linear-gradient(145deg, rgba(109,47,23,0.75), rgba(46,24,15,0.96)); color: #fff8f0; }
    .studio-card.terminal { background: linear-gradient(145deg, rgba(22,30,26,0.92), rgba(8,12,11,0.98)); color: #d8f0e2; }
    .studio-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      font-family: "Menlo", "Consolas", monospace;
      font-size: 10px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    .studio-card-controls {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: auto;
      padding-top: 8px;
      border-top: 1px solid rgba(255,255,255,0.08);
      opacity: 0;
      transform: translateY(4px);
      transition: opacity 120ms ease, transform 120ms ease;
      pointer-events: none;
    }
    .studio-card:hover .studio-card-controls,
    .studio-card.selected .studio-card-controls {
      opacity: 1;
      transform: translateY(0);
      pointer-events: auto;
    }
    .selected-pane-size-group {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 12px;
      align-items: start;
      min-width: 0;
    }
    .studio-size-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 6px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.12);
      background: rgba(0,0,0,0.18);
      backdrop-filter: blur(6px);
      font-size: 9px;
      letter-spacing: 0.1em;
    }
    .studio-size-chip[data-active="false"] {
      opacity: 0.62;
    }
    .studio-size-input {
      width: 38px;
      padding: 2px 4px;
      border: 1px solid rgba(0,0,0,0.16);
      border-radius: 999px;
      background: rgba(255,255,255,0.7);
      color: #333;
      font-size: 9px;
      font-family: "Menlo", "Consolas", monospace;
      text-align: center;
    }
    .studio-size-input:disabled {
      opacity: 0.58;
      cursor: not-allowed;
    }
    .studio-size-input:focus {
      outline: none;
      border-color: rgba(181,83,47,0.5);
      background: #fff;
    }
    .selected-pane-size-label {
      font-size: 13px;
      font-weight: 700;
      color: var(--ink);
    }
    .selected-pane-size-field {
      display: grid;
      gap: 6px;
      min-width: 0;
    }
    .selected-pane-size-field[data-active="false"] {
      opacity: 0.62;
    }
    .selected-pane-size-group .studio-size-input {
      width: 100%;
      min-width: 0;
      padding: 6px 8px;
      font-size: 13px;
      text-align: left;
      border-radius: 4px;
      box-sizing: border-box;
    }
    @media (max-width: 680px) {
      .selected-pane-size-group {
        grid-template-columns: minmax(0, 1fr);
      }
    }
    .studio-tag {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 8px;
      border-radius: 5px;
      background: rgba(255,255,255,0.08);
      backdrop-filter: blur(6px);
    }
    .studio-split-btn {
      border: 1px solid rgba(255,255,255,0.14);
      background: rgba(255,255,255,0.10);
      color: inherit;
      padding: 5px 8px;
      border-radius: 6px;
      font-size: 10px;
      font-family: "Menlo", "Consolas", monospace;
      white-space: normal;
      line-height: 1.2;
      text-align: center;
    }
    .studio-remove-btn {
      border: 1px solid rgba(186, 59, 42, 0.30);
      background: rgba(186, 59, 42, 0.12);
      color: #d77a61;
      padding: 5px 8px;
      border-radius: 6px;
      font-size: 10px;
      font-family: "Menlo", "Consolas", monospace;
    }
    .studio-remove-btn:hover {
      background: rgba(186, 59, 42, 0.20);
      border-color: rgba(186, 59, 42, 0.45);
    }
    .studio-resize-handle {
      position: absolute;
      border: 0;
      background: transparent;
      padding: 0;
      opacity: 0;
      transition: opacity 120ms ease;
      pointer-events: none;
      z-index: 4;
    }
    .studio-card.selected .studio-resize-handle {
      opacity: 1;
      pointer-events: auto;
      filter: drop-shadow(0 0 10px rgba(87, 31, 16, 0.22));
    }
    .studio-resize-handle::before,
    .studio-resize-handle::after {
      content: "";
      position: absolute;
      border-radius: 999px;
      background: rgba(191, 98, 54, 0.96);
      box-shadow: 0 0 0 1px rgba(255, 247, 235, 0.72);
    }
    .studio-resize-handle[data-edge="left"],
    .studio-resize-handle[data-edge="right"] {
      top: 10px;
      bottom: 10px;
      width: 18px;
      cursor: ew-resize;
    }
    .studio-resize-handle[data-edge="left"]::before,
    .studio-resize-handle[data-edge="right"]::before {
      top: 16px;
      bottom: 16px;
      left: 7px;
      width: 4px;
    }
    .studio-resize-handle[data-edge="left"]::after,
    .studio-resize-handle[data-edge="right"]::after {
      top: 50%;
      left: 2px;
      width: 14px;
      height: 30px;
      margin-top: -15px;
      background: rgba(255, 248, 238, 0.96);
      box-shadow: 0 0 0 2px rgba(87, 31, 16, 0.18);
    }
    .studio-resize-handle[data-edge="left"] { left: -9px; }
    .studio-resize-handle[data-edge="right"] { right: -9px; }
    .studio-resize-handle[data-edge="top"],
    .studio-resize-handle[data-edge="bottom"] {
      left: 10px;
      right: 10px;
      height: 18px;
      cursor: ns-resize;
    }
    .studio-resize-handle[data-edge="top"]::before,
    .studio-resize-handle[data-edge="bottom"]::before {
      left: 16px;
      right: 16px;
      top: 7px;
      height: 4px;
    }
    .studio-resize-handle[data-edge="top"]::after,
    .studio-resize-handle[data-edge="bottom"]::after {
      left: 50%;
      top: 2px;
      width: 30px;
      height: 14px;
      margin-left: -15px;
      background: rgba(255, 248, 238, 0.96);
      box-shadow: 0 0 0 2px rgba(87, 31, 16, 0.18);
    }
    .studio-resize-handle[data-edge="top"] { top: -9px; }
    .studio-resize-handle[data-edge="bottom"] { bottom: -9px; }
    .studio-resize-handle[data-mode="corner"] {
      width: 20px;
      height: 20px;
      cursor: nwse-resize;
    }
    .studio-resize-handle[data-mode="corner"]::before {
      inset: 4px;
      border: 2px solid rgba(255, 248, 238, 0.96);
      background: rgba(191, 98, 54, 0.94);
      box-shadow: 0 0 0 2px rgba(87, 31, 16, 0.18);
    }
    .studio-resize-handle[data-mode="corner"]::after {
      display: none;
    }
    .studio-resize-handle[data-mode="corner"][data-corner="top-left"] {
      top: -10px;
      left: -10px;
      cursor: nwse-resize;
    }
    .studio-resize-handle[data-mode="corner"][data-corner="top-right"] {
      top: -10px;
      right: -10px;
      cursor: nesw-resize;
    }
    .studio-resize-handle[data-mode="corner"][data-corner="bottom-left"] {
      bottom: -10px;
      left: -10px;
      cursor: nesw-resize;
    }
    .studio-resize-handle[data-mode="corner"][data-corner="bottom-right"] {
      bottom: -10px;
      right: -10px;
      cursor: nwse-resize;
    }
    .studio-card-title { font-size: 16px; font-weight: 700; line-height: 1.05; letter-spacing: -0.02em; }
    .studio-card-meta { font-size: 11px; opacity: 0.80; line-height: 1.4; max-width: 26ch; }
    .studio-inspector {
      border-radius: var(--r-sm);
      border: 1px solid var(--line);
      background: var(--surface-high);
      padding: 12px;
      display: grid;
      gap: 10px;
    }
    .selected-pane-section {
      display: grid;
      gap: 10px;
      padding-bottom: 2px;
      min-width: 0;
    }
    .selected-pane-section + .selected-pane-section {
      padding-top: 12px;
      border-top: 1px solid var(--line);
    }
    .studio-empty { color: var(--muted); font-size: 12px; line-height: 1.5; }
    /* ─── playlist ────────────────────────────────────── */
    .playlist-editor { display: grid; gap: 10px; }
    .queue-editor-head {
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .queue-editor-note {
      margin-top: 4px;
      margin-bottom: 0;
    }
    .queue-editor-target {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 11px;
      font-family: "Menlo", "Consolas", monospace;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent-dark);
      background: rgba(255,255,255,0.03);
      white-space: nowrap;
    }
    .playlist-list {
      display: grid;
      gap: 7px;
      max-height: calc(5 * 112px + 4 * 7px);
      overflow-y: auto;
      padding-right: 6px;
    }
    .playlist-list::-webkit-scrollbar {
      width: 10px;
    }
    .playlist-list::-webkit-scrollbar-thumb {
      background: rgba(0,0,0,0.18);
      border-radius: 999px;
      border: 2px solid transparent;
      background-clip: padding-box;
    }
    .playlist-targets {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }
    .playlist-target-btn {
      border: 1px solid var(--line);
      background: var(--surface-high);
      color: var(--muted);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 11px;
      font-family: "Menlo", "Consolas", monospace;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      transition: background 140ms ease, color 140ms ease, border-color 140ms ease, box-shadow 140ms ease;
    }
    .playlist-target-btn.active {
      color: #fff8f0;
      border-color: rgba(255,240,200,0.48);
      background: linear-gradient(135deg, rgba(181,83,47,0.92), rgba(132,58,31,0.96));
      box-shadow: 0 4px 12px rgba(181,83,47,0.18);
    }
    .media-editor {
      display: grid;
      gap: 10px;
    }
    .playlist-item {
      border: 1px solid var(--line);
      border-radius: var(--r-sm);
      background: var(--surface-high);
      padding: 9px 10px;
      display: grid;
      gap: 8px;
      min-height: 112px;
      transition: border-color 120ms ease, box-shadow 120ms ease;
    }
    .playlist-item.alt {
      background: color-mix(in srgb, var(--surface-high) 78%, #000 22%);
    }
    .playlist-item:hover { border-color: var(--line-strong); }
    .playlist-item.dragging { opacity: 0.5; transform: scale(0.99); }
    .playlist-item.drag-over { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(181,83,47,0.14); }
    .playlist-row {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 8px 10px;
      align-items: start;
    }
    .playlist-item.portrait-thumb .playlist-row {
      grid-template-columns: auto minmax(0, 1fr);
    }
    .playlist-media-cell {
      width: 156px;
      display: flex;
      justify-content: center;
      align-items: center;
      align-self: start;
      min-width: 0;
    }
    .playlist-duration-chip {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 30px;
      padding: 0 8px;
      border-radius: 6px;
      background: rgba(255,255,255,0.04);
      border: 1px solid var(--line);
      color: var(--muted);
      font-family: "Menlo", "Consolas", monospace;
      font-size: 10px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .playlist-duration-chip:empty { display: none; }
    .playlist-thumb {
      height: auto;
      width: auto;
      min-height: 88px;
      margin-inline: auto;
      border-radius: 8px;
      overflow: hidden;
      background:
        linear-gradient(140deg, rgba(181,83,47,0.15), transparent),
        var(--surface-high);
      border: 1px solid var(--line);
      position: relative;
      flex-shrink: 0;
    }
    .playlist-item.portrait-thumb .playlist-thumb {
      margin-inline: auto;
    }
    .playlist-thumb-media {
      position: absolute;
      inset: 0;
      display: grid;
      place-items: center;
      overflow: hidden;
      padding: 3px;
      transition: transform 160ms ease, box-shadow 160ms ease;
      transform-origin: center center;
    }
    .playlist-thumb img, .playlist-thumb video {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
      transform-origin: center center;
    }
    .playlist-thumb.cover img, .playlist-thumb.cover video {
      object-fit: cover;
    }
    .playlist-thumb video { background: #0e0c0a; }
    .playlist-thumb.empty::after {
      content: "—";
      position: absolute;
      inset: 0;
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 14px;
      opacity: 0.5;
    }
    .playlist-path {
      width: 100%;
      margin-top: 1px;
      min-width: 0;
      box-sizing: border-box;
    }
    .playlist-controls {
      display: grid;
      gap: 6px;
      min-width: 0;
      align-content: start;
    }
    .playlist-controls-row {
      display: flex;
      flex-wrap: nowrap;
      gap: 6px;
      align-items: center;
      min-width: 0;
    }
    .playlist-inline-group {
      display: inline-flex;
      gap: 6px;
      align-items: center;
      flex: 0 0 auto;
      min-width: 0;
    }
    .playlist-controls-row .playlist-mini-btn {
      margin-top: 0;
      margin-bottom: 0;
      align-self: center;
      flex: 0 0 auto;
      white-space: nowrap;
    }
    .playlist-thumb-media.quarter-turn { padding: 0; }
    .playlist-index {
      position: absolute;
      top: 5px;
      right: 5px;
      z-index: 3;
      width: 22px;
      height: 22px;
      border-radius: 5px;
      display: grid;
      place-items: center;
      background: rgba(0,0,0,0.56);
      color: #fff8f0;
      font-family: "Menlo", "Consolas", monospace;
      font-size: 10px;
      font-weight: 700;
      pointer-events: none;
      backdrop-filter: blur(4px);
    }
    .playlist-row input {
      min-width: 0;
      box-sizing: border-box;
    }
    .playlist-repeat {
      text-align: center;
      font-family: "Menlo", "Consolas", monospace;
      height: 30px;
      width: 5ch !important;
      min-width: 5ch !important;
      max-width: 5ch !important;
      flex: 0 0 5ch;
      padding: 6px 4px;
    }
    .playlist-repeat-label {
      display: inline-flex;
      align-items: center;
      flex: 0 0 auto;
      color: var(--muted);
      font-size: 9px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      font-family: "Menlo", "Consolas", monospace;
      white-space: nowrap;
      line-height: 30px;
    }
    .playlist-mini-btn {
      padding: 5px 8px;
      border-radius: 6px;
      background: var(--surface-high);
      border: 1px solid var(--line);
      color: var(--ink);
      height: 30px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-top: 0;
      margin-bottom: 0;
    }
    .playlist-mini-btn.danger { color: var(--danger); }
    .playlist-bulk {
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: var(--r-sm);
      background: rgba(255,255,255,0.02);
      overflow: clip;
    }
    .playlist-bulk summary {
      cursor: pointer;
      list-style: none;
      padding: 10px 12px;
      font-family: "Menlo", "Consolas", monospace;
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--accent-dark);
      background: rgba(255,255,255,0.03);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      user-select: none;
    }
    .playlist-bulk summary::-webkit-details-marker { display: none; }
    .playlist-bulk summary::after { content: "+"; font-size: 14px; letter-spacing: 0; }
    .playlist-bulk[open] summary::after { content: "−"; }
    .playlist-bulk-body {
      padding: 10px 12px 12px;
      display: grid;
      gap: 8px;
    }
    .layout-suggestions-block {
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: var(--r-sm);
      background: rgba(255,255,255,0.02);
      overflow: clip;
    }
    .layout-suggestions-block summary {
      cursor: pointer;
      list-style: none;
      padding: 10px 12px;
      font-family: "Menlo", "Consolas", monospace;
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--accent-dark);
      background: rgba(255,255,255,0.03);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      user-select: none;
    }
    .layout-suggestions-block summary::-webkit-details-marker { display: none; }
    .layout-suggestions-block summary::after { content: "+"; font-size: 14px; letter-spacing: 0; }
    .layout-suggestions-block[open] summary::after { content: "−"; }
    .layout-suggestions-body {
      padding: 12px;
    }
    /* ─── misc ────────────────────────────────────────── */
    .muted-note { color: var(--muted); font-size: 11px; line-height: 1.5; margin-top: 7px; }
    .hidden { display: none !important; }
    /* ─── responsive ──────────────────────────────────── */
    @media (max-width: 1100px) {
      .shell { grid-template-columns: 1fr; }
      .left-rail { position: static; }
      .studio-grid { grid-template-columns: 1fr; }
      .scene-toolbar { grid-template-columns: 1fr; }
    }
    @media (max-width: 680px) {
      .shell { margin: 8px auto 16px; }
      .grid, .checks { grid-template-columns: 1fr; }
      .panel-body > div, details.advanced-block { padding: 10px 12px; }
      .queue-editor-head { flex-direction: column; align-items: stretch; }
      .queue-editor-target { white-space: normal; }
      .playlist-row { grid-template-columns: auto 1fr; }
      .playlist-item.portrait-thumb .playlist-row { grid-template-columns: auto 1fr; }
      .playlist-media-cell { width: 100%; }
      .playlist-thumb { margin-inline: auto; }
      .playlist-controls-row { flex-wrap: wrap; }
    }
  </style>
</head>
<body class="grain">
  <div class="shell">
    <section class="card left-rail">
      <div class="app-header">
        <div class="accent-bar"></div>
        <div class="app-title-block">
          <span class="app-name">KMS Mosaic</span>
          <span id="configPath" class="config-path">—</span>
        </div>
      </div>
      <div class="stage">
        <div class="preview-layout" id="preview-outer">
          <div class="preview-wrap" id="preview">
            <div class="preview-stage">
              <video id="previewVideo" class="preview-video" autoplay playsinline muted></video>
            </div>
          </div>
          <div class="preview-bar">
            <label>Preview
              <select id="previewProfile">
                <option value="auto">Auto</option>
                <option value="quality">Quality · 60 fps</option>
                <option value="balanced">Balanced · 30 fps</option>
                <option value="economy">Economy · 10 fps</option>
              </select>
            </label>
            <a class="remote-link" href="?remote=1">Open Mobile Remote</a>
          </div>
        </div>
        <div class="status" id="status"></div>
      </div>
    </section>

    <section class="card">
      <div class="panel-body">
        <div class="panel-wide">
          <h2 class="section-title">Scene Profiles</h2>
          <div class="scene-toolbar">
            <label>Saved Scene
              <select id="sceneSelect"><option value="">New scene…</option></select>
            </label>
            <label>Scene Name
              <input id="sceneName" type="text" maxlength="80" placeholder="Morning dashboard" />
            </label>
            <div class="scene-actions">
              <button type="button" class="primary" id="sceneSaveBtn">Save Scene</button>
              <button type="button" class="secondary" id="sceneApplyBtn" disabled>Apply</button>
              <button type="button" class="secondary" id="sceneDeleteBtn" disabled>Delete</button>
            </div>
          </div>
          <details class="advanced-block" style="margin-top:10px;">
            <summary>Scene Schedule</summary>
            <div class="advanced-body">
              <label>Switch Time <input id="sceneScheduleTime" type="time" value="08:00" /></label>
              <div class="scene-days" id="sceneScheduleDays">
                <label class="scene-day"><input type="checkbox" value="0" checked /> Mon</label>
                <label class="scene-day"><input type="checkbox" value="1" checked /> Tue</label>
                <label class="scene-day"><input type="checkbox" value="2" checked /> Wed</label>
                <label class="scene-day"><input type="checkbox" value="3" checked /> Thu</label>
                <label class="scene-day"><input type="checkbox" value="4" checked /> Fri</label>
                <label class="scene-day"><input type="checkbox" value="5" /> Sat</label>
                <label class="scene-day"><input type="checkbox" value="6" /> Sun</label>
              </div>
              <button type="button" class="secondary" id="sceneScheduleAddBtn" disabled>Add Schedule</button>
              <div class="scene-schedule-list" id="sceneScheduleList"></div>
            </div>
          </details>
        </div>

        <details class="advanced-block panel-wide" id="healthPanel">
          <summary>System Health</summary>
          <div class="advanced-body">
            <div class="health-grid" id="healthGrid"></div>
            <div class="health-processes" id="healthProcesses"></div>
            <div class="health-errors" id="healthErrors"></div>
          </div>
        </details>

        <details class="advanced-block panel-wide" id="monitorPanel">
          <summary>Monitor Controls</summary>
          <div class="advanced-body monitor-controls">
            <label>Connected Display
              <select id="monitorConnector"><option value="">Open panel to discover displays…</option></select>
            </label>
            <div class="monitor-control-row">
              <label>Brightness
                <input id="monitorBrightness" type="number" min="0" max="100" value="100" />
              </label>
              <button type="button" class="secondary" data-monitor-control="brightness" data-monitor-value-id="monitorBrightness">Apply</button>
            </div>
            <div class="monitor-control-row">
              <label>Contrast
                <input id="monitorContrast" type="number" min="0" max="100" value="100" />
              </label>
              <button type="button" class="secondary" data-monitor-control="contrast" data-monitor-value-id="monitorContrast">Apply</button>
            </div>
            <div class="monitor-control-row">
              <label>Input Source
                <select id="monitorInput">
                  <option value="17">HDMI 1 (0x11)</option>
                  <option value="18">HDMI 2 (0x12)</option>
                  <option value="15">DisplayPort 1 (0x0F)</option>
                  <option value="16">DisplayPort 2 (0x10)</option>
                  <option value="6">Legacy / monitor-specific (0x06)</option>
                </select>
              </label>
              <button type="button" class="secondary" data-monitor-control="input" data-monitor-value-id="monitorInput">Apply</button>
            </div>
            <div class="monitor-power-actions">
              <button type="button" class="secondary" data-monitor-control="power" data-monitor-value="1">Power On</button>
              <button type="button" class="secondary danger" data-monitor-control="power" data-monitor-value="4">Power Off</button>
              <button type="button" class="secondary" data-monitor-control="power" data-monitor-value="5">Standby</button>
            </div>
            <p class="muted-note" id="monitorStatus">Controls are sent only when a button is pressed. Unsupported values may be ignored by the monitor.</p>
          </div>
        </details>

        <details class="advanced-block panel-wide" id="configHistoryPanel">
          <summary>Config History</summary>
          <div class="advanced-body">
            <div class="actions tight"><button type="button" class="secondary" id="historyRefreshBtn">Refresh</button></div>
            <div class="history-list" id="historyList">Open panel to load snapshots…</div>
            <pre class="history-diff" id="historyDiff"></pre>
          </div>
        </details>

        <div class="panel-wide">
          <h2 class="section-title">Pane Layout</h2>
          <div class="studio-grid">
            <div>
              <div class="actions tight" style="margin-bottom:10px;">
                <button type="button" class="secondary" id="studioUndoBtn" title="Undo layout change (Ctrl/Cmd+Z)" disabled>Undo</button>
                <button type="button" class="secondary" id="studioRedoBtn" title="Redo layout change (Ctrl/Cmd+Shift+Z)" disabled>Redo</button>
                <span class="hint">Drag handles to resize. Hold Alt to bypass snapping.</span>
              </div>
              <div class="studio-board" id="studioBoard"></div>
            </div>
          </div>
        </div>

        <div class="panel-wide selected-pane-shell" id="selectedPaneShell">
          <div class="studio-inspector" id="studioInspector">
            <div class="studio-empty">Select a pane to edit it.</div>
          </div>
        </div>

        <div class="advanced-block panel-wide" id="advancedPanel">
          <div class="advanced-body">
            <div>
              <div class="checks">
                <label class="check" title="Enable the compositor's smooth-presentation preset for gentler frame pacing defaults."><input id="flagSmooth" type="checkbox" /> Smooth Preset</label>
                <label class="check" title="Shuffle playlist order before playback advances through the queue."><input id="flagShuffle" type="checkbox" /> Shuffle</label>
                <label class="check" title="Use DRM atomic modesetting when the GPU and connector support it."><input id="flagAtomic" type="checkbox" /> Atomic</label>
                <label class="check" title="Request non-blocking atomic commits when atomic modesetting is enabled."><input id="flagAtomicNonblock" type="checkbox" /> Atomic Nonblock</label>
                <label class="check" title="Force a glFinish after rendering each frame. Useful for troubleshooting timing issues, but it can hurt performance."><input id="flagGlFinish" type="checkbox" /> glFinish</label>
                <label class="check" title="Hide the on-screen display and control overlay text."><input id="flagNoOsd" type="checkbox" /> No OSD</label>
              </div>
            </div>

            <div>
              <h2 class="section-title">Scene Rules</h2>
              <div class="grid">
                <label>Display
                  <select id="connector">
                    <option value="">Auto</option>
                  </select>
                </label>
                <label>Rotation
                  <select id="rotation">
                    <option value="0">0</option>
                    <option value="90">90</option>
                    <option value="180">180</option>
                    <option value="270">270</option>
                  </select>
                </label>
                <label>Font Size
                  <input id="fontSize" type="number" min="10" max="48" />
                </label>
                <label>Mode
                  <input id="mode" type="text" placeholder="1920x1080@60" />
                </label>
                <label>Fullscreen Cycle Sec
                  <input id="fsCycleSec" type="number" min="0" max="600" />
                </label>
                <label>Scene Fade (ms)
                  <input id="transitionMs" type="number" min="0" max="5000" step="50" />
                </label>
              </div>
            </div>

            <div>
              <h2 class="section-title">Extra Config Lines</h2>
              <label>Preserved Unknown Flags
                <textarea id="extraLines" placeholder="# Unknown or advanced flags are preserved here"></textarea>
              </label>
            </div>

            <div>
              <h2 class="section-title">Raw Config</h2>
              <label>Full Config File
                <textarea id="rawConfig" spellcheck="false" placeholder="The full kms_mosaic.conf file appears here"></textarea>
              </label>
              <div class="actions">
                <button class="secondary" id="saveRawBtn">Save Raw Config</button>
              </div>
            </div>
          </div>
        </div>

        <div class="actions">
          <button class="secondary" id="saveBtn">Save Config</button>
          <button class="secondary" id="reloadBtn">Reload From Disk</button>
        </div>

      </div>
    </section>
  </div>

  <main class="remote-shell" id="remoteShell">
    <div class="remote-header">
      <h1>KMS Mosaic Remote</h1>
      <a class="remote-link" href="?">Full Editor</a>
    </div>
    <div class="remote-card">
      <h2>System</h2>
      <div class="remote-health" id="remoteHealth">Loading…</div>
      <button type="button" class="secondary" id="remoteRefreshBtn">Refresh</button>
    </div>
    <div class="remote-card">
      <h2>Scenes</h2>
      <div class="remote-button-grid" id="remoteScenes"></div>
    </div>
    <div class="remote-card">
      <h2>Visible Content</h2>
      <div class="remote-button-grid" id="remoteVisibility">
        <button type="button" class="secondary" data-remote-visibility="neither">Show Everything</button>
        <button type="button" class="secondary" data-remote-visibility="no-terminal">Media Only</button>
        <button type="button" class="secondary" data-remote-visibility="no-video">Terminal Only</button>
      </div>
    </div>
    <div class="remote-card">
      <h2>Restart Pane</h2>
      <div class="remote-button-grid" id="remotePanes"></div>
    </div>
    <div class="status" id="remoteStatus"></div>
  </main>

  <script>
    const remoteMode = new URLSearchParams(window.location.search).get("remote") === "1";
    document.body.classList.toggle("remote-mode", remoteMode);
    const layoutNames = ["stack", "row", "2x1", "1x2", "2over1", "1over2", "overlay"];
    const previewVideo = document.getElementById("previewVideo");
    const previewStage = document.querySelector(".preview-stage");
    const previewLayout = document.querySelector(".preview-layout");
    const layoutSelect = document.getElementById("layout");
    const studioBoard = document.getElementById("studioBoard");
    const studioInspector = document.getElementById("studioInspector");
    const statusEl = document.getElementById("status");
    const STUDIO_SIZE_MIN = 5;
    const STUDIO_SIZE_MAX = 95;
    const STUDIO_HISTORY_LIMIT = 50;
    const STUDIO_SNAP_POINTS = [20, 25, 33, 40, 50, 60, 67, 75, 80];
    let state = null;
    let rawConfigText = "";
    let selectedRole = -1;
    let draggedStudioRole = null;
    let studioResizeDrag = null;
    let studioUndoStack = [];
    let studioRedoStack = [];
    let activeConfigPath = "";
    let livePreviewTimer = null;
    let livePreviewController = null;
    let livePreviewUrl = null;
    let playlistDragIndex = null;
    let playlistPreviewObserver = null;
    let previewFrameWidth = 16;
    let previewFrameHeight = 9;
    const previewProfileSelect = document.getElementById("previewProfile");

    function selectedPreviewProfile() {
      return previewProfileSelect?.value || "auto";
    }

    function resolvedPreviewProfile() {
      const selected = selectedPreviewProfile();
      if (selected !== "auto") return selected;
      const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (connection?.saveData || ["slow-2g", "2g"].includes(connection?.effectiveType)) return "economy";
      if (Number(navigator.deviceMemory || 8) <= 2) return "economy";
      return "balanced";
    }

    try {
      const savedPreviewProfile = localStorage.getItem("kmsMosaicPreviewProfile");
      if (["auto", "quality", "balanced", "economy"].includes(savedPreviewProfile)) {
        previewProfileSelect.value = savedPreviewProfile;
      }
    } catch (err) {}
    let webrtcPeer = null;
    let webrtcPeerId = null;
    let webrtcStream = null;
    let webrtcRetryTimer = null;
    let webrtcHeartbeatTimer = null;
    let sceneCatalog = { scenes: [], schedules: [] };
    let paneTemplateCatalog = { templates: [] };
    let selectedPaneTemplateId = "";
    let remoteState = null;
    let healthTimer = null;
    if (layoutSelect) {
      layoutNames.forEach(name => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        layoutSelect.appendChild(option);
      });
    }

    function slotName(index) {
      return roleName(index);
    }

    function roleName(role) {
      if (role >= 0 && role < 26) return `Pane ${String.fromCharCode(65 + role)}`;
      return `Pane ${role + 1}`;
    }

    function orderedRolesFromState(nextState) {
      const paneCount = Math.max(1, Number(nextState?.pane_count || 2));
      const fallback = Array.from({ length: paneCount }, (_, i) => i);
      const text = String(nextState?.roles || "").trim();
      if (!text) return fallback;
      const ordered = [];
      const used = new Set();
      for (const char of text) {
        let role = -1;
        if (char >= "0" && char <= "9") role = Number(char);
        else if (char >= "A" && char <= "Z") role = char.charCodeAt(0) - 65;
        else if (char >= "a" && char <= "z") role = char.charCodeAt(0) - 97;
        if (role < 0 || role >= paneCount || used.has(role)) continue;
        ordered.push(role);
        used.add(role);
      }
      return ordered.length === paneCount ? ordered : fallback;
    }

    function visibilityModeForState(nextState = state) {
      if (!nextState) return "neither";
      if (typeof nextState.visibility_mode === "string" && nextState.visibility_mode) {
        if (nextState.visibility_mode === "no-panes") return "no-terminal";
        return nextState.visibility_mode;
      }
      if (nextState?.flags?.no_video) return "no-video";
      if (nextState?.flags?.no_panes) return "no-terminal";
      return "neither";
    }

    function normalizeVisibilityFlags() {
      if (!state) return;
      const mode = visibilityModeForState(state);
      state.visibility_mode = mode;
    }

    function currentVisibilityMode() {
      normalizeVisibilityFlags();
      return visibilityModeForState(state);
    }

    function visibilityModeHidesRole(nextState, role) {
      const mode = visibilityModeForState(nextState);
      const paneType = nextState?.pane_types?.[role] || "terminal";
      if (mode === "no-video") return paneType === "mpv";
      if (mode === "no-terminal") return paneType === "terminal";
      return false;
    }

    function visibleStudioRoles(nextState = state) {
      const paneCount = Math.max(1, Number(nextState?.pane_count || 2));
      const roles = [];
      for (let role = 0; role < paneCount; role += 1) {
        if (!visibilityModeHidesRole(nextState, role)) roles.push(role);
      }
      return roles;
    }

    function buildStudioSlots(nextState, count) {
      const screen = { x: 0, y: 0, w: 100, h: 100 };
      if (count <= 1) {
        return [screen];
      }
      if (count === 2) {
        return layoutNames.indexOf(nextState.layout || "stack") === 1
          ? splitHorizontal(screen, 2)
          : splitVertical(screen, 2);
      }
      const paneCount = Math.max(1, Number(count > 0 ? count - 1 : 1));
      const roleCount = Math.max(1, Number(count || 1));
      const mode = layoutNames.indexOf(nextState.layout || "stack");
      const splitPct = Math.max(10, Math.min(90, Number(nextState.pane_split || 50)));
      const colPct = Math.max(20, Math.min(80, 100 - Number(nextState.right_frac || 33)));
      let slots = Array.from({ length: roleCount }, () => ({ x: 0, y: 0, w: 0, h: 0 }));

      if (paneCount > 2) {
        if (mode === 0) slots = splitVertical(screen, roleCount);
        else if (mode === 1) slots = splitHorizontal(screen, roleCount);
        else if (mode === 2) {
          const wleft = Math.floor(screen.w * colPct / 100);
          slots[0] = { x: wleft, y: 0, w: screen.w - wleft, h: screen.h };
          const paneRects = tileRects({ x: 0, y: 0, w: wleft, h: screen.h }, paneCount);
          paneRects.forEach((rect, index) => { slots[index + 1] = rect; });
        } else if (mode === 3) {
          const wleft = Math.floor(screen.w * colPct / 100);
          slots[0] = { x: 0, y: 0, w: wleft, h: screen.h };
          const paneRects = tileRects({ x: wleft, y: 0, w: screen.w - wleft, h: screen.h }, paneCount);
          paneRects.forEach((rect, index) => { slots[index + 1] = rect; });
        } else if (mode === 4) {
          const htop = Math.floor(screen.h * splitPct / 100);
          const paneRects = tileRects({ x: 0, y: screen.h - htop, w: screen.w, h: htop }, paneCount);
          slots[0] = { x: 0, y: 0, w: screen.w, h: screen.h - htop };
          paneRects.forEach((rect, index) => { slots[index + 1] = rect; });
        } else if (mode === 5) {
          const htop = Math.floor(screen.h * splitPct / 100);
          slots[0] = { x: 0, y: screen.h - htop, w: screen.w, h: htop };
          const paneRects = tileRects({ x: 0, y: 0, w: screen.w, h: screen.h - htop }, paneCount);
          paneRects.forEach((rect, index) => { slots[index + 1] = rect; });
        } else {
          slots[0] = screen;
          const paneRects = tileRects({ x: 10, y: 10, w: 80, h: 80 }, paneCount);
          paneRects.forEach((rect, index) => { slots[index + 1] = rect; });
        }
      } else {
        const s = [];
        if (mode === 6) {
          s.push(screen);
          const horizontal = Number(nextState.rotation || 0) === 0 || Number(nextState.rotation || 0) === 180;
          if (horizontal) {
            const wleft = Math.floor(screen.w * splitPct / 100);
            s.push({ x: 0, y: 0, w: wleft, h: screen.h });
            s.push({ x: wleft, y: 0, w: screen.w - wleft, h: screen.h });
          } else {
            const htop = Math.floor(screen.h * splitPct / 100);
            s.push({ x: 0, y: screen.h - htop, w: screen.w, h: htop });
            s.push({ x: 0, y: 0, w: screen.w, h: screen.h - htop });
          }
        } else if (mode === 0) {
          const h = Math.floor(screen.h / 3);
          const h2 = h;
          s.push({ x: 0, y: screen.h - h, w: screen.w, h });
          s.push({ x: 0, y: screen.h - h - h2, w: screen.w, h: h2 });
          s.push({ x: 0, y: 0, w: screen.w, h: screen.h - h - h2 });
        } else if (mode === 1) {
          const w = Math.floor(screen.w / 3);
          const w2 = w;
          s.push({ x: 0, y: 0, w, h: screen.h });
          s.push({ x: w, y: 0, w: w2, h: screen.h });
          s.push({ x: w + w2, y: 0, w: screen.w - w - w2, h: screen.h });
        } else if (mode === 2) {
          const wleft = Math.floor(screen.w * colPct / 100);
          const wright = screen.w - wleft;
          const htop = Math.floor(screen.h * splitPct / 100);
          const hbot = screen.h - htop;
          s.push({ x: 0, y: screen.h - htop, w: wleft, h: htop });
          s.push({ x: 0, y: 0, w: wleft, h: hbot });
          s.push({ x: wleft, y: 0, w: wright, h: screen.h });
        } else if (mode === 3) {
          const wleft = Math.floor(screen.w * colPct / 100);
          const wright = screen.w - wleft;
          const htop = Math.floor(screen.h * splitPct / 100);
          const hbot = screen.h - htop;
          s.push({ x: 0, y: 0, w: wleft, h: screen.h });
          s.push({ x: wleft, y: screen.h - htop, w: wright, h: htop });
          s.push({ x: wleft, y: 0, w: wright, h: hbot });
        } else if (mode === 4) {
          const wleft = Math.floor(screen.w * colPct / 100);
          const wright = screen.w - wleft;
          const htop = Math.floor(screen.h * splitPct / 100);
          const hbot = screen.h - htop;
          s.push({ x: 0, y: screen.h - htop, w: wleft, h: htop });
          s.push({ x: wleft, y: screen.h - htop, w: wright, h: htop });
          s.push({ x: 0, y: 0, w: screen.w, h: hbot });
        } else {
          const wleft = Math.floor(screen.w * colPct / 100);
          const wright = screen.w - wleft;
          const htop = Math.floor(screen.h * splitPct / 100);
          const hbot = screen.h - htop;
          s.push({ x: 0, y: screen.h - htop, w: screen.w, h: htop });
          s.push({ x: 0, y: 0, w: wleft, h: hbot });
          s.push({ x: wleft, y: 0, w: wright, h: hbot });
        }
        slots = s;
      }

      return slots;
    }

    function visibilityLayoutForState(nextState = state) {
      const roleCount = Math.max(1, Number(nextState?.pane_count || 2));
      const allRoles = Array.from({ length: roleCount }, (_, role) => role);
      const visibleRoles = allRoles.filter((role) => !visibilityModeHidesRole(nextState, role));
      const rects = Array.from({ length: roleCount }, () => ({ x: 0, y: 0, w: 0, h: 0 }));
      const mode = visibilityModeForState(nextState);
      const splitTree = nextState === state ? normalizeSplitTreeState() : parseSplitTreeSpec(nextState.split_tree || "");

      if (visibleRoles.length === roleCount && splitTree) {
        splitTreeApplyRects(splitTree, { x: 0, y: 0, w: 100, h: 100 }, rects);
        return { mode, visibleRoles, hiddenRoles: [], rects };
      }

      const orderedRoles = splitTree ? (() => {
        const roles = [];
        splitTreeCollectRoles(splitTree, roles);
        return roles;
      })() : (() => {
        const perm = parseRolesString(nextState);
        return [...allRoles].sort((left, right) => perm[left] - perm[right]);
      })();
      const visibleOrderedRoles = orderedRoles.filter((role) => visibleRoles.includes(role));
      const slots = buildStudioSlots(nextState, visibleOrderedRoles.length || 1);
      visibleOrderedRoles.forEach((role, index) => {
        rects[role] = slots[index] || { x: 0, y: 0, w: 100, h: 100 };
      });
      return {
        mode,
        visibleRoles: visibleOrderedRoles,
        hiddenRoles: allRoles.filter((role) => !visibleRoles.includes(role)),
        rects,
      };
    }

    function parseMpvOptionGroups(opts) {
      const groups = {
        videoMode: "", shaders: [], other: [], hwdec: "", scale: "",
        deband: "", interpolation: "", videoSync: "",
      };
      const structuredValues = {
        hwdec: new Set(["auto-copy-safe", "no"]),
        scale: new Set(["bilinear", "bicubic", "lanczos"]),
        deband: new Set(["yes", "no"]),
        interpolation: new Set(["yes", "no"]),
        "video-sync": new Set(["audio", "display-resample", "display-vdrop"]),
      };
      (Array.isArray(opts) ? opts : []).forEach((opt) => {
        const value = String(opt || "").trim();
        if (!value) return;
        if (value === "no-audio" || value === "audio=no" || value === "ao=null" || value === "mpv-out=no-audio" || value === "aid=no") {
          return;
        }
        if (value === "mute=yes" || value === "mute=no") return;
        if (value === "loop-file=no" || value === "loop-file=yes" || value === "loop-file=inf") return;
        if (value === "vid=no") {
          groups.videoMode = "audio-only";
          return;
        }
        const separator = value.indexOf("=");
        if (separator > 0) {
          const key = value.slice(0, separator);
          const optionValue = value.slice(separator + 1);
          if (structuredValues[key]?.has(optionValue)) {
            if (key === "video-sync") groups.videoSync = optionValue;
            else groups[key] = optionValue;
            return;
          }
        }
        const shaderMarker = "glsl-shaders=";
        const shaderIndex = value.indexOf(shaderMarker);
        if (shaderIndex >= 0) {
          groups.shaders.push(value.slice(shaderIndex + shaderMarker.length));
          return;
        }
        groups.other.push(value);
      });
      return groups;
    }

    function buildMpvOptsFromParts(parts) {
      const opts = [];
      if (parts.videoMode === "audio-only") {
        opts.push("vid=no");
      }
      if (parts.hwdec) opts.push(`hwdec=${parts.hwdec}`);
      if (parts.scale) opts.push(`scale=${parts.scale}`);
      if (parts.deband) opts.push(`deband=${parts.deband}`);
      if (parts.interpolation) opts.push(`interpolation=${parts.interpolation}`);
      if (parts.videoSync) opts.push(`video-sync=${parts.videoSync}`);
      String(parts.shadersText || "")
        .split("\n")
        .map(v => v.trim())
        .filter(Boolean)
        .forEach(shader => opts.push(`glsl-shaders=${shader}`));
      String(parts.otherText || "")
        .split("\n")
        .map(v => v.trim())
        .filter(Boolean)
        .forEach(opt => opts.push(opt));
      return opts;
    }

    function syncInspectorPaneMpvOpts(paneIndex) {
      if (!state || paneIndex < 0) return;
      const panscanEl = document.getElementById("inspectorPanePanscan");
      const shadersEl = document.getElementById("inspectorPaneShaders");
      const otherEl = document.getElementById("inspectorPaneMpvOpts");
      const watchdogEl = document.getElementById("inspectorPaneWatchdog");
      if (!panscanEl || !shadersEl || !otherEl || !watchdogEl) return;
      state.pane_mpv_opts[paneIndex] = buildMpvOptsFromParts({
        hwdec: document.getElementById("inspectorPaneHwdec")?.value || "",
        scale: document.getElementById("inspectorPaneScale")?.value || "",
        deband: document.getElementById("inspectorPaneDeband")?.value || "",
        interpolation: document.getElementById("inspectorPaneInterpolation")?.value || "",
        videoSync: document.getElementById("inspectorPaneVideoSync")?.value || "",
        shadersText: shadersEl.value,
        otherText: otherEl.value,
      });
      state.pane_panscan[paneIndex] = panscanEl.value;
      state.pane_watchdogs[paneIndex] = Math.max(0, parseInt(watchdogEl.value || "0", 10) || 0);
      state.pane_sync_groups[paneIndex] = document.getElementById("inspectorPaneSyncGroup")?.value.trim() || "";
    }

    async function restartPane(paneIndex) {
      const response = await fetch("/api/panes/restart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pane: paneIndex }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      setStatus(`Restart requested for ${roleTitle(paneIndex)}.`, false, true);
      return payload;
    }

    function roleType(role) {
      return state?.pane_types?.[role] === "mpv" ? "video" : "terminal";
    }

    function selectedPaneQueueField() {
      return document.getElementById("videoList");
    }

    function selectedPaneQueueEditor() {
      return document.getElementById("playlistEditor");
    }

    function selectedPaneQueueNote() {
      return document.getElementById("queueEditorNote");
    }

    function selectedPaneAddQueueButton() {
      return document.getElementById("addQueueItemBtn");
    }

    function selectedPaneStateDetail() {
      if (!state || selectedRole < 0) {
        return {
          selectedRole: -1,
          paneType: "none",
          roleLabel: "",
          title: "Selected Pane",
          summary: "Select a pane on the board to edit it.",
          hasSelection: false,
        };
      }
      const paneType = selectedPaneType();
      const roleLabel = roleTitle(selectedRole);
      return {
        selectedRole,
        paneType,
        roleLabel,
        title: roleLabel,
        summary: paneType === "mpv" ? "mpv pane" : "terminal pane",
        hasSelection: true,
      };
    }

    function dispatchSelectedPaneState() {
      window.dispatchEvent(new CustomEvent("kms:selected-pane-state", {
        detail: selectedPaneStateDetail(),
      }));
    }

    function queueEditorContext() {
      if (!state) return null;
      const paneType = selectedPaneType();
      if (selectedRole < 0) {
        return {
          emptyMessage: "Select an mpv pane to view its queue.",
          paths: [],
          editable: false,
          paneType: "none",
          role: -1,
          note: "Select an mpv pane to view or edit its queue.",
          apply() {}
        };
      }
      if (paneType !== "mpv") {
        return {
          emptyMessage: "This pane does not have a media queue.",
          paths: [],
          editable: false,
          paneType,
          role: selectedRole,
          note: `${roleTitle(selectedRole)} is a terminal pane. Switch it to mpv to edit a queue.`,
          apply() {}
        };
      }
      const paneIndex = selectedRole;
      const playlistPath = state.pane_playlists?.[paneIndex] || "";
      const paneMpvOpts = Array.isArray(state.pane_mpv_opts?.[paneIndex]) ? state.pane_mpv_opts[paneIndex].slice() : [];
      const noteParts = [`This queue controls ${roleTitle(selectedRole)}.`];
      if (playlistPath) noteParts.push(`Playlist: ${playlistPath}`);
      if (paneMpvOpts.length) noteParts.push(`${paneMpvOpts.length} pane-local mpv option${paneMpvOpts.length === 1 ? "" : "s"}.`);
      return {
        emptyMessage: "No videos queued yet. Add one below or open Bulk Add Videos.",
        paths: Array.isArray(state.pane_video_paths?.[paneIndex]) ? state.pane_video_paths[paneIndex].slice() : [],
        editable: true,
        paneType,
        role: selectedRole,
        note: noteParts.join(" "),
        apply(paths) {
          state.pane_video_paths[paneIndex] = paths.slice();
          const queueField = selectedPaneQueueField();
          if (queueField) queueField.value = state.pane_video_paths[paneIndex].join("\n");
        }
      };
    }

    function isRemoteMediaUrl(path) {
      return /^https?:\/\//i.test(String(path || "").trim());
    }

    function remoteMediaPathInfo(path) {
      const value = String(path || "").trim();
      if (!value) return "";
      if (!isRemoteMediaUrl(value)) return value;
      try {
        const parsed = new URL(value);
        const params = [];
        parsed.searchParams.forEach((paramValue, key) => {
          params.push(key, paramValue);
        });
        return `${parsed.pathname} ${params.join(" ")}`.trim();
      } catch (_) {
        return value;
      }
    }

    function mediaUrl(path) {
      const value = String(path || "").trim();
      if (!value) return "";
      if (isRemoteMediaUrl(value)) return value;
      return `/api/media?path=${encodeURIComponent(value)}`;
    }

    function playlistThumbCacheKey(path, metrics) {
      return [
        "kmsmosaic-thumb-v2",
        String(path || "").trim(),
        String(metrics.rotation || 0),
        String(metrics.aspectRatio || ""),
        metrics.cover ? "cover" : "contain",
      ].join("|");
    }

    function readCachedPlaylistThumb(path, metrics) {
      try {
        const raw = localStorage.getItem(playlistThumbCacheKey(path, metrics));
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object") return null;
        const src = typeof parsed.src === "string" ? parsed.src : "";
        const duration = Number.isFinite(parsed.duration) && parsed.duration > 0 ? parsed.duration : null;
        if (!src && duration == null) return null;
        return {
          src,
          duration,
          savedAt: Number(parsed.savedAt || 0) || 0,
        };
      } catch (_) {
        return null;
      }
    }

    function writeCachedPlaylistThumb(path, metrics, src, duration) {
      try {
        const value = {
          src,
          duration: Number.isFinite(duration) && duration > 0 ? duration : null,
          savedAt: Date.now(),
        };
        localStorage.setItem(playlistThumbCacheKey(path, metrics), JSON.stringify(value));
      } catch (_) {
        return;
      }
    }

    function isLikelyImagePath(path) {
      const value = String(path || "").trim();
      if (!value) return false;
      const probe = isRemoteMediaUrl(value) ? remoteMediaPathInfo(value) : value;
      return /\.(avif|bmp|gif|jpe?g|png|webp)(?:$|[^a-z0-9])/i.test(probe);
    }

    function isLikelyVideoPath(path) {
      const value = String(path || "").trim();
      if (!value) return false;
      const probe = isRemoteMediaUrl(value) ? remoteMediaPathInfo(value) : value;
      if (/\.(m4v|mkv|mov|mp4|mpeg|mpg|ts|webm)(?:$|[^a-z0-9])/i.test(probe)) return true;
      return isRemoteMediaUrl(value) && !isLikelyImagePath(value);
    }

    function targetPlaylistMetrics(role = (selectedRole >= 0 ? selectedRole : 0)) {
      const rotation = effectivePlaylistThumbRotationDegrees(role);
      const rects = computeStudioRects(state);
      const rawRect = rects?.[role] || { w: 16, h: 9 };
      const rect = transformStudioPaneRect(rawRect);
      const pw = Math.max(1, Number(rect?.w || 16));
      const ph = Math.max(1, Number(rect?.h || 9));
      // Scale pane rect by the board's visual aspect ratio so portrait/landscape detection
      // accounts for the display rotation (the studio board renders 9:16 at rotation 90/270).
      const displayRotation = normalizedRotationDegrees();
      const boardPortrait = displayRotation === 90 || displayRotation === 270;
      const physW = pw * (boardPortrait ? 9 : 16);
      const physH = ph * (boardPortrait ? 16 : 9);
      const panscanValue = String(state?.pane_panscan?.[role] || "").trim();
      const panscan = Number.parseFloat(panscanValue || "0");
      const thumbHeight = 88;
      const thumbWidth = Math.round(Math.min(156, Math.max(84, thumbHeight * (physW / Math.max(1, physH)))));
      return {
        rotation,
        aspectRatio: `${physW} / ${physH}`,
        cover: Number.isFinite(panscan) && panscan > 0,
        isPortrait: physH > physW,
        thumbHeight,
        thumbWidth,
      };
    }

    function playlistThumbMarkup(path, index, metrics) {
      const value = String(path || "").trim();
      if (!value) return "";
      const src = mediaUrl(value);
      const total = metrics.rotation === 270 ? 0 : metrics.rotation;
      const quarterTurn = total === 90 || total === 270;
      const cached = readCachedPlaylistThumb(value, metrics);
      let mediaStyle = "";
      if (total) {
        if (quarterTurn) {
          const [mw, mh] = metrics.aspectRatio.split(" / ").map(Number);
          const thumbW = 120;
          const thumbH = Math.round(thumbW * mh / mw);
          mediaStyle = ` style="position:absolute;width:${thumbH}px;height:${thumbW}px;top:50%;left:50%;transform:translate(-50%,-50%) rotate(${total}deg);object-fit:cover;"`;
        } else {
          mediaStyle = ` style="transform: rotate(${total}deg);"`;
        }
      }
      if (isLikelyImagePath(value)) {
        return `<div class="playlist-thumb-media${quarterTurn ? " quarter-turn" : ""}"><img src="${src}" alt="Preview for queue item ${index + 1}" loading="lazy"${mediaStyle} /></div>`;
      }
      if (isLikelyVideoPath(value)) {
        if (cached?.src) {
          return `<div class="playlist-thumb-media${quarterTurn ? " quarter-turn" : ""}"><img src="${cached.src}" alt="Preview for queue item ${index + 1}" loading="lazy"${mediaStyle} /></div>`;
        }
        return `<div class="playlist-thumb-media${quarterTurn ? " quarter-turn" : ""}"><video data-preview-video="${index}" data-preview-path="${value.replace(/"/g, "&quot;")}" data-preview-src="${src}" muted playsinline preload="metadata"${mediaStyle}></video></div>`;
      }
      return "";
    }

    function formatMediaDuration(totalSeconds) {
      const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const secs = seconds % 60;
      if (hours > 0) {
        return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
      }
      return `${minutes}:${String(secs).padStart(2, "0")}`;
    }

    function compressPlaylistPaths(paths) {
      const input = Array.isArray(paths) ? paths : [];
      const groups = [];
      input.forEach((path) => {
        const value = String(path || "");
        const last = groups[groups.length - 1];
        if (last && last.path === value) {
          last.count += 1;
          return;
        }
        groups.push({ path: value, count: 1 });
      });
      return groups;
    }

    function expandPlaylistGroups(groups) {
      const out = [];
      (Array.isArray(groups) ? groups : []).forEach((group) => {
        const path = String(group?.path || "");
        const count = Math.max(1, Number(group?.count || 1));
        for (let i = 0; i < count; i += 1) out.push(path);
      });
      return out;
    }

    function roleTitle(role) {
      return roleName(role);
    }

    function readInt(id, fallback) {
      const value = parseInt(document.getElementById(id).value, 10);
      return Number.isFinite(value) ? value : fallback;
    }

    function ensurePaneCommands(nextState) {
      const count = Math.max(1, Number(nextState.pane_count || 2));
      nextState.pane_count = count;
      nextState.pane_commands = Array.isArray(nextState.pane_commands) ? nextState.pane_commands.slice(0, count) : [];
      nextState.pane_types = Array.isArray(nextState.pane_types) ? nextState.pane_types.slice(0, count) : [];
      nextState.pane_type_raw = Array.isArray(nextState.pane_type_raw) ? nextState.pane_type_raw.slice(0, count) : [];
      nextState.pane_type_settings = Array.isArray(nextState.pane_type_settings)
        ? nextState.pane_type_settings.slice(0, count).map((value) => (
            value && typeof value === "object" && !Array.isArray(value)
              ? { ...value }
              : {}
          ))
        : [];
      nextState.pane_playlists = Array.isArray(nextState.pane_playlists) ? nextState.pane_playlists.slice(0, count) : [];
      nextState.pane_playlist_extended = Array.isArray(nextState.pane_playlist_extended) ? nextState.pane_playlist_extended.slice(0, count) : [];
      nextState.pane_playlist_fifos = Array.isArray(nextState.pane_playlist_fifos) ? nextState.pane_playlist_fifos.slice(0, count) : [];
      nextState.pane_mpv_outs = Array.isArray(nextState.pane_mpv_outs) ? nextState.pane_mpv_outs.slice(0, count) : [];
      nextState.pane_video_rotate = Array.isArray(nextState.pane_video_rotate) ? nextState.pane_video_rotate.slice(0, count) : [];
      nextState.pane_panscan = Array.isArray(nextState.pane_panscan) ? nextState.pane_panscan.slice(0, count) : [];
      nextState.pane_watchdogs = Array.isArray(nextState.pane_watchdogs) ? nextState.pane_watchdogs.slice(0, count) : [];
      nextState.pane_sync_groups = Array.isArray(nextState.pane_sync_groups) ? nextState.pane_sync_groups.slice(0, count) : [];
      nextState.pane_video_paths = Array.isArray(nextState.pane_video_paths)
        ? nextState.pane_video_paths.slice(0, count).map(paths => Array.isArray(paths) ? paths.slice() : [])
        : [];
      nextState.pane_mpv_opts = Array.isArray(nextState.pane_mpv_opts)
        ? nextState.pane_mpv_opts.slice(0, count).map(opts => Array.isArray(opts) ? opts.slice() : [])
        : [];
      while (nextState.pane_commands.length < count) nextState.pane_commands.push("");
      while (nextState.pane_types.length < count) nextState.pane_types.push("terminal");
      while (nextState.pane_type_raw.length < count) nextState.pane_type_raw.push("");
      while (nextState.pane_type_settings.length < count) nextState.pane_type_settings.push({});
      while (nextState.pane_playlists.length < count) nextState.pane_playlists.push("");
      while (nextState.pane_playlist_extended.length < count) nextState.pane_playlist_extended.push("");
      while (nextState.pane_playlist_fifos.length < count) nextState.pane_playlist_fifos.push("");
      while (nextState.pane_mpv_outs.length < count) nextState.pane_mpv_outs.push("");
      while (nextState.pane_video_rotate.length < count) nextState.pane_video_rotate.push("");
      while (nextState.pane_panscan.length < count) nextState.pane_panscan.push("");
      while (nextState.pane_watchdogs.length < count) nextState.pane_watchdogs.push(0);
      while (nextState.pane_sync_groups.length < count) nextState.pane_sync_groups.push("");
      while (nextState.pane_video_paths.length < count) nextState.pane_video_paths.push([]);
      while (nextState.pane_mpv_opts.length < count) nextState.pane_mpv_opts.push([]);
    }

    function skipTreeWs(spec, index) {
      let i = index;
      while (i < spec.length && /\s/.test(spec[i])) i += 1;
      return i;
    }

    function parseSplitTreeSpec(spec) {
      const text = String(spec || "").trim();
      if (!text) return null;

      function parseNode(startIndex) {
        let index = skipTreeWs(text, startIndex);
        if (index >= text.length) return null;
        if (/\d/.test(text[index])) {
          let end = index + 1;
          while (end < text.length && /\d/.test(text[end])) end += 1;
          return [{ leaf: true, role: Number(text.slice(index, end)) }, end];
        }
        let kind = null;
        if (text.startsWith("row", index)) {
          kind = "row";
          index += 3;
        } else if (text.startsWith("col", index)) {
          kind = "col";
          index += 3;
        } else {
          return null;
        }
        index = skipTreeWs(text, index);
        if (text[index] !== ":") return null;
        index += 1;
        index = skipTreeWs(text, index);
        let pctEnd = index;
        while (pctEnd < text.length && /\d/.test(text[pctEnd])) pctEnd += 1;
        if (pctEnd === index) return null;
        const pct = Number(text.slice(index, pctEnd));
        index = skipTreeWs(text, pctEnd);
        if (text[index] !== "(") return null;
        const left = parseNode(index + 1);
        if (!left) return null;
        index = skipTreeWs(text, left[1]);
        if (text[index] !== ",") return null;
        const right = parseNode(index + 1);
        if (!right) return null;
        index = skipTreeWs(text, right[1]);
        if (text[index] !== ")") return null;
        return [{ leaf: false, kind, pct, first: left[0], second: right[0] }, index + 1];
      }

      const parsed = parseNode(0);
      if (!parsed) return null;
      const end = skipTreeWs(text, parsed[1]);
      return end === text.length ? parsed[0] : null;
    }

    function serializeSplitTree(node) {
      if (!node) return "";
      if (node.leaf) return String(node.role);
      return `${node.kind}:${Math.round(node.pct)}(${serializeSplitTree(node.first)},${serializeSplitTree(node.second)})`;
    }

    function cloneSplitTree(node) {
      if (!node) return null;
      if (node.leaf) return { leaf: true, role: node.role };
      return {
        leaf: false,
        kind: node.kind,
        pct: node.pct,
        first: cloneSplitTree(node.first),
        second: cloneSplitTree(node.second)
      };
    }

    function clampStudioPercent(value, fallback = 50) {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return Math.max(STUDIO_SIZE_MIN, Math.min(STUDIO_SIZE_MAX, Math.round(Number(fallback) || 50)));
      return Math.max(STUDIO_SIZE_MIN, Math.min(STUDIO_SIZE_MAX, Math.round(parsed)));
    }

    function normalizeSplitTreeState() {
      if (!state) return null;
      if (!state.splitTreeModel) {
        state.splitTreeModel = parseSplitTreeSpec(state.split_tree || "");
      }
      return state.splitTreeModel;
    }

    function syncSplitTreeState() {
      if (!state) return;
      state.split_tree = state.splitTreeModel ? serializeSplitTree(state.splitTreeModel) : "";
    }

    function captureStudioHistorySnapshot() {
      if (!state) return null;
      syncSplitTreeState();
      return JSON.stringify(state);
    }

    function updateStudioHistoryButtons() {
      const undoButton = document.getElementById("studioUndoBtn");
      const redoButton = document.getElementById("studioRedoBtn");
      if (undoButton) undoButton.disabled = studioUndoStack.length === 0;
      if (redoButton) redoButton.disabled = studioRedoStack.length === 0;
    }

    function commitStudioHistory(previousSnapshot) {
      if (!previousSnapshot) return false;
      const currentSnapshot = captureStudioHistorySnapshot();
      if (!currentSnapshot || currentSnapshot === previousSnapshot) return false;
      studioUndoStack.push(previousSnapshot);
      if (studioUndoStack.length > STUDIO_HISTORY_LIMIT) studioUndoStack.shift();
      studioRedoStack = [];
      updateStudioHistoryButtons();
      return true;
    }

    function restoreStudioHistorySnapshot(snapshot) {
      if (!snapshot) return false;
      try {
        const restored = JSON.parse(snapshot);
        fillForm(restored, activeConfigPath, rawConfigText);
        updateStudioHistoryButtons();
        return true;
      } catch (_) {
        return false;
      }
    }

    function undoStudioLayout() {
      if (!state || !studioUndoStack.length) return false;
      const currentSnapshot = captureStudioHistorySnapshot();
      const previousSnapshot = studioUndoStack.pop();
      if (currentSnapshot) studioRedoStack.push(currentSnapshot);
      const restored = restoreStudioHistorySnapshot(previousSnapshot);
      setStatus(restored ? "Undid layout change." : "Could not undo the layout change.", !restored);
      return restored;
    }

    function redoStudioLayout() {
      if (!state || !studioRedoStack.length) return false;
      const currentSnapshot = captureStudioHistorySnapshot();
      const nextSnapshot = studioRedoStack.pop();
      if (currentSnapshot) studioUndoStack.push(currentSnapshot);
      const restored = restoreStudioHistorySnapshot(nextSnapshot);
      setStatus(restored ? "Redid layout change." : "Could not redo the layout change.", !restored);
      return restored;
    }

    function snapStudioSize(value, bypassSnap = false) {
      const normalized = Math.max(STUDIO_SIZE_MIN, Math.min(STUDIO_SIZE_MAX, Number(value) || 0));
      if (bypassSnap) return { value: normalized, snapped: false };
      let nearest = normalized;
      let distance = Infinity;
      STUDIO_SNAP_POINTS.forEach((point) => {
        const nextDistance = Math.abs(point - normalized);
        if (nextDistance < distance) {
          nearest = point;
          distance = nextDistance;
        }
      });
      return distance <= 1.75
        ? { value: nearest, snapped: true }
        : { value: normalized, snapped: false };
    }

    function splitTreeCollectRoles(node, out) {
      if (!node) return;
      if (node.leaf) {
        out.push(node.role);
        return;
      }
      splitTreeCollectRoles(node.first, out);
      splitTreeCollectRoles(node.second, out);
    }

    function balancedTreeForRoles(roles, preferRows = false) {
      if (!roles.length) return null;
      if (roles.length === 1) return { leaf: true, role: roles[0] };
      const mid = Math.ceil(roles.length / 2);
      return {
        leaf: false,
        kind: preferRows ? "row" : "col",
        pct: 50,
        first: balancedTreeForRoles(roles.slice(0, mid), !preferRows),
        second: balancedTreeForRoles(roles.slice(mid), !preferRows)
      };
    }

    function presetTreeFromState(nextState) {
      const paneCount = Math.max(1, Number(nextState?.pane_count || 2));
      if (paneCount <= 1) return { leaf: true, role: 0 };
      const roles = orderedRolesFromState(nextState);
      const [primaryRole, ...secondaryRoles] = roles;
      const layout = nextState?.layout || "stack";
      const colPct = Math.max(20, Math.min(80, 100 - Number(nextState?.right_frac || 33)));
      const rowPct = Math.max(10, Math.min(90, Number(nextState?.pane_split || 50)));
      if (layout === "stack") return balancedTreeForRoles(roles, true);
      if (layout === "row") return balancedTreeForRoles(roles, false);
      if (layout === "overlay") {
        return {
          leaf: false,
          kind: Number(nextState?.rotation || 0) === 90 || Number(nextState?.rotation || 0) === 270 ? "row" : "col",
          pct: rowPct,
          first: { leaf: true, role: primaryRole },
          second: balancedTreeForRoles(secondaryRoles, false)
        };
      }
      if (layout === "2x1") {
        return {
          leaf: false,
          kind: "col",
          pct: colPct,
          first: balancedTreeForRoles(secondaryRoles, true),
          second: { leaf: true, role: primaryRole }
        };
      }
      if (layout === "1x2") {
        return {
          leaf: false,
          kind: "col",
          pct: colPct,
          first: { leaf: true, role: primaryRole },
          second: balancedTreeForRoles(secondaryRoles, true)
        };
      }
      if (layout === "2over1") {
        return {
          leaf: false,
          kind: "row",
          pct: 100 - rowPct,
          first: balancedTreeForRoles(secondaryRoles, false),
          second: { leaf: true, role: primaryRole }
        };
      }
      if (layout === "1over2") {
        return {
          leaf: false,
          kind: "row",
          pct: rowPct,
          first: { leaf: true, role: primaryRole },
          second: balancedTreeForRoles(secondaryRoles, false)
        };
      }
      return balancedTreeForRoles(roles, false);
    }

    function ensureSplitTreeModel() {
      const parsed = normalizeSplitTreeState();
      if (parsed) return parsed;
      state.splitTreeModel = presetTreeFromState(state);
      syncSplitTreeState();
      return state.splitTreeModel;
    }

    function splitTreeApplyRects(node, area, rects) {
      if (!node) return;
      if (node.leaf) {
        rects[node.role] = area;
        return;
      }
      const pct = clampStudioPercent(node.pct || 50, node.pct || 50);
      if (node.kind === "row") {
        const firstH = area.h * pct / 100;
        splitTreeApplyRects(node.first, { x: area.x, y: area.y, w: area.w, h: firstH }, rects);
        splitTreeApplyRects(node.second, { x: area.x, y: area.y + firstH, w: area.w, h: area.h - firstH }, rects);
      } else {
        const firstW = area.w * pct / 100;
        splitTreeApplyRects(node.first, { x: area.x, y: area.y, w: firstW, h: area.h }, rects);
        splitTreeApplyRects(node.second, { x: area.x + firstW, y: area.y, w: area.w - firstW, h: area.h }, rects);
      }
    }

    function splitTreeReplaceLeaf(node, role, replacer) {
      if (!node) return false;
      if (node.leaf) {
        if (node.role !== role) return false;
        const next = replacer(node);
        Object.keys(node).forEach(key => delete node[key]);
        Object.assign(node, next);
        return true;
      }
      return splitTreeReplaceLeaf(node.first, role, replacer) || splitTreeReplaceLeaf(node.second, role, replacer);
    }

    function splitTreeCollapseRole(node, role) {
      if (!node || node.leaf) return false;
      if (node.first?.leaf && node.first.role === role) {
        const replacement = cloneSplitTree(node.second);
        Object.keys(node).forEach(key => delete node[key]);
        Object.assign(node, replacement);
        return true;
      }
      if (node.second?.leaf && node.second.role === role) {
        const replacement = cloneSplitTree(node.first);
        Object.keys(node).forEach(key => delete node[key]);
        Object.assign(node, replacement);
        return true;
      }
      return splitTreeCollapseRole(node.first, role) || splitTreeCollapseRole(node.second, role);
    }

    function splitTreeSwapRoles(node, firstRole, secondRole) {
      if (!node) return false;
      let changed = false;
      if (node.leaf) {
        if (node.role === firstRole) {
          node.role = secondRole;
          return true;
        }
        if (node.role === secondRole) {
          node.role = firstRole;
          return true;
        }
        return false;
      }
      changed = splitTreeSwapRoles(node.first, firstRole, secondRole) || changed;
      changed = splitTreeSwapRoles(node.second, firstRole, secondRole) || changed;
      return changed;
    }

    function splitTreeRemapRoles(node, mapping) {
      if (!node) return;
      if (node.leaf) {
        if (Object.prototype.hasOwnProperty.call(mapping, node.role)) node.role = mapping[node.role];
        return;
      }
      splitTreeRemapRoles(node.first, mapping);
      splitTreeRemapRoles(node.second, mapping);
    }

    function splitTreeNodeAtPath(node, path) {
      let current = node;
      for (const step of String(path || "")) {
        if (!current || current.leaf) return null;
        current = step === "0" ? current.first : current.second;
      }
      return current;
    }

    function splitTreePathForRole(node, role, path = "") {
      if (!node) return null;
      if (node.leaf) return node.role === role ? path : null;
      const firstPath = splitTreePathForRole(node.first, role, `${path}0`);
      if (firstPath != null) return firstPath;
      return splitTreePathForRole(node.second, role, `${path}1`);
    }

    function splitTreeTrailForRole(node, role, path = "", trail = []) {
      if (!node) return null;
      if (node.leaf) return node.role === role ? trail : null;
      const firstTrail = splitTreeTrailForRole(
        node.first,
        role,
        `${path}0`,
        trail.concat({ node, path, branch: "0", childPath: `${path}0` })
      );
      if (firstTrail) return firstTrail;
      return splitTreeTrailForRole(
        node.second,
        role,
        `${path}1`,
        trail.concat({ node, path, branch: "1", childPath: `${path}1` })
      );
    }

    function splitTreeNearestAncestor(trail, kind) {
      for (let index = (trail?.length || 0) - 1; index >= 0; index -= 1) {
        if (trail[index]?.node?.kind === kind) return trail[index];
      }
      return null;
    }

    function splitTreeCollectAreas(node, area, out, path = "") {
      if (!node) return;
      out[path] = { x: area.x, y: area.y, w: area.w, h: area.h };
      if (node.leaf) return;
      const pct = clampStudioPercent(node.pct || 50, node.pct || 50);
      if (node.kind === "row") {
        const firstH = area.h * pct / 100;
        splitTreeCollectAreas(node.first, { x: area.x, y: area.y, w: area.w, h: firstH }, out, `${path}0`);
        splitTreeCollectAreas(node.second, { x: area.x, y: area.y + firstH, w: area.w, h: area.h - firstH }, out, `${path}1`);
      } else {
        const firstW = area.w * pct / 100;
        splitTreeCollectAreas(node.first, { x: area.x, y: area.y, w: firstW, h: area.h }, out, `${path}0`);
        splitTreeCollectAreas(node.second, { x: area.x + firstW, y: area.y, w: area.w - firstW, h: area.h }, out, `${path}1`);
      }
    }

    function splitTreeUpdateNodePct(node, pct) {
      if (!node || node.leaf) return false;
      node.pct = clampStudioPercent(pct, node.pct || 50);
      return true;
    }

    function rectAxisLength(rect, axis) {
      return axis === "h" ? Number(rect?.h || 0) : Number(rect?.w || 0);
    }

    function effectiveStudioSizeValue(value) {
      return clampStudioPercent(value, value);
    }

    function logicalResizeEdgeForSplit(kind, branchArea, area) {
      if (!branchArea || !area) return null;
      if (kind === "col") {
        const branchOnLeft = (branchArea.x + (branchArea.w / 2)) < (area.x + (area.w / 2));
        return branchOnLeft ? "right" : "left";
      }
      const branchOnTop = (branchArea.y + (branchArea.h / 2)) < (area.y + (area.h / 2));
      return branchOnTop ? "bottom" : "top";
    }

    function decorateSplitTreeAncestor(entry, areaMap) {
      if (!entry) return null;
      const area = areaMap?.[entry.path];
      const branchArea = areaMap?.[entry.childPath];
      if (!area || !branchArea) return null;
      const displayArea = transformStudioPaneRect(area);
      const displayBranch = transformStudioPaneRect(branchArea);
      const total = studioRotationDegrees();
      const logicalEdge = logicalResizeEdgeForSplit(entry.node.kind, branchArea, area);
      const edge = displayEdgeForLogicalEdge(logicalEdge, total);
      return {
        ...entry,
        area,
        branchArea,
        displayArea,
        displayBranch,
        logicalEdge,
        edge,
      };
    }

    function splitTreeResizeContext(nextState, role) {
      if (!nextState || role < 0) return null;
      const splitTree = nextState === state ? ensureSplitTreeModel() : parseSplitTreeSpec(nextState.split_tree || "");
      if (!splitTree) return null;
      const trail = splitTreeTrailForRole(splitTree, role);
      if (!trail) return null;
      const areaMap = {};
      splitTreeCollectAreas(splitTree, { x: 0, y: 0, w: 100, h: 100 }, areaMap);
      const rects = computeStudioRects(nextState);
      const rect = rects[role];
      if (!rect) return null;
      return {
        splitTree,
        trail,
        areaMap,
        rect,
        displayRect: transformStudioPaneRect(rect),
        colAncestor: decorateSplitTreeAncestor(splitTreeNearestAncestor(trail, "col"), areaMap),
        rowAncestor: decorateSplitTreeAncestor(splitTreeNearestAncestor(trail, "row"), areaMap),
      };
    }

    function splitTreeAncestorForAxis(ctx, axis) {
      return axis === "h" ? ctx?.rowAncestor : ctx?.colAncestor;
    }

    function currentStudioInputValue(role, axis) {
      const ctx = splitTreeResizeContext(state, role);
      return effectiveStudioSizeValue(rectAxisLength(ctx?.rect, axis));
    }

    function studioSizeInputMarkup(role, axis, active, value) {
      const axisLabel = axis === "h" ? "Height" : "Width";
      const title = active
        ? (axis === "w"
            ? "Adjusts the nearest vertical split."
            : "Adjusts the nearest horizontal split.")
        : (axis === "w"
            ? "This pane has no vertical split ancestor to resize."
            : "This pane has no horizontal split ancestor to resize.");
      return `<label class="studio-size-chip" data-active="${active ? "true" : "false"}" title="${title}">
        <span>${axisLabel}</span>
        <input type="number" class="studio-size-input" value="${value}" min="${STUDIO_SIZE_MIN}" max="${STUDIO_SIZE_MAX}" data-role="${role}" data-axis="${axis}" step="1" ${active ? "" : "disabled"}>
      </label>`;
    }

    function selectedPaneSizeInputMarkup(role, axis, label, active, value) {
      const title = active
        ? (axis === "w"
            ? "Adjusts the nearest vertical split."
            : "Adjusts the nearest horizontal split.")
        : (axis === "w"
            ? "This pane has no vertical split ancestor to resize."
            : "This pane has no horizontal split ancestor to resize.");
      return `
        <div class="selected-pane-size-field" data-active="${active ? "true" : "false"}" title="${title}">
          <div class="selected-pane-size-label">${label}</div>
          <input type="number" class="studio-size-input" value="${value}" min="${STUDIO_SIZE_MIN}" max="${STUDIO_SIZE_MAX}" data-role="${role}" data-axis="${axis}" step="1" ${active ? "" : "disabled"}>
        </div>
      `;
    }

    function selectedPaneSizeSectionMarkup(role) {
      const ctx = splitTreeResizeContext(state, role);
      const rect = ctx?.rect || visibilityLayoutForState(state)?.rects?.[role];
      const widthValue = effectiveStudioSizeValue(rect?.w || 0);
      const heightValue = effectiveStudioSizeValue(rect?.h || 0);
      const widthActive = !!ctx?.colAncestor;
      const heightActive = !!ctx?.rowAncestor;
      return `
        <div class="selected-pane-section">
          <h2 class="section-title">Pane Size</h2>
          <div class="selected-pane-size-group">
            ${selectedPaneSizeInputMarkup(role, "w", "Width", widthActive, widthValue)}
            ${selectedPaneSizeInputMarkup(role, "h", "Height", heightActive, heightValue)}
          </div>
        </div>
      `;
    }

    function resizePaneAxis(role, axis, requestedSize) {
      if (!state) return { ok: false, value: effectiveStudioSizeValue(requestedSize) };
      const tree = ensureSplitTreeModel();
      if (!tree) return { ok: false, value: currentStudioInputValue(role, axis) };
      const ctx = splitTreeResizeContext(state, role);
      const ancestor = splitTreeAncestorForAxis(ctx, axis);
      const fallbackValue = effectiveStudioSizeValue(rectAxisLength(ctx?.rect, axis));
      if (!Number.isFinite(Number(requestedSize))) {
        return { ok: false, value: fallbackValue, reason: "invalid" };
      }
      if (!ctx || !ancestor) return { ok: false, value: fallbackValue, reason: "inapplicable" };
      const areaLength = rectAxisLength(ancestor.area, axis);
      const branchLength = rectAxisLength(ancestor.branchArea, axis);
      const roleLength = rectAxisLength(ctx.rect, axis);
      if (!(areaLength > 0) || !(branchLength > 0) || !(roleLength > 0)) {
        return { ok: false, value: fallbackValue, reason: "geometry" };
      }
      const coverage = roleLength / branchLength;
      if (!(coverage > 0)) return { ok: false, value: fallbackValue, reason: "coverage" };
      const desiredRoleLength = clampStudioPercent(requestedSize, roleLength);
      const minBranchLength = Math.max(STUDIO_SIZE_MIN, STUDIO_SIZE_MIN / coverage);
      const maxBranchLength = Math.max(minBranchLength, areaLength - STUDIO_SIZE_MIN);
      const desiredBranchLength = Math.max(
        minBranchLength,
        Math.min(maxBranchLength, desiredRoleLength / coverage)
      );
      const desiredBranchPct = desiredBranchLength / areaLength * 100;
      const nextPct = ancestor.branch === "0" ? desiredBranchPct : 100 - desiredBranchPct;
      if (!splitTreeUpdateNodePct(ancestor.node, nextPct)) {
        return { ok: false, value: fallbackValue, reason: "update" };
      }
      state.splitTreeModel = tree;
      syncSplitTreeState();
      return { ok: true, value: currentStudioInputValue(role, axis) };
    }

    function bindStudioSizeInputs(scope) {
      scope?.querySelectorAll(".studio-size-input").forEach((input) => {
        input.addEventListener("click", (event) => {
          event.stopPropagation();
        });
        input.addEventListener("input", (event) => {
          event.stopPropagation();
        });
        input.addEventListener("change", (event) => {
          event.preventDefault();
          event.stopPropagation();
          if (input.disabled) return;
          const role = parseInt(input.dataset.role || "", 10);
          if (!Number.isFinite(role)) return;
          selectRole(role);
          const axis = input.dataset.axis === "h" ? "h" : "w";
          const parsed = parseInt(input.value, 10);
          const historySnapshot = captureStudioHistorySnapshot();
          const result = resizePaneAxis(role, axis, parsed);
          if (!result.ok) {
            input.value = String(result.value);
            renderPlaylistEditor();
            renderStudioBoard();
            renderStudioInspector();
            setStatus(
              result.reason === "invalid"
                ? `Use an integer from ${STUDIO_SIZE_MIN} to ${STUDIO_SIZE_MAX}.`
                : (axis === "w"
                    ? `${roleTitle(role)} width follows the current split tree. Choose a pane edge with a vertical split to resize it.`
                    : `${roleTitle(role)} height follows the current split tree. Choose a pane edge with a horizontal split to resize it.`),
              true
            );
            return;
          }
          commitStudioHistory(historySnapshot);
          renderPlaylistEditor();
          renderStudioBoard();
          renderStudioInspector();
          setStatus(
            axis === "w"
              ? `Updated ${roleTitle(role)} width to ${result.value}%.`
              : `Updated ${roleTitle(role)} height to ${result.value}%.`,
            false
          );
        });
      });
    }

    function paneIdentityForRole(nextState, role) {
      if (!nextState || role < 0) return null;
      const paneIndex = role;
      const paneType = nextState.pane_types?.[paneIndex] || "terminal";
      return {
        kind: "pane",
        rawType: String(nextState.pane_type_raw?.[paneIndex] || ""),
        typeSettings: (
          nextState.pane_type_settings?.[paneIndex]
          && typeof nextState.pane_type_settings[paneIndex] === "object"
          && !Array.isArray(nextState.pane_type_settings[paneIndex])
        ) ? { ...nextState.pane_type_settings[paneIndex] } : {},
        paneType,
        command: String(nextState.pane_commands?.[paneIndex] || ""),
        playlist: String(nextState.pane_playlists?.[paneIndex] || ""),
        playlistExtended: String(nextState.pane_playlist_extended?.[paneIndex] || ""),
        playlistFifo: String(nextState.pane_playlist_fifos?.[paneIndex] || ""),
        mpvOut: String(nextState.pane_mpv_outs?.[paneIndex] || ""),
        videoRotate: String(nextState.pane_video_rotate?.[paneIndex] || ""),
        panscan: String(nextState.pane_panscan?.[paneIndex] || ""),
        watchdog: Number(nextState.pane_watchdogs?.[paneIndex] || 0),
        syncGroup: String(nextState.pane_sync_groups?.[paneIndex] || ""),
        videoPaths: Array.isArray(nextState.pane_video_paths?.[paneIndex]) ? nextState.pane_video_paths[paneIndex].slice() : [],
        mpvOpts: Array.isArray(nextState.pane_mpv_opts?.[paneIndex]) ? nextState.pane_mpv_opts[paneIndex].slice() : [],
      };
    }

    function paneIdentityEquals(left, right) {
      if (!left || !right) return false;
      return JSON.stringify(left) === JSON.stringify(right);
    }

    function captureSelectedPaneSnapshot(nextState) {
      if (!nextState || selectedRole < 0) return null;
      const splitTree = nextState === state ? normalizeSplitTreeState() : parseSplitTreeSpec(nextState.split_tree || "");
      return {
        role: selectedRole,
        path: splitTree ? splitTreePathForRole(splitTree, selectedRole) : null,
        identity: paneIdentityForRole(nextState, selectedRole),
      };
    }

    function restoreSelectedRole(nextState, snapshot) {
      if (!nextState || !snapshot?.identity) return -1;
      if (snapshot.identity.kind === "main") return 0;
      const matchingRoles = [];
      for (let role = 0; role < Number(nextState.pane_count || 0); role += 1) {
        if (paneIdentityEquals(paneIdentityForRole(nextState, role), snapshot.identity)) {
          matchingRoles.push(role);
        }
      }
      if (matchingRoles.length <= 1) {
        return matchingRoles.length === 1 ? matchingRoles[0] : -1;
      }
      let narrowedRoles = matchingRoles.slice();
      const splitTree = parseSplitTreeSpec(nextState.split_tree || "");
      if (splitTree && snapshot.path != null) {
        const nodeAtPath = splitTreeNodeAtPath(splitTree, snapshot.path);
        const roleAtPath = nodeAtPath?.leaf ? Number(nodeAtPath.role) : null;
        if (Number.isFinite(roleAtPath) && narrowedRoles.includes(roleAtPath)) {
          narrowedRoles = [roleAtPath];
        }
      }
      if (narrowedRoles.length > 1 && snapshot.role >= 0 && narrowedRoles.includes(snapshot.role)) {
        narrowedRoles = [snapshot.role];
      }
      return narrowedRoles.length === 1 ? narrowedRoles[0] : -1;
    }

    function ensureSelectedRole() {
      const maxRole = Math.max(-1, Number(state?.pane_count || 0) - 1);
      if (!Number.isFinite(selectedRole)) selectedRole = -1;
      if (selectedRole < -1) selectedRole = -1;
      if (selectedRole > maxRole) selectedRole = -1;
    }

    function selectedPaneType() {
      if (!state || selectedRole < 0) return "none";
      return state.pane_types?.[selectedRole] || "terminal";
    }

    function selectRole(role) {
      if (!Number.isFinite(role)) {
        selectedRole = -1;
      } else {
        selectedRole = Number(role);
      }
      ensureSelectedRole();
      if (state) state.selected_pane = selectedRole;
    }

    function parseRolesString(nextState) {
      const roleCount = Math.max(1, Number(nextState.pane_count || 2));
      const perm = Array.from({ length: roleCount }, (_, index) => index);
      const used = Array(roleCount).fill(false);
      let slot = 0;
      for (const char of String(nextState.roles || "")) {
        let role = -1;
        if (char === "C" || char === "c") role = 0;
        else if (char === "A" || char === "a" || char === "1") role = 1;
        else if (char === "B" || char === "b" || char === "2") role = 2;
        else if (char === "D" || char === "d" || char === "3") role = 3;
        else if (char === "E" || char === "e" || char === "4") role = 4;
        else if (char >= "0" && char <= "9") role = Number(char);
        if (role < 0 || role >= roleCount || used[role]) continue;
        perm[role] = slot++;
        used[role] = true;
      }
      return slot === roleCount ? perm : Array.from({ length: roleCount }, (_, index) => index);
    }

    function tileRects(area, count) {
      if (count <= 0) return [];
      let cols = 1;
      while (cols * cols < count) cols += 1;
      const rows = Math.ceil(count / cols);
      const out = [];
      let y = area.y;
      let idx = 0;
      for (let r = 0; r < rows; r += 1) {
        const cellsLeft = count - idx;
        const rowCols = Math.min(cellsLeft, cols);
        const rowH = (r === rows - 1) ? (area.y + area.h - y) : Math.floor(area.h / rows);
        let x = area.x;
        for (let c = 0; c < rowCols; c += 1, idx += 1) {
          const cellW = (c === rowCols - 1) ? (area.x + area.w - x) : Math.floor(area.w / rowCols);
          out.push({ x, y, w: cellW, h: rowH });
          x += cellW;
        }
        y += rowH;
      }
      return out;
    }

    function splitVertical(area, count) {
      const out = [];
      let y = area.y;
      for (let i = 0; i < count; i += 1) {
        const h = (i === count - 1) ? (area.y + area.h - y) : Math.floor(area.h / count);
        out.push({ x: area.x, y, w: area.w, h });
        y += h;
      }
      return out;
    }

    function splitHorizontal(area, count) {
      const out = [];
      let x = area.x;
      for (let i = 0; i < count; i += 1) {
        const w = (i === count - 1) ? (area.x + area.w - x) : Math.floor(area.w / count);
        out.push({ x, y: area.y, w, h: area.h });
        x += w;
      }
      return out;
    }

    function computeStudioRects(nextState) {
      return visibilityLayoutForState(nextState).rects;
    }

    function transformRectByDegrees(rect, total) {
      if (total === 90) {
        return { x: 100 - (rect.y + rect.h), y: rect.x, w: rect.h, h: rect.w };
      }
      if (total === 180) {
        return { x: 100 - (rect.x + rect.w), y: 100 - (rect.y + rect.h), w: rect.w, h: rect.h };
      }
      if (total === 270) {
        return { x: rect.y, y: 100 - (rect.x + rect.w), w: rect.h, h: rect.w };
      }
      return rect;
    }

    function inversePointByDegrees(x, y, total) {
      if (total === 90) return { x: y, y: 100 - x };
      if (total === 180) return { x: 100 - x, y: 100 - y };
      if (total === 270) return { x: 100 - y, y: x };
      return { x, y };
    }

    function displayPointToLogicalPoint(point) {
      if (!point) return null;
      return inversePointByDegrees(point.x, 100 - point.y, studioRotationDegrees());
    }

    function displayEdgeForLogicalEdge(logicalEdge, total) {
      const candidates = [
        { edge: "left", point: { x: 0, y: 50 } },
        { edge: "right", point: { x: 100, y: 50 } },
        { edge: "top", point: { x: 50, y: 0 } },
        { edge: "bottom", point: { x: 50, y: 100 } },
      ];
      for (const candidate of candidates) {
        const logicalPoint = inversePointByDegrees(candidate.point.x, 100 - candidate.point.y, total);
        const xBias = Math.abs(logicalPoint.x - 50);
        const yBias = Math.abs(logicalPoint.y - 50);
        const resolvedEdge = xBias > yBias
          ? (logicalPoint.x < 50 ? "left" : "right")
          : (logicalPoint.y < 50 ? "top" : "bottom");
        if (resolvedEdge === logicalEdge) return candidate.edge;
      }
      return logicalEdge;
    }

    function transformStudioPaneRect(rect) {
      const rotated = transformRectByDegrees(rect, studioRotationDegrees());
      return { x: rotated.x, y: 100 - (rotated.y + rotated.h), w: rotated.w, h: rotated.h };
    }

    function applyStudioGeometry() {
      const total = normalizedRotationDegrees();
      studioBoard.style.aspectRatio = (total === 90 || total === 270) ? "9 / 16" : "16 / 9";
    }

    function normalizeStudioResizeCorner(edgeA, edgeB) {
      const vertical = [edgeA, edgeB].find((edge) => edge === "top" || edge === "bottom");
      const horizontal = [edgeA, edgeB].find((edge) => edge === "left" || edge === "right");
      if (!vertical || !horizontal) return null;
      return `${vertical}-${horizontal}`;
    }

    function studioResizeCursor(mode, corner = "", edge = "") {
      if (mode === "corner") {
        return corner === "top-right" || corner === "bottom-left" ? "nesw-resize" : "nwse-resize";
      }
      if (edge === "top" || edge === "bottom") return "ns-resize";
      if (edge === "left" || edge === "right") return "ew-resize";
      return mode === "h" ? "ns-resize" : "ew-resize";
    }

    function studioResizeCornerName(ctx) {
      if (!ctx?.colAncestor || !ctx?.rowAncestor) return null;
      return normalizeStudioResizeCorner(ctx.rowAncestor.edge, ctx.colAncestor.edge);
    }

    function studioResizeHandleMarkup(ctx) {
      if (!ctx) return "";
      const handles = [];
      if (ctx.colAncestor) {
        handles.push(`<button type="button" class="studio-resize-handle" data-studio-resize="w" data-axis="w" data-edge="${ctx.colAncestor.edge}" aria-label="Resize pane width"></button>`);
      }
      if (ctx.rowAncestor) {
        handles.push(`<button type="button" class="studio-resize-handle" data-studio-resize="h" data-axis="h" data-edge="${ctx.rowAncestor.edge}" aria-label="Resize pane height"></button>`);
      }
      const corner = studioResizeCornerName(ctx);
      if (corner) {
        handles.push(`<button type="button" class="studio-resize-handle" data-studio-resize="corner" data-mode="corner" data-corner="${corner}" aria-label="Resize pane width and height"></button>`);
      }
      return handles.join("");
    }

    function studioBoardPointerPosition(event) {
      const bounds = studioBoard?.getBoundingClientRect();
      if (!bounds?.width || !bounds?.height) return null;
      return {
        x: Math.max(0, Math.min(100, ((event.clientX - bounds.left) / bounds.width) * 100)),
        y: Math.max(0, Math.min(100, ((event.clientY - bounds.top) / bounds.height) * 100)),
      };
    }

    function desiredStudioSizeFromPointer(displayRect, axis, edge, pointer) {
      if (!displayRect || !pointer) return null;
      if (axis === "w") {
        return edge === "left"
          ? (displayRect.x + displayRect.w) - pointer.x
          : pointer.x - displayRect.x;
      }
      return edge === "top"
        ? (displayRect.y + displayRect.h) - pointer.y
        : pointer.y - displayRect.y;
    }

    function applyStudioResizeDrag(event) {
      if (!studioResizeDrag) return;
      const pointer = studioBoardPointerPosition(event);
      if (!pointer) return;
      const logicalPointer = displayPointToLogicalPoint(pointer);
      const ctx = splitTreeResizeContext(state, studioResizeDrag.role);
      if (!ctx) return;
      let changed = false;
      studioResizeDrag.snapAxes = {};
      if (studioResizeDrag.mode === "w" || studioResizeDrag.mode === "corner") {
        const desiredWidth = desiredStudioSizeFromPointer(ctx.rect, "w", ctx.colAncestor?.logicalEdge, logicalPointer);
        const snappedWidth = desiredWidth == null ? null : snapStudioSize(desiredWidth, event.altKey);
        if (snappedWidth && resizePaneAxis(studioResizeDrag.role, "w", snappedWidth.value).ok) {
          studioResizeDrag.snapAxes.w = snappedWidth.snapped;
          changed = true;
        }
      }
      if (studioResizeDrag.mode === "h" || studioResizeDrag.mode === "corner") {
        const latestCtx = splitTreeResizeContext(state, studioResizeDrag.role) || ctx;
        const desiredHeight = desiredStudioSizeFromPointer(latestCtx.rect, "h", latestCtx.rowAncestor?.logicalEdge, logicalPointer);
        const snappedHeight = desiredHeight == null ? null : snapStudioSize(desiredHeight, event.altKey);
        if (snappedHeight && resizePaneAxis(studioResizeDrag.role, "h", snappedHeight.value).ok) {
          studioResizeDrag.snapAxes.h = snappedHeight.snapped;
          changed = true;
        }
      }
      if (changed) renderStudioBoard();
    }

    function renderStudioResizeGuides() {
      if (!studioResizeDrag || !studioBoard) return;
      const ctx = splitTreeResizeContext(state, studioResizeDrag.role);
      if (!ctx) return;
      const addGuide = (axis, edge) => {
        const rect = ctx.displayRect;
        const guide = document.createElement("div");
        if (axis === "w") {
          const x = edge === "left" ? rect.x : rect.x + rect.w;
          guide.className = "studio-guide vertical";
          guide.style.left = `${x}%`;
        } else {
          const y = edge === "top" ? rect.y : rect.y + rect.h;
          guide.className = "studio-guide horizontal";
          guide.style.top = `${y}%`;
        }
        studioBoard.appendChild(guide);
      };
      if (studioResizeDrag.snapAxes?.w && ctx.colAncestor?.edge) addGuide("w", ctx.colAncestor.edge);
      if (studioResizeDrag.snapAxes?.h && ctx.rowAncestor?.edge) addGuide("h", ctx.rowAncestor.edge);
    }

    function stopStudioResizeDrag() {
      if (!studioResizeDrag) return;
      const historySnapshot = studioResizeDrag.historySnapshot;
      studioResizeDrag = null;
      studioBoard?.classList.remove("resizing");
      if (studioBoard) studioBoard.style.cursor = "";
      window.removeEventListener("pointermove", applyStudioResizeDrag);
      window.removeEventListener("pointerup", stopStudioResizeDrag);
      window.removeEventListener("pointercancel", stopStudioResizeDrag);
      commitStudioHistory(historySnapshot);
      renderStudioBoard();
      renderStudioInspector();
    }

    function startStudioResizeDrag(event, role, mode, edge = "", corner = "") {
      if (!state) return;
      const ctx = splitTreeResizeContext(state, role);
      if (!ctx) return;
      if ((mode === "w" && !ctx.colAncestor) || (mode === "h" && !ctx.rowAncestor) || (mode === "corner" && !studioResizeCornerName(ctx))) {
        return;
      }
      selectRole(role);
      studioResizeDrag = {
        role,
        mode,
        historySnapshot: captureStudioHistorySnapshot(),
        snapAxes: {},
      };
      const resolvedCorner = corner || studioResizeCornerName(ctx) || "";
      studioBoard?.classList.add("resizing");
      if (studioBoard) studioBoard.style.cursor = studioResizeCursor(mode, resolvedCorner, edge);
      window.addEventListener("pointermove", applyStudioResizeDrag);
      window.addEventListener("pointerup", stopStudioResizeDrag);
      window.addEventListener("pointercancel", stopStudioResizeDrag);
      applyStudioResizeDrag(event);
    }

    function renderStudioBoard() {
      if (!state) return;
      ensureSelectedRole();
      applyStudioGeometry();
      const layout = visibilityLayoutForState(state);
      const rects = layout.rects;
      studioBoard.classList.toggle("resizing", !!studioResizeDrag);
      studioBoard.innerHTML = "";
      layout.visibleRoles.forEach((role) => {
        const rect = rects[role];
        if (!rect || rect.w <= 0 || rect.h <= 0) return;
        const displayRect = transformStudioPaneRect(rect);
        const resizeCtx = splitTreeResizeContext(state, role);
        const handleMarkup = selectedRole === role ? studioResizeHandleMarkup(resizeCtx) : "";
        const card = document.createElement("div");
        card.draggable = true;
        card.tabIndex = 0;
        card.setAttribute("role", "button");
        card.className = `studio-card ${roleType(role)}${selectedRole === role ? " selected" : ""}`;
        card.style.left = `${displayRect.x}%`;
        card.style.top = `${displayRect.y}%`;
        card.style.width = `${displayRect.w}%`;
        card.style.height = `${displayRect.h}%`;
        const paneType = state.pane_types?.[role] || "terminal";
        card.dataset.studioRole = String(role);
        card.innerHTML = `
          <div class="studio-top">
            <span class="studio-card-title">${roleTitle(role)}</span>
            <span class="studio-tag">${paneType === "mpv" ? "mpv" : "shell"}</span>
          </div>
          ${handleMarkup}
        `;
        card.addEventListener("click", () => {
          selectRole(role);
          renderStudioInspector();
          renderStudioBoard();
        });
        card.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          selectRole(role);
          renderStudioInspector();
          renderStudioBoard();
        });
        card.addEventListener("dragstart", (event) => {
          draggedStudioRole = role;
          card.classList.add("dragging");
          if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", String(role));
          }
        });
        card.addEventListener("dragend", () => {
          draggedStudioRole = null;
          studioBoard.querySelectorAll(".studio-card").forEach((node) => node.classList.remove("dragging", "drop-target"));
        });
        card.addEventListener("dragover", (event) => {
          if (draggedStudioRole == null || draggedStudioRole === role) return;
          event.preventDefault();
          card.classList.add("drop-target");
        });
        card.addEventListener("dragleave", () => {
          card.classList.remove("drop-target");
        });
        card.addEventListener("drop", (event) => {
          if (draggedStudioRole == null || draggedStudioRole === role) return;
          event.preventDefault();
          const tree = ensureSplitTreeModel();
          if (!tree) {
            setStatus("Could not reposition panes.", true);
            return;
          }
          const sourceRole = draggedStudioRole;
          const historySnapshot = captureStudioHistorySnapshot();
          card.classList.remove("drop-target");
          if (!splitTreeSwapRoles(tree, sourceRole, role)) {
            setStatus("Could not reposition panes.", true);
            return;
          }
          state.splitTreeModel = tree;
          syncSplitTreeState();
          commitStudioHistory(historySnapshot);
          draggedStudioRole = null;
          selectRole(sourceRole);
          renderPlaylistEditor();
          renderStudioBoard();
          renderStudioInspector();
          setStatus(`Swapped ${roleTitle(sourceRole)} with ${roleTitle(role)}.`, false);
        });
        card.querySelectorAll("[data-studio-resize]").forEach((handle) => {
          handle.addEventListener("pointerdown", (event) => {
            event.preventDefault();
            event.stopPropagation();
            startStudioResizeDrag(
              event,
              role,
              handle.dataset.studioResize || "w",
              handle.dataset.edge || "",
              handle.dataset.corner || ""
            );
          });
          handle.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
          });
        });
        studioBoard.appendChild(card);
      });
      renderStudioResizeGuides();
      syncStudioBoardSelectionState();
    }

    function syncStudioBoardSelectionState() {
      if (!studioBoard) return;
      studioBoard.querySelectorAll(".studio-card").forEach((card) => {
        const role = Number(card.dataset.studioRole);
        card.classList.toggle("selected", role === selectedRole);
      });
    }

    function selectedPaneQueueSectionMarkup() {
      const ctx = queueEditorContext();
      if (!ctx || ctx.paneType !== "mpv") return "";
      return `
        <div class="selected-pane-section">
          <h2 class="section-title">Queue</h2>
          <p class="muted-note queue-editor-note" id="queueEditorNote">${ctx.note}</p>
          <div class="playlist-editor" id="playlistEditor"></div>
          <details class="playlist-bulk" id="playlistBulk">
            <summary>Bulk Add Videos</summary>
            <div class="playlist-bulk-body">
              <p class="muted-note">Paste one path or URL per line to replace the current queue for the selected mpv pane.</p>
              <label>Video Files
                <textarea id="videoList" spellcheck="false" placeholder="/path/one.mp4&#10;/path/two.mp4"></textarea>
              </label>
            </div>
          </details>
          <div class="actions tight">
            <button class="secondary" id="addQueueItemBtn">Add Video</button>
          </div>
        </div>
      `;
    }

    function playlistHoverOverlayBounds(rect) {
      const viewportPadding = 12;
      const maxWidth = Math.max(1, window.innerWidth - viewportPadding * 2);
      const maxHeight = Math.max(1, window.innerHeight - viewportPadding * 2);
      const mediaWidth = Number(rect?.mediaWidth || 0);
      const mediaHeight = Number(rect?.mediaHeight || 0);
      if (mediaWidth > 0 && mediaHeight > 0) {
        const scale = Math.min(1, maxWidth / mediaWidth, maxHeight / mediaHeight);
        return {
          width: Math.max(1, Math.round(mediaWidth * scale)),
          height: Math.max(1, Math.round(mediaHeight * scale)),
        };
      }
      const preferredWidth = Math.round(Math.max(240, rect.width * 2));
      const preferredHeight = Math.round(Math.max(160, rect.height * 2));
      return {
        width: Math.min(preferredWidth, maxWidth),
        height: Math.min(preferredHeight, maxHeight),
      };
    }

    function playlistHoverOverlayPosition(rect, overlayWidth, overlayHeight) {
      const viewportPadding = 12;
      const left = Math.max(viewportPadding, Math.min(window.innerWidth - overlayWidth - viewportPadding, rect.left));
      const above = rect.top - overlayHeight - 10;
      const below = rect.bottom + 10;
      const top = above >= viewportPadding
        ? Math.max(viewportPadding, Math.min(above, window.innerHeight - overlayHeight - viewportPadding))
        : Math.max(viewportPadding, Math.min(below, window.innerHeight - overlayHeight - viewportPadding));
      return { left, top };
    }

    function playlistHoverOverlayFallback(overlay, message) {
      overlay.innerHTML = "";
      const fallback = document.createElement("div");
      fallback.style.width = "100%";
      fallback.style.height = "100%";
      fallback.style.display = "grid";
      fallback.style.placeItems = "center";
      fallback.style.padding = "16px";
      fallback.style.textAlign = "center";
      fallback.style.color = "#f0f0f2";
      fallback.style.fontFamily = '"Menlo", "Consolas", monospace';
      fallback.style.fontSize = "12px";
      fallback.style.fontWeight = "700";
      fallback.style.letterSpacing = "0.08em";
      fallback.style.textTransform = "uppercase";
      fallback.style.background = "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02))";
      fallback.textContent = message || "Preview unavailable";
      overlay.appendChild(fallback);
      overlay.style.display = "block";
    }

    function updatePlaylistHoverOverlay(overlay, thumb, mediaWidth = 0, mediaHeight = 0) {
      if (!overlay || !thumb) return;
      const rect = thumb.getBoundingClientRect();
      const size = playlistHoverOverlayBounds({
        width: rect.width,
        height: rect.height,
        mediaWidth,
        mediaHeight,
      });
      const position = playlistHoverOverlayPosition(rect, size.width, size.height);
      overlay.style.width = `${size.width}px`;
      overlay.style.height = `${size.height}px`;
      overlay.style.left = `${position.left}px`;
      overlay.style.top = `${position.top}px`;
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function paneTemplatePayload(role = selectedRole) {
      ensurePaneCommands(state);
      return {
        type: state.pane_types?.[role] || "terminal",
        raw: state.pane_type_raw?.[role] || "",
        settings: { ...(state.pane_type_settings?.[role] || {}) },
        command: state.pane_commands?.[role] || "",
        playlist: state.pane_playlists?.[role] || "",
        playlist_extended: state.pane_playlist_extended?.[role] || "",
        playlist_fifo: state.pane_playlist_fifos?.[role] || "",
        mpv_out: state.pane_mpv_outs?.[role] || "",
        video_rotate: state.pane_video_rotate?.[role] || "",
        panscan: state.pane_panscan?.[role] || "",
        watchdog: Number(state.pane_watchdogs?.[role] || 0),
        sync_group: state.pane_sync_groups?.[role] || "",
        video_paths: [...(state.pane_video_paths?.[role] || [])],
        mpv_opts: [...(state.pane_mpv_opts?.[role] || [])],
      };
    }

    function paneTemplateMarkup() {
      const options = (paneTemplateCatalog.templates || []).map((template) => (
        `<option value="${escapeHtml(template.id)}" ${template.id === selectedPaneTemplateId ? "selected" : ""}>${escapeHtml(template.name || "Unnamed template")}</option>`
      )).join("");
      return `
        <div class="selected-pane-section">
          <h2 class="section-title">Pane Templates</h2>
          <label>Saved Template
            <select id="paneTemplateSelect"><option value="">New template…</option>${options}</select>
          </label>
          <label>Template Name
            <input id="paneTemplateName" type="text" maxlength="80" placeholder="Dashboard video pane" />
          </label>
          <div class="actions tight">
            <button id="paneTemplateSaveBtn" type="button" class="secondary">Save Template</button>
            <button id="paneTemplateApplyBtn" type="button" class="secondary">Apply</button>
            <button id="paneTemplateDeleteBtn" type="button" class="secondary danger">Delete</button>
          </div>
          <p class="muted-note">Apply changes only this editor pane. Use Save Config when you are ready to activate it.</p>
        </div>`;
    }

    async function paneTemplateApi(path, payload = {}) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Pane template operation failed");
      return result;
    }

    async function loadPaneTemplates() {
      const response = await fetch("/api/templates");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Failed to load pane templates");
      paneTemplateCatalog = payload;
      if (!(payload.templates || []).some((template) => template.id === selectedPaneTemplateId)) {
        selectedPaneTemplateId = "";
      }
      if (state && selectedRole >= 0) renderStudioInspector();
    }

    function applyPaneTemplate(template) {
      if (!state || selectedRole < 0 || !template?.pane) return;
      ensurePaneCommands(state);
      const role = selectedRole;
      const pane = template.pane;
      state.pane_types[role] = String(pane.type || "terminal");
      state.pane_type_raw[role] = String(pane.raw || "");
      state.pane_type_settings[role] = pane.settings && typeof pane.settings === "object" ? { ...pane.settings } : {};
      state.pane_commands[role] = String(pane.command || "");
      state.pane_playlists[role] = String(pane.playlist || "");
      state.pane_playlist_extended[role] = String(pane.playlist_extended || "");
      state.pane_playlist_fifos[role] = String(pane.playlist_fifo || "");
      state.pane_mpv_outs[role] = String(pane.mpv_out || "");
      state.pane_video_rotate[role] = String(pane.video_rotate || "");
      state.pane_panscan[role] = String(pane.panscan || "");
      state.pane_watchdogs[role] = Math.max(0, Number(pane.watchdog || 0));
      state.pane_sync_groups[role] = String(pane.sync_group || "");
      state.pane_video_paths[role] = Array.isArray(pane.video_paths) ? pane.video_paths.slice() : [];
      state.pane_mpv_opts[role] = Array.isArray(pane.mpv_opts) ? pane.mpv_opts.slice() : [];
      renderStudioBoard();
      renderStudioInspector();
      renderPlaylistEditor();
      setStatus(`Applied pane template ${template.name || ""}; save config to activate it.`, false, true);
    }

    function bindPaneTemplateControls() {
      const select = document.getElementById("paneTemplateSelect");
      const name = document.getElementById("paneTemplateName");
      const apply = document.getElementById("paneTemplateApplyBtn");
      const remove = document.getElementById("paneTemplateDeleteBtn");
      if (!select || !name || !apply || !remove) return;
      const refresh = () => {
        const template = (paneTemplateCatalog.templates || []).find((item) => item.id === select.value);
        selectedPaneTemplateId = template?.id || "";
        name.value = template ? String(template.name || "") : "";
        apply.disabled = !template;
        remove.disabled = !template;
      };
      select.addEventListener("change", refresh);
      refresh();
      document.getElementById("paneTemplateSaveBtn")?.addEventListener("click", () => {
        if (selectedPaneType() === "mpv") syncInspectorPaneMpvOpts(selectedRole);
        paneTemplateApi("/api/templates/save", {
          id: selectedPaneTemplateId,
          name: name.value,
          pane: paneTemplatePayload(),
        }).then((payload) => {
          paneTemplateCatalog = payload;
          selectedPaneTemplateId = payload.template?.id || "";
          renderStudioInspector();
          setStatus(`Saved pane template ${payload.template?.name || ""}.`, false, true);
        }).catch((err) => setStatus(err.message, true));
      });
      apply.addEventListener("click", () => {
        const template = (paneTemplateCatalog.templates || []).find((item) => item.id === select.value);
        applyPaneTemplate(template);
      });
      remove.addEventListener("click", () => {
        const template = (paneTemplateCatalog.templates || []).find((item) => item.id === select.value);
        if (!template || !window.confirm(`Delete pane template “${template.name || "Unnamed template"}”?`)) return;
        paneTemplateApi("/api/templates/delete", { id: template.id }).then((payload) => {
          paneTemplateCatalog = payload;
          selectedPaneTemplateId = "";
          renderStudioInspector();
          setStatus("Deleted pane template.", false, true);
        }).catch((err) => setStatus(err.message, true));
      });
    }

    function renderStudioInspector() {
      if (!state || !studioInspector) return;
      ensureSelectedRole();

      if (selectedRole < 0) {
        studioInspector.innerHTML = `<div class="studio-empty">Select a pane to edit it.</div>`;
        dispatchSelectedPaneState();
        return;
      }

      const layoutActions = selectedPaneLayoutActionsMarkup(selectedRole);
      const paneIndex = selectedRole;
      const paneType = state.pane_types?.[paneIndex] || "terminal";
      const value = state.pane_commands?.[paneIndex] || "";
      if (paneType === "mpv") {
        const paneMpvGroups = parseMpvOptionGroups(state.pane_mpv_opts?.[paneIndex] || []);
        const panePanscan = String(state.pane_panscan?.[paneIndex] || "");
        const paneWatchdog = Number(state.pane_watchdogs?.[paneIndex] || 0);
        const paneSyncGroup = String(state.pane_sync_groups?.[paneIndex] || "");
        studioInspector.innerHTML = `
          <div class="selected-pane-section">
            <h2 class="section-title">Pane Behavior</h2>
            <label>Pane Type
              <select id="inspectorPaneType">
                <option value="terminal">terminal</option>
                <option value="mpv" selected>mpv</option>
              </select>
            </label>
            <label>Hardware Decode
              <select id="inspectorPaneHwdec">
                <option value="" ${paneMpvGroups.hwdec ? "" : "selected"}>Automatic default</option>
                <option value="auto-copy-safe" ${paneMpvGroups.hwdec === "auto-copy-safe" ? "selected" : ""}>Auto copy-safe</option>
                <option value="no" ${paneMpvGroups.hwdec === "no" ? "selected" : ""}>Software decode</option>
              </select>
            </label>
            <label>Image Scaling
              <select id="inspectorPaneScale">
                <option value="" ${paneMpvGroups.scale ? "" : "selected"}>mpv default</option>
                <option value="bilinear" ${paneMpvGroups.scale === "bilinear" ? "selected" : ""}>Bilinear (lowest cost)</option>
                <option value="bicubic" ${paneMpvGroups.scale === "bicubic" ? "selected" : ""}>Bicubic</option>
                <option value="lanczos" ${paneMpvGroups.scale === "lanczos" ? "selected" : ""}>Lanczos (sharper)</option>
              </select>
            </label>
            <label>Debanding
              <select id="inspectorPaneDeband">
                <option value="" ${paneMpvGroups.deband ? "" : "selected"}>mpv default</option>
                <option value="yes" ${paneMpvGroups.deband === "yes" ? "selected" : ""}>On</option>
                <option value="no" ${paneMpvGroups.deband === "no" ? "selected" : ""}>Off</option>
              </select>
            </label>
            <label>Frame Interpolation
              <select id="inspectorPaneInterpolation">
                <option value="" ${paneMpvGroups.interpolation ? "" : "selected"}>mpv default</option>
                <option value="yes" ${paneMpvGroups.interpolation === "yes" ? "selected" : ""}>On</option>
                <option value="no" ${paneMpvGroups.interpolation === "no" ? "selected" : ""}>Off</option>
              </select>
            </label>
            <label>Video Sync
              <select id="inspectorPaneVideoSync">
                <option value="" ${paneMpvGroups.videoSync ? "" : "selected"}>mpv default</option>
                <option value="audio" ${paneMpvGroups.videoSync === "audio" ? "selected" : ""}>Audio clock</option>
                <option value="display-resample" ${paneMpvGroups.videoSync === "display-resample" ? "selected" : ""}>Display resample</option>
                <option value="display-vdrop" ${paneMpvGroups.videoSync === "display-vdrop" ? "selected" : ""}>Display drop</option>
              </select>
            </label>
            <label>Panscan
              <input id="inspectorPanePanscan" type="number" step="0.01" placeholder="0.00" value="${panePanscan.replace(/"/g, "&quot;")}">
            </label>
            <label>Playback Watchdog (seconds)
              <input id="inspectorPaneWatchdog" type="number" min="0" step="1" value="${paneWatchdog}">
            </label>
            <p class="muted-note">0 disables it. When enabled, only active, unpaused playback is restarted after it stops advancing.</p>
            <label>Start Sync Group
              <input id="inspectorPaneSyncGroup" type="text" placeholder="wall-a" value="${paneSyncGroup.replace(/"/g, "&quot;")}">
            </label>
            <p class="muted-note">Media panes with the same group wait until all members are loaded, then begin together. Later queue changes remain independent.</p>
            <button id="inspectorRestartPane" type="button" class="secondary">Restart This Pane</button>
            <label>Shader Stack
              <textarea id="inspectorPaneShaders" spellcheck="false" placeholder="/path/to/shader1.glsl&#10;/path/to/shader2.glsl">${paneMpvGroups.shaders.join("\n")}</textarea>
            </label>
            <label>Additional mpv Options
              <textarea id="inspectorPaneMpvOpts" spellcheck="false" placeholder="profile=fast&#10;deband=yes">${paneMpvGroups.other.join("\n")}</textarea>
            </label>
          </div>
          ${paneTemplateMarkup()}
          ${selectedPaneSizeSectionMarkup(selectedRole)}
          ${layoutActions}
          ${selectedPaneQueueSectionMarkup()}
        `;
        document.getElementById("inspectorPaneType").addEventListener("change", (event) => {
          state.pane_types[paneIndex] = event.target.value;
          renderStudioBoard();
          renderStudioInspector();
        });
        [
          "inspectorPanePanscan",
          "inspectorPaneWatchdog",
          "inspectorPaneHwdec",
          "inspectorPaneScale",
          "inspectorPaneDeband",
          "inspectorPaneInterpolation",
          "inspectorPaneVideoSync",
          "inspectorPaneSyncGroup",
          "inspectorPaneShaders",
          "inspectorPaneMpvOpts",
        ].forEach((id) => {
          document.getElementById(id).addEventListener("input", () => {
            syncInspectorPaneMpvOpts(paneIndex);
            renderStudioBoard();
            renderPlaylistEditor();
          });
          document.getElementById(id).addEventListener("change", () => {
            syncInspectorPaneMpvOpts(paneIndex);
            renderStudioBoard();
            renderPlaylistEditor();
          });
        });
        const addQueueButton = selectedPaneAddQueueButton();
        if (addQueueButton) {
          addQueueButton.addEventListener("click", () => {
            addQueueItem();
            setStatus("Added a new queue entry.", false);
          });
        }
        bindStudioSizeInputs(studioInspector);
        bindPaneTemplateControls();
        document.getElementById("inspectorRestartPane")?.addEventListener("click", () => {
          restartPane(paneIndex).catch((err) => setStatus(err.message, true));
        });
        bindSelectedPaneLayoutActions(selectedRole);
        syncInspectorPaneMpvOpts(paneIndex);
        renderPlaylistEditor();
        dispatchSelectedPaneState();
        return;
      }

      studioInspector.innerHTML = `
        <div class="selected-pane-section">
          <h2 class="section-title">Pane Behavior</h2>
          <label>Pane Type
            <select id="inspectorPaneType">
              <option value="terminal" selected>terminal</option>
              <option value="mpv">mpv</option>
            </select>
          </label>
          <label>Command
            <input id="inspectorPaneCommand" type="text" value="${value.replace(/"/g, "&quot;")}" placeholder="btop --utf-force" />
          </label>
          <p class="muted-note">This pane currently spawns a shell command. Switch it to mpv here if you want a dedicated video pane instead.</p>
          <button id="inspectorRestartPane" type="button" class="secondary">Restart This Pane</button>
        </div>
        ${paneTemplateMarkup()}
        ${selectedPaneSizeSectionMarkup(selectedRole)}
        ${layoutActions}
      `;
      document.getElementById("inspectorPaneType").addEventListener("change", (event) => {
        state.pane_types[paneIndex] = event.target.value;
        renderStudioBoard();
        renderStudioInspector();
      });
      document.getElementById("inspectorPaneCommand").addEventListener("input", (event) => {
        state.pane_commands[paneIndex] = event.target.value;
        renderStudioBoard();
      });
      bindStudioSizeInputs(studioInspector);
      bindPaneTemplateControls();
      document.getElementById("inspectorRestartPane")?.addEventListener("click", () => {
        restartPane(paneIndex).catch((err) => setStatus(err.message, true));
      });
      bindSelectedPaneLayoutActions(selectedRole);
      dispatchSelectedPaneState();
    }

    function renderPlaylistEditor() {
      if (!state) return;
      const ctx = queueEditorContext();
      const playlistEditor = selectedPaneQueueEditor();
      const queueField = selectedPaneQueueField();
      const queueNote = selectedPaneQueueNote();
      const addQueueButton = selectedPaneAddQueueButton();
      if (!ctx || !playlistEditor || !queueField) return;
      const previewRotation = effectivePlaylistThumbRotationDegrees();
      const previewQuarterTurn = previewRotation === 90 || previewRotation === 270;
      if (queueNote) queueNote.textContent = ctx.note;
      if (addQueueButton) addQueueButton.disabled = !ctx.editable;
      queueField.value = (ctx.paths || []).join("\n");
      queueField.disabled = !ctx.editable;
      playlistEditor.innerHTML = "";
      const paths = ctx.paths;
      const groups = compressPlaylistPaths(paths);
      if (!ctx.editable) {
        playlistEditor.innerHTML = `<div class="studio-empty">${ctx.emptyMessage}</div>`;
        return;
      }
      const thumbMetrics = targetPlaylistMetrics(ctx.role);
      if (!groups.length) {
        playlistEditor.innerHTML = `<div class="studio-empty">${ctx.emptyMessage}</div>`;
        return;
      }
      const list = document.createElement("div");
      list.className = "playlist-list";
      groups.forEach((group, index) => {
        const thumb = playlistThumbMarkup(group.path, index, thumbMetrics);
        const cachedThumb = readCachedPlaylistThumb(group.path, thumbMetrics);
        const durationText = cachedThumb?.duration ? formatMediaDuration(cachedThumb.duration) : "";
        const item = document.createElement("div");
        item.className = `playlist-item${thumbMetrics.isPortrait ? " portrait-thumb" : ""}${index % 2 === 1 ? " alt" : ""}`;
        item.draggable = true;
        item.dataset.videoDragIndex = String(index);
        const thumbCell = `
          <div class="playlist-media-cell">
            <div class="playlist-thumb${thumb ? "" : " empty"}${thumbMetrics.cover ? " cover" : ""}" style="aspect-ratio: ${thumbMetrics.aspectRatio}; width: ${thumbMetrics.thumbWidth}px; height: ${thumbMetrics.thumbHeight}px;" data-hover-src="${mediaUrl(group.path).replace(/"/g, "&quot;")}" data-hover-path="${group.path.replace(/"/g, "&quot;")}" data-hover-video="${isLikelyVideoPath(group.path) ? "1" : "0"}">
              <div class="playlist-index">${index + 1}</div>
              ${thumb}
            </div>
          </div>`;
        const controls = `
            <span class="playlist-duration-chip">${durationText}</span>
            <div class="playlist-inline-group">
              <span class="playlist-repeat-label" title="How many times this video repeats in a row">Repeat</span>
              <input class="playlist-repeat" type="number" min="1" step="1" data-video-group-repeat="${index}" value="${group.count}" title="Repeat count" />
              <button class="playlist-mini-btn danger" data-video-group-remove="${index}">Remove</button>
            </div>`;
        const pathInput = `<input class="playlist-path" type="text" data-video-group-index="${index}" value="${group.path.replace(/"/g, "&quot;")}" placeholder="/path/to/video.mp4" />`;
        item.innerHTML = `
          <div class="playlist-row">
            ${thumbCell}
            <div class="playlist-controls">
              <div class="playlist-controls-row">
                ${controls}
              </div>
              ${pathInput}
            </div>
          </div>
        `;
        item.addEventListener("dragstart", () => {
          playlistDragIndex = index;
          item.classList.add("dragging");
        });
        item.addEventListener("dragend", () => {
          playlistDragIndex = null;
          item.classList.remove("dragging");
          list.querySelectorAll(".playlist-item").forEach((node) => node.classList.remove("drag-over"));
        });
        item.addEventListener("dragover", (event) => {
          if (playlistDragIndex == null || playlistDragIndex === index) return;
          event.preventDefault();
          item.classList.add("drag-over");
        });
        item.addEventListener("dragleave", () => item.classList.remove("drag-over"));
        item.addEventListener("drop", (event) => {
          if (playlistDragIndex == null || playlistDragIndex === index) return;
          event.preventDefault();
          item.classList.remove("drag-over");
          moveQueueGroupTo(playlistDragIndex, index);
        });
        list.appendChild(item);
      });
      playlistEditor.appendChild(list);
      playlistEditor.querySelectorAll("input[data-video-group-index]").forEach((input) => {
        input.addEventListener("input", (event) => {
          const idx = Number(event.target.dataset.videoGroupIndex);
          updateQueueGroup(idx, { path: event.target.value });
        });
      });
      playlistEditor.querySelectorAll("input[data-video-group-repeat]").forEach((input) => {
        input.addEventListener("input", (event) => {
          const idx = Number(event.target.dataset.videoGroupRepeat);
          updateQueueGroup(idx, { count: event.target.value });
        });
      });
      playlistEditor.querySelectorAll("[data-video-group-remove]").forEach((button) => {
        button.addEventListener("click", () => removeQueueGroup(Number(button.dataset.videoGroupRemove)));
      });
      playlistEditor.querySelectorAll(".playlist-thumb").forEach((thumb) => {
        thumb.addEventListener("mouseenter", () => {
          if (!window.__kmsThumbHoverOverlay) {
            const overlay = document.createElement("div");
            overlay.id = "kmsThumbHoverOverlay";
            overlay.style.position = "fixed";
            overlay.style.zIndex = "99999";
            overlay.style.pointerEvents = "none";
            overlay.style.display = "none";
            overlay.style.borderRadius = "12px";
            overlay.style.overflow = "hidden";
            overlay.style.background = "transparent";
            overlay.style.boxShadow = "0 18px 40px rgba(0,0,0,0.42)";
            overlay.style.maxWidth = "calc(100vw - 24px)";
            overlay.style.maxHeight = "calc(100vh - 24px)";
            document.body.appendChild(overlay);
            window.__kmsThumbHoverOverlay = overlay;
          }
          const overlay = window.__kmsThumbHoverOverlay;
          overlay.innerHTML = "";
          const src = thumb.dataset.hoverSrc || "";
          const isVideo = thumb.dataset.hoverVideo === "1";
          updatePlaylistHoverOverlay(overlay, thumb);
          let media;
          if (!src) {
            playlistHoverOverlayFallback(overlay, "Preview unavailable");
            overlay.style.display = "block";
            return;
          }
          if (isVideo) {
            media = document.createElement("video");
            media.src = src;
            media.muted = true;
            media.autoplay = true;
            media.loop = true;
            media.playsInline = true;
            media.preload = "auto";
            media.setAttribute("muted", "");
            media.setAttribute("autoplay", "");
            media.setAttribute("playsinline", "");
          } else {
            media = document.createElement("img");
            media.src = src;
            media.alt = thumb.dataset.hoverPath || "Preview";
          }
          media.addEventListener("error", () => {
            playlistHoverOverlayFallback(overlay, "Preview unavailable");
          }, { once: true });
          const updateOverlayToNativeSize = () => {
            const mediaWidth = media instanceof HTMLVideoElement ? media.videoWidth : media.naturalWidth;
            const mediaHeight = media instanceof HTMLVideoElement ? media.videoHeight : media.naturalHeight;
            if (mediaWidth > 0 && mediaHeight > 0) updatePlaylistHoverOverlay(overlay, thumb, mediaWidth, mediaHeight);
          };
          if (media instanceof HTMLVideoElement) {
            media.addEventListener("loadedmetadata", updateOverlayToNativeSize, { once: true });
            media.addEventListener("loadedmetadata", () => {
              if (!Number.isFinite(media.duration) || media.duration <= 0) return;
              const target = Math.min(5, Math.max(0.1, media.duration / 3));
              try {
                media.currentTime = target;
              } catch (_) {
                // ignore seek failure and fall back to current frame
              }
            }, { once: true });
            media.addEventListener("seeked", () => {
              media.play().catch(() => {});
            }, { once: true });
            media.addEventListener("loadeddata", () => {
              media.play().catch(() => {});
            }, { once: true });
            media.addEventListener("canplay", () => {
              media.play().catch(() => {});
            }, { once: true });
          } else {
            media.addEventListener("load", updateOverlayToNativeSize, { once: true });
          }
          media.style.width = "100%";
          media.style.height = "100%";
          media.style.display = "block";
          media.style.objectFit = "contain";
          media.style.background = "transparent";
          overlay.appendChild(media);
          overlay.style.display = "block";
        });
        thumb.addEventListener("mouseleave", () => {
          if (window.__kmsThumbHoverOverlay) window.__kmsThumbHoverOverlay.style.display = "none";
        });
      });
      playlistEditor.querySelectorAll("video[data-preview-video]").forEach((video) => {
        const seekToPreview = () => {
          const path = video.dataset.previewPath || "";
          const durationChip = video.closest(".playlist-item")?.querySelector(".playlist-duration-chip");
          if (Number.isFinite(video.duration) && video.duration > 0) {
            writeCachedPlaylistThumb(path, thumbMetrics, "", video.duration);
            if (durationChip) durationChip.textContent = formatMediaDuration(video.duration);
          }
          video.closest(".playlist-thumb")?.classList.remove("empty");
          if (!Number.isFinite(video.duration) || video.duration <= 0) return;
          try {
            video.currentTime = Math.min(5, Math.max(0.1, video.duration / 3));
          } catch (_) {
            return;
          }
        };
        video.addEventListener("loadedmetadata", seekToPreview, { once: true });
        video.addEventListener("seeked", () => {
          video.pause();
        }, { once: true });
        video.addEventListener("error", () => {
          video.closest(".playlist-thumb")?.classList.add("empty");
          video.remove();
        }, { once: true });
        observePlaylistPreviewVideo(video);
      });
    }

    function ensurePlaylistPreviewObserver() {
      if (playlistPreviewObserver || typeof IntersectionObserver !== "function") return playlistPreviewObserver;
      playlistPreviewObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const video = entry.target;
          activatePlaylistPreviewVideo(video);
          playlistPreviewObserver?.unobserve(video);
        });
      }, {
        root: null,
        rootMargin: "200px 0px",
        threshold: 0.01,
      });
      return playlistPreviewObserver;
    }

    function activatePlaylistPreviewVideo(video) {
      if (!video || video.src) return;
      const src = video.dataset.previewSrc;
      if (!src) return;
      video.src = src;
      try {
        video.load();
      } catch (_) {
        // ignore
      }
    }

    function observePlaylistPreviewVideo(video) {
      const observer = ensurePlaylistPreviewObserver();
      if (!observer) {
        activatePlaylistPreviewVideo(video);
        return;
      }
      observer.observe(video);
    }

    function moveQueueItem(index, delta) {
      const ctx = queueEditorContext();
      if (!ctx) return;
      const target = index + delta;
      if (target < 0 || target >= ctx.paths.length) return;
      const next = ctx.paths.slice();
      const [item] = next.splice(index, 1);
      next.splice(target, 0, item);
      ctx.apply(next);
      renderPlaylistEditor();
      renderStudioBoard();
    }

    function updateQueueGroup(groupIndex, patch) {
      const ctx = queueEditorContext();
      if (!ctx) return;
      const groups = compressPlaylistPaths(ctx.paths);
      if (groupIndex < 0 || groupIndex >= groups.length) return;
      groups[groupIndex] = {
        ...groups[groupIndex],
        ...patch,
      };
      groups[groupIndex].count = Math.max(1, Number(groups[groupIndex].count || 1));
      ctx.apply(expandPlaylistGroups(groups));
      renderPlaylistEditor();
      renderStudioBoard();
    }

    function removeQueueGroup(groupIndex) {
      const ctx = queueEditorContext();
      if (!ctx) return;
      const groups = compressPlaylistPaths(ctx.paths);
      if (groupIndex < 0 || groupIndex >= groups.length) return;
      groups.splice(groupIndex, 1);
      ctx.apply(expandPlaylistGroups(groups));
      renderPlaylistEditor();
      renderStudioBoard();
    }

    function moveQueueGroupTo(fromIndex, toIndex) {
      const ctx = queueEditorContext();
      if (!ctx) return;
      const groups = compressPlaylistPaths(ctx.paths);
      if (fromIndex < 0 || toIndex < 0 || fromIndex >= groups.length || toIndex >= groups.length) return;
      const next = groups.slice();
      const [item] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, item);
      ctx.apply(expandPlaylistGroups(next));
      renderPlaylistEditor();
      renderStudioBoard();
    }

    function removeQueueItem(index) {
      const ctx = queueEditorContext();
      if (!ctx) return;
      ctx.apply(ctx.paths.filter((_, idx) => idx !== index));
      renderPlaylistEditor();
      renderStudioBoard();
    }

    function moveQueueItemTo(fromIndex, toIndex) {
      const ctx = queueEditorContext();
      if (!ctx) return;
      if (fromIndex < 0 || toIndex < 0 || fromIndex >= ctx.paths.length || toIndex >= ctx.paths.length) return;
      const next = ctx.paths.slice();
      const [item] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, item);
      ctx.apply(next);
      renderPlaylistEditor();
      renderStudioBoard();
    }

    function addQueueItem() {
      const ctx = queueEditorContext();
      if (!ctx || !ctx.editable) return;
      ctx.apply([...(ctx.paths || []), ""]);
      renderPlaylistEditor();
      renderStudioBoard();
      renderStudioInspector();
    }

    function selectedPaneLayoutActionsMarkup(role) {
      if (!Number.isFinite(role) || role < 0) return "";
      const canRemove = Number(state?.pane_count || 0) > 1;
      return `
        <div class="selected-pane-section">
          <h2 class="section-title">Layout Actions</h2>
          <div class="actions tight">
            <button type="button" class="secondary studio-split-btn" data-selected-pane-split="col">Split Vertically</button>
            <button type="button" class="secondary studio-split-btn" data-selected-pane-split="row">Split Horizontally</button>
            ${canRemove ? '<button type="button" class="secondary studio-remove-btn" data-selected-pane-remove="true">Remove Pane</button>' : ""}
          </div>
        </div>
      `;
    }

    function bindSelectedPaneLayoutActions(role) {
      if (!studioInspector || !Number.isFinite(role) || role < 0) return;
      studioInspector.querySelectorAll("[data-selected-pane-split]").forEach((button) => {
        button.addEventListener("click", () => {
          selectRole(role);
          const kind = button.dataset.selectedPaneSplit === "row" ? "row" : "col";
          const ok = splitSelectedRole(kind);
          setStatus(ok ? `Split ${roleTitle(role)} ${kind === "row" ? "horizontally" : "vertically"}.` : "Could not split the selected pane.", !ok);
        });
      });
      const removeButton = studioInspector.querySelector("[data-selected-pane-remove]");
      if (!removeButton) return;
      removeButton.addEventListener("click", () => {
        selectRole(role);
        const removed = removeSelectedPane();
        setStatus(removed ? `Removed ${roleTitle(role)}.` : "Could not remove the selected pane.", !removed);
      });
    }

    function splitSelectedRole(kind) {
      if (!state) return false;
      const historySnapshot = captureStudioHistorySnapshot();
      const tree = ensureSplitTreeModel();
      const targetRole = selectedRole;
      const newRole = Number(state.pane_count || 0);
      state.pane_count = newRole + 1;
      ensurePaneCommands(state);
      const changed = splitTreeReplaceLeaf(tree, targetRole, (leaf) => ({
        leaf: false,
        kind,
        pct: 50,
        first: { leaf: true, role: leaf.role },
        second: { leaf: true, role: newRole }
      }));
      if (!changed) {
        state.pane_count = newRole;
        ensurePaneCommands(state);
        return false;
      }
      state.splitTreeModel = tree;
      syncSplitTreeState();
      commitStudioHistory(historySnapshot);
      const paneCountInput = document.getElementById("paneCount");
      if (paneCountInput) paneCountInput.value = String(state.pane_count);
      selectRole(newRole);
      renderPlaylistEditor();
      renderStudioBoard();
      renderStudioInspector();
      return true;
    }

    function addPane() {
      if (!state) return;
      splitSelectedRole("col");
    }

    function removeSelectedPane() {
      if (!state || selectedRole < 0 || Number(state.pane_count || 0) === 1) return false;
      const historySnapshot = captureStudioHistorySnapshot();
      const tree = ensureSplitTreeModel();
      const paneIndex = selectedRole;
      const role = selectedRole;
      if (!splitTreeCollapseRole(tree, role)) return false;
      for (let i = role + 1; i < state.pane_count; i += 1) {
        splitTreeReplaceLeaf(tree, i, () => ({ leaf: true, role: i - 1 }));
      }
      state.pane_commands.splice(paneIndex, 1);
      state.pane_types.splice(paneIndex, 1);
      state.pane_type_raw.splice(paneIndex, 1);
      state.pane_type_settings.splice(paneIndex, 1);
      state.pane_playlists.splice(paneIndex, 1);
      state.pane_playlist_extended.splice(paneIndex, 1);
      state.pane_playlist_fifos.splice(paneIndex, 1);
      state.pane_mpv_outs.splice(paneIndex, 1);
      state.pane_video_rotate.splice(paneIndex, 1);
      state.pane_panscan.splice(paneIndex, 1);
      state.pane_watchdogs.splice(paneIndex, 1);
      state.pane_sync_groups.splice(paneIndex, 1);
      state.pane_video_paths.splice(paneIndex, 1);
      state.pane_mpv_opts.splice(paneIndex, 1);
      state.pane_count = Math.max(1, state.pane_count - 1);
      ensurePaneCommands(state);
      state.splitTreeModel = tree;
      syncSplitTreeState();
      commitStudioHistory(historySnapshot);
      const paneCountInput = document.getElementById("paneCount");
      if (paneCountInput) paneCountInput.value = String(state.pane_count);
      selectRole(-1);
      renderPlaylistEditor();
      renderStudioBoard();
      renderStudioInspector();
      return true;
    }

    function waitForIceGatheringComplete(pc) {
      if (pc.iceGatheringState === "complete") return Promise.resolve();
      return new Promise((resolve) => {
        const checkState = () => {
          if (pc.iceGatheringState === "complete") {
            pc.removeEventListener("icegatheringstatechange", checkState);
            resolve();
          }
        };
        pc.addEventListener("icegatheringstatechange", checkState);
      });
    }

    function syncWebRtcPreviewGeometry() {
      if (!previewVideo || previewVideo.readyState < HTMLMediaElement.HAVE_METADATA) return;
      const width = previewVideo.videoWidth || 0;
      const height = previewVideo.videoHeight || 0;
      if (!width || !height) return;
      previewFrameWidth = width;
      previewFrameHeight = height;
      applyPreviewGeometry();
    }

    function setPreviewStageState(message, idle) {
      if (!previewStage) return;
      previewStage.dataset.previewStatus = message || "";
      previewStage.classList.toggle("is-idle", !!idle);
    }

    async function startLivePreviewStream() {
      stopLivePreviewStream();
      if (document.hidden) return;
      if (!window.RTCPeerConnection) {
        throw new Error("This browser does not support the WebRTC preview");
      }
      setPreviewStageState("Connecting preview…", true);
      let pc = null;
      try {
        pc = new RTCPeerConnection({ iceServers: [] });
        webrtcPeer = pc;
        const remoteStream = new MediaStream();
        webrtcStream = remoteStream;
        const transceiver = pc.addTransceiver("video", { direction: "recvonly" });
        if (transceiver && transceiver.setCodecPreferences && window.RTCRtpReceiver?.getCapabilities) {
          const capabilities = RTCRtpReceiver.getCapabilities("video");
          if (capabilities?.codecs?.length) {
            const codecRank = (mimeType) => {
              if (mimeType === "video/H264") return 0;
              if (mimeType === "video/VP8") return 1;
              if (mimeType === "video/VP9") return 2;
              if (mimeType === "video/AV1") return 3;
              return 4;
            };
            const ordered = capabilities.codecs.slice().sort((a, b) => codecRank(a.mimeType) - codecRank(b.mimeType));
            if (ordered.length) transceiver.setCodecPreferences(ordered);
          }
        }
        const attachPreviewTrack = (track) => {
          if (!track || remoteStream.getTracks().some(existing => existing.id === track.id)) return;
          remoteStream.addTrack(track);
          previewVideo.muted = true;
          previewVideo.autoplay = true;
          previewVideo.playsInline = true;
          previewVideo.setAttribute("muted", "");
          previewVideo.setAttribute("autoplay", "");
          previewVideo.setAttribute("playsinline", "");
          previewVideo.srcObject = remoteStream;
          const ensurePlay = () => previewVideo.play().catch(() => {});
          ensurePlay();
          previewVideo.onloadedmetadata = () => {
            syncWebRtcPreviewGeometry();
            setStatus("Live preview connected over WebRTC.", false, true);
          };
          previewVideo.onresize = () => syncWebRtcPreviewGeometry();
          previewVideo.oncanplay = () => ensurePlay();
          setPreviewStageState("", false);
        };
        pc.addEventListener("track", (event) => {
          attachPreviewTrack(event.track);
        });
        pc.addEventListener("connectionstatechange", () => {
          if (pc !== webrtcPeer) return;
          if (pc.connectionState === "connected") {
            if (!remoteStream.getVideoTracks().length) {
              setStatus("WebRTC connected; waiting for preview frames…", false, true);
            }
            setPreviewStageState("", false);
            return;
          }
          if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
            setPreviewStageState("Preview reconnecting…", true);
            if (webrtcRetryTimer) clearTimeout(webrtcRetryTimer);
            webrtcRetryTimer = setTimeout(() => {
              startLivePreviewStream().catch(err => setStatus(err.message || "Live preview reconnect failed", true));
            }, 800);
          }
        });
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        await waitForIceGatheringComplete(pc);
        const response = await fetch("/api/webrtc-offer", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sdp: pc.localDescription?.sdp || "",
            type: pc.localDescription?.type || "offer",
            preview_profile: resolvedPreviewProfile(),
          }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Failed to establish WebRTC preview");
        if (pc !== webrtcPeer) {
          if (payload.peer_id) {
            fetch("/api/webrtc-close", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ peer_id: payload.peer_id }),
              keepalive: true,
            }).catch(() => {});
          }
          return;
        }
        webrtcPeerId = payload.peer_id || null;
        if (webrtcHeartbeatTimer) clearInterval(webrtcHeartbeatTimer);
        if (webrtcPeerId) {
          webrtcHeartbeatTimer = setInterval(() => {
            if (!webrtcPeerId) return;
            fetch("/api/webrtc-keepalive", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ peer_id: webrtcPeerId }),
            }).catch(() => {});
          }, 5000);
        }
        await pc.setRemoteDescription({ sdp: payload.sdp, type: payload.type });
        attachPreviewTrack(transceiver?.receiver?.track);
      } catch (err) {
        if (pc === webrtcPeer) {
          stopLivePreviewStream();
          setPreviewStageState("Preview unavailable", true);
        }
        throw err;
      }
    }

    function releaseWebRtcPeer(peerId, useBeacon = false) {
      if (!peerId) return;
      const body = JSON.stringify({ peer_id: peerId });
      if (useBeacon && navigator.sendBeacon) {
        navigator.sendBeacon("/api/webrtc-close", new Blob([body], { type: "application/json" }));
        return;
      }
      fetch("/api/webrtc-close", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        keepalive: true,
      }).catch(() => {});
    }

    function stopLivePreviewStream(useBeacon = false) {
      if (webrtcHeartbeatTimer) {
        clearInterval(webrtcHeartbeatTimer);
        webrtcHeartbeatTimer = null;
      }
      if (webrtcRetryTimer) {
        clearTimeout(webrtcRetryTimer);
        webrtcRetryTimer = null;
      }
      if (previewVideo) {
        previewVideo.pause();
        previewVideo.srcObject = null;
        previewVideo.onloadedmetadata = null;
        previewVideo.onresize = null;
        previewVideo.oncanplay = null;
      }
      if (webrtcStream) {
        webrtcStream.getTracks().forEach(track => track.stop());
        webrtcStream = null;
      }
      if (webrtcPeer) {
        webrtcPeer.ontrack = null;
        webrtcPeer.onconnectionstatechange = null;
        webrtcPeer.close();
        webrtcPeer = null;
      }
      if (webrtcPeerId) {
        const peerId = webrtcPeerId;
        webrtcPeerId = null;
        releaseWebRtcPeer(peerId, useBeacon);
      }
      if (livePreviewController) {
        livePreviewController.abort();
        livePreviewController = null;
      }
      if (livePreviewUrl) livePreviewUrl = null;
      setPreviewStageState("Preview offline", true);
    }

    function getPreviewCorrection() {
      return 0;
    }

    function normalizedRotationDegrees() {
      const rotation = Number(state?.rotation || 0);
      const correction = getPreviewCorrection();
      let total = (rotation + correction) % 360;
      if (total < 0) total += 360;
      return total;
    }

    function effectiveDisplayRotationDegrees() {
      return normalizedRotationDegrees();
    }

    function defaultMediaPaneRole() {
      if (selectedRole >= 0) return selectedRole;
      const paneTypes = Array.isArray(state?.pane_types) ? state.pane_types : [];
      const firstMediaPane = paneTypes.findIndex((type) => type === "mpv");
      return firstMediaPane >= 0 ? firstMediaPane : -1;
    }

    function configuredVideoRotationDegrees(role = (selectedRole >= 0 ? selectedRole : defaultMediaPaneRole())) {
      const resolvedRole = Number.isFinite(Number(role))
        ? Number(role)
        : defaultMediaPaneRole();
      if (resolvedRole < 0) return 0;
      const rawValue = state?.pane_video_rotate?.[Number(resolvedRole)] || "0";
      const raw = parseInt(String(rawValue || "0"), 10);
      if (!Number.isFinite(raw)) return 0;
      let total = raw % 360;
      if (total < 0) total += 360;
      return total;
    }

    function effectivePlaylistThumbRotationDegrees(role = (selectedRole >= 0 ? selectedRole : defaultMediaPaneRole())) {
      let total = (normalizedRotationDegrees() + configuredVideoRotationDegrees(role)) % 360;
      if (total < 0) total += 360;
      return total;
    }

    function studioRotationDegrees() {
      return 0;
    }

    function rotatePanels(direction) {
      if (!state) return false;
      const tree = ensureSplitTreeModel();
      if (!tree) return false;
      const rects = computeStudioRects(state);
      const roles = rects
        .map((rect, role) => ({ role, rect: transformStudioPaneRect(rect) }))
        .filter(({ rect }) => rect.w > 0 && rect.h > 0);
      if (roles.length < 2) return false;
      const cx = roles.reduce((sum, entry) => sum + entry.rect.x + entry.rect.w / 2, 0) / roles.length;
      const cy = roles.reduce((sum, entry) => sum + entry.rect.y + entry.rect.h / 2, 0) / roles.length;
      roles.sort((a, b) => {
        const aa = Math.atan2((a.rect.y + a.rect.h / 2) - cy, (a.rect.x + a.rect.w / 2) - cx);
        const ba = Math.atan2((b.rect.y + b.rect.h / 2) - cy, (b.rect.x + b.rect.w / 2) - cx);
        return aa - ba;
      });
      const orderedRoles = roles.map((entry) => entry.role);
      const mapping = {};
      for (let i = 0; i < orderedRoles.length; i += 1) {
        const current = orderedRoles[i];
        const nextIndex = direction === "cw"
          ? (i - 1 + orderedRoles.length) % orderedRoles.length
          : (i + 1) % orderedRoles.length;
        mapping[current] = orderedRoles[nextIndex];
      }
      splitTreeRemapRoles(tree, mapping);
      state.splitTreeModel = tree;
      syncSplitTreeState();
      renderStudioBoard();
      renderStudioInspector();
      renderPlaylistEditor();
      return true;
    }

    function applyPreviewGeometry() {
      const total = effectiveDisplayRotationDegrees();
      const naturalW = previewFrameWidth || 16;
      const naturalH = previewFrameHeight || 9;
      const quarterTurn = total === 90 || total === 270;
      const displayW = quarterTurn ? 9 : 16;
      const displayH = quarterTurn ? 16 : 9;
      const preview = document.getElementById("preview");
      previewLayout?.classList.toggle("portrait", quarterTurn);
      previewLayout?.classList.toggle("landscape", !quarterTurn);
      preview.style.aspectRatio = `${displayW} / ${displayH}`;
      const previewTop = preview.getBoundingClientRect().top;
      const viewportPadding = 24;
      const availableHeight = Math.max(220, Math.floor(window.innerHeight - previewTop - viewportPadding));
      preview.style.width = "100%";
      preview.style.maxWidth = "100%";
      preview.style.maxHeight = "none";
      preview.style.minHeight = `${Math.min(availableHeight, Math.floor(window.innerHeight * 0.72))}px`;
      if (previewVideo) previewVideo.style.transform = "";
      applyStudioGeometry();
    }

    function syncFormToState() {
      const previousPaneCount = Array.isArray(state?.pane_commands) ? state.pane_commands.length : 0;
      const connectorEl = document.getElementById("connector");
      if (connectorEl) state.connector = connectorEl.value.trim();
      const modeEl = document.getElementById("mode");
      const rotationEl = document.getElementById("rotation");
      const fontSizeEl = document.getElementById("fontSize");
      const rightFracEl = document.getElementById("rightFrac");
      const paneSplitEl = document.getElementById("paneSplit");
      const videoFracEl = document.getElementById("videoFrac");
      const paneCountEl = document.getElementById("paneCount");
      const layoutEl = document.getElementById("layout");
      const rolesEl = document.getElementById("roles");
      const fsCycleEl = document.getElementById("fsCycleSec");
      const transitionEl = document.getElementById("transitionMs");
      if (modeEl) state.mode = modeEl.value.trim();
      if (rotationEl) state.rotation = readInt("rotation", 0);
      if (fontSizeEl) state.font_size = readInt("fontSize", 18);
      if (rightFracEl) state.right_frac = readInt("rightFrac", 33);
      if (paneSplitEl) state.pane_split = readInt("paneSplit", 50);
      if (videoFracEl) state.video_frac = readInt("videoFrac", 0);
      if (paneCountEl) state.pane_count = Math.max(1, readInt("paneCount", 2));
      if (layoutEl) state.layout = layoutEl.value;
      if (rolesEl) state.roles = rolesEl.value.trim();
      if (fsCycleEl) state.fs_cycle_sec = readInt("fsCycleSec", 5);
      if (transitionEl) state.transition_ms = Math.max(0, Math.min(5000, readInt("transitionMs", 0)));
      state.visibility_mode = visibilityModeForState(state);
      normalizeVisibilityFlags();
      state.flags.smooth = document.getElementById("flagSmooth").checked;
      state.flags.shuffle = document.getElementById("flagShuffle").checked;
      state.flags.atomic = document.getElementById("flagAtomic").checked;
      state.flags.atomic_nonblock = document.getElementById("flagAtomicNonblock").checked;
      state.flags.gl_finish = document.getElementById("flagGlFinish").checked;
      state.flags.no_osd = document.getElementById("flagNoOsd").checked;
      state.extra_lines = document.getElementById("extraLines").value;
      const queueCtx = queueEditorContext();
      const queueField = selectedPaneQueueField();
      const queuePaths = queueField
        ? queueField.value.split("\n").map(v => v.trim()).filter(Boolean)
        : [];
      if (queueCtx && queueCtx.editable && queueField) {
        queueCtx.apply(queuePaths);
      }
      ensurePaneCommands(state);
      const treeRoles = [];
      splitTreeCollectRoles(normalizeSplitTreeState(), treeRoles);
      const normalizedTreeRoles = Array.from(new Set(treeRoles.map((role) => Number(role)).filter(Number.isFinite))).sort((a, b) => a - b);
      if (treeRoles.length && (normalizedTreeRoles.length !== state.pane_count || normalizedTreeRoles.some((role, index) => role !== index))) {
        state.splitTreeModel = presetTreeFromState(state);
        syncSplitTreeState();
      }
      renderPlaylistEditor();
      renderStudioBoard();
      renderStudioInspector();
      applyPreviewGeometry();
    }

    function fillForm(nextState, configPath, nextRawConfig) {
      const previousSelection = captureSelectedPaneSnapshot(state);
      activeConfigPath = configPath || activeConfigPath;
      state = nextState;
      state.visibility_mode = visibilityModeForState(state);
      normalizeVisibilityFlags();
      rawConfigText = nextRawConfig;
      ensurePaneCommands(state);
      state.splitTreeModel = parseSplitTreeSpec(state.split_tree || "");
      const parsedSelection = Number(state?.selected_pane);
      selectedRole = Number.isFinite(parsedSelection) ? parsedSelection : restoreSelectedRole(state, previousSelection);
      ensureSelectedRole();
      if (selectedRole < 0) {
        selectedRole = restoreSelectedRole(state, previousSelection);
        ensureSelectedRole();
      }
      state.selected_pane = selectedRole;
      document.getElementById("configPath").textContent = `Config: ${configPath}`;
      const modeEl = document.getElementById("mode");
      const connectorEl = document.getElementById("connector");
      const rotationEl = document.getElementById("rotation");
      const fontSizeEl = document.getElementById("fontSize");
      const rightFracEl = document.getElementById("rightFrac");
      const paneSplitEl = document.getElementById("paneSplit");
      const videoFracEl = document.getElementById("videoFrac");
      const paneCountEl = document.getElementById("paneCount");
      const layoutEl = document.getElementById("layout");
      const rolesEl = document.getElementById("roles");
      const fsCycleEl = document.getElementById("fsCycleSec");
      const transitionEl = document.getElementById("transitionMs");
      if (modeEl) modeEl.value = state.mode || "";
      if (connectorEl) connectorEl.value = state.connector || "";
      if (rotationEl) rotationEl.value = String(state.rotation || 0);
      if (fontSizeEl) fontSizeEl.value = String(state.font_size || 18);
      if (rightFracEl) rightFracEl.value = String(state.right_frac || 33);
      if (paneSplitEl) paneSplitEl.value = String(state.pane_split || 50);
      if (videoFracEl) videoFracEl.value = String(state.video_frac || 0);
      if (paneCountEl) paneCountEl.value = String(state.pane_count || 2);
      if (layoutEl) layoutEl.value = state.layout || "stack";
      if (rolesEl) rolesEl.value = state.roles || "";
      if (fsCycleEl) fsCycleEl.value = String(state.fs_cycle_sec || 5);
      if (transitionEl) transitionEl.value = String(state.transition_ms || 0);
      const queueField = selectedPaneQueueField();
      const queueCtx = queueEditorContext();
      if (queueField) queueField.value = (queueCtx?.paths || []).join("\n");
      document.getElementById("flagSmooth").checked = !!state.flags.smooth;
      document.getElementById("flagShuffle").checked = !!state.flags.shuffle;
      document.getElementById("flagAtomic").checked = !!state.flags.atomic;
      document.getElementById("flagAtomicNonblock").checked = !!state.flags.atomic_nonblock;
      document.getElementById("flagGlFinish").checked = !!state.flags.gl_finish;
      document.getElementById("flagNoOsd").checked = !!state.flags.no_osd;
      document.getElementById("extraLines").value = state.extra_lines || "";
      document.getElementById("rawConfig").value = rawConfigText;
      renderPlaylistEditor();
      renderStudioBoard();
      renderStudioInspector();
      applyPreviewGeometry();
      updateStudioHistoryButtons();
    }

    async function loadConnectorOptions() {
      const connectorEl = document.getElementById("connector");
      if (!connectorEl) return;
      const response = await fetch("/api/connectors");
      const text = await response.text();
      let payload = {};
      if (text.trim()) {
        try {
          payload = JSON.parse(text);
        } catch (err) {
          throw new Error("Failed to parse connectors response");
        }
      }
      if (!response.ok) throw new Error(payload.error || "Failed to load displays");
      const connectors = Array.isArray(payload.connectors) ? payload.connectors : [];
      const currentValue = state.connector || "";
      connectorEl.innerHTML = "";
      const autoOption = document.createElement("option");
      autoOption.value = "";
      autoOption.textContent = "Auto";
      connectorEl.appendChild(autoOption);
      connectors.forEach((connector) => {
        const option = document.createElement("option");
        option.value = connector.name || "";
        option.textContent = connector.id ? `${connector.name} (${connector.id})` : (connector.name || "");
        connectorEl.appendChild(option);
      });
      if (currentValue && !connectors.some((connector) => connector.name === currentValue)) {
        const option = document.createElement("option");
        option.value = currentValue;
        option.textContent = `${currentValue} (saved)`;
        connectorEl.appendChild(option);
      }
      connectorEl.value = state.connector || "";
    }

    function selectedSceneId() {
      return document.getElementById("sceneSelect")?.value || "";
    }

    function formatHealthBytes(value) {
      const bytes = Number(value || 0);
      if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
      if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MiB`;
      return `${Math.round(bytes / 1024)} KiB`;
    }

    function renderHealth(payload) {
      const grid = document.getElementById("healthGrid");
      if (!grid) return;
      const compositor = payload.compositor;
      const web = payload.web;
      const gpu = (payload.gpu || [])[0] || {};
      const recovery = payload.last_recovery;
      const cards = [
        ["Compositor", compositor ? "Running" : "Stopped", compositor ? "health-ok" : "health-bad"],
        ["Compositor CPU", `${Number(compositor?.cpu_percent || 0).toFixed(1)}%`, ""],
        ["Compositor RAM", formatHealthBytes(compositor?.rss_bytes), ""],
        ["GPU Busy", gpu.busy_percent == null ? "Unavailable" : `${gpu.busy_percent}%`, ""],
        ["Web CPU", `${Number(web?.cpu_percent || 0).toFixed(1)}%`, ""],
        ["Web RAM", formatHealthBytes(web?.rss_bytes), ""],
        ["Preview Clients", String(payload.preview_peers || 0), ""],
        ["Pane Processes", String((payload.pane_processes || []).length), ""],
        ["Last Recovery", recovery ? `${roleTitle(Number(recovery.pane || 0))} · ${recovery.source || "unknown"}` : "None", recovery?.ok === false ? "health-bad" : ""],
      ];
      grid.innerHTML = "";
      cards.forEach(([labelText, valueText, className]) => {
        const card = document.createElement("div");
        card.className = "health-card";
        const label = document.createElement("div");
        label.className = "health-label";
        label.textContent = labelText;
        const value = document.createElement("div");
        value.className = `health-value ${className}`.trim();
        value.textContent = valueText;
        card.append(label, value);
        grid.appendChild(card);
      });
      const processes = document.getElementById("healthProcesses");
      if (processes) {
        processes.textContent = (payload.pane_processes || []).length
          ? (payload.pane_processes || []).map((item) => `${item.name} · PID ${item.pid} · ${item.cpu_percent}% · ${formatHealthBytes(item.rss_bytes)}`).join("\n")
          : "No direct pane child processes detected.";
      }
      const errors = document.getElementById("healthErrors");
      if (errors) {
        errors.textContent = (payload.recent_errors || []).length
          ? `Recent errors\n${payload.recent_errors.join("\n")}`
          : "No recent logged errors.";
      }
    }

    async function loadHealth() {
      const response = await fetch("/api/health");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Failed to load health data");
      renderHealth(payload);
    }

    async function loadMonitors() {
      const select = document.getElementById("monitorConnector");
      const note = document.getElementById("monitorStatus");
      if (!select || !note) return;
      const response = await fetch("/api/monitors");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Failed to discover monitor controls");
      const monitors = Array.isArray(payload.monitors) ? payload.monitors : [];
      select.innerHTML = "";
      monitors.forEach((monitor) => {
        const option = document.createElement("option");
        option.value = String(monitor.connector || "");
        option.textContent = `${monitor.label || monitor.connector}${monitor.available ? "" : " · unavailable"}`;
        option.disabled = !monitor.available;
        select.appendChild(option);
      });
      if (!monitors.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No connected DDC/CI displays found";
        select.appendChild(option);
      }
      const available = monitors.find((monitor) => monitor.available);
      if (available) select.value = String(available.connector || "");
      note.textContent = available
        ? `Ready on ${available.bus}. Controls are sent only when a button is pressed.`
        : "No writable DDC/CI bus is available for a connected display.";
    }

    async function applyMonitorControl(control, value) {
      const connector = document.getElementById("monitorConnector")?.value || "";
      if (!connector) throw new Error("Select an available display first");
      if (control === "power" && Number(value) === 4 && !window.confirm("Power off this display?")) return;
      const response = await fetch("/api/monitors/set", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ connector, control, value: Number(value) }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Monitor control failed");
      const note = document.getElementById("monitorStatus");
      if (note) note.textContent = `Sent ${control} = ${value} to ${connector}.`;
      setStatus(`Sent monitor ${control} control.`, false, true);
    }

    function renderConfigHistory(entries) {
      const list = document.getElementById("historyList");
      if (!list) return;
      list.innerHTML = "";
      (entries || []).forEach((entry) => {
        const row = document.createElement("div");
        row.className = "history-item";
        const label = document.createElement("div");
        label.className = "history-label";
        label.textContent = `${new Date(Number(entry.created || 0) * 1000).toLocaleString()} · ${entry.reason || "change"} · ${formatHealthBytes(entry.size)}`;
        const actions = document.createElement("div");
        actions.className = "history-item-actions";
        const diff = document.createElement("button");
        diff.type = "button";
        diff.className = "secondary";
        diff.textContent = "Diff";
        diff.dataset.historyDiff = String(entry.id || "");
        const rollback = document.createElement("button");
        rollback.type = "button";
        rollback.className = "secondary danger";
        rollback.textContent = "Roll Back";
        rollback.dataset.historyRollback = String(entry.id || "");
        actions.append(diff, rollback);
        row.append(label, actions);
        list.appendChild(row);
      });
      if (!(entries || []).length) list.textContent = "No prior config snapshots yet.";
    }

    async function loadConfigHistory() {
      const response = await fetch("/api/history");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Failed to load config history");
      renderConfigHistory(payload.entries || []);
    }

    async function showConfigHistoryDiff(entryId) {
      const response = await fetch(`/api/history/diff?id=${encodeURIComponent(entryId)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Failed to load config diff");
      const output = document.getElementById("historyDiff");
      if (output) output.textContent = payload.diff || "No differences from the current config.";
    }

    async function rollbackConfigHistory(entryId) {
      if (!window.confirm("Roll back to this config snapshot? The current config will be saved first.")) return;
      const response = await fetch("/api/history/rollback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: entryId }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Config rollback failed");
      studioUndoStack = [];
      studioRedoStack = [];
      fillForm(payload.state, payload.config_path, payload.raw_config);
      renderConfigHistory(payload.entries || []);
      const output = document.getElementById("historyDiff");
      if (output) output.textContent = "";
      scheduleLivePreview();
      setStatus("Rolled back config; the previous current version was saved.", false, true);
    }

    function scheduleHealthPolling() {
      if (healthTimer) {
        clearInterval(healthTimer);
        healthTimer = null;
      }
      const panel = document.getElementById("healthPanel");
      if (!panel?.open || document.hidden) return;
      loadHealth().catch((err) => setStatus(err.message, true));
      healthTimer = setInterval(() => {
        loadHealth().catch((err) => setStatus(err.message, true));
      }, 3000);
    }

    function renderSceneControls(preferredSceneId = selectedSceneId()) {
      const select = document.getElementById("sceneSelect");
      const nameInput = document.getElementById("sceneName");
      if (!select || !nameInput) return;
      select.innerHTML = "";
      const emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = "New scene…";
      select.appendChild(emptyOption);
      (sceneCatalog.scenes || []).forEach((scene) => {
        const option = document.createElement("option");
        option.value = String(scene.id || "");
        option.textContent = String(scene.name || "Unnamed scene");
        select.appendChild(option);
      });
      select.value = (sceneCatalog.scenes || []).some((scene) => scene.id === preferredSceneId)
        ? preferredSceneId
        : "";
      const selected = (sceneCatalog.scenes || []).find((scene) => scene.id === select.value);
      if (selected) nameInput.value = String(selected.name || "");
      const hasSelection = !!selected;
      document.getElementById("sceneApplyBtn").disabled = !hasSelection;
      document.getElementById("sceneDeleteBtn").disabled = !hasSelection;
      document.getElementById("sceneScheduleAddBtn").disabled = !hasSelection;

      const scheduleList = document.getElementById("sceneScheduleList");
      if (!scheduleList) return;
      scheduleList.innerHTML = "";
      const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
      (sceneCatalog.schedules || []).forEach((schedule) => {
        const scene = (sceneCatalog.scenes || []).find((item) => item.id === schedule.scene_id);
        const item = document.createElement("div");
        item.className = "scene-schedule-item";
        const label = document.createElement("span");
        const days = (schedule.days || []).map((day) => dayNames[Number(day)] || "").filter(Boolean).join(", ");
        label.textContent = `${scene?.name || "Missing scene"} · ${schedule.time || "--:--"} · ${days}`;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "secondary";
        remove.textContent = "Remove";
        remove.dataset.scheduleId = String(schedule.id || "");
        item.append(label, remove);
        scheduleList.appendChild(item);
      });
    }

    async function sceneApi(path, payload = {}) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Scene operation failed");
      return result;
    }

    function setRemoteStatus(message, isError = false) {
      const el = document.getElementById("remoteStatus");
      if (!el) return;
      el.textContent = message;
      el.className = `status${isError ? " error" : " success"}`;
    }

    function renderRemoteControls(scenes, health) {
      const sceneGrid = document.getElementById("remoteScenes");
      const paneGrid = document.getElementById("remotePanes");
      const healthEl = document.getElementById("remoteHealth");
      if (sceneGrid) {
        sceneGrid.innerHTML = "";
        (scenes || []).forEach((scene) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "secondary";
          button.dataset.remoteScene = String(scene.id || "");
          button.textContent = String(scene.name || "Unnamed scene");
          sceneGrid.appendChild(button);
        });
        if (!(scenes || []).length) sceneGrid.textContent = "No saved scenes yet.";
      }
      if (paneGrid) {
        paneGrid.innerHTML = "";
        const count = Math.max(0, Number(remoteState?.pane_count || 0));
        for (let pane = 0; pane < count; pane += 1) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "secondary";
          button.dataset.remotePane = String(pane);
          button.textContent = `${roleName(pane)} · ${remoteState?.pane_types?.[pane] || "terminal"}`;
          paneGrid.appendChild(button);
        }
      }
      document.querySelectorAll("[data-remote-visibility]").forEach((button) => {
        button.classList.toggle("primary", button.dataset.remoteVisibility === visibilityModeForState(remoteState));
        button.classList.toggle("secondary", button.dataset.remoteVisibility !== visibilityModeForState(remoteState));
      });
      if (healthEl && health) {
        const compositor = health?.compositor;
        healthEl.textContent = compositor
          ? `Online · PID ${compositor.pid} · CPU ${Number(compositor.cpu_percent || 0).toFixed(1)}% · ${formatHealthBytes(compositor.rss_bytes)}`
          : "Compositor stopped";
      }
    }

    async function loadRemoteControl() {
      const [stateResponse, scenesResponse, healthResponse] = await Promise.all([
        fetch("/api/state"), fetch("/api/scenes"), fetch("/api/health"),
      ]);
      const [statePayload, scenesPayload, healthPayload] = await Promise.all([
        stateResponse.json(), scenesResponse.json(), healthResponse.json(),
      ]);
      if (!stateResponse.ok) throw new Error(statePayload.error || "Failed to load state");
      if (!scenesResponse.ok) throw new Error(scenesPayload.error || "Failed to load scenes");
      if (!healthResponse.ok) throw new Error(healthPayload.error || "Failed to load health");
      remoteState = statePayload.state;
      sceneCatalog = scenesPayload;
      renderRemoteControls(sceneCatalog.scenes || [], healthPayload);
      setRemoteStatus("Remote is ready.");
    }

    async function setRemoteVisibility(mode) {
      if (!remoteState) throw new Error("Remote state is not loaded yet");
      remoteState.visibility_mode = mode;
      const response = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: remoteState }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Failed to change visible content");
      remoteState = payload.state;
      renderRemoteControls(sceneCatalog.scenes || [], null);
      setRemoteStatus("Updated visible content.");
    }

    async function loadScenes(preferredSceneId = selectedSceneId()) {
      const response = await fetch("/api/scenes");
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Failed to load scenes");
      sceneCatalog = payload;
      renderSceneControls(preferredSceneId);
    }

    async function saveCurrentScene() {
      syncFormToState();
      const name = document.getElementById("sceneName")?.value || "";
      const payload = await sceneApi("/api/scenes/save", {
        id: selectedSceneId(),
        name,
        state,
      });
      sceneCatalog = payload;
      renderSceneControls(payload.scene?.id || "");
      setStatus(`Saved scene ${payload.scene?.name || name}.`, false, true);
    }

    async function applySelectedScene() {
      const id = selectedSceneId();
      if (!id) return;
      const payload = await sceneApi("/api/scenes/apply", { id });
      studioUndoStack = [];
      studioRedoStack = [];
      fillForm(payload.state, payload.config_path, payload.raw_config);
      setStatus(`Applied scene ${payload.scene?.name || ""}.`, false, true);
    }

    async function deleteSelectedScene() {
      const id = selectedSceneId();
      if (!id) return;
      const scene = (sceneCatalog.scenes || []).find((item) => item.id === id);
      if (!window.confirm(`Delete scene “${scene?.name || "Unnamed scene"}”?`)) return;
      const payload = await sceneApi("/api/scenes/delete", { id });
      sceneCatalog = payload;
      document.getElementById("sceneName").value = "";
      renderSceneControls("");
      setStatus("Deleted scene.", false, true);
    }

    async function addSceneSchedule() {
      const sceneId = selectedSceneId();
      if (!sceneId) return;
      const atTime = document.getElementById("sceneScheduleTime")?.value || "";
      const days = Array.from(document.querySelectorAll("#sceneScheduleDays input:checked"))
        .map((input) => Number(input.value));
      const payload = await sceneApi("/api/schedules/save", { scene_id: sceneId, time: atTime, days });
      sceneCatalog = payload;
      renderSceneControls(sceneId);
      setStatus("Added scene schedule.", false, true);
    }

    async function loadState() {
      const response = await fetch("/api/state");
      const text = await response.text();
      let payload = {};
      if (text.trim()) {
        try {
          payload = JSON.parse(text);
        } catch (err) {
          throw new Error("Failed to parse state response");
        }
      }
      if (!response.ok) throw new Error(payload.error || "Failed to load state");
      studioUndoStack = [];
      studioRedoStack = [];
      fillForm(payload.state, payload.config_path, payload.raw_config);
      await loadConnectorOptions();
      scheduleLivePreview();
      setStatus(`Loaded ${payload.config_path}`, false, true);
    }

    async function saveState() {
      syncFormToState();
      const response = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state })
      });
      const text = await response.text();
      let payload = {};
      if (text.trim()) {
        try {
          payload = JSON.parse(text);
        } catch (err) {
          throw new Error("Failed to parse save response");
        }
      }
      if (!response.ok) throw new Error(payload.error || "Failed to save config");
      if (!text.trim()) {
        await new Promise((resolve) => setTimeout(resolve, 250));
        await loadState();
        setStatus("Saved config. kms_mosaic will reload on file change.", false, true);
        return;
      }
      fillForm(payload.state, payload.config_path, payload.raw_config);
      scheduleLivePreview();
      setStatus(`Saved ${payload.config_path}. kms_mosaic will reload on file change.`, false, true);
    }

    async function saveRawConfig() {
      const rawConfig = document.getElementById("rawConfig").value;
      const response = await fetch("/api/raw_config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_config: rawConfig })
      });
      const text = await response.text();
      let payload = {};
      if (text.trim()) {
        try {
          payload = JSON.parse(text);
        } catch (err) {
          throw new Error("Failed to parse raw config save response");
        }
      }
      if (!response.ok) throw new Error(payload.error || "Failed to save raw config");
      if (!text.trim()) {
        await new Promise((resolve) => setTimeout(resolve, 250));
        await loadState();
        setStatus("Saved raw config.", false, true);
        return;
      }
      fillForm(payload.state, payload.config_path, payload.raw_config);
      scheduleLivePreview();
      setStatus(`Saved raw config to ${payload.config_path}.`, false, true);
    }

    async function setVisibilityMode(mode) {
      if (!state) return;
      const nextMode = mode === "no-video"
        ? "no-video"
        : (mode === "no-terminal" || mode === "no-panes")
          ? "no-terminal"
          : "neither";
      const previousMode = visibilityModeForState(state);
      state.visibility_mode = nextMode;
      try {
        await saveState();
      } catch (err) {
        state.visibility_mode = previousMode;
        renderPlaylistEditor();
        renderStudioBoard();
        renderStudioInspector();
        applyPreviewGeometry();
        try {
          await loadState();
        } catch (_) {
          // Keep the rolled-back local state if the reload path is unavailable.
        }
        throw err;
      }
    }

    function setStatus(message, isError, isSuccess) {
      statusEl.textContent = message;
      statusEl.className = `status${isError ? " error" : isSuccess ? " success" : ""}`;
    }

    function currentLivePreviewDelay() {
      return 180;
    }

    function scheduleLivePreview() {
      if (livePreviewTimer) {
        clearTimeout(livePreviewTimer);
        livePreviewTimer = null;
      }
      if (remoteMode || document.hidden) {
        stopLivePreviewStream();
        return;
      }
      const delay = currentLivePreviewDelay();
      if (delay == null) {
        stopLivePreviewStream();
        return;
      }
      if (!webrtcPeer) {
        startLivePreviewStream().catch(err => setStatus(err.message, true));
        return;
      }
      if (["new", "connecting", "connected"].includes(webrtcPeer.connectionState)) return;
      livePreviewTimer = setTimeout(async () => {
        try {
          await startLivePreviewStream();
        } catch (err) {
          setStatus(err.message, true);
        }
      }, delay);
    }

    document.getElementById("studioUndoBtn")?.addEventListener("click", () => undoStudioLayout());
    document.getElementById("studioRedoBtn")?.addEventListener("click", () => redoStudioLayout());
    document.getElementById("sceneSelect")?.addEventListener("change", () => renderSceneControls(selectedSceneId()));
    document.getElementById("sceneSaveBtn")?.addEventListener("click", () => {
      saveCurrentScene().catch((err) => setStatus(err.message, true));
    });
    document.getElementById("sceneApplyBtn")?.addEventListener("click", () => {
      applySelectedScene().catch((err) => setStatus(err.message, true));
    });
    document.getElementById("sceneDeleteBtn")?.addEventListener("click", () => {
      deleteSelectedScene().catch((err) => setStatus(err.message, true));
    });
    document.getElementById("sceneScheduleAddBtn")?.addEventListener("click", () => {
      addSceneSchedule().catch((err) => setStatus(err.message, true));
    });
    document.getElementById("sceneScheduleList")?.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-schedule-id]");
      if (!button) return;
      const scheduleId = button.dataset.scheduleId || "";
      sceneApi("/api/schedules/delete", { id: scheduleId })
        .then((payload) => {
          sceneCatalog = payload;
          renderSceneControls(selectedSceneId());
          setStatus("Removed scene schedule.", false, true);
        })
        .catch((err) => setStatus(err.message, true));
    });
    document.getElementById("healthPanel")?.addEventListener("toggle", () => scheduleHealthPolling());
    document.getElementById("remoteRefreshBtn")?.addEventListener("click", () => {
      loadRemoteControl().catch((err) => setRemoteStatus(err.message, true));
    });
    document.getElementById("remoteScenes")?.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-remote-scene]");
      if (!button) return;
      sceneApi("/api/scenes/apply", { id: button.dataset.remoteScene || "" })
        .then((payload) => {
          remoteState = payload.state;
          renderRemoteControls(sceneCatalog.scenes || [], null);
          setRemoteStatus(`Applied ${payload.scene?.name || "scene"}.`);
        })
        .catch((err) => setRemoteStatus(err.message, true));
    });
    document.getElementById("remoteVisibility")?.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-remote-visibility]");
      if (!button) return;
      setRemoteVisibility(button.dataset.remoteVisibility || "neither")
        .catch((err) => setRemoteStatus(err.message, true));
    });
    document.getElementById("remotePanes")?.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-remote-pane]");
      if (!button) return;
      sceneApi("/api/panes/restart", { pane: Number(button.dataset.remotePane) })
        .then(() => setRemoteStatus(`Restart queued for ${roleName(Number(button.dataset.remotePane))}.`))
        .catch((err) => setRemoteStatus(err.message, true));
    });
    document.getElementById("monitorPanel")?.addEventListener("toggle", (event) => {
      if (event.currentTarget.open) loadMonitors().catch((err) => setStatus(err.message, true));
    });
    document.getElementById("configHistoryPanel")?.addEventListener("toggle", (event) => {
      if (event.currentTarget.open) loadConfigHistory().catch((err) => setStatus(err.message, true));
    });
    document.getElementById("historyRefreshBtn")?.addEventListener("click", () => {
      loadConfigHistory().catch((err) => setStatus(err.message, true));
    });
    document.getElementById("historyList")?.addEventListener("click", (event) => {
      const diffButton = event.target?.closest?.("[data-history-diff]");
      const rollbackButton = event.target?.closest?.("[data-history-rollback]");
      if (diffButton) {
        showConfigHistoryDiff(diffButton.dataset.historyDiff || "").catch((err) => setStatus(err.message, true));
      } else if (rollbackButton) {
        rollbackConfigHistory(rollbackButton.dataset.historyRollback || "").catch((err) => setStatus(err.message, true));
      }
    });
    document.getElementById("monitorPanel")?.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-monitor-control]");
      if (!button) return;
      const valueElement = button.dataset.monitorValueId
        ? document.getElementById(button.dataset.monitorValueId)
        : null;
      const value = valueElement?.value ?? button.dataset.monitorValue;
      applyMonitorControl(button.dataset.monitorControl || "", value)
        .catch((err) => setStatus(err.message, true));
    });
    previewProfileSelect?.addEventListener("change", () => {
      try { localStorage.setItem("kmsMosaicPreviewProfile", selectedPreviewProfile()); } catch (err) {}
      setStatus(`Switching preview to ${resolvedPreviewProfile()} mode…`, false, true);
      startLivePreviewStream().catch((err) => setStatus(err.message, true));
    });
    document.addEventListener("keydown", (event) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
      const target = event.target;
      if (target?.matches?.("input, textarea, select, [contenteditable='true']")) return;
      event.preventDefault();
      if (event.shiftKey) redoStudioLayout();
      else undoStudioLayout();
    });

    document.getElementById("saveBtn").addEventListener("click", async () => {
      try {
        await saveState();
      } catch (err) {
        setStatus(err.message, true);
      }
    });
    document.getElementById("reloadBtn").addEventListener("click", async () => {
      try {
        await loadState();
      } catch (err) {
        setStatus(err.message, true);
      }
    });
    document.getElementById("saveRawBtn").addEventListener("click", async () => {
      try {
        await saveRawConfig();
      } catch (err) {
        setStatus(err.message, true);
      }
    });
    window.addEventListener("resize", () => {
      applyPreviewGeometry();
      renderStudioBoard();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        stopLivePreviewStream();
      } else {
        scheduleLivePreview();
      }
      scheduleHealthPolling();
    });
    [
      "mode","connector","rotation","fontSize","rightFrac","paneSplit",
      "videoFrac","paneCount","layout","roles","fsCycleSec","transitionMs",
      "videoList","extraLines","flagSmooth","flagShuffle","flagAtomic",
      "flagAtomicNonblock","flagGlFinish","flagNoOsd"
    ].forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("input", () => syncFormToState());
      el.addEventListener("change", () => syncFormToState());
    });
    window.currentVisibilityMode = currentVisibilityMode;
    window.fillForm = fillForm;
    window.loadConnectorOptions = loadConnectorOptions;
    window.loadScenes = loadScenes;
    window.loadHealth = loadHealth;
    window.loadMonitors = loadMonitors;
    window.loadConfigHistory = loadConfigHistory;
    window.loadPaneTemplates = loadPaneTemplates;
    window.loadState = loadState;
    window.saveState = saveState;
    window.scheduleLivePreview = scheduleLivePreview;
    window.setStatus = setStatus;
    window.setVisibilityMode = setVisibilityMode;
    if (remoteMode) {
      loadRemoteControl().catch((err) => setRemoteStatus(err.message, true));
    } else {
      loadState().catch(err => setStatus(err.message, true));
      loadScenes().catch(err => setStatus(err.message, true));
      loadPaneTemplates().catch(err => setStatus(err.message, true));
      requestAnimationFrame(() => {
        if (!webrtcPeer) scheduleLivePreview();
      });
    }
    window.addEventListener("pagehide", () => stopLivePreviewStream());
    window.addEventListener("beforeunload", () => stopLivePreviewStream(true));
  </script>
</body>
</html>
"""


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "KMSMosaicWeb/0.1"

    @property
    def app_config(self) -> WebConfig:
        return self.server.app_config  # type: ignore[attr-defined]

    @property
    def webrtc(self) -> WebRTCBridge:
        return self.server.webrtc  # type: ignore[attr-defined]

    @property
    def scenes(self) -> SceneManager:
        return self.server.scenes  # type: ignore[attr-defined]

    @property
    def health(self) -> HealthMonitor:
        return self.server.health  # type: ignore[attr-defined]

    @property
    def history(self) -> ConfigHistory:
        return self.server.history  # type: ignore[attr-defined]

    @property
    def templates(self) -> PaneTemplateManager:
        return self.server.templates  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        cfg = getattr(self.server, "app_config", None)
        if cfg is None or not getattr(cfg, "verbose", False):
            return
        print(f"[web] {self.address_string()} - {fmt % args}")

    def log_error(self, fmt: str, *args: Any) -> None:
        cfg = getattr(self.server, "app_config", None)
        if cfg is None or not getattr(cfg, "verbose", False):
            return
        print(f"[web-err] {self.address_string()} - {fmt % args}")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_text_atomic(self, path: Path, text: str) -> None:
        write_text_atomic(path, text)

    def _read_state(self) -> dict[str, Any]:
        path = self.app_config.config_path
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return parse_config_text(text)

    def _read_raw_config(self) -> str:
        path = self.app_config.config_path
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _request_snapshot(self) -> bytes:
        data, _ = read_latest_raw_preview_frame(self.app_config, 0, 180, 3.0)
        return data

    def _write_preview_lease(self, interval_ms: int) -> None:
        write_preview_lease(self.app_config, interval_ms)

    def _wait_for_snapshot_update(self, last_mtime_ns: int, timeout_sec: float = 3.0) -> tuple[bytes, int]:
        output_path = self.app_config.snapshot_output_path
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if output_path.exists():
                st = output_path.stat()
                if st.st_mtime_ns > last_mtime_ns and st.st_size > 0:
                    return output_path.read_bytes(), st.st_mtime_ns
            time.sleep(0.015)
        raise TimeoutError("Timed out waiting for preview frame")

    def _thumbnail_cache_path(self, source: Path) -> Path:
        digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
        return self.app_config.thumb_cache_dir / f"{digest}.jpg"

    def _prune_thumbnail_cache(self) -> None:
        cache_dir = self.app_config.thumb_cache_dir
        if not cache_dir.exists():
            return
        now = time.time()
        entries: list[tuple[Path, os.stat_result]] = []
        for path in cache_dir.glob("*.jpg"):
            try:
                st = path.stat()
                if now - st.st_mtime > THUMB_CACHE_MAX_AGE_SEC:
                    path.unlink(missing_ok=True)
                    continue
                entries.append((path, st))
            except OSError:
                continue
        entries.sort(key=lambda item: item[1].st_mtime_ns, reverse=True)
        total_bytes = 0
        for index, (path, st) in enumerate(entries):
            if index >= THUMB_CACHE_MAX_FILES or total_bytes + st.st_size > THUMB_CACHE_MAX_BYTES:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            total_bytes += st.st_size

    def _generate_thumbnail(self, source: Path, dest: Path) -> bool:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".kms_mosaic_thumb.", suffix=".jpg", dir=str(dest.parent))
        os.close(fd)
        try:
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-ss", "5",
                "-i", str(source),
                "-frames:v", "1",
                "-vf", "scale=320:-2:force_original_aspect_ratio=decrease",
                "-q:v", "4",
                temp_path,
            ]
            result = subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode != 0 or not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                return False
            os.replace(temp_path, dest)
            return True
        finally:
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _read_thumbnail(self, source_path: str) -> bytes | None:
        source = Path(source_path)
        if not source.exists() or not source.is_file():
            return None
        dest = self._thumbnail_cache_path(source)
        src_mtime = source.stat().st_mtime_ns
        cache_ok = dest.exists() and dest.stat().st_mtime_ns >= src_mtime and dest.stat().st_size > 0
        if not cache_ok and not self._generate_thumbnail(source, dest):
            return None
        try:
            os.utime(dest, None)
        except OSError:
            pass
        self._prune_thumbnail_cache()
        return dest.read_bytes() if dest.exists() else None

    def _serve_media_file(self, source_path: str) -> None:
        source = Path(source_path)
        if not source.exists() or not source.is_file():
            self._send_json({"error": "Media not found"}, status=404)
            return

        size = source.stat().st_size
        content_type = mimetypes.guess_type(str(source))[0] or "application/octet-stream"
        start = 0
        end = size - 1
        status = HTTPStatus.OK

        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            spec = range_header[len("bytes="):].strip()
            if "-" in spec:
                start_text, end_text = spec.split("-", 1)
                if start_text:
                    start = max(0, int(start_text))
                if end_text:
                    end = min(size - 1, int(end_text))
                if not start_text and end_text:
                    suffix = min(size, int(end_text))
                    start = max(0, size - suffix)
                    end = size - 1
                if start > end or start >= size:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = HTTPStatus.PARTIAL_CONTENT

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "max-age=300")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with source.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def _stream_live_bin(self, interval_ms: int) -> None:
        interval_ms = max(100, min(interval_ms, 1000))
        heartbeat_sec = min(max(interval_ms / 1000.0, 0.15), 1.0)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        output_path = self.app_config.snapshot_output_path
        last_mtime_ns = output_path.stat().st_mtime_ns if output_path.exists() else 0

        try:
            while True:
                self._write_preview_lease(interval_ms)
                frame, last_mtime_ns = self._wait_for_snapshot_update(last_mtime_ns)
                self.wfile.write(len(frame).to_bytes(4, "big"))
                self.wfile.write(frame)
                self.wfile.flush()
                time.sleep(heartbeat_sec)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, socket.timeout):
            return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            data = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path == "/api/state":
            self._send_json({
                "config_path": str(self.app_config.config_path),
                "state": self._read_state(),
                "raw_config": self._read_raw_config(),
            })
            return

        if parsed.path == "/api/connectors":
            self._send_json({"connectors": list_connectors()})
            return

        if parsed.path == "/api/scenes":
            self._send_json(self.scenes.read())
            return

        if parsed.path == "/api/health":
            self._send_json(self.health.snapshot(len(self.webrtc.peers)))
            return

        if parsed.path == "/api/monitors":
            self._send_json({"monitors": list_ddc_monitors()})
            return

        if parsed.path == "/api/history":
            self._send_json({"entries": self.history.entries()})
            return

        if parsed.path == "/api/history/diff":
            entry_id = (parse_qs(parsed.query).get("id") or [""])[0]
            try:
                self._send_json({"id": entry_id, "diff": self.history.diff(entry_id)})
            except (OSError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if parsed.path == "/api/templates":
            self._send_json(self.templates.read())
            return

        if parsed.path == "/api/frame.bin":
            try:
                data = self._request_snapshot()
            except Exception as exc:  # pragma: no cover
                self._send_json({"error": str(exc)}, status=500)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path == "/api/thumb.jpg":
            params = parse_qs(parsed.query)
            source_path = (params.get("path") or [""])[0]
            if not source_path:
                self._send_json({"error": "Missing path"}, status=400)
                return
            data = self._read_thumbnail(source_path)
            if not data:
                self._send_json({"error": "Thumbnail unavailable"}, status=404)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "max-age=300")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path == "/api/media":
            params = parse_qs(parsed.query)
            source_path = (params.get("path") or [""])[0]
            if not source_path:
                self._send_json({"error": "Missing path"}, status=400)
                return
            self._serve_media_file(source_path)
            return

        if parsed.path == "/api/live.bin":
            try:
                interval_ms = 120
                if parsed.query:
                    for chunk in parsed.query.split("&"):
                        if chunk.startswith("interval="):
                            interval_ms = int(chunk.split("=", 1)[1])
                            break
                self._stream_live_bin(interval_ms)
            except Exception as exc:  # pragma: no cover
                self._send_json({"error": str(exc)}, status=500)
            return

        self._send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        config_paths = {"/api/config", "/api/raw_config"}
        rtc_paths = {"/api/webrtc-offer", "/api/webrtc-close", "/api/webrtc-keepalive"}
        scene_paths = {
            "/api/scenes/save", "/api/scenes/apply", "/api/scenes/delete",
            "/api/schedules/save", "/api/schedules/delete",
        }
        template_paths = {"/api/templates/save", "/api/templates/delete"}
        control_paths = {"/api/panes/restart", "/api/monitors/set", "/api/history/rollback"}
        if self.path not in config_paths:
            if self.path not in rtc_paths | scene_paths | template_paths | control_paths:
                self._send_json({"error": "Not found"}, status=404)
                return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
            if self.path == "/api/webrtc-offer":
                answer = self.webrtc.create_answer(
                    str(payload["sdp"]), str(payload["type"]),
                    str(payload.get("preview_profile") or "balanced"),
                )
                self._send_json(answer)
                return
            if self.path == "/api/webrtc-close":
                self.webrtc.close_peer(str(payload.get("peer_id", "")))
                self._send_json({"ok": True})
                return
            if self.path == "/api/webrtc-keepalive":
                alive = self.webrtc.keep_peer_alive(str(payload.get("peer_id", "")))
                self._send_json({"ok": alive}, status=200 if alive else 404)
                return
            if self.path == "/api/panes/restart":
                pane_index = int(payload.get("pane", -1))
                state = self._read_state()
                pane_count = int(state.get("pane_count", 0))
                if pane_index < 0 or pane_index >= pane_count:
                    raise ValueError("Pane index is out of range")
                write_text_atomic(self.app_config.control_request_path, f"restart-pane {pane_index}\n")
                self._send_json({"ok": True, "pane": pane_index, "queued": True})
                return
            if self.path == "/api/monitors/set":
                connector = str(payload.get("connector") or "")
                control = str(payload.get("control") or "")
                value = int(payload.get("value", -1))
                monitor = next(
                    (item for item in list_ddc_monitors() if item["connector"] == connector),
                    None,
                )
                if not monitor or not monitor["available"]:
                    raise ValueError("Selected display has no writable DDC/CI bus")
                write_ddc_control(str(monitor["bus"]), control, value)
                self._send_json({
                    "ok": True,
                    "connector": connector,
                    "control": control,
                    "value": value,
                })
                return
            if self.path == "/api/history/rollback":
                entry_id = str(payload.get("id") or "")
                changed = self.history.rollback(entry_id)
                self._send_json({
                    "ok": True,
                    "changed": changed,
                    "entries": self.history.entries(),
                    "config_path": str(self.app_config.config_path),
                    "state": self._read_state(),
                    "raw_config": self._read_raw_config(),
                })
                return
            if self.path == "/api/templates/save":
                template = self.templates.save(
                    str(payload.get("name") or ""),
                    payload.get("pane"),
                    str(payload.get("id") or ""),
                )
                self._send_json({"ok": True, "template": template, **self.templates.read()})
                return
            if self.path == "/api/templates/delete":
                deleted = self.templates.delete(str(payload.get("id") or ""))
                self._send_json({"ok": deleted, **self.templates.read()}, status=200 if deleted else 404)
                return
            if self.path == "/api/scenes/save":
                scene = self.scenes.save_scene(
                    str(payload.get("name") or ""),
                    payload["state"],
                    str(payload.get("id") or ""),
                )
                self._send_json({"ok": True, "scene": scene, **self.scenes.read()})
                return
            if self.path == "/api/scenes/apply":
                scene = self.scenes.apply_scene(str(payload.get("id") or ""))
                self._send_json({
                    "ok": True,
                    "scene": scene,
                    "config_path": str(self.app_config.config_path),
                    "state": self._read_state(),
                    "raw_config": self._read_raw_config(),
                })
                return
            if self.path == "/api/scenes/delete":
                deleted = self.scenes.delete_scene(str(payload.get("id") or ""))
                self._send_json({"ok": deleted, **self.scenes.read()}, status=200 if deleted else 404)
                return
            if self.path == "/api/schedules/save":
                schedule = self.scenes.save_schedule(
                    str(payload.get("scene_id") or ""),
                    str(payload.get("time") or ""),
                    list(payload.get("days") or []),
                    bool(payload.get("enabled", True)),
                    str(payload.get("id") or ""),
                )
                self._send_json({"ok": True, "schedule": schedule, **self.scenes.read()})
                return
            if self.path == "/api/schedules/delete":
                deleted = self.scenes.delete_schedule(str(payload.get("id") or ""))
                self._send_json({"ok": deleted, **self.scenes.read()}, status=200 if deleted else 404)
                return
            config_path = self.app_config.config_path
            if self.path == "/api/config":
                state = payload["state"]
                text = serialize_config(state)
            else:
                text = str(payload["raw_config"])
            self.history.write(text, "raw" if self.path == "/api/raw_config" else "editor")
        except Exception as exc:  # pragma: no cover
            self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json({
            "ok": True,
            "config_path": str(self.app_config.config_path),
            "state": self._read_state(),
            "raw_config": self._read_raw_config(),
        })


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Web UI for KMS Mosaic")
    parser.add_argument("--config", default=default_config_path(), help="Config file to edit")
    parser.add_argument("--scenes", help="Named-scene JSON file (defaults beside the config file)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8787, help="Bind port")
    parser.add_argument("--dump-state", action="store_true", help="Print parsed config state as JSON and exit")
    parser.add_argument("--dump-connectors", action="store_true", help="Print connected display outputs as JSON and exit")
    parser.add_argument("--print-html", action="store_true", help="Print the standalone HTML shell and exit")
    parser.add_argument("--write-state-json", help="Read a JSON file containing {state: ...}, write config, print updated JSON")
    parser.add_argument("--write-raw-json", help="Read a JSON file containing {raw_config: ...}, write config, print updated JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable HTTP access logs and startup banner on stdout (quiet by default)")
    return parser.parse_args()


def parse_args() -> WebConfig:
    args = parse_cli_args()
    return WebConfig(
        config_path=Path(args.config),
        host=args.host,
        port=args.port,
        snapshot_request_path=Path("/tmp/kms_mosaic_snapshot.request"),
        preview_lease_path=Path("/tmp/kms_mosaic_preview.active"),
        snapshot_output_path=Path("/tmp/kms_mosaic_preview.rgba"),
        thumb_cache_dir=Path("/tmp/kms_mosaic_web_thumbs"),
        scenes_path=Path(args.scenes) if args.scenes else Path(args.config).with_suffix(".scenes.json"),
    )


def main() -> int:
    cli = parse_cli_args()
    config_path = Path(cli.config)
    if cli.print_html:
        print(HTML)
        return 0
    if cli.dump_state:
        print(json.dumps({
            "config_path": str(config_path),
            "state": read_state_from_config(config_path),
            "raw_config": read_raw_config_text(config_path),
        }))
        return 0
    if cli.dump_connectors:
        print(json.dumps({"connectors": list_connectors()}))
        return 0
    if cli.write_state_json:
        payload = json.loads(Path(cli.write_state_json).read_text(encoding="utf-8"))
        state = payload["state"]
        write_text_atomic(config_path, serialize_config(state))
        print(json.dumps({
            "ok": True,
            "config_path": str(config_path),
            "state": read_state_from_config(config_path),
            "raw_config": read_raw_config_text(config_path),
        }))
        return 0
    if cli.write_raw_json:
        payload = json.loads(Path(cli.write_raw_json).read_text(encoding="utf-8"))
        write_text_atomic(config_path, str(payload["raw_config"]))
        print(json.dumps({
            "ok": True,
            "config_path": str(config_path),
            "state": read_state_from_config(config_path),
            "raw_config": read_raw_config_text(config_path),
        }))
        return 0

    app_config = WebConfig(
        config_path=config_path,
        host=cli.host,
        port=cli.port,
        snapshot_request_path=Path("/tmp/kms_mosaic_snapshot.request"),
        preview_lease_path=Path("/tmp/kms_mosaic_preview.active"),
        snapshot_output_path=Path("/tmp/kms_mosaic_preview.rgba"),
        thumb_cache_dir=Path("/tmp/kms_mosaic_web_thumbs"),
        scenes_path=Path(cli.scenes) if cli.scenes else config_path.with_suffix(".scenes.json"),
        verbose=bool(getattr(cli, "verbose", False)),
    )
    server = ReusableThreadingHTTPServer((app_config.host, app_config.port), Handler)
    server.app_config = app_config  # type: ignore[attr-defined]
    server.webrtc = WebRTCBridge(app_config)  # type: ignore[attr-defined]
    server.history = ConfigHistory(app_config.config_path)  # type: ignore[attr-defined]
    server.templates = PaneTemplateManager(app_config)  # type: ignore[attr-defined]
    server.scenes = SceneManager(app_config, server.history)  # type: ignore[attr-defined]
    server.health = HealthMonitor()  # type: ignore[attr-defined]
    server.webrtc.start()  # type: ignore[attr-defined]
    server.scenes.start()  # type: ignore[attr-defined]
    if app_config.verbose:
        print(f"KMS Mosaic web UI serving {app_config.config_path} on http://{app_config.host}:{app_config.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.scenes.close()  # type: ignore[attr-defined]
        server.webrtc.close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    raise SystemExit(main())
