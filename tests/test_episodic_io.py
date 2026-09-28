#!/usr/bin/env python3
"""
Validation suite for the bounded episodic append lock.

    python3 tests/test_episodic_io.py

Exit 0 = all tests passed.

Tests:
  1. Plain append lands one intact JSON line.
  2. A wedged lock holder does not block past EPISODIC_LOCK_TIMEOUT_S;
     the entry still lands and the stderr note fires. Before the bound,
     append_jsonl waited on flock(LOCK_EX) forever, and ZCode killed the
     hook at its 60 s default (26 lost-entry timeouts over two days).
  3. Four concurrent writers produce only intact JSON lines.
"""

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".agent" / "harness"))

from hooks._episodic_io import append_jsonl  # noqa: E402

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(f"{label}: {detail}")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="episodic-io-fixture-"))
    path = str(root / "AGENT_LEARNINGS.jsonl")

    # 1. plain append
    append_jsonl(path, {"k": 1})
    lines = Path(path).read_text().splitlines()
    check("plain append lands one intact line",
          len(lines) == 1 and json.loads(lines[0])["k"] == 1, str(lines))

    # 2. wedged holder: bounded wait, then unlocked append
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, sys, time\n"
         "f = open(sys.argv[1], 'ab')\n"
         "fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
         "print('held', flush=True)\n"
         "time.sleep(30)\n",
         path],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        holder.stdout.readline()  # wait until the lock is held
        env = dict(os.environ, EPISODIC_LOCK_TIMEOUT_S="0.3")
        started = time.monotonic()
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys\n"
             f"sys.path.insert(0, {str(REPO / '.agent' / 'harness')!r})\n"
             "from hooks._episodic_io import append_jsonl\n"
             "append_jsonl(sys.argv[1], {'k': 2})\n",
             path],
            capture_output=True, text=True, env=env, timeout=20)
        took = time.monotonic() - started
        check("wedged holder: append completed well under the 60 s hook kill",
              r.returncode == 0 and took < 5, f"rc={r.returncode} took={took:.1f}s")
        check("wedged holder: stderr note fired",
              "appending unlocked" in r.stderr, r.stderr[:200])
        rows = [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
        check("wedged holder: entry still landed", [row.get("k") for row in rows] == [1, 2],
              str(rows))
    finally:
        holder.kill()
        holder.wait()

    # 3. concurrent writers (fresh file: the cases above already appended)
    path = str(root / "AGENT_LEARNINGS-concurrent.jsonl")
    procs = [subprocess.Popen(
        [sys.executable, "-c",
         "import sys\n"
         f"sys.path.insert(0, {str(REPO / '.agent' / 'harness')!r})\n"
         "from hooks._episodic_io import append_jsonl\n"
         "for i in range(10):\n"
         "    append_jsonl(sys.argv[1], {'w': sys.argv[2], 'i': i})\n",
         path, str(w)]) for w in range(4)]
    for p in procs:
        p.wait(timeout=30)
    lines = [x for x in Path(path).read_text().splitlines() if x.strip()]
    parsed = 0
    for line in lines:
        try:
            json.loads(line)
            parsed += 1
        except json.JSONDecodeError:
            pass
    check("40 concurrent appends, every line intact",
          len(lines) == 40 and parsed == 40, f"lines={len(lines)} parsed={parsed}")

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("   -", f)
        return 1
    print("all episodic-io cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
