#!/usr/bin/env python3
"""Smallest correct messenger adapter (see ADAPTER-CONTRACT.md).

Stateless relay: inbound text -> OpenAI-compatible backend -> reply text.
Stdlib only. Works against a Hermes gateway, Ollama, or any /v1 endpoint.

Usage:
  one-shot : python generic-adapter.py "your message"
  webhook  : echo '{"text":"hi","sender_id":"123"}' | python generic-adapter.py
Env:
  GATEWAY_URL      backend base URL      (default http://127.0.0.1:8642/v1)
  GATEWAY_API_KEY  bearer key            (default none; ALWAYS set for gateways)
  MODEL            model name            (default: backend's default, "default")
  ALLOWED_SENDERS  comma-separated ids   (default: allow all in one-shot mode,
                                          DENY all unlisted in webhook mode)
Exit codes: 0 ok | 2 backend unreachable | 3 auth rejected | 4 sender denied
            5 bad input | 6 backend error (other)
"""
import json
import os
import sys
import urllib.error
import urllib.request

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8642/v1").rstrip("/")
API_KEY = os.environ.get("GATEWAY_API_KEY", "")
MODEL = os.environ.get("MODEL", "default")
ALLOWED = [s for s in os.environ.get("ALLOWED_SENDERS", "").split(",") if s]


def chat(text: str) -> str:
    req = urllib.request.Request(
        GATEWAY_URL + "/chat/completions",
        data=json.dumps({
            "model": MODEL,
            "messages": [{"role": "user", "content": text}],
        }).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": "Bearer " + API_KEY} if API_KEY else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.load(resp)
    return body["choices"][0]["message"]["content"]


def fail(code: int, msg: str):
    print("adapter-error: " + msg, file=sys.stderr)
    sys.exit(code)


def main():
    if len(sys.argv) > 1:                       # one-shot CLI mode
        text, sender = " ".join(sys.argv[1:]), None
    else:                                        # webhook/stdin mode
        try:
            payload = json.load(sys.stdin)
            text = payload["text"]
            sender = str(payload.get("sender_id", ""))
        except Exception:
            fail(5, 'stdin must be JSON: {"text": "...", "sender_id": "..."}')
        # webhook mode: deny-by-default sender gate
        if sender not in ALLOWED:
            fail(4, "sender not in ALLOWED_SENDERS (deny-by-default)")

    if not text or not text.strip():
        fail(5, "empty message")

    try:
        print(chat(text))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            fail(3, "backend rejected auth (%d) - check GATEWAY_API_KEY" % e.code)
        fail(6, "backend HTTP %d: %s" % (e.code, e.read()[:200].decode(errors="replace")))
    except urllib.error.URLError as e:
        fail(2, "backend unreachable at %s (%s)" % (GATEWAY_URL, e.reason))
    except TimeoutError:
        fail(6, "backend timeout (model may be loading) - retry once")


if __name__ == "__main__":
    main()
