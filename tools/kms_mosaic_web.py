#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
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
from urllib.parse import parse_qs, urlparse
from typing import Any

try:
    import av
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from aiortc.rtcrtpsender import RTCRtpSender
except ImportError:  # pragma: no cover
    av = None
    RTCPeerConnection = None
    RTCSessionDescription = None
    VideoStreamTrack = object
    RTCRtpSender = None


LAYOUT_NAMES = ["stack", "row", "2x1", "1x2", "2over1", "1over2", "overlay"]
DEFAULT_PANE_COMMANDS = [
    "btop --utf-force",
    "tail -F /var/log/syslog -n 500",
]


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
        "visibility_mode": "neither",
        "pane_types": ["terminal", "terminal"],
        "pane_commands": DEFAULT_PANE_COMMANDS.copy(),
        "pane_playlists": ["", ""],
        "pane_playlist_extended": ["", ""],
        "pane_playlist_fifos": ["", ""],
        "pane_mpv_outs": ["", ""],
        "pane_video_rotate": ["", ""],
        "pane_panscan": ["", ""],
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
        "extra_lines": "",
    }


def visibility_mode_from_flags(flags: dict[str, Any]) -> str:
    if bool(flags.get("no_video")):
        return "no-video"
    if bool(flags.get("no_panes")):
        return "no-terminal"
    return "neither"


def ensure_panes(state: dict[str, Any]) -> None:
    pane_count = max(1, int(state.get("pane_count", 2)))
    state["pane_count"] = pane_count
    pane_commands = list(state.get("pane_commands", []))
    pane_types = list(state.get("pane_types", []))
    pane_playlists = list(state.get("pane_playlists", []))
    pane_playlist_extended = list(state.get("pane_playlist_extended", []))
    pane_playlist_fifos = list(state.get("pane_playlist_fifos", []))
    pane_mpv_outs = list(state.get("pane_mpv_outs", []))
    pane_video_rotate = list(state.get("pane_video_rotate", []))
    pane_panscan = list(state.get("pane_panscan", []))
    pane_video_paths = [list(paths) for paths in state.get("pane_video_paths", [])]
    pane_mpv_opts = [list(opts) for opts in state.get("pane_mpv_opts", [])]
    while len(pane_commands) < pane_count:
        pane_commands.append(DEFAULT_PANE_COMMANDS[0] if len(pane_commands) == 0 else "")
    while len(pane_types) < pane_count:
        pane_types.append("terminal")
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
    while len(pane_video_paths) < pane_count:
        pane_video_paths.append([])
    while len(pane_mpv_opts) < pane_count:
        pane_mpv_opts.append([])
    state["pane_commands"] = pane_commands[:pane_count]
    state["pane_types"] = pane_types[:pane_count]
    state["pane_playlists"] = pane_playlists[:pane_count]
    state["pane_playlist_extended"] = pane_playlist_extended[:pane_count]
    state["pane_playlist_fifos"] = pane_playlist_fifos[:pane_count]
    state["pane_mpv_outs"] = pane_mpv_outs[:pane_count]
    state["pane_video_rotate"] = pane_video_rotate[:pane_count]
    state["pane_panscan"] = pane_panscan[:pane_count]
    state["pane_video_paths"] = pane_video_paths[:pane_count]
    state["pane_mpv_opts"] = pane_mpv_opts[:pane_count]


