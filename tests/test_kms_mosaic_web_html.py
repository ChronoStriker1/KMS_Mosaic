import importlib.util
import pathlib
import re
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "kms_mosaic_web.py"
PLUGIN_PAGE_PATH = REPO_ROOT / "unraid-plugin" / "package-root" / "usr" / "local" / "emhttp" / "plugins" / "kms.mosaic" / "KMSMosaic.page"


def load_module():
    spec = importlib.util.spec_from_file_location("kms_mosaic_web", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class KmsMosaicWebHtmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_studio_board_does_not_rotate_pane_positions_a_second_time(self):
        html = self.module.HTML
        match = re.search(
            r"function studioRotationDegrees\(\)\s*\{\s*(?P<body>.*?)\s*\}",
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertIn("return 0;", match.group("body"))

        point_match = re.search(
            r"function displayPointToLogicalPoint\(point\)\s*\{\s*(?P<body>.*?)\s*\}",
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(point_match)
        self.assertIn("studioRotationDegrees()", point_match.group("body"))
        self.assertNotIn("normalizedRotationDegrees()", point_match.group("body"))
        self.assertIn("100 - point.y", point_match.group("body"))

        rect_match = re.search(
            r"function transformStudioPaneRect\(rect\)\s*\{\s*(?P<body>.*?)\s*\}",
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(rect_match)
        self.assertIn("100 - (rotated.y + rotated.h)", rect_match.group("body"))

        aspect_match = re.search(
            r"function applyStudioGeometry\(\)\s*\{\s*(?P<body>.*?)\s*\}",
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(aspect_match)
        self.assertIn("normalizedRotationDegrees()", aspect_match.group("body"))

        ancestor_match = re.search(
            r"function decorateSplitTreeAncestor\(entry, areaMap\)\s*\{\s*(?P<body>.*?)\s*\}",
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(ancestor_match)
        self.assertIn("const total = studioRotationDegrees();", ancestor_match.group("body"))
        self.assertNotIn("const total = normalizedRotationDegrees();", ancestor_match.group("body"))

    def test_selected_studio_pane_exposes_visible_resize_grips(self):
        html = self.module.HTML
        self.assertIn('.studio-card.selected .studio-resize-handle {', html)
        self.assertIn('.studio-resize-handle[data-edge="left"]::after,', html)
        self.assertIn('.studio-resize-handle[data-edge="top"]::after,', html)
        self.assertIn('box-shadow: 0 0 0 2px rgba(87, 31, 16, 0.18);', html)

    def test_preview_webrtc_prefers_h264_first_in_browser(self):
        html = self.module.HTML
        match = re.search(
            r"const codecRank = \(mimeType\) => \{(?P<body>.*?)\};",
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertLess(body.index('mimeType === "video/H264"'), body.index('mimeType === "video/VP8"'))
        self.assertLess(body.index('mimeType === "video/VP8"'), body.index('mimeType === "video/VP9"'))

    def test_visibility_mode_is_not_written_back_into_legacy_runtime_flags(self):
        html = self.module.HTML
        normalize_match = re.search(
            r"function normalizeVisibilityFlags\(\) \{(?P<body>.*?)\n    \}",
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(normalize_match)
        normalize_body = normalize_match.group("body")
        self.assertIn("state.visibility_mode = mode;", normalize_body)
        self.assertNotIn("state.flags.no_video", normalize_body)
        self.assertNotIn("state.flags.no_panes", normalize_body)

        set_match = re.search(
            r"async function setVisibilityMode\(mode\) \{(?P<body>.*?)\n    \}",
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(set_match)
        set_body = set_match.group("body")
        self.assertIn("state.visibility_mode = nextMode;", set_body)
        self.assertNotIn("state.flags.no_video", set_body)
        self.assertNotIn("state.flags.no_panes", set_body)
        self.assertNotIn("flagNoVideo", set_body)
        self.assertNotIn("flagNoPanes", set_body)

    def test_editor_removes_audio_and_loop_controls(self):
        html = self.module.HTML
        self.assertNotIn("Audio Output", html)
        self.assertNotIn("Mute", html)
        self.assertNotIn("Loop Current File", html)
        self.assertNotIn("Loop File", html)
        self.assertNotIn("Loop Playlist", html)
        self.assertNotIn('id="flagLoop"', html)
        self.assertNotIn('id="flagLoopPlaylist"', html)

    def test_editor_exposes_display_selector_and_loads_connector_options(self):
        html = self.module.HTML
        self.assertIn("<label>Display", html)
        self.assertIn('id="connector"', html)
        self.assertIn('fetch("/api/connectors")', html)
        self.assertIn("async function loadConnectorOptions()", html)
        self.assertIn("connectorEl.value = state.connector || \"\";", html)

    def test_plugin_page_keeps_visible_resize_grips_after_relocation(self):
        html = PLUGIN_PAGE_PATH.read_text(encoding="utf-8")
        self.assertIn('#kmsStudioPlaceholder .studio-card.selected .studio-resize-handle {', html)
        self.assertIn('filter: drop-shadow(0 0 10px rgba(87, 31, 16, 0.22)) !important;', html)
        self.assertIn('#kmsStudioPlaceholder .studio-resize-handle[data-edge="left"]::after,', html)
        self.assertIn('#kmsStudioPlaceholder .studio-resize-handle[data-edge="top"]::after,', html)
        self.assertIn('background: rgba(191, 98, 54, 0.96);', html)

    def test_plugin_page_moves_visibility_controls_into_service_action_row(self):
        html = PLUGIN_PAGE_PATH.read_text(encoding="utf-8")
        self.assertNotIn('Apply Settings', html)
        self.assertIn('id="kmsReloadConfigBtn"', html)
        self.assertIn('id="kmsVisibilityGroup"', html)
        self.assertIn('id="kmsVisibilityButtons"', html)
        self.assertLess(html.index('id="kmsReloadConfigBtn"'), html.index('id="kmsVisibilityGroup"'))
        self.assertIn('.kms-visibility-group {', html)
        self.assertIn('display: inline-flex;', html)

    def test_plugin_page_allows_advanced_checks_to_wrap_and_autosaves_plugin_settings(self):
        html = PLUGIN_PAGE_PATH.read_text(encoding="utf-8")
        self.assertIn('#kmsAdvancedPlaceholder .checks {', html)
        self.assertIn('display: flex;', html)
        self.assertIn('flex-wrap: wrap;', html)
        self.assertIn('#kmsAdvancedPlaceholder .check {', html)
        self.assertIn('flex: 1 1 220px;', html)
        self.assertIn('min-width: 220px;', html)
        for field_id in ['kmsServiceBoot', 'kmsWebBoot', 'kmsWebPort', 'kmsConfigPath']:
            self.assertIn(field_id, html)
        self.assertIn("['change', 'blur'].forEach((eventName) => {", html)
        self.assertIn('kmsMaybeAutoSavePluginSettings().catch(() => {});', html)

    def test_plugin_page_filters_unified_pane_model_from_raw_config_display(self):
        html = PLUGIN_PAGE_PATH.read_text(encoding="utf-8")
        self.assertIn('function kmsFilterRawConfigText(rawConfig)', html)
        self.assertIn("replace(/^\\s*--pane-model unified\\s*\\r?\\n?/gm, '')", html)
        self.assertIn('window.fillForm = function(state, configPath, rawConfig)', html)
        self.assertIn('kmsFilterRawConfigText(rawConfig)', html)
        self.assertIn('window.saveState = async function(...args)', html)
        self.assertIn('kmsSanitizeRawConfigField();', html)
        self.assertIn(".replaceAll('/api/connectors', `${kmsActionUrl}?action=backend_connectors`)", html)
        self.assertIn("if (typeof window.loadConnectorOptions === 'function') {", html)
        self.assertIn("await window.loadConnectorOptions();", html)


if __name__ == "__main__":
    unittest.main()
