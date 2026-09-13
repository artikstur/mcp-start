from __future__ import annotations

import subprocess

from .config import settings


async def get_text(url: str) -> str:
    result = subprocess.run(
        [
            "curl.exe",
            "--ssl-no-revoke",
            "-L",
            "--max-time",
            str(settings.request_timeout),
            url,
        ],
        capture_output=True,
        check=True,
    )

    return result.stdout.decode("cp1251", errors="replace")


async def get_bytes(url: str) -> bytes:
    result = subprocess.run(
        [
            "curl.exe",
            "--ssl-no-revoke",
            "-L",
            "--max-time",
            str(settings.request_timeout),
            url,
        ],
        capture_output=True,
        check=True,
    )

    return result.stdout