"""Annotate failed episodes with their cause and the fix that followed.

Runs after the session's successes exist, which is why it is a pass and not a
hook: the cause is readable at failure time, but the fix is only knowable once
a later call succeeded. The annotation lands in an append-only sidecar,
`episodic/failure_causes.jsonl`, rather than rewriting the 94 MB episodic log
on every wrap-up.

Idempotent by timestamp: a re-run appends nothing it already holds. Cheap by
default: it scans the tail (last 3000 entries); `--all` backfills the corpus
once.

No model, no network, no guessing: `failure_cause` returns `unknown` and
`unresolved` where the evidence does not support a claim, and those are kept as
labels rather than smoothed over.

Run:
  python3 ~/.agent/memory/enrich_failures.py            # tail, the wrap-up case
  python3 ~/.agent/memory/enrich_failures.py --all      # one-time backfill
  python3 ~/.agent/memory/enrich_failures.py --stats    # distributions only
"""
import json
import os
import sys

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "harness"))

import failure_cause as fc
from auto_dream import _claim_cycle_lock

DEFAULT_TAIL = 3000


def memory_home():
    """Where the corpus lives; AGENT_MEMORY_HOME overrides for testing."""
    return os.environ.get("AGENT_MEMORY_HOME") or BASE


def _entries(path):
    out = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        pass
    return out


def distributions(rows):
    """Signature and fix-kind counts, for the report and for tests."""
    signatures, fixes = {}, {}
    for row in rows:
        signatures[row["signature"]] = signatures.get(row["signature"], 0) + 1
        kind = (row.get("fix") or {}).get("kind", "unresolved")
        fixes[kind] = fixes.get(kind, 0) + 1
    return signatures, fixes


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true",
                        help="scan the whole log instead of the tail")
    parser.add_argument("--tail", type=int, default=DEFAULT_TAIL,
                        help=f"entries scanned when not --all (default {DEFAULT_TAIL})")
    parser.add_argument("--max-gap", type=int, default=3,
                        help="how many later entries may carry the fix")
    parser.add_argument("--dry-run", action="store_true",
                        help="report only; append nothing")
    parser.add_argument("--stats", action="store_true",
                        help="read the sidecar as it stands and report")
    args = parser.parse_args(argv)

    home = memory_home()
    episodic_path = os.path.join(home, "memory", "episodic",
                                 "AGENT_LEARNINGS.jsonl")
    causes_path = os.path.join(home, "memory", "episodic",
                               "failure_causes.jsonl")

    if args.stats:
        known = fc.read_causes(causes_path)
        if not known:
            print(f"no annotations yet at {causes_path}")
            return 0
        rows = list(known.values())
        signatures, fixes = distributions(rows)
        print(f"annotated: {len(rows)}")
        for name, count in sorted(signatures.items(), key=lambda p: -p[1]):
            print(f"  cause {count:5d}  {name}")
        for name, count in sorted(fixes.items(), key=lambda p: -p[1]):
            print(f"  fix   {count:5d}  {name}")
        return 0

    entries = _entries(episodic_path)
    if not entries:
        print(f"ERROR: no episodic log at {episodic_path}", file=sys.stderr)
        return 1

    lock_fd, acquired = _claim_cycle_lock()
    if not acquired:
        print("another dream/harvest/enrich holds the cycle lock; exiting")
        return 0
    try:
        window = entries if args.all else entries[-args.tail:]
        rows = fc.annotate(window, max_gap=args.max_gap)
        signatures, fixes = distributions(rows)
        print(f"failures: {len(rows)}  (scanned {len(window)} entries)")
        for name, count in sorted(signatures.items(), key=lambda p: -p[1]):
            print(f"  cause {count:5d}  {name}")
        for name, count in sorted(fixes.items(), key=lambda p: -p[1]):
            print(f"  fix   {count:5d}  {name}")
        if not args.dry_run:
            written = fc.append_causes(causes_path, rows)
            print(f"appended: {written} new annotation(s)")
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
