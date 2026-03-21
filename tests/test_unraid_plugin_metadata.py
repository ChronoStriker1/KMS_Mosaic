import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_README = ROOT / "unraid-plugin" / "package-root" / "usr" / "local" / "emhttp" / "plugins" / "kms.mosaic" / "README.md"


class UnraidPluginMetadataTests(unittest.TestCase):
    def test_packaged_plugin_readme_exists_for_installed_plugins_listing(self) -> None:
        text = PLUGIN_README.read_text(encoding="utf-8")

        self.assertIn("# KMS Mosaic", text)
        self.assertIn("pane layout editing", text)
        self.assertIn("live preview", text)


if __name__ == "__main__":
    unittest.main()
