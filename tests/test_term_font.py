import pathlib
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TINY5_MONO = REPO_ROOT / "fonts" / "Tiny5Mono.bdf"

CELL_A = [
    "......",
    "......",
    ".###..",
    "#...#.",
    "#####.",
    "#...#.",
    "#...#.",
    "......",
    "......",
]
CELL_BOX_HORIZONTAL = [
    "......",
    "......",
    "......",
    "......",
    "######",
    "......",
    "......",
    "......",
    "......",
]


def upscale(rows: list[str], scale: int) -> list[str]:
    return [("".join(ch * scale for ch in row)) for row in rows for _ in range(scale)]


class TermFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(cls._tmp.name)
        probe = tmp / "term_font_probe.c"
        probe.write_text(
            textwrap.dedent(
                """
                #include <stdio.h>
                #include <stdlib.h>
                #include "term_font.h"

                int main(int argc, char **argv) {
                    term_font f;
                    if (!term_font_open(&f, argv[1])) return 2;
                    if (!term_font_set_px(&f, atoi(argv[2]))) return 3;
                    printf("%d %d %d %d\\n", f.scale, f.cell_w, f.cell_h, f.baseline);
                    for (int i = 3; i < argc; ++i) {
                        term_glyph g;
                        if (!term_font_render(&f, (uint32_t)strtoul(argv[i], NULL, 0), &g)) return 4;
                        for (int y = 0; y < f.cell_h; ++y) {
                            for (int x = 0; x < f.cell_w; ++x) {
                                int gx = x - g.x, gy = y - g.y;
                                int on = gx >= 0 && gy >= 0 && gx < g.w && gy < g.h && g.alpha[gy * g.w + gx] > 127;
                                putchar(on ? '#' : '.');
                            }
                            putchar('\\n');
                        }
                        free(g.alpha);
                    }
                    term_font_close(&f);
                    return 0;
                }
                """
            ),
            encoding="utf-8",
        )
        cls.binary = tmp / "term_font_probe"
        flags = subprocess.run(
            ["pkg-config", "--cflags", "--libs", "freetype2", "fontconfig"],
            check=True, capture_output=True, text=True,
        ).stdout.split()
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", f"-I{REPO_ROOT / 'src'}",
             str(probe), str(REPO_ROOT / "src" / "term_font.c"), str(REPO_ROOT / "src" / "font_util.c"),
             "-o", str(cls.binary), *flags],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _probe(self, path: str, font_px: int, *codepoints: int) -> tuple[list[int], list[str]]:
        result = subprocess.run(
            [str(self.binary), path, str(font_px), *(hex(cp) for cp in codepoints)],
            check=True, capture_output=True, text=True,
        )
        lines = result.stdout.splitlines()
        return [int(v) for v in lines[0].split()], lines[1:]

    def test_tiny5_mono_cells_are_whole_multiples_of_the_6x9_bitmap(self) -> None:
        cases = {8: [1, 6, 9, 7], 17: [1, 6, 9, 7], 18: [2, 12, 18, 14], 26: [2, 12, 18, 14], 27: [3, 18, 27, 21]}
        for font_px, expected in cases.items():
            with self.subTest(font_px=font_px):
                metrics, _ = self._probe(str(TINY5_MONO), font_px)
                self.assertEqual(metrics, expected)

    def test_tiny5_mono_glyphs_land_on_the_pixel_grid(self) -> None:
        for font_px, scale in ((9, 1), (18, 2), (27, 3)):
            with self.subTest(font_px=font_px):
                _, rows = self._probe(str(TINY5_MONO), font_px, ord("A"), 0x2500)
                cell_rows = 9 * scale
                self.assertEqual(rows[:cell_rows], upscale(CELL_A, scale))
                self.assertEqual(rows[cell_rows:], upscale(CELL_BOX_HORIZONTAL, scale))

    def test_missing_font_falls_back_to_scalable_monospace(self) -> None:
        metrics, _ = self._probe("/nonexistent/font.bdf", 18)
        scale, cell_w, cell_h, _ = metrics
        self.assertEqual(scale, 0)
        self.assertEqual(cell_h, 20)
        self.assertGreater(cell_w, 0)


if __name__ == "__main__":
    unittest.main()
