from __future__ import annotations

import io
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pypdf import PdfReader
from openpyxl import load_workbook

from ..config import settings
from ..http import get_bytes, get_text

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

async def _published_documents() -> list[dict]:
    html = await get_text(settings.schedule_page_url)
    soup = BeautifulSoup(html, "html.parser")
    docs = []
    for a in soup.find_all("a", href=True):
        href = urljoin(settings.schedule_page_url, a["href"])
        label = _norm(a.get_text(" ", strip=True))
        if href.lower().endswith((".pdf", ".xlsx", ".xls")):
            docs.append({"title": label, "url": href})
    return docs

def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return _norm("\n".join(page.extract_text() or "" for page in reader.pages))

def _extract_xlsx(data: bytes) -> str:
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    chunks = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            chunks.append(" ".join(str(v) for v in row if v is not None))
    return _norm("\n".join(chunks))

def _extract_document(url: str, data: bytes) -> str:
    low = url.lower()
    if low.endswith(".pdf"):
        return _extract_pdf(data)
    if low.endswith(".xlsx"):
        return _extract_xlsx(data)
    return ""

async def get_group_schedule(group: str, target_date: date | None = None) -> dict:
    docs = await _published_documents()
    group_l = group.lower()
    matches = []

    for doc in docs:
        try:
            data = await get_bytes(doc["url"])
            text = _extract_document(doc["url"], data)
        except Exception:
            continue

        if group_l not in text.lower():
            continue

        pos = text.lower().find(group_l)
        matches.append({
            "group": group,
            "date": target_date.isoformat() if target_date else None,
            "source_title": doc["title"],
            "source_url": doc["url"],
            "context": text[max(0, pos - 800):pos + 2500],
        })

    if matches:
        return {
            "status": "ok",
            "group": group,
            "date": target_date.isoformat() if target_date else None,
            "sources": matches,
            "source_page": settings.schedule_page_url,
        }

    return {
        "status": "not_found_in_public_documents",
        "group": group,
        "date": target_date.isoformat() if target_date else None,
        "source_page": settings.schedule_page_url,
        "message": (
            "Группа не найдена в опубликованных PDF/XLSX. "
            "Официальная страница ИФМиБ указывает ИАС "
            "«Электронный университет» как источник актуального "
            "расписания. Для закрытой части ИАС нужен отдельный "
            "официальный способ авторизации/API."
        ),
    }
