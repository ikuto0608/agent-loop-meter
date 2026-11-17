#!/usr/bin/env python3
"""agent-loop-meter hook. Reads a Claude Code hook payload on stdin and appends at most
one JSON line to $OUT (default ~/.claude/loop-events.jsonl). Never prints, never blocks.
Only the KIND and a short LABEL of a command are recorded -- never the command text.

Rules come from $LOOP_METER_CONFIG, else <repo>/loop-meter.config.json, else the built-in
defaults below. First matching rule wins."""
import json, os, re, sys, datetime

OUT = os.path.expanduser(os.environ.get("OUT", "~/.claude/loop-events.jsonl"))
HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_RULES = [
    {"match": r"\bmkdir\s+\S*\.lock\b", "kind": "lock_wait", "label": "lock"},
    {"match": r"\brmdir\s+\S*\.lock\b", "kind": "lock_free", "label": "lock"},
    {"match": r"\bplaywright\s+test\b", "kind": "test", "label": "playwright"},
    {"match": r"\bvitest\b", "kind": "test", "label": "vitest"},
    {"match": r"\bjest\b", "kind": "test", "label": "jest"},
    {"match": r"\bpytest\b", "kind": "test", "label": "pytest"},
    {"match": r"\bgo\s+test\b", "kind": "test", "label": "go test"},
    {"match": r"\bcargo\s+test\b", "kind": "test", "label": "cargo test"},
    {"match": r"\b(npm|yarn|pnpm)\s+(run\s+)?test\b", "kind": "test", "label": "npm test"},
    {"match": r"\bdocker\s+build\b", "kind": "build", "label": "docker build"},
    {"match": r"\bgh\s+pr\s+merge\b", "kind": "merge", "label": "pr merge"},
    {"match": r"\bgh\s+pr\s+create\b", "kind": "pr", "label": "pr create"},
    {"match": r"\bgit\s+push\b", "kind": "push", "label": "git push"},
]

def load_rules():
    path = os.environ.get("LOOP_METER_CONFIG") or os.path.join(HERE, "..", "loop-meter.config.json")
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            rules = json.load(f).get("rules")
        if isinstance(rules, list):
            return rules
    except Exception:
        pass
    return DEFAULT_RULES

def classify_bash(cmd, rules):
    for r in rules:
        try:
            if re.search(r["match"], cmd or ""):
                return r.get("kind") or "other", r.get("label") or r.get("kind") or ""
        except Exception:
            continue
    return None, None

def main():
    try:
        p = json.load(sys.stdin)
    except Exception:
        return
    ev = p.get("hook_event_name", "")
    agent = p.get("agent_id") or p.get("subagent_id") or "main"
    rec = None
    if ev == "SubagentStart":
        rec = {"kind": "agent_start", "label": p.get("agent_type") or p.get("description") or ""}
    elif ev == "SubagentStop":
        rec = {"kind": "agent_stop", "label": p.get("agent_type") or ""}
    elif ev in ("PreToolUse", "PostToolUse"):
        tool = p.get("tool_name", "")
        ti = p.get("tool_input") or {}
        if tool == "Agent" and ev == "PreToolUse":
            rec = {"kind": "agent_spawn", "label": (ti.get("description") or "")[:60]}
        elif tool == "Bash":
            k, label = classify_bash(ti.get("command", ""), load_rules())
            if k:
                start = ev == "PreToolUse"
                if k == "lock_wait":  # acquire command: waiting before, holding after
                    rec = {"kind": "lock_wait" if start else "lock_got", "label": label}
                elif k == "test":
                    rec = {"kind": "test_start" if start else "test_end", "label": label,
                           "bg": bool(ti.get("run_in_background"))}
                elif not start:
                    rec = {"kind": k, "label": label}
    if not rec:
        return
    rec = {"t": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "agent": agent, **rec}
    try:
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

if __name__ == "__main__":
    main()
