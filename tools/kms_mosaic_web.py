#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Any


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
        "pane_types": ["terminal", "terminal"],
        "pane_commands": DEFAULT_PANE_COMMANDS.copy(),
        "pane_playlists": ["", ""],
        "pane_playlist_extended": ["", ""],
        "pane_playlist_fifos": ["", ""],
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


def ensure_panes(state: dict[str, Any]) -> None:
    pane_count = max(1, int(state.get("pane_count", 2)))
    state["pane_count"] = pane_count
    pane_commands = list(state.get("pane_commands", []))
    pane_types = list(state.get("pane_types", []))
    pane_playlists = list(state.get("pane_playlists", []))
    pane_playlist_extended = list(state.get("pane_playlist_extended", []))
    pane_playlist_fifos = list(state.get("pane_playlist_fifos", []))
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
    while len(pane_video_paths) < pane_count:
        pane_video_paths.append([])
    while len(pane_mpv_opts) < pane_count:
        pane_mpv_opts.append([])
    state["pane_commands"] = pane_commands[:pane_count]
    state["pane_types"] = pane_types[:pane_count]
    state["pane_playlists"] = pane_playlists[:pane_count]
    state["pane_playlist_extended"] = pane_playlist_extended[:pane_count]
    state["pane_playlist_fifos"] = pane_playlist_fifos[:pane_count]
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
    pane_video_paths = [list(paths) for paths in state.get("pane_video_paths", [])]
    pane_mpv_opts = [list(opts) for opts in state.get("pane_mpv_opts", [])]
    for idx, cmd in enumerate(pane_commands):
        pane_type = pane_types[idx] if idx < len(pane_types) else "terminal"
        if pane_type == "mpv":
            lines.append(f"--pane-media {idx + 1}")
            playlist = pane_playlists[idx] if idx < len(pane_playlists) else ""
            playlist_ext = pane_playlist_extended[idx] if idx < len(pane_playlist_extended) else ""
            playlist_fifo = pane_playlist_fifos[idx] if idx < len(pane_playlist_fifos) else ""
            videos = pane_video_paths[idx] if idx < len(pane_video_paths) else []
            mpv_opts = pane_mpv_opts[idx] if idx < len(pane_mpv_opts) else []
            if playlist:
                lines.append(f"--pane-playlist {idx + 1} {shlex.quote(str(playlist))}")
            if playlist_ext:
                lines.append(f"--pane-playlist-extended {idx + 1} {shlex.quote(str(playlist_ext))}")
            if playlist_fifo:
                lines.append(f"--pane-playlist-fifo {idx + 1} {shlex.quote(str(playlist_fifo))}")
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
    snapshot_output_path: Path
    thumb_cache_dir: Path


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
      align-items: center;
      gap: 10px;
      padding: 0 14px 0 0;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255,255,255,0.55), rgba(255,255,255,0.25));
      min-height: 44px;
      overflow: hidden;
    }
    .accent-bar {
      width: 4px;
      align-self: stretch;
      flex-shrink: 0;
      background: linear-gradient(180deg, rgba(181,83,47,0.9), rgba(109,47,23,0.7));
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
      flex: 1;
    }
    /* ─── preview ─────────────────────────────────────── */
    .stage { padding: 12px; }
    .preview-layout { display: block; }
    .preview-layout.portrait .preview-wrap { aspect-ratio: 9 / 16; max-height: 440px; margin: 0 auto; }
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
    }
    .preview-stage {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .preview-canvas { width: 100%; height: 100%; display: block; background: #070605; }
    .preview-image { display: none; }
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
    .studio-card {
      position: absolute;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.03), 0 12px 28px rgba(0,0,0,0.24);
      overflow: hidden;
      cursor: pointer;
      transition: border-color 120ms ease, box-shadow 120ms ease;
      display: grid;
      align-content: space-between;
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
    .studio-tag {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 8px;
      border-radius: 5px;
      background: rgba(255,255,255,0.08);
      backdrop-filter: blur(6px);
    }
    .studio-card-body { display: grid; gap: 5px; align-self: end; }
    .studio-split-actions { display: flex; gap: 5px; margin-top: 6px; flex-wrap: wrap; }
    .studio-split-btn {
      border: 1px solid rgba(255,255,255,0.14);
      background: rgba(255,255,255,0.10);
      color: inherit;
      padding: 5px 8px;
      border-radius: 6px;
      font-size: 10px;
      font-family: "Menlo", "Consolas", monospace;
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
    .studio-empty { color: var(--muted); font-size: 12px; line-height: 1.5; }
    /* ─── playlist ────────────────────────────────────── */
    .playlist-editor { display: grid; gap: 7px; }
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
    }
    .playlist-target-btn.active {
      color: #fff8f0;
      border-color: rgba(255,240,200,0.48);
      background: linear-gradient(135deg, rgba(181,83,47,0.92), rgba(132,58,31,0.96));
      box-shadow: 0 4px 12px rgba(181,83,47,0.18);
    }
    .playlist-item {
      border: 1px solid var(--line);
      border-radius: var(--r-sm);
      background: var(--surface-high);
      padding: 9px 10px;
      display: grid;
      gap: 8px;
    }
    .playlist-item.dragging { opacity: 0.5; transform: scale(0.99); }
    .playlist-item.drag-over { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(181,83,47,0.14); }
    .playlist-media-cell { width: 120px; display: grid; gap: 4px; align-self: start; }
    .playlist-duration {
      color: var(--muted);
      font-family: "Menlo", "Consolas", monospace;
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      text-align: center;
    }
    .playlist-thumb {
      width: 120px;
      aspect-ratio: 16 / 9;
      border-radius: 9px;
      overflow: hidden;
      background:
        linear-gradient(140deg, rgba(181,83,47,0.15), transparent),
        var(--surface-high);
      border: 1px solid var(--line);
      position: relative;
    }
    .playlist-thumb img, .playlist-thumb video {
      width: 100%; height: 100%; object-fit: cover; display: block;
    }
    .playlist-thumb video { background: #0e0c0a; }
    .playlist-thumb.empty::after {
      content: "—";
      position: absolute;
      inset: 0;
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 16px;
      opacity: 0.5;
    }
    .playlist-row {
      display: grid;
      grid-template-columns: auto 120px 80px auto auto auto;
      gap: 8px;
      align-items: center;
    }
    .playlist-path { width: 100%; grid-column: 1 / -1; margin-top: 1px; }
    .playlist-index {
      width: 26px; height: 26px;
      border-radius: 6px;
      display: grid;
      place-items: center;
      background: rgba(207,120,83,0.13);
      color: var(--accent-dark);
      font-family: "Menlo", "Consolas", monospace;
      font-size: 11px;
      font-weight: 700;
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
      width: 80px;
    }
    .playlist-repeat-wrap span { display: block; line-height: 9px; }
    .playlist-mini-btn {
      padding: 6px 9px;
      border-radius: 6px;
      background: var(--surface-high);
      border: 1px solid var(--line);
      color: var(--ink);
      height: 30px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-top: 10px;
    }
    .playlist-mini-btn.danger { color: var(--danger); }
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
      .playlist-row { grid-template-columns: auto 1fr; }
      .playlist-media-cell, .playlist-thumb { width: 100%; }
    }
  </style>
</head>
<body class="grain">
  <div class="shell">
    <section class="card left-rail">
      <div class="app-header">
        <div class="accent-bar"></div>
        <span class="app-name">KMS Mosaic</span>
        <span id="configPath" class="config-path">—</span>
      </div>
      <div class="stage">
        <div class="preview-layout" id="preview-outer">
          <div class="preview-wrap" id="preview">
            <div class="preview-stage">
              <canvas id="previewCanvas" class="preview-canvas"></canvas>
              <img id="previewImage" class="preview-image" alt="Current KMS Mosaic output" />
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="panel-body">
        <div class="panel-wide">
          <h2 class="section-title">Layout Studio</h2>
          <div class="studio-grid">
            <div>
              <div class="actions tight" style="margin-bottom:10px;">
                <button class="secondary" id="addPaneBtn">Add Pane</button>
                <button class="secondary" id="removePaneBtn">Remove Selected Pane</button>
                <button class="secondary" id="splitVerticalBtn">Split Vertical</button>
                <button class="secondary" id="splitHorizontalBtn">Split Horizontal</button>
                <button class="secondary" id="rotatePanelsCcwBtn">Rotate Panels CCW</button>
                <button class="secondary" id="rotatePanelsCwBtn">Rotate Panels CW</button>
              </div>
              <div class="studio-board" id="studioBoard"></div>
            </div>
            <div class="studio-inspector" id="studioInspector">
              <div class="studio-empty">Select a pane to edit it.</div>
            </div>
          </div>
        </div>

        <div>
          <h2 class="section-title">Layout Suggestions</h2>
          <div class="suggestion-grid" id="layoutSuggestions"></div>
        </div>

        <div>
          <h2 class="section-title">Media</h2>
          <div class="grid">
            <label>Connector
              <input id="connector" type="text" placeholder="HDMI-A-1" />
            </label>
            <label>Playlist
              <input id="playlist" type="text" placeholder="/path/to/list.txt" />
            </label>
            <label>Playlist Extended
              <input id="playlistExtended" type="text" placeholder="/path/to/extended-playlist.txt" />
            </label>
            <label>Playlist FIFO
              <input id="playlistFifo" type="text" placeholder="/tmp/mosaic.fifo" />
            </label>
            <label>mpv Out
              <input id="mpvOut" type="text" placeholder="/tmp/mpv.log" />
            </label>
            <label>Panscan
              <input id="panscan" type="text" placeholder="1" />
            </label>
            <label>Video Rotate
              <input id="videoRotate" type="text" placeholder="270" />
            </label>
            <label>Audio Output
              <select id="mpvAudioMode">
                <option value="">Default</option>
                <option value="no-audio">No Audio</option>
              </select>
            </label>
            <label style="grid-column: 1 / -1;">Shader Stack
              <textarea id="mpvShaders" spellcheck="false" placeholder="/path/to/shader1.glsl&#10;/path/to/shader2.glsl"></textarea>
            </label>
          </div>
        </div>

        <div class="panel-wide">
          <h2 class="section-title" id="queueEditorTitle">Queue Editor</h2>
          <div class="playlist-targets" id="playlistTargets"></div>
          <div class="playlist-editor" id="playlistEditor"></div>
          <div class="actions tight">
            <button class="secondary" id="addQueueItemBtn">Add Video</button>
          </div>
          <p class="muted-note" id="queueEditorNote">Pick which mpv pane you want to edit.</p>
          <label id="videoListWrap" class="hidden">Video Files
            <textarea id="videoList" spellcheck="false" placeholder="/path/one.mp4&#10;/path/two.mp4"></textarea>
          </label>
        </div>

        <div>
          <h2 class="section-title">Panes</h2>
          <div class="pane-list" id="paneList"></div>
        </div>

        <div>
          <h2 class="section-title">Flags</h2>
          <div class="checks">
            <label class="check"><input id="flagNoVideo" type="checkbox" /> No Video</label>
            <label class="check"><input id="flagNoPanes" type="checkbox" /> No Panes</label>
            <label class="check"><input id="flagSmooth" type="checkbox" /> Smooth Preset</label>
            <label class="check"><input id="flagLoop" type="checkbox" /> Loop File</label>
            <label class="check"><input id="flagLoopPlaylist" type="checkbox" /> Loop Playlist</label>
            <label class="check"><input id="flagShuffle" type="checkbox" /> Shuffle</label>
            <label class="check"><input id="flagAtomic" type="checkbox" /> Atomic</label>
            <label class="check"><input id="flagAtomicNonblock" type="checkbox" /> Atomic Nonblock</label>
            <label class="check"><input id="flagGlFinish" type="checkbox" /> glFinish</label>
            <label class="check"><input id="flagNoOsd" type="checkbox" /> No OSD</label>
          </div>
        </div>

        <details class="advanced-block panel-wide" id="advancedPanel">
          <summary>Advanced</summary>
          <div class="advanced-body">
            <div>
              <h2 class="section-title">Scene Rules</h2>
              <div class="grid">
                <label>Legacy Layout Suggestion
                  <select id="layout"></select>
                </label>
                <label>Terminal Pane Count
                  <input id="paneCount" type="number" min="1" max="12" />
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
                <label>Column Split %
                  <input id="rightFrac" type="number" min="10" max="90" />
                </label>
                <label>Row Split %
                  <input id="paneSplit" type="number" min="10" max="90" />
                </label>
                <label>Video Fraction %
                  <input id="videoFrac" type="number" min="0" max="90" />
                </label>
                <label>Roles
                  <input id="roles" type="text" placeholder="CAB or 01234" />
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
              <h2 class="section-title">Additional mpv Options</h2>
              <label>Raw Global mpv Options
                <textarea id="mpvOpts" spellcheck="false" placeholder="hwdec=no&#10;profile=fast"></textarea>
              </label>
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
        </details>

        <div class="actions">
          <button class="primary" id="saveBtn">Save Config</button>
          <button class="secondary" id="reloadBtn">Reload From Disk</button>
        </div>
        <div class="status" id="status"></div>
      </div>
    </section>
  </div>

  <script>
    const layoutNames = ["stack", "row", "2x1", "1x2", "2over1", "1over2", "overlay"];
    const previewImage = document.getElementById("previewImage");
    const previewCanvas = document.getElementById("previewCanvas");
    const previewCtx = previewCanvas.getContext("2d");
    const previewLayout = document.querySelector(".preview-layout");
    const layoutSelect = document.getElementById("layout");
    const paneList = document.getElementById("paneList");
    const studioBoard = document.getElementById("studioBoard");
    const studioInspector = document.getElementById("studioInspector");
    const playlistEditor = document.getElementById("playlistEditor");
    const playlistTargets = document.getElementById("playlistTargets");
    const layoutSuggestions = document.getElementById("layoutSuggestions");
    const queueEditorTitleEl = document.getElementById("queueEditorTitle");
    const queueEditorNoteEl = document.getElementById("queueEditorNote");
    const addQueueItemBtn = document.getElementById("addQueueItemBtn");
    const statusEl = document.getElementById("status");
    let state = null;
    let rawConfigText = "";
    let selectedRole = 0;
    let playlistTargetRole = 0;
    let draggedStudioRole = null;
    let pendingNewPaneIndexes = new Set();
    let livePreviewTimer = null;
    let livePreviewInFlight = false;
    let livePreviewController = null;
    let livePreviewUrl = null;
    let playlistDragIndex = null;
    const layoutSuggestionCopy = {
      stack: "Vertical stack of the visible panes.",
      row: "Horizontal row of the visible panes.",
      "2x1": "Video to the right, panes grouped on the left.",
      "1x2": "Video to the left, panes grouped on the right.",
      "2over1": "Panes on top, video below.",
      "1over2": "Video on top, panes below.",
      overlay: "Video with pane strip overlay."
    };

    layoutNames.forEach(name => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      layoutSelect.appendChild(option);
    });

    function slotName(index) {
      if (index === 0) return "Video";
      if (index <= 26) return `Pane ${String.fromCharCode(64 + index)}`;
      return `Pane ${index}`;
    }

    function parseMpvOptionGroups(opts) {
      const groups = { audioMode: "", muteMode: "", loopFile: "", videoMode: "", shaders: [], other: [] };
      (Array.isArray(opts) ? opts : []).forEach((opt) => {
        const value = String(opt || "").trim();
        if (!value) return;
        if (value === "no-audio" || value === "audio=no" || value === "mpv-out=no-audio") {
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
        opts.push("mpv-out=no-audio");
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
        .forEach(shader => opts.push(`mpv-out=glsl-shaders=${shader}`));
      String(parts.otherText || "")
        .split("\n")
        .map(v => v.trim())
        .filter(Boolean)
        .forEach(opt => opts.push(opt));
      return opts;
    }

    function buildMpvOptsFromControls() {
      return buildMpvOptsFromParts({
        audioMode: document.getElementById("mpvAudioMode").value,
        shadersText: document.getElementById("mpvShaders").value,
        otherText: document.getElementById("mpvOpts").value,
      });
    }

    function syncMainInspectorMpvOpts() {
      const audioEl = document.getElementById("inspectorMainAudioMode");
      const shadersEl = document.getElementById("inspectorMainShaders");
      const otherEl = document.getElementById("inspectorMainMpvOpts");
      if (!audioEl || !shadersEl || !otherEl) return;
      state.mpv_opts = buildMpvOptsFromParts({
        audioMode: audioEl.value,
        shadersText: shadersEl.value,
        otherText: otherEl.value,
      });
      document.getElementById("mpvAudioMode").value = audioEl.value;
      document.getElementById("mpvShaders").value = shadersEl.value;
      document.getElementById("mpvOpts").value = otherEl.value;
    }

    function syncInspectorPaneMpvOpts(paneIndex) {
      if (!state || paneIndex < 0) return;
      const audioEl = document.getElementById("inspectorPaneAudioMode");
      const muteEl = document.getElementById("inspectorPaneMuteMode");
      const loopEl = document.getElementById("inspectorPaneLoopFile");
      const videoEl = document.getElementById("inspectorPaneVideoMode");
      const shadersEl = document.getElementById("inspectorPaneShaders");
      const otherEl = document.getElementById("inspectorPaneMpvOpts");
      if (!audioEl || !muteEl || !loopEl || !videoEl || !shadersEl || !otherEl) return;
      state.pane_mpv_opts[paneIndex] = buildMpvOptsFromParts({
        audioMode: audioEl.value,
        muteMode: muteEl.value,
        loopFile: loopEl.value,
        videoMode: videoEl.value,
        shadersText: shadersEl.value,
        otherText: otherEl.value,
      });
    }

    function roleType(role) {
      if (role === 0) return "video";
      return state?.pane_types?.[role - 1] === "mpv" ? "video" : "terminal";
    }

    function availablePlaylistTargets() {
      if (!state) return [];
      const targets = [{ role: 0, title: "Main mpv Pane" }];
      for (let role = 1; role <= Number(state.pane_count || 0); role += 1) {
        const paneIndex = role - 1;
        if (pendingNewPaneIndexes.has(paneIndex)) continue;
        if ((state.pane_types?.[paneIndex] || "terminal") !== "mpv") continue;
        targets.push({ role, title: roleTitle(role) });
      }
      return targets;
    }

    function ensurePlaylistTargetRole() {
      const roles = availablePlaylistTargets().map((target) => target.role);
      if (!roles.length) {
        playlistTargetRole = 0;
        return;
      }
      if (!roles.includes(playlistTargetRole)) {
        playlistTargetRole = roles[0];
      }
    }

    function renderPlaylistTargets() {
      if (!playlistTargets) return;
      ensurePlaylistTargetRole();
      playlistTargets.innerHTML = "";
      availablePlaylistTargets().forEach((target) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `playlist-target-btn${playlistTargetRole === target.role ? " active" : ""}`;
        button.textContent = target.title;
        button.addEventListener("click", () => {
          playlistTargetRole = target.role;
          renderPlaylistTargets();
          renderPlaylistEditor();
        });
        playlistTargets.appendChild(button);
      });
    }

    function queueEditorContext() {
      if (!state) return null;
      ensurePlaylistTargetRole();
      if (playlistTargetRole === 0) {
        return {
          title: "Main Video Queue",
          note: "This queue controls the main mpv pane.",
          paths: Array.isArray(state.video_paths) ? state.video_paths.slice() : [],
          editable: true,
          apply(paths) {
            state.video_paths = paths.slice();
            document.getElementById("videoList").value = state.video_paths.join("\n");
          }
        };
      }
      const paneIndex = playlistTargetRole - 1;
      const paneType = state.pane_types?.[paneIndex] || "terminal";
      if (paneType !== "mpv") {
        return {
          title: `${roleTitle(playlistTargetRole)} Queue`,
          note: "This pane is not currently an mpv pane.",
          paths: [],
          editable: false,
          apply() {}
        };
      }
      const playlistPath = state.pane_playlists?.[paneIndex] || "";
      const playlistExtended = state.pane_playlist_extended?.[paneIndex] || "";
      const playlistFifo = state.pane_playlist_fifos?.[paneIndex] || "";
      const paneMpvOpts = Array.isArray(state.pane_mpv_opts?.[paneIndex]) ? state.pane_mpv_opts[paneIndex].slice() : [];
      const noteParts = [`This queue controls ${roleTitle(playlistTargetRole)}.`];
      if (playlistPath) noteParts.push(`Playlist: ${playlistPath}`);
      if (playlistExtended) noteParts.push(`Extended: ${playlistExtended}`);
      if (playlistFifo) noteParts.push(`FIFO: ${playlistFifo}`);
      if (paneMpvOpts.length) noteParts.push(`${paneMpvOpts.length} pane-local mpv option${paneMpvOpts.length === 1 ? "" : "s"}.`);
      return {
        title: `${roleTitle(playlistTargetRole)} Queue`,
        note: noteParts.join(" "),
        paths: Array.isArray(state.pane_video_paths?.[paneIndex]) ? state.pane_video_paths[paneIndex].slice() : [],
        editable: true,
        apply(paths) {
          state.pane_video_paths[paneIndex] = paths.slice();
          document.getElementById("videoList").value = state.pane_video_paths[paneIndex].join("\n");
        }
      };
    }

    function mediaUrl(path) {
      const value = String(path || "").trim();
      if (!value) return "";
      return `/api/media?path=${encodeURIComponent(value)}`;
    }

    function isLikelyImagePath(path) {
      return /\.(avif|bmp|gif|jpe?g|png|webp)$/i.test(String(path || ""));
    }

    function isLikelyVideoPath(path) {
      return /\.(m4v|mkv|mov|mp4|mpeg|mpg|ts|webm)$/i.test(String(path || ""));
    }

    function playlistThumbMarkup(path, index) {
      const value = String(path || "").trim();
      if (!value) return "";
      const src = mediaUrl(value);
      if (isLikelyImagePath(value)) {
        return `<img src="${src}" alt="Preview for queue item ${index + 1}" loading="lazy" />`;
      }
      if (isLikelyVideoPath(value)) {
        return `<video data-preview-video="${index}" muted playsinline preload="metadata" src="${src}#t=5"></video>`;
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
      return role === 0 ? "Main mpv Pane" : slotName(role);
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
      const pct = Math.max(10, Math.min(90, Number(node.pct || 50)));
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

    function ensureSelectedRole() {
      const maxRole = Math.max(0, Number(state?.pane_count || 0));
      if (!Number.isFinite(selectedRole) || selectedRole < 0) selectedRole = 0;
      if (selectedRole > maxRole) selectedRole = maxRole;
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
      const screen = { x: 0, y: 0, w: 100, h: 100 };
      const paneCount = Math.max(1, Number(nextState.pane_count || 2));
      const roleCount = 1 + paneCount;
      const splitTree = nextState === state ? normalizeSplitTreeState() : parseSplitTreeSpec(nextState.split_tree || "");
      if (splitTree) {
        const rects = Array.from({ length: roleCount }, () => ({ x: 0, y: 0, w: 0, h: 0 }));
        splitTreeApplyRects(splitTree, screen, rects);
        return rects;
      }
      const mode = layoutNames.indexOf(nextState.layout || "stack");
      const splitPct = Math.max(10, Math.min(90, Number(nextState.pane_split || 50)));
      const colPct = Math.max(20, Math.min(80, 100 - Number(nextState.right_frac || 33)));
      const perm = parseRolesString(nextState);
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

      return Array.from({ length: roleCount }, (_, role) => slots[perm[role]] || screen);
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

    function transformStudioPaneRect(rect) {
      const rotated = transformRectByDegrees(rect, studioRotationDegrees());
      return { x: rotated.x, y: 100 - (rotated.y + rotated.h), w: rotated.w, h: rotated.h };
    }

    function applyStudioGeometry() {
      const total = normalizedRotationDegrees();
      studioBoard.style.aspectRatio = (total === 90 || total === 270) ? "9 / 16" : "16 / 9";
    }

    function renderLayoutSuggestions() {
      if (!state) return;
      layoutSuggestions.innerHTML = "";
      layoutNames.forEach((name) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "suggestion-btn";
        button.innerHTML = `<strong>${name}</strong><span>${layoutSuggestionCopy[name] || "Starter split-tree preset."}</span>`;
        button.addEventListener("click", () => {
          state.layout = name;
          document.getElementById("layout").value = name;
          state.splitTreeModel = presetTreeFromState(state);
          syncSplitTreeState();
          renderStudioBoard();
          renderStudioInspector();
          setStatus(`Applied ${name} as the current split-tree starter.`, false);
        });
        layoutSuggestions.appendChild(button);
      });
    }

    function renderStudioBoard() {
      if (!state) return;
      ensureSelectedRole();
      applyStudioGeometry();
      const rects = computeStudioRects(state);
      studioBoard.innerHTML = "";
      rects.forEach((rect, role) => {
        const displayRect = transformStudioPaneRect(rect);
        const card = document.createElement("button");
        card.type = "button";
        card.draggable = true;
        card.className = `studio-card ${roleType(role)}${selectedRole === role ? " selected" : ""}`;
        card.style.left = `${displayRect.x}%`;
        card.style.top = `${displayRect.y}%`;
        card.style.width = `${displayRect.w}%`;
        card.style.height = `${displayRect.h}%`;
        const paneType = role === 0 ? "mpv" : (state.pane_types?.[role - 1] || "terminal");
        const summary = role === 0
          ? `${(state.video_paths || []).length} queued video${(state.video_paths || []).length === 1 ? "" : "s"}`
          : (paneType === "mpv"
              ? `${(state.pane_video_paths?.[role - 1] || []).length} queued video${(state.pane_video_paths?.[role - 1] || []).length === 1 ? "" : "s"}`
              : (state.pane_commands?.[role - 1] || "No command"));
        const splitActions = `<div class="studio-split-actions">
               <button type="button" class="studio-split-btn" data-studio-split="col">Split V</button>
               <button type="button" class="studio-split-btn" data-studio-split="row">Split H</button>
             </div>`;
        card.innerHTML = `
          <div class="studio-top">
            <span class="studio-tag">${paneType === "mpv" ? "mpv" : "shell"}</span>
            <span>${Math.round(rect.w)} x ${Math.round(rect.h)}</span>
          </div>
          <div class="studio-card-body">
            <div class="studio-card-title">${roleTitle(role)}</div>
            <div class="studio-card-meta">${summary}</div>
            ${splitActions}
          </div>
        `;
        card.addEventListener("click", () => {
          selectedRole = role;
          renderStudioBoard();
          renderStudioInspector();
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
          selectedRole = sourceRole;
          renderStudioBoard();
          renderStudioInspector();
          setStatus(`Swapped ${roleTitle(sourceRole)} with ${roleTitle(role)}.`, false);
        });
        card.querySelectorAll("[data-studio-split]").forEach((button) => {
          button.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            selectedRole = role;
            if (!splitSelectedRole(button.dataset.studioSplit)) {
              setStatus("Could not split the selected pane.", true);
              return;
            }
            setStatus(button.dataset.studioSplit === "col" ? "Split the selected pane vertically." : "Split the selected pane horizontally.", false);
          });
        });
        studioBoard.appendChild(card);
      });
    }

    function renderStudioInspector() {
      if (!state) return;
      ensureSelectedRole();
      if (selectedRole === 0) {
        const mainMpvGroups = parseMpvOptionGroups(state.mpv_opts || []);
        studioInspector.innerHTML = `
          <div>
            <h2 class="section-title">Selected Pane</h2>
            <div class="mini">Main video pane</div>
          </div>
          <label>Pane Type
            <input type="text" value="mpv" readonly />
          </label>
          <label>Playlist Path
            <input id="inspectorPlaylist" type="text" value="${(state.playlist || "").replace(/"/g, "&quot;")}" placeholder="/path/to/list.txt" />
          </label>
          <label>Playlist FIFO
            <input id="inspectorPlaylistFifo" type="text" value="${(state.playlist_fifo || "").replace(/"/g, "&quot;")}" placeholder="/tmp/mosaic.fifo" />
          </label>
          <label>Audio Output
            <select id="inspectorMainAudioMode">
              <option value="">Default</option>
              <option value="no-audio"${mainMpvGroups.audioMode === "no-audio" ? " selected" : ""}>No Audio</option>
            </select>
          </label>
          <label>Shader Stack
            <textarea id="inspectorMainShaders" spellcheck="false" placeholder="/path/to/shader1.glsl&#10;/path/to/shader2.glsl">${mainMpvGroups.shaders.join("\n")}</textarea>
          </label>
          <label>Additional mpv Options
            <textarea id="inspectorMainMpvOpts" spellcheck="false" placeholder="profile=fast&#10;deband=yes">${mainMpvGroups.other.join("\n")}</textarea>
          </label>
          <p class="muted-note">This inspector controls the main video pane. The queue editor below still controls the main pane video list.</p>
        `;
        document.getElementById("inspectorPlaylist").addEventListener("input", (event) => {
          state.playlist = event.target.value;
          document.getElementById("playlist").value = state.playlist;
          renderPlaylistEditor();
        });
        document.getElementById("inspectorPlaylistFifo").addEventListener("input", (event) => {
          state.playlist_fifo = event.target.value;
          document.getElementById("playlistFifo").value = state.playlist_fifo;
        });
        ["inspectorMainAudioMode", "inspectorMainShaders", "inspectorMainMpvOpts"].forEach((id) => {
          document.getElementById(id).addEventListener("input", () => {
            syncMainInspectorMpvOpts();
            renderStudioBoard();
          });
          document.getElementById(id).addEventListener("change", () => {
            syncMainInspectorMpvOpts();
            renderStudioBoard();
          });
        });
        syncMainInspectorMpvOpts();
        return;
      }

      const paneIndex = selectedRole - 1;
      const paneType = state.pane_types?.[paneIndex] || "terminal";
      const value = state.pane_commands?.[paneIndex] || "";
      if (paneType === "mpv") {
        if (pendingNewPaneIndexes.has(paneIndex)) {
          studioInspector.innerHTML = `
            <div>
              <h2 class="section-title">Selected Pane</h2>
              <div class="mini">${roleTitle(selectedRole)}</div>
            </div>
            <label>Pane Type
              <select id="inspectorPaneType">
                <option value="terminal">terminal</option>
                <option value="mpv" selected>mpv</option>
              </select>
            </label>
            <p class="muted-note">This new mpv pane has been placed in the layout, but its playlist and mpv options stay hidden until you save and let the page reload from the persisted config.</p>
          `;
          document.getElementById("inspectorPaneType").addEventListener("change", (event) => {
            state.pane_types[paneIndex] = event.target.value;
            renderPaneList(state);
            renderStudioBoard();
            renderStudioInspector();
          });
          return;
        }
        const panePlaylist = state.pane_playlists?.[paneIndex] || "";
        const panePlaylistExtended = state.pane_playlist_extended?.[paneIndex] || "";
        const panePlaylistFifo = state.pane_playlist_fifos?.[paneIndex] || "";
        const paneVideos = (state.pane_video_paths?.[paneIndex] || []).join("\n");
        const paneMpvGroups = parseMpvOptionGroups(state.pane_mpv_opts?.[paneIndex] || []);
        studioInspector.innerHTML = `
          <div>
            <h2 class="section-title">Selected Pane</h2>
            <div class="mini">${roleTitle(selectedRole)}</div>
          </div>
          <label>Pane Type
            <select id="inspectorPaneType">
              <option value="terminal">terminal</option>
              <option value="mpv" selected>mpv</option>
            </select>
          </label>
          <label>Playlist Path
            <input id="inspectorPanePlaylist" type="text" value="${panePlaylist.replace(/"/g, "&quot;")}" placeholder="/path/to/list.txt" />
          </label>
          <label>Playlist Extended
            <input id="inspectorPanePlaylistExtended" type="text" value="${panePlaylistExtended.replace(/"/g, "&quot;")}" placeholder="/path/to/extended.txt" />
          </label>
          <label>Playlist FIFO
            <input id="inspectorPanePlaylistFifo" type="text" value="${panePlaylistFifo.replace(/"/g, "&quot;")}" placeholder="/tmp/pane-playlist.fifo" />
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
          <label>Video Output
            <select id="inspectorPaneVideoMode">
              <option value="">Default</option>
              <option value="audio-only"${paneMpvGroups.videoMode === "audio-only" ? " selected" : ""}>Audio Only</option>
            </select>
          </label>
          <label>Shader Stack
            <textarea id="inspectorPaneShaders" spellcheck="false" placeholder="/path/to/shader1.glsl&#10;/path/to/shader2.glsl">${paneMpvGroups.shaders.join("\n")}</textarea>
          </label>
          <label>Videos
            <textarea id="inspectorPaneVideos" spellcheck="false" placeholder="/path/one.mp4&#10;/path/two.mp4">${paneVideos}</textarea>
          </label>
          <label>Additional mpv Options
            <textarea id="inspectorPaneMpvOpts" spellcheck="false" placeholder="profile=fast&#10;deband=yes">${paneMpvGroups.other.join("\n")}</textarea>
          </label>
          <p class="muted-note">This pane writes its own per-pane mpv config and queue. The structured controls above generate common pane-local mpv options, and the final box keeps any extra per-pane flags that do not have a dedicated control yet.</p>
        `;
        document.getElementById("inspectorPaneType").addEventListener("change", (event) => {
          state.pane_types[paneIndex] = event.target.value;
          renderPaneList(state);
          renderStudioBoard();
          renderStudioInspector();
        });
        document.getElementById("inspectorPanePlaylist").addEventListener("input", (event) => {
          state.pane_playlists[paneIndex] = event.target.value;
          renderStudioBoard();
          renderPlaylistEditor();
        });
        document.getElementById("inspectorPanePlaylistExtended").addEventListener("input", (event) => {
          state.pane_playlist_extended[paneIndex] = event.target.value;
          renderStudioBoard();
          renderPlaylistEditor();
        });
        document.getElementById("inspectorPanePlaylistFifo").addEventListener("input", (event) => {
          state.pane_playlist_fifos[paneIndex] = event.target.value;
          renderStudioBoard();
          renderPlaylistEditor();
        });
        document.getElementById("inspectorPaneVideos").addEventListener("input", (event) => {
          state.pane_video_paths[paneIndex] = event.target.value.split("\n").map(v => v.trim()).filter(Boolean);
          renderStudioBoard();
          renderPlaylistEditor();
        });
        [
          "inspectorPaneAudioMode",
          "inspectorPaneMuteMode",
          "inspectorPaneLoopFile",
          "inspectorPaneVideoMode",
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
        syncInspectorPaneMpvOpts(paneIndex);
        return;
      }
      studioInspector.innerHTML = `
        <div>
          <h2 class="section-title">Selected Pane</h2>
          <div class="mini">${roleTitle(selectedRole)}</div>
        </div>
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
      `;
      document.getElementById("inspectorPaneType").addEventListener("change", (event) => {
        state.pane_types[paneIndex] = event.target.value;
        renderPaneList(state);
        renderStudioBoard();
        renderStudioInspector();
      });
      document.getElementById("inspectorPaneCommand").addEventListener("input", (event) => {
        state.pane_commands[paneIndex] = event.target.value;
        renderPaneList(state);
        renderStudioBoard();
      });
    }

    function renderPlaylistEditor() {
      if (!state) return;
      renderPlaylistTargets();
      const ctx = queueEditorContext();
      if (!ctx) return;
      queueEditorTitleEl.textContent = ctx.title;
      queueEditorNoteEl.textContent = ctx.note;
      addQueueItemBtn.disabled = !ctx.editable;
      document.getElementById("videoList").value = (ctx.paths || []).join("\n");
      document.getElementById("videoList").disabled = !ctx.editable;
      playlistEditor.innerHTML = "";
      const paths = ctx.paths;
      const groups = compressPlaylistPaths(paths);
      if (!ctx.editable) {
        playlistEditor.innerHTML = `<div class="studio-empty">No media queue is available for this target.</div>`;
        return;
      }
      if (!groups.length) {
        playlistEditor.innerHTML = `<div class="studio-empty">No videos queued yet. Add one below or paste paths into the raw list.</div>`;
        return;
      }
      groups.forEach((group, index) => {
        const thumb = playlistThumbMarkup(group.path, index);
        const durationLabel = isLikelyVideoPath(group.path) ? "Loading..." : "";
        const item = document.createElement("div");
        item.className = "playlist-item";
        item.draggable = true;
        item.dataset.videoDragIndex = String(index);
        item.innerHTML = `
          <div class="playlist-row">
            <div class="playlist-index">${index + 1}</div>
            <div class="playlist-media-cell">
              <div class="playlist-duration" data-video-duration="${index}">${durationLabel}</div>
              <div class="playlist-thumb${thumb ? "" : " empty"}">
                ${thumb}
              </div>
            </div>
            <label class="playlist-repeat-wrap" title="How many times this video repeats in a row">
              <span>Repeat</span>
              <input class="playlist-repeat" type="number" min="1" step="1" data-video-group-repeat="${index}" value="${group.count}" title="Repeat count" />
            </label>
            <button class="playlist-mini-btn" data-video-group-up="${index}">Up</button>
            <button class="playlist-mini-btn" data-video-group-down="${index}">Down</button>
            <button class="playlist-mini-btn danger" data-video-group-remove="${index}">Remove</button>
            <input class="playlist-path" type="text" data-video-group-index="${index}" value="${group.path.replace(/"/g, "&quot;")}" placeholder="/path/to/video.mp4" />
          </div>
        `;
        item.addEventListener("dragstart", () => {
          playlistDragIndex = index;
          item.classList.add("dragging");
        });
        item.addEventListener("dragend", () => {
          playlistDragIndex = null;
          item.classList.remove("dragging");
          playlistEditor.querySelectorAll(".playlist-item").forEach((node) => node.classList.remove("drag-over"));
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
        playlistEditor.appendChild(item);
      });
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
      playlistEditor.querySelectorAll("video[data-preview-video]").forEach((video) => {
        const durationEl = playlistEditor.querySelector(`[data-video-duration="${video.dataset.previewVideo}"]`);
        const seekToPreview = () => {
          if (!Number.isFinite(video.duration) || video.duration <= 0) return;
          if (durationEl) durationEl.textContent = formatMediaDuration(video.duration);
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
          if (durationEl) durationEl.textContent = "";
          video.closest(".playlist-thumb")?.classList.add("empty");
          video.remove();
        }, { once: true });
      });
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
      document.getElementById("paneCount").value = String(state.pane_count);
      selectedRole = newRole;
      renderPaneList(state);
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
      if (!state || selectedRole === 0) return;
      const tree = ensureSplitTreeModel();
      const paneIndex = selectedRole - 1;
      const role = selectedRole;
      if (!splitTreeCollapseRole(tree, role)) return;
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
      document.getElementById("paneCount").value = String(state.pane_count);
      selectedRole = Math.min(selectedRole, state.pane_count);
      renderPaneList(state);
      renderPlaylistEditor();
      renderStudioBoard();
      renderStudioInspector();
    }

    function renderPaneList(nextState) {
      paneList.innerHTML = "";
      ensurePaneCommands(nextState);
      nextState.pane_commands.forEach((cmd, index) => {
        const paneType = nextState.pane_types?.[index] || "terminal";
        const item = document.createElement("div");
        item.className = "pane-item";
        item.innerHTML = `
          <div class="pane-head">
            <div class="pane-name">${slotName(index + 1)}</div>
            <div class="mini">${paneType === "mpv" ? "mpv pane" : `role ${index + 1}`}</div>
          </div>
          <label>Command
            <input type="text" data-pane-index="${index}" value="${(cmd || "").replace(/"/g, "&quot;")}" placeholder="btop --utf-force" ${paneType === "mpv" ? "disabled" : ""} />
          </label>
        `;
        paneList.appendChild(item);
      });
      paneList.querySelectorAll("input[data-pane-index]").forEach(input => {
        input.addEventListener("input", event => {
          const idx = Number(event.target.dataset.paneIndex);
          state.pane_commands[idx] = event.target.value;
        });
      });
    }

    async function refreshSnapshot() {
      if (livePreviewInFlight) return;
      livePreviewInFlight = true;
      try {
        previewImage.src = `/api/snapshot.bmp?t=${Date.now()}`;
      } finally {
        // release in onload/onerror so the next cycle waits for the actual image
      }
    }

    function parseHeaderBlock(bytes) {
      const text = new TextDecoder().decode(bytes);
      const headers = {};
      text.split("\r\n").forEach(line => {
        const idx = line.indexOf(":");
        if (idx <= 0) return;
        headers[line.slice(0, idx).trim().toLowerCase()] = line.slice(idx + 1).trim();
      });
      return headers;
    }

    function findBytes(haystack, needle, fromIndex = 0) {
      outer: for (let i = fromIndex; i <= haystack.length - needle.length; i++) {
        for (let j = 0; j < needle.length; j++) {
          if (haystack[i + j] !== needle[j]) continue outer;
        }
        return i;
      }
      return -1;
    }

    function concatBytes(a, b) {
      const merged = new Uint8Array(a.length + b.length);
      merged.set(a, 0);
      merged.set(b, a.length);
      return merged;
    }

    async function drawFrameBytes(frameBytes) {
      const blob = new Blob([frameBytes], { type: "image/bmp" });
      const url = URL.createObjectURL(blob);
      try {
        previewImage.src = url;
        await new Promise((resolve, reject) => {
          previewImage.onload = () => resolve();
          previewImage.onerror = () => reject(new Error("Failed to decode live preview frame"));
        });
      } finally {
        URL.revokeObjectURL(url);
      }
      applyPreviewGeometry();
    }

    async function startLivePreviewStream() {
      stopLivePreviewStream();
      const delay = currentLivePreviewDelay();
      if (delay == null) return;

      livePreviewController = new AbortController();
      livePreviewUrl = `/api/live.bin?interval=${delay}`;

      try {
        const response = await fetch(livePreviewUrl, {
          cache: "no-store",
          signal: livePreviewController.signal
        });
        if (!response.ok || !response.body) throw new Error("Failed to open live preview stream");

        const reader = response.body.getReader();
        let buffer = new Uint8Array(0);

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          if (!value) continue;
          buffer = concatBytes(buffer, value);

          while (true) {
            if (buffer.length < 4) break;
            const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
            const frameLength = view.getUint32(0, false);
            if (!Number.isFinite(frameLength) || frameLength <= 0) {
              buffer = buffer.slice(4);
              continue;
            }
            if (buffer.length < 4 + frameLength) break;
            const frameBytes = buffer.slice(4, 4 + frameLength);
            buffer = buffer.slice(4 + frameLength);
            await drawFrameBytes(frameBytes);
          }
        }
      } catch (err) {
        if (livePreviewController?.signal.aborted) return;
        setStatus(err.message || "Live preview stream failed", true);
      } finally {
        if (livePreviewController && !livePreviewController.signal.aborted) {
          setTimeout(() => {
            if (currentLivePreviewDelay() != null) startLivePreviewStream().catch(err => setStatus(err.message, true));
          }, 500);
        }
      }
    }

    function stopLivePreviewStream() {
      if (livePreviewController) {
        livePreviewController.abort();
        livePreviewController = null;
      }
      if (livePreviewUrl) livePreviewUrl = null;
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
      const rotation = Number(state?.rotation || 0);
      const correction = getPreviewCorrection();
      let total = ((360 - rotation) + correction) % 360;
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
      const naturalW = previewImage.naturalWidth || 16;
      const naturalH = previewImage.naturalHeight || 9;
      const quarterTurn = total === 90 || total === 270;
      const displayW = quarterTurn ? naturalH : naturalW;
      const displayH = quarterTurn ? naturalW : naturalH;
      const preview = document.getElementById("preview");
      previewLayout?.classList.toggle("portrait", quarterTurn);
      previewLayout?.classList.toggle("landscape", !quarterTurn);
      preview.style.aspectRatio = `${displayW} / ${displayH}`;
      const previewTop = preview.getBoundingClientRect().top;
      const viewportPadding = 24;
      const availableHeight = Math.max(220, Math.floor(window.innerHeight - previewTop - viewportPadding));
      const parentWidth = preview.parentElement ? preview.parentElement.clientWidth : 560;
      const maxWidthByHeight = Math.floor(availableHeight * (displayW / displayH));
      const finalWidth = Math.max(180, Math.min(560, parentWidth, maxWidthByHeight));
      preview.style.width = `${finalWidth}px`;
      preview.style.maxWidth = "100%";
      preview.style.maxHeight = `${availableHeight}px`;
      previewCanvas.width = displayW;
      previewCanvas.height = displayH;
      if (!previewCtx || !previewImage.naturalWidth || !previewImage.naturalHeight) return;
      previewCtx.save();
      previewCtx.clearRect(0, 0, displayW, displayH);
      previewCtx.translate(displayW / 2, displayH / 2);
      previewCtx.rotate(total * Math.PI / 180);
      previewCtx.drawImage(previewImage, -naturalW / 2, -naturalH / 2, naturalW, naturalH);
      previewCtx.restore();
      applyStudioGeometry();
    }

    function syncFormToState() {
      const previousPaneCount = Array.isArray(state?.pane_commands) ? state.pane_commands.length : 0;
      state.connector = document.getElementById("connector").value.trim();
      state.mode = document.getElementById("mode").value.trim();
      state.rotation = readInt("rotation", 0);
      state.font_size = readInt("fontSize", 18);
      state.right_frac = readInt("rightFrac", 33);
      state.pane_split = readInt("paneSplit", 50);
      state.video_frac = readInt("videoFrac", 0);
      state.pane_count = Math.max(1, readInt("paneCount", 2));
      state.layout = document.getElementById("layout").value;
      state.roles = document.getElementById("roles").value.trim();
      state.fs_cycle_sec = readInt("fsCycleSec", 5);
      state.playlist = document.getElementById("playlist").value.trim();
      state.playlist_extended = document.getElementById("playlistExtended").value.trim();
      state.playlist_fifo = document.getElementById("playlistFifo").value.trim();
      state.mpv_out = document.getElementById("mpvOut").value.trim();
      state.panscan = document.getElementById("panscan").value.trim();
      state.video_rotate = document.getElementById("videoRotate").value.trim();
      state.flags.no_video = document.getElementById("flagNoVideo").checked;
      state.flags.no_panes = document.getElementById("flagNoPanes").checked;
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
      const queuePaths = document.getElementById("videoList").value
        .split("\n")
        .map(v => v.trim())
        .filter(Boolean);
      if (queueCtx && queueCtx.editable) {
        queueCtx.apply(queuePaths);
      }
      state.mpv_opts = buildMpvOptsFromControls();
      ensurePaneCommands(state);
      const treeRoles = [];
      splitTreeCollectRoles(normalizeSplitTreeState(), treeRoles);
      if (treeRoles.length && (treeRoles.length !== state.pane_count + 1 || Math.max(...treeRoles) !== state.pane_count)) {
        state.splitTreeModel = presetTreeFromState(state);
        syncSplitTreeState();
      }
      if (state.pane_commands.length !== previousPaneCount) renderPaneList(state);
      renderLayoutSuggestions();
      renderPlaylistEditor();
      renderStudioBoard();
      renderStudioInspector();
      applyPreviewGeometry();
    }

    function fillForm(nextState, configPath, nextRawConfig) {
      state = nextState;
      rawConfigText = nextRawConfig;
      pendingNewPaneIndexes = new Set();
      playlistTargetRole = 0;
      ensurePaneCommands(state);
      state.splitTreeModel = parseSplitTreeSpec(state.split_tree || "");
      document.getElementById("configPath").textContent = `Config: ${configPath}`;
      document.getElementById("connector").value = state.connector || "";
      document.getElementById("mode").value = state.mode || "";
      document.getElementById("rotation").value = String(state.rotation || 0);
      document.getElementById("fontSize").value = String(state.font_size || 18);
      document.getElementById("rightFrac").value = String(state.right_frac || 33);
      document.getElementById("paneSplit").value = String(state.pane_split || 50);
      document.getElementById("videoFrac").value = String(state.video_frac || 0);
      document.getElementById("paneCount").value = String(state.pane_count || 2);
      document.getElementById("layout").value = state.layout || "stack";
      document.getElementById("roles").value = state.roles || "";
      document.getElementById("fsCycleSec").value = String(state.fs_cycle_sec || 5);
      document.getElementById("playlist").value = state.playlist || "";
      document.getElementById("playlistExtended").value = state.playlist_extended || "";
      document.getElementById("playlistFifo").value = state.playlist_fifo || "";
      document.getElementById("mpvOut").value = state.mpv_out || "";
      document.getElementById("panscan").value = state.panscan || "";
      document.getElementById("videoRotate").value = state.video_rotate || "";
      document.getElementById("videoList").value = (state.video_paths || []).join("\n");
      const mpvGroups = parseMpvOptionGroups(state.mpv_opts || []);
      document.getElementById("mpvAudioMode").value = mpvGroups.audioMode || "";
      document.getElementById("mpvShaders").value = mpvGroups.shaders.join("\n");
      document.getElementById("mpvOpts").value = mpvGroups.other.join("\n");
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
      renderLayoutSuggestions();
      renderPaneList(state);
      renderPlaylistEditor();
      renderStudioBoard();
      renderStudioInspector();
      applyPreviewGeometry();
    }

    async function loadState() {
      const response = await fetch("/api/state");
      if (!response.ok) throw new Error("Failed to load state");
      const payload = await response.json();
      fillForm(payload.state, payload.config_path, payload.raw_config);
      await refreshSnapshot();
      scheduleLivePreview();
      setStatus(`Loaded ${payload.config_path}`, false);
    }

    async function saveState() {
      syncFormToState();
      const response = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Failed to save config");
      fillForm(payload.state, payload.config_path, payload.raw_config);
      await refreshSnapshot();
      scheduleLivePreview();
      setStatus(`Saved ${payload.config_path}. kms_mosaic will reload on file change.`, false);
    }

    async function saveRawConfig() {
      const rawConfig = document.getElementById("rawConfig").value;
      const response = await fetch("/api/raw_config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_config: rawConfig })
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Failed to save raw config");
      fillForm(payload.state, payload.config_path, payload.raw_config);
      await refreshSnapshot();
      scheduleLivePreview();
      setStatus(`Saved raw config to ${payload.config_path}.`, false);
    }

    function setStatus(message, isError) {
      statusEl.textContent = message;
      statusEl.className = `status${isError ? " error" : ""}`;
    }

    function currentLivePreviewDelay() {
      return 120;
    }

    function scheduleLivePreview() {
      if (livePreviewTimer) {
        clearTimeout(livePreviewTimer);
        livePreviewTimer = null;
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
    document.getElementById("addPaneBtn").addEventListener("click", () => {
      addPane();
      setStatus("Added a pane by splitting the selected slot.", false);
    });
    document.getElementById("removePaneBtn").addEventListener("click", () => {
      if (selectedRole === 0) {
        setStatus("Select a terminal pane first.", true);
        return;
      }
      removeSelectedPane();
      setStatus("Removed the selected terminal pane.", false);
    });
    addQueueItemBtn.addEventListener("click", () => {
      addQueueItem();
      setStatus("Added a new queue entry.", false);
    });
    document.getElementById("splitVerticalBtn").addEventListener("click", () => {
      if (!splitSelectedRole("col")) {
        setStatus("Could not split the selected pane.", true);
        return;
      }
      setStatus("Split the selected pane vertically.", false);
    });
    document.getElementById("splitHorizontalBtn").addEventListener("click", () => {
      if (!splitSelectedRole("row")) {
        setStatus("Could not split the selected pane.", true);
        return;
      }
      setStatus("Split the selected pane horizontally.", false);
    });
    document.getElementById("rotatePanelsCcwBtn").addEventListener("click", () => {
      if (!rotatePanels("ccw")) {
        setStatus("Could not rotate panels counterclockwise.", true);
        return;
      }
      setStatus("Rotated panels counterclockwise.", false);
    });
    document.getElementById("rotatePanelsCwBtn").addEventListener("click", () => {
      if (!rotatePanels("cw")) {
        setStatus("Could not rotate panels clockwise.", true);
        return;
      }
      setStatus("Rotated panels clockwise.", false);
    });
    previewImage.addEventListener("load", () => {
      livePreviewInFlight = false;
      applyPreviewGeometry();
    });
    previewImage.addEventListener("error", () => {
      livePreviewInFlight = false;
    });
    window.addEventListener("resize", () => {
      applyPreviewGeometry();
      renderStudioBoard();
    });
    [
      "connector","mode","rotation","fontSize","rightFrac","paneSplit",
      "videoFrac","paneCount","layout","roles","fsCycleSec","playlist",
      "playlistExtended","playlistFifo","mpvOut","panscan","videoRotate",
      "videoList","mpvAudioMode","mpvShaders","mpvOpts","extraLines","flagNoVideo","flagNoPanes",
      "flagSmooth","flagLoop","flagLoopPlaylist","flagShuffle","flagAtomic",
      "flagAtomicNonblock","flagGlFinish","flagNoOsd"
    ].forEach(id => {
      document.getElementById(id).addEventListener("input", () => syncFormToState());
      document.getElementById(id).addEventListener("change", () => syncFormToState());
    });
    loadState().catch(err => setStatus(err.message, true));
    window.addEventListener("beforeunload", () => stopLivePreviewStream());
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "KMSMosaicWeb/0.1"

    @property
    def app_config(self) -> WebConfig:
        return self.server.app_config  # type: ignore[attr-defined]

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
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".kms_mosaic.", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_path, path)

    def _read_state(self) -> dict[str, Any]:
        path = self.app_config.config_path
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return parse_config_text(text)

    def _read_raw_config(self) -> str:
        path = self.app_config.config_path
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _request_snapshot(self) -> bytes:
        request_path = self.app_config.snapshot_request_path
        output_path = self.app_config.snapshot_output_path
        output_mtime = output_path.stat().st_mtime_ns if output_path.exists() else 0
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(f"{time.time_ns()}\n", encoding="utf-8")

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if output_path.exists():
                new_mtime = output_path.stat().st_mtime_ns
                if new_mtime > output_mtime:
                    return output_path.read_bytes()
            time.sleep(0.05)
        raise TimeoutError("Timed out waiting for kms_mosaic snapshot")

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
        interval_sec = max(0.1, min(interval_ms / 1000.0, 5.0))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        try:
            while True:
                frame = self._request_snapshot()
                self.wfile.write(len(frame).to_bytes(4, "big"))
                self.wfile.write(frame)
                self.wfile.flush()
                time.sleep(interval_sec)
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

        if parsed.path == "/api/snapshot.bmp":
            try:
                data = self._request_snapshot()
            except Exception as exc:  # pragma: no cover
                self._send_json({"error": str(exc)}, status=500)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/bmp")
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
            self._send_json({"error": "Not found"}, status=404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
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


def parse_args() -> WebConfig:
    parser = argparse.ArgumentParser(description="Web UI for KMS Mosaic")
    parser.add_argument("--config", default=default_config_path(), help="Config file to edit")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8787, help="Bind port")
    args = parser.parse_args()
    return WebConfig(
        config_path=Path(args.config),
        host=args.host,
        port=args.port,
        snapshot_request_path=Path("/tmp/kms_mosaic_snapshot.request"),
        snapshot_output_path=Path("/tmp/kms_mosaic_snapshot.bmp"),
        thumb_cache_dir=Path("/tmp/kms_mosaic_web_thumbs"),
    )


def main() -> int:
    app_config = parse_args()
    server = ThreadingHTTPServer((app_config.host, app_config.port), Handler)
    server.app_config = app_config  # type: ignore[attr-defined]
    print(f"KMS Mosaic web UI serving {app_config.config_path} on http://{app_config.host}:{app_config.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
