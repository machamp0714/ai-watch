# ai-watch 運用スキーマ

このディレクトリは `~/repo/ai-watch` の夜間ジョブが書き込む **LLM 情報ダイジェストの運用面**。Claude Code からここを扱うときの規約。

## ファイルの役割

| ファイル | 書き手 | 役割 |
|---|---|---|
| `profile.md` | 人 | トリアージ基準（興味領域・除外・現在のテーマ）。夜間ジョブがプロンプトに埋め込む。**ここを育てるのが唯一のチューニング手段** |
| `digests/YYYY-MM-DD.md` | ジョブ | 日次ダイジェスト（`type: record`）。朝の判断 UI。`- [ ] 🧪` に `[x]` で試す候補、`- [ ] 📣`（Claude Code / Codex 等の公式更新。要約は下の箇条書き）に `[x]` で X 投稿待ちへ。3 日放置した 🧪 は暗黙の見送り。`## 📖 読む` と `## 👀 注目` は「記事 / ソース / 要約」のテーブルで、読むだけ（チェック無し） |
| `backlog.md` | ジョブ→人 | 試す候補の待ち行列。`/ai-watch pick` で 1 件選ぶと `[x]` と実験ノートへのリンクが付く |
| `experiments/YYYY-MM-DD <title>.md` | 人 | 週末に試した記録（`type: record`）。仮説 / 手順 / 結果 / 学び / 記事の切り口 / X 草稿 |
| `outputs.md` | ジョブ→人 | 発信ログ。投稿待ち → 投稿済み（URL・日付・反応） |
| `log.md` | ジョブ | `## [YYYY-MM-DD] nightly | …` の 1 行ログ。`grep "^## \[" log.md | tail -5` で直近を見る |

## 手順

- **ingest（夜間・自動）**: `uv run --project ~/repo/ai-watch ai-watch nightly`。05:00 に launchd が実行。手動で回すときも同じ
- **query（朝）**: 今日の `digests/` を開いて読み、🧪 / 📣 にチェック。それ以外の操作は不要
- **sync（任意）**: チェックを今すぐ反映したいとき `uv run --project ~/repo/ai-watch ai-watch sync`
- **pick（週末）**: `/ai-watch pick` → backlog 上位 5 件から 1 件選び、`experiments/` にノートを作る
- **lint**: ダイジェストの `^aw-xxxxxxxx` ブロック ID（テーブル内は `%%^aw-…%%` コメント）を消さない（回収に使う）。`backlog.md` の `## 候補` / `## 完了`、`outputs.md` の `## 投稿待ち` / `## 投稿済み` の見出しを変えない

## 規約

- ダイジェスト・実験ノートは `type: record`。`profile.md` と本ファイルは `type: spec`
- ダイジェストは 30 日を超えたら削除してよい（raw は `~/repo/ai-watch/data/raw/` に 90 日残る）
- ハブページではないので、ここから他ノートへのリンクは実験ノートの「学び」から `02_Knowledge/` へ張る
