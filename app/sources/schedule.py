from __future__ import annotations

import asyncio
import json
import io
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from pypdf import PdfReader
from openpyxl import load_workbook

from ..config import settings
from ..http import get_bytes, get_text

MAX_PARALLEL_SCHEDULE_DOWNLOADS = 6
SCHEDULE_CACHE_TTL_SECONDS = 15 * 60
BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = Path(os.getenv(
    "MCP_START_SCHEDULE_CACHE", str(BASE_DIR / "data" / "schedule_cache.json")
))

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_relevant_schedule_document(doc: dict) -> bool:
    title = _norm(str(doc.get("title", ""))).casefold().replace("ё", "е")
    url = str(doc.get("url", "")).casefold().replace("ё", "е")
    haystack = f"{title} {url}"
    if re.search(r"распоряж|экзамен|зачет|дополнительн.{0,20}сесс|комиссионн", haystack):
        return False
    is_course_title = re.fullmatch(r"\d+\s*(?:курс|course)", title) is not None
    return is_course_title or any(token in haystack for token in ("расписание", "raspisanie", "timetable"))


async def _published_documents() -> list[dict]:
    html = await get_text(settings.schedule_page_url)
    soup = BeautifulSoup(html, "html.parser")
    docs = []
    for a in soup.find_all("a", href=True):
        href = urljoin(settings.schedule_page_url, a["href"])
        label = _norm(a.get_text(" ", strip=True))
        doc = {"title": label, "url": href}
        if href.lower().endswith((".pdf", ".xlsx", ".xls")) and _is_relevant_schedule_document(doc):
            docs.append(doc)
    return docs

def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return _norm("\n".join(page.extract_text() or "" for page in reader.pages))

def _cell_text(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _norm(str(value)) if value is not None else ""


def _extract_xlsx_payload_from_workbook(wb) -> dict:
    text_parts = []
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [_cell_text(value) for value in row]
            if any(cells):
                rows.append(cells)
                text_parts.append(" ".join(cell for cell in cells if cell))
        sheets.append({"name": ws.title, "rows": rows})
    return {"text": _norm("\n".join(text_parts)), "sheets": sheets}


def _extract_xlsx_payload(data: bytes) -> dict:
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    return _extract_xlsx_payload_from_workbook(wb)


def _extract_xlsx(data: bytes) -> str:
    return _extract_xlsx_payload(data)["text"]


_WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
    "пятница": 4, "суббота": 5, "воскресенье": 6,
}


def _normalize_group(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]", "", value.casefold()).replace("ё", "е")


def _academic_week(target_date: date, term_start: date) -> int:
    first_monday = term_start - timedelta(days=term_start.weekday())
    return (target_date - first_monday).days // 7 + 1


def _class_applies_to_week(text: str, week: int) -> bool:
    normalized = text.casefold().replace("ё", "е")
    ranges = re.findall(r"(?<!\d)(\d{1,2})\s*[-–]\s*(\d{1,2})\s*(?:нед|недел)", normalized)
    if ranges and not any(int(first) <= week <= int(last) for first, last in ranges):
        return False
    without_ranges = re.sub(r"\d{1,2}\s*[-–]\s*\d{1,2}\s*(?:нед|недел)", "", normalized)
    specific_weeks = [int(value) for value in re.findall(r"(?<![\d-])(\d{1,2})\s*(?:нед|недел)", without_ranges)]
    if specific_weeks and week not in specific_weeks:
        return False
    if re.search(r"(?<![а-я])нечет(?:н(?:ая|ой|ую|ые|ых|ым|ыми))?\.?\s*нед", normalized) and week % 2 == 0:
        return False
    if re.search(r"(?<![а-я])чет(?:н(?:ая|ой|ую|ые|ых|ым|ыми))?\.?\s*нед", normalized) and week % 2 == 1:
        return False
    return True


def _cell_time(text: str) -> tuple[str | None, str]:
    """Return an explicit time prefix from a class cell, if it has one."""
    match = re.match(r"^\s*(\d{1,2}[.:]\d{2}\s*[-–]\s*\d{1,2}[.:]\d{2})\s*(.*)$", text)
    if not match:
        return None, text
    return match.group(1).replace(":", "."), match.group(2).strip()


