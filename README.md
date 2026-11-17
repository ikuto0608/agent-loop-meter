# agent-loop-meter — see where a Claude Code loop spends its time

One Claude Code "loop" is: instruction -> implementation -> waiting for tests -> tests -> review -> merge -> live.
With sub-agents running in parallel, nobody can say where the minutes go. `agent-loop-meter` records
the loop through Claude Code hooks and shows it live in a browser: who is working, who is waiting, who is
testing, how long each took. Works in any project; zero dependencies (Python 3 stdlib + Node stdlib).

- [What it measures](#what-it-measures)
- [How it works](#how-it-works)
- [Set up in 5 minutes](#set-up-in-5-minutes)
- [Event format](#event-format)
- [Example](#example)
- [Build the "before" from your own log](#build-the-before-from-your-own-log)
- [No secrets in the log](#no-secrets-in-the-log)
- [Limits](#limits)
- [日本語](#日本語)

## What it measures

One loop = from the moment an agent is spawned until the change is confirmed live. The meter splits it into
**working**, **testing**, and **waiting** (for a lock / a queue), and counts milestones (PR, merge, release).
What counts as "test" or "merge" in *your* project is a list of regexes in `loop-meter.config.json`.

## How it works

```
 Claude Code (main session + sub-agents)
        |  hooks: PreToolUse / PostToolUse / SubagentStart / SubagentStop
        v
 hooks/loop-event.py --(append 1 line)--> ~/.claude/loop-events.jsonl <--(append 1 line)-- watch/release-watch.sh
        ^                                         |                                          (optional: external deploy)
        | rules                                   | tail (500 ms)
 loop-meter.config.json                           v
                                      dashboard/server.mjs --SSE /events--> dashboard/index.html
                                      (plain Node)                          (cards, lanes, kinds, optional "before")
```

## Set up in 5 minutes

1. **Register the hooks.** Copy the `hooks` block of [`hooks/settings.example.json`](hooks/settings.example.json)
   into your project's `.claude/settings.local.json`. If `$CLAUDE_PROJECT_DIR` is not this repo, put the
   absolute path of `hooks/loop-event.py` instead.
2. **Describe your loop** in [`loop-meter.config.json`](loop-meter.config.json) (the repo ships a generic example;
   point elsewhere with `LOOP_METER_CONFIG=/path/to/file.json`). No file -> built-in defaults, same as the example.

   ```json
   { "rules": [
     { "match": "\\bpytest\\b",         "kind": "test",      "label": "pytest" },
     { "match": "\\bmkdir\\s+\\S*\\.lock\\b", "kind": "lock_wait", "label": "lock" },
     { "match": "\\brmdir\\s+\\S*\\.lock\\b", "kind": "lock_free", "label": "lock" },
     { "match": "\\bgh\\s+pr\\s+merge\\b", "kind": "merge",     "label": "pr merge" },
     { "match": "\\bmake deploy\\b",     "kind": "deploy",    "label": "deploy" }
   ] }
   ```

   First matching rule wins. `kind` is one of `lock_wait`, `lock_got`, `lock_free`, `test`, `merge`, `pr`,
   `release_check`, `review`, or any word you like (shown as "other" in the dashboard). Semantics:
   `lock_wait` logs waiting before the command and holding after it; `test` and any rule with `"phase": "span"` log start and end;
   everything else logs once, after the command.
3. **Start the dashboard:** `node dashboard/server.mjs [--log <jsonl>] [--port 4317] [--before <csv>]`
   and open `http://127.0.0.1:4317` (add `?lang=ja` for Japanese). Log path defaults to `~/.claude/loop-events.jsonl`;
   the hook's `OUT` environment variable changes where it writes.
4. **Optional:** `--before tasks.csv` adds a "before" strip. Columns: `task,category,started,...,impl_min,wait_min,test_min,review_min,deploy_min,other_min`
   (and optionally `total_min`). Without the flag that section is hidden.
5. **Optional:** [`watch/release-watch.sh`](watch/release-watch.sh) is an example of watching an external deploy
   target (Heroku): it writes a `release` event when the release number changes. Replace its probe with yours.

## Event format

One JSON object per line:

```json
{"t":"2026-11-17T19:42:10+09:00","agent":"a1b2c3","kind":"test_start","label":"playwright","bg":false}
```

| kind | source |
|---|---|
| `agent_spawn` | PreToolUse on the Agent tool; label = first 60 chars of its description |
| `agent_start` / `agent_stop` | SubagentStart / SubagentStop |
| `lock_wait` / `lock_got` / `lock_free` | rules with kind `lock_wait` (before / after) and `lock_free` |
| `test_start` / `test_end` (also `review_*`, `deploy_*`, any `"phase":"span"` kind) | before / after the command (`bg` = background run) |
| any other rule kind (`pr`, `merge`, ...) | after the command |
| `release` | `watch/release-watch.sh` |

## Example

[`examples/small-shop-site/`](examples/small-shop-site/README.md): 55 sub-agent tasks on a small website, median
74 min per task; tests 37%, implementation 33%, queueing 13%. Run
`node dashboard/server.mjs --before examples/small-shop-site/before-tasks.csv` to see it as the "before" strip.

## Build the "before" from your own log

`tools/summarize.py` turns the JSONL into the same CSV as the example, one row per agent (`source=measured`), so
anyone gets the same split by the same rules:

```
python3 tools/summarize.py --since 2026-11-17 --out tasks.csv      # [--log path] [--include-open]
node dashboard/server.mjs --before tasks.csv
```

One loop = `agent_start` -> `agent_stop` (agents never stopped are left out unless `--include-open`; a resumed agent adds each stretch). Stages, in priority order:
`lock_wait..lock_got` = wait; `test_start..test_end` = test (a background test lasts until the agent's next event; `full_e2e_min` = tests whose label contains `e2e`/`full`);
`review_start..review_end` = review; `deploy_start..deploy_end` = deploy; everything else = impl.
After the first `merge`, the time up to the last `release` / `release_check` / `deploy_end` is deploy and the rest is other; with no such event nothing after the merge is called deploy.
Prints loops, median and stage shares. Test: `python3 -m unittest tools/test_summarize.py`.

Rule options: `"phase": "span"` writes `<kind>_start` before and `<kind>_end` after the command (default for `test`; shipped for `review` and `deploy`); `"point"` (default for other kinds) writes one line after it.
The shipped `review` rule matches `gh pr review` and `/code-review` only. `deploy` is a span for `fly deploy`, `kubectl rollout`, `vercel deploy|--prod|promote`; `heroku releases` stays a one-shot `release_check` (a poll after the merge, not a duration). Edit both to your project.
Not adopted: treating a sub-agent whose description says "review" as review. The Agent spawn event is logged under `main`, not under the new agent's id, so it cannot be tied to that agent's minutes without guessing.

## Reading the screen

One loop = from handing out a task to confirming it in production. The page has a live map and three parts, top to bottom:

0. **Constellation**: the control tower in the middle, running agents on orbits around it (color = stage), the lock and production on the sides. A dot runs along a line for each event; a merge flies to production, a `release` makes production swell once, and finished agents drift out into the "shipped" count. With `prefers-reduced-motion` it is a still picture.
1. **Now**: one card per running agent (name, current stage, elapsed time).
2. **Today**: one row per agent; time runs left to right with clock ticks below; the legend above gives the stage colors.
3. **Before**: past tasks, one row each (length = minutes, color = stage). The top bar adds all of them up with percentages; the dotted line is the median. Hover any bar for its stage and minutes.

## No secrets in the log

- The **command text is never written**: only `kind` and the short `label` from your rule.
- Avoid rules that match commands printing secrets (config dumps, token commands).
- The hook prints nothing and never fails the host; `*.jsonl` is git-ignored.

## Limits

- The end of a background test is not observed (the end hook fires when it is started).
- Lock logic assumes one lock command pair; unknown kinds are counted but not drawn as lane phases.
- Regex matching sees commands, not outcomes: a failed test still counts as a test.
- Parallel review and test time overlap, so stage sums can exceed the total.
- The example data is partly estimated (see its README); its waiting numbers are lower bounds.

License: MIT (`LICENSE`).

---

# 日本語

Claude Code の 1 ループ (指示 -> 実装 -> テスト待ち -> テスト -> レビュー -> マージ -> 反映) の時間が、サブエージェント込みで**どこに消えているか**を見える化するツールです。フックで出来事を JSONL に記録し、ブラウザでリアルタイムに表示します。依存ライブラリはありません。

## 入れ方 (5 分)

1. `hooks/settings.example.json` の `hooks` を、対象プロジェクトの `.claude/settings.local.json` に足す。
2. `loop-meter.config.json` の `rules` に、自分のプロジェクトのテスト・マージ・ロックのコマンドを正規表現で書く (`match` / `kind` / `label`)。場所を変えるときは環境変数 `LOOP_METER_CONFIG`。無ければ既定の例で動く。
3. `node dashboard/server.mjs` を起動して `http://127.0.0.1:4317/?lang=ja` を開く。
4. 任意: `--before <csv>` で「前」の内訳の帯を出す。無ければその区画は隠れる。

## 自分の記録から「前」を作る

`tools/summarize.py` が JSONL から事例と同じ形の CSV (1 行 1 担当・`source=measured`) を作ります。誰が回しても同じ決まりで段階が分かれます。

```
python3 tools/summarize.py --since 2026-11-17 --out tasks.csv      # [--log path] [--include-open]
node dashboard/server.mjs --before tasks.csv
```

1 ループ = `agent_start` から `agent_stop` まで (止まっていない担当は `--include-open` が無ければ除く)。段階は優先順に、鍵待ち (`lock_wait`〜`lock_got`)・テスト (バックグラウンドは次の出来事まで・ラベルに `e2e`/`full` を含むものは `full_e2e_min` にも)・確認役 (`review_start`〜`review_end`)・反映 (`deploy_start`〜`deploy_end`)・残りは実装。マージのあと、最後の `release`/`release_check`/`deploy_end` までを反映待ち、それ以降はその他。反映の出来事が無ければマージ後も反映待ちにはしません。
本数・中央値・段階の割合も出力します。確認: `python3 -m unittest tools/test_summarize.py`。

ルールの `"phase": "span"` は前後に `<kind>_start`/`<kind>_end` を書き、`"point"` は後に 1 行 (`test` だけ既定が span)。既定の review は `gh pr review` と `/code-review` のみ、deploy は `fly deploy` などの span、`heroku releases` は期間でなく確認なので 1 回の `release_check` のまま。プロジェクトに合わせて書き換えてください。
「説明に review と書いたサブエージェントを確認役にする」案は採りません。担当を出す出来事は `main` 名義で、新しい担当の id と結べない (推測になる) ためです。

## 画面の見方

1 ループ = 担当を出してから本番確認まで。画面はいちばん上の地図と、その下の 3 つの区画です。

0. **星座**: 中央が管制塔、周りの軌道に動いている担当 (色は段階)、左に鍵・右に本番。出来事ごとに線の上を粒が流れ、マージは本番へ飛び、`release` で本番が一度ふわっと明るくなり、終わった担当は外へ流れて「出荷」の数に入ります。`prefers-reduced-motion` のときは止まった絵です。
1. **いま**: 動いている担当ごとに 1 枚のカード (名前・今の段階・経過時間)。
2. **今日**: 1 行 1 担当。横軸は時刻で左が早く、下に時刻の目盛り、上に色の凡例。
3. **前**: 過去の 1 本ずつ (長さが分・色が段階)。いちばん上の帯が全部を足した内訳 (%)、縦の点線が中央値。帯にマウスを載せると段階と分が出ます。

## 仕組みと安全

フックが 1 出来事 1 行を `~/.claude/loop-events.jsonl` に追記し、Node のサーバが SSE でブラウザに流す。コマンドの**全文は記録せず**、種類 (`kind`) と短い名前 (`label`) だけ残す。事例は [`examples/small-shop-site/`](examples/small-shop-site/README.md) (小さな店のサイトの直し 55 本・中央値 74 分)。

## 限界

バックグラウンドのテストの終わりは拾えない・コマンドの成否は見ない・事例の一部は時刻からの推定で待ちは下限。担当の名前は Agent ツールの description を時刻順の待ち行列で結ぶので、同時に複数出すとずれることがある (結べないときは `種類 · id 4 文字`)。
