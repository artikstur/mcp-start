import unittest

from bs4 import BeautifulSoup

from app.sources.institute import _extract_main_text


class InstituteExtractionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
