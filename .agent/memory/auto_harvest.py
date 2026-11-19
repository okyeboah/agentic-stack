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
import re
import subprocess
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
    ("High-stakes op FAILED", "high_stakes_op_event"),
    ("Now working on", "workspace_status_event"),
    ("Completed todo", "todo_event"),
    ("Ran: ", "command_event"),
    ("Command failed: ", "command_event"),
    ("Tool Agent completed", "subagent_event"),
    ("Tool Agent failed", "subagent_event"),
    ("Tool Bash completed", "subagent_event"),
    ("todo-update: ", "todo_event"),
    ("task: ", "tool_event"),
    ("fetch: ", "tool_event"),
    ("tool:", "tool_event"),
)

#: File-write shapes: the verb alone is not enough, because prose also opens
#: with it ("Wrote the migration before reading the trigger..."). A write event
#: names a path, so the second token must look like one.
PATH_VERBS = (
    ("Wrote ", "command_event"),
    ("Edited ", "command_event"),
    ("Read ", "command_event"),
)
_PATH_LIKE = re.compile(r"^(/|~/|\./|\.\./|[\w.-]+/|\S+\.[A-Za-z0-9]{1,8}$)")

#: on_failure.py wraps a reflection as "FAILURE in <harness>: <text>". One
#: prefix is stripped and the remainder is judged, so a wrapped real failure
#: survives and a wrapped event is still caught.
_FAILURE_WRAPPER = re.compile(r"^FAILURE in [\w.-]+:\s*")

REVIEWER = "auto-harvest"
MIN_CLUSTER = 3


def memory_home():
    """Where the corpus lives; AGENT_MEMORY_HOME overrides for testing."""
    return os.environ.get("AGENT_MEMORY_HOME") or BASE


def noise_reason(claim):
    """The noise family for a claim, or None when it reads like a lesson.

    Two shapes need care, and both were wrong before the ported mechanical test
    caught them (2026-11-19):

      * A write event names a path; prose also opens with the same verb, so the
        second token decides. "Wrote the migration before reading the trigger"
        is a lesson, "Wrote /path/x.md (161 lines)" is an event.
      * on_failure.py wraps reflections one prefix deep as
        "FAILURE in <harness>: <text>", so the wrapper is stripped once and the
        remainder judged. Without that, every wrapped lesson was rejected as a
        harness failure while every wrapped event needed a second pass.
    """
    text = _FAILURE_WRAPPER.sub("", (claim or "").strip(), count=1)
    for prefix, reason in NOISE_FAMILIES:
        if text.startswith(prefix):
            return reason
    for prefix, reason in PATH_VERBS:
        if text.startswith(prefix):
            rest = text[len(prefix):].strip()
            if _PATH_LIKE.match(rest):
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


GITIGNORE = """\
episodic/
logs/
__pycache__/
.dream.lock
.dream.lock
"""


def snapshot_memory(home: str) -> str:
    """Commit the memory corpus to its own local git store (issue #570, G11).

    The memory home is append-only by convention and had no recovery path at
    all: an accidental write was permanent. The store lives at
    `<home>/memory/.git` - personal data, never pushed anywhere, so the
    auto_dream rule against unattended `git commit` (which targets work
    repositories) does not apply here. Churn that never needs history
    (episodic telemetry, caches, the cycle lock) is ignored. Best effort:
    every failure prints and returns, never raises into the harvest result.
    """
    memory_dir = os.path.join(home, "memory")
    if not os.path.isdir(os.path.join(memory_dir, ".git")):
        if not _git_run(memory_dir, "init", "-q"):
            return ""
    ignore_path = os.path.join(memory_dir, ".gitignore")
    if not os.path.exists(ignore_path):
        try:
            with open(ignore_path, "w", encoding="utf-8") as handle:
                handle.write(GITIGNORE)
        except OSError:
            pass
    if not _git_run(memory_dir, "add", "-A"):
        return ""
    try:
        status = subprocess.run(
            ["git", "-C", memory_dir, "status", "--porcelain"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"snapshot: status failed: {exc}", file=sys.stderr)
        return ""
    if not status.stdout.strip():
        return ""
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%MZ")
    if _git_run(memory_dir, "commit", "-q", "-m", f"memory snapshot {stamp}"):
        return stamp
    return ""


def _git_run(memory_dir: str, *args: str) -> bool:
    try:
        done = subprocess.run(
            ["git", "-C", memory_dir, *args],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"snapshot: git {' '.join(args[:2])} failed: {exc}",
              file=sys.stderr)
        return False
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()[:160]
        print(f"snapshot: git {' '.join(args[:2])}: {detail}", file=sys.stderr)
        return False
    return True


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

    if not args.dry_run:
        committed = snapshot_memory(home)
        if committed:
            print(f"memory snapshot committed: {committed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
