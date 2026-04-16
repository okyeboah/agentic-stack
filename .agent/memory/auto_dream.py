"""Nightly compression. Orchestrates promote + decay + archive + git snapshot."""
import json, os, datetime, subprocess
from promote import find_recurring_patterns, promote_to_semantic
from decay import decay_old_entries
from archive import archive_stale_workspace

ROOT = os.path.abspath(os.path.dirname(__file__))  # .agent/memory/ — absolute so cron paths hold
PROJ_ROOT = os.path.dirname(os.path.dirname(ROOT))   # project root
EPISODIC = os.path.join(ROOT, "episodic/AGENT_LEARNINGS.jsonl")
PROMOTION_THRESHOLD = 7.0


def _load_entries():
    if not os.path.exists(EPISODIC):
        return []
    entries = []
    for line in open(EPISODIC):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _write_entries(entries):
    with open(EPISODIC, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _git_snapshot(promoted, decayed, kept):
    msg = f"dream cycle: promoted {promoted}, decayed {decayed}, kept {kept}"
    try:
        subprocess.run(["git", "add", ROOT], check=False, cwd=PROJ_ROOT)
        subprocess.run(["git", "commit", "-m", msg], check=False, cwd=PROJ_ROOT)
    except FileNotFoundError:
        pass
    return msg


def run_dream_cycle():
    entries = _load_entries()
    if not entries:
        print("dream cycle: no entries")
        return

    recurring = find_recurring_patterns(entries)
    promotable = [e for e in recurring.values()
                  if e.get("_salience", 0) >= PROMOTION_THRESHOLD]
    promoted = promote_to_semantic(promotable, semantic_dir=os.path.join(ROOT, "semantic"))

    kept, archived = decay_old_entries(entries, archive_dir=os.path.join(ROOT, "episodic/snapshots"))
    _write_entries(kept)
    archive_stale_workspace(working_dir=os.path.join(ROOT, "working"),
                            archive_dir=os.path.join(ROOT, "episodic/snapshots"))

    print(_git_snapshot(promoted, len(archived), len(kept)))


if __name__ == "__main__":
    run_dream_cycle()
