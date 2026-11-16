from __future__ import annotations

import json
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from .sources.institute import crawl_pages, search_institute_info as _search
from .sources.schedule import get_group_schedule as _get_schedule
from .rag import search_documents

mcp = FastMCP("IFMIB KFU")


@mcp.tool()
async def search_institute_info(query: str) -> str:
    """Search current institute information on official KFU/IFMIB pages."""
    results = await _search(query)
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_institute_pages() -> str:
    """Return only titles and URLs of crawled IFMIB pages, without page text."""
    pages = await crawl_pages()
    metadata = [{"title": page["title"], "url": page["url"]} for page in pages]
    return json.dumps(metadata, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_group_schedule(group: str, target_date: str | None = None) -> str:
    """Get schedule for a student group. target_date format: YYYY-MM-DD."""
    parsed_date = None
    if target_date:
        try:
            parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            return json.dumps({
                "status": "invalid_date",
                "group": group,
                "date": target_date,
                "message": "Дата должна существовать и иметь формат YYYY-MM-DD.",
            }, ensure_ascii=False, indent=2)
    result = await _get_schedule(group, parsed_date)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def search_docs(query: str) -> str:
    """Search indexed local documents and return source IDs, URLs, and excerpts."""
    result = await search_documents(query)
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
