"""
Parser — Convert session *.md and DECISIONS.md entries to MemoryRecord.

3-tier fallback:
  Tier 1: YAML frontmatter (explicit confidence)
  Tier 2: regex heuristic (inferred confidence)
  Tier 3: raw text + filename timestamp (inferred confidence, ParseWarning emitted)
"""

from __future__ import annotations

import re
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .models import MemoryRecord
from .capture_quality import clean_memory_line, is_standalone_noise_line


# ---------------------------------------------------------------------------
# Warning type
# ---------------------------------------------------------------------------

class ParseWarning(UserWarning):
    """Emitted on partial parse; caller decides whether to ingest or quarantine."""


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def parse_session_file(path: Path, project: Optional[str] = None) -> List[MemoryRecord]:
    """Parse a session *.md file into one or more MemoryRecord objects."""
    text = path.read_text(encoding="utf-8", errors="replace")
    records: List[MemoryRecord] = []

    # Try tier 1 — YAML frontmatter
    fm, body = _extract_frontmatter(text)
    if fm is not None:
        records.extend(_records_from_frontmatter(fm, body, path, project))
        return records

    # Try tier 2 — regex heuristic
    heuristic = _records_from_heuristic(text, path, project)
    if heuristic:
        records.extend(heuristic)
        return records

    # Tier 3 — raw fallback
    warnings.warn(
        f"ParseWarning: no frontmatter or heuristic structure in {path.name} — "
        "ingesting as raw text with inferred confidence.",
        ParseWarning,
        stacklevel=2,
    )
    records.append(_record_from_raw(text, path, project))
    return records


def parse_decisions_file(path: Path, project: Optional[str] = None) -> List[MemoryRecord]:
    """Parse a decisions-*.md file. Each line is one decision record."""
    records: List[MemoryRecord] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = clean_memory_line(line)
        if is_standalone_noise_line(line):
            continue
        # Match: [YYYY-MM-DD] — decision name: rationale. Projects: ... Thread: ...
        rec = parse_decision_line(line, path, project)
        if rec:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Tier 1 — YAML frontmatter
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _extract_frontmatter(text: str) -> Tuple[Optional[dict], str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    try:
        import yaml  # optional; only needed for tier-1 paths
        fm = yaml.safe_load(m.group(1))
        return (fm if isinstance(fm, dict) else None), text[m.end():]
    except Exception:
        return None, text


def _records_from_frontmatter(
    fm: dict, body: str, path: Path, project: Optional[str]
) -> List[MemoryRecord]:
    # Frontmatter notes without an explicit date fall back to the inferred timestamp
    # (filename date -> file mtime -> now), same as the heuristic path. Without this,
    # a dateless frontmatter note yields timestamp=None and crashes to_chroma_doc().
    ts = _parse_timestamp(fm.get("date") or fm.get("timestamp")) or _infer_timestamp(path)
    proj = fm.get("project") or project or _infer_project(path)
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    records = []
    # Split body into chunks by H2 heading
    chunks = _split_by_heading(body) or [(None, body.strip())]
    for heading, chunk in chunks:
        if not chunk.strip():
            continue
        rec = MemoryRecord(
            text=chunk.strip(),
            source_uri=str(path),
            memory_type="session",
            confidence="explicit",
            timestamp=ts,
            session_id=fm.get("session_id") or path.stem,
            wing=fm.get("wing"),
            room=fm.get("room") or heading,
            drawer=fm.get("drawer"),
            project=proj,
            thread=fm.get("thread"),
            tags=tags,
            category=fm.get("category"),
        )
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Tier 2 — regex heuristic
# ---------------------------------------------------------------------------

_DATE_IN_FILENAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DECISION_LINE_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2})\]\s*[—–-]\s*(.+?):\s*(.+?)(?:\.\s*Projects?:\s*([^.]+))?(?:\.\s*Thread:\s*(\S+))?\.?\s*$"
)
_H2_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


def _records_from_heuristic(
    text: str, path: Path, project: Optional[str]
) -> List[MemoryRecord]:
    records = []
    proj = project or _infer_project(path)
    ts = _infer_timestamp(path)
    session_id = path.stem

    chunks = _split_by_heading(text)
    if not chunks:
        return []

    for heading, chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        rec = MemoryRecord(
            text=chunk,
            source_uri=str(path),
            memory_type="session",
            confidence="inferred",
            timestamp=ts,
            session_id=session_id,
            room=heading,
            project=proj,
        )
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Tier 3 — raw fallback
# ---------------------------------------------------------------------------

def _record_from_raw(text: str, path: Path, project: Optional[str]) -> MemoryRecord:
    return MemoryRecord(
        text=text.strip(),
        source_uri=str(path),
        memory_type="session",
        confidence="inferred",
        timestamp=_infer_timestamp(path),
        session_id=path.stem,
        project=project or _infer_project(path),
    )


# ---------------------------------------------------------------------------
# Decision line parser
# ---------------------------------------------------------------------------

def parse_decision_line(
    line: str, path: Path, project: Optional[str]
) -> Optional[MemoryRecord]:
    line = clean_memory_line(line)
    if is_standalone_noise_line(line):
        return None
    # Strip markdown strikethrough
    line_clean = re.sub(r"~~(.+?)~~", r"[SUPERSEDED:\1]", line)
    m = _DECISION_LINE_RE.match(line_clean.lstrip("- "))
    if not m:
        # Fallback: treat whole line as inferred decision text
        return MemoryRecord(
            text=line_clean,
            source_uri=str(path),
            memory_type="decision",
            confidence="inferred",
            timestamp=_infer_timestamp(path),
            project=project or _infer_project(path),
        )

    date_str, name, rationale, projects_str, thread = m.groups()
    ts = _parse_timestamp(date_str) or _infer_timestamp(path)
    proj = project or (projects_str.strip().split(",")[0].strip() if projects_str else _infer_project(path))

    return MemoryRecord(
        text=f"{name}: {rationale}",
        source_uri=str(path),
        memory_type="decision",
        confidence="explicit",
        timestamp=ts,
        project=proj,
        thread=thread.strip() if thread else None,
        category="decision",
        superseded="SUPERSEDED" in line,
    )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _split_by_heading(text: str) -> List[Tuple[Optional[str], str]]:
    """Split markdown body into (heading, content) pairs."""
    parts = _H2_RE.split(text)
    if len(parts) < 3:
        return []
    result = []
    # parts: [pre, heading1, content1, heading2, content2, ...]
    for i in range(1, len(parts) - 1, 2):
        result.append((parts[i].strip(), parts[i + 1].strip()))
    return result


def _parse_timestamp(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _infer_timestamp(path: Path) -> datetime:
    m = _DATE_IN_FILENAME_RE.search(path.stem)
    if m:
        dt = _parse_timestamp(m.group(1))
        if dt:
            return dt
    # Fall back to file mtime
    try:
        mtime = path.stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(tz=timezone.utc)


def _infer_project(path: Path) -> Optional[str]:
    """Infer project name from file path (e.g., myproject/...)."""
    parts = path.parts
    for i, part in enumerate(parts):
        if part.lower() == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    return None
