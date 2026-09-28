"""Batch worker for Laya typed decisions. Runs under the laya-mlx venv.

The brain's tools must stay stdlib-only on whatever python3 the host harness
uses, while laya-mlx needs Apple-Silicon MLX in its own venv. This worker
bridges the two: it is executed BY the venv interpreter (see laya_client.py),
reads one JSON request, loads the checkpoint once, answers every request,
and prints one JSON response on stdout. No argv, no chatter on stdout —
stderr may carry diagnostics.

Request:  {"checkpoint": str, "batch_size": int,
           "requests": [{"state": str, "questions": {qid: qdef}}, ...]}
Response: {"results": [{"answers": {...}} | {"error": str}, ...],
           "load_seconds": float, "model": str, "online_fetch": bool}

Question shapes follow laya-mlx `predict`: choice (criteria = string labels),
score (criteria = ordered level labels), noul (no criteria). A per-request
failure is reported in place and never kills the batch. The first load tries
HF_HUB_OFFLINE so a cached checkpoint never touches the network; if the
weights are not cached yet the worker re-execs itself once with the hub
online (HF_HUB_OFFLINE is snapshotted at import, so an in-process flip is
invisible to huggingface_hub, and stdin is already consumed by then — the
request travels to the replacement process in a temp file), and every later
run is offline again.
"""
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

DEFAULT_CHECKPOINT = "aac6fef/laya-mlx"


def _load(checkpoint, batch_size):
    """Load the agent, preferring batch_size but tolerating an older Agent()."""
    import laya_mlx as laya
    try:
        return laya.load(checkpoint, batch_size=batch_size)
    except TypeError:
        return laya.load(checkpoint)


def _read_request():
    """The request payload: re-exec temp file if present, else stdin."""
    path = os.environ.get("LAYA_WORKER_PAYLOAD_PATH")
    if not path:
        return sys.stdin.read()
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    raw = _read_request()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        json.dump({"error": f"bad request JSON: {exc}"}, sys.stdout)
        return 2

    requests = payload.get("requests") or []
    checkpoint = payload.get("checkpoint") or os.environ.get("LAYA_CHECKPOINT") or DEFAULT_CHECKPOINT
    try:
        batch_size = int(payload.get("batch_size") or 16)
    except (TypeError, ValueError):
        batch_size = 16

    try:
        import laya_mlx  # noqa: F401
    except ImportError as exc:
        json.dump({"error": f"laya_mlx not importable under this interpreter: {exc}"}, sys.stdout)
        return 2

    started_offline = os.environ.get("HF_HUB_OFFLINE") == "1"
    already_online = os.environ.get("LAYA_WORKER_ONLINE") == "1"
    t0 = time.perf_counter()
    online_fetch = already_online
    try:
        agent = _load(checkpoint, batch_size)
    except Exception as first_error:
        if not started_offline or already_online:
            json.dump({"error": f"load failed: {first_error}"}, sys.stdout)
            return 2
        # Weights not cached on this machine: fetch once, stay offline after.
        # The retry must be a re-exec, not an in-process env flip: huggingface_hub
        # snapshots HF_HUB_OFFLINE into module constants at import time, before
        # main() runs, so changing os.environ here does nothing for the hub. And
        # stdin was consumed above, so the payload travels via a temp file.
        # LAYA_WORKER_ONLINE caps this at one online attempt per invocation.
        fd, path = tempfile.mkstemp(prefix="laya-worker-payload-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(raw)
            os.environ["HF_HUB_OFFLINE"] = "0"
            os.environ["LAYA_WORKER_ONLINE"] = "1"
            os.environ["LAYA_WORKER_PAYLOAD_PATH"] = path
            os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        except OSError as exc:
            try:
                os.unlink(path)
            except OSError:
                pass
            json.dump({"error": f"load failed offline ({first_error}); "
                                f"online re-exec failed: {exc}"}, sys.stdout)
            return 2
    load_seconds = time.perf_counter() - t0

    results = []
    for req in requests:
        try:
            answered = agent.predict(req.get("state", ""), req.get("questions") or {})
            results.append({"answers": answered.get("answers", {})})
        except Exception as exc:
            results.append({"error": str(exc)})

    json.dump({
        "results": results,
        "load_seconds": round(load_seconds, 3),
        "model": checkpoint,
        "online_fetch": online_fetch,
    }, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
