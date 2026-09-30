# Permissions

The pre_tool_call hook reads this file and enforces it before any tool
invocation. Humans edit this file; the agent does not.

## Always allowed (no approval)
- Read any file in the project directory.
- Run tests.
- Create branches.
- Write to `memory/` and `skills/` directories.
- Create draft pull requests.
- Read public HTTP APIs in the approved domains list.

## Requires approval
- Merge pull requests.
- Deploy to any environment (staging, production).
- Delete files outside of `memory/working/`.
- Install new dependencies or upgrade pinned versions.
- Modify CI/CD configuration.
- Run database migrations.

## Never allowed
- Force push to `main`, `production`, or `staging`.
- Access secrets or credentials directly (use env vars through the shell only).
- Send HTTP requests to domains not on the approved list.
- Modify `permissions.md` (only humans edit this file).
- Disable or bypass `pre_tool_call` hooks.
- Delete entries from episodic or semantic memory (archive, don't delete).

## Approved external domains
  # Google Fonts vendoring (print-design-pipeline fetch_google_fonts.py):
  # one build-time fetch of OFL-licensed families + their woff2 files,
  # so renders stay deterministic and offline afterwards
- `fonts.googleapis.com`
- `fonts.gstatic.com`
  # OpenRouter model catalog + auth/key quota checks (or-free-models skill)
- `openrouter.ai`
- `api.github.com`
- `registry.npmjs.org`
- `pypi.org`
- `api.anthropic.com`
- `api.openai.com`
