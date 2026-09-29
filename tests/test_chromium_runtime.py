import importlib.util
import os
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_module(name, relative_path):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runtime = load_module("quillon_chromium_runtime_test", "quillon/core/chromium_runtime.py")
packager = load_module("quillon_chromium_packager_test", "scripts/package_chromium_build.py")


class ChromiumRuntimeTests(unittest.TestCase):
    def test_balanced_policy_is_serializable_and_private(self):
        policy = runtime.policy_for_mode("balanced")
        data = policy.to_dict()
        self.assertEqual(data["version"], runtime.POLICY_VERSION)
        self.assertEqual(runtime.PrivacyPolicy.from_dict(data), policy)
        flags = policy.flags()
        self.assertIn("--disable-sync", flags)
        self.assertIn("--disable-component-update", flags)
        self.assertIn("--metrics-recording-only", flags)
        self.assertNotIn("--disable-client-side-phishing-detection", flags)

    def test_invalid_policy_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            runtime.PrivacyPolicy(mode="unknown")

    def test_command_contains_profile_proxy_and_url(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            spec = runtime.ChromiumLaunchSpec(
                binary=Path("/opt/quillon/chrome"),
                profile_dir=profile,
                initial_url="https://www.youtube.com/",
                policy=runtime.policy_for_mode("standard"),
                proxy_url="http://127.0.0.1:8228",
                spki_fingerprint="abc123",
                extra_args=("--start-maximized",),
            )
            command = spec.command()
            self.assertEqual(command[0], "/opt/quillon/chrome")
            self.assertIn(f"--user-data-dir={profile}", command)
            self.assertIn("--proxy-server=http://127.0.0.1:8228", command)
            self.assertIn("--ignore-certificate-errors-spki-list=abc123", command)
            self.assertIn("--start-maximized", command)
            self.assertEqual(command[-1], "https://www.youtube.com/")
            self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o700)

    def test_command_rejects_unsupported_url(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = runtime.ChromiumLaunchSpec(
                binary=Path("/opt/quillon/chrome"),
                profile_dir=Path(directory) / "profile",
                initial_url="file:///etc/passwd",
            )
            with self.assertRaises(ValueError):
                spec.command()

    def test_explicit_binary_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "chrome"
            binary.write_text("#!/bin/sh\n", encoding="ascii")
            binary.chmod(0o700)
            self.assertEqual(runtime.find_chromium(str(binary)), binary)


class ChromiumPackagerTests(unittest.TestCase):
    def test_package_contains_runtime_files_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory) / "out"
            build.mkdir()
            (build / "chrome").write_text("binary", encoding="ascii")
            (build / "resources.pak").write_bytes(b"pak")
            (build / "obj").mkdir()
            output = Path(directory) / "dist" / "quillon.tar.gz"
            artifact, checksum = packager.package(build, output)
            self.assertTrue(artifact.is_file())
            self.assertTrue(checksum.is_file())
            with tarfile.open(artifact, "r:gz") as archive:
                names = set(archive.getnames())
            self.assertIn("chrome", names)
            self.assertIn("resources.pak", names)
            self.assertNotIn("obj", names)


if __name__ == "__main__":
    unittest.main()
