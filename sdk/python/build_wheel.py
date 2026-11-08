#!/usr/bin/env python3
"""Build the SDK wheel, optionally with the native decoder for compressed transfer (docs/compressed-transfer.md).

  python build_wheel.py --out DIR                                  # pure wheel (py3-none-any), raw reads only
  python build_wheel.py --out DIR --lib PATH --platform TAG        # platform wheel with the decoder in _lib/

PATH is the built crates/morlet-ffi library (libmorlet_ffi.so or .dylib); TAG a wheel platform tag such as
manylinux_2_28_x86_64, manylinux_2_28_aarch64 or macosx_11_0_arm64. The library is plain C ABI and independent of
the Python version, so the wheel is tagged py3-none-<TAG>. Needs `build` and `wheel` (pinned in CI)."""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB_DIR = HERE / "src" / "morletdb" / "_lib"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--lib", type=Path)
    ap.add_argument("--platform")
    a = ap.parse_args()
    if bool(a.lib) != bool(a.platform):
        sys.exit("--lib and --platform go together")
    a.out.mkdir(parents=True, exist_ok=True)
    if LIB_DIR.exists():
        sys.exit(f"{LIB_DIR} exists; remove it (it is created only while a platform wheel is built)")
    try:
        if a.lib:
            LIB_DIR.mkdir()
            shutil.copy2(a.lib, LIB_DIR / a.lib.name)
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", tmp, str(HERE)], check=True)
            (whl,) = Path(tmp).glob("*.whl")
            if a.lib:
                subprocess.run([sys.executable, "-m", "wheel", "tags", "--remove", "--python-tag", "py3",
                                "--abi-tag", "none", "--platform-tag", a.platform, str(whl)], check=True)
                (whl,) = Path(tmp).glob("*.whl")
            shutil.copy2(whl, a.out / whl.name)
            print(a.out / whl.name)
    finally:
        if a.lib and LIB_DIR.exists():
            shutil.rmtree(LIB_DIR)
        shutil.rmtree(HERE / "build", ignore_errors=True)


if __name__ == "__main__":
    main()
