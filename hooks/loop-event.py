#!/usr/bin/env python3
"""Loop-event logger (meetup 2026-11-17). Reads a Claude Code hook payload on stdin and
appends at most one line to $OUT (default ~/.claude/loop-events.jsonl). Never prints, never blocks.
Only the KIND of a command is recorded — never the command text (no secrets)."""
import json, os, re, sys, datetime

OUT = os.path.expanduser(os.environ.get("OUT", "~/.claude/loop-events.jsonl"))

def classify_bash(cmd: str):
    c = cmd or ""
    if re.search(r"mkdir\s+\S*heavy\.lock", c):
        return "lock", "heavy.lock"
    if re.search(r"rmdir\s+\S*heavy\.lock", c):
        return "lock_free", "heavy.lock"
    if "full-e2e.sh" in c:
        full = "--full" in c
        return "test", "full-e2e" + (" full" if full else "")
    if re.search(r"prod-smoke|test:e2e:prod", c):
        return "test", "prod-smoke"
    if re.search(r"live-prod", c):
        return "test", "live-prod"
    if re.search(r"playwright test", c):
        return "test", "playwright"
    if re.search(r"\bjest\b|yarn (-s )?test\b|test:changed", c):
        return "test", "jest"
    if re.search(r"fast-gate", c):
        return "test", "fast-gate"
    if re.search(r"gh pr merge", c):
        return "merge", "pr merge"
    if re.search(r"gh pr create", c):
        return "pr", "pr create"
    if re.search(r"heroku releases\b", c):
        return "release_check", "releases"
    return None, None

def main():
    try:
        p = json.load(sys.stdin)
    except Exception:
        return
    ev = p.get("hook_event_name", "")
    agent = p.get("agent_id") or p.get("subagent_id") or "main"
    rec = None
    if ev in ("SubagentStart",):
        rec = {"kind": "agent_start", "label": p.get("agent_type") or p.get("description") or ""}
    elif ev == "SubagentStop":
        rec = {"kind": "agent_stop", "label": p.get("agent_type") or ""}
    elif ev in ("PreToolUse", "PostToolUse"):
        tool = p.get("tool_name", "")
        ti = p.get("tool_input") or {}
        if tool == "Agent" and ev == "PreToolUse":
            rec = {"kind": "agent_spawn", "label": (ti.get("description") or "")[:60]}
        elif tool == "Bash":
            k, label = classify_bash(ti.get("command", ""))
            if k:
                phase = "start" if ev == "PreToolUse" else "end"
                bg = bool(ti.get("run_in_background"))
                if k == "lock":
                    rec = {"kind": "lock_wait" if phase == "start" else "lock_got", "label": label}
                elif k == "test":
                    rec = {"kind": f"test_{phase}", "label": label, "bg": bg}
                elif phase == "end":
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