def parse_config_text(text: str) -> dict[str, Any]:
    state = empty_state()
    extra_lines: list[str] = []

    lines = text.splitlines()
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            if stripped:
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
                state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
                ensure_panes(state)
                state["pane_types"][pane_index] = "mpv"
                i += 2
            elif tok == "--pane-playlist" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
                ensure_panes(state)
                state["pane_types"][pane_index] = "mpv"
                state["pane_playlists"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-playlist-extended" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
                ensure_panes(state)
                state["pane_types"][pane_index] = "mpv"
                state["pane_playlist_extended"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-playlist-fifo" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
                ensure_panes(state)
                state["pane_types"][pane_index] = "mpv"
                state["pane_playlist_fifos"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-mpv-out" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
                ensure_panes(state)
                state["pane_types"][pane_index] = "mpv"
                state["pane_mpv_outs"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-video-rotate" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
                ensure_panes(state)
                state["pane_types"][pane_index] = "mpv"
                state["pane_video_rotate"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-panscan" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
                ensure_panes(state)
                state["pane_types"][pane_index] = "mpv"
                state["pane_panscan"][pane_index] = tokens[i + 2]
                i += 3
            elif tok == "--pane-video" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
                ensure_panes(state)
                state["pane_types"][pane_index] = "mpv"
                state["pane_video_paths"][pane_index].append(tokens[i + 2])
                i += 3
            elif tok == "--pane-mpv-opt" and i + 2 < len(tokens):
                pane_index = max(0, int(tokens[i + 1]) - 1)
                state["pane_count"] = max(int(state["pane_count"]), pane_index + 1)
                ensure_panes(state)
                state["pane_types"][pane_index] = "mpv"
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

    ensure_panes(state)
    state["extra_lines"] = "\n".join(extra_lines).strip()
    state["visibility_mode"] = visibility_mode_from_flags(state.get("flags", {}))
    return state


def serialize_config(state: dict[str, Any]) -> str:
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
    roles = str(state.get("roles", "")).strip()
    if roles:
        add_opt("--roles", roles)
    add_opt("--fs-cycle-sec", state.get("fs_cycle_sec", 5))

    pane_count = int(state.get("pane_count", 2))
    if pane_count != 2:
        add_opt("--pane-count", pane_count)

    pane_commands = list(state.get("pane_commands", []))
    pane_types = list(state.get("pane_types", []))
    pane_playlists = list(state.get("pane_playlists", []))
    pane_playlist_extended = list(state.get("pane_playlist_extended", []))
    pane_playlist_fifos = list(state.get("pane_playlist_fifos", []))
    pane_mpv_outs = list(state.get("pane_mpv_outs", []))
    pane_video_rotate = list(state.get("pane_video_rotate", []))
    pane_panscan = list(state.get("pane_panscan", []))
    pane_video_paths = [list(paths) for paths in state.get("pane_video_paths", [])]
    pane_mpv_opts = [list(opts) for opts in state.get("pane_mpv_opts", [])]
    for idx, cmd in enumerate(pane_commands):
        pane_type = pane_types[idx] if idx < len(pane_types) else "terminal"
        if pane_type == "mpv":
            lines.append(f"--pane-media {idx + 1}")
            playlist = pane_playlists[idx] if idx < len(pane_playlists) else ""
            playlist_ext = pane_playlist_extended[idx] if idx < len(pane_playlist_extended) else ""
            playlist_fifo = pane_playlist_fifos[idx] if idx < len(pane_playlist_fifos) else ""
            pane_mpv_out = pane_mpv_outs[idx] if idx < len(pane_mpv_outs) else ""
            pane_rotate = pane_video_rotate[idx] if idx < len(pane_video_rotate) else ""
            pane_panscan_value = pane_panscan[idx] if idx < len(pane_panscan) else ""
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
    add_flag("--no-video", bool(flags.get("no_video")))
    add_flag("--no-panes", bool(flags.get("no_panes")))
    add_flag("--smooth", bool(flags.get("smooth")))
    add_flag("--loop-file", bool(flags.get("loop_file")))
    add_flag("--loop-playlist", bool(flags.get("loop_playlist")))
    add_flag("--shuffle", bool(flags.get("shuffle")))
    add_flag("--no-osd", bool(flags.get("no_osd")))
    if flags.get("atomic_nonblock"):
        add_flag("--atomic-nonblock", True)
    else:
        add_flag("--atomic", bool(flags.get("atomic")))
    add_flag("--gl-finish", bool(flags.get("gl_finish")))

    for opt in state.get("mpv_opts", []):
        if str(opt).strip():
            add_opt("--mpv-opt", opt)
    add_opt("--playlist", state.get("playlist", ""))
    add_opt("--playlist-extended", state.get("playlist_extended", ""))
    add_opt("--playlist-fifo", state.get("playlist_fifo", ""))
    add_opt("--mpv-out", state.get("mpv_out", ""))
    add_opt("--video-rotate", state.get("video_rotate", ""))
    add_opt("--panscan", state.get("panscan", ""))
    for video_path in state.get("video_paths", []):
        if str(video_path).strip():
            add_opt("--video", video_path)

    extra_lines = str(state.get("extra_lines", "")).strip()
    if extra_lines:
        lines.append("")
        lines.extend(extra_lines.splitlines())

    return "\n".join(lines).strip() + "\n"


@dataclass
class WebConfig:
    config_path: Path
    host: str
    port: int
    snapshot_request_path: Path
    preview_lease_path: Path
    snapshot_output_path: Path
    thumb_cache_dir: Path


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".kms_mosaic.", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temp_path, path)


def write_preview_lease(app_config: WebConfig, interval_ms: int) -> None:
    interval_ms = max(1, min(int(interval_ms), 1000))
    write_text_atomic(app_config.preview_lease_path, f"{interval_ms}\n{time.time_ns()}\n")


def read_latest_raw_preview_frame(app_config: WebConfig, last_mtime_ns: int = 0, interval_ms: int = 16,
                                  timeout_sec: float = 3.0) -> tuple[bytes, int]:
    output_path = app_config.snapshot_output_path
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        write_preview_lease(app_config, interval_ms)
        if output_path.exists():
            st = output_path.stat()
            if st.st_size >= 8 and (last_mtime_ns <= 0 or st.st_mtime_ns > last_mtime_ns):
                return output_path.read_bytes(), st.st_mtime_ns
        time.sleep(0.01)
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


def boost_h264_bitrate_sdp(sdp: str, start_kbps: int = 50000, max_kbps: int = 100000, min_kbps: int = 20000) -> str:
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
    if mime == "video/vp9":
        return (0, mime)
    if mime == "video/h264":
        return (1, mime)
    if mime == "video/vp8":
        return (2, mime)
    if mime == "video/av1":
        return (3, mime)
    return (4, mime)


class RawPreviewVideoTrack(VideoStreamTrack):
    def __init__(self, app_config: WebConfig) -> None:
        super().__init__()
        self.app_config = app_config
        self.interval_ms = 1
        self.last_mtime_ns = 0
        self.last_frame: av.VideoFrame | None = None
        self.timestamp = 0
        self.time_base = Fraction(1, 90000)
        self.last_timestamp_time = time.monotonic()

    async def recv(self) -> av.VideoFrame:
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
            frame.planes[0].update(rgba)
            self.last_frame = frame.reformat(format="yuvj420p")
        except Exception:
            if self.last_frame is None:
                fallback = av.VideoFrame(16, 9, "rgba")
                fallback.planes[0].update(bytes(16 * 9 * 4))
                self.last_frame = fallback.reformat(format="yuvj420p")
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
        if pc.connectionState != "closed":
            await pc.close()

    async def _create_answer(self, offer_sdp: str, offer_type: str) -> dict[str, str]:
        if not self.available:
            raise RuntimeError("WebRTC preview dependencies are not installed")
        pc = RTCPeerConnection()
        self.peers.add(pc)

        @pc.on("connectionstatechange")
        async def _on_connectionstatechange() -> None:
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                await self._close_peer(pc)

        transceiver = pc.addTransceiver("video", direction="sendonly")
        if RTCRtpSender is not None:
            capabilities = RTCRtpSender.getCapabilities("video")
            codecs = list(capabilities.codecs) if capabilities else []
            if codecs:
                preferred = sorted(codecs, key=codec_preference_key)
                if preferred:
                    transceiver.setCodecPreferences(preferred)
        pc.addTrack(RawPreviewVideoTrack(self.app_config))
        sender = transceiver.sender
        if sender is not None and hasattr(sender, "getParameters") and hasattr(sender, "setParameters"):
            params = sender.getParameters()
            if params.encodings:
                for encoding in params.encodings:
                    encoding.maxBitrate = 50_000_000
            try:
                await sender.setParameters(params)
            except Exception:
                pass
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await self._wait_for_ice_complete(pc)
        assert pc.localDescription is not None
        return {
            "sdp": boost_h264_bitrate_sdp(pc.localDescription.sdp),
            "type": pc.localDescription.type,
        }

    def create_answer(self, offer_sdp: str, offer_type: str) -> dict[str, str]:
        if not self.available or self.loop is None:
            raise RuntimeError("WebRTC preview bridge is unavailable")
        future = asyncio.run_coroutine_threadsafe(self._create_answer(offer_sdp, offer_type), self.loop)
        return future.result(timeout=15.0)

    async def _shutdown(self) -> None:
        peers = list(self.peers)
        for pc in peers:
            await self._close_peer(pc)
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
      overflow: hidden;
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
      overflow: hidden;
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
      overflow: hidden;
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
      overflow: hidden;
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
    .studio-size-group {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
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
    .studio-tag {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 8px;
      border-radius: 5px;
      background: rgba(255,255,255,0.08);
      backdrop-filter: blur(6px);
    }
    .studio-card-body {
      display: grid;
      gap: 5px;
      min-width: 0;
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
    }
    .studio-resize-handle::before,
    .studio-resize-handle::after {
      content: "";
      position: absolute;
      border-radius: 999px;
      background: rgba(255,240,200,0.84);
      box-shadow: 0 0 0 1px rgba(26,15,10,0.22);
    }
    .studio-resize-handle[data-axis="w"] {
      top: 10px;
      bottom: 10px;
      width: 18px;
      cursor: ew-resize;
    }
    .studio-resize-handle[data-axis="w"]::before {
      top: 16px;
      bottom: 16px;
      left: 8px;
      width: 2px;
    }
    .studio-resize-handle[data-axis="w"][data-edge="left"] { left: -9px; }
    .studio-resize-handle[data-axis="w"][data-edge="right"] { right: -9px; }
    .studio-resize-handle[data-axis="h"] {
      left: 10px;
      right: 10px;
      height: 18px;
      cursor: ns-resize;
    }
    .studio-resize-handle[data-axis="h"]::before {
      left: 16px;
      right: 16px;
      top: 8px;
      height: 2px;
    }
    .studio-resize-handle[data-axis="h"][data-edge="top"] { top: -9px; }
    .studio-resize-handle[data-axis="h"][data-edge="bottom"] { bottom: -9px; }
    .studio-resize-handle[data-mode="corner"] {
      width: 20px;
      height: 20px;
      cursor: nwse-resize;
    }
    .studio-resize-handle[data-mode="corner"]::before {
      inset: 4px;
      border: 2px solid rgba(255,240,200,0.84);
      background: rgba(255,240,200,0.16);
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
      grid-template-columns: minmax(0, 138px) minmax(0, 1fr);
      gap: 8px 10px;
      align-items: start;
    }
    .playlist-item.portrait-thumb .playlist-row {
      grid-template-columns: minmax(0, 124px) minmax(0, 1fr);
    }
    .playlist-media-cell {
      width: auto;
      display: flex;
      align-self: start;
      min-width: 0;
    }
    .playlist-duration {
      position: absolute;
      bottom: 5px;
      right: 6px;
      padding: 1px 5px 2px;
      border-radius: 4px;
      background: rgba(0,0,0,0.60);
      color: rgba(255,255,255,0.90);
      font-family: "Menlo", "Consolas", monospace;
      font-size: 9px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      z-index: 2;
      pointer-events: none;
      backdrop-filter: blur(4px);
    }
    .playlist-duration:empty { display: none; }
    .playlist-thumb {
      height: 72px;
      width: 100%;
      min-height: 72px;
      max-height: 72px;
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
      width: 92px;
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
    .playlist-path { width: 100%; margin-top: 1px; }
    .playlist-controls {
      display: grid;
      gap: 6px;
      min-width: 0;
      align-content: start;
    }
    .playlist-controls-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .playlist-controls-row .playlist-mini-btn { margin-top: 0; }
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
    .playlist-row input { min-width: 0; }
    .playlist-repeat {
      text-align: center;
      font-family: "Menlo", "Consolas", monospace;
      height: 30px;
      padding: 6px 8px;
    }
    .playlist-repeat-wrap {
      display: grid;
      gap: 3px;
      align-items: center;
      justify-items: center;
      color: var(--muted);
      font-size: 9px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      font-family: "Menlo", "Consolas", monospace;
      width: 72px;
    }
    .playlist-item.portrait-thumb .playlist-controls-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      align-items: stretch;
    }
    .playlist-item.portrait-thumb .playlist-repeat-wrap {
      width: 100%;
      justify-items: stretch;
      grid-column: 1 / -1;
    }
    .playlist-item.portrait-thumb .playlist-repeat {
      width: 100%;
    }
    .playlist-repeat-wrap span { display: block; line-height: 9px; }
    .playlist-mini-btn {
      padding: 5px 8px;
      border-radius: 6px;
      background: var(--surface-high);
      border: 1px solid var(--line);
      color: var(--ink);
      height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-top: 10px;
    }
    .playlist-controls-row {
      align-items: end;
    }
    .playlist-mini-btn.danger { color: var(--danger); }
    .playlist-bulk {
      margin-top: 10px;
      border: 1px solid var(--line);
      border-radius: var(--r-sm);
      background: rgba(255,255,255,0.02);
      overflow: hidden;
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
      overflow: hidden;
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
    }
    @media (max-width: 680px) {
      .shell { margin: 8px auto 16px; }
      .grid, .checks { grid-template-columns: 1fr; }
      .panel-body > div, details.advanced-block { padding: 10px 12px; }
      .queue-editor-head { flex-direction: column; align-items: stretch; }
      .queue-editor-target { white-space: normal; }
      .playlist-row { grid-template-columns: auto 1fr; }
      .playlist-item.portrait-thumb .playlist-row { grid-template-columns: auto 1fr; }
      .playlist-media-cell, .playlist-thumb { width: 100%; }
      .playlist-item.portrait-thumb .playlist-thumb { width: 100%; }
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
        </div>
        <div class="status" id="status"></div>
      </div>
    </section>

    <section class="card">
      <div class="panel-body">
        <div class="panel-wide">
          <h2 class="section-title">Pane Layout</h2>
          <div class="studio-grid">
            <div>
              <div class="actions tight" style="margin-bottom:10px;"></div>
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
              <input id="flagNoVideo" type="hidden" />
              <input id="flagNoPanes" type="hidden" />
              <div class="checks">
                <label class="check" title="Enable the compositor's smooth-presentation preset for gentler frame pacing defaults."><input id="flagSmooth" type="checkbox" /> Smooth Preset</label>
                <label class="check" title="Loop the current file when a pane or queue is pointed at a single item."><input id="flagLoop" type="checkbox" /> Loop File</label>
                <label class="check" title="Loop each pane playlist instead of stopping at the end."><input id="flagLoopPlaylist" type="checkbox" /> Loop Playlist</label>
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

  <script>
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
    let state = null;
    let rawConfigText = "";
    let selectedRole = -1;
    let draggedStudioRole = null;
    let studioResizeDrag = null;
    let pendingNewPaneIndexes = new Set();
    let livePreviewTimer = null;
    let livePreviewController = null;
    let livePreviewUrl = null;
    let playlistDragIndex = null;
    let playlistPreviewObserver = null;
    let previewFrameWidth = 16;
    let previewFrameHeight = 9;
    let webrtcPeer = null;
    let webrtcStream = null;
    let webrtcRetryTimer = null;
    if (layoutSelect) {
      layoutNames.forEach(name => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        layoutSelect.appendChild(option);
      });
    }

    function slotName(index) {
      if (index === 0) return "Video";
      if (index <= 26) return `Pane ${String.fromCharCode(64 + index)}`;
      return `Pane ${index}`;
    }

    function roleName(role) {
      if (role === 0) return "Pane C";
      if (role === 1) return "Pane A";
      if (role === 2) return "Pane B";
      if (role <= 26) return `Pane ${String.fromCharCode(64 + role + 1)}`;
      return `Pane ${role}`;
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
      if (!state?.flags) return;
      const mode = visibilityModeForState(state);
      state.visibility_mode = mode;
      state.flags.no_video = mode === "no-video";
      state.flags.no_panes = mode === "no-terminal";
    }

    function currentVisibilityMode() {
      normalizeVisibilityFlags();
      return visibilityModeForState(state);
    }

    function visibilityModeHidesRole(nextState, role) {
      const mode = visibilityModeForState(nextState);
      const paneType = role === 0 ? "mpv" : (nextState?.pane_types?.[role - 1] || "terminal");
      if (mode === "no-video") return paneType === "mpv";
      if (mode === "no-terminal") return paneType === "terminal";
      return false;
    }

    function visibleStudioRoles(nextState = state) {
      const paneCount = Math.max(1, Number(nextState?.pane_count || 2));
      const roleCount = 1 + paneCount;
      const roles = [];
      for (let role = 0; role < roleCount; role += 1) {
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
      const roleCount = 1 + Math.max(1, Number(nextState?.pane_count || 2));
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
      const groups = { audioMode: "", muteMode: "", loopFile: "", videoMode: "", shaders: [], other: [] };
      (Array.isArray(opts) ? opts : []).forEach((opt) => {
        const value = String(opt || "").trim();
        if (!value) return;
        if (value === "no-audio" || value === "audio=no" || value === "ao=null" || value === "mpv-out=no-audio") {
          groups.audioMode = "no-audio";
          return;
        }
        if (value === "mute=yes" || value === "mute=no") {
          groups.muteMode = value.slice("mute=".length);
          return;
        }
        if (value === "loop-file=no" || value === "loop-file=yes" || value === "loop-file=inf") {
          groups.loopFile = value.slice("loop-file=".length);
          return;
        }
        if (value === "vid=no") {
          groups.videoMode = "audio-only";
          return;
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
      if (parts.audioMode === "no-audio") {
        opts.push("audio=no");
      }
      if (parts.muteMode === "yes" || parts.muteMode === "no") {
        opts.push(`mute=${parts.muteMode}`);
      }
      if (parts.loopFile === "no" || parts.loopFile === "yes" || parts.loopFile === "inf") {
        opts.push(`loop-file=${parts.loopFile}`);
      }
      if (parts.videoMode === "audio-only") {
        opts.push("vid=no");
      }
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

    function buildMpvOptsFromControls() {
      const audioEl = document.getElementById("mpvAudioMode");
      const shadersEl = document.getElementById("mpvShaders");
      return buildMpvOptsFromParts({
        audioMode: audioEl ? audioEl.value : "",
        shadersText: shadersEl ? shadersEl.value : "",
        otherText: "",
      });
    }

    function syncMainInspectorMpvOpts() {
      const audioEl = document.getElementById("inspectorMainAudioMode");
      const muteEl = document.getElementById("inspectorMainMuteMode");
      const loopEl = document.getElementById("inspectorMainLoopFile");
      const shadersEl = document.getElementById("inspectorMainShaders");
      const otherEl = document.getElementById("inspectorMainMpvOpts");
      if (!audioEl || !muteEl || !loopEl || !shadersEl || !otherEl) return;
      state.mpv_opts = buildMpvOptsFromParts({
        audioMode: audioEl.value,
        muteMode: muteEl.value,
        loopFile: loopEl.value,
        shadersText: shadersEl.value,
        otherText: otherEl.value,
      });
      const mpvAudioEl = document.getElementById("mpvAudioMode");
      const mpvShadersEl = document.getElementById("mpvShaders");
      if (mpvAudioEl) mpvAudioEl.value = audioEl.value;
      if (mpvShadersEl) mpvShadersEl.value = shadersEl.value;
    }

    function syncInspectorPaneMpvOpts(paneIndex) {
      if (!state || paneIndex < 0) return;
      const audioEl = document.getElementById("inspectorPaneAudioMode");
      const muteEl = document.getElementById("inspectorPaneMuteMode");
      const loopEl = document.getElementById("inspectorPaneLoopFile");
      const shadersEl = document.getElementById("inspectorPaneShaders");
      const otherEl = document.getElementById("inspectorPaneMpvOpts");
      if (!audioEl || !muteEl || !loopEl || !shadersEl || !otherEl) return;
      state.pane_mpv_opts[paneIndex] = buildMpvOptsFromParts({
        audioMode: audioEl.value,
        muteMode: muteEl.value,
        loopFile: loopEl.value,
        shadersText: shadersEl.value,
        otherText: otherEl.value,
      });
    }

    function roleType(role) {
      if (role === 0) return "video";
      return state?.pane_types?.[role - 1] === "mpv" ? "video" : "terminal";
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
      if (selectedRole === 0) {
        const noteParts = [`This queue controls ${roleTitle(0)}.`];
        const mainMpvOpts = Array.isArray(state.mpv_opts) ? state.mpv_opts.slice() : [];
        if (mainMpvOpts.length) noteParts.push(`${mainMpvOpts.length} global mpv option${mainMpvOpts.length === 1 ? "" : "s"}.`);
        return {
          emptyMessage: "No videos queued yet. Add one below or open Bulk Add Videos.",
          paths: Array.isArray(state.video_paths) ? state.video_paths.slice() : [],
          editable: true,
          paneType,
          role: selectedRole,
          note: noteParts.join(" "),
          apply(paths) {
            state.video_paths = paths.slice();
            const queueField = selectedPaneQueueField();
            if (queueField) queueField.value = state.video_paths.join("\n");
          }
        };
      }
      const paneIndex = selectedRole - 1;
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

    function mediaUrl(path) {
      const value = String(path || "").trim();
      if (!value) return "";
      return `/api/media?path=${encodeURIComponent(value)}`;
    }

    function playlistThumbCacheKey(path, metrics) {
      return [
        "kmsmosaic-thumb-v1",
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
        if (!parsed || typeof parsed.src !== "string" || !parsed.src) return null;
        return parsed;
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
      return /\.(avif|bmp|gif|jpe?g|png|webp)$/i.test(String(path || ""));
    }

    function isLikelyVideoPath(path) {
      return /\.(m4v|mkv|mov|mp4|mpeg|mpg|ts|webm)$/i.test(String(path || ""));
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
      const paneIndex = role - 1;
      const panscanValue = role === 0
        ? String(state?.panscan || "").trim()
        : String(state?.pane_panscan?.[paneIndex] || "").trim();
      const panscan = Number.parseFloat(panscanValue || "0");
      return {
        rotation,
        aspectRatio: `${physW} / ${physH}`,
        cover: Number.isFinite(panscan) && panscan > 0,
        isPortrait: physH > physW,
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
        return `<div class="playlist-thumb-media${quarterTurn ? " quarter-turn" : ""}"><video data-preview-video="${index}" data-preview-path="${value.replace(/"/g, "&quot;")}" data-preview-src="${src}#t=5" muted playsinline preload="metadata"${mediaStyle}></video></div>`;
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
      nextState.pane_playlists = Array.isArray(nextState.pane_playlists) ? nextState.pane_playlists.slice(0, count) : [];
      nextState.pane_playlist_extended = Array.isArray(nextState.pane_playlist_extended) ? nextState.pane_playlist_extended.slice(0, count) : [];
      nextState.pane_playlist_fifos = Array.isArray(nextState.pane_playlist_fifos) ? nextState.pane_playlist_fifos.slice(0, count) : [];
      nextState.pane_video_paths = Array.isArray(nextState.pane_video_paths)
        ? nextState.pane_video_paths.slice(0, count).map(paths => Array.isArray(paths) ? paths.slice() : [])
        : [];
      nextState.pane_mpv_opts = Array.isArray(nextState.pane_mpv_opts)
        ? nextState.pane_mpv_opts.slice(0, count).map(opts => Array.isArray(opts) ? opts.slice() : [])
        : [];
      while (nextState.pane_commands.length < count) nextState.pane_commands.push("");
      while (nextState.pane_types.length < count) nextState.pane_types.push("terminal");
      while (nextState.pane_playlists.length < count) nextState.pane_playlists.push("");
      while (nextState.pane_playlist_extended.length < count) nextState.pane_playlist_extended.push("");
      while (nextState.pane_playlist_fifos.length < count) nextState.pane_playlist_fifos.push("");
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
      const roles = Array.from({ length: paneCount + 1 }, (_, i) => i);
      const paneRoles = roles.slice(1);
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
          first: { leaf: true, role: 0 },
          second: balancedTreeForRoles(paneRoles, false)
        };
      }
      if (layout === "2x1") {
        return { leaf: false, kind: "col", pct: colPct, first: balancedTreeForRoles(paneRoles, true), second: { leaf: true, role: 0 } };
      }
      if (layout === "1x2") {
        return { leaf: false, kind: "col", pct: colPct, first: { leaf: true, role: 0 }, second: balancedTreeForRoles(paneRoles, true) };
      }
      if (layout === "2over1") {
        return { leaf: false, kind: "row", pct: 100 - rowPct, first: balancedTreeForRoles(paneRoles, false), second: { leaf: true, role: 0 } };
      }
      if (layout === "1over2") {
        return { leaf: false, kind: "row", pct: rowPct, first: { leaf: true, role: 0 }, second: balancedTreeForRoles(paneRoles, false) };
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

    function decorateSplitTreeAncestor(entry, areaMap) {
      if (!entry) return null;
      const area = areaMap?.[entry.path];
      const branchArea = areaMap?.[entry.childPath];
      if (!area || !branchArea) return null;
      const displayArea = transformStudioPaneRect(area);
      const displayBranch = transformStudioPaneRect(branchArea);
      const total = normalizedRotationDegrees();
      const logicalEdge = entry.node.kind === "col"
        ? ((branchArea.x + (branchArea.w / 2)) < (area.x + (area.w / 2)) ? "left" : "right")
        : ((branchArea.y + (branchArea.h / 2)) < (area.y + (area.h / 2)) ? "top" : "bottom");
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

    function paneIdentityForRole(nextState, role) {
      if (!nextState || role < 0) return null;
      if (role === 0) {
        return {
          kind: "main",
          paneType: "mpv",
        };
      }
      const paneIndex = role - 1;
      const paneType = nextState.pane_types?.[paneIndex] || "terminal";
      return {
        kind: "pane",
        paneType,
        command: String(nextState.pane_commands?.[paneIndex] || ""),
        playlist: String(nextState.pane_playlists?.[paneIndex] || ""),
        playlistExtended: String(nextState.pane_playlist_extended?.[paneIndex] || ""),
        playlistFifo: String(nextState.pane_playlist_fifos?.[paneIndex] || ""),
        mpvOut: String(nextState.pane_mpv_outs?.[paneIndex] || ""),
        videoRotate: String(nextState.pane_video_rotate?.[paneIndex] || ""),
        panscan: String(nextState.pane_panscan?.[paneIndex] || ""),
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
      for (let role = 1; role <= Number(nextState.pane_count || 0); role += 1) {
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
      if (narrowedRoles.length > 1 && snapshot.role > 0 && narrowedRoles.includes(snapshot.role)) {
        narrowedRoles = [snapshot.role];
      }
      return narrowedRoles.length === 1 ? narrowedRoles[0] : -1;
    }

    function ensureSelectedRole() {
      const maxRole = Math.max(0, Number(state?.pane_count || 0));
      if (!Number.isFinite(selectedRole)) selectedRole = -1;
      if (selectedRole < -1) selectedRole = -1;
      if (selectedRole > maxRole) selectedRole = -1;
    }

    function selectedPaneType() {
      if (!state || selectedRole < 0) return "none";
      if (selectedRole === 0) return "mpv";
      return state.pane_types?.[selectedRole - 1] || "terminal";
    }

    function selectRole(role) {
      if (!Number.isFinite(role)) {
        selectedRole = -1;
      } else {
        selectedRole = Number(role);
      }
      ensureSelectedRole();
    }

    function parseRolesString(nextState) {
      const roleCount = 1 + Math.max(1, Number(nextState.pane_count || 2));
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
      return inversePointByDegrees(point.x, 100 - point.y, normalizedRotationDegrees());
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

    function studioResizeCursor(mode, corner = "") {
      if (mode === "corner") {
        return corner === "top-right" || corner === "bottom-left" ? "nesw-resize" : "nwse-resize";
      }
      return mode === "h" ? "ns-resize" : "ew-resize";
    }

    function studioResizeCornerName(ctx) {
      if (!ctx?.colAncestor || !ctx?.rowAncestor) return null;
      return `${ctx.rowAncestor.edge}-${ctx.colAncestor.edge}`;
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
      if (studioResizeDrag.mode === "w" || studioResizeDrag.mode === "corner") {
        const desiredWidth = desiredStudioSizeFromPointer(ctx.rect, "w", ctx.colAncestor?.logicalEdge, logicalPointer);
        if (desiredWidth != null && resizePaneAxis(studioResizeDrag.role, "w", desiredWidth).ok) changed = true;
      }
      if (studioResizeDrag.mode === "h" || studioResizeDrag.mode === "corner") {
        const latestCtx = splitTreeResizeContext(state, studioResizeDrag.role) || ctx;
        const desiredHeight = desiredStudioSizeFromPointer(latestCtx.rect, "h", latestCtx.rowAncestor?.logicalEdge, logicalPointer);
        if (desiredHeight != null && resizePaneAxis(studioResizeDrag.role, "h", desiredHeight).ok) changed = true;
      }
      if (changed) renderStudioBoard();
    }

    function stopStudioResizeDrag() {
      if (!studioResizeDrag) return;
      studioResizeDrag = null;
      studioBoard?.classList.remove("resizing");
      if (studioBoard) studioBoard.style.cursor = "";
      window.removeEventListener("pointermove", applyStudioResizeDrag);
      window.removeEventListener("pointerup", stopStudioResizeDrag);
      window.removeEventListener("pointercancel", stopStudioResizeDrag);
    }

    function startStudioResizeDrag(event, role, mode) {
      if (!state) return;
      const ctx = splitTreeResizeContext(state, role);
      if (!ctx) return;
      if ((mode === "w" && !ctx.colAncestor) || (mode === "h" && !ctx.rowAncestor) || (mode === "corner" && !studioResizeCornerName(ctx))) {
        return;
      }
      selectRole(role);
      studioResizeDrag = { role, mode };
      const corner = studioResizeCornerName(ctx) || "";
      studioBoard?.classList.add("resizing");
      if (studioBoard) studioBoard.style.cursor = studioResizeCursor(mode, corner);
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
        const widthValue = effectiveStudioSizeValue(rect.w);
        const heightValue = effectiveStudioSizeValue(rect.h);
        const widthActive = !!resizeCtx?.colAncestor;
        const heightActive = !!resizeCtx?.rowAncestor;
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
        const paneType = role === 0 ? "mpv" : (state.pane_types?.[role - 1] || "terminal");
        card.dataset.studioRole = String(role);
        card.innerHTML = `
          <div class="studio-top">
            <span class="studio-card-title">${roleTitle(role)}</span>
            <span class="studio-tag">${paneType === "mpv" ? "mpv" : "shell"}</span>
          </div>
          <div class="studio-card-body">
            <div class="studio-size-group">
              <label class="studio-size-chip" data-active="${widthActive ? "true" : "false"}" title="${widthActive ? "Adjusts the nearest vertical split." : "This pane has no vertical split ancestor to resize."}">
                <span>W</span>
                <input type="number" class="studio-size-input" value="${widthValue}" min="${STUDIO_SIZE_MIN}" max="${STUDIO_SIZE_MAX}" data-role="${role}" data-axis="w" step="1" ${widthActive ? "" : "disabled"}>
              </label>
              <label class="studio-size-chip" data-active="${heightActive ? "true" : "false"}" title="${heightActive ? "Adjusts the nearest horizontal split." : "This pane has no horizontal split ancestor to resize."}">
                <span>H</span>
                <input type="number" class="studio-size-input" value="${heightValue}" min="${STUDIO_SIZE_MIN}" max="${STUDIO_SIZE_MAX}" data-role="${role}" data-axis="h" step="1" ${heightActive ? "" : "disabled"}>
              </label>
            </div>
          </div>
          ${handleMarkup}
        `;
        card.addEventListener("click", () => {
          selectRole(role);
          renderStudioInspector();
          syncStudioBoardSelectionState();
        });
        card.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          selectRole(role);
          renderStudioInspector();
          syncStudioBoardSelectionState();
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
          card.classList.remove("drop-target");
          if (!splitTreeSwapRoles(tree, sourceRole, role)) {
            setStatus("Could not reposition panes.", true);
            return;
          }
          state.splitTreeModel = tree;
          syncSplitTreeState();
          draggedStudioRole = null;
          selectRole(sourceRole);
          renderPlaylistEditor();
          renderStudioBoard();
          renderStudioInspector();
          setStatus(`Swapped ${roleTitle(sourceRole)} with ${roleTitle(role)}.`, false);
        });
        card.querySelectorAll(".studio-size-input").forEach((input) => {
          input.addEventListener("change", (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (input.disabled) return;
            selectRole(role);
            const axis = input.dataset.axis === "h" ? "h" : "w";
            const parsed = parseInt(input.value, 10);
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
          input.addEventListener("click", (event) => {
            event.stopPropagation();
          });
          input.addEventListener("input", (event) => {
            event.stopPropagation();
          });
        });
        card.querySelectorAll("[data-studio-resize]").forEach((handle) => {
          handle.addEventListener("pointerdown", (event) => {
            event.preventDefault();
            event.stopPropagation();
            startStudioResizeDrag(event, role, handle.dataset.studioResize || "w");
          });
          handle.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
          });
        });
        studioBoard.appendChild(card);
      });
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
      const preferredWidth = Math.round(Math.max(220, rect.width * 2.4));
      const preferredHeight = Math.round(Math.max(180, rect.height * 2.4));
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

    function renderStudioInspector() {
      if (!state || !studioInspector) return;
      ensureSelectedRole();

      if (selectedRole < 0) {
        studioInspector.innerHTML = `<div class="studio-empty">Select a pane to edit it.</div>`;
        dispatchSelectedPaneState();
        return;
      }

      const layoutActions = selectedPaneLayoutActionsMarkup(selectedRole);

      if (selectedRole === 0) {
        const mainMpvGroups = parseMpvOptionGroups(state.mpv_opts || []);
        studioInspector.innerHTML = `
          <div class="selected-pane-section">
            <h2 class="section-title">Pane Behavior</h2>
            <label>Audio Output
              <select id="inspectorMainAudioMode">
                <option value="">Default</option>
                <option value="no-audio"${mainMpvGroups.audioMode === "no-audio" ? " selected" : ""}>No Audio</option>
              </select>
            </label>
            <label>Mute
              <select id="inspectorMainMuteMode">
                <option value="">Default</option>
                <option value="yes"${mainMpvGroups.muteMode === "yes" ? " selected" : ""}>Muted</option>
                <option value="no"${mainMpvGroups.muteMode === "no" ? " selected" : ""}>Unmuted</option>
              </select>
            </label>
            <label>Loop Current File
              <select id="inspectorMainLoopFile">
                <option value="">Default</option>
                <option value="no"${mainMpvGroups.loopFile === "no" ? " selected" : ""}>Off</option>
                <option value="yes"${mainMpvGroups.loopFile === "yes" ? " selected" : ""}>On</option>
                <option value="inf"${mainMpvGroups.loopFile === "inf" ? " selected" : ""}>Infinite</option>
              </select>
            </label>
            <label>Shader Stack
              <textarea id="inspectorMainShaders" spellcheck="false" placeholder="/path/to/shader1.glsl&#10;/path/to/shader2.glsl">${mainMpvGroups.shaders.join("\n")}</textarea>
            </label>
            <label>Additional mpv Options
              <textarea id="inspectorMainMpvOpts" spellcheck="false" placeholder="profile=fast&#10;deband=yes">${mainMpvGroups.other.join("\n")}</textarea>
            </label>
          </div>
          ${layoutActions}
          ${selectedPaneQueueSectionMarkup()}
        `;
        [
          "inspectorMainAudioMode",
          "inspectorMainMuteMode",
          "inspectorMainLoopFile",
          "inspectorMainShaders",
          "inspectorMainMpvOpts"
        ].forEach((id) => {
          document.getElementById(id).addEventListener("input", () => {
            syncMainInspectorMpvOpts();
            renderStudioBoard();
            renderPlaylistEditor();
          });
          document.getElementById(id).addEventListener("change", () => {
            syncMainInspectorMpvOpts();
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
        bindSelectedPaneLayoutActions(selectedRole);
        syncMainInspectorMpvOpts();
        renderPlaylistEditor();
        dispatchSelectedPaneState();
        return;
      }

      const paneIndex = selectedRole - 1;
      const paneType = state.pane_types?.[paneIndex] || "terminal";
      const value = state.pane_commands?.[paneIndex] || "";
      if (paneType === "mpv") {
        if (pendingNewPaneIndexes.has(paneIndex)) {
          studioInspector.innerHTML = `
            <div class="selected-pane-section">
              <h2 class="section-title">Pane Behavior</h2>
              <label>Pane Type
                <select id="inspectorPaneType">
                  <option value="terminal">terminal</option>
                  <option value="mpv" selected>mpv</option>
                </select>
              </label>
              <p class="muted-note">This new mpv pane has been placed in the layout, but its playlist and mpv options stay hidden until you save and let the page reload from the persisted config.</p>
            </div>
            ${layoutActions}
          `;
          document.getElementById("inspectorPaneType").addEventListener("change", (event) => {
            state.pane_types[paneIndex] = event.target.value;
            renderStudioBoard();
            renderStudioInspector();
          });
          bindSelectedPaneLayoutActions(selectedRole);
          dispatchSelectedPaneState();
          return;
        }
        const paneMpvGroups = parseMpvOptionGroups(state.pane_mpv_opts?.[paneIndex] || []);
        studioInspector.innerHTML = `
          <div class="selected-pane-section">
            <h2 class="section-title">Pane Behavior</h2>
            <label>Pane Type
              <select id="inspectorPaneType">
                <option value="terminal">terminal</option>
                <option value="mpv" selected>mpv</option>
              </select>
            </label>
            <label>Audio Output
              <select id="inspectorPaneAudioMode">
                <option value="">Default</option>
                <option value="no-audio"${paneMpvGroups.audioMode === "no-audio" ? " selected" : ""}>No Audio</option>
              </select>
            </label>
            <label>Mute
              <select id="inspectorPaneMuteMode">
                <option value="">Default</option>
                <option value="yes"${paneMpvGroups.muteMode === "yes" ? " selected" : ""}>Muted</option>
                <option value="no"${paneMpvGroups.muteMode === "no" ? " selected" : ""}>Unmuted</option>
              </select>
            </label>
            <label>Loop Current File
              <select id="inspectorPaneLoopFile">
                <option value="">Default</option>
                <option value="no"${paneMpvGroups.loopFile === "no" ? " selected" : ""}>Off</option>
                <option value="yes"${paneMpvGroups.loopFile === "yes" ? " selected" : ""}>On</option>
                <option value="inf"${paneMpvGroups.loopFile === "inf" ? " selected" : ""}>Infinite</option>
              </select>
            </label>
            <label>Shader Stack
              <textarea id="inspectorPaneShaders" spellcheck="false" placeholder="/path/to/shader1.glsl&#10;/path/to/shader2.glsl">${paneMpvGroups.shaders.join("\n")}</textarea>
            </label>
            <label>Additional mpv Options
              <textarea id="inspectorPaneMpvOpts" spellcheck="false" placeholder="profile=fast&#10;deband=yes">${paneMpvGroups.other.join("\n")}</textarea>
            </label>
          </div>
          ${layoutActions}
          ${selectedPaneQueueSectionMarkup()}
        `;
        document.getElementById("inspectorPaneType").addEventListener("change", (event) => {
          state.pane_types[paneIndex] = event.target.value;
          renderStudioBoard();
          renderStudioInspector();
        });
        [
          "inspectorPaneAudioMode",
          "inspectorPaneMuteMode",
          "inspectorPaneLoopFile",
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
        </div>
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
        const item = document.createElement("div");
        item.className = `playlist-item${thumbMetrics.isPortrait ? " portrait-thumb" : ""}${index % 2 === 1 ? " alt" : ""}`;
        item.draggable = true;
        item.dataset.videoDragIndex = String(index);
        const thumbCell = `
          <div class="playlist-media-cell">
            <div class="playlist-thumb${thumb ? "" : " empty"}${thumbMetrics.cover ? " cover" : ""}" style="aspect-ratio: ${thumbMetrics.aspectRatio};" data-hover-src="${mediaUrl(group.path).replace(/"/g, "&quot;")}" data-hover-path="${group.path.replace(/"/g, "&quot;")}" data-hover-video="${isLikelyVideoPath(group.path) ? "1" : "0"}">
              <div class="playlist-index">${index + 1}</div>
              ${thumb}
            </div>
          </div>`;
        const controls = `
            <label class="playlist-repeat-wrap" title="How many times this video repeats in a row">
              <span>Repeat</span>
              <input class="playlist-repeat" type="number" min="1" step="1" data-video-group-repeat="${index}" value="${group.count}" title="Repeat count" />
            </label>
            <button class="playlist-mini-btn" data-video-group-up="${index}">Up</button>
            <button class="playlist-mini-btn" data-video-group-down="${index}">Down</button>
            <button class="playlist-mini-btn danger" data-video-group-remove="${index}">Remove</button>`;
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
      playlistEditor.querySelectorAll("[data-video-group-up]").forEach((button) => {
        button.addEventListener("click", () => moveQueueGroupTo(Number(button.dataset.videoGroupUp), Math.max(0, Number(button.dataset.videoGroupUp) - 1)));
      });
      playlistEditor.querySelectorAll("[data-video-group-down]").forEach((button) => {
        button.addEventListener("click", () => moveQueueGroupTo(Number(button.dataset.videoGroupDown), Math.min(groups.length - 1, Number(button.dataset.videoGroupDown) + 1)));
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
            overlay.style.background = "rgba(8,8,10,0.96)";
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
          const rect = thumb.getBoundingClientRect();
          const size = playlistHoverOverlayBounds(rect);
          const position = playlistHoverOverlayPosition(rect, size.width, size.height);
          overlay.style.width = `${size.width}px`;
          overlay.style.height = `${size.height}px`;
          overlay.style.left = `${position.left}px`;
          overlay.style.top = `${position.top}px`;
          let media;
          if (!src) {
            playlistHoverOverlayFallback(overlay, "Preview unavailable");
            overlay.style.display = "block";
            return;
          }
          if (isVideo) {
            media = document.createElement("video");
            media.src = `${src}#t=5`;
            media.muted = true;
            media.autoplay = true;
            media.loop = true;
            media.playsInline = true;
          } else {
            media = document.createElement("img");
            media.src = src;
            media.alt = thumb.dataset.hoverPath || "Preview";
          }
          media.addEventListener("error", () => {
            playlistHoverOverlayFallback(overlay, "Preview unavailable");
          }, { once: true });
          media.style.width = "100%";
          media.style.height = "100%";
          media.style.display = "block";
          media.style.objectFit = "contain";
          media.style.background = "#000";
          overlay.appendChild(media);
          overlay.style.display = "block";
        });
        thumb.addEventListener("mouseleave", () => {
          if (window.__kmsThumbHoverOverlay) window.__kmsThumbHoverOverlay.style.display = "none";
        });
      });
      playlistEditor.querySelectorAll("video[data-preview-video]").forEach((video) => {
        const seekToPreview = () => {
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
      return `
        <div class="selected-pane-section">
          <h2 class="section-title">Layout Actions</h2>
          <div class="actions tight">
            <button type="button" class="secondary studio-split-btn" data-selected-pane-split="col">Split Vertically</button>
            <button type="button" class="secondary studio-split-btn" data-selected-pane-split="row">Split Horizontally</button>
            ${role > 0 ? '<button type="button" class="secondary studio-remove-btn" data-selected-pane-remove="true">Remove Pane</button>' : ''}
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
      const tree = ensureSplitTreeModel();
      const targetRole = selectedRole;
      const newRole = Number(state.pane_count || 0) + 1;
      state.pane_count = newRole;
      ensurePaneCommands(state);
      while (state.pane_commands.length < state.pane_count) state.pane_commands.push("");
      while (state.pane_types.length < state.pane_count) state.pane_types.push("terminal");
      while (state.pane_playlists.length < state.pane_count) state.pane_playlists.push("");
      while (state.pane_playlist_extended.length < state.pane_count) state.pane_playlist_extended.push("");
      while (state.pane_playlist_fifos.length < state.pane_count) state.pane_playlist_fifos.push("");
      while (state.pane_video_paths.length < state.pane_count) state.pane_video_paths.push([]);
      while (state.pane_mpv_opts.length < state.pane_count) state.pane_mpv_opts.push([]);
      pendingNewPaneIndexes.add(newRole - 1);
      const changed = splitTreeReplaceLeaf(tree, targetRole, (leaf) => ({
        leaf: false,
        kind,
        pct: 50,
        first: { leaf: true, role: leaf.role },
        second: { leaf: true, role: newRole }
      }));
      if (!changed) {
        state.pane_count -= 1;
        state.pane_commands.pop();
        state.pane_types.pop();
        state.pane_playlists.pop();
        state.pane_playlist_extended.pop();
        state.pane_playlist_fifos.pop();
        state.pane_video_paths.pop();
        state.pane_mpv_opts.pop();
        pendingNewPaneIndexes.delete(newRole - 1);
        return false;
      }
      state.splitTreeModel = tree;
      syncSplitTreeState();
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
      if (!state || selectedRole <= 0) return false;
      const tree = ensureSplitTreeModel();
      const paneIndex = selectedRole - 1;
      const role = selectedRole;
      if (!splitTreeCollapseRole(tree, role)) return false;
      for (let i = role + 1; i <= state.pane_count; i += 1) {
        splitTreeReplaceLeaf(tree, i, () => ({ leaf: true, role: i - 1 }));
      }
      state.pane_commands.splice(paneIndex, 1);
      state.pane_types.splice(paneIndex, 1);
      state.pane_playlists.splice(paneIndex, 1);
      state.pane_playlist_extended.splice(paneIndex, 1);
      state.pane_playlist_fifos.splice(paneIndex, 1);
      state.pane_video_paths.splice(paneIndex, 1);
      state.pane_mpv_opts.splice(paneIndex, 1);
      pendingNewPaneIndexes = new Set(Array.from(pendingNewPaneIndexes)
        .filter((index) => index !== paneIndex)
        .map((index) => index > paneIndex ? index - 1 : index));
      state.pane_count = Math.max(1, state.pane_count - 1);
      state.splitTreeModel = tree;
      syncSplitTreeState();
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
      try {
        const pc = new RTCPeerConnection({ iceServers: [] });
        webrtcPeer = pc;
        const remoteStream = new MediaStream();
        webrtcStream = remoteStream;
        const transceiver = pc.addTransceiver("video", { direction: "recvonly" });
        if (transceiver && transceiver.setCodecPreferences && window.RTCRtpReceiver?.getCapabilities) {
          const capabilities = RTCRtpReceiver.getCapabilities("video");
          if (capabilities?.codecs?.length) {
            const codecRank = (mimeType) => {
              if (mimeType === "video/AV1") return 0;
              if (mimeType === "video/VP9") return 1;
              if (mimeType === "video/VP8") return 2;
              if (mimeType === "video/H264") return 3;
              return 4;
            };
            const ordered = capabilities.codecs.slice().sort((a, b) => codecRank(a.mimeType) - codecRank(b.mimeType));
            if (ordered.length) transceiver.setCodecPreferences(ordered);
          }
        }
        pc.addEventListener("track", (event) => {
          remoteStream.addTrack(event.track);
          previewVideo.muted = true;
          previewVideo.autoplay = true;
          previewVideo.playsInline = true;
          previewVideo.setAttribute("muted", "");
          previewVideo.setAttribute("autoplay", "");
          previewVideo.setAttribute("playsinline", "");
          previewVideo.srcObject = remoteStream;
          const ensurePlay = () => previewVideo.play().catch(() => {});
          ensurePlay();
          previewVideo.onloadedmetadata = () => syncWebRtcPreviewGeometry();
          previewVideo.onresize = () => syncWebRtcPreviewGeometry();
          previewVideo.oncanplay = () => ensurePlay();
          setPreviewStageState("", false);
        });
        pc.addEventListener("connectionstatechange", () => {
          if (pc !== webrtcPeer) return;
          if (pc.connectionState === "connected") {
            setStatus("Live preview connected over WebRTC.", false, true);
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
          }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Failed to establish WebRTC preview");
        await pc.setRemoteDescription(payload);
      } catch (err) {
        stopLivePreviewStream();
        setPreviewStageState("Preview unavailable", true);
        throw err;
      }
    }

    function stopLivePreviewStream() {
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

    function configuredVideoRotationDegrees(role = (selectedRole >= 0 ? selectedRole : 0)) {
      let rawValue = state?.video_rotate || "0";
      if (Number(role) > 0) {
        const paneIndex = Number(role) - 1;
        rawValue = state?.pane_video_rotate?.[paneIndex] || "0";
      }
      const raw = parseInt(String(rawValue || "0"), 10);
      if (!Number.isFinite(raw)) return 0;
      let total = raw % 360;
      if (total < 0) total += 360;
      return total;
    }

    function effectivePlaylistThumbRotationDegrees(role = (selectedRole >= 0 ? selectedRole : 0)) {
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
      state.flags.no_video = document.getElementById("flagNoVideo").checked;
      state.flags.no_panes = document.getElementById("flagNoPanes").checked;
      state.visibility_mode = document.getElementById("flagNoVideo").checked
        ? "no-video"
        : (document.getElementById("flagNoPanes").checked ? "no-terminal" : "neither");
      normalizeVisibilityFlags();
      state.flags.smooth = document.getElementById("flagSmooth").checked;
      state.flags.loop_file = document.getElementById("flagLoop").checked;
      state.flags.loop_playlist = document.getElementById("flagLoopPlaylist").checked;
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
      if (treeRoles.length && (treeRoles.length !== state.pane_count + 1 || Math.max(...treeRoles) !== state.pane_count)) {
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
      state = nextState;
      state.visibility_mode = visibilityModeForState(state);
      normalizeVisibilityFlags();
      rawConfigText = nextRawConfig;
      pendingNewPaneIndexes = new Set();
      ensurePaneCommands(state);
      state.splitTreeModel = parseSplitTreeSpec(state.split_tree || "");
      selectedRole = restoreSelectedRole(state, previousSelection);
      ensureSelectedRole();
      document.getElementById("configPath").textContent = `Config: ${configPath}`;
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
      if (modeEl) modeEl.value = state.mode || "";
      if (rotationEl) rotationEl.value = String(state.rotation || 0);
      if (fontSizeEl) fontSizeEl.value = String(state.font_size || 18);
      if (rightFracEl) rightFracEl.value = String(state.right_frac || 33);
      if (paneSplitEl) paneSplitEl.value = String(state.pane_split || 50);
      if (videoFracEl) videoFracEl.value = String(state.video_frac || 0);
      if (paneCountEl) paneCountEl.value = String(state.pane_count || 2);
      if (layoutEl) layoutEl.value = state.layout || "stack";
      if (rolesEl) rolesEl.value = state.roles || "";
      if (fsCycleEl) fsCycleEl.value = String(state.fs_cycle_sec || 5);
      const queueField = selectedPaneQueueField();
      if (queueField) queueField.value = (state.video_paths || []).join("\n");
      document.getElementById("flagNoVideo").checked = !!state.flags.no_video;
      document.getElementById("flagNoPanes").checked = !!state.flags.no_panes;
      document.getElementById("flagSmooth").checked = !!state.flags.smooth;
      document.getElementById("flagLoop").checked = !!state.flags.loop_file;
      document.getElementById("flagLoopPlaylist").checked = !!state.flags.loop_playlist;
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
      fillForm(payload.state, payload.config_path, payload.raw_config);
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
      if (!state?.flags) return;
      const nextMode = mode === "no-video"
        ? "no-video"
        : (mode === "no-terminal" || mode === "no-panes")
          ? "no-terminal"
          : "neither";
      const previousMode = visibilityModeForState(state);
      const previousFlags = {
        no_video: !!state.flags.no_video,
        no_panes: !!state.flags.no_panes,
      };
      state.visibility_mode = nextMode;
      state.flags.no_video = nextMode === "no-video";
      state.flags.no_panes = nextMode === "no-terminal";
      const noVideoField = document.getElementById("flagNoVideo");
      const noPanesField = document.getElementById("flagNoPanes");
      if (typeof noVideoField !== "undefined" && noVideoField) noVideoField.checked = !!state.flags.no_video;
      if (typeof noPanesField !== "undefined" && noPanesField) noPanesField.checked = !!state.flags.no_panes;
      try {
        await saveState();
      } catch (err) {
        state.visibility_mode = previousMode;
        state.flags.no_video = previousFlags.no_video;
        state.flags.no_panes = previousFlags.no_panes;
        if (noVideoField) noVideoField.checked = !!state.flags.no_video;
        if (noPanesField) noPanesField.checked = !!state.flags.no_panes;
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
      if (document.hidden) {
        stopLivePreviewStream();
        return;
      }
      const delay = currentLivePreviewDelay();
      if (delay == null) {
        stopLivePreviewStream();
        return;
      }
      livePreviewTimer = setTimeout(async () => {
        try {
          await startLivePreviewStream();
        } catch (err) {
          setStatus(err.message, true);
        }
      }, 0);
    }

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
    });
    [
      "mode","rotation","fontSize","rightFrac","paneSplit",
      "videoFrac","paneCount","layout","roles","fsCycleSec",
      "videoList","extraLines","flagNoVideo","flagNoPanes",
      "flagSmooth","flagLoop","flagLoopPlaylist","flagShuffle","flagAtomic",
      "flagAtomicNonblock","flagGlFinish","flagNoOsd"
    ].forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("input", () => syncFormToState());
      el.addEventListener("change", () => syncFormToState());
    });
    loadState().catch(err => setStatus(err.message, true));
    window.addEventListener("beforeunload", () => stopLivePreviewStream());
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

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}")

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
            time.sleep(0.03)
        raise TimeoutError("Timed out waiting for preview frame")

    def _thumbnail_cache_path(self, source: Path) -> Path:
        digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
        return self.app_config.thumb_cache_dir / f"{digest}.jpg"

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
        if self.path not in ("/api/config", "/api/raw_config"):
            if self.path != "/api/webrtc-offer":
                self._send_json({"error": "Not found"}, status=404)
                return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
            if self.path == "/api/webrtc-offer":
                answer = self.webrtc.create_answer(str(payload["sdp"]), str(payload["type"]))
                self._send_json(answer)
                return
            config_path = self.app_config.config_path
            if self.path == "/api/config":
                state = payload["state"]
                text = serialize_config(state)
            else:
                text = str(payload["raw_config"])
            self._write_text_atomic(config_path, text)
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
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8787, help="Bind port")
    parser.add_argument("--dump-state", action="store_true", help="Print parsed config state as JSON and exit")
    parser.add_argument("--print-html", action="store_true", help="Print the standalone HTML shell and exit")
    parser.add_argument("--write-state-json", help="Read a JSON file containing {state: ...}, write config, print updated JSON")
    parser.add_argument("--write-raw-json", help="Read a JSON file containing {raw_config: ...}, write config, print updated JSON")
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
    )
    server = ReusableThreadingHTTPServer((app_config.host, app_config.port), Handler)
    server.app_config = app_config  # type: ignore[attr-defined]
    server.webrtc = WebRTCBridge(app_config)  # type: ignore[attr-defined]
    server.webrtc.start()  # type: ignore[attr-defined]
    print(f"KMS Mosaic web UI serving {app_config.config_path} on http://{app_config.host}:{app_config.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.webrtc.close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    raise SystemExit(main())
