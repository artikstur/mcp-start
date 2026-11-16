from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    source_tool: str | None
    found: bool
    sources: list[int]


def parse_answer(content: str) -> Answer:
    return Answer.model_validate_json(content)


def is_group_clarification(text: str) -> bool:
    return bool(re.fullmatch(
        r"\s*(?:пожалуйста,?\s*)?(?:укажите|назовите)\s+(?:вашу\s+)?группу"
        r"(?:,?\s+чтобы\s+я\s+мог\s+найти\s+расписание(?:\s+на\s+завтра)?)?[.!?]?\s*",
        text, re.IGNORECASE,
    ))


def provider_error_message(error: Exception) -> str:
    if getattr(error, "status_code", None) == 402:
        return "OpenRouter отклонил запрос: проверьте баланс и лимит API-ключа или уменьшите лимит ответа."
    return "Сервис ответа временно недоступен. Проверьте соединение и повторите запрос."


def director_claim_supported(query: str, answer: str, excerpts: list[str]) -> bool:
    if not re.search(r"\bдиректор\w*", query, re.IGNORECASE):
        return True
    names = re.findall(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\b", answer)
    if not names:
        return False
    for excerpt in excerpts:
        for role in re.finditer(r"\bдиректор\w*", excerpt, re.IGNORECASE):
            context = excerpt[max(0, role.start() - 100):role.end() + 180]
            if re.search(r"(?:бывш\w*|экс[- ]|заместител\w*)\s+(?:директор\w*)", context, re.IGNORECASE):
                continue
            if all(name.casefold() in context.casefold() for name in names):
                return True
    return False


def format_schedule_answer(payload: dict) -> str:
    """Render verified schedule rows without asking the model to reconstruct them."""
    if payload.get("status") != "ok":
        message = payload.get("message")
        return str(message) if message else "Не удалось получить подтверждённое расписание."

    lines = [f"Расписание группы {payload.get('group', '')}:"]
    current_day = None
    for source in payload.get("sources", []):
        for entry in source.get("classes", []):
            weekday = entry.get("weekday") or "День не указан"
            if weekday != current_day:
                lines.extend(("", f"**{weekday.capitalize()}:**"))
                current_day = weekday

            time = str(entry.get("time") or "Время не указано")
            source_time = str(entry.get("source_time") or "")
            normalize_time = lambda value: value.replace(":", ".").replace(" ", "")
            if source_time and normalize_time(source_time) != normalize_time(time):
                time += f" (в строке таблицы {source_time}; время расходится)"

            subgroup = str(entry.get("subgroup") or "").strip()
            subgroup_label = f" (подгруппа {subgroup})" if subgroup else ""
            classes = ", ".join(str(value) for value in entry.get("classes", []))
            lines.append(f"- {time} — {classes}{subgroup_label}")

    source_page = payload.get("source_page")
    if source_page:
        lines.extend(("", f"Источник: [официальное расписание ИФМиБ]({source_page})"))
    return "\n".join(lines)


def provider_cost_usd(usage) -> float | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        value = usage.get("cost")
    else:
        value = getattr(usage, "cost", None)
        if value is None:
            value = (getattr(usage, "model_extra", None) or {}).get("cost")
        if value is None and hasattr(usage, "model_dump"):
            value = usage.model_dump().get("cost")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def enforce_tool_evidence(
    answer: Answer,
    evidence_tools: set[str],
    available_source_ids: set[int] | None = None,
) -> Answer:
    """Require a successful tool call and, for document search, a selected source."""
    valid_sources = [source_id for source_id in answer.sources if source_id in (available_source_ids or set())]
    missing_tool = answer.found and (
        not answer.source_tool or answer.source_tool not in evidence_tools
    )
    requires_source = answer.source_tool in {"search_docs", "search_institute_info"}
    missing_document_source = answer.found and requires_source and not valid_sources
    if missing_tool or missing_document_source:
        return Answer(
            answer="Не могу подтвердить ответ по доступным официальным источникам.",
            source_tool=None,
            found=False,
            sources=[],
        )
    if answer.found and answer.source_tool == "search_docs":
        return answer.model_copy(update={"sources": valid_sources})
    return answer


def tool_result_has_evidence(tool_name: str, content: str) -> bool:
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return False

    if tool_name == "search_docs":
        return bool(payload.get("found") and payload.get("sources")) if isinstance(payload, dict) else False
    if tool_name == "get_group_schedule":
        return bool(payload.get("status") == "ok" and payload.get("sources")) if isinstance(payload, dict) else False
    if tool_name == "search_institute_info":
        return bool(payload) if isinstance(payload, list) else False
    return False


def tool_result_source_urls(tool_name: str, content: str) -> set[str]:
    """Return source URLs supplied by successful retrieval tools this turn."""
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return set()

    if tool_name == "search_docs" and isinstance(payload, dict):
        records = payload.get("sources", [])
    elif tool_name == "search_institute_info" and isinstance(payload, list):
        records = payload
    else:
        return set()
    return {
        record["url"] for record in records
        if isinstance(record, dict) and isinstance(record.get("url"), str)
    }


def cited_source_ids(answer: Answer, available_sources: dict[int, str]) -> list[int]:
    return [source_id for source_id in answer.sources if source_id in available_sources]


def append_source_citations(text: str, citations: list[tuple[int, str]]) -> str:
    existing_urls = {
        match.rstrip(".,;:!?")
        for match in re.findall(r"https?://[^\s<>()\[\]]+", text)
    }
    missing = [(source_id, url) for source_id, url in citations if url not in existing_urls]
    if not missing:
        return text
    return text + "\n\nИсточники:\n" + "\n".join(
        f"[{source_id}]({url})" for source_id, url in missing
    )


def remap_source_ids(
    result: dict,
    available_sources: dict[int, str],
    next_source_id: int,
) -> tuple[dict, int]:
    """Give each search result a unique turn-wide ID, even across repeated searches."""
    mapped = dict(result)
    sources = []
    for source in result.get("sources", []):
        if not isinstance(source, dict) or not isinstance(source.get("url"), str):
            continue
        updated = {**source, "id": next_source_id}
        available_sources[next_source_id] = source["url"]
        sources.append(updated)
        next_source_id += 1
    mapped["sources"] = sources
    return mapped, next_source_id


def remap_source_records(
    records: list[dict],
    available_sources: dict[int, str],
    next_source_id: int,
) -> tuple[list[dict], int]:
    """Assign turn-wide IDs to source records returned as a list."""
    mapped = []
    for source in records:
        if not isinstance(source, dict) or not isinstance(source.get("url"), str):
            continue
        mapped.append({**source, "id": next_source_id})
        available_sources[next_source_id] = source["url"]
        next_source_id += 1
    return mapped, next_source_id


def sanitize_markdown_links(text: str, allowed_urls: set[str]) -> str:
    """Keep only markdown links whose URLs came from this turn's retrieval."""
    return re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: match.group(0) if match.group(2) in allowed_urls else match.group(1),
        text,
    )
