"""Tests for the mechanical-claim filter that auto_harvest applies.

Two obligations, and the second matters more than the first. The filter must
catch every shape the tool-call hooks emit, and it must never touch a claim a
person wrote. A filter that swallows a real lesson is worse than the backlog
it was added to clear, so the negative cases below are drawn from lessons that
are already accepted in lessons.jsonl.

This test used to import `is_mechanical` and `write_suppression_report` from
promote. Both were removed when auto_harvest took over the filtering, so the
file could not even import; the port keeps the same case list against the live
`auto_harvest.noise_reason`.

Run: python3 ~/.agent/memory/test_mechanical_filter.py
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.agent/memory"))
sys.path.insert(0, os.path.expanduser("~/.agent/harness"))

from auto_harvest import noise_reason

# Verbatim claims from the 42 candidates cleared on 2026-09-07.
MECHANICAL = [
    "FAILURE in claude-code: High-stakes op FAILED (release): dotnet build "
    "desktop/AdoBoardSync.slnx -c Release --nologo",
    "High-stakes op FAILED (schemas): WT=/private/var/folders/wp/x; python3 -",
    "High-stakes op completed (secret): cd ~/dev-repo/ddi && python3 scan.py",
    "High-stakes op completed (force-push): ztk run grep -n \"Force push\"",
    "Ran: grep -rl \"InMemoryCollectionUserCredentialStore\" /Users/x/dev-repo/",
    "Command failed: dotnet test --configuration Release",
    "Tool Agent completed successfully",
    "Tool Bash completed successfully",
    "Wrote /Users/okyeboah/dev-repo/ado-insights/docs/PRD.md (161 lines)",
    "Wrote /private/tmp/ddi-1206/docs/prd-fsd-drift-register.md (285 lines)",
    "Edited /Users/okyeboah/dev-repo/agent-brain/.github/workflows/ci.yml: "
    "replaced ' - name: Secret scan (pat' with ' - name: Secret scan (pat'",
    "Read /Users/okyeboah/.agent/protocols/permissions.md",
    "Completed todo: Wire UI conventions checker into CI. Now working on: X",
    "todo-update: rewrite the PRD",
    "task: review the open tickets",
    "fetch: https://dev.azure.com/AppDev123",
    "tool:NotebookEdit",
]

# Real accepted lessons, and hand-written memory_reflect notes. Every one of
# these must reach the queue.
PROSE = [
    "In this harness the Bash tool's working directory does not persist across "
    "calls: a compound 'cd sub && cmd' affects only that one invocation.",
    "A retraction must clear the lesson's scope, never inherit it.",
    "RLS policy on user_profiles must be updated whenever a new column is "
    "added that affects row visibility. Missed this, caused 401s in staging.",
    "Production env vars for edge functions must be set in supabase secrets, "
    "not .env. The .env file is ignored at deploy time.",
    "Never read the 'High-stakes op' label in episodic memory as evidence that "
    "a high-stakes operation happened.",
    "Rejecting a mechanical review-queue candidate does not remove it.",
    # Adversarial: prose that merely mentions the shapes it must not match.
    "Wrote the migration before reading the trigger, and the bulk insert fired "
    "it 847 times. Read triggers before any bulk write.",
    "Ran out of retries because the token refresh is not idempotent.",
    "The task: field in a candidate is not a rule.",
    # on_failure.py's wrapper must not suppress a real failure reflection.
    "FAILURE in deploy: the RLS policy blocked the insert, so add the policy "
    "in the same migration that adds the column.",
]

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


for claim in MECHANICAL:
    check(noise_reason(claim) is not None, f"missed mechanical: {claim[:70]}")
for claim in PROSE:
    check(noise_reason(claim) is None, f"false positive on prose: {claim[:70]}")

# The one prefix on_failure.py adds must be seen through, and only one: a
# wrapped reflection still has to be classified by the text behind it.
check(noise_reason("FAILURE in zcode: Ran: cd /tmp") is not None,
      "a wrapped mechanical claim must still be caught")
check(noise_reason("FAILURE in deploy: the token refresh is not idempotent, "
                   "so never retry it blindly.") is None,
      "a wrapped real failure must survive")
check(noise_reason("") is None, "empty claim must not be classified")
check(noise_reason(None) is None, "None must not raise")

if failures:
    print(f"FAILED ({len(failures)}):")
    for failure in failures:
        print("  -", failure)
    sys.exit(1)
print(f"PASS: {len(MECHANICAL)} mechanical shapes, {len(PROSE)} prose cases, "
      "plus the wrapper and empty-input paths.")
