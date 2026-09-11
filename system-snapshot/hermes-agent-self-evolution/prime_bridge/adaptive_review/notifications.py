"""Truthful completion notifications and confirmation-gated S4 transitions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Mapping


class S4Action(str, Enum):
    RUN_NOW = "Run now"
    LATER = "Later"
    SKIP = "Skip"


class NotificationState(str, Enum):
    REVIEW_READY = "review_ready"
    S4_CONFIRMATION_REQUIRED = "S4_confirmation_required"
    USER_CONFIRMED = "user_confirmed"
    DEFERRED = "deferred"
    WAIVED = "waived"
    REMEDIATION_SELECTED = "remediation_selected"


class StateTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompletionNotification:
    execution_status: str
    review_pipeline: str
    review_verdict: str
    finding_counts: Mapping[str, int]
    git_status: Mapping[str, Any]
    receipt_path: str
    commit_status: str
    state: NotificationState

    def __post_init__(self) -> None:
        for name in (
            "execution_status", "review_pipeline", "review_verdict",
            "finding_counts", "git_status", "receipt_path", "commit_status", "state",
        ):
            if getattr(self, name) is None:
                raise ValueError(f"missing notification field: {name}")

    def to_json(self) -> str:
        value = asdict(self)
        value["state"] = self.state.value
        return json.dumps(redact(value), sort_keys=True, indent=2)


def apply_s4_action(notification: CompletionNotification, action: S4Action) -> CompletionNotification:
    if notification.state is not NotificationState.S4_CONFIRMATION_REQUIRED:
        raise StateTransitionError("S4 action requires S4_confirmation_required state")
    target = {
        S4Action.RUN_NOW: NotificationState.USER_CONFIRMED,
        S4Action.LATER: NotificationState.DEFERRED,
        S4Action.SKIP: NotificationState.WAIVED,
    }[action]
    return replace(notification, state=target)


SENSITIVE_KEYS = ("token", "secret", "api_key", "apikey", "password", "credential", "private_key")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key in sorted(value):
            result[key] = "<redacted>" if any(marker in key.lower() for marker in SENSITIVE_KEYS) else redact(value[key])
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value
