import os
import pathlib
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = ROOT / "unraid-plugin" / "package-root" / "usr" / "local" / "emhttp" / "plugins" / "kms.mosaic" / "scripts" / "kms_mosaic-service"
KMS_MAIN = ROOT / "src" / "kms_mosaic.c"


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

        self.assertIn('KMS_LIBDIR="${KMS_LIBDIR:-/usr/local/lib/kms_mosaic}"', text)
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
        self.assertIn("if is_kms_running && kms_render_healthy; then", start_kms)
        self.assertIn('launch_detached "$SELF" supervise-kms', start_kms)
        self.assertIn('write_pidfile "$KMS_SUPERVISOR_PIDFILE" "$!"', start_kms)
        self.assertIn("sleep 5", start_kms)
        self.assertIn("return 1", start_kms)

    def test_start_waits_for_host_graphics_stack_and_two_fresh_frames(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")
        start_kms = text[text.index("start_kms() {") : text.index("stop_kms() {")]

        self.assertIn("wait_for_runtime_dependencies", start_kms)
        self.assertIn("kms_render_healthy", start_kms)
        self.assertIn("request_fresh_render || return 1", text)
        self.assertEqual(text.count("request_fresh_render || return 1"), 2)
        self.assertIn('touch "$SNAPSHOT_REQUEST"', text)
        self.assertIn('[ -s "$SNAPSHOT_OUTPUT" ]', text)

    def test_start_waits_for_configured_media_panes(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")
        start_kms = text[text.index("start_kms() {") : text.index("stop_kms() {")]

        self.assertIn('STARTUP_MEDIA_TIMEOUT="${STARTUP_MEDIA_TIMEOUT:-300}"', text)
        self.assertIn("configured_media_requirements()", text)
        self.assertIn("has_configured_media_requirements()", text)
        self.assertIn("unraid_array_started()", text)
        self.assertIn("configured_media_ready()", text)
        self.assertIn("wait_for_configured_media()", text)
        self.assertIn('tok == "--pane-media"', text)
        self.assertIn('tok == "--pane-video"', text)
        self.assertIn('tok in {"--pane-playlist", "--pane-playlist-extended"}', text)
        self.assertIn('if has_configured_media_requirements && ! unraid_array_started; then', start_kms)
        self.assertIn('"array-stopped"', start_kms)
        self.assertLess(start_kms.index("! unraid_array_started"), start_kms.index("wait_for_runtime_dependencies"))
        self.assertLess(start_kms.index("! unraid_array_started"), start_kms.index("wait_for_configured_media"))
        self.assertIn("wait_for_configured_media", start_kms)
        self.assertIn('"media-timeout"', start_kms)

    def test_array_stopped_detection_uses_unraid_state_before_mount_fallback(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")
        array_started = text[text.index("unraid_array_started() {") : text.index("configured_media_ready() {")]

        self.assertIn("mdcmd=\"/root/mdcmd\"", array_started)
        self.assertIn("command -v mdcmd", array_started)
        self.assertIn('s/^mdState=//p', array_started)
        self.assertIn('tr -d \'"\'', array_started)
        self.assertIn("STARTED) return 0", array_started)
        self.assertIn("STOPPED|STOPPING|STARTING", array_started)
        self.assertIn('awk \'$2 == "/mnt/user" || $2 ~ "^/mnt/disk[0-9]+$"', array_started)
        self.assertIn("[ -d /boot/config ] && return 1", array_started)

    def test_start_schedules_one_boot_settle_restart_for_media_configs(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")
        start_kms = text[text.index("start_kms() {") : text.index("stop_kms() {")]

        self.assertIn('BOOT_SETTLE_RESTART_DELAY="${BOOT_SETTLE_RESTART_DELAY:-90}"', text)
        self.assertIn('BOOT_SETTLE_STAMP="${BOOT_SETTLE_STAMP:-/var/run/kms_mosaic.boot-settle-restart}"', text)
        self.assertIn("schedule_boot_settle_restart()", text)
        self.assertIn("boot_settle_restart_already_scheduled()", text)
        self.assertIn('touch "$BOOT_SETTLE_STAMP"', text)
        self.assertIn('"boot-settle-restart"', text)
        self.assertIn("has_configured_media_requirements || return 0", text[text.index("schedule_boot_settle_restart() {") : text.index("launch_detached() {")])
        self.assertIn("stop_kms", text[text.index("schedule_boot_settle_restart() {") : text.index("launch_detached() {")])
        self.assertIn("start_kms || true", text[text.index("schedule_boot_settle_restart() {") : text.index("launch_detached() {")])
        self.assertIn("schedule_boot_settle_restart", start_kms)

    def test_services_launch_in_isolated_sessions(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("launch_detached()", text)
        self.assertIn('nohup setsid "$@" &', text)
        self.assertIn('launch_detached "$SELF" supervise-kms', text)
        self.assertIn('launch_detached "$SELF" supervise-web', text)
        self.assertIn('setsid "$KMS_WRAPPER" --config "$CONFIG_PATH"', text)

    def test_web_supervisor_recovers_when_webrtc_venv_appears(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")
        supervise_web = text[text.index("supervise_web() {") : text.index("stop_supervisor() {")]

        self.assertIn('WEB_RECOVERY_INTERVAL="${WEB_RECOVERY_INTERVAL:-30}"', text)
        self.assertIn('WEB_PREFERRED_PY="${WEB_PREFERRED_PY:-/mnt/cache/appdata/kms_mosaic/venv/bin/python}"', text)
        self.assertIn("web_preferred_python_ready()", text)
        self.assertIn("web_pid_uses_preferred_python()", text)
        self.assertIn("import aiortc", text)
        self.assertIn("import av", text)
        self.assertIn('local next_dependency_check=$SECONDS', supervise_web)
        self.assertIn("web_preferred_python_ready && ! web_pid_uses_preferred_python", supervise_web)
        self.assertIn('"restarting-for-webrtc-deps"', supervise_web)
        self.assertIn('kill "$child"', supervise_web)

    def test_supervisors_record_and_restart_unexpected_exits(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("supervise_kms()", text)
        self.assertIn("supervise_web()", text)
        self.assertIn('"unexpected-exit"', text)
        self.assertIn('restart_in=${RESTART_DELAY}s', text)
        self.assertIn('LIFECYCLE_LOG="${LIFECYCLE_LOG:-/boot/config/plugins/${PLUGIN}/lifecycle.log}"', text)
        self.assertIn('trim_file "$LIFECYCLE_LOG" "$LIFECYCLE_MAX_BYTES"', text)
        self.assertIn('trim_file "$target" "$LOG_MAX_BYTES"', text)
        self.assertIn('stop_supervisor "$KMS_SUPERVISOR_PIDFILE" "supervise-kms"', text)
        self.assertIn('stop_supervisor "$WEB_SUPERVISOR_PIDFILE" "supervise-web"', text)
        supervise_kms = text[text.index("supervise_kms() {") : text.index("supervise_web() {")]
        self.assertIn('monitor_kms_child "$child" "$groups_file" "$reason_file" &', supervise_kms)
        self.assertIn('child_groups="$(awk', supervise_kms)
        self.assertIn('kill_process_group "$group"', supervise_kms)
        self.assertIn("cleaned_groups=", supervise_kms)

    def test_supervised_config_reload_uses_a_fresh_process(self) -> None:
        service = SERVICE_SCRIPT.read_text(encoding="utf-8")
        main = KMS_MAIN.read_text(encoding="utf-8")

        self.assertIn("KMS_MOSAIC_SUPERVISED_RELOAD=1", service)
        self.assertIn('if [ "$rc" -eq "$RELOAD_EXIT_CODE" ]; then', service)
        self.assertIn('"config-reload"', service)
        self.assertIn('getenv("KMS_MOSAIC_SUPERVISED_RELOAD")', main)
        self.assertIn("return 75;", main)

    def test_supervisor_restarts_a_live_process_that_stops_rendering(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("supervised_child_is_active()", text)
        self.assertIn("local next_health_check=$SECONDS", text)
        self.assertIn("monitor_kms_child()", text)
        self.assertIn("snapshot_fingerprint()", text)
        self.assertIn('touch "$SNAPSHOT_REQUEST"', text)
        self.assertIn('"render-timeout"', text)
        self.assertIn('"restarting-unresponsive"', text)
        self.assertIn('kill -9 "$child"', text)

    def test_kms_supervisor_restarts_a_failed_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            wrapper = tmp / "wrapper"
            count_file = tmp / "count"
            wrapper.write_text(
                "#!/bin/bash\n"
                f"printf 'run\\n' >> {count_file!s}\n"
                "exit 7\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "KMS_MOSAIC_CFG": str(tmp / "missing.cfg"),
                    "KMS_WRAPPER": str(wrapper),
                    "KMS_PIDFILE": str(tmp / "kms.pid"),
                    "KMS_SUPERVISOR_PIDFILE": str(tmp / "supervisor.pid"),
                    "KMS_STOP_FILE": str(tmp / "stop"),
                    "KMS_LOG": str(tmp / "kms.log"),
                    "LIFECYCLE_LOG": str(tmp / "lifecycle.log"),
                    "RESTART_DELAY": "0.05",
                    "SUPERVISOR_POLL_INTERVAL": "0.05",
                }
            )
            proc = subprocess.Popen(
                ["bash", str(SERVICE_SCRIPT), "supervise-kms"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    runs = count_file.read_text(encoding="utf-8").count("run") if count_file.exists() else 0
                    if runs >= 2:
                        break
                    time.sleep(0.05)
                self.assertGreaterEqual(runs, 2)
                (tmp / "stop").touch()
                proc.wait(timeout=6)
                lifecycle = (tmp / "lifecycle.log").read_text(encoding="utf-8")
                self.assertIn("event=unexpected-exit", lifecycle)
                self.assertIn("rc=7", lifecycle)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=6)

    def test_kms_supervisor_restarts_a_stalled_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir)
            wrapper = tmp / "wrapper"
            count_file = tmp / "count"
            pid_file = tmp / "kms.pid"
            stop_file = tmp / "stop"
            wrapper.write_text(
                "#!/bin/bash\n"
                f"printf 'run\\n' >> {count_file!s}\n"
                "trap 'exit 0' TERM INT HUP\n"
                "while :; do sleep 0.1; done\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "KMS_MOSAIC_CFG": str(tmp / "missing.cfg"),
                    "KMS_WRAPPER": str(wrapper),
                    "KMS_PIDFILE": str(pid_file),
                    "KMS_SUPERVISOR_PIDFILE": str(tmp / "supervisor.pid"),
                    "KMS_STOP_FILE": str(stop_file),
                    "KMS_LOG": str(tmp / "kms.log"),
                    "LIFECYCLE_LOG": str(tmp / "lifecycle.log"),
                    "SNAPSHOT_REQUEST": str(tmp / "snapshot.request"),
                    "SNAPSHOT_OUTPUT": str(tmp / "snapshot.rgba"),
                    "RUNTIME_HEALTH_INTERVAL": "1",
                    "RUNTIME_HEALTH_TIMEOUT": "1",
                    "HEALTH_RESTART_DELAY": "0.05",
                    "SUPERVISOR_POLL_INTERVAL": "0.05",
                }
            )
            proc = subprocess.Popen(
                ["bash", str(SERVICE_SCRIPT), "supervise-kms"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 8
                runs = 0
                while time.monotonic() < deadline:
                    runs = count_file.read_text(encoding="utf-8").count("run") if count_file.exists() else 0
                    if runs >= 2:
                        break
                    time.sleep(0.05)
                self.assertGreaterEqual(runs, 2)
                lifecycle = (tmp / "lifecycle.log").read_text(encoding="utf-8")
                self.assertIn("event=render-timeout", lifecycle)
                self.assertIn("event=restarting-unresponsive", lifecycle)
            finally:
                stop_file.touch()
                if pid_file.exists():
                    child_pid = int(pid_file.read_text(encoding="utf-8").strip())
                    try:
                        os.kill(child_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if proc.poll() is None:
                    proc.wait(timeout=6)

    def test_unraid_defaults_to_event_driven_atomic_presentation(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('KMS_ARGS="--atomic-nonblock"', text)
        self.assertIn('local args="${KMS_ARGS:-}"', text)
        self.assertIn('args="${args:+${args} }--debug"', text)

    def test_startup_ddc_is_configurable_and_connector_aware(self) -> None:
        text = SERVICE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('DDC_STARTUP="enable"', text)
        self.assertIn('DDC_CONNECTOR=""', text)
        self.assertIn('DDC_BUS=""', text)
        self.assertIn('Path("/sys/class/drm").glob("card*-*")', text)
        self.assertIn('if status.read_text().strip() != "connected"', text)
        self.assertIn('return unique[0] if len(unique) == 1 else ""', text)
        self.assertNotIn('BUS = "/dev/i2c-3"', text)


if __name__ == "__main__":
    unittest.main()
