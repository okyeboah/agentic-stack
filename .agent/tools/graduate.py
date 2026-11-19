"""Graduate a staged candidate to semantic memory.

The host agent reviews a candidate, decides it's worth keeping, and calls
this tool with a rationale. A heuristic re-check (length + exact duplicate
against current LESSONS.md) runs automatically so last-minute issues get
caught. The rationale is REQUIRED — rubber-stamped promotions are the
whole failure mode this layer is designed to prevent.
"""
import os, sys, json, argparse, hashlib, datetime

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(BASE, "memory"))

from review_state import mark_graduated
from validate import heuristic_check
from render_lessons import append_lesson, render_lessons, load_lessons

CANDIDATES = os.path.join(BASE, "memory/candidates")
SEMANTIC = os.path.join(BASE, "memory/semantic")


def _lesson_id(candidate):
    """1:1 with the candidate's own id (claim + conditions, stable).

    Using md5(claim) alone collides when two distinct patterns happen to
    share canonical text — different clusters about different situations
    with the same "the test failed" reflection, etc. Keying off the
    candidate id keeps them distinct and matches how write_candidates
    identifies the pattern lifecycle.
    """
    cid = candidate.get("id") or ""
    if cid:
        return f"lesson_{cid}"
    # Fallback for older candidate dicts without `id`
    claim = (candidate.get("claim") or "").strip().lower()
    return "lesson_" + hashlib.md5(claim.encode()).hexdigest()[:12]


#: Cosine at or above which a neighbour is annotated as a likely restatement.
#: Calibrated 2026-11-19: a distilled cwd rule scored 0.675 against its sharper
#: accepted twin, related-but-distinct cwd lessons 0.644-0.648, and a
#: same-domain-but-different rule 0.668. The bands overlap, so this never
#: decides anything: it labels the neighbours the reviewer must read.
NEAR_DUPLICATE_THRESHOLD = 0.66
NEAR_DUPLICATE_POOL = 25
EMBED_MODEL = "nomic-embed-text"
EMBED_HOST = "http://127.0.0.1:11434"


