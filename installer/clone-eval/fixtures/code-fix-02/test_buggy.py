from buggy import contains_secret


def test_detects_anthropic_key():
    assert contains_secret("here is sk-ant-api03-abc123def456 my key")


def test_detects_openrouter_key():
    assert contains_secret("sk-or-v1-abcdef1234567890abcdef")


def test_detects_github_pat():
    assert contains_secret("use ghp_abcdefghijklmnopqrstuvwxyz12")


def test_detects_slack_bot_token():
    assert contains_secret("SLACK_TOKEN is xoxb-123-456-abcdef")


def test_detects_bearer_token():
    assert contains_secret("Authorization: Bearer dsm_bYxe4QUvPsDjRThUu2Qb3z")


def test_detects_env_var_openai():
    assert contains_secret("OPENAI_API_KEY=sk-blah")


def test_detects_password_assignment():
    assert contains_secret("password=my_super_secret_123")
    assert contains_secret("password: hunter2abc")


def test_detects_token_assignment_long_value():
    assert contains_secret("token=abcdef1234567890")


def test_ignores_short_token_assignment():
    assert not contains_secret("token=abc")


def test_ignores_normal_text():
    assert not contains_secret("sort these messages by topic")


def test_ignores_short_sk_prefix():
    assert not contains_secret("I asked about sk")


def test_ignores_prose_with_key_word():
    assert not contains_secret("the key insight is that we need to refactor")


def test_ignores_word_bearer_in_prose():
    assert not contains_secret("the bearer of bad news")


def test_ignores_code_token_discussion():
    assert not contains_secret("parse the JWT token from the header")


def test_ignores_ask_substring():
    assert not contains_secret("ask the user for their preferences")
