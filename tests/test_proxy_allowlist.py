import importlib.util
import re
import unittest
from pathlib import Path


path = Path(__file__).parents[1] / "bfsb" / "core" / "proxy_bootstrap.py"
spec = importlib.util.spec_from_file_location("bfsb_proxy_bootstrap_test", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AllowlistTests(unittest.TestCase):
    def test_reviewed_hosts_match(self):
        pattern = re.compile(module.ALLOW_HOSTS_REGEX)
        for host in ("youtube.com:443", "www.youtube.com:443", "r1.googlevideo.com:443"):
            self.assertRegex(host, pattern)

    def test_unreviewed_and_confusable_hosts_do_not_match(self):
        pattern = re.compile(module.ALLOW_HOSTS_REGEX)
        for host in (
            "youtube.com.evil:443",
            "evil.youtube.com:443",
            "youtube-nocookie.com:443",
            "ytimg.com:443",
            "youtube.com:443.evil",
        ):
            self.assertNotRegex(host, pattern)

    def test_process_detection_handles_nul_delimited_argv(self):
        raw = "mitmdump\0--listen-port\08228\0-s\0/proj/bfsb/core/proxy_addon.py\0"
        self.assertTrue(module._is_bfsb_mitmprocess(raw, 8228))
        self.assertFalse(module._is_bfsb_mitmprocess("mitmdump\0--listen-port\09080\0-s\0/proj/bfsb/core/proxy_addon.py\0", 8228))

    def test_occupied_unrecognized_proxy_fails_closed(self):
        # This reached the real _cleanup_recorded_proxy(), which reads the
        # shared ~/.bfsb/proxy.pid. Running the suite with BFSB open would
        # SIGTERM the live proxy and give the user ERR_PROXY_CONNECTION_
        # FAILED. Redirect the pid file AND neutralise the kill.
        import tempfile
        from pathlib import Path

        original_port_check = module._port_open
        original_pid_check = module._mitmdump_pids_on_port
        original_pid_file = module.PROXY_PID_FILE
        original_cleanup = module._cleanup_recorded_proxy
        with tempfile.TemporaryDirectory() as tmp:
            module.PROXY_PID_FILE = Path(tmp) / "proxy.pid"
            module._cleanup_recorded_proxy = lambda: None
            module._port_open = lambda host, port: True
            module._mitmdump_pids_on_port = lambda port: []
            try:
                self.assertIsNone(module.ensure_proxy_running(timeout=0.01))
            finally:
                module._port_open = original_port_check
                module._mitmdump_pids_on_port = original_pid_check
                module.PROXY_PID_FILE = original_pid_file
                module._cleanup_recorded_proxy = original_cleanup


if __name__ == "__main__":
    unittest.main()
