#!/usr/bin/env python3
"""laya_triage must advise on the right candidate for the right reason.

Tests target the CALIBRATED contract (agent-brain PRs #47/#48): two noul
questions per paired candidate in SEPARATE states (generic asks about the
claim alone; duplicate asks about the claim + lesson pair), the quality
rubric removed as unvalidated, and advisory bands
duplicate > generic > cluster-priority > review. Offline, fake results.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_triage as lt  # noqa: E402


CAND = {"id": "c1", "claim": "Always run verify_test_tree before closing a ticket",
        "cluster_size": 3, "status": "staged"}

LESSONS = [
    {"id": "l1", "claim": "Run verify_test_tree before closing any ticket", "status": "accepted"},
    {"id": "l2", "claim": "Prefer dotnet publish over manual xcopy deploys", "status": "accepted"},
]


def fake_results(plans, generic=0.1, duplicate=0.1):
    """Worker answers shaped like laya_client.ask output for these plans."""
    by_kind = {"generic": generic, "duplicate": duplicate}
    out = []
    for _, kind, _ in plans:
        out.append({"answers": {kind: {"noul": by_kind[kind]}}})
    return out


class AdvisoryTest(unittest.TestCase):
    def test_near_duplicate_wins(self):
        self.assertEqual(lt.advisory(CAND, 0.7, 0.76), "near-duplicate")

    def test_generic_next(self):
        self.assertEqual(lt.advisory(CAND, 0.65, 0.1), "generic")

    def test_priority_from_cluster_size(self):
        self.assertEqual(lt.advisory(CAND, 0.1, 0.1), "priority")

    def test_review_for_lone_clean_candidate(self):
        lone = dict(CAND, cluster_size=1)
        self.assertEqual(lt.advisory(lone, 0.1, 0.1), "review")


class PairingTest(unittest.TestCase):
    def test_paired_candidate_gets_two_requests_in_two_states(self):
        requests, plans = lt.build_requests([CAND], LESSONS)
        self.assertEqual(len(requests), 2)
        kinds = [p[1] for p in plans]
        self.assertEqual(kinds, ["generic", "duplicate"])
        # the duplicate question sees BOTH claims; the generic one alone
        self.assertNotIn("Existing accepted lesson", requests[0]["state"])
        self.assertIn("Existing accepted lesson", requests[1]["state"])
        self.assertEqual(plans[1][2].get("id"), "l1")

    def test_no_overlap_one_generic_request_only(self):
        lone = dict(CAND, claim="zebra quantum falafel harmonica")
        requests, plans = lt.build_requests([lone], LESSONS)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("duplicate", requests[0]["questions"])
        self.assertIsNone(plans[0][2])

    def test_long_claims_truncated(self):
        long_cand = dict(CAND, claim="x" * 5000)
        requests, _ = lt.build_requests([long_cand], LESSONS)
        self.assertLess(max(len(r["state"]) for r in requests), 800)


class ScoreTest(unittest.TestCase):
    def test_one_row_per_candidate_with_dup_metadata(self):
        requests, plans = lt.build_requests([CAND], LESSONS)
        rows = lt.score(plans, fake_results(plans, duplicate=0.9))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["advisory"], "near-duplicate")
        self.assertEqual(rows[0]["duplicate_of"], "l1")

    def test_request_error_leaves_candidate_unscored(self):
        requests, plans = lt.build_requests([CAND], LESSONS)
        results = [{"error": "boom"}] * len(plans)
        rows = lt.score(plans, results)
        self.assertEqual(rows[0]["advisory"], "unscored")


class SummarizeCompatTest(unittest.TestCase):
    def test_rows_expose_sortable_fields(self):
        requests, plans = lt.build_requests([CAND], LESSONS)
        rows = lt.score(plans, fake_results(plans, generic=0.7))
        self.assertEqual(rows[0]["p_generic"], 0.7)
        self.assertIn("cluster_size", rows[0])


if __name__ == "__main__":
    unittest.main()
