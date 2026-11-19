"""Distil rule-shaped lesson candidates from a session's episodic window.

Reflections in AGENT_LEARNINGS.jsonl are telemetry ("Ran: <cmd>",
"FAILURE in zcode: ..."). The dream cycle clusters that text and auto_harvest
rejects the telemetry families, so nothing new can graduate: the gap sits
upstream of both, in turning episodes into claims. This module is that step,
and it is the only place a model is asked to write a lesson.

WHY IT NEVER AUTO-ACCEPTS: a model's claim is a proposal. Candidates land
staged with reviewer "local-distill", cluster_size 1 and salience below
PROMOTION_THRESHOLD, so auto_harvest cannot promote them (it requires a
cluster of three). The ordinary review path judges them: the heuristic
prefilter here, the REVIEW_QUEUE hint, and graduate.py by hand.

INPUT POSTURE: only fields the brain already treats as shareable leave the
corpus - skill, action, result, pain_score, importance, reflection. `detail`
never goes to the model: it holds command text and error output verbatim, and
distilling does not need them because a failure's reflection already carries
the error.

SAMPLING: temperature 0.2 and seed 42 follow the fleet contract
(brain-tools/sampling_contract.py), so a rerun on the same window is stable.

MODEL CHOICE, measured 2026-11-19: the default is qwen2.5:7b (13.8 s on a
15-episode window, clean JSON). gpt-oss:20b is a reasoning model on ollama
0.40.16: it spends the whole num_predict budget inside `thinking` (4120
chars, done_reason=length) and returns empty content, and `think: false` is
ignored by this build. Ask for it explicitly with --model gpt-oss:20b and a
--num-predict of 3000 or more.

FAIL-OPEN: an unreachable model, a malformed reply, or a timeout stages
nothing and exits 0. Distillation must never be why a wrap-up fails.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.path.insert(0, os.path.join(BASE, "harness"))

from auto_dream import PROMOTION_THRESHOLD, _claim_cycle_lock
from auto_harvest import noise_reason
from cluster import pattern_id
from validate import MIN_CLAIM_LEN, heuristic_check

try:
    from text import word_set
except ImportError:  # a brain without the harness text module keeps the rest
    def word_set(text):
        return set(re.findall(r"[a-z0-9][a-z0-9_-]+", str(text).lower()))

DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_WINDOW = 300
DEFAULT_NUM_PREDICT = 900
MIN_IMPORTANCE = 5
#: A window needs this many signal episodes (a failure, or pain at the top of
#: the scale) before a model call is worth making. A window of successful
#: command logs has nothing to distil: the 2026-11-17 audit proved command
#: telemetry becomes no candidate a reviewer would accept.
MIN_SIGNAL = 2
SIGNAL_PAIN = 5
MAX_EPISODES_IN_PROMPT = 20
MAX_CLAIMS = 8
TIMEOUT_S = 120

SYSTEM_PROMPT = (
    "You turn coding-agent session logs into durable lessons. A lesson is a "
    "rule the next agent should follow, stated in the present tense, "
    "explaining the non-obvious reason. It must not describe what happened in "
    "one session; it must generalise. Reply with a JSON array only, no prose, "
    "no code fences. Each element: "
    '{"claim": "<one or two sentences, <= 240 chars, a rule>", '
    '"evidence": [<0-based episode indices this rule came from>], '
    '"confidence": <0.0-1.0>}. '
    "If the log contains no rule worth keeping, reply with []. "
    "Never invent an episode index that is not in the list."
)


def memory_home():
    """Where the corpus lives; AGENT_MEMORY_HOME overrides for testing."""
    return os.environ.get("AGENT_MEMORY_HOME") or BASE


def _rows(path):
    rows = []
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
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _ts(value):
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def is_signal(entry):
    """A failure, or a high-pain episode: the two shapes worth distilling."""
    if str(entry.get("result") or "") == "failure":
        return True
    try:
        return float(entry.get("pain_score") or 0) >= SIGNAL_PAIN
    except (TypeError, ValueError):
        return False


def select_window(entries, limit=DEFAULT_WINDOW, since=None):
    """The newest `limit` entries at or above MIN_IMPORTANCE inside the window.

    The default window is one day because an episode's originality decays: a
    rule distilled from last week competes with lessons the corpus already
    holds.
    """
    cutoff = (since or datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    picked = []
    for entry in reversed(entries):
        if len(picked) >= limit:
            break
        if _ts(entry.get("timestamp")) < cutoff:
            continue
        try:
            importance = float(entry.get("importance") or 0)
        except (TypeError, ValueError):
            importance = 0
        if importance < MIN_IMPORTANCE:
            continue
        picked.append(entry)
    return picked


def _render_episode(index, entry):
    """One shareable line per episode: no detail, no file contents."""
    def cut(value, n):
        return str(value or "").replace("\n", " ")[:n]

    return (
        f"{index}. [{cut(entry.get('timestamp'), 19)}] skill={cut(entry.get('skill'), 24)}"
        f" result={cut(entry.get('result'), 12)} pain={cut(entry.get('pain_score'), 3)}"
        f" importance={cut(entry.get('importance'), 3)}\n"
        f"   action: {cut(entry.get('action'), 160)}\n"
        f"   reflection: {cut(entry.get('reflection'), 240)}"
    )


def build_prompt(episodes):
    return "\n".join(_render_episode(i, e) for i, e in enumerate(episodes))


def _call_ollama(model, host, prompt, num_predict=DEFAULT_NUM_PREDICT,
                 timeout=TIMEOUT_S):
    """One non-streaming chat call; returns the reply text or None.

    `format: json` is the difference between a usable reply and prose: on the
    measured window qwen2.5:7b answers with a bare JSON object under the
    format constraint and with the empty array without it.
    """
    payload = {
        "model": model,
        "format": "json",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2, "seed": 42, "num_predict": num_predict},
    }
    request = urllib.request.Request(
        host.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        body = json.loads(urllib.request.urlopen(request, timeout=timeout).read())
    except Exception:
        return None
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict):
        return None
    return str(message.get("content") or "")


def parse_claims(text):
    """The claims in a reply, whatever JSON shape the model chose.

    Under `format: json` the model emits one object more often than the array
    the prompt asks for, and sometimes wraps the array in a key. All three
    shapes are accepted; anything else is no claims.
    """
    if not text:
        return []
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    # Root-first, then fall back: a valid document starting with { or [ is
    # the whole reply, and scanning for the "[" inside it first would parse
    # the evidence array instead of the claim object.
    candidates = []
    if stripped.startswith("{"):
        candidates.append(("{", "}"))
        candidates.append(("[", "]"))
    elif stripped.startswith("["):
        candidates.append(("[", "]"))
        candidates.append(("{", "}"))
    else:
        candidates.append(("[", "]"))
        candidates.append(("{", "}"))
    doc = None
    for opener, closer in candidates:
        lo, hi = stripped.find(opener), stripped.rfind(closer)
        if lo < 0 or hi <= lo:
            continue
        try:
            parsed = json.loads(stripped[lo:hi + 1])
        except ValueError:
            continue
        if isinstance(parsed, list) and all(isinstance(d, dict) for d in parsed):
            doc = parsed
            break
        if isinstance(parsed, dict) and (parsed.get("claim")
                                         or any(isinstance(parsed.get(k), list)
                                                for k in ("claims", "lessons", "result"))):
            doc = parsed
            break
    if doc is None:
        return []
    if isinstance(doc, list):
        return [d for d in doc if isinstance(d, dict)]
    if not isinstance(doc, dict):
        return []
    for key in ("claims", "lessons", "result"):
        if isinstance(doc.get(key), list):
            return [d for d in doc[key] if isinstance(d, dict)]
    return [doc] if doc.get("claim") else []


_RULE_SHAPE_RE = re.compile(
    r"\b(never|always|must|must not|do not|don't|avoid|prefer|use|check|verify|"
    r"run|keep|state|assert|require|fail|skip|record|treat|read|write|stage|"
    r"report|gate|block|refuse|name|add|set|ensure|before|after|when|if)\b",
    re.IGNORECASE,
)
_FIRST_PERSON_RE = re.compile(r"^\s*(i|we|my|our)\b", re.IGNORECASE)


def shape_problem(claim):
    """Why a claim is not a rule, or None when it reads like one.

    Deterministic and blind to the model: the reviewer's gate, not the
    model's self-assessment. Event telemetry is the family that already
    flooded the corpus (auto_harvest.NOISE_FAMILIES), so it is refused here
    before any heuristic runs.
    """
    text = (claim or "").strip()
    if len(text) < MIN_CLAIM_LEN:
        return "claim_too_short"
    if noise_reason(text):
        return "event_telemetry"
    if text.endswith("?"):
        return "question_not_rule"
    if _FIRST_PERSON_RE.search(text):
        return "first_person_narration"
    if len(set(word_set(text))) < 4:
        return "too_few_content_words"
    if not _RULE_SHAPE_RE.search(text):
        return "no_directive"
    return None


def conditions_for(claim):
    """Retrieval conditions: the claim's content words, capped."""
    return sorted(set(word_set(claim)))[:12]


