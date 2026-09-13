from datetime import date
from fastapi import FastAPI, HTTPException, Query

from .sources.institute import crawl_pages, search_institute_info
from .sources.schedule import get_group_schedule

app = FastAPI(title="IFMIB KFU API", version="0.1.0")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/v1/institute/search")
async def institute_search(q: str = Query(..., min_length=2)):
    return {"query": q, "results": await search_institute_info(q)}

@app.get("/api/v1/institute/pages")
async def institute_pages():
    return {"pages": await crawl_pages()}

@app.get("/api/v1/schedule/group/{group}")
async def schedule_group(
    group: str,
    target_date: date | None = Query(default=None, alias="date"),
):
    try:
        return await get_group_schedule(group, target_date)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось получить расписание: {exc}") from exc
