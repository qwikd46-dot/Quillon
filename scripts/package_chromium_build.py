#!/usr/bin/env python3
"""Package Chromium runtime files after a build."""

from __future__ import annotations

import argparse
import hashlib
import tarfile
from pathlib import Path


RUNTIME_PATTERNS = (
    "chrome",
    "chrome_sandbox",
    "*.pak",
    "icudtl.dat",
    "snapshot_blob.bin",
    "v8_context_snapshot.bin",
    "libEGL.so",
    "libGLESv2.so",
    "libvk_swiftshader.so",
    "vk_swiftshader_icd.json",
    "locales",
    "swiftshader",
)


def runtime_files(build_dir: Path) -> list[Path]:
    files: set[Path] = set()
    for pattern in RUNTIME_PATTERNS:
        for path in build_dir.glob(pattern):
            if path.is_file() or path.is_dir():
                files.add(path)
    binary = build_dir / "chrome"
    if not binary.is_file():
        raise FileNotFoundError(f"missing Chromium binary: {binary}")
    if not files:
        raise FileNotFoundError(f"no runtime files found in {build_dir}")
    return sorted(files, key=lambda path: path.name)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package(build_dir: Path, output: Path) -> tuple[Path, Path]:
    build_dir = build_dir.resolve()
    output = output.resolve()
    files = runtime_files(build_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=path.relative_to(build_dir), recursive=True)
    checksum = output.with_suffix(output.suffix + ".sha256")
    checksum.write_text(f"{sha256_file(output)}  {output.name}\n", encoding="ascii")
    return output, checksum


def main() -> int:
    parser = argparse.ArgumentParser(description="Package a Chromium build")
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("dist/quillon-chromium.tar.gz"))
    args = parser.parse_args()
    try:
        output, checksum = package(args.build_dir, args.output)
    except (FileNotFoundError, OSError) as error:
        print(f"packaging failed: {error}")
        return 1
    print(f"artifact={output}")
    print(f"checksum={checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
