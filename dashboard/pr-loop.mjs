// One row per merged PR: created -> live, how many gate runs (and why each re-run), the lock wait,
// the test seconds (unit / e2e / prod-smoke) and the wait for the release after the merge.
// Pure functions (no I/O) so they can be tested; server.mjs feeds them.
//
// Inputs:
//   prs      [{number,title,headRefName,createdAt,mergedAt}]   (gh pr list --state merged --json ...)
//   gates    lines of a gate-results JSONL: {t (end), branch, sha, ok, kind, total_s, stages:{name:s},
//            failed, why, behind, lock_wait_s, rerun:{n,reason,...}|null}
//   events   loop-events lines; only {kind:"release", t, label} are used

export const parseJsonl = (text) =>
  String(text || "")
    .split("\n")
    .filter(Boolean)
    .flatMap((l) => {
      try {
        return [JSON.parse(l)];
      } catch {
        return [];
      }
    });

const ms = (iso) => {
  const v = Date.parse(iso);
  return Number.isFinite(v) ? v : null;
};

export const median = (xs) => {
  const v = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
};

/** Local calendar day (YYYY-MM-DD) of an epoch ms. */
export const dayOf = (t) => {
  const d = new Date(t);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

// Gate runs that end up to this long after the merge still count (the gate's own merge step logs last).
const AFTER_MERGE_MS = 5 * 60e3;
// A gate run may start well before `gh pr create` (gate first, PR after); this bounds how far back.
const BEFORE_CREATE_MS = 24 * 3600e3;

/**
 * @param {{prs:any[], gates:any[], events:any[], now?:number}} input
 * @returns {{rows:any[], days:any[], gateLogStart:number|null}}
 */
export function buildPrLoop({ prs = [], gates = [], events = [], now = Date.now() }) {
  const gateLines = gates
    .map((g) => ({ ...g, end: ms(g.t) }))
    .filter((g) => g.end != null && g.branch)
    .sort((a, b) => a.end - b.end);
  const gateLogStart = gateLines.length
    ? Math.min(...gateLines.map((g) => g.end - (+g.total_s || 0) * 1000))
    : null;
  const releases = events
    .filter((e) => e.kind === "release")
    .map((e) => ({ at: ms(e.t), label: e.label || "" }))
    .filter((r) => r.at != null)
    .sort((a, b) => a.at - b.at);

  // Oldest merge first, so a branch name reused later does not take the earlier PR's gate runs.
  const merged = prs
    .map((p) => ({ ...p, created: ms(p.createdAt), merged: ms(p.mergedAt) }))
    .filter((p) => p.created != null && p.merged != null)
    .sort((a, b) => a.merged - b.merged);
  const taken = new Set();
  const rows = merged.map((p) => {
    const mine = gateLines.filter(
      (g) =>
        !taken.has(g) &&
        g.branch === p.headRefName &&
        g.end <= p.merged + AFTER_MERGE_MS &&
        g.end >= p.created - BEFORE_CREATE_MS,
    );
    mine.forEach((g) => taken.add(g));
    const release = releases.find((r) => r.at >= p.merged) || null;
    const before = gateLogStart == null || p.created < gateLogStart;
    const sum = (f) => mine.reduce((s, g) => s + (f(g) || 0), 0);
    const st = (name) => sum((g) => +(g.stages || {})[name] || 0);
    // the loop starts at the PR's creation, or at its first gate run when the gate ran before `gh pr create`
    const firstGate = mine.length ? Math.min(...mine.map((g) => g.end - (+g.total_s || 0) * 1000)) : Infinity;
    const from = Math.min(p.created, firstGate);
    const reruns = mine.slice(1).map((g) => g.rerun?.reason || "unknown");
    return {
      number: p.number,
      title: p.title || "",
      branch: p.headRefName,
      created: p.createdAt,
      merged: p.mergedAt,
      day: dayOf(p.merged),
      release: release ? release.label : null,
      released: release ? new Date(release.at).toISOString() : null,
      from: new Date(from).toISOString(),
      gate_first: from < p.created,   // the first gate run started before the PR was created
      lead_s: release ? Math.round((release.at - from) / 1000) : null,
      // still waiting for a release: seconds so far (shown as "not live yet")
      pending_s: release ? null : Math.round((now - from) / 1000),
      deploy_wait_s: release ? Math.round((release.at - p.merged) / 1000) : null,
      // gate columns: null when the gate log had not started yet for this PR and nothing matched
      gate_recorded: !(before && !mine.length),
      gate_partial: before && mine.length > 0,
      gates: mine.length,
      reruns,
      failed: mine.filter((g) => !g.ok).map((g) => g.failed === "preflight" && g.why ? g.why : `${g.failed || "?"}-red`),
      lock_wait_s: sum((g) => +g.lock_wait_s || 0),
      tests_s: { unit: st("unit"), e2e: st("e2e"), "prod-smoke": st("prod-smoke") },
      gate_s: sum((g) => +g.total_s || 0),
    };
  });

  const byDay = new Map();
  for (const r of rows) {
    if (!byDay.has(r.day)) byDay.set(r.day, []);
    byDay.get(r.day).push(r);
  }
  const days = [...byDay.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([day, rs]) => {
      const rec = rs.filter((r) => r.gate_recorded && !r.gate_partial && r.gates > 0);
      return {
        day,
        prs: rs.length,
        recorded: rec.length,
        median_lead_s: median(rs.map((r) => r.lead_s)),
        median_gates: median(rec.map((r) => r.gates)),
        median_lock_wait_s: median(rec.map((r) => r.lock_wait_s)),
        reruns: rec.reduce((s, r) => s + r.reruns.length, 0),
        rows: rs.slice().sort((a, b) => (a.merged < b.merged ? 1 : -1)),
      };
    });
  return { rows, days, gateLogStart: gateLogStart == null ? null : new Date(gateLogStart).toISOString() };
}
