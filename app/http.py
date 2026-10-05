from __future__ import annotations

import httpx

from .config import settings

_CLIENT_KWARGS = {
    "timeout": settings.request_timeout,
    "follow_redirects": True,
    "verify": False,  # Russian edu sites often use Mintsifry certs not in default trust store
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
