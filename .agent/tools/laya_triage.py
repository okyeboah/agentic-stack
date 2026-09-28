#!/usr/bin/env python3
"""Advisory triage for staged review-queue candidates, scored locally by Laya.

    python3 ~/.agent/tools/laya_triage.py                 # score every staged candidate
    python3 ~/.agent/tools/laya_triage.py --limit 10 --json

Read-only. The dream cycle stages candidates mechanically (auto_dream.py);
the subjective judgment is the host agent's. This tool adds a local
typed-decision model (laya-mlx, Apple Silicon, no cloud) that answers the
ONE question it is validated at — near-duplicate against the closest
accepted lesson — plus a conservative genericness check. Advisory only:
graduate.py and reject.py remain the decision path, and an unscored
candidate is not a blocker.

Question set (2026-09-23 calibration pass; see DECISIONS.md for the data):
  duplicate  noul  P(says the same thing as the closest accepted lesson) —
                   asked only when some lesson shares >=1 content word.
                   Validated on labeled pairs: duplicates 0.86-0.97,
                   non-duplicates <=0.12.
  generic    noul  P(claim too vague to act on), asked on the claim ALONE —
                   never with the paired lesson in the state, which measurably
                   poisons the answer (same claim: 0.26 alone vs 0.88 paired).
                   Conservative by design: fires >= 0.60, low recall.
  quality   REMOVED — every rubric variant inverted on labeled fixtures
                   (vague advice scored as useful guidance); the checkpoint
                   cannot judge lesson quality, so the tool no longer asks.

Advisory mapping (first match wins):
  P(duplicate) >= 0.75   -> near-duplicate  (of the named lesson)
  P(generic)   >= 0.60   -> generic
  cluster_size >= 2      -> priority        (mechanical: dream-cycle evidence)
  otherwise              -> review

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
CLAIM_CHARS = 300          # per claim in a state sent to Laya (max_len is 512 tokens)
OVERLAP_WORDS = 1          # min shared content words before the duplicate question fires
GENERIC_P = 0.6
DUPLICATE_P = 0.75
PRIORITY_CLUSTER = 2

Q_DUPLICATE = {
    "type": "noul",
    "instructions": "Does the candidate say the same thing as the existing lesson?",
}
Q_GENERIC = {
    "type": "noul",
    "instructions": "This lesson claim is too vague to act on.",
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
    """Laya requests + plans. Two requests per PAIRED candidate (the generic
    and duplicate questions need different states — see module docstring),
    one per unpaired candidate.

    plans[i] carries what request i was asked: (candidate, kind, paired).
    """
    requests, plans = [], []
    for cand in cands:
        claim = (cand.get("claim") or "")[:CLAIM_CHARS]
        paired = closest_lesson(claim, lessons)
        requests.append({"state": f'Candidate lesson claim: "{claim}"',
                         "questions": {"generic": Q_GENERIC}})
        plans.append((cand, "generic", None))
        if paired is not None:
            state = (f'Candidate lesson claim: "{claim}"\n'
                     f'Existing accepted lesson: "{(paired.get("claim") or "")[:CLAIM_CHARS]}"')
            requests.append({"state": state, "questions": {"duplicate": Q_DUPLICATE}})
            plans.append((cand, "duplicate", paired))
    return requests, plans


def advisory(cand, generic_p, dup_p):
    """First-match-wins label; advisory only, the reviewer decides."""
    if dup_p is not None and dup_p >= DUPLICATE_P:
        return "near-duplicate"
    if generic_p is not None and generic_p >= GENERIC_P:
        return "generic"
    if (cand.get("cluster_size") or 1) >= PRIORITY_CLUSTER:
        return "priority"
    return "review"


def score(plans, results):
    """Merge worker answers into one row per candidate."""
    rows = {}
    order = []
    for (cand, kind, paired), res in zip(plans, results):
        cid = cand.get("id") or id(cand)
        if cid not in rows:
            order.append(cid)
            rows[cid] = {
                "id": cand.get("id"),
                "claim": (cand.get("claim") or "")[:80],
                "cluster_size": cand.get("cluster_size", 1),
                "p_generic": None,
                "p_duplicate": None,
                "duplicate_of": None,
                "advisory": "unscored",
                "_cand": cand,
            }
        row = rows[cid]
        answers = res.get("answers") if isinstance(res, dict) else None
        if isinstance(res, dict) and res.get("error"):
            row[f"error_{kind}"] = res["error"]
        elif isinstance(answers, dict):
            if kind == "generic":
                g = answers.get("generic")
                if isinstance(g, dict) and isinstance(g.get("noul"), (int, float)):
                    row["p_generic"] = round(float(g["noul"]), 4)
            else:
                d = answers.get("duplicate")
                if isinstance(d, dict) and isinstance(d.get("noul"), (int, float)):
                    row["p_duplicate"] = round(float(d["noul"]), 4)
                    if paired is not None:
                        row["duplicate_of"] = paired.get("id")
    out = []
    for cid in order:
        row = rows[cid]
        scored = row["p_generic"] is not None or row["p_duplicate"] is not None
        row["advisory"] = (advisory(row["_cand"], row["p_generic"], row["p_duplicate"])
                           if scored else "unscored")
        out.append({k: v for k, v in row.items() if k != "_cand"})
    return out


def summarize(rows):
    counts = {}
    for row in rows:
        counts[row["advisory"]] = counts.get(row["advisory"], 0) + 1
    return {"total": len(rows), "advisory_counts": counts,
            "review_order": ["priority", "review", "near-duplicate", "generic"]}


def log_triage(rows, meta):
    """Record the triage event in episodic memory — the adoption surface.

    Same shape as recall.py/laya_rerank.py logging: writes an episodic event,
    never a candidate. Logging must never fail the triage itself.
    """
    try:
        sys.path.insert(0, os.path.join(BASE, "tools"))
        from memory_reflect import reflect  # noqa: E402
        summary = summarize(rows)
        detail = {
            "candidates": summary["total"],
            "advisory_counts": summary["advisory_counts"],
            "model": meta.get("model"),
            "score_seconds": meta.get("score_seconds"),
            "via": os.environ.get("LAYA_VIA") or "manual",
        }
        reflect("laya-triage", "triage:review-queue",
                json.dumps(detail, ensure_ascii=False), success=True, importance=5)
    except Exception as e:
        print(f"(warning: triage log failed: {e})", file=sys.stderr)


def format_pretty(rows, meta):
    lines = [f"Laya triage of {len(rows)} staged candidate(s) — advisory only, "
             f"graduate.py/reject.py stay the decision path"]
    lines.append(f"  model: {meta['model']}  scored in {meta['score_seconds']}s  local-only")
    if not rows:
        lines.append("  (nothing staged)")
        return "\n".join(lines)
    lines.append(f"{'id':<16}{'advisory':<15}{'cluster':>8}{'P(dup)':>8}{'P(gen)':>8}  claim")
    for row in rows:
        d = "-" if row["p_duplicate"] is None else f"{row['p_duplicate']:.2f}"
        g = "-" if row["p_generic"] is None else f"{row['p_generic']:.2f}"
        lines.append(f"{row['id'] or '?':<16}{row['advisory']:<15}"
                     f"{row['cluster_size']:>8}{d:>8}{g:>8}  {row['claim']}")
        if row["duplicate_of"]:
            lines.append(f"{'':<47}-> near-duplicate of lesson {row['duplicate_of']}")
        for key in sorted(k for k in row if k.startswith("error_")):
            lines.append(f"{'':<47}-> {key}: {row[key]}")
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
    parser.add_argument("--quiet", action="store_true", help="Don't log to episodic.")
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
              "requests; candidates untouched.", file=sys.stderr)
        return 2

    rows = score(plans, results)
    meta = {"model": laya_client.DEFAULT_CHECKPOINT, "score_seconds": score_seconds}
    if not args.quiet:
        log_triage(rows, meta)

    if args.json:
        print(json.dumps({"meta": meta, "candidates": rows, "summary": summarize(rows)},
                         indent=2))
    else:
        print(format_pretty(rows, meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
