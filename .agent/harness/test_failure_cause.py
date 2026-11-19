"""Tests for failure_cause: the cause classifier, the fix delta, and pairing.

Every case below is a real string or command pair from AGENT_LEARNINGS.jsonl
(measured 2026-11-19), not a invented one. The two obligations:

  1. A cause must be named when the evidence supports it, and `unknown` must be
     returned when it does not. A guessed cause becomes evidence in the dream
     and distill steps, so a wrong label is worse than no label.
  2. A retry with an identical command must be reported as transient, because
     "retry succeeded unchanged" is the instruction, not a lesson about code.

Run: python3 ~/.agent/harness/test_failure_cause.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_cause as fc

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


# --- classify: real failure text -----------------------------------------

CASES = {
    "FAILURE in zcode: Command failed: python3 - <<'PYEOF'. Error: Shell cwd "
    "was reset to /Users/okyeboah/dev-repo/agent-brain": "cwd_reset",
    "FAILURE in zcode: Command failed: cd /repo && dotnet test "
    "tests/DDI.ArchitectureTests.csproj. Error: Test Run Failed.": "test_failure",
    "BLOCKED: the pull-request description breaks the template": "guard_block",
    "ztk: command denied by permission rules": "guard_block",
    "sh: 1: zig: not found": "missing_binary",
    "cat: /tmp/nope.md: No such file or directory": "no_such_path",
    "rm: /usr/local/protected: Operation not permitted": "permission_denied",
    "CONFLICT (content): Merge conflict in src/app.ts": "merge_conflict",
    "GraphQL: Could not resolve to a PullRequest with the number of 999999.": "api_error",
    "curl: (6) Could not resolve host: example.invalid": "network",
    "something odd happened": "unknown",
    "": "unknown",
}
for text, expected in CASES.items():
    got = fc.classify(text)
    check(got["signature"] == expected,
          f"classify({text[:50]!r}) -> {got['signature']}, expected {expected}")

# Evidence is bounded and never the whole message, so a credential in stderr
# cannot be copied into a lesson.
secret = ("Error: Shell cwd was reset to /x while token=ghp_"
          "abcdefghijklmnopqrstuvwxyz0123456789 was set")
evidence = fc.classify(secret)["evidence"]
check(len(evidence) <= 40 and "ghp_" not in evidence,
      f"evidence leaked or overran: {evidence!r}")

# --- command_delta: the two real sequences --------------------------------

# 1. The transient case: identical command, retried.
delta = fc.command_delta("bash: python3 - <<'PYEOF'", "bash: python3 - <<'PYEOF'")
check(delta["kind"] == "identical" and not delta["classes"], "identical retry")

# 2. The real fix: the ztk wrapper was dropped and the merge worked.
delta = fc.command_delta(
    'bash: PR_MERGE_APPROVED=27 ztk run --raw gh pr merge 27 --repo x/y --squash',
    'bash: PR_MERGE_APPROVED=27 gh pr merge 27 --repo x/y --squash')
check(delta["kind"] == "edited", "wrapper removal is an edit")
check("removed the ztk wrapper" in delta["classes"],
      f"wrapper class missing: {delta['classes']}")
check("--raw" in delta["removed"], f"--raw not reported removed: {delta['removed']}")

# 3. The cwd fix: a cd prefix removed in favour of absolute paths.
delta = fc.command_delta("bash: cd /a/b && python3 x.py", "bash: python3 /a/b/x.py")
check("removed the cd prefix" in delta["classes"]
      or "switched to an absolute path" in delta["classes"],
      f"cwd fix not recognised: {delta['classes']}")

# 4. A flag added for a build.
delta = fc.command_delta("bash: dotnet test x.slnx", "bash: dotnet test x.slnx --no-build")
check(any("flag" in c for c in delta["classes"]), f"flag class missing: {delta['classes']}")

# --- pair: unresolved, transient, and tool swap --------------------------

FAIL = {"timestamp": "t1", "skill": "zcode", "result": "failure",
        "action": "bash: python3 - <<'EOF'",
        "reflection": "FAILURE in zcode: Command failed. Error: Shell cwd was reset to /x"}
OK_SAME = {"timestamp": "t2", "skill": "zcode", "result": "success",
           "action": "bash: python3 - <<'EOF'"}
OK_EDIT = {"timestamp": "t2", "skill": "zcode", "result": "success",
           "action": "bash: gh pr merge 1"}
OK_TOOL = {"timestamp": "t2", "skill": "zcode", "result": "success",
           "action": "Write /tmp/x"}

row = fc.pair(FAIL, None)
check(row["fix"]["kind"] == "unresolved", "no successor must be unresolved")
check(row["signature"] == "cwd_reset", "cause still classified without a fix")

row = fc.pair(FAIL, OK_SAME)
check(row["fix"]["kind"] == "identical" and row["fix"]["classes"] == ["retried unchanged"],
      f"transient retry misreported: {row['fix']}")

row = fc.pair({**FAIL, "action": "bash: ztk run gh pr merge 1"}, OK_EDIT)
check(row["fix"]["kind"] == "edited" and any("ztk" in c or "--raw" in c
                                             for c in row["fix"]["classes"]),
      f"edit not carried: {row['fix']}")

row = fc.pair(FAIL, OK_TOOL)
check(row["fix"]["kind"] == "replaced" and "switched tool" in row["fix"]["classes"],
      f"tool swap misreported: {row['fix']}")

# --- annotate: pairing window and skill match ---------------------------

RELATED = "bash: python3 - <<'EOF' --check"
SEQ = [
    FAIL,
    {"timestamp": "t2", "skill": "OTHER", "result": "success",
     "action": RELATED},
    {"timestamp": "t3", "skill": "zcode", "result": "success",
     "action": RELATED},
]
rows = fc.annotate(SEQ, max_gap=3)
check(len(rows) == 1 and rows[0]["fix"]["gap"] == 2,
      f"a success from another skill must not be the fix: {rows[0]['fix']}")

rows = fc.annotate([FAIL] + [{"timestamp": f"x{i}", "skill": "zcode",
                              "result": "success", "action": RELATED}
                             for i in range(5)], max_gap=3)
check(rows and rows[0]["fix"]["gap"] == 1, "nearest success wins")

# An unrelated success inside the window is NOT the fix: the floor protects the
# annotation from a parallel session's command.
rows = fc.annotate([FAIL, {"timestamp": "t4", "skill": "zcode",
                           "result": "success",
                           "action": "bash: dotnet test other.slnx --no-build"}],
                   max_gap=3)
check(rows and rows[0]["fix"]["kind"] == "unresolved"
      and "no related successor" in rows[0]["fix"]["classes"],
      f"unrelated successor must stay unresolved: {rows[0]['fix']}")

rows = fc.annotate([FAIL, {"timestamp": "t9", "skill": "zcode",
                           "result": "success", "action": "bash: s"}], max_gap=0)
check(rows and rows[0]["fix"]["kind"] == "unresolved", "gap 0 disables pairing")

if failures:
    print(f"FAILED ({len(failures)}):")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print(f"PASS: {len(CASES)} classifier cases, bounded evidence, 4 delta cases, "
      "4 pairing cases, window and skill scoping.")
