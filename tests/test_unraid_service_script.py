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
        self.assertIn("wait_for_kms_exit()", text)
        self.assertIn("kms_child_group_leaders()", text)
        self.assertIn("ps -o pid=,pgid= --ppid", text)
        self.assertIn('pids="$(kms_pids_from_system)"', text)
        self.assertIn('new_groups="$(kms_child_group_leaders "$pid" || true)"', text)
        self.assertIn('child_groups="$(printf \'%s\\n%s\\n\' \"$child_groups\" \"$new_groups\" | awk \'NF && !seen[$0]++\')"', text)
        self.assertIn('for pid in $pids; do', text)
        self.assertIn('if [ "$any_running" = "false" ]; then', text)
        self.assertIn("wait_for_kms_exit 20 || true", text)
        self.assertIn("for leader in $child_groups; do", text)
        self.assertNotIn('child_pids="$(pgrep -P "$pid"', text)
        self.assertNotIn("for child_pid in $child_pids; do", text)

    def test_restart_waits_for_old_kms_pid_to_clear_before_starting(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")
        restart_case = text[text.index("  restart)") : text.index("  status)")]

        self.assertLess(restart_case.index("stop_kms"), restart_case.index("wait_for_kms_exit 20 || true"))
        self.assertLess(restart_case.index("wait_for_kms_exit 20 || true"), restart_case.index("start_kms"))

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

    def test_start_kms_retries_when_new_process_exits_immediately(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")
        start_kms = text[text.index("start_kms() {") : text.index("stop_kms() {")]

        self.assertIn("for attempt in 1 2; do", start_kms)
        self.assertIn("sleep 3", start_kms)
        self.assertIn("if is_kms_running && kms_render_healthy; then", start_kms)
        self.assertIn('cleanup_pidfile "$KMS_PIDFILE"', start_kms)
        self.assertIn("sleep 5", start_kms)

    def test_start_waits_for_host_graphics_stack_and_two_fresh_frames(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")
        start_kms = text[text.index("start_kms() {") : text.index("stop_kms() {")]

        self.assertIn("wait_for_runtime_dependencies", start_kms)
        self.assertIn("kms_render_healthy", start_kms)
        self.assertIn("request_fresh_render || return 1", text)
        self.assertEqual(text.count("request_fresh_render || return 1"), 2)
        self.assertIn('touch "$SNAPSHOT_REQUEST"', text)
        self.assertIn('[ -s "$SNAPSHOT_OUTPUT" ]', text)

    def test_services_launch_in_isolated_sessions(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("launch_detached()", text)
        self.assertIn('nohup setsid "$@" &', text)
        self.assertIn('launch_detached "$KMS_WRAPPER"', text)
        self.assertIn('launch_detached "$WEB_WRAPPER"', text)

    def test_unraid_defaults_to_event_driven_atomic_presentation(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('KMS_ARGS="--atomic-nonblock"', text)
        self.assertIn('local args="${KMS_ARGS:-}"', text)
        self.assertIn('args="${args:+${args} }--debug"', text)


if __name__ == "__main__":
    unittest.main()
