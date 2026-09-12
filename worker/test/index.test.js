import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import worker, { appendCheck } from "../src/index.js";


const DIGEST = {
  date: "2026-09-12",
  mode: "triaged",
  items_total: 12,
  items_shown: 4,
  cost_usd: 0.12,
  warnings: [],
  sections: {
    try: [
      {
        id: "aw-00000001",
        title: "架空ツールの新機能",
        url: "https://example.com/tool",
        source: "sample",
        mentions: ["sample", "community"],
        lang: "ja",
        score: 95,
        reason: "手元で挙動を確かめられる",
        summary: ["概要"],
        try_plan: "小さなfixtureで試す",
        article_angle: "従来版との違い",
        published_at: "2026-09-11T20:00:00+00:00"
      }
    ],
    update: [
      {
        id: "aw-00000002",
        title: "架空API 2.0",
        url: "https://example.com/api",
        source: "sample-release",
        mentions: ["sample-release"],
        lang: "ja",
        score: 80,
        reason: "公式更新",
        summary: ["新しいオプションを追加", "旧形式も継続"],
        try_plan: "",
        article_angle: "",
        published_at: null
      }
    ],
    read: [
      {
        id: "aw-00000003",
        title: "設計の読み物",
        url: "https://example.com/read",
        source: "sample-blog",
        mentions: ["sample-blog"],
        lang: "ja",
        score: 70,
        reason: "背景を理解できる",
        summary: ["要点"],
        try_plan: "",
        article_angle: "",
        published_at: null
      }
    ],
    overflow: [
      {
        id: "aw-00000004",
        title: "関連する話題",
        url: "https://example.com/topic",
        source: "sample-community",
        mentions: ["sample-community"],
        lang: "ja",
        score: 60,
        reason: "注目されている",
        summary: ["要点"],
        try_plan: "",
        article_angle: "",
        published_at: null
      }
    ]
  }
};


class FakeBucket {
  constructor(objects = {}) {
    this.objects = new Map(
      Object.entries(objects).map(([key, value]) => [
        key,
        { body: value, etag: "1", uploaded: new Date("2026-09-12T00:00:00Z") }
      ])
    );
    this.version = 1;
  }

  async get(key) {
    const object = this.objects.get(key);
    if (!object) return null;
    return {
      key,
      etag: object.etag,
      uploaded: object.uploaded,
      text: async () => object.body
    };
  }

  async put(key, body, options = {}) {
    const current = this.objects.get(key);
    const condition = options.onlyIf || {};
    if (condition.etagMatches && current?.etag !== condition.etagMatches) return null;
    if (condition.etagDoesNotMatch === "*" && current) return null;
    const etag = String(++this.version);
    this.objects.set(key, { body: String(body), etag, uploaded: new Date() });
    return { key, etag };
  }

  async list({ prefix, cursor } = {}) {
    assert.equal(cursor, undefined);
    const objects = [...this.objects.entries()]
      .filter(([key]) => key.startsWith(prefix || ""))
      .map(([key, value]) => ({ key, uploaded: value.uploaded }));
    return { objects, truncated: false };
  }
}


function env(bucket) {
  return { AI_WATCH_BUCKET: bucket };
}


test("Access保護後はworkers.devを有効にして既存R2だけをbindingする", () => {
  const config = JSON.parse(
    readFileSync(new URL("../../wrangler.jsonc", import.meta.url), "utf-8")
  );

  assert.equal(config.workers_dev, true);
  assert.equal(config.preview_urls, false);
  assert.deepEqual(config.r2_buckets, [
    { binding: "AI_WATCH_BUCKET", bucket_name: "ai-watch-prod" }
  ]);
});


test("日付一覧はdigest JSONだけを新しい順に表示する", async () => {
  const bucket = new FakeBucket({
    "vault/digests/2026-09-10.json": JSON.stringify({ ...DIGEST, date: "2026-09-10" }),
    "vault/digests/2026-09-12.json": JSON.stringify(DIGEST),
    "vault/digests/2026-09-12.md": "非公開Markdown本文"
  });

  const response = await worker.fetch(new Request("https://watch.example/"), env(bucket));
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.ok(html.indexOf("2026年9月12日") < html.indexOf("9月10日"));
  assert.match(html, /今日の4件を読む/);
  assert.doesNotMatch(html, /非公開Markdown本文/);
});


