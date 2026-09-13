import json
import httpx
from mcp.server.fastmcp import FastMCP

API_BASE = "http://127.0.0.1:8000/api/v1"
mcp = FastMCP("IFMIB KFU")

async def _get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{API_BASE}{path}", params=params)
        response.raise_for_status()
        return response.json()

@mcp.tool()
async def search_institute_info(query: str) -> str:
    # Search current institute information on official KFU/IFMIB pages.
    return json.dumps(
        await _get("/institute/search", {"q": query}),
        ensure_ascii=False,
        indent=2,
    )

@mcp.tool()
async def list_institute_pages() -> str:
    return json.dumps(
        await _get("/institute/pages"),
        ensure_ascii=False,
        indent=2,
    )

@mcp.tool()
async def get_group_schedule(group: str, target_date: str | None = None) -> str:
    params = {"date": target_date} if target_date else None
    return json.dumps(
        await _get(f"/schedule/group/{group}", params),
        ensure_ascii=False,
        indent=2,
    )

if __name__ == "__main__":
    mcp.run()
