#!/usr/bin/env python3
"""Restricted-domain content gate for corporate repos, scored locally by Laya.

    python3 ~/.agent/tools/laya_domain_guard.py --text "..."
    cat draft.md    | python3 ~/.agent/tools/laya_domain_guard.py --stdin
    python3 ~/.agent/tools/laya_domain_guard.py --staged
    python3 ~/.agent/tools/laya_domain_guard.py --eval "$HOME/.agent/runtime/laya_guard/eval_set.json"

Enforces the operator's hard segregation rule mechanically: betting and
trading content never enters corporate repositories — not in commits, PR
text, or docs. Enforcing that by hand costs a recall + a judgment pass
every single time; this gate answers one local typed question instead
(noul P(restricted-domain), ~13 ms inference, no cloud, no tokens).

Decision bands (default --threshold 0.7, --uncertain-floor 0.4):
  p >= 0.7            restricted  — block in --enforce, loud advisory otherwise
  0.4 <= p < 0.7      uncertain   — escalate: flagged for human/LLM review;
                                    blocks in --enforce (the rule is hard,
                                    so fail-closed)
  p < 0.4             pass

Advisory is the default and always exits 0 after logging to
~/.agent/runtime/laya_guard/decisions.jsonl. --enforce makes restricted
and uncertain exit 1 (git hooks treat that as veto); --dry-run forces
advisory even when --enforce was passed, so a run can prove what it
WOULD block without blocking. Exit codes: 0 pass/advisory, 1 blocked,
2 environment failure.

In --staged mode the gate reads `git diff --cached --stat` plus the first
lines of each staged file in the current repository and notes when a
remote points at an okyeboah repository — where the rule definitely
applies. The eval fixture ships separately under runtime/ (NOT in any
git-tracked surface): its betting-domain examples are exactly the content
the segregation rule keeps out of shared repos.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RUNTIME_DIR = os.path.join(BASE, "runtime", "laya_guard")
MODEL = "aac6fef/laya-mlx"
MAX_STATE_CHARS = 2400          # state must fit the 512-1024 token context
DEFAULT_THRESHOLD = 0.7
DEFAULT_UNCERTAIN_FLOOR = 0.4

QUESTION = {
    "restricted": {
        "type": "noul",
        "instructions": ("Does this text belong to the sports-betting / trading "
                         "domain (odds, tips, stakes, bookmakers, bankrolls, "
                         "match predictions, betting bonuses)? Answer true if "
                         "the text is betting-domain content."),
    },
}


def trim_state(text, limit=MAX_STATE_CHARS):
    """Head+tail keep, so both the opening context and the latest lines fit."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[: limit * 2 // 3]
    tail = text[-limit // 3:]
    return head + "\n[… omitted …]\n" + tail


def decide(p, threshold, uncertain_floor):
    if p >= threshold:
        return "restricted"
    if p >= uncertain_floor:
        return "uncertain"
    return "pass"


def staged_text(cwd):
    """State text for the staged diff: stat lines + per-file first lines."""
    try:
        stat = subprocess.run(["git", "diff", "--cached", "--stat"], cwd=cwd,
                              capture_output=True, text=True, timeout=30).stdout
        names = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=cwd,
            capture_output=True, text=True, timeout=30).stdout.split()
        remotes = subprocess.run(["git", "remote", "-v"], cwd=cwd,
                                 capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"git failed: {exc}")
    chunks = [stat.strip() or "(nothing staged)"]
    for name in names[:20]:
        try:
            content = subprocess.run(
                ["git", "diff", "--cached", "--", name], cwd=cwd,
                capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        chunks.append(f"--- {name} ---\n{content[:600]}")
    state = "\n".join(chunks)
    note = ""
    if "okyeboah" in remotes:
        note = "gate applies: an okyeboah remote is configured here"
    return state, note


def log_path():
    return os.path.join(RUNTIME_DIR, "decisions.jsonl")


def log_decision(entry):
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "model": MODEL, **entry}
    with open(log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def metrics(rows, t):
    """Confusion counts at threshold t; precision/recall None when undefined."""
    tp = sum(1 for r in rows if r["p"] >= t and r["label"])
    fp = sum(1 for r in rows if r["p"] >= t and not r["label"])
    fn = sum(1 for r in rows if r["p"] < t and r["label"])
    tn = sum(1 for r in rows if r["p"] < t and not r["label"])
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"threshold": t, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None}


