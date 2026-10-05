from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

# $ per 1M tokens (gpt-4o-mini on OpenRouter)
PRICE_IN  = 0.15
PRICE_OUT = 0.60

LOG_PATH = Path(__file__).resolve().parent.parent / "log.jsonl"

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


# ---------------------------------------------------------
# Skills
# ---------------------------------------------------------

BASE_DIR   = Path(__file__).resolve().parent.parent
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

SYSTEM_PROMPT = f"""
Ты ассистент Института фундаментальной медицины
и биологии КФУ.

У тебя есть MCP-инструменты для получения актуальной
информации из открытых источников КФУ.

Ниже находятся skills — инструкции о том,
как работать с разными типами запросов.

{SKILLS}

Общие правила:

1. Если вопрос относится к одному из skills,
   следуй соответствующей инструкции.

2. Если для ответа нужна актуальная информация,
   используй MCP-инструмент.

3. Не придумывай информацию.

4. Если MCP-инструмент вернул данные,
   основывай ответ на этих данных.

5. Если MCP-инструмент вернул статус
   'not_found_in_public_documents' или пустой результат —
   отвечай точно так:
   «Не знаю, эта информация не найдена в официальных
   открытых источниках ИФМиБ КФУ.»
   Никогда не придумывай расписание, даты, имена и факты.

6. Не сообщай пользователю внутреннюю механику
   MCP, если он специально об этом не спрашивает.
"""


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
) -> tuple[str, dict]:
    """
    Returns (answer_text, usage_dict).
    usage_dict keys: input_tokens, output_tokens, cost_usd.
    Also appends a line to log.jsonl.
    """

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
    )

    total_input  = 0
    total_output = 0

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            tools = [mcp_tool_to_openai(t) for t in tools_result.tools]

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_query},
            ]

            for _ in range(5):
                response = await llm.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=temperature,
                )

                if response.usage:
                    total_input  += response.usage.prompt_tokens
                    total_output += response.usage.completion_tokens

                message = response.choices[0].message

                if not message.tool_calls:
                    answer = message.content or ""
                    cost   = _calc_cost(total_input, total_output)
                    usage  = {
                        "input_tokens":  total_input,
                        "output_tokens": total_output,
                        "cost_usd":      cost,
                    }
                    _write_log({
                        "ts":    datetime.now().isoformat(timespec="seconds"),
                        "model": MODEL,
                        "query": user_query,
                        **usage,
                    })
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
                        arguments = json.loads(call.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}

                    result = await session.call_tool(call.function.name, arguments)

                    result_parts = []
                    for content in result.content:
                        if hasattr(content, "text"):
                            result_parts.append(content.text)
                        else:
                            result_parts.append(str(content))

                    messages.append({
                        "role":         "tool",
                        "tool_call_id": call.id,
                        "content":      "\n".join(result_parts),
                    })

    answer = "Не удалось получить ответ."
    cost   = _calc_cost(total_input, total_output)
    usage  = {"input_tokens": total_input, "output_tokens": total_output, "cost_usd": cost}
    _write_log({"ts": datetime.now().isoformat(timespec="seconds"), "model": MODEL, "query": user_query, **usage})
    return answer, usage


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

async def main():
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = input("Вопрос: ")

    answer, usage = await run_openrouter_mcp(query)

    print()
    print(answer)
    print()
    print(f"[tokens: {usage['input_tokens']} in / {usage['output_tokens']} out | cost: ${usage['cost_usd']:.6f}]")


if __name__ == "__main__":
    asyncio.run(main())
