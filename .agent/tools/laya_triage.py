#!/usr/bin/env python3
"""Advisory triage for staged review-queue candidates, scored locally by Laya.

    python3 ~/.agent/tools/laya_triage.py                 # score every staged candidate
    python3 ~/.agent/tools/laya_triage.py --limit 10 --json

Read-only. The dream cycle stages candidates mechanically (auto_dream.py);
the heuristic prefilter (validate.py) catches only exact duplicates; the
subjective judgment is the host agent's. This tool adds a local
typed-decision model (laya-mlx, Apple Silicon, no cloud) that scores each
candidate BEFORE the reviewer looks, so review time goes to the candidates
that deserve it. It is advisory only: graduate.py and reject.py remain the
decision path, and an unscored candidate is not a blocker.

Per candidate, up to three decisions (~13 ms each, batched in one process):
  quality    score 0-3  durability as a durable lesson (junk..essential rule)
  generic    noul       P(claim too vague to act on)
  duplicate  noul       P(claim restates its closest accepted lesson) —
                        asked only when some lesson shares >=1 content word

Advisory mapping (first match wins):
  quality < 1.0         -> junk            (not worth review time)
  P(generic) >= 0.6     -> generic
  P(duplicate) >= 0.75  -> near-duplicate  (of the named lesson)
  quality >= 2.0        -> priority        (review these first)
  otherwise             -> review

Exit codes: 0 scored (or nothing staged), 2 laya environment unavailable.
"""
import argparse
import glob
import json
import os
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "harness"))
from text import word_set  # noqa: E402

CANDIDATES = os.path.join(BASE, "memory", "candidates")
CLAIM_CHARS = 300          # per claim in the state sent to Laya (max_len is 512 tokens)
OVERLAP_WORDS = 1          # min shared content words before the duplicate question fires
GENERIC_P = 0.6
DUPLICATE_P = 0.75
JUNK_QUALITY = 1.0
PRIORITY_QUALITY = 2.0

QUESTIONS = {
    "quality": {
        "type": "score",
        "instructions": "Rate this candidate as a durable, reusable agent lesson.",
        "criteria": ["junk session noise", "marginal", "useful practice", "essential durable rule"],
    },
    "generic": {
        "type": "noul",
        "instructions": "Is this candidate too vague or context-free to be an actionable lesson? true if generic.",
    },
    "duplicate": {
        "type": "noul",
        "instructions": "Is the candidate claim a near-duplicate or restatement of the existing lesson? true if duplicate.",
    },
}


def load_lessons():
    """Accepted lessons, latest row per id — same rule recall.py applies."""
    import recall
    lessons, _ = recall._merge_sources()
    return lessons


