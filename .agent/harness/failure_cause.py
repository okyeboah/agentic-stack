"""Name the cause of a failed tool call, and the fix that followed it.

The episodic log records WHAT ran and THAT it failed. It does not record why,
which is why reflection shapes like "Command failed: python3 - <<EOF" carry no
distillation signal: 92 percent of 109,542 reflections are telemetry and 0.4
percent name a reason (measured 2026-11-19).

This module derives the missing halves from evidence the log already holds:

  * CAUSE from the recorded error text, matched against patterns measured on
    the corpus. Two signatures cover essentially every current failure: a
    failing test (63 percent) and a harness cwd reset (37 percent).
  * FIX from the next success of the same skill. A failure retried with an
    identical command was transient; a failure succeeded by a changed command
    carries the change as its fix.

Deliberately deterministic and model-free: the same episode always produces the
same annotation, so an annotation can be trusted, diffed and re-run. Nothing
here quotes raw error text beyond the fixed patterns below, because stderr can
carry credentials; the annotation stores the signature and a bounded fragment
the pattern itself matched.

Never a verdict on quality: `unknown` and `unresolved` are recorded as such.
An annotation that guesses is worse than an absent one, because the dream and
distill steps read it as evidence.
"""
from __future__ import annotations

import json
import os
import re

#: Ordered (signature, pattern, mechanism). First match wins, so the specific
#: shapes sit above the generic "failed" ones.
SIGNATURES = (
    ("cwd_reset",
     re.compile(r"Shell cwd was reset", re.I),
     "the harness reset the working directory mid-command, so a relative path "
     "resolved against another root; the command itself was not the problem"),
    ("guard_block",
     re.compile(r"BLOCKED|denied by permission rules|requires user confirmation", re.I),
     "a guard refused the command before it ran; the rule is the blocker, not "
     "the code"),
    ("missing_binary",
     re.compile(r"command not found|not found: |: not found", re.I),
     "the binary is not on PATH in this shell"),
    ("no_such_path",
     re.compile(r"No such file or directory", re.I),
     "the path did not exist when the command ran"),
    ("permission_denied",
     re.compile(r"Permission denied|Operation not permitted", re.I),
     "the OS refused the filesystem or process operation"),
    ("merge_conflict",
     re.compile(r"CONFLICT|merge conflict|Automatic merge failed", re.I),
     "the branch has a content conflict against its base"),
    ("test_failure",
     re.compile(r"(?=.*\b(test|tests|suite|assert\w*|vitest|pytest|mstest|"
                r"xunit|nunit|jest|spec)\b)(?=.*\b(fail\w*|error|✗))",
                re.I | re.S),
     "a test suite reported a failing test"),
    ("api_error",
     re.compile(r"GraphQL|HTTP [45]\d\d|\b40[0-9]\b|\b50[0-9]\b", re.I),
     "the remote API rejected or failed the request"),
    ("network",
     re.compile(r"Could not resolve|Connection refused|timed out|timeout", re.I),
     "the network call did not complete"),
)

UNKNOWN_MECHANISM = "cause not identified from the recorded output"
#: A failure whose entry carries no error text at all. Distinct from `unknown`:
#: the classifier was not confused, the capture dropped the message. Measured
#: 2026-11-19: 2,451 of 4,782 failures, so this is the dominant capture gap.
NO_ERROR_MECHANISM = ("the failure was recorded without its error text, so the "
                      "cause cannot be named; the tool result reached the hook "
                      "without a message")
VERSION = 3

_NO_ERROR = re.compile(r"Command failed|FAILED|exit", re.I)
_ERROR_LINE = re.compile(r"\bError:", re.I)

#: A shell-word splitter good enough for command comparison: keeps quoted
#: strings together so a flag and its value compare as one token.
_TOKEN = re.compile(r"[^\s'\"]+|\"[^\"]*\"|'[^']*'")


def classify(text):
    """{signature, mechanism, evidence} for a failure's recorded text.

    `evidence` is the pattern's own matched fragment and never the whole
    message: stderr can contain credentials, and an annotation is copied into
    candidates and lessons.
    """
    haystack = str(text or "")
    for signature, pattern, mechanism in SIGNATURES:
        found = pattern.search(haystack)
        if found:
            return {"signature": signature, "mechanism": mechanism,
                    "evidence": found.group(0)[:40]}
    if _NO_ERROR.search(haystack) and not _ERROR_LINE.search(haystack):
        return {"signature": "no_error_recorded",
                "mechanism": NO_ERROR_MECHANISM, "evidence": ""}
    return {"signature": "unknown", "mechanism": UNKNOWN_MECHANISM,
            "evidence": ""}


def _tokens(command):
    return _TOKEN.findall(str(command or ""))


