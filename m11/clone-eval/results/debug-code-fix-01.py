"""Extract a JSON object from LLM scoring output.

Derived from evolution.core.external_importers._parse_scoring_json.
Self-contained so the eval runner needs no repo install.

The function must:
  1. Parse clean JSON via json.loads (dict only; arrays/strings -> None).
  2. Fall back to a balanced-brace scan that respects string literals, so
     braces inside string values (e.g. "handle {edge} cases") do not break it.
"""

import json
import re
from typing import Optional


def parse_scoring_json(text: str) -> Optional[dict]:
    if not text:
        return None

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    start = text.find('{')
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None

    return None
