// agent-loop-meter dashboard — zero-dependency Node server.
// Tails a JSONL event log written by hooks/loop-event.py and streams new lines to the
// browser with Server-Sent Events. Also serves the "before" CSV for the comparison strip.
//
//   node dashboard/server.mjs [--log ~/.claude/loop-events.jsonl] [--port 4317] [--before path/to/tasks.csv] [--stale-hours 6]
//                             [--pr-repo owner/name] [--gates ~/.claude/gate-results.jsonl]
// /recent.csv = the latest loops from the log (tools/summarize.py, needs python3); --before adds a fixed
// "before the tool" CSV as a second choice.
// With --pr-repo, /pr-loop.json gives one row per merged PR (gh pr list, cached, refreshed every few minutes).
import http from "node:http";
import { execFile } from "node:child_process";
import { buildPrLoop, parseJsonl } from "./pr-loop.mjs";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const arg = (k, d) => {
  const i = process.argv.indexOf(k);
  return i > 0 ? process.argv[i + 1] : d;
};
const LOG = (arg("--log", "~/.claude/loop-events.jsonl")).replace(/^~/, os.homedir());
const PORT = Number(arg("--port", "4317"));
const STALE_HOURS = Number(arg("--stale-hours", "6")); // an agent silent this long (no stop) is stale: no card
const BEFORE = arg("--before", null); // optional "before" CSV; the strip is hidden without it
const PR_REPO = arg("--pr-repo", process.env.LOOP_METER_PR_REPO || null); // owner/name; the PR section is hidden without it
const GATES = (arg("--gates", "~/.claude/gate-results.jsonl")).replace(/^~/, os.homedir());
const PR_REFRESH_MS = 3 * 60e3;

const clients = new Set();
let offset = 0;

function readAll() {
  try {
    return fs.readFileSync(LOG, "utf8").split("\n").filter(Boolean);
  } catch {
    return [];
  }
}

function pump() {
  let size;
  try {
    size = fs.statSync(LOG).size;
  } catch {
    return;
  }
  if (size < offset) offset = 0; // truncated
  if (size === offset) return;
  const fd = fs.openSync(LOG, "r");
  const buf = Buffer.alloc(size - offset);
  fs.readSync(fd, buf, 0, buf.length, offset);
  fs.closeSync(fd);
  offset = size;
  for (const line of buf.toString("utf8").split("\n").filter(Boolean)) {
    for (const res of clients) res.write(`data: ${line}\n\n`);
  }
}

// Merged PRs from gh, cached. A missing gh or a failed call keeps the last list (or none) and says why.
const prCache = { prs: [], at: null, error: null };
function refreshPrs() {
  if (!PR_REPO) return;
  execFile(
    "gh",
    ["pr", "list", "--repo", PR_REPO, "--state", "merged", "--limit", "100",
      "--json", "number,title,headRefName,createdAt,mergedAt"],
    { timeout: 30000, maxBuffer: 4 << 20 },
    (err, stdout) => {
      if (err) { prCache.error = err.code === "ENOENT" ? "gh not found" : "gh failed"; return; }
      try {
        prCache.prs = JSON.parse(stdout);
        prCache.at = new Date().toISOString();
        prCache.error = null;
      } catch { prCache.error = "gh output not JSON"; }
    },
  );
}
refreshPrs();
setInterval(refreshPrs, PR_REFRESH_MS).unref();
// The latest loops, rebuilt from the event log by tools/summarize.py (one row per agent, newest first).
// An agent that handed back less than SETTLED_MIN ago without a final report may still be busy: left out.
const SETTLED_MIN = 20;
const recentCache = new Map(); // n -> { at, csv }
function recentCsv(n, cb) {
  const c = recentCache.get(n);
  if (c && Date.now() - c.at < 30e3) return cb(null, c.csv);
  execFile(
    "python3",
    [path.join(here, "..", "tools", "summarize.py"), "--log", LOG, "--out", "-", "--last", String(n),
      "--settled-min", String(SETTLED_MIN), "--stale-hours", String(STALE_HOURS)],
    { timeout: 30000, maxBuffer: 8 << 20 },
    (err, stdout) => {
      if (err) return cb(err);
      recentCache.set(n, { at: Date.now(), csv: stdout });
      cb(null, stdout);
    },
  );
}
function readGates() {
  try { return parseJsonl(fs.readFileSync(GATES, "utf8")); } catch { return []; }
}

try {
  offset = fs.statSync(LOG).size;
} catch {}
setInterval(pump, 500);

http
  .createServer((req, res) => {
    if (req.url === "/events") {
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      });
      for (const line of readAll()) res.write(`data: ${line}\n\n`);
      clients.add(res);
      req.on("close", () => clients.delete(res));
      return;
    }
    if (req.url === "/config.json") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ stale_hours: STALE_HOURS, before: !!(BEFORE && fs.existsSync(BEFORE)), settled_min: SETTLED_MIN }));
    }
    if (req.url.startsWith("/pr-loop.json")) {
      const days = Math.max(1, Math.min(30, Number(new URL(req.url, "http://x").searchParams.get("days")) || 3));
      const out = PR_REPO
        ? buildPrLoop({ prs: prCache.prs, gates: readGates(), events: parseJsonl(readAll().join("\n")) })
        : { rows: [], days: [], gateLogStart: null };
      res.writeHead(200, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-cache" });
      return res.end(JSON.stringify({
        enabled: !!PR_REPO, repo: PR_REPO, fetched_at: prCache.at, error: prCache.error,
        gate_log_start: out.gateLogStart, days: out.days.slice(0, days),
      }));
    }
    if (req.url === "/agent-status.mjs") {
      res.writeHead(200, { "Content-Type": "text/javascript; charset=utf-8" });
      return fs.createReadStream(path.join(here, "agent-status.mjs")).pipe(res);
    }
    if (req.url.startsWith("/recent.csv")) {
      const n = Math.max(1, Math.min(500, Number(new URL(req.url, "http://x").searchParams.get("n")) || 50));
      return recentCsv(n, (err, csv) => {
        if (err) { res.writeHead(500); return res.end(); }
        res.writeHead(200, { "Content-Type": "text/csv; charset=utf-8", "Cache-Control": "no-cache" });
        res.end(csv);
      });
    }
    if (req.url === "/before.csv") {
      if (!BEFORE || !fs.existsSync(BEFORE)) {
        res.writeHead(404);
        return res.end();
      }
      res.writeHead(200, { "Content-Type": "text/csv; charset=utf-8" });
      return fs.createReadStream(BEFORE).on("error", () => res.end()).pipe(res);
    }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    fs.createReadStream(path.join(here, "index.html")).pipe(res);
  })
  .listen(PORT, "127.0.0.1", () =>
    console.log(`agent-loop-meter on http://127.0.0.1:${PORT}  (log: ${LOG})`),
  );
