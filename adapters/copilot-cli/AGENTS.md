# AGENTS.md — Copilot CLI adapter for agentic-stack

Copilot CLI reads `AGENTS.md` as **primary instructions** (highest priority,
above `.github/instructions/` files). This file points it at the portable
brain in `.agent/`.

> **Python invocation**: examples below use `python3`. On stock Windows
> only `python` is on PATH; use whichever resolves on your system.

## Startup (read in order)
1. `.agent/AGENTS.md` — the map of the whole brain
2. `.agent/memory/personal/PREFERENCES.md` — user conventions
3. Lesson recall — never read LESSONS.md whole: `python3 .agent/tools/recall.py "<intent>" --rerank laya`
4. `.agent/protocols/permissions.md` — hard rules, read before any tool call

## Skills
Skills live in `.agent/skills/` (mirrored to `.github/skills/` for native
`/skills` support). Skill discovery: grep your trigger in `.agent/skills/_manifest.jsonl`, then load only that `SKILL.md` (never read `_index.md` whole).
Don't skip this — skills carry constraints the permissions file doesn't cover.

Edit skills in `.agent/skills/` — `.github/skills/` is a mirror; re-running
`./install.sh copilot-cli` will sync it back.

## Recall before non-trivial tasks
For deploy / ship / migration / schema / timestamp / date / failing test /
debug / refactor, FIRST run:

```bash
python3 .agent/tools/recall.py "<description>"
```

Surface results in a `Consulted lessons before acting:` block and follow them.

## Memory discipline
- Update `.agent/memory/working/WORKSPACE.md` as you work.
- After significant actions, run
  `python3 .agent/tools/memory_reflect.py <skill> <action> <outcome>`.
- Never delete memory entries; archive only.
- Quick state: `python3 .agent/tools/show.py`.
- Teach a rule: `python3 .agent/tools/learn.py "<rule>" --rationale "<why>"`.

## Hard rules
- No force push to `main`, `production`, `staging`.
- No modification of `.agent/protocols/permissions.md`.
- No hand-editing `.agent/memory/semantic/LESSONS.md` — use `graduate.py`.
