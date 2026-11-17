#!/usr/bin/env node
// Render examples/animation/index.html frame by frame and encode mp4s with ffmpeg.
//
//   node examples/animation/record.mjs [--lang ja|en] [--only 4x5|16x9] [--playwright /path/to/node_modules/playwright]
//
// Needs Playwright (with its Chromium) and ffmpeg on PATH. Playwright is not a dependency of this
// repo: pass --playwright or set PLAYWRIGHT_PATH to any install, or have `playwright` resolvable.
// Output: examples/animation/out/agent-loop-74min-<size>[-en].mp4 and a thumbnail png per size.
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { mkdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const arg = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const lang = arg("--lang", "ja") === "en" ? "en" : "ja";
const only = arg("--only", null);
const pwPath = arg("--playwright", process.env.PLAYWRIGHT_PATH || "playwright");
const require = createRequire(import.meta.url);
const { chromium } = require(pwPath);

const FPS = 30;
const THUMB_T = 15.0; // breakdown bar with all labels, before the closing line
const SIZES = [
  { name: "4x5", width: 1080, height: 1350 },
  { name: "16x9", width: 1920, height: 1080 },
].filter(s => !only || s.name === only);

const outDir = join(here, "out");
mkdirSync(outDir, { recursive: true });
const suffix = lang === "en" ? "-en" : "";
const page_url = pathToFileURL(join(here, "index.html")).href + `?record=1&lang=${lang}`;

const browser = await chromium.launch();
try {
  for (const size of SIZES) {
    const page = await browser.newPage({ viewport: { width: size.width, height: size.height }, deviceScaleFactor: 1 });
    await page.goto(page_url);
    await page.waitForFunction(() => typeof window.__render === "function");
    const duration = await page.evaluate(() => window.__duration);
    const frames = Math.round(duration * FPS);
    const mp4 = join(outDir, `agent-loop-74min-${size.name}${suffix}.mp4`);

    const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-i", "-",
      "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-r", String(FPS),
      "-movflags", "+faststart", "-an", mp4], { stdio: ["pipe", "inherit", "inherit"] });
    const done = new Promise((res, rej) => ff.on("close", c => c === 0 ? res() : rej(new Error(`ffmpeg exited ${c}`))));

    for (let f = 0; f < frames; f++) {
      await page.evaluate(t => window.__render(t), f / FPS);
      const buf = await page.screenshot({ type: "png" });
      if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once("drain", r));
      if (f % 60 === 0) process.stdout.write(`\r${size.name}: frame ${f}/${frames}`);
    }
    ff.stdin.end();
    await done;

    await page.evaluate(t => window.__render(t), THUMB_T);
    const png = join(outDir, `agent-loop-74min-${size.name}${suffix}.png`);
    await page.screenshot({ path: png, type: "png" });
    await page.close();
    const mb = (statSync(mp4).size / 1e6).toFixed(2);
    console.log(`\r${size.name}: ${mp4} (${frames} frames, ${duration}s, ${mb} MB) + ${png}`);
  }
} finally {
  await browser.close();
}
