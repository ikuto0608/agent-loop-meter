# agent-loop-meter

- A Claude Code control-tower session spawns sub-agents that each fix a small thing on a small shop's website; one fix took a median of 74 minutes.
- This repo measures where that time goes (implementation 33%, tests 37%, queueing 13%, ...) and streams it live.
- Hooks write events to JSONL, a zero-dependency Node server pushes them over SSE to a browser dashboard. Built at Agent Harness Build Night, 2026-11-17.

## 何の問題か

「ある小さな店のサイト」を、Claude Code の管制塔セッションが出した AI の担当 (サブエージェント) が少しずつ直していく。
1 本の直し (担当を出してから本番確認まで) に、**中央値 74 分** かかっていた。どこで時間が消えているのかが見えなかったので、見える化した。

## 「前」の数字 (3 日間・55 本)

データ: [`data/before-tasks.csv`](data/before-tasks.csv) (1 行 1 本。タイトルは種類に一般化済み)。

| 項目 | 値 |
|---|---|
| 1 本の所要 | 中央値 74 分 (真ん中の半分は 52〜104 分)・平均 81 分・最短 32 分・最長 195 分 |
| 本数 | 55 (担当の報告の表から 28・時刻から推定 27) |

### 段階ごとの割合 (報告のある 28 本・合計 2,505 分)

| 段階 | 割合 |
|---|---|
| 実装 | 33% |
| テスト | 37% |
| 順番待ち (重いテストの鍵) | 13% |
| その他 (中断・追加の指示・別の PR 待ち・CI) | 8% |
| 確認役 (レビュー) | 5% |
| 反映待ち (マージから本番の版が出るまで・毎回 約 4 分) | 5% |

### 待ちの原因 上位 3

1. **重いテストの鍵の取り合い。** 1 台のマシンで重いテストを 1 本ずつ回すための鍵を、並行する 2〜3 人の担当が取り合う。待ちは 28 本中 13 本で起き、平均 25 分・最大 70 分。
2. **外の要因での中断。** ネット切れ 30 分、CI のチェックが 10 分付かない、デプロイ先にログインできない、など。
3. **別の PR 待ち・途中の指示の追加・rebase 後の再実行。**

### 打った手と、その効き

- **変更したファイルから回すテストを選ぶ仕組み**: 対応表にあるものだけ絞り、表にないもの・共通の土台に触れる変更は一式に戻す。全画面の e2e 一式 約 16 分 → 絞れたときは約 7 分 (後の測定は 2 本だけの参考値)。
- **地図の変更を 1 コマンドにする**: 地図の変更 1 本 195 分 → 70 分。

## 今日作ったもの

```
 Claude Code (管制塔 + サブエージェント)
        |  hooks: PreToolUse / PostToolUse / SubagentStart / SubagentStop
        v
 hooks/loop-event.py  --(1 行追記)-->  ~/.claude/loop-events.jsonl  <--(1 行追記)--  watch/release-watch.sh
                                                |                                      (本番の版の見張り)
                                                | tail (500 ms)
                                                v
                                    dashboard/server.mjs  --SSE /events-->  dashboard/index.html
                                    (依存なしの Node)                      (カード・帯グラフ・順番待ち・「前」との比較)
```

## 使い方

1. フックを入れる。[`hooks/settings.example.json`](hooks/settings.example.json) の `hooks` を、対象プロジェクトの `.claude/settings.local.json` に足す。`$CLAUDE_PROJECT_DIR` がこのリポの場所を指さないときは、`loop-event.py` の絶対パスに置き換える。
2. 画面を起動する: `node dashboard/server.mjs [--log <jsonl のパス>] [--port 4317]` を実行し、`http://127.0.0.1:4317` を開く。
3. (任意) 本番の版の見張り: `watch/release-watch.sh <heroku-app>` (30 秒ごとに `heroku releases -n 1` を見て、版が変わったら `release` を書く)。
4. 書き先を変えるときは環境変数 `OUT` (既定 `~/.claude/loop-events.jsonl`)。

フックが拾うコマンドの種類は `hooks/loop-event.py` の `classify_bash` にある。自分のプロジェクトのテスト・ロックのコマンドに合わせて正規表現を直す。

## 記録の形

1 行 1 出来事の JSON:

```json
{"t":"2026-11-17T19:42:10+09:00","agent":"a1b2c3","kind":"test_start","label":"playwright","bg":false}
```

| kind | 出どころ |
|---|---|
| `agent_spawn` | PreToolUse (Agent)。label は説明の先頭 60 字 |
| `agent_start` / `agent_stop` | SubagentStart / SubagentStop |
| `lock_wait` / `lock_got` / `lock_free` | 重いテストの鍵 (ディレクトリの mkdir / rmdir) |
| `test_start` / `test_end` | テストのコマンドの前 / 後 (`bg` はバックグラウンド実行か) |
| `pr` / `merge` | `gh pr create` / `gh pr merge` の後 |
| `release_check` | `heroku releases` の後 |
| `release` | `watch/release-watch.sh` が版の変化を検出 |

## 秘密を残さない設計

- コマンドの**全文は書かない**。種類 (`kind`) と短い名前 (`label`) だけを記録する。
- 環境変数・設定値を出すコマンド (`heroku config`、`releases:info` など) は使わない・拾わない。
- フックは何も出力せず、失敗しても本体を止めない。`*.jsonl` は `.gitignore` 済み。

## 限界

- 「前」の 27 本は**推定**。順番待ちと確認役を記録から拾えず実装に入っているので、待ちは下限。割合は報告のある 28 本で出している。
- 所要時間は担当を出してから最後の記録まで。本番の版が出た後の確認と片付けまで入る。
- バックグラウンドで走らせたテストの**終わりは拾えない** (終わりのフックが走るのは起動したとき)。
- 並行実行した確認役とテストの時間は重なるため、段階の和が合計を超える回がある。
- 「後」はまだ 2 本しかなく、順番待ちが減るかは測りきれていない。

## ライセンス

MIT (`LICENSE`)
