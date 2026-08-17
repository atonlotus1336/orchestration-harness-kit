from __future__ import annotations

import importlib.metadata
import json
import math
import struct
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from prime_bridge.jcs import CanonicalizationError, canonicalize, canonicalize_json, loads_strict
from prime_bridge.attestation import AttestationError, begin_run, consumed_evidence_matches, prepare_payload, verify_run


ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads(
    (ROOT / "tests" / "fixtures" / "jcs" / "rfc8785-official-vectors.json").read_text(encoding="utf-8")
)


def test_locked_single_implementation_and_official_source_metadata() -> None:
    assert importlib.metadata.version("rfc8785") == "0.1.4"
    assert "/.git/phase01-gap-lifecycle/tools/" in sys.executable
    assert VECTORS["source"] == {
        "document": "RFC 8785",
        "revision": "June 2020",
        "url": "https://www.rfc-editor.org/rfc/rfc8785.txt",
        "sha256": "63d52294eb0e3f0014174288186d388b4ddbf2c67d1ce8af1d9726eb0c3ab240",
        "sections": ["3.2.2", "3.2.3", "Appendix B"],
    }
    source = (ROOT / "prime_bridge" / "jcs.py").read_text(encoding="utf-8")
    assert source.count("rfc8785.dumps") == 1
    assert "json.dumps(value" not in source


@pytest.mark.parametrize("vector", VECTORS["document_vectors"], ids=lambda item: item["name"])
def test_official_document_vectors(vector: dict[str, str]) -> None:
    assert canonicalize_json(vector["input"]).decode("utf-8") == vector["canonical"]


@pytest.mark.parametrize(("bits", "expected"), VECTORS["ieee754_vectors"])
def test_official_appendix_b_number_vectors(bits: str, expected: str) -> None:
    number = struct.unpack(">d", bytes.fromhex(bits))[0]
    assert canonicalize(number).decode("ascii") == expected


@pytest.mark.parametrize(
    "invalid",
    [
        '{"a":1,"a":2}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":9007199254740992}',
        '"\\ud800"',
        '{"\\udfff":true}',
    ],
)
def test_strict_parser_rejects_ambiguous_or_non_ijson_input(invalid: str) -> None:
    with pytest.raises(CanonicalizationError):
        loads_strict(invalid)


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, 9007199254740992, "\ud800"])
def test_programmatic_input_rejects_nonfinite_unsafe_or_surrogate_values(invalid: object) -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize(invalid)


