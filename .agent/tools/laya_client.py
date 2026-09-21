"""Stdlib bridge from brain tools to the local laya-mlx venv.

The brain runs on whatever python3 the host harness uses; laya-mlx needs
Apple-Silicon MLX in its own venv. This client never imports MLX: it runs
laya_worker.py under the venv interpreter, one subprocess per batch, JSON on
stdin/stdout. Local-only by design: no network except a one-time Hugging Face
weight fetch the first time a checkpoint is used (the worker sets
HF_HUB_OFFLINE for every run after that).

Interpreter resolution order: $LAYA_PYTHON, then
~/dev-repo/laya-mlx/.venv/bin/python. An unavailable environment is reported,
never raised — callers degrade to their non-laya behavior (recall keeps its
lexical order, triage exits 2 with the reason).
"""
import json
import os
import subprocess
import sys

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "laya_worker.py")
DEFAULT_CHECKPOINT = "aac6fef/laya-mlx"


def venv_python():
    """Path to a python that can import laya_mlx, or None."""
    override = os.environ.get("LAYA_PYTHON")
    if override and os.path.exists(override):
        return override
    default = os.path.join(os.path.expanduser("~"), "dev-repo/laya-mlx/.venv/bin/python")
    if os.path.exists(default):
        return default
    return None


def available():
    """(ok, detail) — detail is the interpreter path, or the reason it is not."""
    interpreter = venv_python()
    if not interpreter:
        return False, ("no laya venv found (set LAYA_PYTHON or create "
                       "~/dev-repo/laya-mlx/.venv)")
    if not os.path.exists(WORKER):
        return False, f"worker missing: {WORKER}"
    return True, interpreter


def ask(requests, checkpoint=DEFAULT_CHECKPOINT, batch_size=16, timeout=None):
    """Answer [{state, questions}, ...]; returns aligned result dicts or None.

    Each result is {"answers": {qid: answer}} or {"error": str} — a bad
    question poisons only its own slot. None means the batch as a whole
    failed (no venv, timeout, crash, non-JSON); the reason goes to stderr.
    """
    ok, where = available()
    if not ok:
        print(f"(laya unavailable: {where})", file=sys.stderr)
        return None
    if timeout is None:
        try:
            timeout = int(os.environ.get("LAYA_TIMEOUT", "300"))
        except ValueError:
            timeout = 300
    payload = json.dumps({
        "checkpoint": checkpoint,
        "batch_size": batch_size,
        "requests": requests,
    })
    try:
        proc = subprocess.run([where, WORKER], input=payload, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"(laya worker timed out after {timeout}s)", file=sys.stderr)
        return None
    except OSError as exc:
        print(f"(laya worker failed to start: {exc})", file=sys.stderr)
        return None
    if proc.returncode != 0:
        detail = (proc.stdout.strip() or proc.stderr.strip())[:200]
        print(f"(laya worker exit {proc.returncode}: {detail})", file=sys.stderr)
        return None
    try:
        resp = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("(laya worker returned non-JSON output)", file=sys.stderr)
        return None
    if "error" in resp:
        print(f"(laya load failed: {resp['error']})", file=sys.stderr)
        return None
    return resp.get("results")
