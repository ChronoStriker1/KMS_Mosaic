import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_README = ROOT / "unraid-plugin" / "package-root" / "usr" / "local" / "emhttp" / "plugins" / "kms.mosaic" / "README.md"
PLUGIN_MANIFEST = ROOT / "unraid-plugin" / "kms.mosaic.plg"
CONTAINER_BUILD = ROOT / "scripts" / "_container_build.sh"
MACOS_BUILD = ROOT / "scripts" / "macos_build_pkg.sh"
HOST_GPU_LIB_PATTERNS = [
    "libdrm*.so.*",
    "libgbm.so.*",
    "libEGL*.so.*",
    "libGLESv2.so.*",
    "libGL*.so.*",
    "libOpenGL.so.*",
    "libgallium*.so.*",
    "libX11*.so.*",
    "libXau.so.*",
    "libXdmcp.so.*",
    "libXext.so.*",
    "libxcb*.so.*",
    "libwayland*.so.*",
    "libxshmfence.so.*",
    "libpciaccess.so.*",
    "libLLVM*.so.*",
    "libSPIRV*.so.*",
    "libsensors.so.*",
    "libelf.so.*",
    "libedit.so.*",
    "libexpat.so.*",
    "libz.so.*",
    "libzstd.so.*",
    "liblzma.so.*",
    "libbz2.so.*",
    "libffi.so.*",
    "libncursesw.so.*",
    "libtinfo.so.*",
]


class UnraidPluginMetadataTests(unittest.TestCase):
    def test_packaged_plugin_readme_exists_for_installed_plugins_listing(self) -> None:
        text = PLUGIN_README.read_text(encoding="utf-8")

        self.assertIn("# KMS Mosaic", text)
        self.assertIn("pane layout editing", text)
        self.assertIn("live preview", text)

    def test_package_build_excludes_host_gpu_driver_stack(self) -> None:
        for script_path in (CONTAINER_BUILD, MACOS_BUILD):
            with self.subTest(script=script_path.name):
                text = script_path.read_text(encoding="utf-8")
                for lib_pattern in HOST_GPU_LIB_PATTERNS:
                    self.assertIn(lib_pattern, text)
                self.assertIn("never ship the host GPU driver stack", text)
                self.assertIn('"$LIBDIR"/libEGL*.so*', text)
                self.assertIn('"$LIBDIR"/libxcb*.so*', text)
                self.assertIn('"$LIBDIR"/libexpat.so*', text)
                self.assertIn('rm -f "$LIBDIR"/libdrm*.so*', text)

    def test_plugin_install_removes_stale_packaged_gl_libraries(self) -> None:
        text = PLUGIN_MANIFEST.read_text(encoding="utf-8")

        for lib_pattern in HOST_GPU_LIB_PATTERNS:
            install_cleanup_pattern = lib_pattern.replace(".so.*", ".so*")
            self.assertIn(f"/usr/local/lib/kms_mosaic/{install_cleanup_pattern}", text)


if __name__ == "__main__":
    unittest.main()
