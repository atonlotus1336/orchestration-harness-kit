"""Fail-closed M2 LLM memory authoring helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable, List, Optional

PROMPT = (
    "author 1-3 memories from this note: episode|decision|procedure, "
    "one sentence each, no headers/scaffolding"
)


@dataclass
class AuthoredMemory:
    kind: str
    text: str


def choose_model(note: str, source_file: str = "") -> str:
    haystack = f"{source_file}\n{note}".lower()
    if any(tok in haystack for tok in (".py", ".ts", ".tsx", ".js", "code", "function", "class ")):
        return "qwen2.5-coder:14b"
    return "hermes3:8b"


def _windows_host_ip() -> Optional[str]:
    """In WSL2 NAT mode the Windows host is the default-route gateway."""
    try:
        out = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    match = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out.stdout or "")
    return match.group(1) if match else None


def _ollama_generate_urls() -> List[str]:
    """Ordered /api/generate endpoints to try before the PowerShell bridge.

    Honors MEMORYWEAVER_OLLAMA_URL when set. Under WSL, Windows Ollama is not on
    WSL loopback, so target the Windows host (default gateway) first; 127.0.0.1
    still covers native Windows and WSL mirrored-networking mode.
    """
    urls: List[str] = []
    override = os.environ.get("MEMORYWEAVER_OLLAMA_URL", "").strip()
    if override:
        base = override.rstrip("/")
        urls.append(base if base.endswith("/api/generate") else base + "/api/generate")
    if _looks_like_wsl():
        host = _windows_host_ip()
        if host:
            urls.append(f"http://{host}:11434/api/generate")
    urls.append("http://127.0.0.1:11434/api/generate")
    ordered: List[str] = []
    for url in urls:
        if url not in ordered:
            ordered.append(url)
    return ordered


def parse_authored_output(text: str) -> List[AuthoredMemory]:
    raw = (text or "").strip()
    if not raw:
        return []
    parsed = _try_json(raw)
    candidates = _records_from_json(parsed) if parsed is not None else _records_from_lines(raw.splitlines())
    out: List[AuthoredMemory] = []
    for item in candidates:
        kind = item.kind if item.kind in {"episode", "decision", "procedure"} else "episode"
        sentence = _one_sentence(item.text)
        if sentence and not re.match(r"^\s*(---|#|\[|\||>)", sentence):
            out.append(AuthoredMemory(kind=kind, text=sentence))
        if len(out) == 3:
            break
    return out


def author_memories(note: str, source_file: str = "", model: Optional[str] = None, timeout: int = 20) -> List[AuthoredMemory]:
    model = model or choose_model(note, source_file)
    body = {
        "model": model,
        "prompt": (
            f"{PROMPT}\nFrom NOTE_TEXT only. Do not invent people, places, or events. "
            "Return only JSON: {\"memories\":[{\"kind\":\"decision\",\"text\":\"...\"}]}\n"
            f"NOTE_TEXT<<<{note}>>>"
        ),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    data = json.dumps(body).encode("utf-8")
    payload = None
    last_exc: Optional[Exception] = None
    for url in _ollama_generate_urls():
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
    if payload is None:
        payload = _generate_via_windows_powershell(
            body, timeout, last_exc or RuntimeError("no local Ollama endpoint reachable")
        )
    memories = [m for m in parse_authored_output(payload.get("response", "")) if _grounded_enough(note, m.text)]
    if not memories:
        raise RuntimeError(f"local Ollama authoring produced no valid memories for {model}")
    return memories


def _generate_via_windows_powershell(body: dict, timeout: int, original_exc: Exception) -> dict:
    """WSL fallback for Windows Ollama bound to 127.0.0.1 only."""
    if not _looks_like_wsl():
        raise RuntimeError(f"local Ollama authoring failed for {body.get('model')}: {original_exc}") from original_exc
    ps = (
        "$body = [Console]::In.ReadToEnd(); "
        "$response = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' "
        "-Method Post -ContentType 'application/json' -Body $body -TimeoutSec "
        f"{max(timeout, 1)}; "
        "$response | ConvertTo-Json -Depth 20 -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            input=json.dumps(body),
            text=True,
            capture_output=True,
            timeout=timeout + 10,
        )
    except Exception as exc:
        raise RuntimeError(f"local Ollama PowerShell bridge failed for {body.get('model')}: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"local Ollama PowerShell bridge failed for {body.get('model')}: {proc.stderr.strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"local Ollama PowerShell bridge returned invalid JSON for {body.get('model')}") from exc


def _looks_like_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in open("/proc/version", "r", encoding="utf-8", errors="ignore").read().lower()
    except OSError:
        return False


def _grounded_enough(note: str, memory: str) -> bool:
    note_words = set(_content_words(note))
    memory_words = _content_words(memory)
    if not memory_words:
        return False
    overlap = sum(1 for word in memory_words if word in note_words)
    return (overlap / len(memory_words)) >= 0.4


def _content_words(text: str) -> List[str]:
    stop = {
        "about", "after", "before", "being", "from", "into", "only", "that",
        "their", "there", "this", "uses", "with", "when", "where", "while",
    }
    return [
        _stem(w.lower())
        for w in re.findall(r"[A-Za-z0-9_.-]{3,}", text or "")
        if w.lower() not in stop
    ]


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _try_json(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"(\{.*\}|\[.*\])", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                return None
    return None


def _records_from_json(value) -> List[AuthoredMemory]:
    if isinstance(value, dict):
        if isinstance(value.get("memories"), list):
            value = value.get("memories")
        elif "text" in value:
            value = [value]
        else:
            value = [{"kind": k, "text": v} for k, v in value.items()]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str):
            out.append(AuthoredMemory(kind="episode", text=item))
        elif isinstance(item, dict):
            out.append(AuthoredMemory(kind=str(item.get("kind") or item.get("type") or "episode"), text=str(item.get("text") or "")))
    return out


def _records_from_lines(lines: Iterable[str]) -> List[AuthoredMemory]:
    out = []
    for line in lines:
        cleaned = re.sub(r"^\s*(?:[-*\d.]+|\w+\s*:)\s*", "", line).strip()
        if cleaned:
            out.append(AuthoredMemory(kind="episode", text=cleaned))
    return out


def _one_sentence(text: str) -> str:
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    match = re.match(r"^(.+?[.!?])(?:\s|$)", flat)
    sentence = (match.group(1) if match else flat).strip()
    sentence = re.sub(r"^\.+\s*", "", sentence)
    sentence = re.sub(r"\s*\.+$", ".", sentence)
    if sentence and sentence[-1] not in ".!?":
        sentence += "."
    return sentence
