import unittest

from pydantic import ValidationError

from app.answer import (
    Answer,
    cited_source_ids,
    enforce_tool_evidence,
    parse_answer,
    provider_cost_usd,
    remap_source_ids,
    sanitize_markdown_links,
    tool_result_has_evidence,
)


class AnswerSchemaTests(unittest.TestCase):
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
        answer = Answer(answer="Код 31.05.03", source_tool="search_institute_info", found=True, sources=[])

        checked = enforce_tool_evidence(answer, evidence_tools={"search_institute_info"})

        self.assertTrue(checked.found)

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

    def test_provider_reported_cost_is_used_when_available(self):
        self.assertEqual(provider_cost_usd({"cost": 0.00123}), 0.00123)
        self.assertIsNone(provider_cost_usd({"prompt_tokens": 20}))


if __name__ == "__main__":
    unittest.main()
