from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.checks import evaluate_dialogues, validate_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальная проверка контекста диалогов из golden.jsonl")
    parser.add_argument("--golden", type=Path, default=Path(__file__).parent / "golden.jsonl")
    args = parser.parse_args()
    with args.golden.open(encoding="utf-8") as source:
        dialogues = [json.loads(line) for line in source if line.strip()]
    validate_dataset(dialogues)
    print(json.dumps(evaluate_dialogues(dialogues), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
