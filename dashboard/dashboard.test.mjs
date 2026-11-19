// node --test dashboard/   (TZ is pinned so the day buckets are stable)
process.env.TZ = "Asia/Tokyo";
import test from "node:test";
import assert from "node:assert/strict";
import { buildPrLoop, median, parseJsonl } from "./pr-loop.mjs";
import { agentStatus, QUIET_MS } from "./agent-status.mjs";

const J = (hm) => `2026-11-19T${hm}:00+09:00`;
const at = (hm) => Date.parse(J(hm));
const gate = (hm, branch, o = {}) => ({ t: J(hm), branch, sha: "s", ok: true, kind: "code", total_s: 600,
  stages: { preflight: 5, check: 3, unit: 20, e2e: 400, "prod-smoke": 150 }, failed: null, why: null, behind: 0,
  lock_wait_s: 60, rerun: null, ...o });

test("median and parseJsonl", () => {
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([1, 2, 3, 4]), 2.5);
  assert.equal(median([]), null);
  assert.deepEqual(parseJsonl('{"a":1}\nnot json\n\n{"a":2}'), [{ a: 1 }, { a: 2 }]);
});

test("one row per PR: gate runs, re-run reasons, lock wait, tests, deploy wait", () => {
  const prs = [{ number: 7, title: "bench", headRefName: "feat/bench", createdAt: J("10:00"), mergedAt: J("10:40") }];
  const gates = [
    gate("09:58", "feat/bench", { ok: false, failed: "preflight", why: "behind-same-spec", total_s: 10, stages: { preflight: 10 }, lock_wait_s: null }),
    gate("10:20", "feat/bench", { lock_wait_s: 300, rerun: { n: 1, reason: "after-behind-same-spec" } }),
    gate("10:38", "feat/bench", { rerun: { n: 2, reason: "same-sha-after-e2e-red" } }),
    gate("10:30", "other/branch"),
  ];
  const events = [{ t: J("10:35"), kind: "release", label: "v1" }, { t: J("10:46"), kind: "release", label: "v2" }];
  const { days, rows } = buildPrLoop({ prs, gates, events, now: at("11:00") });
  const r = rows[0];
  assert.equal(r.gates, 3);
  assert.deepEqual(r.reruns, ["after-behind-same-spec", "same-sha-after-e2e-red"]);
  assert.deepEqual(r.failed, ["behind-same-spec"]);
  assert.equal(r.lock_wait_s, 360);
  assert.deepEqual(r.tests_s, { unit: 40, e2e: 800, "prod-smoke": 300 });
  assert.equal(r.release, "v2");           // the first release after the merge, not the earlier one
  // the first gate run (ended 09:58 after 10 s) started before the PR was created at 10:00
  assert.equal(r.gate_first, true);
  assert.equal(r.lead_s, 46 * 60 + 2 * 60 + 10);
  assert.equal(r.deploy_wait_s, 6 * 60);
  assert.equal(r.gate_recorded, true);
  assert.equal(days[0].day, "2026-11-19");
  assert.equal(days[0].median_gates, 3);
});

test("a PR from before the gate log starts says so; a PR not live yet has no lead", () => {
  const prs = [
    { number: 1, title: "early", headRefName: "a", createdAt: J("08:00"), mergedAt: J("08:30") },
    { number: 2, title: "late", headRefName: "b", createdAt: J("10:00"), mergedAt: J("10:20") },
  ];
  const gates = [gate("10:10", "b")];
  const { rows, days } = buildPrLoop({ prs, gates, events: [{ t: J("08:40"), kind: "release", label: "v1" }], now: at("10:30") });
  const [early, late] = rows;
  assert.equal(early.gate_recorded, false);
  assert.equal(early.lead_s, 40 * 60);
  assert.equal(late.lead_s, null);
  assert.equal(late.pending_s, 30 * 60);
  assert.equal(days[0].recorded, 1);        // medians of gate columns only over recorded PRs
  assert.equal(days[0].median_lead_s, 40 * 60);
});

test("a reused branch name: each PR takes only its own gate runs", () => {
  const prs = [
    { number: 1, title: "x", headRefName: "fix/a", createdAt: J("09:00"), mergedAt: J("09:30") },
    { number: 2, title: "y", headRefName: "fix/a", createdAt: J("11:00"), mergedAt: J("11:30") },
  ];
  const { rows } = buildPrLoop({ prs, gates: [gate("09:20", "fix/a"), gate("11:20", "fix/a"), gate("11:25", "fix/a")], events: [] });
  assert.deepEqual(rows.map((r) => r.gates), [1, 2]);
});

const agent = (o) => ({ start: at("20:00"), last: at("20:10"), end: at("20:10"), final: null, noStart: false, state: "done",
  scriptHeld: false, bgPending: false, lockHeld: null, merged: null, ...o });

test("agent status: working, handed back but busy, done", () => {
  assert.equal(agentStatus(agent({ end: null, state: "work" }), at("20:15")).status, "work");
  // handed back a few minutes ago, no final report: still in the background
  assert.deepEqual(agentStatus(agent({}), at("20:15")), { status: "bg", why: "quiet", until: at("20:15") });
  // quiet for longer than QUIET_MS: done
  assert.equal(agentStatus(agent({}), at("20:10") + QUIET_MS + 1).status, "done");
  // a final report ends it at once
  assert.equal(agentStatus(agent({ final: at("20:10") }), at("20:11")).status, "done");
  // a background test after the stop
  assert.equal(agentStatus(agent({ end: null, state: "gap" }), at("20:50")).why, "test");
});

test("agent status: a held lock keeps it busy until a release after its own merge (or the cap)", () => {
  const a = agent({ lockHeld: { since: at("20:05"), waiting: false } });
  // someone else's release does not end it
  assert.deepEqual(agentStatus(a, at("20:50"), [at("20:20")]).why, "lock");
  // its own merge then a release: over (and quiet long enough)
  const b = { ...a, merged: at("20:30") };
  const s = agentStatus(b, at("21:00"), [at("20:20"), at("20:35")]);
  assert.equal(s.status, "done");
  assert.equal(s.until, at("20:35") > at("20:10") + QUIET_MS ? at("20:35") : at("20:10") + QUIET_MS);
  // capped: a lock freed inside a script is never seen
  assert.equal(agentStatus(a, at("21:06")).status, "done");
  assert.equal(agentStatus(agent({ lockHeld: { since: at("20:05"), waiting: true } }), at("20:50")).why, "lockwait");
});
