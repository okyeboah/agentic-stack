#!/usr/bin/env python3
"""Classify PR review comments locally so the agent reads only what matters.

    python3 ~/.agent/tools/laya_comment_triage.py --stdin <<'EOF'
    ["LGTM - merge when green", "Why is this a raw Guid instead of TypedId?"]
    EOF
    python3 ~/.agent/tools/laya_comment_triage.py --eval "$HOME/.agent/runtime/laya_triage/comments_eval.json"

One local `choice` decision per comment (~13 ms, one batched worker call
per batch, no cloud): change-request / question / approval / noise.
Comments are consumed in bulk on every review cycle; today that is a
cloud read per comment. `act` marks labels the agent can trust (confidence
>= --threshold, default 0.7); below that the comment is escalated and the
agent reads it normally — a wrong triage must never hide a change request.

The `triage` subcommand of ado_pr_comments.py calls classify() directly;
this CLI covers any other harness. Read-only: nothing is posted, edited,
or resolved here — routing advice only. Batches log a summary line to
~/.agent/runtime/laya_triage/comments.jsonl.
"""
import argparse
import datetime as dt
import json
import os
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RUNTIME_DIR = os.path.join(BASE, "runtime", "laya_triage")
MAX_STATE_CHARS = 2000          # comment + question must fit the 512-1024 token context
DEFAULT_THRESHOLD = 0.7
LABELS = ["change-request", "question", "approval", "noise"]
MODEL = "aac6fef/laya-mlx"

QUESTION = {
    "intent": {
        "type": "choice",
        "instructions": ("Classify this pull-request review comment by what the "
                         "author wants. 'change-request' asks for code or PR "
                         "changes; 'question' asks for information; 'approval' "
                         "accepts the change; 'noise' needs no reply (ack, bot "
                         "notes, resolved markers, +1)."),
        "criteria": LABELS,
    },
}


def trim_state(text, limit=MAX_STATE_CHARS):
    return (text or "").strip()[:limit]


def classify(texts, threshold=DEFAULT_THRESHOLD):
    """[{text, predicted, confidence, act}] aligned with texts, one batched call."""
    if not texts:
        return []
    import laya_client
    requests = [{"state": trim_state(t), "questions": QUESTION} for t in texts]
    results = laya_client.ask(requests)
    if results is None or len(results) != len(requests):
        raise RuntimeError("laya worker unavailable or misaligned results")
    out = []
    for text, res in zip(texts, results):
        answers = res.get("answers") if isinstance(res, dict) else None
        intent = (answers or {}).get("intent")
        probabilities = intent.get("probabilities") if isinstance(intent, dict) else None
        # The answer's `confidence` field is a separate, poorly calibrated
        # signal (measured 0.01-0.49 on clear cases); the mass on the argmax
        # option is the real confidence. Verified against the live model.
        if not isinstance(probabilities, dict) or not probabilities:
            raise RuntimeError(f"unexpected laya answer shape: {answers}")
        predicted = max(probabilities, key=probabilities.get)
        if predicted not in LABELS:
            raise RuntimeError(f"unexpected label in probabilities: {predicted}")
        out.append({
            "text": text,
            "predicted": predicted,
            "confidence": round(float(probabilities[predicted]), 4),
            "act": probabilities[predicted] >= threshold,
        })
    return out


def read_texts(raw):
    """JSON array of strings, or one comment per line."""
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return parsed
    except json.JSONDecodeError:
        pass
    return [l for l in (line.strip() for line in raw.splitlines()) if l]


def accuracy(rows):
    if not rows:
        return None
    return round(sum(1 for r in rows if r["label"] == r["predicted"]) / len(rows), 3)


def per_label_recall(rows):
    out = {}
    for label in LABELS:
        subset = [r for r in rows if r["label"] == label]
        if subset:
            hit = sum(1 for r in subset if r["predicted"] == label)
            out[label] = f"{hit}/{len(subset)}"
    return out


def evaluate(path, threshold):
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)
    try:
        rows = classify([c["text"] for c in cases], threshold)
    except RuntimeError as exc:
        print(f"evaluate: {exc}", file=sys.stderr)
        return 2
    for r, case in zip(rows, cases):
        r["label"] = case["label"]          # expected label from the fixture
    print(f"{'confidence':>11}  {'predicted':<16}{'expected':<16} act  text")
    for r in sorted(rows, key=lambda x: -x["confidence"]):
        print(f"{r['confidence']:>11.4f}  {r['predicted']:<16}{r['label']:<16} "
              f"{'yes' if r['act'] else 'ESC'}  {r['text'][:52]}")
    print(f"\naccuracy: {accuracy(rows)}   per-label recall: "
          f"{json.dumps(per_label_recall(rows))}")
    escalated = sum(1 for r in rows if not r["act"])
    print(f"escalated to manual reading at threshold={threshold}: "
          f"{escalated}/{len(rows)}")
    log({"mode": "eval", "source": path, "cases": len(rows), "accuracy": accuracy(rows)})
    return 0


def log(entry):
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "model": MODEL, **entry}
    with open(os.path.join(RUNTIME_DIR, "comments.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Local PR-comment intent triage (read-only routing advice).")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--stdin", action="store_true",
                        help="comments as a JSON array of strings or one per line")
    source.add_argument("--file", help="same formats, from a file")
    source.add_argument("--eval", metavar="PATH",
                        help="run a labeled JSON fixture and print calibration")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help="confidence below which a comment escalates (default 0.7)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.eval:
        return evaluate(args.eval, args.threshold)

    raw = sys.stdin.read() if args.stdin else open(args.file, encoding="utf-8").read()
    texts = read_texts(raw)
    if not texts:
        print("laya_comment_triage: no comments on input")
        return 0
    try:
        rows = classify(texts, args.threshold)
    except RuntimeError as exc:
        print(f"laya_comment_triage: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rows))
    else:
        order = {label: i for i, label in enumerate(LABELS)}
        for r in sorted(rows, key=lambda r: (order[r["label"]], -r["confidence"])):
            mark = "act" if r["act"] else "READ-MANUALLY"
            print(f"[{r['label']} {r['confidence']:.2f} {mark}] {r['text'][:90]}")
    log({"mode": "classify", "count": len(rows),
         "escalated": sum(1 for r in rows if not r["act"])})
    return 0


if __name__ == "__main__":
    sys.exit(main())
