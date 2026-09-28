"""Cross-platform locked append for episodic JSONL writes.

POSIX `write(2)` in O_APPEND mode is atomic for payloads up to PIPE_BUF
(4 KB on Linux, 512 B minimum per POSIX). Most episodic entries fit,
but failure entries with reflection + context + detail can exceed that,
and two harness hooks writing from the same process (or from two Pi
sessions on the same repo) can interleave bytes mid-line. Silent
corruption is worse than a visible error because every downstream
reader (`auto_dream.py`, `cluster.py`, `context_budget.py`,
`show.py`) skips `JSONDecodeError` lines without surfacing the loss.

This module serializes appends with `fcntl.flock(LOCK_EX)` on POSIX.
On platforms without `fcntl` (native Windows Python) the lock is a
no-op and behavior matches the pre-lock baseline. WSL, git-bash via
Cygwin, macOS, and Linux all provide `fcntl`.

The lock is bounded: `EPISODIC_LOCK_TIMEOUT_S` (default 5) seconds of
non-blocking retries, then the append proceeds WITHOUT the lock. A
writer holding the lock holds it for microseconds; waiting longer than
the timeout means the holder is wedged, and the alternative on this
machine was measured, not hypothetical: six concurrent agent sessions
append to one shared JSONL, ZCode kills a blocked hook at its 60 s
default, and the entry is lost (26 such timeouts over 2026-09-27/28).
For payloads above PIPE_BUF the unlocked fallback re-opens the
interleave risk the lock exists to close; a rare torn line that readers
already skip is the cheaper failure mode, and the stderr note makes it
visible.
"""
import json
import os
import sys
import time

try:
    import fcntl  # POSIX
    _HAVE_FLOCK = True
except ImportError:
    _HAVE_FLOCK = False

LOCK_TIMEOUT_S = float(os.environ.get("EPISODIC_LOCK_TIMEOUT_S") or "5")


def _acquire(fileno: int) -> bool:
    """Take an exclusive lock, bounded; False means proceed unlocked."""
    if not _HAVE_FLOCK:
        return True  # already unlocked, nothing to wait on
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)


def append_jsonl(path: str, entry: dict) -> dict:
    """Serialize `entry` to one JSON line and append to `path`.

    Uses `open(..., "ab")` (append-binary) to bypass Python's text-mode
    buffering and guarantee a single `write(2)` per call. `fcntl.flock`
    provides cross-process mutual exclusion on POSIX, bounded by
    LOCK_TIMEOUT_S; past the deadline the write proceeds unlocked.
    """
    payload = (json.dumps(entry) + "\n").encode("utf-8")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "ab") as f:
        locked = _acquire(f.fileno())
        if not locked:
            print(f"episodic_io: lock on {path} held past {LOCK_TIMEOUT_S}s; "
                  f"appending unlocked", file=sys.stderr)
        try:
            f.write(payload)
            f.flush()
        finally:
            if locked and _HAVE_FLOCK:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return entry
