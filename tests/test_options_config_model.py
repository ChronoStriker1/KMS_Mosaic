import pathlib
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OPTIONS_C = REPO_ROOT / "src" / "options.c"


class OptionsConfigModelTests(unittest.TestCase):
    def _probe_config(self, config_text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = pathlib.Path(tmpdir)
            (tmp / "mpv").mkdir()
            (tmp / "mpv" / "client.h").write_text(
                textwrap.dedent(
                    """
                    typedef struct mpv_handle mpv_handle;
                    typedef struct mpv_node_list {
                        int num;
                        struct mpv_node *values;
                        char **keys;
                    } mpv_node_list;
                    typedef union mpv_node_u {
                        char *string;
                        mpv_node_list *list;
                    } mpv_node_u;
                    typedef struct mpv_node {
                        int format;
                        mpv_node_u u;
                    } mpv_node;
                    #define MPV_FORMAT_STRING 1
                    #define MPV_FORMAT_NODE_ARRAY 7
                    #define MPV_FORMAT_NODE_MAP 8
                    int mpv_command_async(mpv_handle *ctx, unsigned long long reply_userdata, const char **args);
                    int mpv_command_node_async(mpv_handle *ctx, unsigned long long reply_userdata, mpv_node *args);
                    void mpv_free_node_contents(mpv_node *node);
                    """
                ),
                encoding="utf-8",
            )
            config = tmp / "kms_mosaic.conf"
            config.write_text(textwrap.dedent(config_text).strip(), encoding="utf-8")
            probe = tmp / "options_probe.c"
            probe.write_text(
                textwrap.dedent(
                    f"""
                    #include <stdio.h>
                    #include <string.h>
                    #include "options.h"

                    int mpv_command_async(mpv_handle *ctx, unsigned long long reply_userdata, const char **args) {{
                        (void)ctx;
                        (void)reply_userdata;
                        (void)args;
                        return 0;
                    }}

                    int mpv_command_node_async(mpv_handle *ctx, unsigned long long reply_userdata, mpv_node *args) {{
                        (void)ctx;
                        (void)reply_userdata;
                        (void)args;
                        return 0;
                    }}

                    void mpv_free_node_contents(mpv_node *node) {{
                        (void)node;
                    }}

                    int main(void) {{
                        options_t opt = {{0}};
                        int debug = 0;
                        char *argv[] = {{
                            "options_probe",
                            "--config",
                            "{config}",
                            NULL
                        }};
                        int rc = options_parse_cli(&opt, 3, argv, &debug);
                        if (rc != 0) {{
                            fprintf(stderr, "parse failed: %d\\n", rc);
                            return rc;
                        }}
                        printf("pane_count=%d\\n", opt.pane_count);
                        printf("pane0_cmd=%s\\n", opt.pane_cmds[0] ? opt.pane_cmds[0] : "(null)");
                        printf("pane1_cmd=%s\\n", opt.pane_cmds[1] ? opt.pane_cmds[1] : "(null)");
                        printf("pane2_cmd=%s\\n", opt.pane_cmds[2] ? opt.pane_cmds[2] : "(null)");
                        printf("pane0_media=%d\\n", opt.pane_media[0].enabled ? 1 : 0);
                        printf("pane1_media=%d\\n", opt.pane_media[1].enabled ? 1 : 0);
                        printf("pane2_media=%d\\n", opt.pane_media[2].enabled ? 1 : 0);
                        printf("split_tree=%s\\n", opt.split_tree_spec ? opt.split_tree_spec : "(null)");
                        options_destroy(&opt);
                        return 0;
                    }}
                    """
                ),
                encoding="utf-8",
            )

            binary = tmp / "options_probe"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-include",
                    "stddef.h",
                    f"-I{tmp}",
                    f"-I{REPO_ROOT / 'src'}",
                    str(OPTIONS_C),
                    str(probe),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [str(binary)],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip().splitlines()

    def test_unified_config_file_without_explicit_pane_model_does_not_reintroduce_hidden_video_slot(self):
        self.assertEqual(
            self._probe_config(
                """
                --rotate 270
                --split-tree 'row:50(0,col:50(1,2))'
                --layout 2over1
                --pane-count 3
                --pane-a 'btop --utf-force'
                --pane-b 'tail -F /var/log/syslog'
                --pane-media 3
                --pane-video 3 /mnt/user/demo.mp4
                """
            ),
            [
                "pane_count=3",
                "pane0_cmd=btop --utf-force",
                "pane1_cmd=tail -F /var/log/syslog",
                "pane2_cmd=(null)",
                "pane0_media=0",
                "pane1_media=0",
                "pane2_media=1",
                "split_tree=row:50(0,col:50(1,2))",
            ],
        )

    def test_root_media_config_without_explicit_pane_model_still_infers_legacy_hidden_video_slot(self):
        self.assertEqual(
            self._probe_config(
                """
                --layout 2x1
                --pane-count 3
                --split-tree 'col:60(0,row:50(1,col:50(2,3)))'
                --roles CDBA
                --video /media/main.mp4
                --mpv-opt audio=no
                --pane-a htop
                --pane-b 'tail -f /tmp/app.log'
                --pane-media 3
                --pane-video 3 /media/pane3.mp4
                """
            ),
            [
                "pane_count=4",
                "pane0_cmd=(null)",
                "pane1_cmd=htop",
                "pane2_cmd=tail -f /tmp/app.log",
                "pane0_media=0",
                "pane1_media=0",
                "pane2_media=0",
                "split_tree=col:60(0,row:50(1,col:50(2,3)))",
            ],
        )


if __name__ == "__main__":
    unittest.main()
