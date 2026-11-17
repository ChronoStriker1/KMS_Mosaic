import pathlib
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
EDID_C = REPO_ROOT / "src" / "edid.c"

INGNOK_EDID = bytes.fromhex(
    "00ffffffffffff000614712200000000092101038000007828ee91a3544c99260f50542108"
    "0071408180a9c0d1c00101010101010101023a801871382d40582c250058c31000001e0000"
    "00fc00696e676e6f6b0a202020202020000000ff000a202020202020202020202020000000"
    "fd00324b1e5414000a202020202020017e"
)
CONNECTOR_EDID = bytes.fromhex(
    "00ffffffffffff005873451131154500001f0103800000000ae5b5a355499927135054a308"
    "00d1c0b30081008180814081c001010101023a801871382d40582c4500c01c3200001a7f21"
    "56aa51001e30468f33003f432100001e000000fd00324c1e5010000a202020202020000000"
    "fc00436f6e6e6563746f720a202020012c"
)


class EdidMonitorNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(cls._tmp.name)
        probe = tmp / "edid_probe.c"
        probe.write_text(
            textwrap.dedent(
                """
                #include <stdio.h>
                #include "edid.h"

                int main(void) {
                    uint8_t buf[512];
                    size_t len = fread(buf, 1, sizeof(buf), stdin);
                    char name[EDID_MONITOR_NAME_MAX];
                    if (!edid_monitor_name(buf, len, name)) return 3;
                    printf("%s", name);
                    return 0;
                }
                """
            ),
            encoding="utf-8",
        )
        cls.binary = tmp / "edid_probe"
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", f"-I{REPO_ROOT / 'src'}",
             str(EDID_C), str(probe), "-o", str(cls.binary)],
            check=True,
            capture_output=True,
            text=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _name(self, edid: bytes) -> str | None:
        result = subprocess.run([str(self.binary)], input=edid, capture_output=True, check=False)
        return result.stdout.decode() if result.returncode == 0 else None

    def test_reads_monitor_name_from_tower_edids(self) -> None:
        self.assertEqual(self._name(INGNOK_EDID), "ingnok")
        self.assertEqual(self._name(CONNECTOR_EDID), "Connector")

    def test_monitor_name_survives_cea_extension_block(self) -> None:
        self.assertEqual(self._name(INGNOK_EDID + bytes(128)), "ingnok")

    def test_edid_without_name_descriptor_has_no_name(self) -> None:
        unnamed = bytearray(INGNOK_EDID)
        unnamed[54 + 18 + 3] = 0xFE
        self.assertIsNone(self._name(bytes(unnamed)))

    def test_rejects_truncated_or_headerless_data(self) -> None:
        self.assertIsNone(self._name(INGNOK_EDID[:100]))
        self.assertIsNone(self._name(bytes(128)))

    def test_quote_and_control_bytes_are_replaced(self) -> None:
        odd = bytearray(INGNOK_EDID)
        start = 54 + 18 + 5
        odd[start:start + 6] = b'a"b\x01cd'
        self.assertEqual(self._name(bytes(odd)), "a?b?cd")


if __name__ == "__main__":
    unittest.main()
