import os
import inspect
import pathlib
import tempfile
import time
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_C = (ROOT / "src" / "app.c").read_text(encoding="utf-8")
DISPLAY_C = (ROOT / "src" / "display.c").read_text(encoding="utf-8")
FRAME_C = (ROOT / "src" / "frame.c").read_text(encoding="utf-8")
MEDIA_C = (ROOT / "src" / "media.c").read_text(encoding="utf-8")
RENDER_GL_C = (ROOT / "src" / "render_gl.c").read_text(encoding="utf-8")
TERM_PANE_C = (ROOT / "src" / "term_pane.c").read_text(encoding="utf-8")
RUNTIME_C = (ROOT / "src" / "runtime.c").read_text(encoding="utf-8")


class ResourceEfficiencyTests(unittest.TestCase):
    def test_main_loop_reuses_readiness_storage_and_gates_rendering(self) -> None:
        loop = APP_C[APP_C.index("while (rt.running)") : APP_C.index("cleanup:")]
        self.assertNotIn("calloc((size_t)scene.pane_count", loop)
        self.assertIn("if (rt.render_dirty && !g.in_flight)", loop)
        self.assertIn("rt.render_dirty = ui.layout_reinit_countdown > 0;", loop)
        self.assertIn("app_poll_timeout_ms(", loop)
        self.assertIn("if (!layout_changed) return false;", APP_C)

    def test_plugin_only_control_removes_terminal_input_resources(self) -> None:
        self.assertNotIn("app_handle_input_ready", APP_C)
        self.assertNotIn("RUNTIME_POLL_STDIN", RUNTIME_C)
        self.assertNotIn("ui_handle_input", (ROOT / "src" / "ui.c").read_text(encoding="utf-8"))
        damaged = TERM_PANE_C[TERM_PANE_C.index("static void update_damaged_rows") : TERM_PANE_C.index("bool term_pane_poll")]
        self.assertIn("tp->row_cells_cap < tp->layout.cols", damaged)
        self.assertNotIn("free(row_cells)", damaged)

    def test_file_changes_wake_the_existing_poll_loop(self) -> None:
        self.assertIn("inotify_init1(IN_NONBLOCK | IN_CLOEXEC)", APP_C)
        self.assertIn("RUNTIME_POLL_FILE_WATCH", RUNTIME_C)
        self.assertIn("app_file_watch_drain(&file_watch)", APP_C)
        self.assertIn("control_next_check_sec", APP_C)

    def test_layout_only_config_changes_keep_media_processes_alive(self) -> None:
        self.assertIn("app_config_unsafe_hash", APP_C)
        self.assertIn("app_apply_hot_config", APP_C)
        self.assertIn("Applied layout/UI config changes without restarting media panes", APP_C)
        self.assertIn("next_unsafe_hash == cfg_watch.unsafe_hash", APP_C)

    def test_preview_downscales_before_reusable_readback(self) -> None:
        self.assertIn("render_gl_write_preview_frame(", FRAME_C)
        self.assertIn("logical_w, logical_h, snapshot_max_edge", FRAME_C)
        self.assertIn("app_snapshot_watch_max_edge(&snap_watch)", APP_C)
        self.assertIn("watch->stream_max_edge = parsed", APP_C)
        self.assertIn("ctx->preview_pixels_cap < pixel_bytes", RENDER_GL_C)
        self.assertIn("glBindFramebuffer(GL_FRAMEBUFFER, ctx->preview_fbo);", RENDER_GL_C)

    def test_preview_pipelines_readback_when_pixel_buffers_are_available(self) -> None:
        self.assertIn('GL_NV_pixel_buffer_object', RENDER_GL_C)
        self.assertIn('glMapBufferRangeEXT', RENDER_GL_C)
        self.assertIn('ctx->preview_pbo_pending_index = write_index;', RENDER_GL_C)
        self.assertIn('render_gl_write_rgba_file(ctx->preview_pbo_pending_path', RENDER_GL_C)

    def test_osd_uses_observed_properties_instead_of_sync_reads(self) -> None:
        osd_section = FRAME_C[FRAME_C.index("if (osd_media && osd_media->mpv)") : FRAME_C.index("if (snapshot_path && snapshot_written")]
        self.assertNotIn("mpv_get_property", osd_section)
        self.assertIn('mpv_observe_property(m->mpv, MEDIA_OBSERVE_PLAYLIST_POS', MEDIA_C)
        self.assertIn("MPV_EVENT_PROPERTY_CHANGE", MEDIA_C)

    def test_drm_framebuffers_are_cached_on_gbm_buffers(self) -> None:
        self.assertIn("gbm_bo_get_user_data(bo)", DISPLAY_C)
        self.assertIn("gbm_bo_set_user_data(bo, fb, display_destroy_bo_fb)", DISPLAY_C)
        page_flip = DISPLAY_C[DISPLAY_C.index("void display_page_flip") : DISPLAY_C.index("int display_wait_for_page_flip")]
        self.assertNotIn("drmModeRmFB", page_flip)

    def test_thumbnail_cache_prunes_expired_entries(self) -> None:
        from tools import kms_mosaic_web

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = pathlib.Path(tmp)
            old_entry = cache_dir / "old.jpg"
            fresh_entry = cache_dir / "fresh.jpg"
            old_entry.write_bytes(b"old")
            fresh_entry.write_bytes(b"fresh")
            old_time = time.time() - kms_mosaic_web.THUMB_CACHE_MAX_AGE_SEC - 60
            os.utime(old_entry, (old_time, old_time))
            handler = object.__new__(kms_mosaic_web.Handler)
            handler.server = types.SimpleNamespace(
                app_config=types.SimpleNamespace(thumb_cache_dir=cache_dir)
            )

            handler._prune_thumbnail_cache()

            self.assertFalse(old_entry.exists())
            self.assertTrue(fresh_entry.exists())

    def test_preview_rgba_rows_reuse_stride_padding_buffer(self) -> None:
        from tools import kms_mosaic_web

        rgba = bytes(range(16))
        scratch = bytearray(24)
        packed = kms_mosaic_web.pack_rgba_rows(rgba, 2, 2, 12, scratch)

        self.assertIs(packed, scratch)
        self.assertEqual(packed[:8], rgba[:8])
        self.assertEqual(packed[8:12], b"\0" * 4)
        self.assertEqual(packed[12:20], rgba[8:16])

    def test_preview_encoder_normalizes_odd_dimensions(self) -> None:
        from tools import kms_mosaic_web

        self.assertEqual(kms_mosaic_web.preview_encode_dimensions(405, 720, 720), (404, 720))
        self.assertEqual(kms_mosaic_web.preview_encode_dimensions(1920, 1080, 720), (720, 404))

    def test_embedded_plugin_preview_api_is_explicitly_exported(self) -> None:
        web_source = (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8")

        self.assertIn("window.scheduleLivePreview = scheduleLivePreview;", web_source)
        self.assertIn("window.fillForm = fillForm;", web_source)
        self.assertIn("if (!webrtcPeer) {", web_source)
        self.assertIn("requestAnimationFrame(() => {", web_source)
        self.assertIn('["new", "connecting", "connected"].includes(webrtcPeer.connectionState)', web_source)

    def test_preview_peers_are_explicitly_released(self) -> None:
        web_source = (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8")

        self.assertIn('fetch("/api/webrtc-close", {', web_source)
        self.assertIn("keepalive: true", web_source)
        self.assertIn('navigator.sendBeacon("/api/webrtc-close"', web_source)
        self.assertIn('window.addEventListener("pagehide"', web_source)
        self.assertIn("self.peer_expiry[pc] = asyncio.create_task", web_source)
        self.assertIn('fetch("/api/webrtc-keepalive", {', web_source)
        self.assertIn("time.monotonic() - self.peer_last_seen", web_source)
        self.assertIn("if not self.peers and self.preview_source is not None:", web_source)
        self.assertIn('if self.readyState != "live":', web_source)

    def test_layout_studio_supports_snapping_guides_and_history(self) -> None:
        web_source = (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8")

        self.assertIn("const STUDIO_SNAP_POINTS =", web_source)
        self.assertIn("function renderStudioResizeGuides()", web_source)
        self.assertIn("function undoStudioLayout()", web_source)
        self.assertIn("function redoStudioLayout()", web_source)
        self.assertIn('id="studioUndoBtn"', web_source)
        self.assertIn("event.altKey", web_source)

    def test_scene_manager_persists_applies_and_schedules_scenes(self) -> None:
        from tools import kms_mosaic_web

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            config = root / "kms_mosaic.conf"
            scenes = root / "scenes.json"
            app_config = kms_mosaic_web.WebConfig(
                config_path=config,
                host="127.0.0.1",
                port=0,
                snapshot_request_path=root / "request",
                preview_lease_path=root / "lease",
                snapshot_output_path=root / "preview",
                thumb_cache_dir=root / "thumbs",
                scenes_path=scenes,
            )
            manager = kms_mosaic_web.SceneManager(app_config)
            state = kms_mosaic_web.empty_state()
            state["rotation"] = 90

            scene = manager.save_scene("Portrait", state)
            schedule = manager.save_schedule(scene["id"], "08:30", [0, 2, 4])
            applied = manager.apply_scene(scene["id"])

            self.assertEqual(applied["name"], "Portrait")
            self.assertIn("--rotate 90", config.read_text(encoding="utf-8"))
            self.assertEqual(manager.read()["schedules"][0]["days"], [0, 2, 4])
            self.assertTrue(manager.delete_schedule(schedule["id"]))
            self.assertTrue(manager.delete_scene(scene["id"]))

    def test_health_monitor_reports_web_process_without_subprocess_polling(self) -> None:
        from tools import kms_mosaic_web

        monitor = kms_mosaic_web.HealthMonitor()
        snapshot = monitor.snapshot(preview_peers=2)

        if pathlib.Path("/proc/self/stat").exists():
            self.assertEqual(snapshot["web"]["pid"], os.getpid())
            self.assertIn("rss_bytes", snapshot["web"])
        else:
            self.assertIsNone(snapshot["web"])
        self.assertEqual(snapshot["preview_peers"], 2)
        health_source = inspect.getsource(kms_mosaic_web.HealthMonitor)
        self.assertNotIn("subprocess", health_source)
        self.assertIn("/proc/{pid}/task/{pid}/children", health_source)

    def test_per_pane_recovery_is_targeted_and_watchdog_is_opt_in(self) -> None:
        from tools import kms_mosaic_web

        state = kms_mosaic_web.empty_state()
        state["pane_types"][0] = "mpv"
        state["pane_commands"][0] = ""
        state["pane_video_paths"][0] = ["/media/test.mp4"]
        state["pane_watchdogs"][0] = 12
        state["pane_sync_groups"][0] = "wall-a"
        text = kms_mosaic_web.serialize_config(state)
        restored = kms_mosaic_web.parse_config_text(text)

        self.assertIn("--pane-watchdog 1 12", text)
        self.assertEqual(restored["pane_watchdogs"][0], 12)
        self.assertIn("--pane-sync-group 1 wall-a", text)
        self.assertEqual(restored["pane_sync_groups"][0], "wall-a")
        self.assertIn('write_text_atomic(self.app_config.control_request_path',
                      (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8"))
        self.assertIn('sscanf(line, "restart-pane %d"', APP_C)
        self.assertIn("media_watchdog_poll", APP_C)
        self.assertIn("m->osd_paused", MEDIA_C)
        self.assertIn("m->osd_playlist_count <= 0", MEDIA_C)
        self.assertIn("app_media_sync_groups_poll", APP_C)
        self.assertIn("media_set_paused(&pane_media[j], false)", APP_C)

    def test_structured_mpv_controls_preserve_raw_fallback(self) -> None:
        web_source = (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8")

        for control_id in (
            "inspectorPaneHwdec", "inspectorPaneScale", "inspectorPaneDeband",
            "inspectorPaneInterpolation", "inspectorPaneVideoSync",
        ):
            self.assertIn(control_id, web_source)
        self.assertIn("groups.other.push(value);", web_source)
        self.assertIn("structuredValues[key]?.has(optionValue)", web_source)
        self.assertIn("if (parts.hwdec) opts.push", web_source)

    def test_preview_profiles_trade_cadence_and_resolution_without_polling_when_idle(self) -> None:
        web_source = (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8")

        self.assertIn('"quality": (33, 1080, 8000, 12000, 2000)', web_source)
        self.assertIn('"balanced": (33, 720, 5000, 8000, 1200)', web_source)
        self.assertIn('"economy": (100, 480, 1800, 3000, 500)', web_source)
        self.assertIn("aiortc_h264.DEFAULT_BITRATE = start_kbps * 1000", web_source)
        self.assertIn("aiortc_h264.MAX_BITRATE = max_kbps * 1000", web_source)
        self.assertIn("write_preview_lease(app_config, interval_ms, max_edge)", web_source)
        self.assertIn('id="previewProfile"', web_source)
        self.assertIn("connection?.saveData", web_source)
        self.assertIn('return "quality";', web_source)
        self.assertIn("preview_profile: resolvedPreviewProfile()", web_source)
        self.assertIn("if not self.peers and self.preview_source is not None:", web_source)

    def test_scene_fades_are_opt_in_and_rendered_on_the_display(self) -> None:
        from tools import kms_mosaic_web

        state = kms_mosaic_web.empty_state()
        state["transition_ms"] = 350
        text = kms_mosaic_web.serialize_config(state)
        restored = kms_mosaic_web.parse_config_text(text)

        self.assertIn("--transition-ms 350", text)
        self.assertEqual(restored["transition_ms"], 350)
        self.assertIn("transition_fading_out", APP_C)
        self.assertIn("rt.transition_brightness", APP_C)
        self.assertIn("render_gl_blit_rt_to_screen_brightness", FRAME_C)
        render_source = (ROOT / "src" / "render_gl.c").read_text(encoding="utf-8")
        self.assertIn("c.rgb*u_brightness", render_source)

    def test_monitor_controls_are_discovered_and_written_only_on_demand(self) -> None:
        from tools import kms_mosaic_web

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            sysfs = root / "drm"
            dev = root / "dev"
            connector = sysfs / "card0-HDMI-A-1"
            bus_target = root / "i2c-3"
            connector.mkdir(parents=True)
            dev.mkdir()
            bus_target.mkdir()
            (dev / "i2c-3").write_bytes(b"")
            (connector / "status").write_text("connected\n", encoding="utf-8")
            (connector / "ddc").symlink_to(bus_target)
            edid = bytearray(128)
            edid[54:72] = b"\0\0\0\xfc\0Test Panel  \n"
            (connector / "edid").write_bytes(edid)

            monitors = kms_mosaic_web.list_ddc_monitors(sysfs, dev)

            self.assertEqual(monitors[0]["connector"], "HDMI-A-1")
            self.assertEqual(monitors[0]["model"], "Test Panel")
            self.assertEqual(monitors[0]["bus"], str(dev / "i2c-3"))
            message = kms_mosaic_web.build_ddc_vcp_message("brightness", 75)
            self.assertEqual(message[:6], bytes([0x51, 0x84, 0x03, 0x10, 0x00, 75]))
            self.assertEqual(message[-1], 0x6E ^ 0x51 ^ 0x84 ^ 0x03 ^ 0x10 ^ 0x00 ^ 75)
            with self.assertRaises(ValueError):
                kms_mosaic_web.build_ddc_vcp_message("brightness", 101)

        web_source = (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8")
        self.assertIn('if (event.currentTarget.open) loadMonitors()', web_source)
        self.assertIn('"/api/monitors/set"', web_source)
        monitor_section = web_source[web_source.index("async function loadMonitors"):
                                     web_source.index("function scheduleHealthPolling")]
        self.assertNotIn("setInterval", monitor_section)

    def test_mobile_remote_skips_preview_and_reuses_targeted_apis(self) -> None:
        web_source = (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8")

        self.assertIn('get("remote") === "1"', web_source)
        self.assertIn('body.remote-mode .shell { display: none; }', web_source)
        self.assertIn('body.remote-mode .remote-shell { display: grid;', web_source)
        self.assertIn('if (remoteMode || document.hidden) {', web_source)
        self.assertIn('sceneApi("/api/scenes/apply"', web_source)
        self.assertIn('sceneApi("/api/panes/restart"', web_source)
        self.assertIn('data-remote-visibility="no-terminal"', web_source)
        startup = web_source[web_source.rindex("if (remoteMode) {"):
                             web_source.index('window.addEventListener("pagehide"')]
        self.assertIn("loadRemoteControl()", startup)
        self.assertIn("requestAnimationFrame", startup)
        self.assertLess(startup.index("loadRemoteControl()"), startup.index("requestAnimationFrame"))

    def test_config_history_is_bounded_diffable_and_rollback_safe(self) -> None:
        from tools import kms_mosaic_web

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            config = root / "kms_mosaic.conf"
            config.write_text("--rotate 0\n", encoding="utf-8")
            history = kms_mosaic_web.ConfigHistory(config, root / "history")
            history.MAX_ENTRIES = 2

            self.assertTrue(history.write("--rotate 90\n", "editor"))
            first = history.entries()[0]
            self.assertIn("--- saved snapshot", history.diff(first["id"]))
            self.assertIn("+--rotate 90", history.diff(first["id"]))
            self.assertFalse(history.write("--rotate 90\n", "editor"))
            self.assertEqual(len(history.entries()), 1)

            self.assertTrue(history.rollback(first["id"]))
            self.assertEqual(config.read_text(encoding="utf-8"), "--rotate 0\n")
            self.assertLessEqual(len(history.entries()), 2)
            self.assertTrue(any(entry["reason"] == "pre rollback" for entry in history.entries()))

        web_source = (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8")
        self.assertIn('id="configHistoryPanel"', web_source)
        self.assertIn('if (event.currentTarget.open) loadConfigHistory()', web_source)
        self.assertIn('"/api/history/rollback"', web_source)

    def test_pane_templates_round_trip_without_activating_config(self) -> None:
        from tools import kms_mosaic_web

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            config = root / "kms_mosaic.conf"
            config.write_text("--pane-count 2\n", encoding="utf-8")
            app_config = kms_mosaic_web.WebConfig(
                config_path=config,
                host="127.0.0.1",
                port=0,
                snapshot_request_path=root / "request",
                preview_lease_path=root / "lease",
                snapshot_output_path=root / "preview",
                thumb_cache_dir=root / "thumbs",
            )
            manager = kms_mosaic_web.PaneTemplateManager(app_config, root / "templates.json")
            pane = {
                "type": "mpv",
                "panscan": "1",
                "watchdog": 12,
                "sync_group": "wall-a",
                "video_paths": ["/media/one.mp4"],
                "mpv_opts": ["hwdec=auto-copy-safe"],
            }

            saved = manager.save("Video wall", pane)

            self.assertEqual(manager.read()["templates"][0]["pane"]["type"], "mpv")
            self.assertEqual(manager.read()["templates"][0]["pane"]["watchdog"], 12)
            self.assertEqual(config.read_text(encoding="utf-8"), "--pane-count 2\n")
            self.assertTrue(manager.delete(saved["id"]))
            self.assertEqual(manager.read()["templates"], [])

        web_source = (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8")
        self.assertIn('id="paneTemplateSelect"', web_source)
        self.assertIn('paneTemplateApi("/api/templates/save"', web_source)
        self.assertIn("Apply changes only this editor pane", web_source)
        self.assertIn("server.templates = PaneTemplateManager", web_source)


if __name__ == "__main__":
    unittest.main()
