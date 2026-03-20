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
    def test_integration_harness_exercises_role_zero_remove_path_and_checks_split_tree_alignment(self) -> None:
        integration_html = (ROOT / "tests" / "integration.test.html").read_text(encoding="utf-8")

        self.assertIn("const removeOk = typeof window.removeSelectedPane === 'function' && window.removeSelectedPane() === true;", integration_html)
        self.assertIn("const splitTreeAfterRemove = typeof window.normalizeSplitTreeState === 'function'", integration_html)
        self.assertIn("if (typeof window.splitTreeCollectRoles === 'function' && splitTreeAfterRemove)", integration_html)
        self.assertIn("const serializedTreeAfterRemove = typeof window.serializeSplitTree === 'function'", integration_html)
        self.assertIn("layoutAfterRemove.visibleRoles.length === 1", integration_html)
        self.assertIn("queueCtxAfterRemove.role === 0", integration_html)
        self.assertIn("const removePathOk = removeOk && paneCountAligned && treeAligned && arraysAligned && selectedStateAligned;", integration_html)

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
        self.assertIn("const paneIndex = selectedRole;", queue_ctx_text)
        self.assertIn("state.pane_mpv_opts?.[paneIndex]", queue_ctx_text)
        self.assertIn("state.pane_video_paths?.[paneIndex]", queue_ctx_text)
        self.assertNotIn("const paneIndex = selectedRole - 1;", queue_ctx_text)
        self.assertNotIn("if (selectedRole === 0)", queue_ctx_text)
        self.assertNotIn("state.video_paths = paths.slice()", queue_ctx_text)

        sync_pane = re.search(
            r"function syncInspectorPaneMpvOpts\(paneIndex\) \{.*?\n    function roleType",
            html,
            re.S,
        )
        self.assertIsNotNone(sync_pane)
        sync_pane_text = sync_pane.group(0)
        self.assertIn("state.pane_mpv_opts[paneIndex] = buildMpvOptsFromParts", sync_pane_text)
        self.assertIn("state.pane_panscan[paneIndex] = panscanEl.value", sync_pane_text)
        self.assertNotIn("state.mpv_opts =", sync_pane_text)
        self.assertNotIn("state.panscan =", sync_pane_text)

        inspector = re.search(
            r"function renderStudioInspector\(\) \{.*?\n    function renderPlaylistEditor",
            html,
            re.S,
        )
        self.assertIsNotNone(inspector)
        inspector_text = inspector.group(0)
        self.assertIn("const paneIndex = selectedRole;", inspector_text)
        self.assertIn("const paneType = state.pane_types?.[paneIndex] || \"terminal\";", inspector_text)
        self.assertIn("parseMpvOptionGroups(state.pane_mpv_opts?.[paneIndex] || [])", inspector_text)
        self.assertIn("String(state.pane_panscan?.[paneIndex] || \"\")", inspector_text)
        self.assertIn("const paneIndex = selectedRole;", inspector_text)
        self.assertNotIn("if (selectedRole === 0)", inspector_text)
        self.assertNotIn("parseMpvOptionGroups(state.mpv_opts || [])", inspector_text)
        self.assertNotIn("String(state.panscan || \"\")", inspector_text)
        self.assertIn("<label>Pane Type", inspector_text)

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

        role_name = re.search(
            r"function roleName\(role\) \{.*?\n    function visibilityModeForState",
            html,
            re.S,
        )
        self.assertIsNotNone(role_name)
        role_name_text = role_name.group(0)
        self.assertNotIn('return "Pane C";', role_name_text)
        self.assertIn('return `Pane ${String.fromCharCode(65 + role)}`;', role_name_text)

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
        self.assertIn("const roles = orderedRolesFromState(nextState);", preset_tree_text)
        self.assertIn("const [primaryRole, ...secondaryRoles] = roles;", preset_tree_text)
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

    def test_split_and_restore_helpers_use_zero_based_role_bounds(self) -> None:
        html = kms_mosaic_web.HTML

        restore_selected = re.search(
            r"function restoreSelectedRole\(nextState, snapshot\) \{.*?\n    function ensureSelectedRole",
            html,
            re.S,
        )
        self.assertIsNotNone(restore_selected)
        restore_text = restore_selected.group(0)
        self.assertIn("for (let role = 0; role < Number(nextState.pane_count || 0); role += 1)", restore_text)
        self.assertIn("snapshot.role >= 0", restore_text)
        self.assertNotIn("for (let role = 1; role <= Number(nextState.pane_count || 0); role += 1)", restore_text)
        self.assertNotIn("snapshot.role > 0", restore_text)

        ensure_selected = re.search(
            r"function ensureSelectedRole\(\) \{.*?\n    function selectedPaneType",
            html,
            re.S,
        )
        self.assertIsNotNone(ensure_selected)
        ensure_text = ensure_selected.group(0)
        self.assertIn("const maxRole = Math.max(-1, Number(state?.pane_count || 0) - 1);", ensure_text)
        self.assertNotIn("const maxRole = Math.max(0, Number(state?.pane_count || 0));", ensure_text)

        split_remove = re.search(
            r"function splitSelectedRole\(kind\) \{.*?\n    function waitForIceGatheringComplete",
            html,
            re.S,
        )
        self.assertIsNotNone(split_remove)
        split_remove_text = split_remove.group(0)
        self.assertIn("const newRole = Number(state.pane_count || 0);", split_remove_text)
        self.assertIn("state.pane_count = newRole + 1;", split_remove_text)
        self.assertIn("if (!state || selectedRole < 0 || Number(state.pane_count || 0) === 1) return false;", split_remove_text)
        self.assertIn("const paneIndex = selectedRole;", split_remove_text)
        self.assertIn("for (let i = role + 1; i < state.pane_count; i += 1)", split_remove_text)
        self.assertNotIn("const newRole = Number(state.pane_count || 0) + 1;", split_remove_text)
        self.assertNotIn("Number(state.pane_count || 0) <= 1", split_remove_text)
        self.assertNotIn("if (!state || selectedRole <= 0) return false;", split_remove_text)
        self.assertNotIn("const paneIndex = selectedRole - 1;", split_remove_text)
        self.assertNotIn("for (let i = role + 1; i <= state.pane_count; i += 1)", split_remove_text)
        self.assertNotIn("pendingNewPaneIndexes", split_remove_text)

        layout_actions = re.search(
            r"function selectedPaneLayoutActionsMarkup\(role\) \{.*?\n    function bindSelectedPaneLayoutActions",
            html,
            re.S,
        )
        self.assertIsNotNone(layout_actions)
        layout_actions_text = layout_actions.group(0)
        self.assertIn("const canRemove = Number(state?.pane_count || 0) > 1;", layout_actions_text)
        self.assertIn('${canRemove ? \'<button type=\"button\" class=\"secondary studio-remove-btn\" data-selected-pane-remove=\"true\">Remove Pane</button>\' : ""}', layout_actions_text)
        self.assertNotIn("role > 0", layout_actions_text)

    def test_unified_browser_helpers_use_pane_scoped_identity_rotation_and_tree_validation(self) -> None:
        html = kms_mosaic_web.HTML

        ensure_panes = re.search(
            r"function ensurePaneCommands\(nextState\) \{.*?\n    function skipTreeWs",
            html,
            re.S,
        )
        self.assertIsNotNone(ensure_panes)
        ensure_panes_text = ensure_panes.group(0)
        self.assertIn("nextState.pane_type_raw = Array.isArray(nextState.pane_type_raw) ? nextState.pane_type_raw.slice(0, count) : [];", ensure_panes_text)
        self.assertIn("nextState.pane_type_settings = Array.isArray(nextState.pane_type_settings)", ensure_panes_text)
        self.assertIn("nextState.pane_mpv_outs = Array.isArray(nextState.pane_mpv_outs) ? nextState.pane_mpv_outs.slice(0, count) : [];", ensure_panes_text)
        self.assertIn("nextState.pane_video_rotate = Array.isArray(nextState.pane_video_rotate) ? nextState.pane_video_rotate.slice(0, count) : [];", ensure_panes_text)
        self.assertIn("nextState.pane_panscan = Array.isArray(nextState.pane_panscan) ? nextState.pane_panscan.slice(0, count) : [];", ensure_panes_text)
        self.assertIn("while (nextState.pane_type_raw.length < count) nextState.pane_type_raw.push(\"\");", ensure_panes_text)
        self.assertIn("while (nextState.pane_type_settings.length < count) nextState.pane_type_settings.push({});", ensure_panes_text)
        self.assertIn("while (nextState.pane_mpv_outs.length < count) nextState.pane_mpv_outs.push(\"\");", ensure_panes_text)
        self.assertIn("while (nextState.pane_video_rotate.length < count) nextState.pane_video_rotate.push(\"\");", ensure_panes_text)
        self.assertIn("while (nextState.pane_panscan.length < count) nextState.pane_panscan.push(\"\");", ensure_panes_text)

        split_remove = re.search(
            r"function splitSelectedRole\(kind\) \{.*?\n    function waitForIceGatheringComplete",
            html,
            re.S,
        )
        self.assertIsNotNone(split_remove)
        split_remove_text = split_remove.group(0)
        self.assertIn("state.pane_type_raw.splice(paneIndex, 1);", split_remove_text)
        self.assertIn("state.pane_type_settings.splice(paneIndex, 1);", split_remove_text)
        self.assertIn("state.pane_mpv_outs.splice(paneIndex, 1);", split_remove_text)
        self.assertIn("state.pane_video_rotate.splice(paneIndex, 1);", split_remove_text)
        self.assertIn("state.pane_panscan.splice(paneIndex, 1);", split_remove_text)

        identity = re.search(
            r"function paneIdentityForRole\(nextState, role\) \{.*?\n    function paneIdentityEquals",
            html,
            re.S,
        )
        self.assertIsNotNone(identity)
        identity_text = identity.group(0)
        self.assertIn("const paneIndex = role;", identity_text)
        self.assertNotIn("if (role === 0)", identity_text)
        self.assertNotIn("const paneIndex = role - 1;", identity_text)
        self.assertNotIn("kind: \"main\"", identity_text)

        rotation = re.search(
            r"function configuredVideoRotationDegrees\(role = .*?\) \{.*?\n    function effectivePlaylistThumbRotationDegrees",
            html,
            re.S,
        )
        self.assertIsNotNone(rotation)
        rotation_text = rotation.group(0)
        self.assertIn("defaultMediaPaneRole()", rotation_text)
        self.assertIn("const resolvedRole = Number.isFinite(Number(role))", rotation_text)
        self.assertIn("const rawValue = state?.pane_video_rotate?.[Number(resolvedRole)] || \"0\";", rotation_text)
        self.assertIn("if (resolvedRole < 0) return 0;", rotation_text)
        self.assertNotIn("state?.video_rotate || \"0\"", rotation_text)
        self.assertNotIn("Number(role) > 0", rotation_text)
        self.assertNotIn("const paneIndex = Number(role) - 1;", rotation_text)

        sync_form = re.search(
            r"function syncFormToState\(\) \{.*?\n    function fillForm",
            html,
            re.S,
        )
        self.assertIsNotNone(sync_form)
        sync_form_text = sync_form.group(0)
        self.assertIn("const normalizedTreeRoles = Array.from(new Set(treeRoles.map((role) => Number(role)).filter(Number.isFinite))).sort((a, b) => a - b);", sync_form_text)
        self.assertIn("normalizedTreeRoles.length !== state.pane_count || normalizedTreeRoles.some((role, index) => role !== index)", sync_form_text)
        self.assertNotIn("treeRoles.length !== state.pane_count + 1 || Math.max(...treeRoles) !== state.pane_count", sync_form_text)

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

    def test_stale_extra_split_tree_leaves_do_not_force_legacy_mode(self) -> None:
        text = textwrap.dedent(
            """
            --pane-count 2
            --pane-media 1
            --pane-video 1 /media/main.mp4
            --pane 2 htop
            --split-tree 'col:50(0,row:50(1,2))'
            """
        ).strip()

        parsed = kms_mosaic_web.parse_config_text(text)

        self.assertEqual(parsed["pane_count"], 2)
        self.assertEqual(parsed["pane_types"][:2], ["mpv", "terminal"])
        self.assertEqual(parsed["pane_video_paths"][0], ["/media/main.mp4"])
        self.assertEqual(parsed["pane_commands"][1], "htop")
        self.assertEqual(parsed["split_tree"], "col:50(0,1)")

    def test_stale_saved_selection_metadata_does_not_allocate_phantom_panes(self) -> None:
        text = textwrap.dedent(
            """
            --pane-count 1
            --pane-media 1
            --pane-video 1 /media/main.mp4
            # kms_mosaic_web_state {"selected_pane":9,"focus_pane":8,"fullscreen_pane":7,"selected_role":6,"focused_role":5,"fullscreen_role":4}
            """
        ).strip()

        parsed = kms_mosaic_web.parse_config_text(text)

        self.assertEqual(parsed["pane_count"], 1)
        self.assertEqual(parsed["pane_types"][:1], ["mpv"])
        self.assertEqual(parsed["pane_video_paths"][0], ["/media/main.mp4"])
        self.assertEqual(parsed["selected_pane"], -1)
        self.assertEqual(parsed["focus_pane"], -1)
        self.assertEqual(parsed["fullscreen_pane"], -1)

    def test_legacy_split_tree_only_config_still_converts_to_unified_pane_zero(self) -> None:
        text = textwrap.dedent(
            """
            --pane-count 2
            --pane-a htop
            --pane-b 'tail -f /tmp/app.log'
            --split-tree 'col:50(0,row:50(1,2))'
            """
        ).strip()

        parsed = kms_mosaic_web.parse_config_text(text)

        self.assertEqual(parsed["pane_count"], 3)
        self.assertEqual(parsed["pane_types"][:3], ["mpv", "terminal", "terminal"])
        self.assertEqual(parsed["pane_commands"][:3], ["", "htop", "tail -f /tmp/app.log"])
        self.assertEqual(parsed["split_tree"], "col:50(0,row:50(1,2))")

    def test_preset_tree_handles_one_pane_layouts_with_single_leaf(self) -> None:
        html = kms_mosaic_web.HTML

        preset_tree = re.search(
            r"function presetTreeFromState\(nextState\) \{.*?\n    function ensureSplitTreeModel",
            html,
            re.S,
        )
        self.assertIsNotNone(preset_tree)
        preset_tree_text = preset_tree.group(0)
        self.assertIn("if (paneCount <= 1) return { leaf: true, role: 0 };", preset_tree_text)
        self.assertNotIn("first: balancedTreeForRoles(paneRoles, true), second: { leaf: true, role: 0 }", preset_tree_text.split("if (paneCount <= 1) return { leaf: true, role: 0 };", 1)[0])

    def test_selection_metadata_round_trips_and_browser_reload_prefers_saved_selected_pane(self) -> None:
        state = kms_mosaic_web.empty_state()
        state["pane_count"] = 2
        state["pane_types"] = ["mpv", "terminal"]
        state["pane_commands"] = ["", "htop"]
        state["pane_video_paths"] = [["/media/main.mp4"], []]
        state["selected_pane"] = 1
        state["focus_pane"] = 0
        state["fullscreen_pane"] = 1

        text = kms_mosaic_web.build_config_text(state)
        reparsed = kms_mosaic_web.parse_config_text(text)

        self.assertIn('"selected_pane":1', text)
        self.assertIn('"focus_pane":0', text)
        self.assertIn('"fullscreen_pane":1', text)
        self.assertEqual(reparsed["selected_pane"], 1)
        self.assertEqual(reparsed["focus_pane"], 0)
        self.assertEqual(reparsed["fullscreen_pane"], 1)

        html = kms_mosaic_web.HTML
        select_role = re.search(
            r"function selectRole\(role\) \{.*?\n    function parseRolesString",
            html,
            re.S,
        )
        self.assertIsNotNone(select_role)
        select_role_text = select_role.group(0)
        self.assertIn("if (state) state.selected_pane = selectedRole;", select_role_text)

        fill_form = re.search(
            r"function fillForm\(nextState, configPath, nextRawConfig\) \{.*?\n    async function loadState",
            html,
            re.S,
        )
        self.assertIsNotNone(fill_form)
        fill_form_text = fill_form.group(0)
        self.assertIn("const parsedSelection = Number(state?.selected_pane);", fill_form_text)
        self.assertIn("selectedRole = Number.isFinite(parsedSelection) ? parsedSelection : restoreSelectedRole(state, previousSelection);", fill_form_text)

    def test_pane_media_parser_clears_seeded_terminal_commands(self) -> None:
        text = textwrap.dedent(
            """
            --pane-count 2
            --pane-media 1
            --pane-video 1 /media/main.mp4
            --pane-playlist 2 /media/playlist.m3u
            --pane-mpv-opt 2 mute=yes
            """
        ).strip()

        parsed = kms_mosaic_web.parse_config_text(text)

        self.assertEqual(parsed["pane_count"], 2)
        self.assertEqual(parsed["pane_types"][:2], ["mpv", "mpv"])
        self.assertEqual(parsed["pane_commands"][:2], ["", ""])
        self.assertEqual(parsed["pane_video_paths"][0], ["/media/main.mp4"])
        self.assertEqual(parsed["pane_playlists"][1], "/media/playlist.m3u")
        self.assertEqual(parsed["pane_mpv_opts"][1], ["mute=yes"])


if __name__ == "__main__":
    unittest.main()
