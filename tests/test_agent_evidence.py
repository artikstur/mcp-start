import importlib
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.storage import SQLiteStore


@asynccontextmanager
async def fake_stdio(_):
    yield None, None


class FakeSession:
    result = None
    calls = 0

    def __init__(self, *args): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def initialize(self): pass
    async def list_tools(self): return SimpleNamespace(tools=[])
    async def call_tool(self, *args):
        type(self).calls += 1
        return type(self).result


def model_response(content=None, calls=None):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(
        content=content, tool_calls=calls,
    ))])


class AgentEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test", "MCP_START_DB": str(root / "initial.sqlite")}):
            self.agent = importlib.import_module("app.agent")
        self.patches = [
            patch.object(self.agent, "STORE", SQLiteStore(root / "test.sqlite")),
            patch.object(self.agent, "LOG_PATH", root / "log.jsonl"),
            patch.object(self.agent, "STEPS_PATH", root / "steps.jsonl"),
            patch.object(self.agent, "AUDIT_PATH", root / "audit.jsonl"),
            patch.object(self.agent, "stdio_client", fake_stdio),
            patch.object(self.agent, "ClientSession", FakeSession),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        FakeSession.calls = 0

    async def test_impossible_date_is_rejected_before_model_or_tool(self):
        with patch.object(self.agent.llm.chat.completions, "create", new=AsyncMock()) as model:
            answer, _ = await self.agent.run_openrouter_mcp("Расписание на 31 февраля 2027 года", user_id="u")
        self.assertIn("несуществующая дата", answer)
        model.assert_not_awaited()
        self.assertEqual(FakeSession.calls, 0)

    async def test_no_tool_does_not_claim_sources_were_searched(self):
        content = json.dumps({"answer": "Дата не найдена в источниках.", "source_tool": None,
                              "found": False, "sources": []}, ensure_ascii=False)
        with patch.object(self.agent.llm.chat.completions, "create", new=AsyncMock(return_value=model_response(content))):
            answer, _ = await self.agent.run_openrouter_mcp("Когда пересдача?", user_id="u")
        self.assertIn("Не могу подтвердить", answer)
        self.assertEqual(FakeSession.calls, 0)

    async def test_unverified_department_is_not_presented_as_fact(self):
        FakeSession.result = SimpleNamespace(isError=False, content=[SimpleNamespace(text=json.dumps([
            {"url": "https://example.test", "title": "Институт", "snippet": "Кафедра биоэкологии."}
        ], ensure_ascii=False))])
        call = SimpleNamespace(id="1", function=SimpleNamespace(name="search_institute_info", arguments='{"query":"кафедры"}'))
        content = json.dumps({"answer": "Кафедра биоэкологии, кафедра физиологии человека.",
                              "source_tool": "search_institute_info", "found": True, "sources": [1]}, ensure_ascii=False)
        with patch.object(self.agent.llm.chat.completions, "create", new=AsyncMock(side_effect=[model_response(calls=[call]), model_response(content)])):
            answer, _ = await self.agent.run_openrouter_mcp("Какие кафедры есть?", user_id="u")
        self.assertIn("Кафедра биоэкологии", answer)
        self.assertNotIn("кафедра физиологии", answer.casefold())

    async def test_missing_tool_is_recovered_from_selected_source(self):
        FakeSession.result = SimpleNamespace(isError=False, content=[SimpleNamespace(text=json.dumps([
            {"url": "https://example.test", "title": "Институт", "snippet": "Высшая школа медицины."}
        ], ensure_ascii=False))])
        call = SimpleNamespace(id="1", function=SimpleNamespace(name="search_institute_info", arguments='{"query":"структура"}'))
        content = json.dumps({"answer": "Высшая школа медицины.", "source_tool": None,
                              "found": True, "sources": [1]}, ensure_ascii=False)
        with patch.object(self.agent.llm.chat.completions, "create", new=AsyncMock(side_effect=[model_response(calls=[call]), model_response(content)])) as model:
            answer, _ = await self.agent.run_openrouter_mcp("Какие подразделения есть?", user_id="u")
        self.assertIn("Высшая школа медицины", answer)
        self.assertIn("https://example.test", answer)
        self.assertEqual(model.await_count, 2)

    async def test_failed_tool_is_not_treated_as_completed_search(self):
        FakeSession.result = SimpleNamespace(isError=True, content=[SimpleNamespace(text='[]')])
        call = SimpleNamespace(id="1", function=SimpleNamespace(name="search_institute_info", arguments='{"query":"пересдача"}'))
        content = json.dumps({"answer": "Данные не найдены в источниках.", "source_tool": None,
                              "found": False, "sources": []}, ensure_ascii=False)
        with patch.object(self.agent.llm.chat.completions, "create", new=AsyncMock(side_effect=[model_response(calls=[call]), model_response(content)])):
            answer, _ = await self.agent.run_openrouter_mcp("Когда пересдача?", user_id="u")
        self.assertIn("Не могу подтвердить", answer)
        self.assertEqual(FakeSession.calls, 1)

    async def test_error_result_cannot_supply_verified_source(self):
        FakeSession.result = SimpleNamespace(isError=True, content=[SimpleNamespace(text=json.dumps([
            {"url": "https://example.test", "title": "Институт", "snippet": "Высшая школа медицины."}
        ], ensure_ascii=False))])
        call = SimpleNamespace(id="1", function=SimpleNamespace(name="search_institute_info", arguments='{"query":"структура"}'))
        content = json.dumps({"answer": "Высшая школа медицины.", "source_tool": None,
                              "found": True, "sources": [1]}, ensure_ascii=False)
        responses = [model_response(calls=[call]), model_response(content), model_response(content)]
        with patch.object(self.agent.llm.chat.completions, "create", new=AsyncMock(side_effect=responses)):
            answer, _ = await self.agent.run_openrouter_mcp("Какие подразделения есть?", user_id="u")
        self.assertIn("Не могу подтвердить", answer)
        self.assertNotIn("https://example.test", answer)


if __name__ == "__main__":
    unittest.main()
