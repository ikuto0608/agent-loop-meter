#!/usr/bin/env python3
"""Turn the loop-events JSONL into a per-agent CSV (one row = one agent loop), same columns as
examples/small-shop-site/before-tasks.csv, with source=measured. Stdlib only.

  python3 tools/summarize.py [--log path] [--since 2026-11-17] [--out tasks.csv|-] [--include-open]
                             [--settled-min 20] [--last 50]

Stage rules (the same state machine as the dashboard). Default stage is impl. Priority when
several apply: wait > test > review > deploy > impl.
  lock_wait..lock_got (or lock_free)  -> wait
  test_start..test_end                -> test (a background test, bg=true, lasts until the agent's next event)
  script lock_got..lock_free          -> test, and wins over the hook's test_start/test_end: a command run in the
                                         background ends at once for the hook, so the script's own span is the real one
  review_start..review_end            -> review
  deploy_start..deploy_end            -> deploy
  after the first merge: up to the last release/release_check/deploy_end event (the agent's own, or the first
                         release-watch "release" line after the merge) -> deploy;
                         after that (or when there is no such event) -> other
An outside script can write its own lock events ("src":"script", "agent":"ext" unless it sets LOOP_METER_AGENT).
They are handed to the agent that has a test with the same label open at that time (see attach_ext), so the
script's queueing counts as wait and is taken out of that test (wait > test). A bash test run in the background
keeps counting as test after the lock is got, until the script's lock_free.
Time counts only between agent_start and agent_stop (a resumed agent adds each stretch). The gap between a stop
and the same agent's next event is test when a background test (bg=true) was still open at the stop (test_end with
bg and nothing after it), or when the agent still held a lock it took itself (hook lock_got, no lock_free: its gate
runs on), else idle: counted nowhere. --include-main adds one row for the main agent.
"""
import argparse, csv, datetime, json, os, statistics, sys

COLS = ["task", "category", "started", "pr_created", "merged", "released", "total_min", "impl_min",
        "wait_min", "test_min", "full_e2e_min", "review_min", "deploy_min", "other_min", "source"]
STAGES = ["impl_min", "wait_min", "test_min", "review_min", "deploy_min", "other_min"]
RELEASE_KINDS = ("release", "release_check", "deploy_end")
# Event sources that are not agents (hook "main", release-watch "release"/old "heroku", "watch").
# They only feed the totals, never a row.
NON_AGENTS = ("main", "release", "heroku", "watch")


def load(path):
    evs = []
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        for i, line in enumerate(f):
            try:
                e = json.loads(line)
                e["_t"] = datetime.datetime.fromisoformat(e["t"])
                e["_i"] = i
                evs.append(e)
            except Exception:
                continue
    evs.sort(key=lambda e: (e["_t"], e["_i"]))
    return evs


EXT = "ext"


def attach_ext(evs):
    """Give the lock events a script writes itself ("src":"script", agent "ext") to the agent whose test with
    the same label (prefix) is open at that time. One wait claims one test (first started first); got/free follow
    their wait by the script's "run" id, else the oldest open wait. Events nobody can own are dropped."""
    open_test = {}   # agent -> [label, claimed]
    by_run, waits = {}, []   # run id -> agent ; agents with an open ext wait, oldest first
    out = []
    for e in evs:
        k, a = e.get("kind"), e.get("agent")
        if a == EXT and e.get("src") == "script" and k in ("lock_wait", "lock_got", "lock_free"):
            run = e.get("run")
            if k == "lock_wait":
                label = e.get("label") or ""
                if run is not None and run in by_run:  # the same script waits again (e.g. after a remote fallback)
                    waits.append(by_run[run]); out.append({**e, "agent": by_run[run]}); continue
                c = [(ag, v) for ag, v in open_test.items() if not v[1] and v[0].startswith(label)]
                if not c: continue
                ag = c[0][0]
                open_test[ag][1] = True
                if run is not None: by_run[run] = ag
                waits.append(ag)
            else:
                ag = by_run.get(run) if run is not None else None
                if ag is None and waits: ag = waits[0]
                if ag is None: continue
                if ag in waits: waits.remove(ag)
            out.append({**e, "agent": ag})
            continue
        out.append(e)
        if a in (None, EXT, *NON_AGENTS): continue
        if k == "test_start": open_test[a] = [e.get("label") or "", False]
        elif k == "test_end" and not e.get("bg"): open_test.pop(a, None)
        elif k == "agent_stop": open_test.pop(a, None)
    return out


def stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def measure(events, desc=None, releases=()):
    """events: one agent's events in time order. desc: the Agent-tool description tied to this agent
    (see summarize). Returns a row dict, or None without agent_start."""
    first = next((e for e in events if e["kind"] in ("agent_spawn", "agent_start")), None)
    if first is None:
        return None
    merged = next((e["_t"] for e in events if e["kind"] == "merge"), None)
    rel = [e["_t"] for e in events if e["kind"] in RELEASE_KINDS and merged and e["_t"] >= merged]
    # the release-watch writes "release" lines as its own source: the first one after this agent's merge (and
    # before the agent's last event) is this merge going live
    glob = [r for r in releases if merged and merged <= r <= events[-1]["_t"]]
    if glob: rel.append(min(glob))
    last_rel = max(rel) if rel else None
    b = dict.fromkeys(STAGES, 0.0)
    e2e = 0.0
    active = False
    wait = test = review = deploy = bg = False
    label = ""
    last = None
    category = ""
    stopped = False
    script_wait = False
    cur = -1
    held = False   # an outside script run is open (lock_wait..lock_free): its span is the truth, whatever the hook said
    slabel = ""
    # test_start events followed by a script lock_wait (before the agent's next test_start/stop): the script times that test
    covered, pend = set(), None
    for i, ev_ in enumerate(events):
        if ev_["kind"] == "test_start": pend = i
        elif ev_["kind"] == "agent_stop": pend = None
        elif ev_["kind"] == "lock_wait" and ev_.get("src") == "script" and pend is not None: covered.add(pend)
    gap = False   # stopped while a background test was still open: the time until the agent's next event is test
    hook_lock = False   # the agent took a lock itself (hook lock_wait/lock_got, no lock_free yet)
    lockgap = False     # this gap is "stopped while holding that lock": its gate keeps running -> test
    for idx, e in enumerate(events):
        t = e["_t"]
        if (active or gap) and last is not None:
            dt = (t - last).total_seconds() / 60
            if wait: b["wait_min"] += dt
            elif test:
                b["test_min"] += dt
                if any(w in label.lower() for w in ("e2e", "full")): e2e += dt
            elif review: b["review_min"] += dt
            elif deploy: b["deploy_min"] += dt
            elif merged and t > merged and last >= merged:
                if last_rel and last < last_rel < t:   # the release lands inside this stretch: split it
                    d = (last_rel - last).total_seconds() / 60
                    b["deploy_min"] += d; b["other_min"] += dt - d
                else: b["deploy_min" if last_rel and last < last_rel else "other_min"] += dt
            else: b["impl_min"] += dt
        last = t
        k = e["kind"]
        script = e.get("src") == "script"
        if k == "agent_stop" and (bg or held): gap = True   # the script (or bg test) still runs after the agent stopped
        elif k == "agent_stop" and hook_lock and e.get("label") != "stopped":
            gap = lockgap = True   # handed back while holding the lock: its gate runs until the agent's next event
        elif gap and not script:
            gap = False
            if lockgap: test = lockgap = False
        if bg and k not in ("test_start", "test_end") and not script and not gap:
            test = bg = False
        if k in ("agent_spawn", "agent_start"):
            if k == "agent_start" or not category: category = e.get("label") or category
            if k == "agent_start": active = True
        elif k == "agent_stop":
            active = False; stopped = True
            review = deploy = False
            if not gap: wait = test = bg = False
            if lockgap: wait = False; test = True; label = "gate"
        elif k == "lock_wait":
            wait = True; script_wait = script
            if not script: hook_lock = True
            if script: held = True; slabel = e.get("label") or ""
        elif k in ("lock_got", "lock_free"):
            wait = False
            if not script: hook_lock = k == "lock_got"
            if script and k == "lock_got": test = True; label = e.get("label") or label   # got: the script's test runs
            elif script:   # lock_free: the script says the test is over
                held = test = bg = False
                if not active: gap = False
        elif k == "test_start": test = True; label = e.get("label") or ""; cur = idx
        elif k == "test_end":
            if e.get("bg"): bg = cur not in covered   # a covered test ends with the script's lock_free, not the next event
            else:
                test = held
                if held and slabel and (e.get("label") or "").startswith(slabel):
                    held = test = wait = script_wait = False   # the script died without a lock_free
        elif k == "review_start": review = True
        elif k == "review_end": review = False
        elif k == "deploy_start": deploy = True
        elif k == "deploy_end": deploy = False
    total = sum(b.values())
    pr = next((e["_t"] for e in events if e["kind"] == "pr"), None)
    name = desc or (f"{category} · {events[0]['agent'][:4]}" if category else "agent")
    row = {"task": events[0]["agent"], "category": name.replace(",", " "),
           "started": stamp(first["_t"]), "pr_created": stamp(pr), "merged": stamp(merged),
           "released": stamp(last_rel), "total_min": round(total, 1), "full_e2e_min": round(e2e, 1),
           "source": "measured", "_stopped": stopped, "_start": first["_t"]}
    row.update({k: round(v, 1) for k, v in b.items()})
    return row


MAIN_NAME = "control tower (main)"


STALE_HOURS = 6.0


def settled(events, end, settled_min):
    """True when this agent's loop is over: a final mark after its last agent_start (agent_report from
    SubagentHandback, or a TaskStop "stopped"), or its last event is more than settled_min before `end`.
    An agent that handed back (agent_stop) may still be busy behind the scenes and come back."""
    if not settled_min or end is None: return True
    starts = [i for i, e in enumerate(events) if e["kind"] == "agent_start"]
    tail = events[starts[-1]:] if starts else events
    if any(e["kind"] == "agent_report" or (e["kind"] == "agent_stop" and e.get("label") == "stopped") for e in tail):
        return True
    return (end - events[-1]["_t"]).total_seconds() > settled_min * 60


