"""Auto-harvest: mechanical graduation for the candidate backlog.

The dream cycle stages candidates; graduate.py graduates ONE candidate with
a human rationale. Nothing ran in between, so the backlog grew unattended
(issue #570, G1/G3: 269 staged, zero in flight, repeat offenses stayed
candidates). This pass is the mechanical middle:

  1. Event-log noise (claim prefix families below) -> rejected/ with
     reviewer "auto-harvest". Rejected candidates keep their history; a
     human who disagrees still graduates them by hand with graduate.py.
  2. High-confidence candidates -> lessons.jsonl (status "accepted") +
     candidates/graduated/, then LESSONS.md re-rendered.
  3. REVIEW_QUEUE.md refreshed either way.

Graduation criteria - ALL must hold, no exceptions:

  * status == "staged"
  * claim matches no event-noise prefix family
  * rejection_count == 0 and every decision is a plain "staged" (a candidate
    a human or the heuristic prefilter once rejected is never auto-accepted)
  * heuristic_check passes against the current LESSONS.md
  * canonical_salience >= auto_dream.PROMOTION_THRESHOLD
  * cluster_size >= 3 (three independent episodes, not one event)

The lesson row mirrors graduate.py exactly - same id scheme (lesson_<cid>),
same fields, reviewer "auto-harvest", a rationale that states the criteria.
lessons.jsonl is append-only; a human can retire any auto-lesson later with
graduate.py --supersedes. No subjective validation happens here: anything
below the bar stays staged for the host agent, which is exactly what
REVIEW_QUEUE.md then reports.

The 2026-11-17 audit found the whole first-run backlog (269/269) to be
event telemetry, so the first run rejects nearly everything. That is the
pass working, not failing: the upstream fix is reflection quality, tracked
in issue #570 as a follow-up.

Locking shares auto_dream's cycle lock so a harvest and a dream never race
the candidates directory. Fails closed on a missing corpus (loud error) and
open on nothing else - this is a maintenance tool, not a hook.
"""
import os
import sys
import datetime

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))

from auto_dream import PROMOTION_THRESHOLD, _claim_cycle_lock  # noqa: E402
from graduate import _lesson_id  # noqa: E402  # one source of truth for the id scheme
from render_lessons import append_lesson, render_lessons  # noqa: E402
from review_state import mark_graduated, mark_rejected, write_review_queue_summary  # noqa: E402
from validate import heuristic_check  # noqa: E402

#: Claim prefix families that are event telemetry, never distilled knowledge.
#: Each maps to the mechanical reason recorded on the rejection. Keep the
#: list to shapes a reflection generator emits; a real lesson starts with a
#: rule statement, not a verb. A wrongly rejected candidate keeps its full
#: decision history and can be graduated by hand.
NOISE_FAMILIES = (
    ("High-stakes op completed", "high_stakes_op_event"),
    ("High-stakes op failed", "high_stakes_op_event"),
    ("FAILURE in ", "harness_failure_event"),
    ("Now working on", "workspace_status_event"),
    ("Completed todo", "todo_event"),
    ("Ran: ", "command_event"),
    ("Wrote ", "command_event"),
    ("Edited ", "command_event"),
    ("Tool Agent completed", "subagent_event"),
    ("Tool Agent failed", "subagent_event"),
)

REVIEWER = "auto-harvest"
MIN_CLUSTER = 3


def memory_home():
    """Where the corpus lives; AGENT_MEMORY_HOME overrides for testing."""
    return os.environ.get("AGENT_MEMORY_HOME") or BASE


def noise_reason(claim):
    """The noise family for a claim, or None when it reads like a lesson."""
    text = (claim or "").strip()
    for prefix, reason in NOISE_FAMILIES:
        if text.startswith(prefix):
            return reason
    return None


def human_touched(candidate):
    """True when any decision is not a plain staging."""
    decisions = candidate.get("decisions") or []
    return any(d.get("action") != "staged" for d in decisions)


def graduation_reason(candidate):
    """Why a candidate is NOT auto-graduated, or None when it is."""
    if candidate.get("status") != "staged":
        return "not_staged"
    if (candidate.get("rejection_count") or 0) > 0:
        return "prior_rejection"
    if human_touched(candidate):
        return "human_reviewed"
    if (candidate.get("canonical_salience") or 0.0) < PROMOTION_THRESHOLD:
        return "salience_below_threshold"
    if (candidate.get("cluster_size") or 0) < MIN_CLUSTER:
        return "cluster_too_small"
    return None


