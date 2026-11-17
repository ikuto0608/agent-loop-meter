// agent-loop-meter dashboard — zero-dependency Node server.
// Tails a JSONL event log written by hooks/loop-event.py and streams new lines to the
// browser with Server-Sent Events. Also serves the "before" CSV for the comparison strip.
//
//   node dashboard/server.mjs [--log ~/.claude/loop-events.jsonl] [--port 4317]
import http from "node:http";
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
const BEFORE = path.join(here, "..", "data", "before-tasks.csv");

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
    if (req.url === "/before.csv") {
      res.writeHead(200, { "Content-Type": "text/csv; charset=utf-8" });
      return fs.createReadStream(BEFORE).on("error", () => res.end()).pipe(res);
    }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    fs.createReadStream(path.join(here, "index.html")).pipe(res);
  })
  .listen(PORT, "127.0.0.1", () =>
    console.log(`agent-loop-meter on http://127.0.0.1:${PORT}  (log: ${LOG})`),
  );
