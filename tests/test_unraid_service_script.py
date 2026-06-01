import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = ROOT / "unraid-plugin" / "package-root" / "usr" / "local" / "emhttp" / "plugins" / "kms.mosaic" / "scripts" / "kms_mosaic-service"


class UnraidServiceScriptTests(unittest.TestCase):
    def test_pidfile_cleanup_is_idempotent_under_errexit(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('rm -f "$file" 2>/dev/null || true', text)
        self.assertNotIn('[ -f "$file" ] && rm -f "$file"', text)

    def test_stop_kms_refreshes_child_group_cleanup_across_shutdown(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("kms_pids_from_system()", text)
        self.assertIn("kms_child_group_leaders()", text)
        self.assertIn("ps -o pid=,pgid= --ppid", text)
        self.assertIn('pids="$(kms_pids_from_system)"', text)
        self.assertIn('new_groups="$(kms_child_group_leaders "$pid")"', text)
        self.assertIn('child_groups="$(printf \'%s\\n%s\\n\' \"$child_groups\" \"$new_groups\" | awk \'NF && !seen[$0]++\')"', text)
        self.assertIn('for pid in $pids; do', text)
        self.assertIn('if [ "$any_running" = "false" ]; then', text)
        self.assertIn("for leader in $child_groups; do", text)
        self.assertNotIn('child_pids="$(pgrep -P "$pid"', text)
        self.assertNotIn("for child_pid in $child_pids; do", text)

    def test_start_kms_removes_packaged_host_gpu_libraries(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('KMS_LIBDIR="/usr/local/lib/kms_mosaic"', text)
        self.assertIn("cleanup_host_gpu_libs()", text)
        for lib_name in (
            "libdrm*.so*",
            "libgbm.so*",
            "libEGL*.so*",
            "libGLESv2.so*",
            "libGL*.so*",
            "libOpenGL.so*",
            "libX11*.so*",
            "libXau.so*",
            "libXdmcp.so*",
            "libXext.so*",
            "libxcb*.so*",
            "libwayland*.so*",
            "libexpat.so*",
            "libz.so*",
            "libzstd.so*",
            "liblzma.so*",
            "libffi.so*",
            "libncursesw.so*",
            "libtinfo.so*",
        ):
            self.assertIn(f'"$KMS_LIBDIR"/{lib_name}', text)

        start_kms = text[text.index("start_kms() {") : text.index("stop_kms() {")]
        self.assertIn("cleanup_host_gpu_libs", start_kms)


if __name__ == "__main__":
    unittest.main()
