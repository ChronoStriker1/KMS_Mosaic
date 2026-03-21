import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICE_SCRIPT = ROOT / "unraid-plugin" / "package-root" / "usr" / "local" / "emhttp" / "plugins" / "kms.mosaic" / "scripts" / "kms_mosaic-service"


class UnraidServiceScriptTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
