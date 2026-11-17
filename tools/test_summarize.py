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
        self.assertEqual((y["wait_min"], y["test_min"]), (5, 15))

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

if __name__ == "__main__":
    unittest.main()
