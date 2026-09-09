"""Offline self-test using the shipped owner ledger and real temporary Git repositories."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import os
import sqlite3
from unittest.mock import patch
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
KIT = ROOT.parents[1]
sys.path.insert(0, str(KIT / "system-snapshot/hermes-agent-self-evolution"))
from prime_bridge.job_ledger import JobLedger
from worker_port import WorkerTransport, TransportError
from worker_port.git_snapshot import snapshot, EvidenceError


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


class WorkerPortTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}))
        self.temp = tempfile.TemporaryDirectory(prefix="worker-port-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        git(self.repo, "init")
        git(self.repo, "config", "user.name", "alice")
        git(self.repo, "config", "user.email", "alice")
        (self.repo / "a.txt").write_text("before\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-m", "fixture")
        route = {"provider": "example", "model": "fixture", "api": "openai-completions",
                 "endpoint": "http://127.0.0.1:11434/v1"}
        route["fingerprint"] = JobLedger.route_fingerprint(route)
        runtime = {"source_commit": "a" * 40, "image_digest": "sha256:" + "b" * 64,
                   "sandbox": "rootless-podman", "command_fingerprint": "c" * 64}
        runtime["fingerprint"] = JobLedger.runtime_fingerprint(runtime)
        self.ledger = JobLedger(self.root / "jobs.sqlite3", [self.root])
        self.job = self.ledger.create(owner="user", profile="workspace", project="fixture",
            session_id="session1", workspace=self.repo, route=route, runtime=runtime,
            limits={"max_iterations": 1, "no_progress_seconds": 30, "wall_seconds": 60})
        self.trees = self.root / "worktrees"
        self.trees.mkdir()
        self.target = {"target_id": "worker-a", "policy_sha256": "d" * 64}
        self.adapter = WorkerTransport(self.ledger, target=self.target, external_source="example-cli")
        self.args = dict(job_id=self.job["job_id"], task_id="task1", goal_id=None,
            contract_sha256="e" * 64, harness="codex", declared_paths=["a.txt", "new.txt"],
            worktree_root=self.trees)

    def ready(self):
        return self.adapter.prepare(**self.args)["receipt"]

    def event(self, receipt):
        return dict(schema_version="1.0", source="example-cli", event_id="event1",
            binding_id=receipt["binding_id"], job_id=self.job["job_id"], harness="codex",
            session_id="session1", external_run_id="run1", sequence=1,
            observed_at=datetime.now(timezone.utc).isoformat(), status="done")

    def test_native_mode_identity(self):
        if os.name != "posix":
            self.skipTest("requires POSIX executable modes")
        from worker_port.git_snapshot import changed_paths
        target = self.repo / "a.txt"
        for filemode in (True, False):
            git(self.repo, "config", "core.filemode", str(filemode).lower())
            target.chmod(0o644)
            before = snapshot(self.repo, ("a.txt",))
            target.chmod(0o755)
            after = snapshot(self.repo, ("a.txt",))
            self.assertEqual(before["identity"] != after["identity"], filemode)
            self.assertEqual("a.txt" in changed_paths(self.repo, before, after), filemode)

    def test_manifest_enumeration_aba(self):
        from worker_port import worker_transport as mod
        receipt = self.ready()
        root = Path(receipt["worktree"])
        target = root / "a.txt"
        clean = target.read_bytes()
        changed = clean + b"captured line\n"
        target.write_bytes(changed)
        original = mod.git
        def transient_clean(root, *args, **kwargs):
            if args[0] == "diff" and "--name-only" in args:
                target.write_bytes(clean)
                try:
                    return original(root, *args, **kwargs)
                finally:
                    target.write_bytes(changed)
            return original(root, *args, **kwargs)
        with patch.object(mod, "git", transient_clean):
            result = self.adapter.collect(receipt["binding_id"])
        self.assertEqual(result["files"]["a.txt"], hashlib.sha256(changed).hexdigest())
        self.assertIn("a.txt", result["file_modes"])

    def test_capability_single_use_and_target_binding(self):
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from dataclasses import replace
        from prime_bridge.capability_policy import CapabilityPolicy, CapabilityDecision
        from prime_bridge.capability_ledger import CapabilityLedger, CapabilityLedgerError
        from prime_bridge.capability_broker import CapabilityBroker, CapabilityBrokerError
        decision = CapabilityDecision("fixture.action", "action", "auto", "fixture", "safe_action")
        policy = CapabilityPolicy(True, {decision.alias: decision}, 900, 4096, 4096)
        ledger = CapabilityLedger(self.root / "cap.sqlite3")
        arguments = policy.canonical_arguments({})
        def create():
            return ledger.create(job_id="a" * 32, profile="home", alias=decision.alias,
                                 decision=decision, arguments=arguments)
        for field in ("server", "tool"):
            record = create()
            class Client:
                async def call(self, *args, **kwargs):
                    raise AssertionError("drifted target dispatched")
            drifted = replace(policy, aliases={decision.alias: replace(decision, **{field: "different"})})
            broker = CapabilityBroker(drifted, ledger, Client())
            with self.assertRaisesRegex(CapabilityBrokerError, "binding"):
                asyncio.run(broker.handle({"job_id": "a" * 32, "profile": "home",
                    "alias": decision.alias, "arguments": {}, "request_id": record["request_id"]}))
        record = create()
        original = ledger.get
        barrier = Barrier(2)
        def overlapping_read(request_id):
            value = original(request_id)
            barrier.wait(timeout=5)
            return value
        def claim(_):
            try:
                ledger.authorize(record["request_id"], profile="home", job_id="a" * 32,
                                 argument_hash=arguments.digest)
                return "claimed"
            except CapabilityLedgerError:
                return "refused"
        with patch.object(ledger, "get", overlapping_read), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))
        self.assertEqual(sorted(results), ["claimed", "refused"])

    def test_real_git_collect_reconnect_preserve(self):
        receipt = self.ready()
        tree = Path(receipt["worktree"])
        (tree / "a.txt").write_bytes(b"after\n")
        result = self.adapter.collect(receipt["binding_id"])
        self.assertEqual(result["files"]["a.txt"], hashlib.sha256(b"after\n").hexdigest())
        self.assertFalse(result["tests_verified"] or result["import_authorized"])
        self.assertEqual(self.adapter.prepare(**self.args)["receipt"], receipt)
        self.adapter.preserve(receipt["binding_id"])
        self.assertEqual((tree / "a.txt").read_text(), "after\n")
        self.assertEqual((self.repo / "a.txt").read_text(), "before\n")

    def test_mode_only_capture_races(self):
        if sys.platform=='win32':self.skipTest('requires POSIX modes')
        from worker_port import worker_transport as mod
        receipt=self.ready();root=Path(receipt['worktree']);target=root/'a.txt'
        git(root,'config','core.filemode','true');real=mod.git
        for window in ('empty_patch','before_capture','after_capture'):
            with self.subTest(window=window):
                target.chmod(0o755 if window=='empty_patch' else 0o644)
                def race(root,*args,**kwargs):
                    if window=='before_capture' and args[0]=='ls-tree':target.chmod(0o755)
                    if '--binary' in args:
                        if window=='empty_patch':target.chmod(0o644)
                        result=real(root,*args,**kwargs)
                        target.chmod(0o755 if window in ('empty_patch','after_capture') else 0o644)
                        return result
                    return real(root,*args,**kwargs)
                with patch.object(mod,'git',race),self.assertRaises(TransportError):
                    self.adapter.collect(receipt['binding_id'])

    def test_mode_only_capture_races(self):
        if sys.platform=='win32':self.skipTest('requires POSIX modes')
        from worker_port import worker_transport as mod
        receipt=self.ready();root=Path(receipt['worktree']);target=root/'a.txt'
        git(root,'config','core.filemode','true');real=mod.git
        for window in ('empty_patch','before_capture','after_capture'):
            with self.subTest(window=window):
                target.chmod(0o755 if window=='empty_patch' else 0o644)
                def race(root,*args,**kwargs):
                    if window=='before_capture' and args[0]=='ls-tree':target.chmod(0o755)
                    if '--binary' in args:
                        if window=='empty_patch':target.chmod(0o644)
                        result=real(root,*args,**kwargs)
                        target.chmod(0o755 if window in ('empty_patch','after_capture') else 0o644)
                        return result
                    return real(root,*args,**kwargs)
                with patch.object(mod,'git',race),self.assertRaises(TransportError):
                    self.adapter.collect(receipt['binding_id'])

    def test_aba_capture_refused(self):
        from worker_port import worker_transport as mod
        receipt=self.ready(); target=Path(receipt["worktree"])/"a.txt"
        target.write_bytes(b"capture A\n"); real=mod.git
        def race(root, *args, **kwargs):
            if "--binary" in args:
                target.write_bytes(b"patch B\n")
                try: return real(root, *args, **kwargs)
                finally: target.write_bytes(b"capture A\n")
            return real(root, *args, **kwargs)
        with patch.object(mod,"git",race), self.assertRaisesRegex(TransportError,"capture"):
            self.adapter.collect(receipt["binding_id"])

    def test_capture_binary_deletion_and_new_file(self):
        receipt=self.ready();root=Path(receipt['worktree'])
        (root/'a.txt').write_bytes(b'\x00\xffbinary\n')
        (root/'new.txt').write_bytes(b'new\n')
        git(root,'add','new.txt')
        result=self.adapter.collect(receipt['binding_id'])
        self.assertEqual(result['files']['a.txt'],hashlib.sha256((root/'a.txt').read_bytes()).hexdigest())
        (root/'a.txt').unlink()
        self.assertIsNone(self.adapter.collect(receipt['binding_id'])['files']['a.txt'])

    def test_reserved_scope_and_legacy_binding(self):
        from worker_port import worker_transport as mod
        for name in (".planning/review-evidence", ".planning/review-evidence/new.txt"):
            with self.assertRaisesRegex(TransportError,"reserved"):
                self.adapter.prepare(**{**self.args,"declared_paths":[name]})
        self.assertEqual(list(self.trees.iterdir()),[])
        receipt={**self.ready(),"declared_paths":[".planning/review-evidence/new.txt"]}
        with patch.object(self.adapter,"_binding",lambda _: (receipt,{})), patch.object(mod,"git",side_effect=AssertionError("Git ran")):
            with self.assertRaisesRegex(TransportError,"reserved"):
                self.adapter.collect(receipt["binding_id"])

    def test_observation_is_not_completion_and_deduplicates(self):
        receipt = self.ready()
        self.adapter.bind_external(receipt["binding_id"], "run1")
        event = self.event(receipt)
        before = self.ledger.get(self.job["job_id"])
        self.assertEqual(self.adapter.observe_external(event)["status"], "recorded")
        self.assertEqual(self.adapter.observe_external(event)["status"], "duplicate")
        self.assertEqual(self.ledger.get(self.job["job_id"]), before)
        with self.assertRaises(TransportError):
            self.adapter.observe_external({**event, "source": "other-cli"})
        with self.assertRaises(TransportError):
            self.adapter.observe_external({**event, "status": "running"})

    def test_binding_tamper_refused(self):
        receipt = self.ready()
        with self.ledger._connect() as db:
            db.execute("UPDATE worker_bindings SET receipt_json=? WHERE binding_id=?",
                (json.dumps({**receipt, "declared_paths": ["outside.txt"]}), receipt["binding_id"]))
        with self.assertRaises(TransportError):
            self.adapter.collect(receipt["binding_id"])

    def test_changed_target_refused(self):
        receipt = self.ready()
        changed = WorkerTransport(self.ledger, target={**self.target, "target_id": "worker-b"},
                                  external_source="example-cli")
        with self.assertRaises(TransportError):
            changed.reconnect(receipt["binding_id"])

    def test_dirty_source_and_scope_escape_refused(self):
        with self.assertRaises(TransportError):
            self.adapter.prepare(**{**self.args, "declared_paths": ["../outside"]})
        (self.repo / "a.txt").write_text("uncommitted")
        with self.assertRaises(TransportError):
            self.ready()
        self.assertEqual((self.repo / "a.txt").read_text(), "uncommitted")

    def test_changed_external_source_requires_reconciliation(self):
        receipt = self.ready()
        changed = WorkerTransport(self.ledger, target=self.target, external_source="other-cli")
        with self.assertRaises(TransportError):
            changed.reconnect(receipt["binding_id"])

    def test_actual_snapshot_is_not_existence_only(self):
        before = snapshot(self.repo, ("a.txt",))
        (self.repo / "a.txt").write_text("changed")
        after = snapshot(self.repo, ("a.txt",))
        self.assertNotEqual(before["identity"], after["identity"])
        with self.assertRaises(EvidenceError):
            snapshot(self.repo, ("../outside",))

    def test_owner_target_cannot_include_commands(self):
        with self.assertRaises(TransportError):
            WorkerTransport(self.ledger, target={**self.target, "command": "launch"},
                            external_source="example-cli")

    def test_all_harness_identifiers_use_same_contract(self):
        for harness in ("hermes", "codex", "claude-code", "moa", "cursor", "dsh", "pi", "opencode", "antigravity"):
            from worker_port.worker_transport import validate
            receipt = self.ready()
            validate("remote-worktree-receipt", {**receipt, "harness": harness})

    def test_filter_refusal_before_worker_or_snapshot(self):
        from worker_port import git_snapshot
        for driver in ("clean", "smudge", "process"):
            with self.subTest(driver=driver):
                git(self.repo, "config", "filter.fixture." + driver, "false")
                with self.assertRaises(TransportError):
                    self.adapter.prepare(**self.args)
                with self.assertRaises(EvidenceError):
                    git_snapshot._git(self.repo, "status", "--porcelain")
                git(self.repo, "config", "--unset", "filter.fixture." + driver)

    def test_host_proof_has_no_unrestricted_fallback(self):
        import importlib
        from types import SimpleNamespace
        sys.path.insert(0, str(KIT))
        sys.path.insert(0, str(KIT / "modules/orchestration-core"))
        proofs = importlib.import_module("lapart_core.review.proofs")
        test = self.repo / "test_acceptance.py"
        test.write_text("def test_acceptance():\n    assert False\n")
        verifier = proofs.HostTestVerifier(self.repo, {"acceptance": proofs.HostCheck(
            test.name, hashlib.sha256(test.read_bytes()).hexdigest(), "Owned acceptance")}, {})
        budget = SimpleNamespace(tool_calls=0, limits={"max_tool_calls": 1}, remaining=lambda: 30)
        with patch.dict(sys.modules, {"lapart_core.evidence_adapter": None}):
            with patch("subprocess.Popen", side_effect=AssertionError("unrestricted launch")):
                with self.assertRaisesRegex(ValueError, "CAPABILITY_REFUSED"):
                    verifier.verify({"blocking_proof": proofs.PROOF,
                                     "evidence": ["check:acceptance"]}, budget)

    def test_collection_race_refuses_or_captures_changed_file(self):
        import worker_port.worker_transport as transport
        receipt = self.adapter.prepare(**self.args)["receipt"]
        root = Path(receipt["worktree"])
        original = transport.git
        changed = False
        def race(repo, *args, **kwargs):
            nonlocal changed
            result = original(repo, *args, **kwargs)
            if "--name-only" in args and not changed:
                changed = True
                (root / "a.txt").write_text("race update\n")
            return result
        with patch.object(transport, "git", race):
            try:
                value = self.adapter.collect(receipt["binding_id"])
            except TransportError:
                return
        self.assertIn("a.txt", value["files"])

    def test_export_fingerprints(self):
        provenance = json.loads((ROOT / "PROVENANCE.json").read_text())
        self.assertTrue(provenance["generated_sha256"])
        for name, expected in provenance["generated_sha256"].items():
            content = (ROOT / name).read_text(encoding="utf-8-sig").rstrip("\n") + "\n"
            self.assertEqual(hashlib.sha256(content.encode()).hexdigest(), expected, name)

    def test_owner_connections_close_and_rollback(self):
        with self.ledger._connect() as db:
            self.assertEqual(db.execute("SELECT 1").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")
        with self.assertRaisesRegex(RuntimeError, "rollback fixture"):
            with self.ledger._connect() as transaction:
                transaction.execute("BEGIN IMMEDIATE")
                transaction.execute("CREATE TABLE rollback_fixture (value INTEGER)")
                raise RuntimeError("rollback fixture")
        with self.ledger._connect() as check:
            self.assertIsNone(check.execute("SELECT name FROM sqlite_master WHERE name='rollback_fixture'").fetchone())


if __name__ == "__main__":
    unittest.main()