def select_group_schedule(
    sheets: list[dict],
    group: str,
    target_date: date | None = None,
    *,
    term_start: date | None = None,
) -> list[dict] | None:
    """Return only cells under an exact group header; None means group absent."""
    requested = _normalize_group(group)
    day_number = target_date.weekday() if target_date else None
    week = _academic_week(target_date, term_start) if target_date and term_start else None
    if target_date and week is None:
        return None

    results = []
    found_group = False
    for sheet in sheets:
        rows = sheet.get("rows", [])
        for row_index, row in enumerate(rows):
            label_cols = [
                i for i, cell in enumerate(row)
                if _normalize_group(cell) == _normalize_group("Номер группы")
            ]
            if not label_cols:
                continue
            label_col = label_cols[0]
            headers = [(i, cell) for i, cell in enumerate(row[label_col + 1:], start=label_col + 1) if cell.strip()]
            for position, (start_col, group_header) in enumerate(headers):
                end_col = headers[position + 1][0] if position + 1 < len(headers) else len(row)
                subgroup_row = rows[row_index + 1] if row_index + 1 < len(rows) else []
                subgroup_cols = [
                    col for col in range(start_col, min(end_col, len(subgroup_row)))
                    if _normalize_group(subgroup_row[col]) == requested
                ]
                if _normalize_group(group_header) == requested:
                    found_group = True
                    if any("п/группы" in cell.casefold() for cell in subgroup_row):
                        subgroup_columns = [
                            (col, subgroup_row[col])
                            for col in range(start_col, min(end_col, len(subgroup_row)))
                            if subgroup_row[col].strip()
                        ]
                        selected_columns = subgroup_columns or [(col, "") for col in range(start_col, end_col)]
                    else:
                        selected_columns = [(col, "") for col in range(start_col, end_col)]
                elif subgroup_cols:
                    found_group = True
                    selected_columns = [(col, subgroup_row[col]) for col in subgroup_cols]
                else:
                    continue

                has_subgroup_row = any("п/группы" in cell.casefold() for cell in subgroup_row)
                first_data_row = row_index + 2 if has_subgroup_row else row_index + 1
                current_day: str | None = None
                for data_row in rows[first_data_row:]:
                    if not data_row:
                        continue
                    first_cell = data_row[0].strip().casefold()
                    if first_cell in _WEEKDAYS:
                        current_day = first_cell
                    if day_number is not None and current_day not in _WEEKDAYS:
                        continue
                    if day_number is not None and _WEEKDAYS[current_day] != day_number:
                        continue
                    source_time = data_row[1].strip() if len(data_row) > 1 else ""
                    for col, subgroup in selected_columns:
                        if col >= len(data_row) or not data_row[col].strip():
                            continue
                        raw_value = data_row[col].strip()
                        if week is not None and not _class_applies_to_week(raw_value, week):
                            continue
                        explicit_time, value = _cell_time(raw_value)
                        results.append({
                            "sheet": sheet.get("name", ""),
                            "weekday": current_day,
                            "time": explicit_time or source_time,
                            "source_time": source_time,
                            "subgroup": subgroup.strip(),
                            "classes": [value or raw_value],
                        })
    return results if found_group else None


def _academic_year_start(doc: dict) -> int | None:
    match = re.search(r"\b(20\d{2})[_/.-](?:\d{2}|\d{4})\b", f"{doc.get('title', '')} {doc.get('url', '')}")
    return int(match.group(1)) if match else None


def _academic_year_for(target_date: date) -> int:
    return target_date.year if target_date.month >= 9 else target_date.year - 1

def _extract_document(url: str, data: bytes) -> str:
    low = url.lower()
    if low.endswith(".pdf"):
        return _extract_pdf(data)
    if low.endswith(".xlsx"):
        return _extract_xlsx(data)
    return ""

def _read_cached_documents(docs: list[dict], cache_path: Path, now: float) -> list[dict] | None:
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        urls = [doc["url"] for doc in docs]
        cached_documents = cached["documents"]
        cached_by_url = {doc["url"]: doc for doc in cached_documents}
        if (cached.get("version") != 2 or now - cached["saved_at"] > SCHEDULE_CACHE_TTL_SECONDS
                or not set(urls).issubset(set(cached["urls"]))
                or not set(urls).issubset(cached_by_url)):
            return None
        return [cached_by_url[url] for url in urls]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