test("日付ページはPencilの階層とアクセシブルな操作を描画する", async () => {
  const bucket = new FakeBucket({
    "vault/digests/2026-09-12.json": JSON.stringify(DIGEST)
  });

  const response = await worker.fetch(
    new Request("https://watch.example/2026-09-12"),
    env(bucket)
  );
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-security-policy"), /default-src 'none'/);
  assert.match(html, /今日のダイジェスト/);
  assert.match(html, /試す候補/);
  assert.match(html, /公式アップデート/);
  assert.match(html, /読む/);
  assert.match(html, /注目/);
  assert.match(html, /data-item-id="aw-00000001"/);
  assert.match(html, /backlog に追加を予約/);
  assert.match(html, /X 投稿待ちに予約/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /@media \(max-width: 700px\)/);
  assert.doesNotMatch(html, /data-item-id="aw-00000003"[^>]*data-check/);
  assert.doesNotMatch(html, /data-item-id="aw-00000004"[^>]*data-check/);
});


test("OSがダーク設定でもPencilのライト配色を維持する", async () => {
  const bucket = new FakeBucket({
    "vault/digests/2026-09-12.json": JSON.stringify(DIGEST)
  });

  const response = await worker.fetch(
    new Request("https://watch.example/2026-09-12"),
    env(bucket)
  );
  const html = await response.text();

  assert.match(html, /<meta name="color-scheme" content="light">/);
  assert.match(html, /color-scheme: light;/);
  assert.match(html, /--bg: #f7f8f5;/);
  assert.match(html, /--paper: #ffffff;/);
  assert.match(html, /--ink: #202c27;/);
  assert.match(html, /--muted: #59665f;/);
  assert.match(html, /--green: #276047;/);
  assert.match(html, /--tint: #eaf1e9;/);
  assert.match(html, /--line: #d7ded7;/);
  assert.match(html, /--control: #7b887f;/);
  assert.doesNotMatch(html, /prefers-color-scheme:\s*dark/);
});


test("未生成の日付は500にせず案内を返す", async () => {
  const response = await worker.fetch(
    new Request("https://watch.example/2026-09-13"),
    env(new FakeBucket())
  );
  const html = await response.text();

  assert.equal(response.status, 404);
  assert.match(html, /この日のダイジェストは/);
  assert.match(html, /まだありません/);
  assert.match(html, /日付一覧へ/);
  assert.match(html, /もう一度確認/);
});


test("チェックAPIはdigestから判断種別を決めてJSONLへ追記する", async () => {
  const bucket = new FakeBucket({
    "vault/digests/2026-09-12.json": JSON.stringify(DIGEST)
  });
  const response = await worker.fetch(
    new Request("https://watch.example/api/checks/2026-09-12/aw-00000001", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ checked: true })
    }),
    env(bucket)
  );

  assert.equal(response.status, 200);
  const log = await (await bucket.get("checks/2026-09-12.jsonl")).text();
  const event = JSON.parse(log.trim());
  assert.deepEqual(
    { id: event.id, category: event.category, checked: event.checked },
    { id: "aw-00000001", category: "try", checked: true }
  );
  assert.match(event.timestamp, /^2026-|^20[0-9]{2}-/);
});


test("読む項目へのチェック要求を拒否する", async () => {
  const bucket = new FakeBucket({
    "vault/digests/2026-09-12.json": JSON.stringify(DIGEST)
  });
  const response = await worker.fetch(
    new Request("https://watch.example/api/checks/2026-09-12/aw-00000003", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ checked: true })
    }),
    env(bucket)
  );

  assert.equal(response.status, 422);
});


test("ETag競合時に再取得して両方のチェックを失わない", async () => {
  const bucket = new FakeBucket();
  await Promise.all([
    appendCheck(bucket, "checks/2026-09-12.jsonl", {
      id: "aw-00000001", category: "try", checked: true, timestamp: "2026-09-12T00:00:00Z"
    }),
    appendCheck(bucket, "checks/2026-09-12.jsonl", {
      id: "aw-00000002", category: "share", checked: true, timestamp: "2026-09-12T00:00:01Z"
    })
  ]);

  const lines = (await (await bucket.get("checks/2026-09-12.jsonl")).text())
    .trim()
    .split("\n")
    .map(JSON.parse);
  assert.deepEqual(new Set(lines.map((event) => event.id)), new Set(["aw-00000001", "aw-00000002"]));
});
