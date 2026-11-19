import io, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
import summarize as S

D = "2026-11-17T"
def ev(hm, agent, kind, label="", **kw):
    return {"t": f"{D}{hm}:00+09:00", "agent": agent, "kind": kind, "label": label, **kw}

# A: 0..60 min. impl 10, wait 5 (lock), impl 5, test 15 (e2e), impl 5, review 8, impl 2 -> pr 50, merge 52?
# Timeline (minutes from 10:00):
#  0 start | 10 lock_wait | 15 lock_got | 20 test_start(playwright e2e) | 35 test_end | 40 pr | 41 review_start
#  49 review_end | 50 merge | 55 release_check | 58 agent_stop (58-55=3 after last release -> other)
#  impl: 10 + 5 + 5 + 1 + 1 = 22 ; wait 5 ; test 15 (full_e2e 15) ; review 8 ; deploy 5 (50..55) ; other 3
A = [ev("10:00","A","agent_start","general-purpose"), ev("10:10","A","lock_wait","lock"), ev("10:15","A","lock_got","lock"),
     ev("10:20","A","test_start","playwright e2e"), ev("10:35","A","test_end","playwright e2e"), ev("10:40","A","pr","pr create"),
     ev("10:41","A","review_start","review"), ev("10:49","A","review_end","review"), ev("10:50","A","merge","pr merge"),
     ev("10:55","A","release_check","heroku releases"), ev("10:58","A","agent_stop")]
# B: bg test 10:00 start, test_end(bg) at 10:00, next event 10:12 -> test 12; then impl 3; explicit deploy span 5; stop. no merge.
B = [ev("11:00","B","agent_start","reviewer"), ev("11:00","B","test_start","pytest",bg=True), ev("11:00","B","test_end","pytest",bg=True),
     ev("11:12","B","push","git push"), ev("11:15","B","deploy_start","deploy"), ev("11:20","B","deploy_end","deploy"), ev("11:20","B","agent_stop")]
# C: merge but no release event -> after merge is other (not deploy). impl 6, other 4.
C = [ev("12:00","C","agent_start","worker"), ev("12:06","C","merge","pr merge"), ev("12:10","C","agent_stop")]
# D: never stopped (open). E: stop without start. main events ignored.
D_ = [ev("13:00","D","agent_start","open"), ev("13:05","D","push","git push")]
E = [ev("13:00","E","agent_stop")]
M = [ev("10:56","main","release_check","heroku releases")]

# X: a test (street-e2e) 14:00..14:30 holds an ext script wait 14:02..14:12 (run 7); Y bg test: test_end(bg) at once,
# script waits 15:03..15:08 and the test keeps going until Y's next event at 15:20.
def sx(hm, kind, run, label="street-e2e"):
    return {"t": f"{D}{hm}:00+09:00", "agent": "ext", "kind": kind, "label": label, "src": "script", "run": run}
X = [ev("14:00","X","agent_start","worker"), ev("14:00","X","test_start","street-e2e"),
     sx("14:02","lock_wait",7), sx("14:12","lock_got",7), sx("14:30","lock_free",7),
     ev("14:30","X","test_end","street-e2e"), ev("14:35","X","agent_stop")]
Y = [ev("15:00","Y","agent_start","worker"), ev("15:00","Y","test_start","street-e2e",bg=True), ev("15:00","Y","test_end","street-e2e",bg=True),
     sx("15:03","lock_wait",8), sx("15:08","lock_got",8), sx("15:25","lock_free",8),
     ev("15:20","Y","push","git push"), ev("15:30","Y","agent_stop")]

class Ext(unittest.TestCase):
    def rows(self, *groups):
        evs = sorted(sum(groups, []), key=lambda e: e["t"])
        rows, _, _ = S.summarize([dict(e, _t=S.datetime.datetime.fromisoformat(e["t"]), _i=i) for i, e in enumerate(evs)])
        return {r["task"]: r for r in rows}

    def test_wait_is_taken_out_of_test(self):
        x = self.rows(X)["X"]
        self.assertEqual((x["wait_min"], x["test_min"], x["impl_min"], x["total_min"]), (10, 20, 5, 35))
        self.assertEqual(x["full_e2e_min"], 20)

    def test_background_test_keeps_going_after_wait(self):
        y = self.rows(Y)["Y"]
        self.assertEqual((y["wait_min"], y["test_min"], y["impl_min"]), (5, 20, 5))  # 3 + 15:08..15:25 (the script wins over the push at 15:20)

    def test_two_runs_each_own_wait(self):
        X2 = [dict(e, agent="Z") if e["agent"] == "X" else e for e in X]
        X2 = [dict(e, run=9) if e["agent"] == "ext" else e for e in X2]
        r = self.rows(X, X2)
        self.assertEqual((r["X"]["wait_min"], r["Z"]["wait_min"]), (10, 10))

