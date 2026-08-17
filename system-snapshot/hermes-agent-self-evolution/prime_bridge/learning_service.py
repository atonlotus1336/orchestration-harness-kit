"""Host-owned boundary for Prime-local observations and promotion proposals."""

from __future__ import annotations

from typing import Any

from prime_bridge.job_ledger import JobLedger
from prime_bridge.learning_ledger import LearningLedger, LearningLedgerError


class LearningServiceError(RuntimeError):
    pass


class LearningService:
    def __init__(self, learning: LearningLedger, jobs: JobLedger):
        self.learning = learning
        self.jobs = jobs

    async def call(self, tool: str, arguments: dict[str, Any], context: dict[str, str]) -> dict[str, Any]:
        job = self.jobs.get(context["job_id"])
        if job["profile"] != context["profile"]:
            raise LearningServiceError("learning context does not own the Prime job")
        try:
            if tool == "record":
                allowed = {"scope", "subject", "content", "privacy", "evidence", "expires_at"}
                if set(arguments) - allowed:
                    raise LearningServiceError("unknown local learning field")
                record = self.learning.record(
                    profile=job["profile"], project=job["project"], session_id=job["session_id"],
                    scope=arguments["scope"], subject=arguments["subject"],
                    content=arguments["content"], privacy=arguments.get("privacy", "internal"),
                    provenance={
                        "kind": "prime-job", "job_id": job["job_id"],
                        "prime_session_id": job.get("prime_session_id"),
                        "source_commit": job["runtime"]["source_commit"],
                    },
                    evidence=arguments["evidence"], expires_at=arguments.get("expires_at"),
                )
                return {key: record[key] for key in (
                    "record_id", "scope", "subject", "content_hash", "privacy", "status",
                )}
            if tool == "propose":
                allowed = {"record_id", "lane", "target_scope", "authority_rank", "expires_at"}
                if set(arguments) - allowed:
                    raise LearningServiceError("unknown promotion proposal field")
                record = self.learning.get_record(arguments["record_id"])
                if record["profile"] != job["profile"] or record["project"] != job["project"]:
                    raise LearningServiceError("learning record is outside the active job scope")
                proposal = self.learning.propose(
                    record["record_id"], profile=job["profile"], lane=arguments["lane"],
                    target_scope=arguments["target_scope"],
                    authority_rank=arguments.get("authority_rank", 10),
                    expires_at=arguments.get("expires_at"),
                )
                return {key: proposal[key] for key in (
                    "proposal_id", "record_id", "lane", "target_scope", "status",
                    "dedupe_key", "expires_at",
                )}
            raise LearningServiceError("unknown Prime learning operation")
        except (KeyError, LearningLedgerError) as exc:
            raise LearningServiceError(str(exc)) from exc
