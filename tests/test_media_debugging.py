import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MEDIA_C = ROOT / "src" / "media.c"


class MediaDebuggingTests(unittest.TestCase):
    def test_smooth_preset_keeps_shader_cache_enabled(self) -> None:
        src = MEDIA_C.read_text(encoding="utf-8")
        self.assertNotIn('mpv_set_option_string(m->mpv, "gpu-shader-cache", "no");', src)
        self.assertIn('mpv_set_option_string(m->mpv, "gpu-shader-cache", "yes");', src)

    def test_embedded_mpv_disables_builtin_scripts_by_default(self) -> None:
        src = MEDIA_C.read_text(encoding="utf-8")
        self.assertIn("bool user_set_load_scripts = false;", src)
        self.assertIn('else if (media_key_matches(kv, "load-scripts")) *user_set_load_scripts = true;', src)
        self.assertIn('if (!user_set_load_scripts) mpv_set_option_string(m->mpv, "load-scripts", "no");', src)

    def test_audio_disabled_configs_also_disable_audio_track_selection(self) -> None:
        src = MEDIA_C.read_text(encoding="utf-8")
        self.assertIn('mpv_set_option_string(m->mpv, "audio", "no");', src)
        self.assertIn('mpv_set_option_string(m->mpv, "aid", "no");', src)

    def test_embedded_mpv_defaults_keep_open_off_unless_user_overrides(self) -> None:
        src = MEDIA_C.read_text(encoding="utf-8")
        self.assertIn("bool user_set_keep_open = false;", src)
        self.assertIn('else if (media_key_matches(kv, "keep-open")) *user_set_keep_open = true;', src)
        self.assertIn('if (!user_set_keep_open) mpv_set_option_string(m->mpv, "keep-open", "no");', src)
        self.assertNotIn('mpv_set_option_string(m->mpv, "keep-open", "yes");', src)

    def test_embedded_mpv_defaults_to_auto_copy_safe_hwdec(self) -> None:
        src = MEDIA_C.read_text(encoding="utf-8")
        self.assertIn("bool user_set_hwdec = false;", src)
        self.assertIn('if (!user_set_hwdec) mpv_set_option_string(m->mpv, "hwdec", "auto-copy-safe");', src)

    def test_embedded_mpv_defaults_shader_cache_on_unless_user_overrides(self) -> None:
        src = MEDIA_C.read_text(encoding="utf-8")
        self.assertIn("bool user_set_shader_cache = false;", src)
        self.assertIn('else if (media_key_matches(kv, "gpu-shader-cache")) *user_set_shader_cache = true;', src)
        self.assertIn('if (!user_set_shader_cache) mpv_set_option_string(m->mpv, "gpu-shader-cache", "yes");', src)
        self.assertLess(
            src.find('if (!user_set_shader_cache) mpv_set_option_string(m->mpv, "gpu-shader-cache", "yes");'),
            src.find("if (opt->smooth) {"),
        )

    def test_legacy_vaapi_hwdec_is_normalized_to_auto_copy_safe(self) -> None:
        src = MEDIA_C.read_text(encoding="utf-8")
        self.assertIn('media_key_matches(kv, "hwdec")', src)
        self.assertIn('static const char *media_normalize_hwdec_value', src)
        self.assertIn('strcmp(value, "vaapi") == 0', src)
        self.assertIn('return "auto-copy-safe";', src)

    def test_embedded_mpv_always_loops_playlist(self) -> None:
        src = MEDIA_C.read_text(encoding="utf-8")
        self.assertIn('mpv_set_option_string(m->mpv, "loop-playlist", "yes");', src)
        self.assertNotIn('if (opt->loop_playlist) mpv_set_option_string(m->mpv, "loop-playlist", "yes");', src)

    def test_mpv_event_logging_includes_path_and_playlist_position(self) -> None:
        src = MEDIA_C.read_text(encoding="utf-8")
        self.assertNotIn('mpv_get_property(m->mpv, "playlist-pos"', src)
        self.assertIn('mpv_get_property_string(m->mpv, "path")', src)
        self.assertIn('mpv_free(current_path);', src)
        self.assertIn("media_log_event(m, \"START_FILE\"", src)
        self.assertIn("media_log_event(m, \"FILE_LOADED\"", src)
        self.assertIn('playlist_entry_id=%lld', src)
        self.assertIn("MPV_EVENT_START_FILE", src)
        self.assertIn("MPV_EVENT_FILE_LOADED", src)
        self.assertIn("MPV_EVENT_END_FILE", src)


if __name__ == "__main__":
    unittest.main()