# G: bg test open at the stop -> the 20 min until the next start are test. H: no bg test -> the gap is idle.
G = [ev("16:00","G","agent_start","w"), ev("16:00","G","test_start","street-e2e",bg=True), ev("16:00","G","test_end","street-e2e",bg=True),
     ev("16:05","G","agent_stop"), ev("16:25","G","agent_start","w"), ev("16:30","G","agent_stop")]
H = [ev("16:00","H","agent_start","w"), ev("16:05","H","agent_stop"), ev("16:25","H","agent_start","w"), ev("16:30","H","agent_stop")]
# M: main runs a test 17:00..17:10 (non-bg) and a push at 17:20.
M2 = [ev("17:00","main","test_start","jest"), ev("17:10","main","test_end","jest"), ev("17:20","main","merge","pr merge")]

# W: the hook sees the bg test end at once (0.1 min); the script's lock_wait 20:00..20:10 and lock_got..lock_free 20:10..20:32 are the truth.
W = [ev("20:00","W","agent_start","worker"), ev("20:00","W","test_start","street-e2e",bg=True), ev("20:00","W","test_end","street-e2e",bg=True),
     sx("20:00","lock_wait",11), sx("20:10","lock_got",11), ev("20:20","W","push","git push"), sx("20:32","lock_free",11),
     ev("20:35","W","agent_stop")]
# V: same, but the agent stops while the script still holds the lock: the time up to lock_free is still test.
V = [ev("21:00","V","agent_start","worker"), ev("21:00","V","test_start","street-e2e",bg=True), ev("21:00","V","test_end","street-e2e",bg=True),
     sx("21:00","lock_wait",12), sx("21:04","lock_got",12), ev("21:06","V","agent_stop"), sx("21:30","lock_free",12),
     ev("21:50","V","agent_start","worker"), ev("21:52","V","agent_stop")]

class Script(unittest.TestCase):
    def rows(self, *groups):
        evs = sorted(sum(groups, []), key=lambda e: e["t"])
        rows, _, _ = S.summarize([dict(e, _t=S.datetime.datetime.fromisoformat(e["t"]), _i=i) for i, e in enumerate(evs)])
        return {r["task"]: r for r in rows}

    def test_script_span_wins_over_bg_hook(self):
        w = self.rows(W)["W"]
        self.assertEqual((w["wait_min"], w["test_min"], w["impl_min"], w["full_e2e_min"]), (10, 22, 3, 22))

    def test_script_span_outlives_agent_stop(self):
        v = self.rows(V)["V"]
        self.assertEqual((v["wait_min"], v["test_min"], v["impl_min"]), (4, 26, 2))

class Gap(unittest.TestCase):
    def rows(self, *groups, **kw):
        evs = sorted(sum(groups, []), key=lambda e: e["t"])
        rows, _, _ = S.summarize([dict(e, _t=S.datetime.datetime.fromisoformat(e["t"]), _i=i) for i, e in enumerate(evs)], **kw)
        return {r["task"]: r for r in rows}

    def test_background_gap_is_test(self):
        g = self.rows(G)["G"]
        self.assertEqual((g["test_min"], g["impl_min"], g["total_min"]), (25, 5, 30))

    def test_gap_without_background_test_is_idle(self):
        h = self.rows(H)["H"]
        self.assertEqual((h["total_min"], h["test_min"], h["other_min"]), (10, 0, 0))

    def test_main_row_only_with_option(self):
        self.assertNotIn("main", self.rows(M2))
        m = self.rows(M2, include_main=True)["main"]
        self.assertEqual((m["category"], m["test_min"], m["total_min"]), ("control tower (main)", 10, 20))

