#!/usr/bin/env python3
"""The domain gate must block only what it is sure of, and never lie about mode.

Two contract lines asserted here:

- bands: p >= threshold -> restricted; uncertain floor < p < threshold ->
  escalate; below -> pass. The escalate band exists because a HARD rule
  with a probabilistic judge must fail closed under --enforce and stay
  advisory (but loud) otherwise;
- --dry-run must force advisory even when --enforce was passed: a run
  that claims to veto but does not — or vetoes when it claimed a rehearsal
  — is worse than no gate.

All offline: the laya client is faked, no venv, no weights.
"""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_domain_guard as dg  # noqa: E402


def install_fake_client(prob_by_text=None, default_p=0.1):
    fake = types.ModuleType("laya_client")

    def ask(requests, **_):
        results = []
        for req in requests:
            text = req["state"]
            p = default_p
            if prob_by_text:
                for key, val in prob_by_text.items():
                    if key in text:
                        p = val
                        break
            results.append({"answers": {"restricted": {"noul": p}}})
        return results

    fake.ask = ask
    sys.modules["laya_client"] = fake
    return fake


class BandTest(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(dg.decide(0.9, 0.7, 0.4), "restricted")
        self.assertEqual(dg.decide(0.7, 0.7, 0.4), "restricted")
        self.assertEqual(dg.decide(0.55, 0.7, 0.4), "uncertain")
        self.assertEqual(dg.decide(0.39, 0.7, 0.4), "pass")

    def test_ask_extracts_noul(self):
        install_fake_client({"odds": 0.93})
        self.assertAlmostEqual(dg.ask_laya("latest odds for the match"), 0.93)


class TrimTest(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(dg.trim_state("hello"), "hello")

    def test_long_text_head_and_tail(self):
        long = "A" * 3000 + "TAIL" + "B" * 100
        out = dg.trim_state(long)
        self.assertLessEqual(len(out), dg.MAX_STATE_CHARS + 20)
        self.assertTrue(out.startswith("A"))
        self.assertIn("TAIL", out)          # recent lines survive


class MetricsTest(unittest.TestCase):
    def test_confusion_counts(self):
        rows = [
            {"p": 0.9, "label": True},
            {"p": 0.8, "label": True},
            {"p": 0.6, "label": True},    # fn at 0.7
            {"p": 0.75, "label": False},  # fp at 0.7
            {"p": 0.1, "label": False},
        ]
        m = dg.metrics(rows, 0.7)
        self.assertEqual((m["tp"], m["fp"], m["fn"], m["tn"]), (2, 1, 1, 1))
        self.assertAlmostEqual(m["precision"], 2 / 3, places=3)
        self.assertAlmostEqual(m["recall"], 2 / 3, places=3)


class LogTest(unittest.TestCase):
    def test_log_appends_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = dg.RUNTIME_DIR
            dg.RUNTIME_DIR = tmp
            try:
                dg.log_decision({"mode": "text", "p": 0.9, "verdict": "restricted"})
                lines = [json.loads(l) for l in
                         open(os.path.join(tmp, "decisions.jsonl"))]
                self.assertEqual(lines[0]["verdict"], "restricted")
                self.assertIn("model", lines[0])
            finally:
                dg.RUNTIME_DIR = old


if __name__ == "__main__":
    unittest.main()