def ask_laya(text):
    import laya_client
    results = laya_client.ask([{"state": trim_state(text), "questions": QUESTION}])
    if results is None or not isinstance(results[0], dict):
        raise RuntimeError("laya worker unavailable or returned no result")
    answers = results[0].get("answers") or {}
    restricted = answers.get("restricted")
    if not (isinstance(restricted, dict) and isinstance(restricted.get("noul"), (int, float))):
        raise RuntimeError(f"unexpected laya answer shape: {answers}")
    return float(restricted["noul"])


def evaluate(path, threshold, uncertain_floor):
    """Run the labeled fixture; print per-example rows + calibration report."""
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)
    import laya_client
    requests = [{"state": trim_state(c["text"]), "questions": QUESTION} for c in cases]
    results = laya_client.ask(requests)
    if results is None or len(results) != len(cases):
        print("evaluate: laya worker failed", file=sys.stderr)
        return 2
    rows = []
    for case, res in zip(cases, results):
        p = float(res["answers"]["restricted"]["noul"])
        verdict = decide(p, threshold, uncertain_floor)
        rows.append({"text": case["text"], "label": bool(case["restricted"]),
                     "p": p, "verdict": verdict})
    print(f"{'P(restricted)':>14}  {'label':<10}{'verdict':<11} text")
    for r in sorted(rows, key=lambda x: -x["p"]):
        print(f"{r['p']:>14.4f}  {str(r['label']):<10}{r['verdict']:<11} {r['text'][:60]}")

    def metrics_at(rows, t):
        return metrics(rows, t)

    print("\ncalibration (classification: predicted restricted when p >= t)")
    for t in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        print(" ", json.dumps(metrics_at(rows, t)))
    gate = metrics_at(rows, threshold)
    uncertain = sum(1 for r in rows if uncertain_floor <= r["p"] < threshold)
    print(f"\ngate bands at threshold={threshold}: restricted+escalate "
          f"({gate['tp']} restricted caught, {gate['fp']} false blocks, "
          f"{uncertain} escalated to review)")
    log_decision({"mode": "eval", "source": path, "cases": len(rows),
                  "gate": gate})
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Local restricted-domain content gate (advisory by default).")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="single text to gate")
    source.add_argument("--stdin", action="store_true", help="read text from stdin")
    source.add_argument("--file", help="gate a file's contents")
    source.add_argument("--staged", action="store_true",
                        help="gate the staged diff of the current repository")
    source.add_argument("--eval", metavar="PATH",
                        help="run a labeled JSON fixture and print calibration")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--uncertain-floor", type=float, default=DEFAULT_UNCERTAIN_FLOOR)
    parser.add_argument("--enforce", action="store_true",
                        help="exit 1 on restricted/uncertain (default: advisory)")
    parser.add_argument("--dry-run", action="store_true",
                        help="force advisory even with --enforce; logs what WOULD block")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if args.eval:
        return evaluate(args.eval, args.threshold, args.uncertain_floor)

    if args.text:
        text, source_name = args.text, "text"
    elif args.stdin:
        text, source_name = sys.stdin.read(), "stdin"
    elif args.file:
        with open(args.file, encoding="utf-8") as f:
            text, source_name = f.read(), args.file
    else:
        text, source_name = staged_text(os.getcwd())
        source_name = "staged"

    if not text.strip():
        print("laya_domain_guard: nothing to gate (empty input)")
        return 0

    enforce = args.enforce and not args.dry_run
    try:
        p = ask_laya(text)
    except RuntimeError as exc:
        print(f"laya_domain_guard: {exc}", file=sys.stderr)
        print("failing open is NOT allowed under --enforce; install laya first "
              "(skills/laya-decisions).", file=sys.stderr)
        return 2

    verdict = decide(p, args.threshold, args.uncertain_floor)
    entry = {"mode": source_name, "chars": len(text), "p": round(p, 4),
             "verdict": verdict, "threshold": args.threshold,
             "enforce": enforce}
    if source_name == "staged":
        _, note = staged_text(os.getcwd())
        if note:
            entry["note"] = note
    log_decision(entry)

    if args.json:
        print(json.dumps(entry))
    else:
        print(f"P(restricted)={p:.4f}  verdict={verdict}  "
              f"mode={'enforce' if enforce else 'advisory'}  logged.")

    if verdict == "pass":
        return 0
    if not enforce:
        if verdict == "restricted":
            print("RESTRICTED-DOMAIN content suspected — advisory only; this "
                  "text must NOT reach corporate repositories. (--enforce to veto)",
                  file=sys.stderr)
        else:
            print("uncertain — escalate: have a human or a bigger model skim "
                  "this before it reaches corporate repositories.", file=sys.stderr)
        return 0
    print(f"laya_domain_guard: blocked ({verdict}); see {log_path()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
