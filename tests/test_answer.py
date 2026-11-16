import unittest
import app.answer as answer_module

from pydantic import ValidationError

from app.answer import (
    Answer,
    cited_source_ids,
    enforce_tool_evidence,
    parse_answer,
    provider_cost_usd,
    remap_source_ids,
    remap_source_records,
    sanitize_markdown_links,
    tool_result_has_evidence,
    tool_result_source_urls,
)


class AnswerSchemaTests(unittest.TestCase):
    def test_director_claim_requires_name_near_director_in_selected_source(self):
        check = getattr(answer_module, "director_claim_supported", None)
        self.assertIsNotNone(check)
        self.assertTrue(check("Кто директор ИФМиБ?", "Директор — Иванов Иван Иванович.",
                              ["Директор института Иванов Иван Иванович."]))
        self.assertFalse(check("Кто директор ИФМиБ?", "Директор — Петров Петр Петрович.",
                               ["Директор института Иванов Иван Иванович."]))
        self.assertFalse(check("Кто директор ИФМиБ?", "Директор — Петров Петр Петрович.",
                               ["Бывший директор института Петров Петр Петрович."]))

    def test_director_source_can_put_name_and_job_in_separate_sentences(self):
        excerpt = ("Директор Института фундаментальной медицины и биологии\n"
                   "Киясов Андрей Павлович, доктор медицинских наук, профессор. "
                   "Директор Института фундаментальной медицины и биологии.")
        self.assertTrue(answer_module.director_claim_supported(
            "Кто директор ИФМиБ?", "Директор — Киясов Андрей Павлович.", [excerpt]
        ))

    def test_group_clarification_is_not_a_factual_claim(self):
        check = getattr(answer_module, "is_group_clarification", None)
        self.assertIsNotNone(check)
        self.assertTrue(check("Пожалуйста, укажите вашу группу, чтобы я мог найти расписание на завтра."))
        self.assertFalse(check("Ваша группа ИВТ-21. Укажите вашу группу?"))

    def test_payment_error_explains_limit_without_exposing_error_text(self):
        render = getattr(answer_module, "provider_error_message", None)
        self.assertIsNotNone(render)
        error = type("ProviderError", (Exception,), {"status_code": 402})("secret")
        self.assertIn("баланс", render(error).casefold())
        self.assertNotIn("secret", render(error))
        self.assertIn("соединение", render(Exception()).casefold())

    def test_parent_source_is_not_hidden_by_a_longer_url(self):
        parent = "https://kpfu.ru/biology-medicine/obrazovanie/specialitet"
        text = f"Программа: [Стоматология]({parent}/stomatologiya)."
        render = getattr(answer_module, "append_source_citations", None)
        self.assertIsNotNone(render)
        self.assertIn(f"[14]({parent})", render(text, [(14, parent)]))

    def test_exact_source_link_is_not_duplicated(self):
        url = "https://kpfu.ru/biology-medicine/obrazovanie/specialitet"
        text = f"Источник: [Программы]({url})."
        render = getattr(answer_module, "append_source_citations", None)
        self.assertIsNotNone(render)
        self.assertEqual(render(text, [(14, url)]), text)

    def test_schedule_formatter_keeps_subgroups_and_conflicting_source_time(self):
        from app.answer import format_schedule_answer

        text = format_schedule_answer({
            "status": "ok",
            "group": "01.2-513",
            "source_page": "https://kpfu.ru/exams",
            "sources": [{"classes": [
                {"weekday": "понедельник", "time": "10.00-11.30",
                 "source_time": "10.10-11.40", "subgroup": "01.2-513а",
                 "classes": ["Элективная физкультура"]},
                {"weekday": "понедельник", "time": "12.10-13.40",
                 "source_time": "12.10-13.40", "subgroup": "01.2-513б",
                 "classes": ["Анатомия"]},
            ]}],
        })

        self.assertIn("01.2-513а", text)
        self.assertIn("01.2-513б", text)
        self.assertIn("10.00-11.30", text)
        self.assertIn("10.10-11.40", text)
        self.assertIn("https://kpfu.ru/exams", text)

    def test_accepts_valid_answer_schema(self):
        answer = parse_answer(
            '{"answer":"Нашёл документ.","source_tool":"search_docs",'
            '"found":true,"sources":[1]}'
        )

        self.assertEqual(answer, Answer(
            answer="Нашёл документ.", source_tool="search_docs", found=True, sources=[1]
        ))

    def test_rejects_missing_required_fields_and_extra_fields(self):
        with self.assertRaises(ValidationError):
            parse_answer('{"answer":"Ответ","found":true,"secret":"x"}')

    def test_factual_answer_requires_a_tool_that_was_actually_called(self):
        answer = Answer(answer="Код 31.05.03", source_tool="search_docs", found=True, sources=[])

        checked = enforce_tool_evidence(answer, evidence_tools=set())

        self.assertFalse(checked.found)
        self.assertIsNone(checked.source_tool)
        self.assertIn("не могу подтвердить", checked.answer.casefold())

    def test_tool_answer_is_allowed_when_that_source_returned_evidence(self):
        answer = Answer(answer="Код 31.05.03", source_tool="search_institute_info", found=True, sources=[1])

        checked = enforce_tool_evidence(
            answer, evidence_tools={"search_institute_info"}, available_source_ids={1}
        )

        self.assertTrue(checked.found)

    def test_institute_answer_requires_a_selected_result(self):
        answer = Answer(answer="Программы есть", source_tool="search_institute_info", found=True, sources=[])

        checked = enforce_tool_evidence(
            answer, evidence_tools={"search_institute_info"}, available_source_ids={1}
        )

        self.assertFalse(checked.found)

    def test_search_answer_requires_a_valid_selected_source(self):
        answer = Answer(answer="Код 31.05.03", source_tool="search_docs", found=True, sources=[])

        checked = enforce_tool_evidence(
            answer, evidence_tools={"search_docs"}, available_source_ids=set()
        )

        self.assertFalse(checked.found)
        self.assertEqual(checked.sources, [])

    def test_search_answer_accepts_a_source_selected_from_this_turn(self):
        answer = Answer(answer="Код 31.05.03", source_tool="search_docs", found=True, sources=[2])

        checked = enforce_tool_evidence(
            answer, evidence_tools={"search_docs"}, available_source_ids={1, 2}
        )

        self.assertTrue(checked.found)
        self.assertEqual(checked.sources, [2])

    def test_search_docs_only_counts_results_that_include_sources(self):
        self.assertFalse(tool_result_has_evidence("search_docs", '{"found":false,"sources":[]}'))
        self.assertTrue(tool_result_has_evidence(
            "search_docs", '{"found":true,"sources":[{"id":1,"url":"https://example.test"}]}'
        ))

    def test_search_docs_answer_does_not_claim_all_sources_if_model_omits_ids(self):
        answer = Answer(answer="Код 31.05.03", source_tool="search_docs", found=True, sources=[])

        ids = cited_source_ids(answer, {1: "https://kpfu.ru/program"})

        self.assertEqual(ids, [])

    def test_model_citations_are_filtered_to_sources_retrieved_this_turn(self):
        answer = Answer(answer="Код 31.05.03", source_tool="search_docs", found=True, sources=[9])

        ids = cited_source_ids(answer, {1: "https://kpfu.ru/program"})

        self.assertEqual(ids, [])

    def test_repeated_search_results_receive_unique_ids_for_the_turn(self):
        sources = {}
        first, next_id = remap_source_ids(
            {"sources": [{"id": 1, "url": "https://example.test/a"}]}, sources, 1
        )
        second, next_id = remap_source_ids(
            {"sources": [{"id": 1, "url": "https://example.test/b"}]}, sources, next_id
        )

        self.assertEqual(first["sources"][0]["id"], 1)
        self.assertEqual(second["sources"][0]["id"], 2)
        self.assertEqual(sources, {1: "https://example.test/a", 2: "https://example.test/b"})

    def test_markdown_links_are_limited_to_sources_returned_this_turn(self):
        text = "[valid](https://kpfu.ru/page) [fake](https://evil.test/page)"

        result = sanitize_markdown_links(text, {"https://kpfu.ru/page"})

        self.assertEqual(result, "[valid](https://kpfu.ru/page) fake")

    def test_official_search_results_are_allowed_as_citation_links(self):
        urls = tool_result_source_urls(
            "search_institute_info",
            '[{"title":"Расписание","url":"https://kpfu.ru/exams"}]',
        )

        self.assertEqual(urls, {"https://kpfu.ru/exams"})

    def test_institute_search_results_receive_turn_wide_source_ids(self):
        sources = {}
        rows, next_id = remap_source_records(
            [{"title": "Программы", "url": "https://kpfu.ru/programs"}], sources, 4
        )

        self.assertEqual(rows[0]["id"], 4)
        self.assertEqual(next_id, 5)
        self.assertEqual(sources, {4: "https://kpfu.ru/programs"})

    def test_provider_reported_cost_is_used_when_available(self):
        self.assertEqual(provider_cost_usd({"cost": 0.00123}), 0.00123)
        self.assertIsNone(provider_cost_usd({"prompt_tokens": 20}))


if __name__ == "__main__":
    unittest.main()