class T(unittest.TestCase):
    def rows(self, **kw):
        evs = sorted(A + B + C + D_ + E + M, key=lambda e: e["t"])
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(e) for e in evs) + "\n\nnot json\n")
        try: loaded = S.load(f.name)
        finally: os.unlink(f.name)
        rows, so, sn = S.summarize(loaded, **kw)
        return {r["task"]: r for r in rows}, so, sn

    def test_a(self):
        r, so, sn = self.rows(); a = r["A"]
        self.assertEqual((so, sn), (1, 1))
        self.assertEqual([a[k] for k in ("impl_min","wait_min","test_min","full_e2e_min","review_min","deploy_min","other_min")],
                         [22, 5, 15, 15, 8, 5, 3])
        self.assertEqual(a["total_min"], 58)
        self.assertEqual((a["started"], a["pr_created"], a["merged"], a["released"]),
                         ("2026-11-17 10:00","2026-11-17 10:40","2026-11-17 10:50","2026-11-17 10:55"))
        self.assertEqual((a["category"], a["source"]), ("general-purpose · A", "measured"))

    def test_bg_and_span_deploy(self):
        b = self.rows()[0]["B"]
        self.assertEqual((b["test_min"], b["impl_min"], b["deploy_min"], b["full_e2e_min"], b["total_min"]), (12, 3, 5, 0, 20))

    def test_merge_without_release_is_not_deploy(self):
        c = self.rows()[0]["C"]
        self.assertEqual((c["impl_min"], c["deploy_min"], c["other_min"]), (6, 0, 4))

    def test_open_and_since(self):
        r, _, _ = self.rows(include_open=True)
        self.assertEqual(r["D"]["total_min"], 5)
        r, _, _ = self.rows(since=S.datetime.date(2026, 11, 18))
        self.assertEqual(r, {})

    def test_csv_and_report(self):
        r = self.rows()[0]
        out = io.StringIO(); S.write_csv(list(r.values()), out)
        self.assertEqual(out.getvalue().splitlines()[0], ",".join(S.COLS))
        rep = io.StringIO(); S.report(list(r.values()), 1, 1, rep)
        self.assertIn("3 loops measured", rep.getvalue())
        self.assertIn("median 20.0 min", rep.getvalue())

class Pairing(unittest.TestCase):
    def test_spawn_queue_in_time_order(self):
        evs = [ev("09:00","main","agent_spawn","fix header"), ev("09:01","main","agent_spawn","write tests"),
               ev("09:02","a1e9x","agent_start","general-purpose"), ev("09:03","b2f8y","agent_start","general-purpose"),
               ev("09:05","a1e9x","agent_stop"), ev("09:06","b2f8y","agent_stop"),
               ev("09:07","heroku","release_check","x"), ev("09:07","release","release","x"), ev("09:07","watch","release","x"),
               ev("09:08","c3d7z","agent_start","reviewer"), ev("09:09","c3d7z","agent_stop")]
        for i, e in enumerate(evs): e["_t"] = S.datetime.datetime.fromisoformat(e["t"]); e["_i"] = i
        rows, _, _ = S.summarize(evs)
        self.assertEqual({r["task"]: r["category"] for r in rows},
                         {"a1e9x": "fix header", "b2f8y": "write tests", "c3d7z": "reviewer · c3d7"})

def prep(evs):
    for i, e in enumerate(evs): e["_t"] = S.datetime.datetime.fromisoformat(e["t"]); e["_i"] = i
    return evs

class Named(unittest.TestCase):
    def cats(self, evs, **kw):
        rows, _, _ = S.summarize(prep(evs), **kw)
        return {r["task"]: r["category"] for r in rows}

    def test_agent_named_beats_time_order(self):
        evs = [ev("09:00","main","agent_spawn","first"), ev("09:01","main","agent_spawn","second"),
               ev("09:02","b2","agent_start","g"), ev("09:03","a1","agent_start","g"),
               ev("09:03","main","agent_named","second",target="a1"), ev("09:03","main","agent_named","first",target="b2"),
               ev("09:05","a1","agent_stop"), ev("09:05","b2","agent_stop")]
        self.assertEqual(self.cats(evs), {"a1": "second", "b2": "first"})

    def test_cloud_description_never_goes_to_a_local_agent(self):
        evs = [ev("09:00","main","agent_spawn","cloud job",iso="remote"), ev("09:01","main","agent_spawn","local job"),
               ev("09:02","a1","agent_start","g"), ev("09:05","a1","agent_stop")]
        self.assertEqual(self.cats(evs), {"a1": "local job"})

    def test_named_spawn_is_taken_out_of_the_queue(self):
        evs = [ev("09:00","main","agent_spawn","cloud job",iso="remote"), ev("09:00","main","agent_named","cloud job",target="c9",iso="remote"),
               ev("09:02","a1","agent_start","reviewer"), ev("09:05","a1","agent_stop")]
        self.assertEqual(self.cats(evs), {"a1": "reviewer · a1"})

    def test_task_stop_closes_the_agent(self):
        evs = [ev("09:00","a1","agent_start","g"), ev("09:10","a1","agent_stop","stopped")]
        rows, so, _ = S.summarize(prep(evs))
        self.assertEqual((len(rows), so, rows[0]["total_min"]), (1, 0, 10))

    def test_stale_after_six_hours(self):
        evs = [ev("09:00","a1","agent_start","g"), ev("09:10","a1","push","git push"),
               ev("16:00","a2","agent_start","g"), ev("16:01","a2","push","git push")]
        rows, so, _ = S.summarize(prep(evs), include_open=True)
        self.assertEqual(([r["task"] for r in rows], so), (["a2"], 1))
        rows, so, _ = S.summarize(prep(evs), include_open=True, stale_hours=8)
        self.assertEqual(len(rows), 2)

