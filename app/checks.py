from __future__ import annotations

from collections import Counter
from collections.abc import Iterable


def _context_for(turn: dict, history: list[str], memory: dict[str, str]) -> str:
    needs = turn.get("needs", "none")
    if needs == "history":
        return "\n".join(history[-4:])
    if needs == "memory":
        return "\n".join(f"{key}={value}" for key, value in memory.items())
    if needs == "none":
        return ""
    raise ValueError(f"unknown needs value: {needs}")


def evaluate_dialogues(dialogues: Iterable[dict]) -> dict:
    rows = list(dialogues)
    all_memory = {d["user_id"]: d.get("memory", {}) for d in rows}
    turn_count = 0
    context_hits = 0
    leaks = 0
    estimated_tokens = 0
    details = []

    for dialogue in rows:
        history: list[str] = []
        memory = dialogue.get("memory", {})
        foreign_facts = [
            f"{key}={value}"
            for owner, facts in all_memory.items() if owner != dialogue["user_id"]
            for key, value in facts.items()
        ]
        for turn in dialogue["turns"]:
            context = _context_for(turn, history, memory)
            evidence = turn.get("evidence", "")
            context_ok = not evidence or evidence.casefold() in context.casefold()
            leak = any(fact and fact in context for fact in foreign_facts)
            turn_count += 1
            context_hits += int(context_ok)
            leaks += int(leak)
            estimated_tokens += (len(context) + 3) // 4
            details.append({"dialogue_id": dialogue["id"], "needs": turn.get("needs", "none"),
                            "context_ok": context_ok, "leak": leak})
            history.extend((f"user: {turn['query']}", f"assistant: {turn['answer']}"))

    return {
        "dialogues": len(rows),
        "turns": turn_count,
        "context_ok": context_hits / turn_count if turn_count else 1.0,
        "tool_ok": None,
        "honest": None,
        "leak": leaks,
        "estimated_context_tokens": estimated_tokens,
        "cost_per_query_usd": None,
        "details": details,
    }


def validate_dataset(dialogues: list[dict], expected_dialogues: int = 10) -> None:
    if len(dialogues) != expected_dialogues:
        raise ValueError(f"expected {expected_dialogues} dialogues, got {len(dialogues)}")
    allowed_needs = {"history", "memory", "none"}
    for dialogue in dialogues:
        count = len(dialogue.get("turns", []))
        if not 2 <= count <= 4:
            raise ValueError(f"dialogue {dialogue.get('id')} must contain 2–4 turns")
        for turn in dialogue["turns"]:
            if turn.get("needs", "none") not in allowed_needs:
                raise ValueError(f"invalid needs in dialogue {dialogue.get('id')}")


def summarize_question_golden(
    questions: list[dict], expected_count: int = 60
) -> dict:
    """Summarize question categories without inventing live model metrics."""
    if len(questions) != expected_count:
        raise ValueError(f"expected {expected_count} questions, got {len(questions)}")

    allowed_kinds = {"fact", "semantic", "unknown", "refuse"}
    kinds = [question.get("kind") for question in questions]
    invalid = sorted(set(kinds) - allowed_kinds)
    if invalid:
        raise ValueError(f"invalid question kinds: {', '.join(map(str, invalid))}")

    return {
        "questions": len(questions),
        "kind_counts": dict(Counter(kinds)),
        "metrics": {"tool_ok": None, "honest": None},
    }
