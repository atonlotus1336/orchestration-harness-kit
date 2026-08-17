#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def main() -> int:
    env_path = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else "/home/__LINUX_USER__/.hermes/profiles/telegram/.env"
    )
    values = load_env(env_path)
    token = values.get("TELEGRAM_BOT_TOKEN", "")
    chat = (
        values.get("TELEGRAM_ALLOWED_USERS", "").split(",")[0].strip()
        or values.get("TELEGRAM_HOME_CHANNEL", "").strip()
    )
    if not token or not chat:
        print("missing telegram token or chat target", file=sys.stderr)
        return 2

    text = (
        "Hermes Telegram gateway test from Codex at 2026-06-09 04:50 ET. "
        "Reply to this message to confirm the gateway receives you."
    )
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode())

    result = payload.get("result") or {}
    chat_info = result.get("chat") or {}
    print(
        json.dumps(
            {
                "ok": payload.get("ok"),
                "message_id": result.get("message_id"),
                "chat_type": chat_info.get("type"),
            },
            indent=2,
        )
    )
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