class Hook(unittest.TestCase):
    def run_hook(self, payload):
        import subprocess, tempfile
        out = tempfile.mktemp(suffix=".jsonl")
        subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "..", "hooks", "loop-event.py")],
                       input=json.dumps(payload), text=True, env=dict(os.environ, OUT=out))
        try:
            with open(out) as f: return [json.loads(l) for l in f]
        except FileNotFoundError: return []
        finally:
            if os.path.exists(out): os.unlink(out)

    def test_post_agent_writes_agent_named(self):
        r = self.run_hook({"hook_event_name": "PostToolUse", "tool_name": "Agent",
                           "tool_input": {"description": "fix things", "isolation": "remote"},
                           "tool_response": {"content": [{"type": "text", "text": "started.\nagentId: a1b2c3d4e5f6a7b8c (use SendMessage)"}]}})
        self.assertEqual([(x["kind"], x["target"], x["label"], x.get("iso")) for x in r], [("agent_named", "a1b2c3d4e5f6a7b8c", "fix things", "remote")])

    def test_post_agent_without_id_writes_nothing(self):
        self.assertEqual(self.run_hook({"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_input": {"description": "x"}, "tool_response": "done"}), [])

    def test_pre_agent_marks_remote(self):
        r = self.run_hook({"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {"description": "x", "isolation": "remote"}})
        self.assertEqual((r[0]["kind"], r[0]["iso"]), ("agent_spawn", "remote"))

    def test_post_task_stop_writes_agent_stop(self):
        r = self.run_hook({"hook_event_name": "PostToolUse", "tool_name": "TaskStop", "tool_input": {"task_id": "a2ae6c129b432dfc7"}})
        self.assertEqual([(x["agent"], x["kind"], x["label"]) for x in r], [("a2ae6c129b432dfc7", "agent_stop", "stopped")])

class HandBack(unittest.TestCase):
    """An agent hands back (agent_stop) while its gate still holds the lock, then comes back."""
    def run_(self, evs, **kw):
        rows, so, _ = S.summarize(prep(evs), **kw)
        return rows, so

    def test_stop_while_holding_the_lock_counts_as_test_until_back(self):
        evs = [ev("10:00", "A", "agent_start", "gp"), ev("10:05", "A", "lock_wait", "heavy.lock"), ev("10:05", "A", "lock_got", "heavy.lock"),
               ev("10:06", "A", "agent_stop"), ev("10:26", "A", "agent_start", "gp"), ev("10:30", "A", "agent_stop")]
        (r,), _ = self.run_(evs)
        self.assertEqual((r["impl_min"], r["test_min"], r["total_min"]), (10.0, 20.0, 30.0))

    def test_stop_after_lock_free_is_idle(self):
        evs = [ev("10:00", "A", "agent_start", "gp"), ev("10:05", "A", "lock_got", "heavy.lock"), ev("10:06", "A", "lock_free", "heavy.lock"),
               ev("10:06", "A", "agent_stop"), ev("10:26", "A", "agent_start", "gp"), ev("10:30", "A", "agent_stop")]
        (r,), _ = self.run_(evs)
        self.assertEqual((r["test_min"], r["total_min"]), (0.0, 10.0))

    def test_settled_skips_a_recent_hand_back_unless_final(self):
        now = S.datetime.datetime.fromisoformat(f"{D}10:15:00+09:00")
        a = [ev("10:00", "A", "agent_start", "gp"), ev("10:10", "A", "agent_stop")]
        b = [ev("10:00", "B", "agent_start", "gp"), ev("10:09", "B", "agent_report"), ev("10:10", "B", "agent_stop")]
        rows, so = self.run_(a + b, now=now, settled_min=20)
        self.assertEqual(([r["task"] for r in rows], so), (["B"], 1))
        rows, _ = self.run_(a + b, now=now + S.datetime.timedelta(minutes=30), settled_min=20)
        self.assertEqual(sorted(r["task"] for r in rows), ["A", "B"])

    def test_release_watch_line_after_merge_is_deploy(self):
        evs = [ev("10:00", "A", "agent_start", "gp"), ev("10:10", "A", "merge", "pr merge"), ev("10:14", "release", "release", "v1"),
               ev("10:15", "A", "agent_stop")]
        (r,), _ = self.run_(evs)
        self.assertEqual((r["deploy_min"], r["other_min"], r["released"]), (4.0, 1.0, "2026-11-17 10:14"))

    def test_hook_writes_agent_report_on_handback(self):
        r = Hook.run_hook(self, {"hook_event_name": "PreToolUse", "tool_name": "SubagentHandback", "agent_id": "a1", "tool_input": {"message": "x"}})
        self.assertEqual([(x["agent"], x["kind"]) for x in r], [("a1", "agent_report")])

if __name__ == "__main__":
    unittest.main()
