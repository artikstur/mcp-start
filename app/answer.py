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
    missing_document_source = answer.found and answer.source_tool == "search_docs" and not valid_sources
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


def cited_source_ids(answer: Answer, available_sources: dict[int, str]) -> list[int]:
    return [source_id for source_id in answer.sources if source_id in available_sources]


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


def sanitize_markdown_links(text: str, allowed_urls: set[str]) -> str:
    """Keep only markdown links whose URLs came from this turn's retrieval."""
    return re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: match.group(0) if match.group(2) in allowed_urls else match.group(1),
        text,
    )
