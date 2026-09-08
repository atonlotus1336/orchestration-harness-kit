#!/usr/bin/env python3
"""Synthetic negative controls for the release evidence gate; no provider calls."""
import copy
import importlib.util
from pathlib import Path
import unittest
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_gate", ROOT / "tools/verify-release-readiness.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def receipts():
    checks = [{"name": name, "status": "PASS"} for name in ("source-snapshot", "v1-ancestry", "source-verifiers", "reproducible-payload", "reproducible-bundle", "install-doctor-verify", "all-harness-connect", "v1-upgrade-rollback", "managed-uninstall")]
    checks[2]["count"] = 6
    checks[3]["files"] = 10
    checks[6].update(count=8, harnesses=list(gate._qualifier().HARNESSES))
    base = dict(schema="lapart.clean-room-platform.v2", decision="CLEAN_ROOM_PASS", source_mode="working-tree", source_tree_sha256="a"*64, source_commit="b"*40, v1_commit=gate._qualifier().V1_COMMIT, checks=checks, artifacts=dict(payload_files=10, payload_tree_sha256="c"*64, bundle_sha256="d"*64, release_manifest_sha256="e"*64, qualifier_sha256="f"*64), provider_calls=0, provider_tokens=0, service_restarts=0, pay_per_token=False, live_activation="unchanged")
    pair = [copy.deepcopy(base), copy.deepcopy(base)]
    pair[0]["runtime"] = {"system": "Windows"}
    pair[1]["runtime"] = {"system": "Linux"}
    return pair


class ReleaseGateTests(unittest.TestCase):
    def check_pair(self, pair, committed=False):
        return gate.validate_pair(pair, "a"*64, "b"*40, "f"*64, committed)

    def test_valid_snapshot(self):
        self.assertEqual(self.check_pair(receipts()), [])

    def test_snapshot_cannot_publish(self):
        self.assertIn("publication requires committed-source qualification", self.check_pair(receipts(), True))

    def test_committed_source(self):
        pair = receipts()
        for item in pair:
            item["source_mode"] = "committed"
            item["checks"][0]["name"] = "clean-local-clone"
        self.assertEqual(self.check_pair(pair, True), [])

    def test_reject_mutated_evidence(self):
        mutations = {
            "stale source": lambda p: p[0].update(source_tree_sha256="0"*64),
            "wrong commit": lambda p: p[0].update(source_commit="0"*40),
            "missing Cursor": lambda p: p[0]["checks"][6]["harnesses"].remove("cursor"),
            "failed check": lambda p: p[0]["checks"][1].update(status="FAIL"),
            "missing check": lambda p: p[0]["checks"].pop(),
            "duplicate platform": lambda p: p[0]["runtime"].update(system="Linux"),
            "different bundle": lambda p: p[0]["artifacts"].update(bundle_sha256="1"*64),
            "bad hash": lambda p: p[0]["artifacts"].update(bundle_sha256=""),
            "empty payload": lambda p: p[0]["artifacts"].update(payload_files=0),
            "provider use": lambda p: p[0].update(provider_calls=1),
            "activation": lambda p: p[0].update(live_activation="active"),
            "old receipt": lambda p: p[0].update(schema="lapart.clean-room-platform.v1"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                pair = receipts(); mutate(pair)
                self.assertTrue(self.check_pair(pair))

    def test_checkout_line_endings_and_source_drift(self):
        with tempfile.TemporaryDirectory(prefix="release-fingerprint-") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitattributes").write_text("*.txt text eol=lf\n", encoding="utf-8")
            path = root / "sample.txt"
            path.write_bytes(b"same\r\n")
            before = gate._qualifier().source_digest(root)
            path.write_bytes(b"same\n")
            self.assertEqual(before, gate._qualifier().source_digest(root))
            path.write_bytes(b"changed\n")
            self.assertNotEqual(before, gate._qualifier().source_digest(root))

    def test_no_evidence(self):
        self.assertTrue(self.check_pair([]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
