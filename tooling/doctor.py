"""Repository toolchain and configuration diagnostics."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

from aieos_api.settings import HostSettings

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYTHON = (3, 13)


def main() -> int:
    """Validate the local bootstrap without printing secret values."""
    failures: list[str] = []
    version = platform.python_version_tuple()
    if tuple(map(int, version[:2])) != EXPECTED_PYTHON:
        failures.append(f"CPython 3.13 required; found {platform.python_version()}")
    if shutil.which("uv") is None and os.environ.get("UV") is None:
        failures.append("pinned uv is unavailable")
    if not (ROOT / "uv.lock").exists():
        failures.append("uv.lock is missing")
    try:
        HostSettings()
    except ValueError as error:
        failures.append(f"configuration invalid: {error}")
    if failures:
        print("\n".join(failures))
        return 1
    print("doctor: toolchain, lockfile, and configuration are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