async def get_group_schedule(
    group: str,
    target_date: date | None = None,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> dict:
    group = group.strip()
    if not group or len(group) > 80:
        return {
            "status": "invalid_group",
            "group": group,
            "date": target_date.isoformat() if target_date else None,
            "message": "Укажите название или код учебной группы.",
        }

    reference_date = target_date or date.today()
    academic_year = _academic_year_for(reference_date)
    semester = 1 if reference_date.month >= 9 or reference_date.month <= 1 else 2
    docs = await _published_documents()
    docs = [doc for doc in docs if _academic_year_start(doc) == academic_year and not (
        re.search(r"(?:^|[._-])1\.?(?:sem|semestr)", doc["url"].casefold()) and semester == 2
        or re.search(r"(?:^|[._-])2\.?(?:sem|semestr)", doc["url"].casefold()) and semester == 1
    )]
    if not docs:
        return {
            "status": "schedule_not_published",
            "group": group,
            "date": target_date.isoformat() if target_date else None,
            "message": f"Не найдены опубликованные файлы расписания на {academic_year}/{academic_year + 1} учебный год.",
            "source_page": settings.schedule_page_url,
        }

    cached_documents = _read_cached_documents(docs, cache_path, time.time())
    if cached_documents is None:
        semaphore = asyncio.Semaphore(MAX_PARALLEL_SCHEDULE_DOWNLOADS)

        async def fetch_document(doc: dict) -> dict | None:
            try:
                async with semaphore:
                    data = await get_bytes(doc["url"])
                if doc["url"].lower().endswith(".xlsx"):
                    payload = _extract_xlsx_payload(data)
                else:
                    payload = {"text": _extract_document(doc["url"], data), "sheets": []}
                return {**doc, **payload}
            except Exception:
                return None

        fetched = await asyncio.gather(*(fetch_document(doc) for doc in docs))
        cached_documents = [item for item in fetched if item is not None]
        if len(cached_documents) == len(docs):
            temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary_path.write_text(json.dumps({
                    "version": 2,
                    "saved_at": time.time(),
                    "urls": [doc["url"] for doc in docs],
                    "documents": cached_documents,
                }, ensure_ascii=False), encoding="utf-8")
                temporary_path.replace(cache_path)
            except OSError:
                temporary_path.unlink(missing_ok=True)

    matches = []
    for doc in cached_documents:
        if not doc.get("sheets"):
            continue
        semester_start = _find_semester_start([doc])
        term_start = date(academic_year, 9, 1) if semester == 1 else date(academic_year + 1, 2, 1)
        if semester_start:
            term_start = semester_start
        schedule_rows = select_group_schedule(doc["sheets"], group, target_date, term_start=term_start)
        if schedule_rows is None:
            continue
        if target_date and semester_start is None:
            return {
                "status": "date_filter_unavailable",
                "group": group,
                "date": target_date.isoformat(),
                "message": "В опубликованных файлах нет даты начала учебной недели; не буду угадывать расписание на конкретный день.",
                "source_page": settings.schedule_page_url,
            }
        matches.append({
            "group": group,
            "date": target_date.isoformat() if target_date else None,
            "source_title": doc["title"],
            "source_url": doc["url"],
            "classes": schedule_rows,
        })

    failed_downloads = len(docs) - len(cached_documents)
    unsupported_documents = [doc for doc in cached_documents if not doc.get("sheets")]
    unverified_files = failed_downloads + len(unsupported_documents)
    if unverified_files:
        return {
            "status": "source_error" if failed_downloads else "incomplete_source_coverage",
            "group": group,
            "date": target_date.isoformat() if target_date else None,
            "sources": matches,
            "unverified_files": unverified_files,
            "message": (
                f"Не удалось полностью проверить расписание: {unverified_files} файл(а) "
                "недоступны или не поддерживают надёжный разбор групп и занятий. "
                "Найденные строки предварительные; отсутствие занятий не подтверждено."
            ),
            "source_page": settings.schedule_page_url,
        }

    if matches:
        if target_date and not any(match["classes"] for match in matches):
            return {
                "status": "no_classes_found_on_date",
                "group": group,
                "date": target_date.isoformat(),
                "sources": [{"title": m["source_title"], "url": m["source_url"]} for m in matches],
                "message": "В найденной версии расписания для этой группы нет занятий на указанную дату.",
            }
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
            "Группа не найдена в проверенных XLSX-файлах. Официальная страница "
            "ИФМиБ указывает ИАС «Электронный университет» как источник актуального "
            "расписания. Для закрытой части ИАС нужен отдельный официальный способ авторизации/API."
        ),
    }


def _find_semester_start(documents: list[dict]) -> date | None:
    found_dates = []
    for doc in documents:
        for sheet in doc.get("sheets", []):
            rows = sheet.get("rows", [])
            for row in rows:
                if not row or _normalize_group(row[0]) != _normalize_group("Дата"):
                    continue
                for value in row[1:]:
                    try:
                        found_dates.append(date.fromisoformat(value[:10]))
                    except (TypeError, ValueError):
                        continue
    return min(found_dates) if found_dates else None