def _accepted_lessons(lessons):
    """Latest row per id, accepted and not superseded."""
    latest = {}
    for lesson in lessons:
        lid = lesson.get("id")
        if lid:
            latest[lid] = lesson
    retired = set()
    for lesson in latest.values():
        target = lesson.get("supersedes")
        if target and lesson.get("status") == "accepted":
            retired.add(target)
    return [l for lid, l in latest.items()
            if l.get("status") == "accepted" and lid not in retired]


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _embed(texts):
    """Embeddings from the local embedding model, or None when it cannot run."""
    import urllib.request
    request = urllib.request.Request(
        EMBED_HOST + "/api/embed",
        data=json.dumps({"model": EMBED_MODEL, "input": texts}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        body = json.loads(urllib.request.urlopen(request, timeout=30).read())
    except Exception:
        return None
    vectors = body.get("embeddings") if isinstance(body, dict) else None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        return None
    return vectors


def near_duplicates(claim, lessons, limit=3):
    """Accepted lessons that restate `claim`, nearest first; [] when unknown.

    A lexical prefilter picks the pool (embeddings for 1278 lessons in one
    call would be minutes), then the embedding cosine ranks it. The score is
    a label, not a verdict: the bands for "paraphrase" and "same domain,
    different rule" overlap, so the caller shows the list and the reviewer
    decides. Fail-open: an unavailable model returns [], so graduation never
    blocks on infrastructure.
    """
    claim = str(claim or "").strip()
    if not claim:
        return []
    pool = _accepted_lessons(lessons)
    if not pool:
        return []
    claim_words = set(str(claim).lower().split())

    def overlap(lesson):
        words = set(str(lesson.get("claim") or "").lower().split())
        return len(claim_words & words)

    pool.sort(key=overlap, reverse=True)
    pool = pool[:NEAR_DUPLICATE_POOL]
    vectors = _embed([claim] + [str(l.get("claim") or "") for l in pool])
    if vectors is None:
        return []
    base = vectors[0]
    scored = [(_cosine(base, vector), lesson)
              for vector, lesson in zip(vectors[1:], pool)]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:limit]


def main():
    p = argparse.ArgumentParser(description="Graduate a staged candidate.")
    p.add_argument("candidate_id")
    p.add_argument("--rationale", required=True,
                   help="Why this lesson should be accepted. Required, not optional.")
    p.add_argument("--reviewer", default="host-agent")
    p.add_argument("--provisional", action="store_true",
                   help="Accept as provisional (probationary) rather than full.")
    p.add_argument("--supersedes", default=None,
                   help="ID of an existing lesson this replaces.")
    p.add_argument("--near-duplicate-ack", action="store_true",
                   help="Confirm the near-duplicate list was read and this "
                        "claim is a distinct rule, not a paraphrase.")
    args = p.parse_args()

    cand_path = os.path.join(CANDIDATES, f"{args.candidate_id}.json")
    if not os.path.exists(cand_path):
        print(f"ERROR: candidate not found: {args.candidate_id}", file=sys.stderr)
        sys.exit(1)
    with open(cand_path) as f:
        cand = json.load(f)

    lesson_id = _lesson_id(cand)

    # Retry-safety: if a prior graduation run got as far as appending to
    # lessons.jsonl but crashed before the candidate move, the staged file
    # still exists and the lesson is already recorded. Heuristic check
    # would otherwise reject the retry as an exact duplicate of its own
    # prior output, leaving the candidate stuck. Detect and complete the
    # move without re-appending.
    prior_lesson = next(
        (l for l in load_lessons(SEMANTIC) if l.get("id") == lesson_id),
        None,
    )
    if prior_lesson:
        print(f"retry detected: lesson {lesson_id} already in lessons.jsonl; "
              f"completing candidate move")

        # Guard: lessons.jsonl row must carry the metadata we're about to
        # sync into the candidate file. A legacy / hand-edited / sparse row
        # missing reviewer or rationale would otherwise get silently mixed
        # with retry args (candidate gets args.*, jsonl stays missing) —
        # the exact drift this branch exists to prevent.
        missing_fields = [
            f for f in ("reviewer", "rationale")
            if not prior_lesson.get(f)
        ]
        if missing_fields:
            print(
                f"ERROR: cannot complete retry — lessons.jsonl row "
                f"{lesson_id} is missing {missing_fields}. This is a "
                f"legacy or hand-edited row. Fix it manually (add the "
                f"missing fields) and re-run, or delete the staged "
                f"candidate to start fresh.",
                file=sys.stderr,
            )
            sys.exit(3)

        # Re-render LESSONS.md too. The first attempt could have crashed
        # between append_lesson() and render_lessons(), leaving lessons.jsonl
        # and the rendered LESSONS.md out of sync. Idempotent: if they're
        # already in sync, this is a no-op write of the same content.
        md_path = render_lessons(SEMANTIC)

        # Use the ORIGINAL reviewer/rationale/provisional-flag from the
        # prior lesson, not the retry invocation's args. The retry is
        # finishing the first decision, not making a new one. The guard
        # above ensures prior_lesson has non-empty reviewer + rationale.
        retry_reviewer = prior_lesson["reviewer"]
        retry_rationale = prior_lesson["rationale"]
        retry_provisional = (prior_lesson.get("status") == "provisional")

        diffs = []
        if retry_reviewer != args.reviewer:
            diffs.append(f"reviewer: {args.reviewer!r} → {retry_reviewer!r}")
        if retry_provisional != args.provisional:
            diffs.append(
                f"provisional: {args.provisional} → {retry_provisional}"
            )
        if retry_rationale != args.rationale:
            diffs.append(
                "rationale: overridden (see first-run text in lessons.jsonl)"
            )
        if diffs:
            print(
                f"note: retry invocation metadata differs from the "
                f"first-run record in lessons.jsonl. Honoring the "
                f"original values so lessons.jsonl and the candidate "
                f"file stay in sync:\n  "
                + "\n  ".join(diffs),
                file=sys.stderr,
            )

        mark_graduated(
            args.candidate_id, retry_reviewer, retry_rationale, CANDIDATES,
            provisional=retry_provisional,
        )
        print(f"graduated {args.candidate_id} → lesson {lesson_id} (retry)")
        print(f"re-rendered: {md_path}")
        return

    lessons_md = os.path.join(SEMANTIC, "LESSONS.md")
    existing = open(lessons_md).read() if os.path.exists(lessons_md) else ""
    # When superseding, exclude the target lesson from the duplicate check —
    # replacing a lesson with structurally-better content but same wording
    # is exactly what supersession is for.
    if args.supersedes:
        existing = "\n".join(
            line for line in existing.splitlines()
            if f"id={args.supersedes}" not in line
        )
    check = heuristic_check(cand, existing)
    if not check["passed"]:
        print(f"ERROR: candidate fails heuristic check: {check['reasons']}",
              file=sys.stderr)
        sys.exit(2)

    # Paraphrase check. The heuristic above catches EXACT duplicates only, and
    # on 2026-11-19 a distilled cwd rule graduated beside its sharper accepted
    # twin, then had to be retracted. No cosine threshold separates a
    # paraphrase from a same-domain sibling reliably, so the check does not
    # pretend to: it shows the nearest accepted lessons. A candidate the
    # DISTILLER produced needs an explicit ack or a supersedes target before it
    # may land, because that is the class with proven risk; a hand-written one
    # only gets the note.
    if not args.supersedes:
        neighbours = near_duplicates(cand.get("claim"), load_lessons(SEMANTIC))
        distilled = "distill" in str(cand.get("source") or "") or "distill" in str(
            args.reviewer or "")
        if neighbours:
            print("nearest accepted lessons:", file=sys.stderr)
            for score, lesson in neighbours:
                label = "likely restatement" if score >= NEAR_DUPLICATE_THRESHOLD else "same area"
                print(f"  {score:.3f}  {lesson.get('id')}  [{label}]  "
                      f"{(lesson.get('claim') or '')[:110]}", file=sys.stderr)
        if distilled and neighbours and not args.near_duplicate_ack:
            print("ERROR: a distilled claim needs the neighbour list read. "
                  "Either:\n"
                  "  --supersedes <id>       replace an existing lesson, or\n"
                  "  --near-duplicate-ack    confirm this is a distinct rule",
                  file=sys.stderr)
            sys.exit(4)

    # Atomicity: write to semantic memory BEFORE moving the candidate.
    # If we crash mid-graduation, the staged candidate remains and the
    # reviewer can retry. The retry-safety block above catches the
    # specific "lesson appended but candidate not moved" scenario.
    accepted_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    lesson = {
        "id": lesson_id,
        "claim": cand.get("claim"),
        "conditions": cand.get("conditions", []),
        "evidence_ids": cand.get("evidence_ids", []),
        "status": "provisional" if args.provisional else "accepted",
        "accepted_at": accepted_at,
        "reviewer": args.reviewer,
        "rationale": args.rationale,
        "cluster_size": cand.get("cluster_size", 1),
        "canonical_salience": cand.get("canonical_salience", 0.0),
        "confidence": check["confidence"],
        "support_count": 0,
        "contradiction_count": 0,
        "supersedes": args.supersedes,
        "source_candidate": args.candidate_id,
    }
    append_lesson(lesson, SEMANTIC)
    md_path = render_lessons(SEMANTIC)

    # Semantic writes survived — now move the candidate file.
    mark_graduated(
        args.candidate_id, args.reviewer, args.rationale, CANDIDATES,
        provisional=args.provisional,
    )

    print(f"graduated {args.candidate_id} → lesson {lesson['id']}")
    print(f"re-rendered: {md_path}")


if __name__ == "__main__":
    main()
