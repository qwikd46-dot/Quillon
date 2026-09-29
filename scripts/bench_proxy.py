import argparse
import json
import shutil
import socket
import statistics
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


class Handler(BaseHTTPRequestHandler):
    payload = b"Quillon proxy benchmark payload\n" * 64

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, format_string, *args):
        return


def percentile(values, percent):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percent / 100) * len(ordered) + 0.5)) - 1))
    return ordered[index]


def measure(url, count):
    samples = []
    opener = build_opener()
    for _ in range(count):
        started = time.perf_counter()
        with opener.open(Request(url), timeout=15) as response:
            first_byte = time.perf_counter()
            response.read()
            finished = time.perf_counter()
        samples.append({
            "total_ms": (finished - started) * 1000,
            "first_byte_ms": (first_byte - started) * 1000,
        })
    return {
        "p50_ms": round(statistics.median(item["total_ms"] for item in samples), 2),
        "p95_ms": round(percentile([item["total_ms"] for item in samples], 95), 2),
        "first_byte_p50_ms": round(statistics.median(item["first_byte_ms"] for item in samples), 2),
        "samples": len(samples),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--proxy-port", type=int, default=8229)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    direct_url = f"http://{args.host}:{server_port}/payload"
    result = {"direct": measure(direct_url, args.requests), "proxied": None}
    mitmdump = shutil.which("mitmdump")
    if mitmdump is None:
        result["skipped"] = "mitmdump is not available on PATH"
        server.shutdown()
        print(json.dumps(result, indent=2))
        return 0

    with tempfile.TemporaryDirectory(prefix="quillon-bench-") as directory:
        addon = Path(__file__).parents[1] / "quillon" / "core" / "proxy_addon.py"
        command = [
            mitmdump,
            "--listen-host", args.host,
            "--listen-port", str(args.proxy_port),
            "--set", "confdir=" + directory,
            "--set", "block_global=false",
            "--allow-hosts", rf"^{re_escape(args.host)}$",
            "-s", str(addon),
            "--quiet",
        ]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    with socket.create_connection((args.host, args.proxy_port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            proxy_url = f"http://{args.host}:{args.proxy_port}"
            opener = build_opener(ProxyHandler({"http": proxy_url}))
            samples = []
            for _ in range(args.requests):
                started = time.perf_counter()
                with opener.open(Request(direct_url), timeout=15) as response:
                    first_byte = time.perf_counter()
                    response.read()
                    finished = time.perf_counter()
                samples.append({"total_ms": (finished - started) * 1000, "first_byte_ms": (first_byte - started) * 1000})
            result["proxied"] = {
                "p50_ms": round(statistics.median(item["total_ms"] for item in samples), 2),
                "p95_ms": round(percentile([item["total_ms"] for item in samples], 95), 2),
                "first_byte_p50_ms": round(statistics.median(item["first_byte_ms"] for item in samples), 2),
                "samples": len(samples),
            }
        finally:
            process.terminate()
            process.wait(timeout=5)
            server.shutdown()
    print(json.dumps(result, indent=2))
    return 0


def re_escape(value):
    import re
    return re.escape(value)


if __name__ == "__main__":
    raise SystemExit(main())
