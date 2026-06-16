"""
DecayScorer — query-time recency multiplier from versioned decay config.

Fixes E-001: policy_version stored per record at write time; results include
decay_score + decay_policy_version so callers have a full audit trail.

Config file: ~/.claude/memory/memoryweaver/decay.yaml
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._paths import MW_DATA_DIR

_DEFAULT_CONFIG_PATH = MW_DATA_DIR / "decay.yaml"

_DEFAULT_POLICY: dict = {
    "version": "1.0",
    "formula": "inverse_days",  # 1 / (1 + days_since_created)
    "half_life_days": None,      # used only by "half_life" formula
}


class DecayScorer:
    """
    Loads versioned decay policy from YAML config and applies it at query time.

    Supported formulas:
      inverse_days   — 1 / (1 + days_since_created)    [default]
      half_life      — 0.5 ^ (days / half_life_days)
      linear         — max(0, 1 - days / max_days)
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        self._policy = self._load_policy()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def policy_version(self) -> str:
        return self._policy.get("version", "1.0")

    def score(self, query_results: List[dict], now: Optional[datetime] = None) -> List[dict]:
        """
        Annotate query results with decay_score and decay_policy_version.

        Input items expected to have 'score' (semantic similarity 0-1)
        and 'metadata' with 'timestamp' ISO string.

        Returns sorted list (combined_score descending).
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)

        scored = []
        for item in query_results:
            ts_str = (item.get("metadata") or {}).get("timestamp", "")
            decay = self._compute_decay(ts_str, now)
            semantic = item.get("score", 0.0)
            combined = semantic * decay
            scored.append({
                **item,
                "decay_score": round(decay, 4),
                "combined_score": round(combined, 4),
                "decay_policy_version": self.policy_version,
            })

        scored.sort(key=lambda x: x["combined_score"], reverse=True)
        return scored

    def policy_version_for_record(self) -> str:
        """Return version tag to stamp onto a MemoryRecord at write time (fixes E-001)."""
        return self.policy_version

    def reload(self) -> None:
        """Re-read config file (e.g., after user edits decay.yaml)."""
        self._policy = self._load_policy()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _load_policy(self) -> dict:
        if not self._config_path.exists():
            return dict(_DEFAULT_POLICY)
        try:
            import yaml
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                return {**_DEFAULT_POLICY, **data}
        except Exception:
            pass
        return dict(_DEFAULT_POLICY)

    def _compute_decay(self, timestamp_str: str, now: datetime) -> float:
        try:
            ts = datetime.fromisoformat(timestamp_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            days = (now - ts).total_seconds() / 86400.0
            days = max(0.0, days)
        except (ValueError, TypeError):
            return 1.0  # unknown timestamp → no decay penalty

        formula = self._policy.get("formula", "inverse_days")

        if formula == "inverse_days":
            return 1.0 / (1.0 + days)
        elif formula == "half_life":
            half_life = float(self._policy.get("half_life_days") or 30)
            return math.pow(0.5, days / half_life)
        elif formula == "linear":
            max_days = float(self._policy.get("max_days") or 365)
            return max(0.0, 1.0 - days / max_days)
        else:
            # Unknown formula — log and default
            return 1.0 / (1.0 + days)