def stage_claim(claim, evidence_ids, candidates_dir, reviewer="local-distill",
                confidence=0.6, rationale=""):
    """Write one staged candidate; returns (id, path) or None on a duplicate.

    Mirrors promote.write_candidates' record so the review tools and the
    queue read it unchanged. cluster_size 1 and a sub-threshold salience are
    the guard against auto_harvest promoting model output: it graduates only
    clusters of three or more.
    """
    conditions = conditions_for(claim)
    cid = pattern_id(claim, conditions)
    path = os.path.join(candidates_dir, f"{cid}.json")
    if os.path.exists(path):
        return None
    for sub in ("graduated", "rejected"):
        if os.path.exists(os.path.join(candidates_dir, sub, f"{cid}.json")):
            return None
    now = datetime.now(timezone.utc).isoformat()
    candidate = {
        "id": cid,
        "key": f"distill_{cid[:6]}",
        "name": f"distill_{cid[:6]}",
        "claim": claim,
        "conditions": conditions,
        "evidence_ids": evidence_ids,
        "cluster_size": 1,
        "canonical_salience": round(PROMOTION_THRESHOLD - 1.0, 2),
        "staged_at": now,
        "status": "staged",
        "decisions": [{"ts": now, "action": "staged", "reviewer": reviewer,
                       "notes": rationale[:200]}],
        "rejection_count": 0,
        "confidence": confidence,
        "source": "local-distill",
    }
    os.makedirs(candidates_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(candidate, handle, indent=2)
    os.replace(tmp, path)
    return cid, path


def distill(candidates_dir, entries, model=DEFAULT_MODEL, host=DEFAULT_HOST,
            limit=DEFAULT_WINDOW, dry_run=False, caller=_call_ollama,
            since=None, num_predict=DEFAULT_NUM_PREDICT):
    """One pass. Returns a summary dict; never raises on model failures."""
    episodes = select_window(entries, limit=limit, since=since)
    signal = [e for e in episodes if is_signal(e)]
    summary = {
        "window": len(episodes),
        "signal": len(signal),
        "claims": [],
        "staged": [],
        "skipped": [],
    }
    if len(signal) < MIN_SIGNAL:
        summary["skipped"].append(("window", "too_few_signal_episodes"))
        return summary

    # Curation order: genuine failures, then other high-pain signal, then the
    # remainder. The 2026-11-19 real-window run showed why - newest-first put
    # high-pain SUCCESS logs (telemetry) in all 20 slots and the model
    # correctly answered {}. A repeated failure is the newest-worthy signal
    # there is.
    ordered = [e for e in episodes if str(e.get("result") or "") == "failure"]
    ordered += [e for e in episodes
                if str(e.get("result") or "") != "failure" and is_signal(e)]
    ordered += [e for e in episodes if not is_signal(e)]
    used = ordered[:MAX_EPISODES_IN_PROMPT]
    reply = caller(model, host, build_prompt(used), num_predict)
    claims = parse_claims(reply)
    summary["claims"] = claims
    if not claims:
        summary["skipped"].append(("reply", "no_claims"))
        return summary

    lessons_md = os.path.join(os.path.dirname(candidates_dir), "semantic",
                              "LESSONS.md")
    lessons_text = ""
    if os.path.exists(lessons_md):
        with open(lessons_md, encoding="utf-8") as handle:
            lessons_text = handle.read()

    seen = set()
    for item in claims[:MAX_CLAIMS]:
        claim = str(item.get("claim") or "").strip()
        if not claim or claim in seen:
            continue
        seen.add(claim)
        problem = shape_problem(claim)
        if problem:
            summary["skipped"].append((claim[:60], problem))
            continue
        evidence = []
        raw = item.get("evidence")
        if isinstance(raw, list):
            for idx in raw:
                if isinstance(idx, int) and 0 <= idx < len(used):
                    evidence.append(used[idx].get("timestamp") or "")
        check = heuristic_check({"claim": claim, "cluster_size": 1}, lessons_text)
        if not check.get("passed"):
            summary["skipped"].append((claim[:60], "heuristic:" + ",".join(
                check.get("reasons", []))))
            continue
        try:
            confidence = round(float(item.get("confidence") or 0.6), 2)
        except (TypeError, ValueError):
            confidence = 0.6
        evidence = [e for e in evidence if e]
        rationale = (f"local-distill from {len(evidence)} episode(s) in a "
                     f"{len(used)}-episode window")
        if dry_run:
            summary["staged"].append((claim, "dry-run"))
            continue
        result = stage_claim(claim, evidence, candidates_dir,
                             confidence=confidence, rationale=rationale)
        if result:
            summary["staged"].append((result[0], claim))
        else:
            summary["skipped"].append((claim[:60], "duplicate"))
    return summary


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        description="Distil rule-shaped lesson candidates from the episodic window.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print claims; stage nothing")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help=f"episodes examined (default {DEFAULT_WINDOW})")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"ollama model (default {DEFAULT_MODEL})")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"ollama host (default {DEFAULT_HOST})")
    parser.add_argument("--num-predict", type=int, default=DEFAULT_NUM_PREDICT,
                        help="reply token budget; gpt-oss:20b reasons first, "
                             "so give it 3000 or more")
    args = parser.parse_args(argv)

    home = memory_home()
    episodic = os.path.join(home, "memory", "episodic", "AGENT_LEARNINGS.jsonl")
    candidates_dir = os.path.join(home, "memory", "candidates")
    if not os.path.exists(episodic):
        print(f"ERROR: no episodic log at {episodic}", file=sys.stderr)
        return 1

    entries = _rows(episodic)

    lock_fd, acquired = _claim_cycle_lock()
    if not acquired:
        print("another dream/harvest/distill holds the cycle lock; exiting")
        return 0
    try:
        summary = distill(candidates_dir, entries, model=args.model,
                          host=args.host, limit=args.window,
                          dry_run=args.dry_run, num_predict=args.num_predict)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)

    print(f"window: {summary['window']} episodes, {summary['signal']} signal")
    print(f"claims: {len(summary['claims'])}")
    print(f"staged: {len(summary['staged'])}")
    for cid, claim in summary["staged"][:10]:
        print(f"  {cid}: {claim[:110]}")
    if summary["skipped"]:
        print(f"skipped: {len(summary['skipped'])}")
        for key, why in summary["skipped"][:8]:
            print(f"  {why}: {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
