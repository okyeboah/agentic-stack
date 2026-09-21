#!/usr/bin/env python3
"""Jev compaction for any harness: prune stale tool results, keep the rest verbatim.

    cat transcript.jsonl | python3 ~/.agent/tools/jev_compact.py > compact.jsonl
    python3 ~/.agent/tools/jev_compact.py transcript.json --json
    python3 ~/.agent/tools/jev_compact.py handoff.json --keep-recent 8 --min-reduction 0.3

fast-jev-compaction replaces lossy summarize-compaction with Jev-scored
pruning: every tool call/result is scored (does the call still matter, is
the result still needed verbatim), stale ones are dropped or truncated, and
everything kept stays byte-identical. User and assistant text is never
rewritten. Claude Code gets this transparently via the plugin's function
hooks; NO other harness exposes a compaction hook, so this tool gives every
other agent the same primitive for the transcripts the BRAIN controls —
handoff notes, session evidence, flywheel exports, any working document the
agent feeds back into its own context.

Input: a JSON array or JSONL of messages shaped like the library's Message:
  {"role": "user"|"assistant", "text": str,
   "toolUses": [{"tool_use_id", "tool", "input"}],
   "toolResults": [{"tool_use_id", "text"}]}
Output: compacted messages as JSONL on stdout, stats on stderr. --json emits
one {"stats", "reduction", "messages"} object instead.

Not worth it: when the reduction is below --min-reduction, the ORIGINAL
messages are emitted unchanged with a stderr note — a summary would be
lossier than the transcript, and jev's own Claude Code hook falls back the
same way. Exit 0 covers both compacted and not-worth-it; exit 2 means the
environment or the Jev call failed (nothing was written to stdout).

Environment: TYPESAFE_API_KEY must be exported in the calling shell — the
key is read from the environment only, never from a flag or file. Override
the library location with JEV_DIST and the node binary with JEV_NODE.
"""
import argparse
import json
import os
import subprocess
import sys

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jev_worker.mjs")


def node_binary():
    override = os.environ.get("JEV_NODE")
    if override:
        return override
    from shutil import which
    return which("node")


def read_messages(text):
    """Accept a JSON array or JSONL; return a list or raise ValueError."""
    text = text.strip()
    if not text:
        raise ValueError("empty input")
    if text.startswith("["):
        messages = json.loads(text)
    else:
        messages = []
        for number, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {number} is not valid JSON: {exc.msg}")
    if not isinstance(messages, list) or not messages:
        raise ValueError("input must be a non-empty array of messages")
    return messages


def compact(messages, keep_recent, threshold, max_state_tokens):
    ok_path = os.path.exists(WORKER)
    node = node_binary()
    if not ok_path:
        return None, f"worker missing: {WORKER}"
    if not node:
        return None, "node not found (install node >=18 or set JEV_NODE)"
    payload = json.dumps({
        "messages": messages,
        "options": {
            "keepThreshold": threshold,
            "preserveRecentMessages": keep_recent,
            "maxStateTokens": max_state_tokens,
        },
    })
    try:
        proc = subprocess.run([node, WORKER], input=payload, capture_output=True,
                              text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None, "jev worker timed out after 300s"
    except OSError as exc:
        return None, f"jev worker failed to start: {exc}"
    try:
        resp = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return None, f"jev worker returned non-JSON output (exit {proc.returncode})"
    if resp.get("error"):
        return None, resp["error"]
    result = resp.get("result") or {}
    return result, None


def main():
    parser = argparse.ArgumentParser(
        description="Prune a transcript's stale tool results with fast-jev-compaction.")
    parser.add_argument("input", nargs="?", default="-",
                        help="transcript file (JSON array or JSONL); default stdin")
    parser.add_argument("--keep-recent", type=int, default=6,
                        help="newest messages never touched (default 6)")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="minimum keep probability (default 0.5)")
    parser.add_argument("--max-state-tokens", type=int, default=25000,
                        help="estimated token ceiling for the state Jev sees")
    parser.add_argument("--min-reduction", type=float, default=0.25,
                        help="below this character reduction, keep the original")
    parser.add_argument("--json", action="store_true",
                        help="emit one JSON object instead of JSONL")
    args = parser.parse_args()

    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    try:
        messages = read_messages(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"jev_compact: bad input: {exc}", file=sys.stderr)
        return 2

    before_chars = sum(len(json.dumps(m)) for m in messages)
    result, error = compact(messages, args.keep_recent, args.threshold,
                            args.max_state_tokens)
    if error is not None:
        print(f"jev_compact: {error}", file=sys.stderr)
        print("nothing written to stdout; the original transcript is untouched.",
              file=sys.stderr)
        return 2

    compacted = result.get("messages") or messages
    reduction = result.get("reduction")
    after_chars = sum(len(json.dumps(m)) for m in compacted)
    char_reduction = 1 - after_chars / before_chars if before_chars else 0.0
    stats = {
        "messages_before": len(messages),
        "messages_after": len(compacted),
        "chars_before": before_chars,
        "chars_after": after_chars,
        "char_reduction": round(char_reduction, 4),
        "jev_reduction": round(reduction, 4) if isinstance(reduction, (int, float)) else None,
        "min_reduction": args.min_reduction,
    }

    if isinstance(reduction, (int, float)) and reduction < args.min_reduction:
        compacted = messages
        stats["char_reduction"] = 0.0
        stats["messages_after"] = len(messages)
        stats["chars_after"] = before_chars
        print(f"jev_compact: reduction {reduction:.2f} < min {args.min_reduction}; "
              "original kept (a summary would be lossier)", file=sys.stderr)

    if args.json:
        print(json.dumps({"stats": stats, "messages": compacted}))
    else:
        for message in compacted:
            print(json.dumps(message, ensure_ascii=False))
        print("jev_compact: " + json.dumps(stats), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
