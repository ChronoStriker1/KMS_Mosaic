import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class KmsMosaicLayoutRuntimeTests(unittest.TestCase):
    def test_layout_runtime_uses_pane_count_as_role_count(self):
        header = (REPO_ROOT / "src" / "options.h").read_text(encoding="utf-8")
        options_src = (REPO_ROOT / "src" / "options.c").read_text(encoding="utf-8")
        layout_src = (REPO_ROOT / "src" / "layout.c").read_text(encoding="utf-8")

        self.assertIn("KMS_MOSAIC_SLOT_PANE_BASE = 0", header)
        self.assertIn("return opt->pane_count;", options_src)
        self.assertIn("int role_count = pane_count;", layout_src)
        self.assertIn("split_tree_translate_legacy_roles(", layout_src)


if __name__ == "__main__":
    unittest.main()
