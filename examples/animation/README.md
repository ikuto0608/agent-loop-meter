# Animation: "74 minutes per AI fix"

A 20-second chart animation for social posts, built from the [small shop site example](../small-shop-site/).
One message: **an AI agent takes a median of 74 min per fix; implementation is a third, the rest is tests and waiting.**

One file, no dependencies, nothing loaded from the network: [`index.html`](index.html).
Open it in a browser and it loops. Japanese by default, `index.html?lang=en` for English.

| time | what happens |
|---|---|
| 0-3 s | Title, the number counts up to 74 min (median of 55 fixes) |
| 3-11 s | The 55 fixes grow as bars, in start order, coloured by stage; the median line (74 min) draws down |
| 11-16 s | The bars fold into one bar: build 33%, tests 37%, queue 13%, other 18% (the 28 fixes with agent reports) |
| 16-20 s | Closing line and the repo URL |

Notes on the data: the 55 bars use every row of `before-tasks.csv`. The breakdown uses only the 28 rows with
`source=report`, because the 27 estimated rows cannot see queueing (it folds into implementation). Percentages are
the ones in the example README.

No flashing: everything fades or grows, the film grain is static.

## Export to video

Needs [Playwright](https://playwright.dev) with Chromium and `ffmpeg` on `PATH`. Playwright is not a dependency
of this repo; point the script at any install.

```
node examples/animation/record.mjs --playwright /path/to/node_modules/playwright
node examples/animation/record.mjs --lang en --only 4x5   # English, one size
```

Each frame is rendered by calling `window.__render(t)` and screenshotting, so the video is frame exact
(30 fps, H.264, yuv420p, no audio). Output goes to `examples/animation/out/` (git-ignored):

- `agent-loop-74min-4x5.mp4` (1080x1350, for a vertical post) and `agent-loop-74min-16x9.mp4` (1920x1080)
- `agent-loop-74min-4x5.png` / `-16x9.png`: a still at 15 s (the labelled breakdown) for a thumbnail

English output gets an `-en` suffix.