def harvest(candidates_dir, semantic_dir, max_graduations=50, dry_run=False):
    """One pass. Returns a summary dict; never raises on candidate errors -
    a single malformed candidate is skipped and counted, not fatal."""
    lessons_md = os.path.join(semantic_dir, "LESSONS.md")
    lessons_text = ""
    if os.path.exists(lessons_md):
        with open(lessons_md, encoding="utf-8") as handle:
            lessons_text = handle.read()

    staged = sorted(
        (f for f in os.listdir(candidates_dir) if f.endswith(".json")),
        key=lambda name: os.path.getmtime(os.path.join(candidates_dir, name)),
    )

    graduated, rejected_noise, skipped = [], [], []
    rendered = None
    for name in staged:
        path = os.path.join(candidates_dir, name)
        try:
            import json
            with open(path, encoding="utf-8") as handle:
                cand = json.load(handle)
        except (OSError, ValueError):
            skipped.append((name, "unreadable"))
            continue
        cid = cand.get("id") or name[:-5]

        family = noise_reason(cand.get("claim"))
        if family:
            if not dry_run:
                mark_rejected(cid, REVIEWER, f"event_log_noise:{family}",
                              candidates_dir)
            rejected_noise.append((cid, family))
            continue

        why = graduation_reason(cand)
        if why:
            skipped.append((cid, why))
            continue
        if len(graduated) >= max_graduations:
            skipped.append((cid, "batch_cap"))
            continue

        check = heuristic_check(cand, lessons_text)
        if not check.get("passed"):
            skipped.append((cid, "heuristic:" + ",".join(check.get("reasons", []))))
            continue

        if dry_run:
            graduated.append((cid, "dry-run"))
            continue

        # Mirror graduate.py: append to semantic memory BEFORE moving the
        # candidate, so a crash mid-pass leaves a staged candidate and the
        # next run completes the move via the exact-duplicate retry path.
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rationale = (
            "auto-harvest: mechanical graduation. "
            f"salience {cand.get('canonical_salience')} >= "
            f"{PROMOTION_THRESHOLD}, cluster {cand.get('cluster_size')} >= "
            f"{MIN_CLUSTER}, no prior rejection, heuristic passed. "
            "Supersede by review if wrong.")
        lesson = {
            "id": _lesson_id(cand),
            "claim": cand.get("claim"),
            "conditions": cand.get("conditions", []),
            "evidence_ids": cand.get("evidence_ids", []),
            "status": "accepted",
            "accepted_at": now,
            "reviewer": REVIEWER,
            "rationale": rationale,
            "cluster_size": cand.get("cluster_size", 1),
            "canonical_salience": cand.get("canonical_salience", 0.0),
            "confidence": check["confidence"],
            "support_count": 0,
            "contradiction_count": 0,
            "supersedes": None,
            "source_candidate": cid,
        }
        append_lesson(lesson, semantic_dir)
        rendered = render_lessons(semantic_dir)
        mark_graduated(cid, REVIEWER, rationale, candidates_dir)
        graduated.append((cid, lesson["id"]))
        if os.path.exists(lessons_md):
            with open(lessons_md, encoding="utf-8") as handle:
                lessons_text = handle.read()

    if not dry_run:
        write_review_queue_summary(candidates_dir, os.path.join(
            os.path.dirname(candidates_dir), "working", "REVIEW_QUEUE.md"))
    return {
        "graduated": graduated,
        "rejected_noise": rejected_noise,
        "skipped": skipped,
        "rendered": rendered,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Mechanically harvest the candidate backlog.")
    parser.add_argument("--dry-run", action="store_true",
                        help="classify only; write nothing")
    parser.add_argument("--max", type=int, default=50,
                        help="graduations per pass (default 50)")
    args = parser.parse_args()

    home = memory_home()
    candidates_dir = os.path.join(home, "memory", "candidates")
    semantic_dir = os.path.join(home, "memory", "semantic")
    if not os.path.isdir(candidates_dir):
        print(f"ERROR: no candidates dir at {candidates_dir}", file=sys.stderr)
        return 1

    lock_fd, acquired = _claim_cycle_lock()
    if not acquired:
        print("another dream/harvest holds the cycle lock; exiting")
        return 0
    try:
        summary = harvest(candidates_dir, semantic_dir,
                          max_graduations=args.max, dry_run=args.dry_run)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)

    print(f"graduated: {len(summary['graduated'])}")
    for cid, lid in summary["graduated"][:10]:
        print(f"  {cid} -> {lid}")
    print(f"rejected as event noise: {len(summary['rejected_noise'])}")
    print(f"left staged: {len(summary['skipped'])}")
    if summary["rendered"]:
        print(f"re-rendered: {summary['rendered']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
