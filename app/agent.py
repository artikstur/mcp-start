from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


load_dotenv()


OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

if not OPENROUTER_API_KEY:
    raise RuntimeError("Не найден OPENROUTER_API_KEY в .env")


llm = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


# ---------------------------------------------------------
# Skills
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = BASE_DIR / "skills"


def load_skills() -> str:
    """
    Загружает все SKILL.md из папки skills/
    и объединяет их в одну инструкцию для LLM.
    """

    skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))

    if not skill_files:
        return "Дополнительные skills не найдены."

    parts = []

    for skill_file in skill_files:
        content = skill_file.read_text(encoding="utf-8")

        parts.append(
            f"""
===== SKILL: {skill_file.parent.name} =====

{content}

===== END SKILL =====
"""
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

5. Не сообщай пользователю внутреннюю механику
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

async def run_openrouter_mcp(user_query: str) -> str:

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "app.mcp_server",
        ],
    )

    async with stdio_client(server_params) as (read, write):

        async with ClientSession(read, write) as session:

            # Подключаемся к MCP
            await session.initialize()

            # Получаем список MCP tools
            tools_result = await session.list_tools()

            tools = [
                mcp_tool_to_openai(tool)
                for tool in tools_result.tools
            ]

            messages = [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_query,
                },
            ]

            # LLM может сделать несколько последовательных
            # вызовов инструментов
            for _ in range(5):

                response = await llm.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                )

                message = response.choices[0].message

                # LLM получила данные и может сразу ответить
                if not message.tool_calls:
                    return message.content or ""

                # Сохраняем сообщение LLM
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.function.name,
                                    "arguments": call.function.arguments,
                                },
                            }
                            for call in message.tool_calls
                        ],
                    }
                )

                # Выполняем выбранные MCP tools
                for call in message.tool_calls:

                    tool_name = call.function.name

                    try:
                        arguments = json.loads(
                            call.function.arguments
                        )
                    except json.JSONDecodeError:
                        arguments = {}

                    result = await session.call_tool(
                        tool_name,
                        arguments,
                    )

                    result_parts = []

                    for content in result.content:
                        if hasattr(content, "text"):
                            result_parts.append(content.text)
                        else:
                            result_parts.append(str(content))

                    tool_result = "\n".join(result_parts)

                    # Возвращаем результат MCP обратно LLM
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": tool_result,
                        }
                    )

            return "Не удалось получить ответ."


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

async def main():

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = input("Вопрос: ")

    answer = await run_openrouter_mcp(query)

    print()
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
