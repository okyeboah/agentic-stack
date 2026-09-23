#!/usr/bin/env python3
"""Pipeline routing must prefer escalation over a confident wrong route.

'unknown' never counts as a routable answer even at high confidence —
routing to 'unknown' IS escalation. Confidence below threshold escalates
to the full-log playbook regardless of label.
"""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_pipeline_route as pr  # noqa: E402


def install_fake(choice, confidence):
    fake = types.ModuleType("laya_client")

    def ask(requests, **_):
        return [{"answers": {"cause": {"probabilities": {choice: confidence}}}}]

    fake.ask = ask
    sys.modules["laya_client"] = fake


class RouteTest(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop("laya_client", None)

    def test_infra_routes_with_playbook(self):
        install_fake("infra", 0.9)
        r = pr.route("no space left on device")
        self.assertEqual(r["label"], "infra")
        self.assertTrue(r["act"])
        self.assertIn("agent", r["hint"])

    def test_infra_below_band_escalates(self):
        install_fake("infra", 0.5)
        r = pr.route("some failure")
        self.assertFalse(r["act"])
        self.assertIn("full log", r["hint"])

    def test_non_routable_labels_always_escalate(self):
        # 'test-failure' absorbs flaky retries at 0.84-0.91 confidence on
        # the fixture — no band for it until that is fixed.
        install_fake("test-failure", 0.95)
        r = pr.route("some failure")
        self.assertFalse(r["act"])
        self.assertIn("full log", r["hint"])

    def test_unknown_never_routes_even_at_high_confidence(self):
        install_fake("unknown", 0.95)
        r = pr.route("Job cancelled by user.")
        self.assertFalse(r["act"])
        self.assertIn("full log", r["hint"])

    def test_worker_failure_raises(self):
        fake = types.ModuleType("laya_client")
        fake.ask = lambda requests, **_: None
        sys.modules["laya_client"] = fake
        with self.assertRaises(RuntimeError):
            pr.route("x")

    def test_rejects_unknown_label(self):
        install_fake("gremlins", 0.9)
        with self.assertRaises(RuntimeError):
            pr.route("x")


if __name__ == "__main__":
    unittest.main()
