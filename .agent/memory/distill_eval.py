"""Measure whether the distiller reproduces lessons a reviewer already accepted.

The shape gate in distill.py checks form, not usefulness, and no lexical or
cosine rule can judge usefulness. This is the measurement that can: accepted
lessons are ground truth, their evidence timestamps locate the original
episodes in the episodic log, and a model run over that reconstructed window
should land near the known-good claim.

Honest limits, stated so the numbers are not over-read:
  * The reconstructed window is the evidence episodes plus context, not the
    exact window the original reviewer saw.
  * Cosine to the accepted claim measures topic agreement, not quality. A
    claim can be good and worded differently; read the printed claims.
  * One seed, one prompt. This ranks models and budgets, it does not prove
    either is sufficient.

Run: python3 ~/.agent/memory/distill_eval.py --model qwen2.5:7b --cases 10
"""
import json
import math
import os
import random
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import distill

HIT = 0.75
EMBED_MODEL = "nomic-embed-text"
EMBED_HOST = "http://127.0.0.1:11434"


def _accepted(lessons_path):
    rows = [json.loads(line) for line in open(lessons_path, encoding="utf-8")
            if line.strip()]
    latest = {}
    for row in rows:
        latest[row["id"]] = row
    return [r for r in latest.values() if r.get("status") == "accepted"]


def _episodes(episodic_path):
    out = []
    for line in open(episodic_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("timestamp"):
            out.append(row)
    out.sort(key=lambda r: str(r.get("timestamp")))
    return out


def _window(episodes, index_by_ts, stamps, context):
    """Evidence episodes plus `context` neighbours each side, in order."""
    indices = [index_by_ts[t] for t in stamps if t in index_by_ts]
    if not indices:
        return []
    low = max(0, min(indices) - context)
    high = min(len(episodes), max(indices) + context + 1)
    return episodes[low:high]


def _embed(texts):
    request = urllib.request.Request(
        EMBED_HOST + "/api/embed",
        data=json.dumps({"model": EMBED_MODEL, "input": texts}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    body = json.loads(urllib.request.urlopen(request, timeout=60).read())
    return body["embeddings"]


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=distill.DEFAULT_MODEL)
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument("--context", type=int, default=3,
                        help="episodes either side of the evidence")
    parser.add_argument("--num-predict", type=int, default=distill.DEFAULT_NUM_PREDICT)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    home = distill.memory_home()
    lessons = _accepted(os.path.join(home, "memory", "semantic", "lessons.jsonl"))
    episodes = _episodes(os.path.join(home, "memory", "episodic",
                                      "AGENT_LEARNINGS.jsonl"))
    index_by_ts = {e["timestamp"]: i for i, e in enumerate(episodes)}

    usable = []
    for lesson in lessons:
        stamps = [t for t in (lesson.get("evidence_ids") or [])
                  if isinstance(t, str) and t in index_by_ts]
        if stamps:
            usable.append((lesson, stamps))
    if not usable:
        print("ERROR: no accepted lesson has recoverable evidence")
        return 1
    usable.sort(key=lambda pair: pair[0]["id"])
    sample = random.Random(args.seed).sample(usable, min(args.cases, len(usable)))

    results = []
    print(f"model={args.model} cases={len(sample)} context={args.context} "
          f"num_predict={args.num_predict}")
    for number, (lesson, stamps) in enumerate(sample, 1):
        window = _window(episodes, index_by_ts, stamps, args.context)
        started = time.time()
        reply = distill._call_ollama(args.model, distill.DEFAULT_HOST,
                                     distill.build_prompt(window),
                                     args.num_predict)
        elapsed = time.time() - started
        claims = [str(c.get("claim") or "").strip()
                  for c in distill.parse_claims(reply)]
        claims = [c for c in claims if c]
        best, best_claim = 0.0, ""
        if claims:
            vectors = _embed([lesson["claim"]] + claims)
            for score, claim in zip((_cosine(vectors[0], v) for v in vectors[1:]),
                                    claims):
                if score > best:
                    best, best_claim = score, claim
        results.append({"lesson": lesson["id"], "best_cosine": round(best, 3),
                        "claim": best_claim, "elapsed_s": round(elapsed, 1),
                        "truth": lesson["claim"]})
        print(f"  {number:2d}. {best:.3f} {'HIT ' if best >= HIT else 'miss'} "
              f"{elapsed:5.1f}s  {lesson['id']}")
        print(f"       truth:  {lesson['claim'][:100]}")
        print(f"       made:   {best_claim[:100] or '(no claim)'}")

    hits = sum(1 for r in results if r["best_cosine"] >= HIT)
    mean = sum(r["best_cosine"] for r in results) / len(results)
    total = sum(r["elapsed_s"] for r in results)
    print(f"\nhit rate: {hits}/{len(results)} at cosine >= {HIT}")
    print(f"mean best cosine: {mean:.3f}")
    print(f"mean latency: {total / len(results):.1f}s per case")

    try:
        out = os.path.join(home, "logs", "distill_eval.jsonl")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model": args.model, "cases": len(results), "hits": hits,
                "mean_cosine": round(mean, 3),
                "mean_latency_s": round(total / len(results), 1),
                "num_predict": args.num_predict, "context": args.context,
                "detail": results,
            }) + "\n")
        print(f"recorded: {out}")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
