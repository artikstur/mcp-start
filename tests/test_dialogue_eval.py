import unittest

from app.checks import evaluate_dialogues, validate_dataset, summarize_question_golden


class DialogueEvalTests(unittest.TestCase):
    def test_context_needs_are_met_without_cross_user_memory_leaks(self):
        dialogues = [
            {"id": "a", "user_id": "u1", "memory": {"group": "ИВТ-21"}, "turns": [
                {"query": "запомни предмет биохимия", "answer": "Запомнил биохимию", "needs": "none"},
                {"query": "какой предмет?", "answer": "Биохимия", "needs": "history", "evidence": "биохимия"},
                {"query": "моя группа?", "answer": "ИВТ-21", "needs": "memory", "evidence": "ИВТ-21"},
            ]},
            {"id": "b", "user_id": "u2", "memory": {"group": "ИВТ-22"}, "turns": [
                {"query": "какая группа?", "answer": "ИВТ-22", "needs": "memory", "evidence": "ИВТ-22"},
                {"query": "спасибо", "answer": "Пожалуйста", "needs": "none"},
            ]},
        ]
        result = evaluate_dialogues(dialogues)
        self.assertEqual(result["turns"], 5)
        self.assertEqual(result["context_ok"], 1.0)
        self.assertEqual(result["leak"], 0)

    def test_missing_required_history_fails_context_check(self):
        result = evaluate_dialogues([{"id": "a", "user_id": "u1", "turns": [
            {"query": "что это?", "answer": "не знаю", "needs": "history", "evidence": "предмет"},
        ]}])
        self.assertEqual(result["context_ok"], 0.0)

    def test_dataset_validation_enforces_dialogue_count_and_turn_range(self):
        with self.assertRaises(ValueError):
            validate_dataset([{"id": "short", "turns": [{"needs": "none"}]}])

    def test_question_golden_reports_its_four_classes_without_faking_live_metrics(self):
        questions = [
            {"id": 1, "q": "fact?", "kind": "fact", "must_contain": ["fact"], "chunk": "c1"},
            {"id": 2, "q": "semantic?", "kind": "semantic", "chunk": "c1"},
            {"id": 3, "q": "unknown?", "kind": "unknown"},
            {"id": 4, "q": "refuse?", "kind": "refuse"},
        ]
        result = summarize_question_golden(questions, expected_count=4)
        self.assertEqual(result["kind_counts"], {"fact": 1, "semantic": 1, "unknown": 1, "refuse": 1})
        self.assertIsNone(result["metrics"]["tool_ok"])
        self.assertIsNone(result["metrics"]["honest"])


if __name__ == "__main__":
    unittest.main()
