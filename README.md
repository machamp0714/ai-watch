# ai-watch

LLM 周辺（Claude Code / Codex の更新、注目記事）を夜間に収集・トリアージし、Obsidian vault に朝のダイジェストを生成する個人ツール。

## 全体の流れ

`nightly` は 5 段のパイプライン。各段は `data/work/YYYY-MM-DD/{stage}.json` に中間出力を残すので、`--from <stage>` で途中から再実行できる。

```mermaid
flowchart TD
    CRON["launchd 毎日 05:00 JST"] --> N["ai-watch nightly<br/>window = 直近 30h"]
    N --> SRC

    subgraph C1["1. collect — HTTP のみ / LLM 不使用"]
        direction LR
        SRC["sources.yaml<br/>公式 6 / EN 7 / JP 7 ソース"] --> AD["adapters<br/>rss・github_releases・github_file_sections<br/>hn_algolia・html_diff・note_search"]
        AD -->|"ホスト単位で直列化 + 2s delay<br/>ソース単位で例外を隔離 → warnings"| RAW[("data/raw/&lt;date&gt;/&lt;source&gt;.json")]
    end

    RAW --> XM

    subgraph C2["2. x_collect — LLM 使用"]
        direction LR
        XM["x_mcp adapter"] --> XC["claude -p + Playwright MCP<br/>閲覧系ツールのみ / budget $1.5"]
    end

    XC --> DG

    subgraph C3["3. sync_decisions — triage より前に回す"]
        direction LR
        DG["vault の直近 7 日の digest"] -->|"チェック済み [x] を回収"| DEC[("data/decisions.jsonl")]
        DEC --> BL["vault backlog.md / outputs.md へ反映"]
    end

    BL --> NRM["normalize<br/>URL 正規化 → 同一 URL を束ね → タイトル Jaccard ≥ 0.8 で束ね"]
    NRM --> SEEN[("data/seen.sqlite<br/>30 日以内に見た id を除外")]
    SEEN --> PR

    subgraph C4["4. triage — LLM 使用"]
        direction LR
        PRF["vault profile.md<br/>（関心プロファイル）"] --> PR["prompts/triage.md"]
        DECF["decisions 直近 30 件<br/>（few-shot）"] --> PR
        PR --> LLM["claude -p<br/>model=sonnet / effort=low / budget $2.0"]
        LLM -->|成功| TR["try / read / update / noise に分類<br/>+ 公式ソースの noise を update へ自動昇格"]
        LLM -->|"失敗（retry 1 回後）"| FB["fallback_rank<br/>metrics 順 / mode=untriaged"]
    end

    TR --> RD
    FB --> RD

    subgraph C5["5. render"]
        direction LR
        RD["render_digest<br/>try 3 / read 3 / update 5 件"] --> MD["digest.md<br/>同日再実行時は既存のチェックを引き継ぐ"]
    end

    MD --> OUT["vault 00_Self/ai-watch/digests/&lt;date&gt;.md"]
    OUT --> LOG["vault log.md に 1 行追記"]
    OUT --> MARK[("seen.sqlite に mark")]
    OUT --> NOTIF["warnings あり or untriaged なら通知"]
```

LLM（`claude -p`）を使うのは **x_collect と triage の 2 箇所だけ**。残りの 20 ソースの収集は素の HTTP で、トークンを消費しない。

`--dry-run` を付けると vault には書かず `data/work/<date>/digest.md` に出力し、`sync_decisions` と `seen.sqlite` への書き込みもスキップする。

### 朝のチェックが翌日に効く仕組み

```mermaid
flowchart LR
    A["朝：digest の try / share にチェックを付ける"] --> B["翌晩の sync_decisions が回収"]
    B --> C[("data/decisions.jsonl")]
    C --> D["vault backlog.md / outputs.md に転記"]
    C --> E["triage プロンプトの few-shot<br/>（直近 30 件）"]
    E --> F["翌日以降の分類・スコアに反映"]
```

チェックを付けなかった項目は `skip_implicit` として記録される。

## セットアップ

```bash
uv sync
uv run ai-watch init-vault          # vault に profile.md 等を作る（既存は触らない）
uv run ai-watch doctor              # claude / npx / vault / MCP 設定の事前チェック
```

## 手動実行

```bash
uv run ai-watch nightly --dry-run           # vault に書かず data/work/<date>/digest.md に出す
uv run ai-watch nightly                     # 本番
uv run ai-watch nightly --from triage       # 収集済みデータから再トリアージ
uv run ai-watch collect --only hn           # 1 ソースだけ疎通
uv run ai-watch sync                        # ダイジェストのチェックを今すぐ反映
```

## launchd（毎日 05:00）

```bash
mkdir -p logs
cp launchd/com.machamp.ai-watch.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.machamp.ai-watch.plist
launchctl kickstart -k gui/$(id -u)/com.machamp.ai-watch      # 今すぐ 1 回走らせて確認
tail -f logs/nightly.err.log
```

解除: `launchctl bootout gui/$(id -u)/com.machamp.ai-watch`

スリープで 05:00 を過ぎた場合は次の起床時に実行される（launchd の StartCalendarInterval の仕様）。

## データの置き場

- `data/raw/YYYY-MM-DD/{source}.json` — 取得生データ。90 日で消す: `find data/raw -mindepth 1 -maxdepth 1 -type d -mtime +90 -exec rm -r {} +`（v1 では手動）
- `data/work/YYYY-MM-DD/*.json` — 段ごとの中間出力（`--from` 再実行用）
- `data/seen.sqlite` — 機械の既読
- `data/decisions.jsonl` — 朝のチェック（try / share / skip_implicit）
- vault `00_Self/ai-watch/` — digests / backlog / outputs / log / profile