def load_candidates(limit=None):
    rows = []
    for path in sorted(glob.glob(os.path.join(CANDIDATES, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                cand = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if cand.get("status") == "staged":
            rows.append(cand)
            if limit is not None and len(rows) >= limit:
                break
    return rows


def closest_lesson(claim, lessons):
    """The accepted lesson with the most content-word overlap (>=1 required)."""
    qwords = word_set(claim)
    best, best_hits = None, 0
    for lesson in lessons:
        hits = len(qwords & word_set(lesson.get("claim", "")))
        if hits > best_hits:
            best, best_hits = lesson, hits
    return best if best_hits >= OVERLAP_WORDS else None


def build_requests(cands, lessons):
    """One {state, questions} per candidate; returns (requests, plans).

    plans[i] carries what row i was asked, so the answer parser needs no
    re-derivation: (candidate, paired_lesson_or_None).
    """
    requests, plans = [], []
    for cand in cands:
        claim = (cand.get("claim") or "")[:CLAIM_CHARS]
        paired = closest_lesson(claim, lessons)
        questions = {"quality": QUESTIONS["quality"], "generic": QUESTIONS["generic"]}
        state = f"Candidate lesson claim: \"{claim}\""
        if paired is not None:
            questions["duplicate"] = QUESTIONS["duplicate"]
            state += (f"\nExisting accepted lesson: "
                      f"\"{(paired.get('claim') or '')[:CLAIM_CHARS]}\"")
        requests.append({"state": state, "questions": questions})
        plans.append((cand, paired))
    return requests, plans


def advisory(quality, generic_p, dup_p):
    """First-match-wins label; advisory only, the reviewer decides."""
    if quality is not None and quality < JUNK_QUALITY:
        return "junk"
    if generic_p is not None and generic_p >= GENERIC_P:
        return "generic"
    if dup_p is not None and dup_p >= DUPLICATE_P:
        return "near-duplicate"
    if quality is not None and quality >= PRIORITY_QUALITY:
        return "priority"
    return "review"


def score(plans, results):
    """Merge worker answers into per-candidate rows, one per plan entry."""
    rows = []
    for (cand, paired), res in zip(plans, results):
        row = {
            "id": cand.get("id"),
            "claim": (cand.get("claim") or "")[:80],
            "cluster_size": cand.get("cluster_size", 1),
            "quality": None,
            "p_generic": None,
            "p_duplicate": None,
            "duplicate_of": None,
            "advisory": "unscored",
        }
        answers = res.get("answers") if isinstance(res, dict) else None
        if isinstance(res, dict) and res.get("error"):
            row["error"] = res["error"]
        elif isinstance(answers, dict):
            q = answers.get("quality")
            if isinstance(q, dict) and isinstance(q.get("score"), (int, float)):
                row["quality"] = round(float(q["score"]), 3)
            g = answers.get("generic")
            if isinstance(g, dict) and isinstance(g.get("noul"), (int, float)):
                row["p_generic"] = round(float(g["noul"]), 4)
            d = answers.get("duplicate")
            if isinstance(d, dict) and isinstance(d.get("noul"), (int, float)):
                row["p_duplicate"] = round(float(d["noul"]), 4)
                if paired is not None:
                    row["duplicate_of"] = paired.get("id")
            row["advisory"] = advisory(row["quality"], row["p_generic"], row["p_duplicate"])
        rows.append(row)
    return rows


def summarize(rows):
    counts = {}
    for row in rows:
        counts[row["advisory"]] = counts.get(row["advisory"], 0) + 1
    return {"total": len(rows), "advisory_counts": counts,
            "review_order": ["priority", "review", "near-duplicate", "generic", "junk"]}


def format_pretty(rows, meta):
    lines = [f"Laya triage of {len(rows)} staged candidate(s) — advisory only, "
             f"graduate.py/reject.py stay the decision path"]
    lines.append(f"  model: {meta['model']}  scored in {meta['score_seconds']}s  local-only")
    if not rows:
        lines.append("  (nothing staged)")
        return "\n".join(lines)
    lines.append(f"{'id':<16}{'advisory':<15}{'quality':>8}{'P(dup)':>8}{'P(gen)':>8}  claim")
    for row in rows:
        q = "-" if row["quality"] is None else f"{row['quality']:.2f}"
        d = "-" if row["p_duplicate"] is None else f"{row['p_duplicate']:.2f}"
        g = "-" if row["p_generic"] is None else f"{row['p_generic']:.2f}"
        lines.append(f"{row['id'] or '?':<16}{row['advisory']:<15}{q:>8}{d:>8}{g:>8}  {row['claim']}")
        if row["duplicate_of"]:
            lines.append(f"{'':<47}-> near-duplicate of lesson {row['duplicate_of']}")
        if row.get("error"):
            lines.append(f"{'':<47}-> error: {row['error']}")
    summary = summarize(rows)
    lines.append("")
    lines.append("review order: " + ", ".join(
        f"{k}={summary['advisory_counts'].get(k, 0)}"
        for k in summary["review_order"] if summary["advisory_counts"].get(k)))
    unscored = summary["advisory_counts"].get("unscored", 0)
    if unscored:
        lines.append(f"  ({unscored} unscored — check laya availability; not a blocker)")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Advisory local-model triage for staged review-queue candidates.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Score only the first N staged candidates.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args()

    import laya_client
    ok, where = laya_client.available()
    if not ok:
        print(f"laya_triage: laya environment unavailable: {where}", file=sys.stderr)
        print("Install per skills/laya-decisions/SKILL.md, or set LAYA_PYTHON.", file=sys.stderr)
        return 2

    cands = load_candidates(args.limit)
    if not cands:
        print("laya_triage: no staged candidates; nothing to do.")
        return 0
    lessons = load_lessons()

    import time
    requests, plans = build_requests(cands, lessons)
    t0 = time.perf_counter()
    results = laya_client.ask(requests)
    score_seconds = round(time.perf_counter() - t0, 2)
    if results is None:
        print("laya_triage: scoring failed (see stderr); candidates untouched.", file=sys.stderr)
        return 2
    if len(results) != len(plans):
        print(f"laya_triage: worker returned {len(results)} results for {len(plans)} "
              "candidates; candidates untouched.", file=sys.stderr)
        return 2

    rows = score(plans, results)
    meta = {"model": laya_client.DEFAULT_CHECKPOINT, "score_seconds": score_seconds}

    if args.json:
        print(json.dumps({"meta": meta, "candidates": rows, "summary": summarize(rows)},
                         indent=2))
    else:
        print(format_pretty(rows, meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
