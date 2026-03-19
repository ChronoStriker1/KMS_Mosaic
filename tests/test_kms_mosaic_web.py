import sys
import textwrap
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import kms_mosaic_web  # noqa: E402


REMOTE_VIDEO_URL = (
    "https://hydrusapi.cstriker.us/get_files/file"
    "?file_id=196226114"
    "&Hydrus-Client-API-Access-Key=908ffd8d0cd415215ecf7642e4d9440d9746a22b49a1e72a257943832daf8d67"
    "&download=false"
)


class KmsMosaicWebConfigTests(unittest.TestCase):
    def test_unified_config_with_legacy_comment_keys_does_not_reenter_legacy_mode(self) -> None:
        text = textwrap.dedent(
            """
            --pane-count 2
            --pane-media 1
            --pane-video 1 /media/main.mp4
            --pane 2 htop
            --split-tree 'col:50(0,1)'
            --roles 10
            # kms_mosaic_web_state {"selected_role":1,"focused_role":0,"fullscreen_role":1}
            """
        ).strip()

        parsed = kms_mosaic_web.parse_config_text(text)

        self.assertEqual(parsed["pane_count"], 2)
        self.assertEqual(parsed["pane_types"][:2], ["mpv", "terminal"])
        self.assertEqual(parsed["pane_video_paths"][0], ["/media/main.mp4"])
        self.assertEqual(parsed["pane_commands"][1], "htop")
        self.assertEqual(parsed["split_tree"], "col:50(0,1)")
        self.assertEqual(parsed["roles"], "10")
        self.assertEqual(parsed["focus_pane"], 0)
        self.assertEqual(parsed["fullscreen_pane"], 1)
        self.assertEqual(parsed["selected_pane"], 1)

    def test_role_zero_ui_paths_use_unified_pane_zero_fields(self) -> None:
        html = kms_mosaic_web.HTML

        queue_ctx = re.search(
            r"function queueEditorContext\(\) \{.*?\n    function isRemoteMediaUrl",
            html,
            re.S,
        )
        self.assertIsNotNone(queue_ctx)
        queue_ctx_text = queue_ctx.group(0)
        self.assertIn("state.pane_mpv_opts?.[0]", queue_ctx_text)
        self.assertIn("state.pane_video_paths?.[0]", queue_ctx_text)
        self.assertNotIn("state.video_paths = paths.slice()", queue_ctx_text)

        sync_main = re.search(
            r"function syncMainInspectorMpvOpts\(\) \{.*?\n    function syncInspectorPaneMpvOpts",
            html,
            re.S,
        )
        self.assertIsNotNone(sync_main)
        sync_main_text = sync_main.group(0)
        self.assertIn("state.pane_mpv_opts[0] = buildMpvOptsFromParts", sync_main_text)
        self.assertIn("state.pane_panscan[0] = panscanEl.value", sync_main_text)
        self.assertNotIn("state.mpv_opts =", sync_main_text)
        self.assertNotIn("state.panscan =", sync_main_text)

        inspector = re.search(
            r"function renderStudioInspector\(\) \{.*?\n    function renderPlaylistEditor",
            html,
            re.S,
        )
        self.assertIsNotNone(inspector)
        inspector_text = inspector.group(0)
        self.assertIn("parseMpvOptionGroups(state.pane_mpv_opts?.[0] || [])", inspector_text)
        self.assertIn("String(state.pane_panscan?.[0] || \"\")", inspector_text)
        self.assertNotIn("parseMpvOptionGroups(state.mpv_opts || [])", inspector_text)
        self.assertNotIn("String(state.panscan || \"\")", inspector_text)

        fill_form = re.search(
            r"function fillForm\(nextState, configPath, nextRawConfig\) \{.*?\n    async function loadState",
            html,
            re.S,
        )
        self.assertIsNotNone(fill_form)
        fill_form_text = fill_form.group(0)
        self.assertIn("const queueCtx = queueEditorContext();", fill_form_text)
        self.assertIn("queueField.value = (queueCtx?.paths || []).join(\"\\n\");", fill_form_text)
        self.assertNotIn("queueField.value = (state.video_paths || []).join(\"\\n\")", fill_form_text)

    def test_browser_role_helpers_use_unified_pane_indexing(self) -> None:
        html = kms_mosaic_web.HTML

        visibility = re.search(
            r"function visibilityModeHidesRole\(nextState, role\) \{.*?\n    function buildStudioSlots",
            html,
            re.S,
        )
        self.assertIsNotNone(visibility)
        visibility_text = visibility.group(0)
        self.assertIn("const paneType = nextState?.pane_types?.[role] || \"terminal\";", visibility_text)
        self.assertIn("for (let role = 0; role < paneCount; role += 1)", visibility_text)
        self.assertNotIn("const roleCount = 1 + paneCount;", visibility_text)
        self.assertNotIn("role === 0 ? \"mpv\" : (nextState?.pane_types?.[role - 1] || \"terminal\")", visibility_text)

        role_helpers = re.search(
            r"function roleType\(role\) \{.*?\n    function selectedPaneQueueField",
            html,
            re.S,
        )
        self.assertIsNotNone(role_helpers)
        role_helpers_text = role_helpers.group(0)
        self.assertIn("return state?.pane_types?.[role] === \"mpv\" ? \"video\" : \"terminal\";", role_helpers_text)
        self.assertNotIn("state?.pane_types?.[role - 1]", role_helpers_text)

        selected_pane = re.search(
            r"function selectedPaneType\(\) \{.*?\n    function selectRole",
            html,
            re.S,
        )
        self.assertIsNotNone(selected_pane)
        selected_pane_text = selected_pane.group(0)
        self.assertIn("return state.pane_types?.[selectedRole] || \"terminal\";", selected_pane_text)
        self.assertNotIn("selectedRole === 0", selected_pane_text)
        self.assertNotIn("selectedRole - 1", selected_pane_text)

        roles_layout = re.search(
            r"function parseRolesString\(nextState\) \{.*?\n    function tileRects",
            html,
            re.S,
        )
        self.assertIsNotNone(roles_layout)
        roles_layout_text = roles_layout.group(0)
        self.assertIn("const roleCount = Math.max(1, Number(nextState.pane_count || 2));", roles_layout_text)
        self.assertNotIn("const roleCount = 1 + Math.max(1, Number(nextState.pane_count || 2));", roles_layout_text)

        layout_state = re.search(
            r"function visibilityLayoutForState\(nextState = state\) \{.*?\n    function parseMpvOptionGroups",
            html,
            re.S,
        )
        self.assertIsNotNone(layout_state)
        layout_state_text = layout_state.group(0)
        self.assertIn("const roleCount = Math.max(1, Number(nextState?.pane_count || 2));", layout_state_text)
        self.assertNotIn("const roleCount = 1 + Math.max(1, Number(nextState?.pane_count || 2));", layout_state_text)

        preset_tree = re.search(
            r"function presetTreeFromState\(nextState\) \{.*?\n    function ensureSplitTreeModel",
            html,
            re.S,
        )
        self.assertIsNotNone(preset_tree)
        preset_tree_text = preset_tree.group(0)
        self.assertIn("const roles = Array.from({ length: paneCount }, (_, i) => i);", preset_tree_text)
        self.assertNotIn("const roles = Array.from({ length: paneCount + 1 }, (_, i) => i);", preset_tree_text)

        board = re.search(
            r"function renderStudioBoard\(\) \{.*?\n    function playlistHoverOverlayBounds",
            html,
            re.S,
        )
        self.assertIsNotNone(board)
        board_text = board.group(0)
        self.assertIn("const paneType = state.pane_types?.[role] || \"terminal\";", board_text)
        self.assertNotIn("role === 0 ? \"mpv\" : (state.pane_types?.[role - 1] || \"terminal\")", board_text)

    def test_parse_legacy_config_converts_global_media_slot_into_pane_zero(self) -> None:
        text = textwrap.dedent(
            """
            --layout 2x1
            --pane-count 3
            --split-tree 'col:60(0,row:50(1,col:50(2,3)))'
            --roles CDBA
            --video /media/main.mp4
            --mpv-opt audio=no
            --panscan 1.25
            --pane-a htop
            --pane-b 'tail -f /tmp/app.log'
            --pane-media 3
            --pane-video 3 /media/pane3.mp4
            --pane-mpv-opt 3 mute=yes
            # kms_mosaic_web_state {"selected_role":3,"focused_role":0,"fullscreen_role":2}
            """
        ).strip()

        parsed = kms_mosaic_web.parse_config_text(text)

        self.assertEqual(parsed["pane_count"], 4)
        self.assertEqual(parsed["pane_types"][:4], ["mpv", "terminal", "terminal", "mpv"])
        self.assertEqual(parsed["pane_video_paths"][0], ["/media/main.mp4"])
        self.assertEqual(parsed["pane_mpv_opts"][0], ["audio=no"])
        self.assertEqual(parsed["pane_panscan"][0], "1.25")
        self.assertEqual(parsed["pane_commands"][1], "htop")
        self.assertEqual(parsed["pane_commands"][2], "tail -f /tmp/app.log")
        self.assertEqual(parsed["pane_video_paths"][3], ["/media/pane3.mp4"])
        self.assertEqual(parsed["pane_mpv_opts"][3], ["mute=yes"])
        self.assertEqual(parsed["split_tree"], "col:60(0,row:50(1,col:50(2,3)))")
        self.assertEqual(parsed["roles"], "0321")
        self.assertEqual(parsed["focus_pane"], 0)
        self.assertEqual(parsed["fullscreen_pane"], 2)
        self.assertEqual(parsed["selected_pane"], 3)
        self.assertEqual(parsed["video_paths"], [])
        self.assertEqual(parsed["mpv_opts"], [])
        self.assertEqual(parsed["panscan"], "")

    def test_remote_pane_video_url_survives_config_round_trip(self) -> None:
        state = kms_mosaic_web.empty_state()
        state["pane_count"] = 1
        state["pane_types"] = ["mpv"]
        state["pane_commands"] = [""]
        state["pane_video_paths"] = [[REMOTE_VIDEO_URL]]

        text = kms_mosaic_web.serialize_config(state)
        parsed = kms_mosaic_web.parse_config_text(text)

        self.assertEqual(parsed["pane_video_paths"][0], [REMOTE_VIDEO_URL])

    def test_legacy_config_round_trips_through_unified_save_path(self) -> None:
        legacy_text = textwrap.dedent(
            """
            --layout 2x1
            --pane-count 3
            --split-tree 'col:60(0,row:50(1,col:50(2,3)))'
            --roles CDBA
            --video /media/main.mp4
            --playlist /playlists/main.m3u
            --mpv-out drm
            --video-rotate 90
            --mpv-opt audio=no
            --panscan 1.25
            --pane-a htop
            --pane-b 'tail -f /tmp/app.log'
            --pane-media 3
            --pane-video 3 /media/pane3.mp4
            --pane-mpv-opt 3 mute=yes
            # kms_mosaic_web_state {"selected_role":3,"focused_role":0,"fullscreen_role":2}
            """
        ).strip()

        parsed = kms_mosaic_web.parse_config_text(legacy_text)
        saved_text = kms_mosaic_web.build_config_text(parsed)
        reparsed = kms_mosaic_web.parse_config_text(saved_text)

        self.assertIn("--pane-media 1", saved_text)
        self.assertIn("--pane-video 1 /media/main.mp4", saved_text)
        self.assertIn("--pane-playlist 1 /playlists/main.m3u", saved_text)
        self.assertIn("--pane-mpv-opt 1 audio=no", saved_text)
        self.assertIn("--pane-panscan 1 1.25", saved_text)
        self.assertNotIn("\n--video /media/main.mp4", saved_text)
        self.assertNotIn("\n--playlist /playlists/main.m3u", saved_text)
        self.assertNotIn("\n--mpv-opt audio=no", saved_text)
        self.assertNotIn("\n--panscan 1.25", saved_text)
        self.assertEqual(reparsed["pane_count"], 4)
        self.assertEqual(reparsed["split_tree"], "col:60(0,row:50(1,col:50(2,3)))")
        self.assertEqual(reparsed["roles"], "0321")
        self.assertEqual(reparsed["focus_pane"], 0)
        self.assertEqual(reparsed["fullscreen_pane"], 2)
        self.assertEqual(reparsed["selected_pane"], 3)
        self.assertEqual(reparsed["pane_video_paths"][0], ["/media/main.mp4"])
        self.assertEqual(reparsed["pane_playlists"][0], "/playlists/main.m3u")
        self.assertEqual(reparsed["pane_mpv_outs"][0], "drm")
        self.assertEqual(reparsed["pane_video_rotate"][0], "90")
        self.assertEqual(reparsed["pane_mpv_opts"][0], ["audio=no"])
        self.assertEqual(reparsed["pane_video_paths"][3], ["/media/pane3.mp4"])
        self.assertEqual(reparsed["pane_mpv_opts"][3], ["mute=yes"])

    def test_invalid_legacy_split_tree_falls_back_without_dropping_panes(self) -> None:
        legacy_text = textwrap.dedent(
            """
            --pane-count 2
            --video /media/main.mp4
            --pane-a htop
            --pane-b 'tail -f /tmp/app.log'
            --split-tree 'col:50(0,9)'
            """
        ).strip()

        parsed = kms_mosaic_web.parse_config_text(legacy_text)

        self.assertEqual(parsed["pane_count"], 3)
        self.assertEqual(parsed["pane_types"][:3], ["mpv", "terminal", "terminal"])
        self.assertEqual(parsed["pane_video_paths"][0], ["/media/main.mp4"])
        self.assertEqual(parsed["pane_commands"][1], "htop")
        self.assertEqual(parsed["pane_commands"][2], "tail -f /tmp/app.log")
        self.assertEqual(parsed["split_tree"], "col:50(0,col:50(1,2))")

    def test_unknown_pane_type_preserves_raw_settings_and_media_on_round_trip(self) -> None:
        legacy_text = textwrap.dedent(
            """
            --pane-count 1
            --video /media/main.mp4
            --mpv-opt audio=no
            # kms_mosaic_web_state {"pane_types":["jukebox","terminal"],"pane_type_settings":{"0":{"station":"lofi","volume":"11"}}}
            """
        ).strip()

        parsed = kms_mosaic_web.parse_config_text(legacy_text)
        saved_text = kms_mosaic_web.build_config_text(parsed)
        reparsed = kms_mosaic_web.parse_config_text(saved_text)

        self.assertEqual(parsed["pane_types"][:2], ["terminal", "terminal"])
        self.assertEqual(parsed["pane_type_raw"][:2], ["jukebox", ""])
        self.assertEqual(parsed["pane_type_settings"][0], {"station": "lofi", "volume": "11"})
        self.assertEqual(reparsed["pane_type_raw"][:2], ["jukebox", ""])
        self.assertEqual(reparsed["pane_type_settings"][0], {"station": "lofi", "volume": "11"})
        self.assertEqual(reparsed["pane_video_paths"][0], ["/media/main.mp4"])
        self.assertEqual(reparsed["pane_mpv_opts"][0], ["audio=no"])

    def test_malformed_pane_type_settings_metadata_is_ignored_safely(self) -> None:
        text = textwrap.dedent(
            """
            --pane-count 1
            --video /media/main.mp4
            # kms_mosaic_web_state {"pane_types":["jukebox","terminal"],"pane_type_settings":{"0":"bad","1":["still","bad"]}}
            """
        ).strip()

        parsed = kms_mosaic_web.parse_config_text(text)

        self.assertEqual(parsed["pane_count"], 2)
        self.assertEqual(parsed["pane_type_raw"][:2], ["jukebox", ""])
        self.assertEqual(parsed["pane_type_settings"][:2], [{}, {}])
        self.assertEqual(parsed["pane_video_paths"][0], ["/media/main.mp4"])


if __name__ == "__main__":
    unittest.main()
