#!/usr/bin/env python3
"""Deny-by-default Telegram long-poll adapter for an OpenAI-compatible backend."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def allowed_senders(raw: str) -> set[int]:
    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if item:
            values.add(int(item))
    return values


def extract_message(update: object) -> tuple[int, int, str] | None:
    if not isinstance(update, dict):
        return None
    message = update.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("text"), str):
        return None
    sender = message.get("from", {}).get("id")
    chat = message.get("chat", {}).get("id")
    if not isinstance(sender, int) or not isinstance(chat, int):
        return None
    return sender, chat, message["text"]


def request_json(url: str, payload: dict | None = None, headers: dict | None = None, timeout: int = 40) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers or {})
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def backend_reply(base_url: str, key: str, model: str, text: str) -> str:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    value = request_json(
        base_url.rstrip("/") + "/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": text}]},
        headers,
        timeout=180,
    )
    try:
        return str(value["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("backend returned an invalid chat response") from exc


def telegram_call(token: str, method: str, payload: dict) -> dict:
    return request_json(f"https://api.telegram.org/bot{token}/{method}", payload)


def send_reply(token: str, chat_id: int, text: str) -> None:
    chunks = [text[index:index + 4000] for index in range(0, len(text), 4000)] or ["(empty reply)"]
    for chunk in chunks:
        value = telegram_call(token, "sendMessage", {"chat_id": chat_id, "text": chunk})
        if value.get("ok") is not True:
            raise RuntimeError("Telegram rejected sendMessage")


def selftest() -> int:
    valid = {"message": {"from": {"id": 7}, "chat": {"id": 9}, "text": "hello"}}
    cases = [
        (extract_message(valid) == (7, 9, "hello"), "valid text"),
        (extract_message({"message": {"photo": []}}) is None, "non-text ignored"),
        (7 in allowed_senders("7, 11"), "allowed sender accepted"),
        (8 not in allowed_senders("7, 11"), "unknown sender denied"),
    ]
    failures = [name for passed, name in cases if not passed]
    print(json.dumps({"cases": len(cases), "failures": failures}, sort_keys=True))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process one polling response, then stop")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    sender_raw = os.environ.get("TELEGRAM_ALLOWED_SENDER_IDS", "").strip()
    gateway = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8642/v1").strip()
    gateway_key = os.environ.get("GATEWAY_KEY", "").strip()
    model = os.environ.get("MODEL", "").strip()
    offset_path = Path(os.environ.get("TELEGRAM_OFFSET_FILE", ".telegram-offset"))
    if not token or not sender_raw or not model:
        print("configuration error: TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_SENDER_IDS, and MODEL are required", file=sys.stderr)
        return 2
    try:
        senders = allowed_senders(sender_raw)
    except ValueError:
        print("configuration error: sender IDs must be comma-separated integers", file=sys.stderr)
        return 2
    offset = int(offset_path.read_text(encoding="utf-8").strip()) if offset_path.exists() else 0

    while True:
        try:
            response = telegram_call(token, "getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message"]})
            if response.get("ok") is not True:
                raise RuntimeError("Telegram rejected getUpdates; check the token or another active consumer")
            for update in response.get("result", []):
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = max(offset, update_id + 1)
                    offset_path.write_text(str(offset), encoding="utf-8")
                extracted = extract_message(update)
                if extracted is None:
                    continue
                sender_id, chat_id, text = extracted
                if sender_id not in senders:
                    print(f"denied sender_id={sender_id}", file=sys.stderr)
                    continue
                try:
                    reply = backend_reply(gateway, gateway_key, model, text)
                    send_reply(token, chat_id, reply)
                except (OSError, RuntimeError, urllib.error.URLError) as exc:
                    print(f"message failed: {type(exc).__name__}", file=sys.stderr)
            if args.once:
                return 0
        except urllib.error.HTTPError as exc:
            reason = "unauthorized token" if exc.code == 401 else "Telegram HTTP error"
            print(f"poll failed: {reason} ({exc.code})", file=sys.stderr)
            return 3 if args.once or exc.code == 401 else 0
        except (OSError, RuntimeError, urllib.error.URLError):
            print("poll failed: network unavailable or another consumer owns the bot", file=sys.stderr)
            if args.once:
                return 3
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
