#!/usr/bin/env python3
"""Provider-free fixed-envelope regression sentinel for the portable core."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CATEGORIES = {
    "system_prompt": "system_prompt_bytes",
    "authority_context": "authority_context_bytes",
    "skills_index": "skills_index_bytes",
    "tool_schema": "tool_schema_bytes",
    "selected_content": "selected_content_bytes",
}


def measure(context_path: Path) -> dict[str, int]:
    value = json.loads(context_path.read_text(encoding="utf-8"))
    if value.get("schema") != "lapart.bootstrap-context.v1" or not isinstance(value.get("items"), list):
        raise ValueError("bootstrap context schema is invalid")
    totals = {field: 0 for field in CATEGORIES.values()}
    required = set()
    for item in value["items"]:
        if set(item) != {"id", "category", "content", "required", "priority"}:
            raise ValueError("bootstrap context item keys are not closed")
        category = item["category"]
        if category not in CATEGORIES or not isinstance(item["content"], str):
            raise ValueError("bootstrap context item is invalid")
        totals[CATEGORIES[category]] += len(item["content"].encode("utf-8"))
        if item["required"] is True:
            required.add(category)
    if required != {"system_prompt", "authority_context"}:
        raise ValueError("required system and authority context changed")
    totals["rendered_total_bytes"] = sum(totals.values())
    return totals


def evaluate(measured: dict[str, int], budget: dict) -> list[str]:
    if budget.get("schema") != "lapart.envelope-budget.v1":
        return ["envelope budget schema is invalid"]
    growth = budget.get("maximum_growth_percent")
    baseline = budget.get("baseline")
    if not isinstance(growth, (int, float)) or isinstance(growth, bool) or not isinstance(baseline, dict):
        return ["envelope budget is invalid"]
    failures = []
    if set(measured) != set(baseline):
        return ["envelope attribution fields differ from the baseline"]
    for field, actual in measured.items():
        expected = baseline[field]
        ceiling = expected * (1 + growth / 100)
        if actual > ceiling:
            failures.append(f"{field} grew from {expected} to {actual} bytes (ceiling {ceiling:.2f})")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, default=ROOT / "bootstrap-context.json")
    parser.add_argument("--budget", type=Path, default=ROOT / "envelope-budget.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        measured = measure(args.context)
        budget = json.loads(args.budget.read_text(encoding="utf-8"))
        failures = evaluate(measured, budget)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"BLOCKED: {exc}")
        return 2
    result = {"status": "PASS" if not failures else "HOLD", "provider_calls": 0, "measured": measured, "failures": failures}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif failures:
        print("HOLD: provider-free envelope regression")
        for failure in failures:
            print(f"- {failure}")
    else:
        print(f"PASS: provider-free envelope sentinel ({measured['rendered_total_bytes']} bytes, 0 provider calls)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