def summarize(evs, since=None, include_open=False, include_main=False, stale_hours=STALE_HOURS, now=None, settled_min=0):
    """Names: an agent_named event (PostToolUse on the Agent tool: description + the real agent id) wins. Only an
    agent nobody named falls back to "the next new agent id takes the oldest agent_spawn description", and a
    spawn with iso=remote (cloud) never enters that queue. An agent with no event for stale_hours (relative to
    now, default: the last event in the log) and no agent_stop is stale: dropped even with include_open."""
    by, descs, queue = {}, {}, []
    evs = attach_ext(evs)
    named = {e["target"]: e.get("label") or "" for e in evs if e.get("kind") == "agent_named" and e.get("target")}
    end = now or (evs[-1]["_t"] if evs else None)
    mains = [e for e in evs if e.get("agent") == "main"]
    if include_main and mains:  # the main agent has no agent_start/stop: it runs from its first to its last event
        t0, t1 = mains[0], mains[-1]
        by["main"] = [{**t0, "kind": "agent_start", "label": MAIN_NAME}] + mains + [{**t1, "kind": "agent_stop", "label": ""}]
        descs["main"] = MAIN_NAME
    for e in evs:
        # agent_spawn (PreToolUse Agent) queues its description; the next new agent id takes the oldest one.
        if e.get("kind") == "agent_spawn" and e.get("label") and e.get("iso") != "remote": queue.append(e["label"])
        if e.get("kind") == "agent_named":   # its spawn is accounted for: take that description out of the queue
            if e.get("label") in queue: queue.remove(e["label"])
            continue
        if e.get("agent") in (None, *NON_AGENTS): continue
        if e["agent"] not in by and queue and e["agent"] not in named: descs[e["agent"]] = queue.pop(0)
        by.setdefault(e["agent"], []).append(e)
    releases = sorted(e["_t"] for e in evs if e.get("kind") == "release")
    rows, skipped_open, skipped_nostart = [], 0, 0
    for events in by.values():
        aid = events[0]["agent"]
        r = measure(events, named.get(aid) or descs.get(aid), releases)
        if r is None: skipped_nostart += 1; continue
        if since and r["_start"].date() < since: continue
        if not settled(events, end, settled_min): skipped_open += 1; continue
        if not r["_stopped"] and (not include_open or (end and stale_hours and (end - events[-1]["_t"]).total_seconds() > stale_hours * 3600)):
            skipped_open += 1; continue
        rows.append(r)
    rows.sort(key=lambda r: r["_start"])
    return rows, skipped_open, skipped_nostart


def write_csv(rows, out):
    w = csv.DictWriter(out, fieldnames=COLS, extrasaction="ignore", lineterminator="\n")
    w.writeheader(); w.writerows(rows)


def report(rows, skipped_open, skipped_nostart, out=sys.stdout):
    print(f"{len(rows)} loops measured (open skipped: {skipped_open}, without agent_start: {skipped_nostart})", file=out)
    if not rows: return
    totals = [r["total_min"] for r in rows]
    print(f"median {statistics.median(totals):.1f} min, longest {max(totals):.1f} min", file=out)
    grand = sum(sum(r[s] for s in STAGES) for r in rows) or 1
    print("stage share: " + ", ".join(f"{s[:-4]} {sum(r[s] for r in rows) / grand * 100:.0f}%" for s in STAGES), file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", default=os.environ.get("OUT", "~/.claude/loop-events.jsonl"))
    ap.add_argument("--since", help="only loops started on or after this date (YYYY-MM-DD)")
    ap.add_argument("--out", help="CSV path (default: stdout is the summary only)")
    ap.add_argument("--include-main", action="store_true", help='add one row for the main agent ("control tower")')
    ap.add_argument("--stale-hours", type=float, default=STALE_HOURS, help="an open agent silent this long is stale (default 6)")
    ap.add_argument("--include-open", action="store_true", help="also count agents with no agent_stop")
    ap.add_argument("--settled-min", type=float, default=0,
                    help="skip an agent whose last event is less than this many minutes ago (vs now) and that has no "
                         "final mark (agent_report / TaskStop): it may still be busy in the background (default 0 = off)")
    ap.add_argument("--last", type=int, default=0, help="keep only the newest N loops, newest first (default: all, oldest first)")
    a = ap.parse_args(argv)
    since = datetime.date.fromisoformat(a.since) if a.since else None
    now = datetime.datetime.now().astimezone() if a.settled_min else None
    rows, so, sn = summarize(load(a.log), since, a.include_open, a.include_main, a.stale_hours, now, a.settled_min)
    if a.last > 0: rows = rows[::-1][:a.last]
    if a.out == "-":   # CSV only, on stdout (the dashboard reads this)
        write_csv(rows, sys.stdout); return
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="") as f: write_csv(rows, f)
        print(f"wrote {a.out}")
    report(rows, so, sn)


if __name__ == "__main__":
    main()