def test_cli_emits_identical_canonical_bytes() -> None:
    raw = b'{"z":0,"a":{"b":2,"a":1}}'
    completed = subprocess.run(
        [sys.executable, "-m", "prime_bridge.jcs"],
        cwd=ROOT,
        input=raw,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == canonicalize_json(raw)


def make_key_and_enrollment(tmp_path: Path) -> tuple[Path, Path, str]:
    private = tmp_path / "desktop_ed25519"
    completed = subprocess.run(
        ["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private)],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    private.chmod(0o600)
    public = private.with_suffix(".pub")
    fingerprint = subprocess.run(
        ["/usr/bin/ssh-keygen", "-lf", str(public), "-E", "sha256"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.split()[1]
    enrollment = {
        "schema_version": "1.0",
        "namespace": "hermes-prime-attester-v1",
        "principal": "hermes-prime-attester@__PRIVATE_TOKEN_02__",
        "ttl_seconds": 300,
        "clock_skew_seconds": 30,
        "clients": {
            "DESKTOP-2S7VQDO": {
                "endpoint": "http://__TAILSCALE_IPV4__:8642",
                "public_key": public.read_text(encoding="utf-8").strip(),
                "fingerprint": fingerprint,
            }
        },
    }
    path = tmp_path / "attesters.json"
    path.write_text(json.dumps(enrollment), encoding="utf-8")
    return private, path, fingerprint


def sign_payload(private: Path, payload: dict[str, object], tmp_path: Path, suffix: str = "") -> bytes:
    canonical = tmp_path / f"payload{suffix}.bin"
    canonical.write_bytes(canonicalize(payload))
    completed = subprocess.run(
        ["/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(private), "-n", "hermes-prime-attester-v1", str(canonical)],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return Path(str(canonical) + ".sig").read_bytes()


def enrolled_begin(tmp_path: Path, now: int = 1_000_000) -> tuple[Path, Path, dict[str, object], Path]:
    private, enrollment, fingerprint = make_key_and_enrollment(tmp_path)
    store = tmp_path / "nonces.sqlite3"
    result = begin_run(
        enrollment, store, "DESKTOP-2S7VQDO", "http://__TAILSCALE_IPV4__:8642",
        uuid.uuid4().hex, True, fingerprint, now,
    )
    assert result["unsigned"]["key_status"] == "ENROLLED"
    assert result["challenge"] is not None
    assert len(result["challenge"]["nonce"]) == 43
    assert store.stat().st_mode & 0o777 == 0o600
    return private, enrollment, result["challenge"], store


def observation(challenge: dict[str, object], status: str = "PASS") -> dict[str, object]:
    return {
        "status": status,
        "http_status": 200 if status == "PASS" else 503,
        "endpoint": challenge["endpoint"],
        "body_sha256": "a" * 64,
        "observed_at": challenge["issued_at"] + 1,
    }


def test_missing_or_unavailable_signer_closes_without_challenge(tmp_path: Path) -> None:
    schema = json.loads((ROOT / "contracts" / "remote-client-evidence.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    missing = begin_run(
        ROOT / "contracts" / "prime-desktop-attesters.json", tmp_path / "missing.sqlite3",
        "DESKTOP-2S7VQDO", "http://__TAILSCALE_IPV4__:8642", uuid.uuid4().hex, True,
    )
    assert missing["challenge"] is None
    assert missing["unsigned"]["key_status"] == "MISSING"
    assert missing["unsigned"]["reason"] == "KEY_MISSING"
    validator.validate(missing["unsigned"])
    assert not (tmp_path / "missing.sqlite3").exists()

    _, enrolled, fingerprint = make_key_and_enrollment(tmp_path)
    unavailable = begin_run(
        enrolled, tmp_path / "unavailable.sqlite3", "DESKTOP-2S7VQDO",
        "http://__TAILSCALE_IPV4__:8642", uuid.uuid4().hex, False, fingerprint,
    )
    assert unavailable["challenge"] is None
    assert unavailable["unsigned"]["key_status"] == "ENROLLED"
    assert unavailable["unsigned"]["reason"] == "SIGNING_PREREQUISITE_UNAVAILABLE"
    validator.validate(unavailable["unsigned"])


def test_repository_enrolls_nucbox_operator_without_enrolling_deferred_desktop() -> None:
    enrollment = json.loads((ROOT / "contracts" / "prime-desktop-attesters.json").read_text(encoding="utf-8"))
    operator = enrollment["clients"]["NUCBOX-MINI-ULTRA"]
    desktop = enrollment["clients"]["DESKTOP-2S7VQDO"]
    assert enrollment["namespace"] == "hermes-prime-attester-v1"
    assert enrollment["principal"] == "hermes-prime-attester@__PRIVATE_TOKEN_02__"
    assert operator["endpoint"] == "http://127.0.0.1:8642"
    assert operator["public_key"].startswith("ssh-ed25519 ")
    assert operator["fingerprint"] == "SHA256:gDHP9I9jTVRHtITnBSX5v4UfR68HO7CO6ZbpXtjRkDM"
    assert desktop["public_key"] is None and desktop["fingerprint"] is None


def test_mismatched_fingerprint_or_endpoint_issues_no_challenge(tmp_path: Path) -> None:
    _, enrollment, fingerprint = make_key_and_enrollment(tmp_path)
    for endpoint, presented in [
        ("http://__TAILSCALE_IPV4__:8642", "SHA256:" + "A" * 43),
        ("http://127.0.0.1:8642", fingerprint),
    ]:
        result = begin_run(enrollment, tmp_path / "mismatch.sqlite3", "DESKTOP-2S7VQDO", endpoint, uuid.uuid4().hex, True, presented)
        assert result["challenge"] is None
        assert result["unsigned"]["key_status"] == "MISMATCH"


def test_valid_signature_consumes_nonce_once_and_matches_schema(tmp_path: Path) -> None:
    private, enrollment, challenge, store = enrolled_begin(tmp_path)
    payload = prepare_payload(challenge, observation(challenge))
    signature = sign_payload(private, payload, tmp_path)
    evidence = verify_run(enrollment, store, payload, signature, now=challenge["issued_at"] + 2)
    schema = json.loads((ROOT / "contracts" / "remote-client-evidence.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(evidence)
    assert evidence["status"] == "READY" and evidence["go_eligible"] is True
    assert consumed_evidence_matches(store, evidence)
    forged = dict(evidence)
    forged["payload_sha256"] = "b" * 64
    assert not consumed_evidence_matches(store, forged)
    with pytest.raises(AttestationError, match="consumed"):
        verify_run(enrollment, store, payload, signature, now=challenge["issued_at"] + 3)


def test_validly_signed_fail_is_authenticated_but_not_go_eligible(tmp_path: Path) -> None:
    private, enrollment, challenge, store = enrolled_begin(tmp_path)
    payload = prepare_payload(challenge, observation(challenge, "FAIL"))
    evidence = verify_run(enrollment, store, payload, sign_payload(private, payload, tmp_path), now=challenge["issued_at"] + 2)
    assert evidence["status"] == "FAIL"
    assert evidence["go_eligible"] is False


def test_wrong_binding_stale_time_extra_fields_and_signature_fail_closed(tmp_path: Path) -> None:
    private, enrollment, challenge, store = enrolled_begin(tmp_path)
    payload = prepare_payload(challenge, observation(challenge))
    signature = sign_payload(private, payload, tmp_path)
    rebound = dict(payload)
    rebound["report_id"] = uuid.uuid4().hex
    with pytest.raises(AttestationError, match="rebound"):
        verify_run(enrollment, store, rebound, sign_payload(private, rebound, tmp_path, "-rebound"), now=challenge["issued_at"] + 2)
    extra = dict(payload)
    extra["unexpected"] = True
    with pytest.raises(AttestationError, match="closed"):
        verify_run(enrollment, store, extra, signature, now=challenge["issued_at"] + 2)
    with pytest.raises(AttestationError, match="stale"):
        verify_run(enrollment, store, payload, signature, now=challenge["expires_at"] + 31)
    with pytest.raises(AttestationError, match="signature"):
        verify_run(enrollment, store, payload, b"not a signature", now=challenge["issued_at"] + 2)


def test_windows_collector_uses_wsl_canonicalizer_and_keeps_private_key_local() -> None:
    source = (ROOT / "scripts" / "capture-prime-desktop-attestation.ps1").read_text(encoding="utf-8")
    assert "-m prime_bridge.jcs" in source
    assert "ssh-keygen.exe -Y sign" in source
    assert "-n hermes-prime-attester-v1" in source
    assert "--signature $signatureWsl" in source
    assert "Copy-Item" not in source and "Set-Content -LiteralPath $PrivateKeyPath" not in source
