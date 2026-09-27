---
trigger: always_on
---

# Windsurf rules — agentic-stack portable brain

This project uses a portable brain in `.agent/`. It is authoritative for
memory, skills, and protocols.

## Startup sequence
1. Read `.agent/AGENTS.md`
2. Read `.agent/memory/personal/PREFERENCES.md`
3. Read `.agent/memory/semantic/LESSONS.md`
4. Read `.agent/protocols/permissions.md`

## Recall before non-trivial tasks
For deploy / ship / release / migration / schema / timestamp / timezone /
date / failing test / debug / investigate / refactor, run recall first:

```bash
python3 .agent/tools/recall.py "<short description>"
```

Show surfaced lessons in a `Consulted lessons before acting:` block and
follow them.

## During work
- Consult `.agent/skills/_index.md`. Load a full `SKILL.md` only when its
  triggers match the current task.
- Update `.agent/memory/working/WORKSPACE.md` as the task evolves.
- After significant actions, call
  `python3 .agent/tools/memory_reflect.py <skill> <action> <outcome>`.
- Quick state: `python3 .agent/tools/show.py`.
- Teach a rule in one shot:
  `python3 .agent/tools/learn.py "<rule>" --rationale "<why>"`.

## Hard rules
- Never force push to `main`, `production`, or `staging`.
- Never delete memory entries; archive only.
- Never modify `.agent/protocols/permissions.md`.

<!-- ztk-manual-use:v1 -->
## ztk output compression (manual)

This harness has no ztk pre-tool hook. For commands likely to produce large
output, wrap them manually:

- `ztk run <command>` — compress output before it reaches context
- `ztk run --raw <command>` — exact output when another command will parse it
- Small-output and unrecognized commands need no wrapping

See `~/.agent/skills/ztk/SKILL.md`.
