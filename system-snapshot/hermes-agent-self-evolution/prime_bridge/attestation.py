"""Replay-resistant OpenSSH Ed25519 operator and remote-client attestation boundary."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from prime_bridge.jcs import canonicalize, loads_strict


CHALLENGE_KEYS = {
    "schema_version", "run_id", "report_id", "client_id", "endpoint", "key_fingerprint",
    "nonce", "issued_at", "expires_at", "namespace", "principal",
}
PAYLOAD_KEYS = CHALLENGE_KEYS | {"observation"}
OBSERVATION_KEYS = {"status", "http_status", "endpoint", "body_sha256", "observed_at"}


class AttestationError(ValueError):
    pass


def _load_enrollment(path: Path) -> dict[str, Any]:
    value = loads_strict(path.read_bytes())
    if set(value) != {"schema_version", "namespace", "principal", "ttl_seconds", "clock_skew_seconds", "clients"}:
        raise AttestationError("closed enrollment contract violated")
    if value["schema_version"] != "1.0" or value["namespace"] != "hermes-prime-attester-v1" or value["principal"] != "hermes-prime-attester@__PRIVATE_TOKEN_02__":
        raise AttestationError("enrollment constants mismatch")
    if not isinstance(value["ttl_seconds"], int) or not 30 <= value["ttl_seconds"] <= 900:
        raise AttestationError("invalid challenge TTL")
    if not isinstance(value["clock_skew_seconds"], int) or not 0 <= value["clock_skew_seconds"] <= 120:
        raise AttestationError("invalid clock skew")
    return value


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None, timeout=10)
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS nonces (nonce_hash TEXT PRIMARY KEY, run_id TEXT NOT NULL, report_id TEXT NOT NULL, client_id TEXT NOT NULL, endpoint TEXT NOT NULL, fingerprint TEXT NOT NULL, issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER, payload_sha256 TEXT, signature_sha256 TEXT, observation_status TEXT)"
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(nonces)")}
    for name in ("payload_sha256", "signature_sha256", "observation_status"):
        if name not in columns:
            connection.execute(f"ALTER TABLE nonces ADD COLUMN {name} TEXT")
    os.chmod(path, 0o600)
    return connection


def _key_status(client: dict[str, Any] | None, endpoint: str, presented_fingerprint: str | None) -> str:
    if client is None or client.get("public_key") is None or client.get("fingerprint") is None:
        return "MISSING"
    if client.get("endpoint") != endpoint or presented_fingerprint != client.get("fingerprint"):
        return "MISMATCH"
    return "ENROLLED"


def begin_run(
    enrollment_path: Path,
    store_path: Path,
    client_id: str,
    endpoint: str,
    report_id: str,
    prerequisites_ready: bool,
    presented_fingerprint: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    enrollment = _load_enrollment(enrollment_path)
    observed_at = int(time.time() if now is None else now)
    if len(report_id) != 32 or any(char not in "0123456789abcdef" for char in report_id):
        raise AttestationError("report_id must be 32 lowercase hexadecimal characters")
    run_id = uuid.uuid4().hex
    client = enrollment["clients"].get(client_id)
    status = _key_status(client, endpoint, presented_fingerprint)
    unsigned = {
        "schema_version": "1.0", "run_id": run_id, "report_id": report_id, "client_id": client_id,
        "endpoint": endpoint, "key_status": status, "observed_at": observed_at, "status": "UNVERIFIED",
        "go_eligible": False, "challenge_issued": False,
        "reason": "SIGNING_PREREQUISITE_UNAVAILABLE" if not prerequisites_ready else ("KEY_MISSING" if status == "MISSING" else "KEY_MISMATCH"),
    }
    if not prerequisites_ready or status != "ENROLLED":
        return {"unsigned": unsigned, "challenge": None}
    unsigned.update({"status": "PENDING", "challenge_issued": True, "reason": "CHALLENGE_ISSUED"})
    nonce = secrets.token_bytes(32)
    issued_at = observed_at
    expires_at = issued_at + enrollment["ttl_seconds"]
    challenge = {
        "schema_version": "1.0", "run_id": run_id, "report_id": report_id, "client_id": client_id,
        "endpoint": endpoint, "key_fingerprint": presented_fingerprint,
        "nonce": base64.urlsafe_b64encode(nonce).decode("ascii").rstrip("="),
        "issued_at": issued_at, "expires_at": expires_at,
        "namespace": enrollment["namespace"], "principal": enrollment["principal"],
    }
    with _connect(store_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO nonces (nonce_hash,run_id,report_id,client_id,endpoint,fingerprint,issued_at,expires_at,consumed_at,payload_sha256,signature_sha256,observation_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
            (hashlib.sha256(nonce).hexdigest(), run_id, report_id, client_id, endpoint, presented_fingerprint, issued_at, expires_at),
        )
        connection.commit()
    return {"unsigned": unsigned, "challenge": challenge}


def prepare_payload(challenge: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    if set(challenge) != CHALLENGE_KEYS or set(observation) != OBSERVATION_KEYS:
        raise AttestationError("closed challenge or observation contract violated")
    if observation["status"] not in {"PASS", "FAIL"} or observation["endpoint"] != challenge["endpoint"]:
        raise AttestationError("observation binding mismatch")
    if (
        not isinstance(observation["body_sha256"], str)
        or len(observation["body_sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in observation["body_sha256"])
    ):
        raise AttestationError("invalid observation body hash")
    return {**challenge, "observation": observation}


def _fingerprint(public_key: str) -> str:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "key.pub"
        path.write_text(public_key.rstrip() + "\n", encoding="utf-8")
        completed = subprocess.run(["/usr/bin/ssh-keygen", "-lf", str(path), "-E", "sha256"], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AttestationError("invalid enrolled public key")
    return completed.stdout.split()[1]


def verify_run(
    enrollment_path: Path,
    store_path: Path,
    payload: dict[str, Any],
    signature: bytes,
    now: int | None = None,
) -> dict[str, Any]:
    if set(payload) != PAYLOAD_KEYS or set(payload["observation"]) != OBSERVATION_KEYS:
        raise AttestationError("closed signed payload contract violated")
    enrollment = _load_enrollment(enrollment_path)
    client = enrollment["clients"].get(payload["client_id"])
    if client is None or _key_status(client, payload["endpoint"], payload["key_fingerprint"]) != "ENROLLED":
        raise AttestationError("key is not exactly enrolled")
    if payload["namespace"] != enrollment["namespace"] or payload["principal"] != enrollment["principal"]:
        raise AttestationError("OpenSSH signing identity mismatch")
    if _fingerprint(client["public_key"]) != client["fingerprint"]:
        raise AttestationError("enrolled key fingerprint mismatch")
    observation = payload["observation"]
    if observation["endpoint"] != payload["endpoint"]:
        raise AttestationError("observation endpoint mismatch")
    current = int(time.time() if now is None else now)
    skew = enrollment["clock_skew_seconds"]
    if payload["issued_at"] > current + skew or current > payload["expires_at"] + skew:
        raise AttestationError("challenge is stale or future")
    if not payload["issued_at"] - skew <= observation["observed_at"] <= payload["expires_at"] + skew:
        raise AttestationError("observation time is outside challenge window")
    try:
        nonce = base64.urlsafe_b64decode(payload["nonce"] + "=")
    except Exception as error:
        raise AttestationError("invalid nonce encoding") from error
    if len(nonce) != 32:
        raise AttestationError("nonce must be 32 bytes")
    canonical = canonicalize(payload)
    with _connect(store_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT run_id,report_id,client_id,endpoint,fingerprint,issued_at,expires_at,consumed_at FROM nonces WHERE nonce_hash=?",
            (hashlib.sha256(nonce).hexdigest(),),
        ).fetchone()
        expected = (payload["run_id"], payload["report_id"], payload["client_id"], payload["endpoint"], payload["key_fingerprint"], payload["issued_at"], payload["expires_at"])
        if row is None or tuple(row[:7]) != expected or row[7] is not None:
            connection.rollback()
            raise AttestationError("nonce missing, rebound, or already consumed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed_signers"
            sig_path = root / "payload.sig"
            allowed.write_text(f"{enrollment['principal']} {client['public_key'].strip()}\n", encoding="utf-8")
            sig_path.write_bytes(signature)
            os.chmod(allowed, 0o600)
            os.chmod(sig_path, 0o600)
            completed = subprocess.run(
                ["/usr/bin/ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I", enrollment["principal"], "-n", enrollment["namespace"], "-s", str(sig_path)],
                input=canonical, capture_output=True, check=False,
            )
        if completed.returncode != 0:
            connection.rollback()
            raise AttestationError("OpenSSH signature verification failed")
        updated = connection.execute(
            "UPDATE nonces SET consumed_at=?,payload_sha256=?,signature_sha256=?,observation_status=? WHERE nonce_hash=? AND consumed_at IS NULL",
            (
                current, hashlib.sha256(canonical).hexdigest(), hashlib.sha256(signature).hexdigest(),
                observation["status"], hashlib.sha256(nonce).hexdigest(),
            ),
        )
        if updated.rowcount != 1:
            connection.rollback()
            raise AttestationError("atomic nonce consume failed")
        connection.commit()
    passed = observation["status"] == "PASS"
    return {
        "schema_version": "1.0", "run_id": payload["run_id"], "report_id": payload["report_id"],
        "client_id": payload["client_id"], "endpoint": payload["endpoint"], "key_status": "ENROLLED",
        "observed_at": observation["observed_at"], "status": "READY" if passed else "FAIL",
        "go_eligible": passed, "challenge_issued": True, "key_fingerprint": payload["key_fingerprint"],
        "namespace": payload["namespace"], "principal": payload["principal"],
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature_base64": base64.b64encode(signature).decode("ascii"), "observation": observation,
    }


def consumed_evidence_matches(store_path: Path, evidence: dict[str, Any]) -> bool:
    """Bind a persisted receipt back to the exact atomically consumed ledger row."""

    required = {
        "run_id", "report_id", "client_id", "endpoint", "key_fingerprint", "payload_sha256",
        "signature_base64", "status", "go_eligible", "observation",
    }
    if not required.issubset(evidence) or not store_path.is_file():
        return False
    try:
        signature = base64.b64decode(evidence["signature_base64"], validate=True)
        expected_observation = "PASS" if evidence["status"] == "READY" else "FAIL"
        if evidence["observation"]["status"] != expected_observation:
            return False
        with _connect(store_path) as connection:
            rows = connection.execute(
                "SELECT payload_sha256,signature_sha256,observation_status,consumed_at FROM nonces WHERE run_id=? AND report_id=? AND client_id=? AND endpoint=? AND fingerprint=?",
                (
                    evidence["run_id"], evidence["report_id"], evidence["client_id"],
                    evidence["endpoint"], evidence["key_fingerprint"],
                ),
            ).fetchall()
    except (KeyError, TypeError, ValueError, sqlite3.Error, OSError):
        return False
    if len(rows) != 1:
        return False
    payload_hash, signature_hash, observation_status, consumed_at = rows[0]
    return (
        consumed_at is not None
        and payload_hash == evidence["payload_sha256"]
        and signature_hash == hashlib.sha256(signature).hexdigest()
        and observation_status == expected_observation
    )


def _main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    begin = sub.add_parser("begin-run")
    for name in ("enrollment", "store", "client-id", "endpoint", "report-id"):
        begin.add_argument(f"--{name}", required=True)
    begin.add_argument("--presented-fingerprint")
    begin.add_argument("--prerequisites-ready", action="store_true")
    prepare = sub.add_parser("prepare-payload")
    prepare.add_argument("--challenge", required=True); prepare.add_argument("--observation", required=True)
    verify = sub.add_parser("verify-run")
    for name in ("enrollment", "store", "payload", "signature"):
        verify.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    try:
        if args.command == "begin-run":
            result = begin_run(Path(args.enrollment), Path(args.store), args.client_id, args.endpoint, args.report_id, args.prerequisites_ready, args.presented_fingerprint)
        elif args.command == "prepare-payload":
            result = prepare_payload(loads_strict(Path(args.challenge).read_bytes()), loads_strict(Path(args.observation).read_bytes()))
        else:
            result = verify_run(Path(args.enrollment), Path(args.store), loads_strict(Path(args.payload).read_bytes()), Path(args.signature).read_bytes())
    except (AttestationError, OSError, sqlite3.Error) as error:
        print(f"prime attestation: FAIL: {error}", file=os.sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
