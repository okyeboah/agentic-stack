#!/usr/bin/env python3
"""Comment triage must route only on confident labels and never invent one.

The failure mode this guards: a low-confidence 'noise' hides a change
request from a reviewer. act=False below threshold is the contract; the
label must still come from the fixed set; batch results must stay
aligned with their inputs.
"""
import os
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_comment_triage as ct  # noqa: E402


def install_fake(entries):
    """entries: list of (choice, confidence) consumed in request order."""
    fake = types.ModuleType("laya_client")
    calls = {"i": 0}

    def ask(requests, **_):
        results = []
        for _ in requests:
            choice, conf = entries[calls["i"] % len(entries)]
            calls["i"] += 1
            results.append({"answers": {"intent": {
                "probabilities": {choice: conf}}}})
        return results

    fake.ask = ask
    sys.modules["laya_client"] = fake
    return fake


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        self._cleanup = None

    def tearDown(self):
        if self._cleanup:
            sys.modules.pop("laya_client", None)

    def test_aligned_and_banded(self):
        install_fake([("approval", 0.92), ("noise", 0.4)])
        rows = ct.classify(["LGTM merge when green", "+1"])
        self.assertEqual(rows[0]["predicted"], "approval")
        self.assertTrue(rows[0]["act"])
        self.assertEqual(rows[1]["predicted"], "noise")
        self.assertFalse(rows[1]["act"])          # escalates, never hides silently

    def test_rejects_unknown_label(self):
        install_fake([("vibes", 0.9)])
        with self.assertRaises(RuntimeError):
            ct.classify(["anything"])

    def test_worker_failure_raises(self):
        fake = install_fake([])
        fake.ask = lambda requests, **_: None
        with self.assertRaises(RuntimeError):
            ct.classify(["x"])

    def test_read_texts_formats(self):
        self.assertEqual(ct.read_texts('["a","b"]'), ["a", "b"])
        self.assertEqual(ct.read_texts("a\n\nb"), ["a", "b"])


class EvalMathTest(unittest.TestCase):
    def test_accuracy_and_per_label_recall(self):
        rows = [
            {"predicted": "approval", "label": "approval"},
            {"predicted": "noise", "label": "approval"},
            {"predicted": "question", "label": "question"},
        ]
        self.assertAlmostEqual(ct.accuracy(rows), 2 / 3, places=3)
        self.assertEqual(ct.per_label_recall(rows)["approval"], "1/2")
        self.assertEqual(ct.accuracy([]), None)


if __name__ == "__main__":
    unittest.main()
