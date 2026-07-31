"""M2 LLM authoring parser tests. Runnable WITHOUT pytest."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memoryweaver.llm_author import choose_model, parse_authored_output


def test_parse_json_authored_memories():
    out = parse_authored_output(
        '{"memories":[{"kind":"decision","text":"Use the freshness beacon to suppress stale digests."}]}'
    )
    assert len(out) == 1
    assert out[0].kind == "decision"
    assert out[0].text == "Use the freshness beacon to suppress stale digests."


def test_parse_rejects_scaffolding_and_caps_at_three():
    out = parse_authored_output(
        "# header\n"
        "- The first useful memory is clean.\n"
        "- The second useful memory is clean.\n"
        "- The third useful memory is clean.\n"
        "- The fourth useful memory is ignored.\n"
    )
    assert [x.text for x in out] == [
        "The first useful memory is clean.",
        "The second useful memory is clean.",
        "The third useful memory is clean.",
    ]


def test_choose_model_routes_code_notes():
    assert choose_model("Refactor this function body", "x.py") == "qwen2.5-coder:14b"
    assert choose_model("Remember the user preference", "note.md") == "hermes3:8b"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\nALL GREEN: {len(tests)}/{len(tests)} M2 LLM-author tests passed")


if __name__ == "__main__":
    _run_all()
