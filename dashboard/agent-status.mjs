// Is an agent still running? An agent often hands control back (SubagentStop -> agent_stop) while its gate
// or test keeps running in the background and it still holds a lock, then comes back later. Judging only
// by agent_start/agent_stop shows such an agent as finished. Shared by the browser (index.html) and tests.
//
// Three statuses:
//   "work" - started and not stopped (the agent itself is working)
//   "bg"   - stopped, but still busy behind the scenes: it holds or waits for a lock (no lock_free, and no
//            release after a merge of its own since it took the lock; at most `lockMaxMs`, since a lock freed
//            inside a script is never seen), a test it started is still open, or its last event is less than `quietMs`
//            ago and no final mark (agent_report from SubagentHandback, or a TaskStop "stopped") came
//   "done" - a final mark, or stopped and quiet for longer than `quietMs` with nothing held
//
// `a` is the dashboard's agent record: { start, last, end, final, noStart, state, scriptHeld, bgPending,
// lockHeld: {since, waiting} | null, merged: time of its last merge | null }; `final` is cleared by the next agent_start. `releases` is a sorted list of release times (ms).

export const QUIET_MS = 20 * 60e3;
export const LOCK_MAX_MS = 60 * 60e3;

const firstAfter = (sorted, t) => sorted.find((r) => r > t) ?? null;

/** @returns {{status:"work"|"bg"|"done", why:string|null, until:number|null}} until = end of the bg band */
export function agentStatus(a, now, releases = [], { quietMs = QUIET_MS, staleMs = 6 * 3600e3, lockMaxMs = LOCK_MAX_MS } = {}) {
  if (a.noStart) return { status: "done", why: null, until: null };
  if (a.end == null) {
    if (a.last != null && now - a.last > staleMs) return { status: "done", why: "stale", until: null };
    if (a.state === "gap") return { status: "bg", why: "test", until: now }; // stopped, its background test open
    return { status: "work", why: null, until: null };
  }
  if (a.final != null) return { status: "done", why: "final", until: a.end };
  if (now - a.last > staleMs) return { status: "done", why: "stale", until: a.end };
  if (a.state === "gap" || a.scriptHeld || a.bgPending) return { status: "bg", why: "test", until: now };
  let lockEnd = null;
  if (a.lockHeld) {
    // the release that ends it: the first one after this agent's own merge (made after it took the lock)
    const rel = a.merged != null && a.merged >= a.lockHeld.since ? firstAfter(releases, a.merged) : null;
    const end = Math.min(rel ?? Infinity, a.lockHeld.since + lockMaxMs);
    if (end > now) return { status: "bg", why: a.lockHeld.waiting ? "lockwait" : "lock", until: now };
    lockEnd = end;
  }
  const quietEnd = a.last + quietMs;
  if (now <= quietEnd) return { status: "bg", why: "quiet", until: now };
  return { status: "done", why: null, until: Math.max(a.end, lockEnd ?? 0, quietEnd) };
}
