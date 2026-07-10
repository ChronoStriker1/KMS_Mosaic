import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DISPLAY_SOURCE = ROOT / "src" / "display.c"
FRAME_SOURCE = ROOT / "src" / "frame.c"


class DrmPageFlipTests(unittest.TestCase):
    def test_legacy_frames_use_page_flips_not_crtc_modesets(self) -> None:
        text = DISPLAY_SOURCE.read_text(encoding="utf-8")
        page_flip = text[text.index("void display_page_flip") : text.index("void display_on_page_flip")]

        self.assertIn("drmModePageFlip", page_flip)
        self.assertIn("DRM_MODE_PAGE_FLIP_EVENT", page_flip)
        self.assertNotIn("drmModeSetCrtc", page_flip)

    def test_nonblocking_atomic_flips_request_completion_events(self) -> None:
        text = DISPLAY_SOURCE.read_text(encoding="utf-8")
        page_flip = text[text.index("void display_page_flip") : text.index("void display_on_page_flip")]

        self.assertIn("DRM_MODE_ATOMIC_NONBLOCK | DRM_MODE_PAGE_FLIP_EVENT", page_flip)

    def test_rendering_waits_until_the_previous_flip_completes(self) -> None:
        text = FRAME_SOURCE.read_text(encoding="utf-8")

        self.assertIn("if (g->in_flight) return;", text)

    def test_blocking_callers_can_wait_for_flip_completion(self) -> None:
        text = DISPLAY_SOURCE.read_text(encoding="utf-8")

        self.assertIn("int display_wait_for_page_flip", text)
        self.assertIn("drmHandleEvent", text)


if __name__ == "__main__":
    unittest.main()
