import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTIONS_PHP = ROOT / "unraid-plugin" / "package-root" / "usr" / "local" / "emhttp" / "plugins" / "kms.mosaic" / "include" / "actions.php"


class UnraidActionsProxyTests(unittest.TestCase):
    def test_backend_media_uses_streaming_proxy(self) -> None:
        src = ACTIONS_PHP.read_text(encoding="utf-8")
        self.assertIn("function stream_backend_response($method, $url, $content_type = null)", src)
        self.assertIn("curl_setopt($ch, CURLOPT_WRITEFUNCTION", src)
        self.assertIn("curl_setopt($ch, CURLOPT_HEADERFUNCTION", src)
        self.assertIn("if ($action === 'backend_media') {", src)
        self.assertIn("stream_backend_response('GET', $backend . '/api/media?path=' . rawurlencode($path));", src)


if __name__ == "__main__":
    unittest.main()
