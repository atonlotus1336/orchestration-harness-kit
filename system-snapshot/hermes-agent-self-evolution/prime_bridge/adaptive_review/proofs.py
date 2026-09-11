"""Independent host-owned acceptance checks. Model text never becomes a command."""
from dataclasses import dataclass
from pathlib import Path
import hashlib, os, subprocess, sys, tempfile
import xml.etree.ElementTree as ET

PROOF = "reproducible_failed_test_tied_to_acceptance"

@dataclass(frozen=True)
class HostCheck:
    test_path: str
    sha256: str
    description: str

class HostTestVerifier:
    def __init__(self, workspace, checks, source_hashes, *, required_capabilities=(), source_presence=None):
        self.workspace = Path(workspace).resolve()
        self.checks = dict(checks)
        self.source_hashes = dict(source_hashes)
        if not isinstance(required_capabilities, (list, tuple)) or any(not isinstance(v, str) for v in required_capabilities):
            raise ValueError('invalid required capabilities')
        self.required_capabilities = tuple(required_capabilities)
        self.source_presence = dict(source_presence) if source_presence is not None else {p:True for p in source_hashes}
        if set(self.source_presence) != set(source_hashes) or any(type(v) is not bool for v in self.source_presence.values()):
            raise ValueError('invalid source presence')
        self.receipts = []
        self._cache = {}

    def _path(self, relative):
        from .adapters import _safe_path, CapabilityViolation
        try:
            safe = _safe_path(relative)
        except CapabilityViolation as exc:
            raise ValueError('unsafe host check path') from exc
        path = (self.workspace / safe).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError("verification path escapes workspace")
        return path

    def _identity(self):
        output = {}
        for p in self.source_hashes:
            target = self._path(p)
            if target.exists() != self.source_presence[p]:
                raise ValueError('verification source presence changed')
            output[p] = hashlib.sha256(target.read_bytes() if self.source_presence[p] else b'').hexdigest()
        return output

    def preflight(self, expected):
        if dict(expected) != self.source_hashes or self._identity() != self.source_hashes:
            raise ValueError("verification source identity mismatch")
        for key, check in self.checks.items():
            if not key or type(check) is not HostCheck:
                raise ValueError("invalid host check")
            if hashlib.sha256(self._path(check.test_path).read_bytes()).hexdigest() != check.sha256:
                raise ValueError("host acceptance check changed")

    def begin(self, expected):
        self.preflight(expected)
        self.receipts.clear()
        self._cache.clear()

    def descriptions(self):
        return [{"id":key,"acceptance":check.description} for key,check in sorted(self.checks.items())]

    def verify(self, finding, budget):
        if finding.get("blocking_proof") != PROOF:
            return False
        refs = [v[6:] for v in finding.get("evidence",[]) if v.startswith("check:")]
        if len(refs) != 1 or refs[0] not in self.checks:
            return False
        key = refs[0]
        self.preflight(self.source_hashes)
        if key in self._cache:
            return self._cache[key]
        budget.remaining()
        if budget.tool_calls >= budget.limits["max_tool_calls"]:
            raise ValueError("host verification tool budget exhausted")
        budget.tool_calls += 1
        check = self.checks[key]
        path = self._path(check.test_path)
        receipt = {"check_id":key,"test_sha256":check.sha256,
                   "source_hashes":self.source_hashes,"verified":False}
        with tempfile.TemporaryDirectory(prefix="review-proof-") as folder:
            xml = Path(folder)/"result.xml"
            config = Path(folder)/"pytest.ini"
            config.write_text("[pytest]\n")
            try:
                from ..evidence_adapter import run_host_acceptance
            except ImportError as exc:
                receipt["reason"] = "CAPABILITY_REFUSED: host evidence executor unavailable"
                self.receipts.append(receipt)
                raise ValueError(receipt["reason"]) from exc
            requirements = {'required_capabilities': self.required_capabilities} if self.required_capabilities else {}
            execution = run_host_acceptance(self.workspace, path, xml, config, budget.remaining(), **requirements)
            receipt["execution"] = execution
            if execution.get("status") != "ok":
                receipt["reason"] = "host verification " + str(execution.get("status", "CAPABILITY_REFUSED"))
                self.receipts.append(receipt)
                raise ValueError(receipt["reason"])
            code = execution["exit_code"]
            self.preflight(self.source_hashes)
            budget.remaining()
            if xml.exists() and xml.stat().st_size <= 2_000_000:
                try:
                    tree=ET.parse(xml).getroot()
                except ET.ParseError as exc:
                    raise ValueError("invalid host verification report") from exc
                tests=list(tree.iter("testcase"))
                failures=list(tree.iter("failure"))
                errors=list(tree.iter("error"))
                # Exceptions, collection errors and empty suites are not acceptance proof.
                assertions=all(f.get("message","").startswith(("assert ","AssertionError")) for f in failures)
                skipped=list(tree.iter("skipped"))
                receipt.update(tests=len(tests),failures=len(failures),errors=len(errors),skipped=len(skipped),exit_code=code)
                receipt['passed']=bool(code==0 and tests and not failures and not errors and not skipped)
                receipt["verified"]=bool(code==1 and tests and failures and not errors and assertions)
            self.receipts.append(receipt)
            self._cache[key]=receipt["verified"]
            return receipt["verified"]
