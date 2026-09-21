---
name: ztk
version: 2026-08-30
triggers: ["run shell command", "long output", "token savings", "compress output", "git diff too long", "ztk"]
tools: [bash]
preconditions: ["ztk binary on PATH (brew install codejunkie99/ztk/ztk)"]
constraints: ["never bypass ztk deny rules", "large outputs and known read-only commands are the target; small outputs need no compression", "hooks already rewrite commands automatically; use ztk run manually only in harnesses without hook support"]
category: tooling
---

# ztk — Token Compression for Shell Output

ztk compresses shell command output before it reaches an AI context.
Hooks are installed for Claude Code, Cursor, Gemini CLI, ZCode, and
OpenCode. Commands that ztk does not recognize pass through untouched.
Shell wrappers (`sh -c`, `bash -lc`, `eval`) always pass through.

## Manual use (harnesses without hook support: Codex CLI, Antigravity)

Wrap the command with `ztk run`:

```bash
ztk run git diff HEAD~5
ztk run ls -la src/
ztk run --raw <cmd>   # exact output, no compression
```

Exit codes propagate: an exit=2 after `ztk run grep ...` is usually the
command's own failure (grep uses 2 for errors), not a refusal. True
refusals print `ztk: command denied by permission rules` and target only
string-eval shells: `sh -c`, `zsh -c`, `eval`. Hooks never rewrite those,
so refusal only happens on explicit `ztk run`; run the command unwrapped
instead.

## Keeping the savings up (measured 2026-09-21)

The savings % declines only when raw volume arrives in commands ztk does not
recognize — per-command rates for known commands improved all month while
the overall average fell 63% → 25%. The drivers, in order of size:

1. **docker is unrecognized** (70M raw tokens in September, 0% saved — 71%
   of everything kept). Never run bare `docker build` / `docker compose up`
   / `docker logs` in an agent shell. Pipe to a file and read the tail:
   ```bash
   docker build -t img . > /tmp/docker-build.log 2>&1; tail -60 /tmp/docker-build.log
   ```
   `tail`/`head` compress well (82–91%); the build log never reaches context.
2. **`cat` has a structural ceiling (~21%)** — file contents cannot be
   losslessly crushed further. Do not `cat` whole files into a shell; use
   the harness Read tool, or `sed -n 'A,Bp'` for targeted ranges. cat was
   12.7% of September's kept tokens.
3. **`python3` script output passes raw** (9.7M at 0%). Scripts that print
   tables should print the summary and write the full dump to a file.

Unrecognized commands are recorded in `~/.local/share/ztk/savings.log`
(`ts, cmd, in, out, pct, exit`) — check it before assuming rates fell.
Compression policy is baked into the binary (no config file); adding a
command class (docker first) is an upstream change.

## Inspect savings

```bash
ztk stats
```

## Hook locations (auto-managed by `ztk init [-g] [--skip-permissions]`)

- Claude Code: `.claude/settings.json` PreToolUse -> `ztk rewrite --skip-permissions`
- ZCode: `~/.zcode/cli/config.json` hooks.PreToolUse -> the brain's
  `ztk_rewrite_safe.py` front, which delegates single-line commands to
  `ztk rewrite --skip-permissions` and passes multi-line commands (heredocs,
  embedded newlines) through untouched — ztk's single-line rewriter drops
  heredoc bodies and its permission rules refuse multi-line one-shots.
- OpenCode: `~/.config/opencode/plugin/ztk.js` (tool.execute.before)
- Cursor: `.cursor/hooks.json` -> `ztk cursor-rewrite`
- Gemini CLI: `.gemini/settings.json` BeforeTool -> `ztk gemini-rewrite`

To change hook flags, remove the ztk entry first — `ztk init` refuses
in-place migration. Backups of pre-ztk configs: `~/.ztk-install-backup-20260830/`.
