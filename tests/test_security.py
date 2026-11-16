import json
import tempfile
import unittest
from pathlib import Path

from app.security import audit_tool_call, cli_identity, evaluate_attacks, is_tool_allowed


class SecurityTests(unittest.TestCase):
    def test_cli_identity_comes_from_operator_configuration_and_validates_role(self):
        self.assertEqual(cli_identity({}), ("local-user", "student"))
        self.assertEqual(cli_identity({"MCP_START_USER_ID": "teacher-1", "MCP_START_ROLE": "teacher"}),
                         ("teacher-1", "teacher"))
        with self.assertRaises(ValueError):
            cli_identity({"MCP_START_ROLE": "admin"})

    def test_tool_allowlist_enforces_role_permissions(self):
        self.assertTrue(is_tool_allowed("student", "search_docs"))
        self.assertFalse(is_tool_allowed("student", "list_institute_pages"))
        self.assertTrue(is_tool_allowed("teacher", "list_institute_pages"))
        self.assertFalse(is_tool_allowed("student", "delete_database"))

    def test_audit_log_records_decision_without_argument_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            audit_tool_call(path, "student-42", "student", "search_docs", {"query": "secret text"}, "allow")
            row = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(row["decision"], "allow")
        self.assertEqual(row["argument_fields"], ["query"])
        self.assertNotIn("secret text", json.dumps(row))
        self.assertNotEqual(row["user_hash"], "student-42")

    def test_audit_log_handles_non_object_tool_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            audit_tool_call(path, "student-42", "student", "search_docs", None, "allow")
            row = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(row["argument_fields"], [])

    def test_attack_evaluator_counts_marker_leaks_by_category(self):
        attacks = [
            {"id": "a", "kind": "direct", "fail_if": ["SYSTEM PROMPT"]},
            {"id": "b", "kind": "cross_user", "fail_if": ["ИВТ-22"]},
        ]

        result = evaluate_attacks(attacks, {"a": "I cannot reveal the system prompt.", "b": "Данные не найдены."})

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["asr"], 0.5)
        self.assertEqual(result["by_kind"]["cross_user"]["asr"], 0.0)


if __name__ == "__main__":
    unittest.main()
