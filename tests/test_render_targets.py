import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_C = ROOT / "src" / "app.c"
FRAME_C = ROOT / "src" / "frame.c"
RENDER_GL_C = ROOT / "src" / "render_gl.c"
RENDER_GL_H = ROOT / "src" / "render_gl.h"
RUNTIME_C = ROOT / "src" / "runtime.c"
RUNTIME_H = ROOT / "src" / "runtime.h"


class RenderTargetTests(unittest.TestCase):
    def test_role_model_uses_pane_indices_and_explicit_legacy_translation(self) -> None:
        header = (ROOT / "src" / "options.h").read_text(encoding="utf-8")
        options_src = (ROOT / "src" / "options.c").read_text(encoding="utf-8")
        layout_src = (ROOT / "src" / "layout.c").read_text(encoding="utf-8")

        self.assertIn("KMS_MOSAIC_SLOT_PANE_BASE = 0", header)
        self.assertNotIn("KMS_MOSAIC_SLOT_VIDEO = 0", header)
        self.assertIn("return opt->pane_count;", options_src)
        self.assertNotIn("return KMS_MOSAIC_SLOT_PANE_BASE + opt->pane_count;", options_src)
        self.assertIn("options_parse_legacy_roles_string(", options_src)
        self.assertIn("options_translate_legacy_split_tree_spec(", options_src)
        self.assertNotIn("if (c == 'C' || c == 'c') role = KMS_MOSAIC_SLOT_VIDEO;", options_src)
        self.assertNotIn("tile_rects(pane_area, pane_count, &slots[KMS_MOSAIC_SLOT_PANE_BASE]);", layout_src)
        self.assertIn("split_tree_translate_legacy_roles(", layout_src)

    def test_pane_mpv_renders_use_pane_specific_targets(self) -> None:
        frame_src = FRAME_C.read_text(encoding="utf-8")
        self.assertIn("render_gl_ensure_pane_video_rt(", frame_src)
        pane_section = frame_src.split("if (!rt->direct_mode) {", 1)[1]
        pane_section = pane_section.split("if (!rt->direct_mode && !opt->no_osd && ui->show_osd)", 1)[0]
        self.assertNotIn("render_gl_ensure_video_rt(rg, vw, vh);", pane_section)

    def test_render_gl_ctx_tracks_pane_video_targets(self) -> None:
        header = RENDER_GL_H.read_text(encoding="utf-8")
        source = RENDER_GL_C.read_text(encoding="utf-8")
        self.assertIn("GLuint *pane_vid_fbos;", header)
        self.assertIn("GLuint *pane_vid_texs;", header)
        self.assertIn("bool render_gl_ensure_pane_video_rt(", header)
        self.assertIn("free(ctx->pane_vid_fbos);", source)
        self.assertIn("free(ctx->pane_vid_texs);", source)

    def test_pane_mpv_render_passes_are_gated_by_wakeup_or_resize(self) -> None:
        app_src = APP_C.read_text(encoding="utf-8")
        frame_src = FRAME_C.read_text(encoding="utf-8")
        runtime_header = RUNTIME_H.read_text(encoding="utf-8")
        runtime_source = RUNTIME_C.read_text(encoding="utf-8")
        self.assertIn("int *pane_mpv_needs_render;", runtime_header)
        self.assertIn("rt->pane_mpv_needs_render = calloc", runtime_source)
        self.assertIn("rt->pane_mpv_needs_render[i] = 1;", runtime_source)
        self.assertIn("if (pane_needs_render && rt->pane_mpv_needs_render)", app_src)
        self.assertIn("bool pane_target_resized = render_gl_ensure_pane_video_rt(", frame_src)
        self.assertIn("if (!pane_needs_render || *pane_needs_render)", frame_src)
        self.assertIn("if (pane_needs_render) *pane_needs_render = 0;", frame_src)

    def test_runtime_focus_and_rendering_do_not_keep_a_special_main_video_slot(self) -> None:
        app_src = APP_C.read_text(encoding="utf-8")
        frame_src = FRAME_C.read_text(encoding="utf-8")
        ui_src = (ROOT / "src" / "ui.c").read_text(encoding="utf-8")
        web_src = (ROOT / "tools" / "kms_mosaic_web.py").read_text(encoding="utf-8")

        self.assertNotIn("UI_SLOT_VIDEO", ui_src)
        self.assertNotIn("return pane_count + (use_mpv ? 1 : 0);", ui_src)
        self.assertNotIn("else if (ui->focus == UI_SLOT_VIDEO && mpv)", ui_src)
        self.assertIn("return pane_count;", ui_src)
        self.assertIn("ui->focus = opt->pane_count > 0 ? 0 : -1;", ui_src)
        self.assertIn("pane_mpv && ui->focus >= 0 && pane_mpv[ui->focus]", ui_src)

        self.assertNotIn("const pane_layout *lay_video = &slot_layouts[KMS_MOSAIC_SLOT_VIDEO];", frame_src)
        self.assertNotIn("if (use_mpv && (!ui->fullscreen || ui->fs_pane == 0))", frame_src)
        self.assertNotIn("focus_layouts[KMS_MOSAIC_SLOT_VIDEO]", frame_src)
        self.assertIn("const pane_layout *focus_layout = &pane_layouts[focus_slot];", frame_src)
        self.assertIn("bool pane_visible = !ui->fullscreen || ui->fs_pane == i;", frame_src)
        self.assertIn("media_ctx *pane_ctx = NULL;", frame_src)

        self.assertNotIn("(i == 0 && use_mpv) ? m->mpv : NULL", app_src)
        self.assertIn("bool has_legacy_root_media =", app_src)
        self.assertIn("opt.video_count > 0", app_src)
        self.assertIn("opt.playlist_path", app_src)
        self.assertIn("if (use_mpv && opt.pane_count > 0 && has_legacy_root_media)", app_src)
        self.assertIn("media_shutdown(&m);", app_src)
        self.assertIn("pane_media[0] = m;", app_src)
        self.assertNotIn('if (index === 0) return "Video";', web_src)
        self.assertNotIn("const paneRoles = roles.slice(1);", web_src)
        self.assertIn("const [primaryRole, ...secondaryRoles] = roles;", web_src)
        self.assertIn("function defaultMediaPaneRole()", web_src)
        self.assertIn("const roles = orderedRolesFromState(nextState);", web_src)
        self.assertIn("return firstMediaPane >= 0 ? firstMediaPane : -1;", web_src)
        self.assertIn("if (resolvedRole < 0) return 0;", web_src)

    def test_app_cleanup_restores_linux_console_after_drm_shutdown(self) -> None:
        app_src = APP_C.read_text(encoding="utf-8")
        self.assertIn("static void app_restore_linux_console(void)", app_src)
        self.assertIn('"/sys/class/vtconsole"', app_src)
        self.assertIn("KDSETMODE", app_src)
        self.assertIn("KD_TEXT", app_src)
        self.assertIn("VT_ACTIVATE", app_src)
        self.assertIn("VT_WAITACTIVE", app_src)
        self.assertIn("app_restore_linux_console();", app_src)
        self.assertLess(
            app_src.find("if (d->fd >= 0) close(d->fd);"),
            app_src.find("app_restore_linux_console();"),
        )


if __name__ == "__main__":
    unittest.main()
