from __future__ import annotations

import json
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from .sources.institute import crawl_pages, search_institute_info as _search
from .sources.schedule import get_group_schedule as _get_schedule

mcp = FastMCP("IFMIB KFU")


@mcp.tool()
async def search_institute_info(query: str) -> str:
    """Search current institute information on official KFU/IFMIB pages."""
    results = await _search(query)
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_institute_pages() -> str:
    """Return a list of crawled IFMIB pages with titles and URLs."""
    pages = await crawl_pages()
    return json.dumps(pages, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_group_schedule(group: str, target_date: str | None = None) -> str:
    """Get schedule for a student group. target_date format: YYYY-MM-DD."""
    parsed_date = None
    if target_date:
        try:
            parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            pass
    result = await _get_schedule(group, parsed_date)
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
