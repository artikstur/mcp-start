from __future__ import annotations

import httpx
import os

from .config import settings


def _tls_verify(environ: dict[str, str] | None = None) -> bool | str:
    values = os.environ if environ is None else environ
    return values.get("KPFU_CA_BUNDLE") or True


_CLIENT_KWARGS = {
    "timeout": settings.request_timeout,
    "follow_redirects": True,
    "verify": _tls_verify(),
}


async def get_text(url: str) -> str:
    async with httpx.AsyncClient(**_CLIENT_KWARGS) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def get_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(**_CLIENT_KWARGS) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
