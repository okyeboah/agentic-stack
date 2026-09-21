#!/usr/bin/env python3
"""laya_triage must advise on the right candidate for the right reason.

The tool is advisory: its whole value is the mapping from typed decisions to
a review-priority label, and the pairing rule that only asks "duplicate?"
when some accepted lesson actually shares vocabulary. Both are asserted
here against a fake Laya client — no venv, no weights, no MLX. A test that
needs the model to pass could not run in CI or on a fresh machine.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_triage as lt  # noqa: E402


def fake_answers(quality=2.0, generic=0.2, duplicate=0.2, error=None):
    """An ask() stand-in keyed to what build_requests actually asks."""
    def ask(requests, **_):
        results = []
        for req in requests:
            if error is not None:
                results.append({"error": error})
                continue
            answers = {
                "quality": {"score": quality},
                "generic": {"noul": generic},
            }
            if "duplicate" in req["questions"]:
                answers["duplicate"] = {"noul": duplicate}
            results.append({"answers": answers})
        return results
    return ask


CAND = {"id": "c1", "claim": "Always run verify_test_tree before closing a ticket",
        "cluster_size": 3, "status": "staged"}

LESSONS = [
    {"id": "l1", "claim": "Run verify_test_tree before closing any ticket", "status": "accepted"},
    {"id": "l2", "claim": "Prefer dotnet publish over manual xcopy deploys", "status": "accepted"},
]


class AdvisoryTest(unittest.TestCase):
    def test_junk_beats_everything(self):
        # quality 0.19 with a high duplicate probability still reads junk:
        # the first match wins so one noisy dup score can't rescue garbage.
        self.assertEqual(lt.advisory(0.19, 0.1, 0.9), "junk")

    def test_generic(self):
        self.assertEqual(lt.advisory(2.2, 0.62, 0.1), "generic")

    def test_near_duplicate(self):
        self.assertEqual(lt.advisory(2.0, 0.3, 0.76), "near-duplicate")

    def test_priority(self):
        self.assertEqual(lt.advisory(2.4, 0.2, 0.1), "priority")

    def test_plain_review(self):
        self.assertEqual(lt.advisory(1.5, 0.2, 0.2), "review")

    def test_missing_scores_land_on_review(self):
        # A worker that answered nothing must not be read as junk (None < 1.0
        # is False in Python, but the intent must not depend on that accident).
        self.assertEqual(lt.advisory(None, None, None), "review")


class PairingTest(unittest.TestCase):
    def test_duplicate_asked_only_with_lexical_overlap(self):
        requests, plans = lt.build_requests([CAND], LESSONS)
        self.assertIn("duplicate", requests[0]["questions"])
        self.assertEqual(plans[0][1].get("id"), "l1")

    def test_no_overlap_no_duplicate_question(self):
        lone = dict(CAND, claim="zebra quantum falafel harmonica")
        requests, plans = lt.build_requests([lone], LESSONS)
        self.assertNotIn("duplicate", requests[0]["questions"])
        self.assertIsNone(plans[0][1])

    def test_state_truncates_long_claims(self):
        long_cand = dict(CAND, claim="x" * 5000)
        requests, _ = lt.build_requests([long_cand], LESSONS)
        self.assertLess(len(requests[0]["state"]), 800)

    def test_question_shapes_are_valid_for_laya(self):
        # laya-mlx rejects empty criteria and non-string labels; keep the
        # contract here so a careless edit fails the test, not the worker.
        for qdef in lt.QUESTIONS.values():
            self.assertIn(qdef["type"], ("choice", "score", "noul"))
            self.assertTrue(qdef["instructions"])
            if "criteria" in qdef:
                self.assertTrue(all(isinstance(c, str) and c for c in qdef["criteria"]))


class ScoreTest(unittest.TestCase):
    def test_rows_align_with_plans_and_label(self):
        requests, plans = lt.build_requests([CAND], LESSONS)
        results = fake_answers(quality=2.4, duplicate=0.9)(requests)
        rows = lt.score(plans, results)
        self.assertEqual(rows[0]["advisory"], "near-duplicate")
        self.assertEqual(rows[0]["duplicate_of"], "l1")
        self.assertAlmostEqual(rows[0]["quality"], 2.4)

    def test_request_error_becomes_unscored_row_not_a_crash(self):
        requests, plans = lt.build_requests([CAND], LESSONS)
        results = fake_answers(error="boom")(requests)
        rows = lt.score(plans, results)
        self.assertEqual(rows[0]["advisory"], "unscored")
        self.assertIn("boom", rows[0]["error"])

    def test_summarize_counts_every_row_once(self):
        rows = [{"advisory": "junk"}, {"advisory": "priority"}, {"advisory": "junk"}]
        summary = lt.summarize(rows)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["advisory_counts"], {"junk": 2, "priority": 1})


if __name__ == "__main__":
    unittest.main()
