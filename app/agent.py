from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import ValidationError

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .context import HISTORY_BUDGET, compact_history, estimate_tokens, format_memory_context
from .answer import (
    Answer,
    cited_source_ids,
    enforce_tool_evidence,
    parse_answer,
    provider_cost_usd,
    remap_source_ids,
    sanitize_markdown_links,
    tool_result_has_evidence,
)
from .security import audit_tool_call, cli_identity, is_tool_allowed
from .storage import SQLiteStore


load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
MAX_COMPLETION_TOKENS = int(os.getenv("OPENROUTER_MAX_TOKENS", "2048"))

# Fallback rates for usage responses that omit OpenRouter's per-generation cost.
PRICE_IN = float(os.getenv("OPENROUTER_INPUT_PRICE_PER_MILLION", "0.15"))
PRICE_OUT = float(os.getenv("OPENROUTER_OUTPUT_PRICE_PER_MILLION", "0.60"))

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_PATH = BASE_DIR / "log.jsonl"
STEPS_PATH = BASE_DIR / "steps.jsonl"
AUDIT_PATH = BASE_DIR / "audit.jsonl"
STORE = SQLiteStore(os.getenv("MCP_START_DB", str(BASE_DIR / "data" / "mcp_start.sqlite3")))
SUMMARY_PROMPT = (BASE_DIR / "prompts" / "summary_v1.md").read_text(encoding="utf-8")
MEMORY_PROMPT = (BASE_DIR / "prompts" / "system_v4.md").read_text(encoding="utf-8")
BASE_SYSTEM_PROMPT = (BASE_DIR / "prompts" / "system_v1.md").read_text(encoding="utf-8")
FEWSHOT_EXAMPLES = json.loads((BASE_DIR / "prompts" / "fewshot_v1.json").read_text(encoding="utf-8"))

if not OPENROUTER_API_KEY:
    raise RuntimeError("Не найден OPENROUTER_API_KEY в .env")

llm = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


def _calc_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens * PRICE_IN + completion_tokens * PRICE_OUT) / 1_000_000


def _write_log(entry: dict) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _write_step(entry: dict) -> None:
    with STEPS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------
# Skills
# ---------------------------------------------------------

SKILLS_DIR = BASE_DIR / "skills"


def load_skills() -> str:
    skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if not skill_files:
        return "Дополнительные skills не найдены."
    parts = []
    for skill_file in skill_files:
        content = skill_file.read_text(encoding="utf-8")
        parts.append(
            f"\n===== SKILL: {skill_file.parent.name} =====\n\n{content}\n\n===== END SKILL =====\n"
        )
    return "\n".join(parts)


SKILLS = load_skills()

SYSTEM_PROMPT = "\n\n".join((BASE_SYSTEM_PROMPT, SKILLS, MEMORY_PROMPT))


def _fewshot_messages() -> list[dict[str, str]]:
    messages = []
    for example in FEWSHOT_EXAMPLES:
        messages.extend((
            {"role": "user", "content": example["user"]},
            {"role": "assistant", "content": json.dumps(example["assistant"], ensure_ascii=False)},
        ))
    return messages


# ---------------------------------------------------------
# MCP → OpenAI tools
# ---------------------------------------------------------

def mcp_tool_to_openai(tool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }


# ---------------------------------------------------------
# Agent
# ---------------------------------------------------------

