#!/usr/bin/env python3
"""Turn the loop-events JSONL into a per-agent CSV (one row = one agent loop), same columns as
examples/small-shop-site/before-tasks.csv, with source=measured. Stdlib only.

  python3 tools/summarize.py [--log path] [--since 2026-11-17] [--out tasks.csv] [--include-open]

Stage rules (the same state machine as the dashboard). Default stage is impl. Priority when
several apply: wait > test > review > deploy > impl.
  lock_wait..lock_got (or lock_free)  -> wait
  test_start..test_end                -> test (a background test, bg=true, lasts until the agent's next event)
  review_start..review_end            -> review
  deploy_start..deploy_end            -> deploy
  after the first merge: up to the last release/release_check/deploy_end event -> deploy;
                         after that (or when there is no such event) -> other
Time counts only between agent_start and agent_stop (a resumed agent adds each stretch).
"""
import argparse, csv, datetime, json, os, statistics, sys

COLS = ["task", "category", "started", "pr_created", "merged", "released", "total_min", "impl_min",
        "wait_min", "test_min", "full_e2e_min", "review_min", "deploy_min", "other_min", "source"]
STAGES = ["impl_min", "wait_min", "test_min", "review_min", "deploy_min", "other_min"]
RELEASE_KINDS = ("release", "release_check", "deploy_end")


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


def stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def measure(events):
    """events: one agent's events in time order. Returns a row dict, or None without agent_start."""
    first = next((e for e in events if e["kind"] in ("agent_spawn", "agent_start")), None)
    if first is None:
        return None
    merged = next((e["_t"] for e in events if e["kind"] == "merge"), None)
    rel = [e["_t"] for e in events if e["kind"] in RELEASE_KINDS and merged and e["_t"] >= merged]
    last_rel = max(rel) if rel else None
    b = dict.fromkeys(STAGES, 0.0)
    e2e = 0.0
    active = False
    wait = test = review = deploy = bg = False
    label = ""
    last = None
    category = ""
    stopped = False
    for e in events:
        t = e["_t"]
        if active and last is not None:
            dt = (t - last).total_seconds() / 60
            if wait: b["wait_min"] += dt
            elif test:
                b["test_min"] += dt
                if any(w in label.lower() for w in ("e2e", "full")): e2e += dt
            elif review: b["review_min"] += dt
            elif deploy: b["deploy_min"] += dt
            elif merged and t > merged and last >= merged:
                b["deploy_min" if last_rel and last < last_rel else "other_min"] += dt
            else: b["impl_min"] += dt
        last = t
        k = e["kind"]
        if bg and k not in ("test_start", "test_end"):
            test = bg = False
        if k in ("agent_spawn", "agent_start"):
            if k == "agent_start" or not category: category = e.get("label") or category
            if k == "agent_start": active = True
        elif k == "agent_stop":
            active = False; stopped = True
            wait = test = review = deploy = bg = False
        elif k == "lock_wait": wait = True
        elif k in ("lock_got", "lock_free"): wait = False
        elif k == "test_start": test = True; label = e.get("label") or ""
        elif k == "test_end":
            if e.get("bg"): bg = True
            else: test = False
        elif k == "review_start": review = True
        elif k == "review_end": review = False
        elif k == "deploy_start": deploy = True
        elif k == "deploy_end": deploy = False
    total = sum(b.values())
    pr = next((e["_t"] for e in events if e["kind"] == "pr"), None)
    row = {"task": events[0]["agent"], "category": (category or "agent").replace(",", " "),
           "started": stamp(first["_t"]), "pr_created": stamp(pr), "merged": stamp(merged),
           "released": stamp(last_rel), "total_min": round(total, 1), "full_e2e_min": round(e2e, 1),
           "source": "measured", "_stopped": stopped, "_start": first["_t"]}
    row.update({k: round(v, 1) for k, v in b.items()})
    return row


def summarize(evs, since=None, include_open=False):
    by = {}
    for e in evs:
        if e.get("agent") in (None, "main", "release"): continue
        by.setdefault(e["agent"], []).append(e)
    rows, skipped_open, skipped_nostart = [], 0, 0
    for events in by.values():
        r = measure(events)
        if r is None: skipped_nostart += 1; continue
        if since and r["_start"].date() < since: continue
        if not r["_stopped"] and not include_open: skipped_open += 1; continue
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
    ap.add_argument("--include-open", action="store_true", help="also count agents with no agent_stop")
    a = ap.parse_args(argv)
    since = datetime.date.fromisoformat(a.since) if a.since else None
    rows, so, sn = summarize(load(a.log), since, a.include_open)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="") as f: write_csv(rows, f)
        print(f"wrote {a.out}")
    report(rows, so, sn)


if __name__ == "__main__":
    main()