def command_delta(before, after):
    """What changed between the failing command and the one that worked.

    `kind` is identical, edited or replaced. Edited commands carry the removed
    and added tokens plus named change classes, so the caller can state the fix
    instead of printing a diff nobody reads. A different tool is `replaced`:
    comparing a Bash call to a Write call as a text diff would invent changes.
    """
    before_tokens = _tokens(before)
    after_tokens = _tokens(after)
    if before_tokens == after_tokens:
        return {"kind": "identical", "removed": [], "added": [], "classes": []}
    if not before_tokens or not after_tokens:
        return {"kind": "replaced", "removed": [], "added": [], "classes": []}

    before_set, after_set = set(before_tokens), set(after_tokens)
    removed = [t for t in before_tokens if t not in after_set][:8]
    added = [t for t in after_tokens if t not in before_set][:8]

    classes = []
    if any(t in ("ztk",) for t in removed) and not any(t == "ztk" for t in after_tokens):
        classes.append("removed the ztk wrapper")
    if any(t in ("--raw",) for t in removed) and "--raw" not in after_set:
        classes.append("dropped --raw")
    if removed and any(t in ("-C", "--git-dir", "--work-tree") for t in added):
        classes.append("added a directory flag")
    if any(t == "cd" for t in before_tokens) and "cd" not in after_set:
        classes.append("removed the cd prefix")
    new_flags = sorted(t for t in added if t.startswith("-") and t not in ("-",))
    if new_flags:
        classes.append("added flag " + " ".join(new_flags[:3]))
    dropped_flags = sorted(t for t in removed if t.startswith("-") and t not in ("-",))
    if dropped_flags:
        classes.append("dropped flag " + " ".join(dropped_flags[:3]))
    if any(t.startswith(("/", "~/")) for t in added) and not any(
            t.startswith(("/", "~/")) for t in before_tokens):
        classes.append("switched to an absolute path")
    return {"kind": "edited", "removed": removed, "added": added,
            "classes": classes}


def _overlap(before, after):
    """Token Jaccard of two commands, 0.0 when either is empty."""
    a, b = set(_tokens(before)), set(_tokens(after))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


#: A successor is this failure's fix only when the commands are recognisably
#: the same work. Two sessions interleave in one log, so a success three calls
#: later can belong to another project entirely; pairing on proximity alone
#: would invent a fix and the delta would describe an unrelated edit.
RELATED_FLOOR = 0.34


def _entry_text(entry):
    """The text a cause is classified from: reflection then detail."""
    return " ".join(str(entry.get(k) or "") for k in ("reflection", "detail"))


def pair(failure, successor):
    """Annotate one failure with its cause and the fix a successor proves."""
    cause = classify(_entry_text(failure))
    result = {"ts": failure.get("timestamp"), "version": VERSION,
              "signature": cause["signature"],
              "mechanism": cause["mechanism"],
              "evidence": cause["evidence"],
              "fix": {"kind": "unresolved", "removed": [], "added": [],
                      "classes": [], "gap": None}}
    if successor is None:
        return result
    delta = command_delta(failure.get("action"), successor.get("action"))
    same_tool = str(failure.get("action")).split(":")[0] == str(
        successor.get("action")).split(":")[0]
    if not same_tool:
        delta = {"kind": "replaced", "removed": [], "added": [],
                 "classes": ["switched tool"]}
    if delta["kind"] == "edited" and _overlap(
            failure.get("action"), successor.get("action")) < RELATED_FLOOR:
        delta = {"kind": "unresolved", "removed": [], "added": [],
                 "classes": ["no related successor"], "gap": None}
    delta["gap"] = successor.get("_gap") if delta["kind"] != "unresolved" else None
    result["fix"] = delta
    if delta["kind"] == "identical":
        result["fix"]["classes"] = ["retried unchanged"]
    return result


def annotate(entries, max_gap=3, tail=None):
    """One annotation row per failure, paired with a nearby success.

    A success counts as the fix only when it comes after the failure for the
    same skill within `max_gap` entries. Older sessions interleave parallel
    work, so the window is small on purpose: a success three calls later in
    another project is not this failure's fix.
    """
    if tail is not None:
        entries = entries[-tail:]
    rows = []
    for index, entry in enumerate(entries):
        if str(entry.get("result") or "") != "failure":
            continue
        skill = entry.get("skill")
        successor = None
        for offset in range(1, max_gap + 1):
            if index + offset >= len(entries):
                break
            candidate = entries[index + offset]
            if str(candidate.get("result") or "") != "success":
                continue
            if candidate.get("skill") != skill:
                continue
            successor = dict(candidate)
            successor["_gap"] = offset
            break
        rows.append(pair(entry, successor))
    return rows


def read_causes(path):
    """Existing annotation rows keyed by timestamp, {} when absent."""
    out = {}
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
                if isinstance(row, dict) and row.get("ts"):
                    out[row["ts"]] = row
    except OSError:
        pass
    return out


def append_causes(path, rows):
    """Append only rows whose timestamp is absent; returns how many landed."""
    known = read_causes(path)
    fresh = [row for row in rows
             if row.get("ts")
             and (row["ts"] not in known
                  or int(row.get("version") or 0) > int(
                      (known.get(row["ts"]) or {}).get("version") or 0))]
    if not fresh:
        return 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for row in fresh:
                handle.write(json.dumps(row) + "\n")
    except OSError:
        return 0
    return len(fresh)
