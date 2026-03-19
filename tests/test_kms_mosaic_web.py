import sys
import textwrap
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


if __name__ == "__main__":
    unittest.main()
