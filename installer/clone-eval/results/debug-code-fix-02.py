"""Secret detection for external eval importers.

Derived from evolution.core.external_importers._contains_secret.
Self-contained so the eval runner needs no repo install.
"""

import re

SECRET_PATTERNS = re.compile(
    r'('
    r'sk-ant-api\S+'
    r'|sk-or-v1-\S+'
    r'|sk-\S{20,}'
    r'|ghp_\S+'
    r'|ghu_\S+'
    r'|xoxb-\S+'
    r'|xapp-\S+'
    r'|ntn_\S+'
    r'|AKIA[0-9A-Z]{16}'
    r'|Bearer\s+\S{20,}'
    r'|-----BEGIN\s+(RSA\s+)?PRIVATE\sKEY-----'
    r'|ANTHROPIC_API_KEY'
    r'|OPENAI_API_KEY'
    r'|OPENROUTER_API_KEY'
    r'|SLACK_BOT_TOKEN'
    r'|GITHUB_TOKEN'
    r'|AWS_SECRET_ACCESS_KEY'
    r'|DATABASE_URL'
    r'|\bpassword\s*[=:]\s*\S+'
    r'|\bsecret\s*[=:]\s*\S+'
    r'|\btoken\s*[=:]\s*\S+'
    r')',
    re.IGNORECASE,
)


def contains_secret(text: str) -> bool:
    return bool(SECRET_PATTERNS.search(text))
