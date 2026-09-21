// Batch worker for fast-jev-compaction. Runs under node; bridges the brain's
// stdlib python tools to the fork's npm library (see jev_compact.py).
//
// The brain must not need a node toolchain to stay honest: this worker is the
// only process that imports the library, reads one JSON request on stdin, and
// prints one JSON response on stdout. No argv, no chatter on stdout.
//
// Request:  {"messages": [Message, ...], "options": {keepThreshold, ...},
//            "dist": "optional path to fast-jev dist/index.js"}
// Response: {"result": {"messages", "stats", "reduction"}} or {"error": str}
//
// Message shape (subset of Claude Code's SessionMessage):
//   {"role": "user"|"assistant", "text": str,
//    "toolUses": [{"tool_use_id", "tool", "input"}],
//    "toolResults": [{"tool_use_id", "text"}]}
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const DEFAULT_DIST = join(homedir(), 'dev-repo/fast-jev-compaction/dist/index.js');

function fail(error) {
  process.stdout.write(JSON.stringify({ error }) + '\n');
  process.exit(2);
}

let payload;
try {
  payload = JSON.parse(readFileSync(0, 'utf8'));
} catch (err) {
  fail(`bad request JSON: ${err.message}`);
}

const dist = payload.dist || process.env.JEV_DIST || DEFAULT_DIST;
let mod;
try {
  mod = await import(pathToFileURL(dist).href);
} catch (err) {
  fail(`cannot load fast-jev library at ${dist}: ${err.message}`);
}

const apiKey = payload.apiKey || process.env.TYPESAFE_API_KEY;
if (!apiKey) {
  fail('TYPESAFE_API_KEY missing — export it in the calling shell; never pass it on a command line');
}

const messages = payload.messages;
if (!Array.isArray(messages) || messages.length === 0) {
  fail('messages must be a non-empty array');
}

const options = { apiKey, ...(payload.options || {}) };
try {
  const result = await mod.compactMessages(messages, options);
  const reduction = typeof mod.reductionRatio === 'function' ? mod.reductionRatio(result) : null;
  process.stdout.write(JSON.stringify({ result: { ...result, reduction } }) + '\n');
} catch (err) {
  fail(`compaction failed: ${err.message}`);
}
