#!/usr/bin/env python3
"""Route a CI/pipeline failure locally before anyone reads the full log.

    python3 ~/.agent/tools/laya_pipeline_route.py --text "Runner image pull failed: no space left on device"
    python3 ~/.agent/tools/laya_pipeline_route.py --eval "$HOME/.agent/runtime/laya_triage/pipelines_eval.json"

One local `choice` decision from the failure SUMMARY (not the log — a
raw log is far beyond the 512-1024 token state limit): infra /
test-failure / auth-secret / flaky / unknown. Routing saves the cloud
read of the full log for the cases that need it.

Bands (calibrated on the 20-example fixture, see --eval): only 'infra'
separates safely — the model absorbs flaky retries into 'test-failure'
at 0.84-0.91 confidence, so routing is per-label: infra routes at
p >= 0.55 (3/3 precision on the fixture); every other label escalates
to the full-log playbook with its suspected label shown as advice.
Read-only; each routing logs to ~/.agent/runtime/laya_triage/pipelines.jsonl.
"""
import argparse
import datetime as dt
import json
import os
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RUNTIME_DIR = os.path.join(BASE, "runtime", "laya_triage")
MAX_STATE_CHARS = 2000
DEFAULT_THRESHOLD = 0.7
LABELS = ["infra", "test-failure", "auth-secret", "flaky", "unknown"]
# Calibrated per-label act bands. A label absent here always escalates:
# 'test-failure' absorbs flaky retries at 0.84-0.91 confidence on the
# fixture, and a wrong "fix the code" route costs more than a log read.
ROUTABLE_BANDS = {"infra": 0.55}
MODEL = "aac6fef/laya-mlx"

PLAYBOOK = {
    "infra": "infrastructure: check agent/runner, network, disk — do not touch code",
    "test-failure": "real failure: reproduce, fix test or code",
    "auth-secret": "check service connections, PAT expiry, variable-group permissions",
    "flaky": "re-run once; quarantine the test if it repeats",
    "unknown": "escalate: read the full log",
}

QUESTION = {
    "cause": {
        "type": "choice",
        "instructions": (
            "Classify the cause of this CI/pipeline failure from its summary "
            "alone. infra: runner, agent, network, disk, registry or tooling "
            "failure, not the code. test-failure: a real assertion or "
            "behavior failure in the code under test. auth-secret: "
            "permissions, service connections, expired credentials, variable "
            "groups. flaky: passes on retry or a later attempt, passes "
            "locally, order-dependent or intermittent — even when an "
            "assertion failed. unknown: cancelled, no logs, skipped, or "
            "cannot be classified from this summary."),
        "criteria": LABELS,
    },
}


def trim_state(text, limit=MAX_STATE_CHARS):
    return (text or "").strip()[:limit]


def route(text):
    """{label, confidence, act, hint} or RuntimeError on laya failure."""
    import laya_client
    results = laya_client.ask([{"state": trim_state(text), "questions": QUESTION}])
    if results is None or not isinstance(results[0], dict):
        raise RuntimeError("laya worker unavailable")
    cause = (results[0].get("answers") or {}).get("cause")
    probabilities = cause.get("probabilities") if isinstance(cause, dict) else None
    # argmax mass is the real confidence; the `confidence` field is a
    # separate, poorly calibrated signal (verified against the live model).
    if not isinstance(probabilities, dict) or not probabilities:
        raise RuntimeError(f"unexpected laya answer shape: {cause}")
    label = max(probabilities, key=probabilities.get)
    if label not in LABELS:
        raise RuntimeError(f"unexpected label in probabilities: {label}")
    confidence = float(probabilities[label])
    band = ROUTABLE_BANDS.get(label)
    act = band is not None and confidence >= band
    return {"label": label, "confidence": round(confidence, 4), "act": act,
            "hint": PLAYBOOK[label if act else "unknown"]}


def log(entry):
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "model": MODEL, **entry}
    with open(os.path.join(RUNTIME_DIR, "pipelines.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def evaluate(path):
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)
    rows = []
    for case in cases:
        try:
            r = route(case["text"])
        except RuntimeError as exc:
            print(f"evaluate: {exc}", file=sys.stderr)
            return 2
        rows.append({**r, "text": case["text"], "expected": case["label"]})
    print(f"{'confidence':>11}  {'routed':<14}{'expected':<14} act  text")
    for r in sorted(rows, key=lambda x: -x["confidence"]):
        print(f"{r['confidence']:>11.4f}  {r['label']:<14}{r['expected']:<14} "
              f"{'yes' if r['act'] else 'ESC'}  {r['text'][:52]}")
    correct = sum(1 for r in rows if r["label"] == r["expected"])
    per_label = {}
    for label in LABELS:
        subset = [r for r in rows if r["expected"] == label]
        if subset:
            per_label[label] = f"{sum(1 for r in subset if r['label'] == label)}/{len(subset)}"
    escalated = sum(1 for r in rows if not r["act"])
    print(f"\naccuracy: {round(correct / len(rows), 3)}   per-label recall: "
          f"{json.dumps(per_label)}")
    print(f"routed automatically (per-label bands {json.dumps(ROUTABLE_BANDS)}): "
          f"{len(rows) - escalated}/{len(rows)}; {escalated} escalated to full log")
    log({"mode": "eval", "source": path, "cases": len(rows),
         "accuracy": round(correct / len(rows), 3)})
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Local pipeline-failure routing from the failure summary.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="failure summary line(s)")
    source.add_argument("--stdin", action="store_true")
    source.add_argument("--file", help="file holding the summary")
    source.add_argument("--eval", metavar="PATH",
                        help="labeled JSON fixture; prints calibration")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.eval:
        return evaluate(args.eval)

    text = args.text or (sys.stdin.read() if args.stdin else open(args.file, encoding="utf-8").read())
    if not text.strip():
        print("laya_pipeline_route: empty summary")
        return 0
    try:
        r = route(text)
    except RuntimeError as exc:
        print(f"laya_pipeline_route: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(r))
    else:
        mark = "route" if r["act"] else "ESCALATE"
        print(f"[{r['label']} {r['confidence']:.2f} {mark}] {r['hint']}")
    log({"mode": "route", "chars": len(text), "label": r["label"],
         "confidence": r["confidence"], "act": r["act"]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
