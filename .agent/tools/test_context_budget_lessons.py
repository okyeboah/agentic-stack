#!/usr/bin/env python3
"""_top_lessons must spend its 8k-char budget on lessons that matter.

Two burns this file guards against, both real:

- the zero-overlap fallback used to inject the first 8000 chars of the
  lesson list in ORIGINAL order when the query shared no vocabulary —
  ~2k tokens of arbitrary lessons per session;
- lexical-only ranking lets vocabulary-matching noise crowd out
  semantically relevant lessons worded differently.

The laya path is asserted against a fake client (no venv, no weights):
a test that needs the model to pass cannot run on a fresh machine, and
the fallback contract (any laya problem keeps the lexical order) is the
part that must never regress.
"""
import os
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
import context_budget as cb  # noqa: E402


LESSONS_MD = "\n".join([
    "- Always run verify_test_tree before closing a ticket <!-- status=accepted -->",
    "- Prefer dotnet publish over manual xcopy deploys <!-- status=accepted -->",
    "- [PROVISIONAL] Draft rule must not reach the prompt <!-- status=provisional -->",
    "- ~~Superseded rule about deploys~~ <!-- status=superseded -->",
])


def install_fake_laya(prob_by_claim):
    """Put a fake laya_client in sys.modules; returns a restorer."""
    fake = types.ModuleType("laya_client")

    def available():
        return True, "fake"

    def ask(requests, **_):
        results = []
        for req in requests:
            claim = req["state"].split("Lesson claim: ", 1)[1]
            p = prob_by_claim.get(claim, 0.0)
            results.append({"answers": {"relevant": {"noul": p}}})
        return results

    fake.available, fake.ask = available, ask
    saved = sys.modules.get("laya_client")
    sys.modules["laya_client"] = fake
    return lambda: sys.modules.pop("laya_client", None) if saved is None else sys.modules.__setitem__("laya_client", saved)


class SelectionTest(unittest.TestCase):
    def test_terminal_filter_only(self):
        lines = cb._accepted_lesson_lines(LESSONS_MD)
        self.assertEqual(len(lines), 2)
        self.assertFalse(any("PROVISIONAL" in l or "Superseded" in l for l in lines))

    def test_zero_overlap_without_laya_injects_nothing(self):
        restore = install_fake_laya({})
        try:
            sys.modules["laya_client"].available = lambda: (False, "no venv")
            out = cb._top_lessons("zebra quantum falafel", LESSONS_MD)
            self.assertEqual(out, "")
        finally:
            restore()

    def test_zero_overlap_with_relevant_laya_hit(self):
        # The whole point of the laya path: no shared vocabulary, but the
        # model recognizes relevance, so the lesson still earns budget.
        restore = install_fake_laya({
            "Prefer dotnet publish over manual xcopy deploys": 0.9,
        })
        try:
            out = cb._top_lessons("zebra quantum falafel", LESSONS_MD)
            self.assertIn("dotnet publish", out)
            self.assertNotIn("verify_test_tree", out)
        finally:
            restore()

    def test_laya_failure_keeps_lexical_order(self):
        restore = install_fake_laya({})
        try:
            sys.modules["laya_client"].ask = lambda requests, **_: None
            out = cb._top_lessons("deploys publish", LESSONS_MD)
            self.assertIn("dotnet publish", out)
        finally:
            restore()

    def test_laya_filters_lexical_noise(self):
        # Lexically matching but model-rejected lessons must not spend
        # budget; model-accepted ones come first.
        restore = install_fake_laya({
            "Prefer dotnet publish over manual xcopy deploys": 0.95,
            "Always run verify_test_tree before closing a ticket": 0.1,
        })
        try:
            out = cb._top_lessons("deploys publish", LESSONS_MD)
            self.assertIn("dotnet publish", out)
            self.assertNotIn("verify_test_tree", out)
        finally:
            restore()

    def test_empty_query_keeps_budget_fit(self):
        out = cb._top_lessons("", LESSONS_MD)
        self.assertIn("verify_test_tree", out)

    def test_no_accepted_lessons_returns_empty(self):
        self.assertEqual(cb._top_lessons("deploy", "no bullets here"), "")


if __name__ == "__main__":
    unittest.main()
