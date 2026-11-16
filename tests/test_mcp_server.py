import json
import unittest
from unittest.mock import AsyncMock, patch

from app.mcp_server import get_group_schedule, list_institute_pages, search_docs


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_schedule_date_returns_error_without_fetching_schedule(self):
        with patch("app.mcp_server._get_schedule", new=AsyncMock(return_value={"status": "unexpected"})) as get_schedule:
            result = await get_group_schedule("БОТ-999999", "2030-02-31")

        get_schedule.assert_not_awaited()
        self.assertEqual(json.loads(result)["status"], "invalid_date")

    async def test_page_listing_returns_metadata_without_full_page_text(self):
        pages = [
            {
                "title": "Структура института",
                "url": "https://kpfu.ru/biology-medicine/struktura-instituta",
                "text": "Большой текст страницы, который нельзя отправлять списком.",
            }
        ]
        with patch("app.mcp_server.crawl_pages", new=AsyncMock(return_value=pages)):
            result = await list_institute_pages()

        self.assertEqual(
            json.loads(result),
            [{"title": pages[0]["title"], "url": pages[0]["url"]}],
        )

    async def test_search_docs_returns_source_ids_and_marked_document_data(self):
        result_data = {
            "found": True,
            "sources": [{"id": 1, "url": "https://example.test/doc", "text": "<doc id=\"1\">Фрагмент</doc>"}],
        }
        with patch("app.mcp_server.search_documents", new=AsyncMock(return_value=result_data)):
            result = await search_docs("вопрос")

        decoded = json.loads(result)
        self.assertTrue(decoded["found"])
        self.assertEqual(decoded["sources"][0]["id"], 1)
        self.assertIn("<doc id=\"1\">", decoded["sources"][0]["text"])


if __name__ == "__main__":
    unittest.main()