async def run_openrouter_mcp(
    user_query: str,
    temperature: float = 0.0,
    session_id: str = "default",
    user_id: str = "local-user",
    role: str = "student",
) -> tuple[str, dict]:
    """
    Returns (answer_text, usage_dict).
    usage_dict keys: input_tokens, output_tokens, cost_usd.
    Also appends a line to log.jsonl.
    """

    normalized = user_query.strip().casefold().strip(".!? \n")
    STORE.cleanup_expired()
    if not STORE.consume_request(user_id, limit=20, window_seconds=60):
        audit_tool_call(AUDIT_PATH, user_id, role, "rate_limit", {}, "deny")
        return "Слишком много запросов. Попробуйте через минуту.", {
            "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0
        }
    affirmative = normalized in {"да", "да, конечно", "да, запомни", "подтверждаю", "согласен", "согласна"}
    negative = normalized in {"нет", "не запоминай", "отмена", "не согласен", "не согласна"}
    forget_request = any(phrase in normalized for phrase in ("забудь", "удали мои данные", "удали память"))

    total_input = 0
    total_output = 0
    provider_cost_total = 0.0
    provider_cost_complete = True
    token_usage_complete = True
    embedding_cost_total = 0.0
    embedding_tokens_total = 0
    embedding_cost_estimated = False

    def usage_snapshot() -> dict:
        if not token_usage_complete:
            cost = None
        else:
            model_cost = provider_cost_total if provider_cost_complete else _calc_cost(total_input, total_output)
            cost = model_cost + embedding_cost_total
        return {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cost_usd": cost,
            "embedding_tokens": embedding_tokens_total,
            "cost_is_estimated": cost is not None and (
                not provider_cost_complete or embedding_cost_estimated
            ),
        }

    def finish_direct(answer: str, persist: bool = True) -> tuple[str, dict]:
        if persist:
            STORE.append_message(user_id, session_id, "user", user_query)
            STORE.append_message(user_id, session_id, "assistant", answer)
        usage = usage_snapshot()
        _write_step({"ts": datetime.now().isoformat(timespec="seconds"),
                     "history_tokens": estimate_tokens(STORE.load_history(user_id, session_id)) if persist else 0})
        _write_log({"ts": datetime.now().isoformat(timespec="seconds"), "model": MODEL, **usage})
        return answer, usage

    if STORE.has_pending_forget(user_id):
        if affirmative:
            STORE.forget_user(user_id)
            return finish_direct("Удалил историю диалогов и сохранённые факты.", persist=False)
        if negative:
            STORE.cancel_forget(user_id)
            return finish_direct("Хорошо, данные оставил без изменений.")
    if forget_request:
        STORE.request_forget(user_id)
        return finish_direct("Подтвердите удаление всей истории и сохранённых фактов: ответьте «да» или «нет».", persist=False)

    pending = STORE.pending_fact(user_id, session_id)
    if pending and affirmative:
        STORE.confirm_pending_fact(user_id, session_id, True)
        return finish_direct(f"Запомнил: {pending['key']} = {pending['value']}.")
    if pending and negative:
        STORE.confirm_pending_fact(user_id, session_id, False)
        return finish_direct("Хорошо, не сохранял этот факт.")

    history = STORE.load_history(user_id, session_id)
    history_before = estimate_tokens(history)

    async def summarize(old_messages):
        nonlocal total_input, total_output, provider_cost_total, provider_cost_complete, token_usage_complete
        try:
            response = await llm.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": SUMMARY_PROMPT},
                          {"role": "user", "content": json.dumps(old_messages, ensure_ascii=False)}],
                temperature=0,
            )
        except Exception:
            token_usage_complete = False
            provider_cost_complete = False
            return ""
        if response.usage:
            total_input += response.usage.prompt_tokens
            total_output += response.usage.completion_tokens
            reported_cost = provider_cost_usd(response.usage)
            if reported_cost is None:
                provider_cost_complete = False
            else:
                provider_cost_total += reported_cost
        else:
            token_usage_complete = False
            provider_cost_complete = False
        return response.choices[0].message.content or ""

    bounded_history = await compact_history(history, summarize, HISTORY_BUDGET)
    history_after = estimate_tokens(bounded_history)
    if history_before > HISTORY_BUDGET and history_after < history_before:
        STORE.replace_history(user_id, session_id, bounded_history)
    facts = STORE.get_memory(user_id)
    memory_context = format_memory_context(facts)

    server_env = {}
    if os.getenv("MCP_START_INDEX_PATH"):
        server_env["MCP_START_INDEX_PATH"] = os.environ["MCP_START_INDEX_PATH"]
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        env=server_env or None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            tools = [mcp_tool_to_openai(t) for t in tools_result.tools if is_tool_allowed(role, t.name)]
            if is_tool_allowed(role, "propose_student_fact"):
                tools.append({"type": "function", "function": {
                "name": "propose_student_fact",
                "description": "Предложить сохранить только явно названные пользователем группу, курс или предмет после запроса запомнить.",
                "parameters": {"type": "object", "properties": {
                    "key": {"type": "string", "enum": ["group", "course", "subject"]},
                    "value": {"type": "string"}}, "required": ["key", "value"], "additionalProperties": False}}})

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                *_fewshot_messages(),
                *bounded_history,
                {"role": "user", "content": (
                    f"{memory_context}\n\nВопрос пользователя:\n{user_query}"
                    if memory_context else user_query
                )},
            ]

            source_urls: dict[int, str] = {}
            next_source_id = 1
            format_retry_used = False
            evidence_retry_used = False
            evidence_tools: set[str] = set()
            for _ in range(5):
                try:
                    response = await llm.chat.completions.create(
                        model=MODEL,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        max_tokens=MAX_COMPLETION_TOKENS,
                        temperature=temperature,
                    )
                except Exception:
                    token_usage_complete = False
                    provider_cost_complete = False
                    answer = "Сервис ответа временно недоступен. Проверьте соединение и повторите запрос."
                    usage = usage_snapshot()
                    _write_log({"ts": datetime.now().isoformat(timespec="seconds"), "model": MODEL,
                                "query_chars": len(user_query), **usage})
                    return answer, usage

                if response.usage:
                    total_input  += response.usage.prompt_tokens
                    total_output += response.usage.completion_tokens
                    reported_cost = provider_cost_usd(response.usage)
                    if reported_cost is None:
                        provider_cost_complete = False
                    else:
                        provider_cost_total += reported_cost
                else:
                    token_usage_complete = False
                    provider_cost_complete = False

                message = response.choices[0].message

                if not message.tool_calls:
                    raw_answer = message.content or ""
                    try:
                        parsed_answer = parse_answer(raw_answer)
                    except ValidationError:
                        if not format_retry_used:
                            format_retry_used = True
                            messages.extend((
                                {"role": "assistant", "content": raw_answer},
                                {"role": "user", "content": (
                                    "Исправь предыдущий ответ и верни только JSON-объект "
                                    'строго по схеме: {"answer":string,"source_tool":string|null,'
                                    '"found":boolean,"sources":array<number>}. '
                                    "Без Markdown, пояснений и дополнительных ключей. Не вызывай инструменты."
                                )},
                            ))
                            continue
                        parsed_answer = Answer(
                            answer="Не удалось сформировать ответ в допустимом формате. Повторите запрос.",
                            source_tool=None,
                            found=False,
                            sources=[],
                        )

                    has_tool_evidence = (
                        parsed_answer.source_tool in evidence_tools
                        and (
                            parsed_answer.source_tool != "search_docs"
                            or any(source_id in source_urls for source_id in parsed_answer.sources)
                        )
                    )
                    if parsed_answer.found and not has_tool_evidence:
                        if not evidence_retry_used:
                            evidence_retry_used = True
                            messages.extend((
                                {"role": "assistant", "content": raw_answer},
                                {"role": "user", "content": (
                                    "Фактический ответ должен опираться на MCP-инструмент, вызванный в этом ходе. "
                                    "Вызови подходящий доступный инструмент и отвечай только по его результату. "
                                    "Для search_docs укажи только ID конкретных фрагментов из результатов этого хода. "
                                    "Если подтверждения нет, верни found=false, source_tool=null и sources=[]."
                                )},
                            ))
                            continue
                        parsed_answer = enforce_tool_evidence(
                            parsed_answer, evidence_tools, set(source_urls)
                        )

                    answer = parsed_answer.answer
                    if parsed_answer.source_tool == "search_docs":
                        answer = sanitize_markdown_links(answer, set(source_urls.values()))
                    citations = [
                        (source_id, source_urls[source_id])
                        for source_id in cited_source_ids(parsed_answer, source_urls)
                    ]
                    citations_to_append = [
                        (source_id, url) for source_id, url in citations if url not in answer
                    ]
                    if citations_to_append:
                        answer += "\n\nИсточники:\n" + "\n".join(
                            f"[{source_id}]({url})" for source_id, url in citations_to_append
                        )
                    usage = usage_snapshot()
                    _write_log({
                        "ts":    datetime.now().isoformat(timespec="seconds"),
                        "model": MODEL,
                        "query_chars": len(user_query),
                        **usage,
                    })
                    STORE.append_message(user_id, session_id, "user", user_query)
                    STORE.append_message(user_id, session_id, "assistant", answer)
                    _write_step({"ts": datetime.now().isoformat(timespec="seconds"),
                                 "history_tokens": history_after,
                                 "history_tokens_before": history_before, "history_tokens_after": history_after})
                    return answer, usage

                messages.append({
                    "role":       "assistant",
                    "content":    message.content,
                    "tool_calls": [
                        {
                            "id":   call.id,
                            "type": "function",
                            "function": {
                                "name":      call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in message.tool_calls
                    ],
                })

                for call in message.tool_calls:
                    try:
                        parsed_arguments = json.loads(call.function.arguments)
                        arguments = parsed_arguments if isinstance(parsed_arguments, dict) else {}
                    except json.JSONDecodeError:
                        arguments = {}

                    allowed = is_tool_allowed(role, call.function.name)
                    audit_tool_call(AUDIT_PATH, user_id, role, call.function.name, arguments,
                                    "allow" if allowed else "deny")
                    if not allowed:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": "Доступ к этому инструменту запрещён для текущей роли.",
                        })
                        continue

                    if call.function.name == "propose_student_fact":
                        try:
                            STORE.propose_fact(user_id, session_id, arguments.get("key", ""), arguments.get("value", ""))
                        except ValueError:
                            return finish_direct("Можно запомнить только группу, курс или предмет.")
                        return finish_direct(f"Запомнить: {arguments['key']} = {arguments['value']}? Ответьте «да» или «нет».")

                    try:
                        result = await session.call_tool(call.function.name, arguments)
                        result_parts = []
                        for content in result.content:
                            if hasattr(content, "text"):
                                result_parts.append(content.text)
                            else:
                                result_parts.append(str(content))
                        tool_content = "\n".join(result_parts)
                    except Exception:
                        tool_content = json.dumps(
                            {"error": "Инструмент временно недоступен."}, ensure_ascii=False
                        )
                    if call.function.name == "search_docs":
                        try:
                            document_result = json.loads(tool_content)
                            document_result, next_source_id = remap_source_ids(
                                document_result, source_urls, next_source_id
                            )
                            tool_content = json.dumps(document_result, ensure_ascii=False)
                        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                            pass
                    if tool_result_has_evidence(call.function.name, tool_content):
                        evidence_tools.add(call.function.name)
                    if call.function.name == "search_docs":
                        try:
                            document_result = json.loads(tool_content)
                            embedding_usage = document_result.get("usage", {})
                            embedding_tokens_total += int(embedding_usage.get("embedding_tokens", 0))
                            embedding_cost_total += float(embedding_usage.get("embedding_cost_usd", 0.0))
                            embedding_cost_estimated |= bool(embedding_usage.get("cost_is_estimated", False))
                        except (KeyError, TypeError, ValueError):
                            pass

                    messages.append({
                        "role":         "tool",
                        "tool_call_id": call.id,
                        "content":      tool_content,
                    })

    answer = "Не удалось получить ответ."
    usage = usage_snapshot()
    _write_log({"ts": datetime.now().isoformat(timespec="seconds"), "model": MODEL,
                "query_chars": len(user_query), **usage})
    return answer, usage


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", default="default")
    parser.add_argument("query", nargs="*")
    args = parser.parse_args()
    query = " ".join(args.query) or input("Вопрос: ")

    user_id, role = cli_identity()
    answer, usage = await run_openrouter_mcp(query, session_id=args.session_id,
                                             user_id=user_id, role=role)

    print()
    print(answer)
    print()
    cost_label = "estimated cost" if usage.get("cost_is_estimated") else "cost"
    cost_value = "unknown" if usage.get("cost_usd") is None else f"${usage['cost_usd']:.6f}"
    print(f"[tokens: {usage['input_tokens']} in / {usage['output_tokens']} out | "
          f"embedding tokens: {usage.get('embedding_tokens', 0)} | "
          f"{cost_label}: {cost_value}]")


if __name__ == "__main__":
    asyncio.run(main())
