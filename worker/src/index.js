import { renderDigestPage, renderListPage, renderMissingPage } from "./page.js";


const DATE = /^\d{4}-\d{2}-\d{2}$/;
const ITEM_ID = /^aw-[0-9a-f]{8}$/;
const DIGEST_PREFIX = "vault/digests/";


function nonce() {
  return crypto.randomUUID().replaceAll("-", "");
}


function htmlResponse(html, token, status = 200) {
  return new Response(html, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "private, no-store",
      "content-security-policy": `default-src 'none'; style-src 'nonce-${token}'; script-src 'nonce-${token}'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`,
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff"
    }
  });
}


function jsonResponse(value, status = 200) {
  return Response.json(value, {
    status,
    headers: {
      "cache-control": "private, no-store",
      "x-content-type-options": "nosniff"
    }
  });
}


async function readJson(bucket, key) {
  const object = await bucket.get(key);
  if (!object) return null;
  return JSON.parse(await object.text());
}


async function listDigestDates(bucket) {
  const dates = [];
  let cursor;
  do {
    const page = await bucket.list({ prefix: DIGEST_PREFIX, cursor });
    for (const object of page.objects) {
      const match = object.key.match(/^vault\/digests\/(\d{4}-\d{2}-\d{2})\.json$/);
      if (match) dates.push(match[1]);
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return [...new Set(dates)].sort().reverse();
}


async function readCheckStates(bucket, date) {
  const object = await bucket.get(`checks/${date}.jsonl`);
  const states = new Map();
  if (!object) return states;
  for (const line of (await object.text()).split("\n")) {
    if (!line.trim()) continue;
    try {
      const event = JSON.parse(line);
      if (ITEM_ID.test(event.id) && ["try", "share"].includes(event.category) && typeof event.checked === "boolean") {
        states.set(event.id, event);
      }
    } catch {
      // 壊れた1行があっても閲覧画面全体は表示する。
    }
  }
  return states;
}


export async function appendCheck(bucket, key, event, attempts = 6) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const current = await bucket.get(key);
    const previous = current ? await current.text() : "";
    const separator = previous && !previous.endsWith("\n") ? "\n" : "";
    const onlyIf = current
      ? { etagMatches: current.etag }
      : { etagDoesNotMatch: "*" };
    const saved = await bucket.put(
      key,
      `${previous}${separator}${JSON.stringify(event)}\n`,
      { onlyIf, httpMetadata: { contentType: "application/x-ndjson" } }
    );
    if (saved !== null) return;
  }
  throw new Error("check write conflict");
}


function categoryFor(digest, id) {
  if ((digest.sections?.try || []).some((item) => item.id === id)) return "try";
  if ((digest.sections?.update || []).some((item) => item.id === id)) return "share";
  return null;
}


async function handleCheck(request, env, date, id, url) {
  if (!DATE.test(date) || !ITEM_ID.test(id)) return jsonResponse({ error: "入力が不正です" }, 400);
  const origin = request.headers.get("origin");
  if (origin && origin !== url.origin) return jsonResponse({ error: "許可されていない送信元です" }, 403);
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
    return jsonResponse({ error: "JSONを送信してください" }, 415);
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return jsonResponse({ error: "JSONを解釈できません" }, 400);
  }
  if (typeof body?.checked !== "boolean") return jsonResponse({ error: "checkedが不正です" }, 400);
  const digest = await readJson(env.AI_WATCH_BUCKET, `${DIGEST_PREFIX}${date}.json`);
  if (!digest) return jsonResponse({ error: "ダイジェストがありません" }, 404);
  const category = categoryFor(digest, id);
  if (!category) return jsonResponse({ error: "この項目はチェックできません" }, 422);
  const event = { id, category, checked: body.checked, timestamp: new Date().toISOString() };
  try {
    await appendCheck(env.AI_WATCH_BUCKET, `checks/${date}.jsonl`, event);
  } catch {
    return jsonResponse({ error: "保存が競合しました" }, 503);
  }
  return jsonResponse({ checked: body.checked, saved_at: event.timestamp });
}


async function handleList(env, token) {
  const dates = await listDigestDates(env.AI_WATCH_BUCKET);
  const entries = [];
  for (const date of dates) {
    let digest = null;
    if (entries.length === 0) {
      try {
        digest = await readJson(env.AI_WATCH_BUCKET, `${DIGEST_PREFIX}${date}.json`);
      } catch {
        digest = null;
      }
    }
    entries.push({ date, digest });
  }
  return htmlResponse(renderListPage(entries, token), token);
}


async function handleDigest(env, date, token) {
  let digest;
  try {
    digest = await readJson(env.AI_WATCH_BUCKET, `${DIGEST_PREFIX}${date}.json`);
  } catch {
    return htmlResponse(renderMissingPage(date, token), token, 502);
  }
  if (!digest) return htmlResponse(renderMissingPage(date, token), token, 404);
  const states = await readCheckStates(env.AI_WATCH_BUCKET, date);
  return htmlResponse(renderDigestPage(digest, states, token), token);
}


export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/") {
      return handleList(env, nonce());
    }
    const check = url.pathname.match(/^\/api\/checks\/(\d{4}-\d{2}-\d{2})\/(aw-[0-9a-f]{8})$/);
    if (request.method === "POST" && check) {
      return handleCheck(request, env, check[1], check[2], url);
    }
    const digest = url.pathname.match(/^\/(\d{4}-\d{2}-\d{2})\/?$/);
    if (request.method === "GET" && digest) {
      return handleDigest(env, digest[1], nonce());
    }
    return new Response("Not Found", { status: 404 });
  }
};
