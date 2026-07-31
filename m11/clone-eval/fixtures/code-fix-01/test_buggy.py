from buggy import parse_scoring_json


def test_clean_json():
    result = parse_scoring_json('{"relevant": true, "difficulty": "easy"}')
    assert result["relevant"] is True


def test_json_in_markdown():
    text = 'Here is my assessment:\n```json\n{"relevant": false}\n```'
    result = parse_scoring_json(text)
    assert result is not None
    assert result["relevant"] is False


def test_json_with_surrounding_text():
    text = 'I think this is relevant. {"relevant": true, "category": "sorting"} That is my answer.'
    result = parse_scoring_json(text)
    assert result["category"] == "sorting"


def test_no_json_returns_none():
    assert parse_scoring_json("This is just plain text with no JSON") is None


def test_malformed_json_returns_none():
    assert parse_scoring_json("{broken json: ???}") is None


def test_non_dict_json_returns_none():
    assert parse_scoring_json('[1, 2, 3]') is None
    assert parse_scoring_json('"just a string"') is None


def test_nested_braces_in_values():
    text = 'Here: {"relevant": true, "expected_behavior": "handle {edge} cases"}'
    result = parse_scoring_json(text)
    assert result is not None
    assert result["relevant"] is True
    assert "{edge}" in result["expected_behavior"]


def test_unbalanced_literal_brace_in_string_value():
    text = 'Here: {"relevant": true, "expected_behavior": "handle a literal { brace"}'
    result = parse_scoring_json(text)
    assert result is not None
    assert result["expected_behavior"] == "handle a literal { brace"


def test_empty_string_returns_none():
    assert parse_scoring_json("") is None
