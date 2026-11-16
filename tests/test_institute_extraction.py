import unittest

from bs4 import BeautifulSoup

from unittest.mock import AsyncMock, patch

from app.sources.institute import _expand_query, _extract_main_text, search_institute_info


class InstituteExtractionTests(unittest.TestCase):
    def test_director_search_fetches_official_director_page(self):
        import asyncio
        director = {"title": "Директор Института фундаментальной медицины и биологии",
                    "url": "https://kpfu.ru/biology-medicine/struktura-instituta/direktor-instituta-fundamentalnoj-mediciny-i",
                    "text": "Киясов Андрей Павлович, профессор. Директор института.", "links": []}
        async def run():
            with patch("app.sources.institute.crawl_pages", new=AsyncMock(return_value=[])), \
                 patch("app.sources.institute._extract_page", new=AsyncMock(return_value=director)) as fetch:
                results = await search_institute_info("Кто директор ИФМиБ?")
                return results, fetch
        results, fetch = asyncio.run(run())
        self.assertTrue(results)
        self.assertEqual(results[0]["url"], director["url"])
        fetch.assert_awaited_once_with(director["url"])

    def test_director_search_prioritizes_page_with_director_statement(self):
        pages = [
            {"title": "ИФМиБ КФУ директор новости", "url": "https://kpfu.ru/news",
             "text": "Новости ИФМиБ КФУ. Мероприятие и участники."},
            {"title": "Руководство", "url": "https://kpfu.ru/director",
             "text": "Директор института Иванов Иван Иванович."},
        ]
        import asyncio
        async def run():
            with patch("app.sources.institute.crawl_pages", new=AsyncMock(return_value=pages)):
                return await search_institute_info("директор ИФМиБ КФУ")
        self.assertEqual(asyncio.run(run())[0]["url"], pages[1]["url"])

    def test_extracts_page_body_without_header_or_sidebar_navigation(self):
        html = """
        <div id="ss_content">
          <div class="area_width">
            <ul><li>Телефон приемной комиссии</li><li>Развитие ИФМиБ</li></ul>
            <h1>Организационная структура</h1>
            <p>В институт входят Высшая школа медицины и Высшая школа биологии.</p>
            <ul class="menu_list_left"><li>Сторонняя навигационная ссылка</li></ul>
          </div>
        </div>
        """

        text = _extract_main_text(BeautifulSoup(html, "html.parser"))

        self.assertIn("В институт входят", text)
        self.assertNotIn("Телефон приемной комиссии", text)
        self.assertNotIn("Сторонняя навигационная ссылка", text)

    def test_training_program_query_expands_to_site_vocabulary(self):
        expanded = _expand_query("Какие направления подготовки есть в ИФМиБ КФУ?")

        self.assertIn("образовательные программы", expanded)
        self.assertIn("бакалавриат", expanded)
        self.assertIn("специалитет", expanded)

    def test_training_program_search_matches_official_program_page(self):
        pages = [{
            "title": "Основные образовательные программы Высшей школы биологии",
            "url": "https://kpfu.ru/biology-medicine/programs",
            "text": "Бакалавриат 06.03.01 Биология. Магистратура 06.04.01 Биология.",
        }]

        async def run():
            with patch("app.sources.institute.crawl_pages", new=AsyncMock(return_value=pages)):
                return await search_institute_info("Какие направления подготовки есть в ИФМиБ КФУ?")

        import asyncio
        results = asyncio.run(run())

        self.assertEqual(results[0]["url"], pages[0]["url"])


if __name__ == "__main__":
    unittest.main()
