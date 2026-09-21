const COLORS = `
:root {
  color-scheme: light;
  --bg: #f7f8f5;
  --paper: #ffffff;
  --ink: #202c27;
  --muted: #59665f;
  --green: #276047;
  --tint: #eaf1e9;
  --line: #d7ded7;
  --control: #7b887f;
  --error-bg: #fff2ee;
  --error: #a13329;
}
`;


function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}


function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? escapeHtml(url.href) : "#";
  } catch {
    return "#";
  }
}


function dateParts(date) {
  const value = new Date(`${date}T00:00:00+09:00`);
  return {
    full: new Intl.DateTimeFormat("ja-JP", {
      timeZone: "Asia/Tokyo",
      year: "numeric",
      month: "long",
      day: "numeric",
      weekday: "short"
    }).format(value),
    short: new Intl.DateTimeFormat("ja-JP", {
      timeZone: "Asia/Tokyo",
      month: "long",
      day: "numeric",
      weekday: "short"
    }).format(value),
    month: new Intl.DateTimeFormat("ja-JP", {
      timeZone: "Asia/Tokyo",
      year: "numeric",
      month: "long"
    }).format(value)
  };
}


function layout(title, body, nonce, { compact = false } = {}) {
  return `<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>${escapeHtml(title)} · ai-watch</title>
  <style nonce="${nonce}">
    ${COLORS}
    * { box-sizing: border-box; }
    html { background: var(--bg); color: var(--ink); font-family: "Noto Sans JP", system-ui, sans-serif; }
    body { margin: 0; background: var(--bg); color: var(--ink); font-size: 14px; line-height: 1.6; overflow-wrap: anywhere; }
    a { color: var(--green); text-underline-offset: 3px; }
    button, summary, a { -webkit-tap-highlight-color: transparent; }
    button:focus-visible, summary:focus-visible, a:focus-visible { outline: 2px solid var(--green); outline-offset: 2px; }
    .site-header { height: 80px; padding: 0 40px; background: var(--paper); border-bottom: 1px solid color-mix(in srgb, var(--line) 45%, transparent); display: flex; align-items: center; justify-content: space-between; }
    .brand { color: var(--ink); font-size: 24px; font-weight: 600; text-decoration: none; }
    .brand-copy { color: var(--muted); font-size: 12px; margin-left: 12px; }
    .header-meta { color: var(--muted); display: flex; align-items: center; gap: 24px; }
    .header-meta strong { color: var(--green); font-weight: 600; }
    .mobile-list { display: none; }
    .button { min-height: 44px; padding: 0 12px; border: 1px solid var(--control); border-radius: 6px; background: var(--paper); color: var(--ink); display: inline-flex; align-items: center; gap: 8px; font: inherit; font-size: 13px; font-weight: 500; text-decoration: none; cursor: pointer; }
    .button:hover { background: var(--tint); border-color: var(--green); }
    .button.primary { color: var(--green); background: var(--tint); border-color: var(--green); }
    .page { max-width: 1440px; margin: 0 auto; }
    .list-main { max-width: 960px; margin: 0 auto; padding: 48px; }
    h1 { font-size: 36px; line-height: 1.4; margin: 0; font-weight: 600; }
    .eyebrow, .muted { color: var(--muted); }
    .eyebrow { font-size: 14px; font-weight: 500; }
    .lede { margin: 16px 0 28px; color: var(--muted); }
    .latest { padding: 24px; border: 1px solid var(--green); border-radius: 8px; background: var(--tint); }
    .latest-label { color: var(--green); font-size: 12px; font-weight: 600; }
    .latest h2 { margin: 8px 0; font-size: 24px; }
    .latest p { color: var(--muted); margin: 0 0 16px; }
    .month { color: var(--muted); font-size: 14px; margin: 24px 0 8px; }
    .date-row { min-height: 56px; padding: 8px; border-bottom: 1px solid var(--line); color: var(--ink); display: flex; align-items: center; justify-content: space-between; font-size: 16px; font-weight: 500; text-decoration: none; }
    .date-row:hover { background: var(--tint); color: var(--green); }
    .digest-layout { display: grid; grid-template-columns: 208px minmax(0, 1fr); gap: 48px; padding: 40px 40px 56px; }
    .sidebar { position: sticky; top: 24px; align-self: start; display: grid; gap: 24px; }
    .sidebar-date { font-weight: 600; }
    .toc { display: grid; gap: 4px; }
    .toc a { min-height: 48px; padding: 0 12px; border-radius: 6px; color: var(--ink); text-decoration: none; display: flex; align-items: center; justify-content: space-between; }
    .toc a:first-child, .toc a:hover { color: var(--green); background: var(--tint); }
    .sidebar-note { border-top: 1px solid var(--line); padding-top: 24px; color: var(--muted); font-size: 12px; }
    .digest-main { min-width: 0; }
    .digest-head { padding-bottom: 32px; border-bottom: 1px solid var(--line); }
    .digest-head h1 { margin-top: 4px; }
    .metrics { color: var(--muted); margin: 0; }
    .warning, .untriaged { margin-top: 16px; padding: 16px; border-radius: 8px; background: var(--error-bg); color: var(--error); }
    .mobile-toc { display: none; }
    .digest-section { border-bottom: 1px solid var(--line); padding: 32px 0; }
    .section-summary { list-style: none; cursor: pointer; display: flex; align-items: baseline; gap: 12px; }
    .section-summary::-webkit-details-marker { display: none; }
    .section-number { color: var(--green); font-size: 13px; font-weight: 600; }
    .section-title { font-size: 22px; font-weight: 600; }
    .section-count, .section-copy { color: var(--muted); font-size: 13px; }
    .section-copy { margin: 8px 0 16px; }
    .cards { display: grid; gap: 16px; }
    .try-card { padding: 24px; border-radius: 8px; background: var(--paper); }
    .item-title { margin: 0; font-size: 18px; line-height: 1.5; font-weight: 600; }
    .item-title a { color: var(--ink); text-decoration: none; }
    .item-title a:hover { color: var(--green); text-decoration: underline; }
    .source { margin: 8px 0; color: var(--green); font-size: 12px; }
    .reason { margin: 8px 0 16px; color: var(--muted); }
    .item-details { margin: 12px 0; padding: 16px; border-radius: 8px; background: var(--bg); }
    .item-details summary { color: var(--green); cursor: pointer; font-weight: 600; }
    .item-details dl { margin: 12px 0 0; }
    .item-details dt { color: var(--muted); font-size: 12px; font-weight: 600; }
    .item-details dd { margin: 2px 0 12px; }
    .check-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .check-button { min-height: 44px; }
    .check-button:hover { background: var(--tint); border-color: var(--green); }
    .check-button.is-saved { color: var(--green); background: var(--tint); border-color: var(--green); }
    .check-button:disabled { cursor: wait; opacity: .72; background: color-mix(in srgb, var(--paper) 60%, var(--line)); }
    .item-status { min-height: 22px; color: var(--green); font-size: 13px; }
    .item-status.has-error { width: 100%; padding: 12px; color: var(--error); background: var(--error-bg); border-radius: 8px; }
    .retry { margin-left: 8px; }
    .updates { border-radius: 8px; background: var(--paper); padding: 0 24px; }
    .update-row { padding: 24px 0; border-bottom: 1px solid var(--line); }
    .update-row:last-child { border-bottom: 0; }
    .summary-lines { color: var(--muted); margin: 8px 0 12px; padding-left: 20px; }
    .more-summary { color: var(--green); cursor: pointer; }
    .reading-list { display: grid; }
    .trend { margin-bottom: 16px; }
    .trend-title { margin: 0 0 8px; font-size: 16px; }
    .reading-item { padding: 0 0 16px; margin-bottom: 16px; border-bottom: 1px solid var(--line); }
    .reading-item:last-child { border-bottom: 0; }
    .overflow-more summary { min-height: 44px; width: fit-content; }
    .empty-main { max-width: 960px; min-height: 450px; margin: 0 auto; padding: 64px 96px; }
    .empty-icon { color: var(--green); font-size: 40px; }
    .empty-main h1 { margin: 16px 0; font-size: 30px; white-space: pre-line; }
    .actions { display: flex; gap: 12px; margin-top: 18px; flex-wrap: wrap; }
    .footer-note { margin-top: 24px; color: var(--muted); font-size: 12px; }
    @media (max-width: 700px) {
      .site-header { height: 64px; padding: 0 20px; }
      .brand { font-size: 22px; }
      .brand-copy, .header-meta .private { display: none; }
      .header-meta { display: none; }
      .mobile-list { display: inline-flex; }
      .page { width: 100%; }
      .list-main { padding: 24px 20px 32px; }
      h1 { font-size: 26px; }
      .latest { padding: 16px; }
      .latest h2 { font-size: 20px; }
      .digest-layout { display: block; padding: 24px 20px 32px; }
      .sidebar { display: none; }
      .digest-head { padding-bottom: 24px; }
      .mobile-toc { margin-top: 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
      .mobile-toc a { min-height: 44px; justify-content: space-between; }
      .digest-section { padding: 24px 0; }
      .section-summary { min-height: 44px; align-items: center; }
      .section-title { font-size: 22px; }
      .try-card { padding: 16px; }
      .item-title { font-size: 17px; }
      .try-card:first-child .item-title { font-size: 18px; }
      .check-button { width: 100%; min-height: 48px; justify-content: center; }
      .updates { padding: 0 16px; }
      .empty-main { padding: 48px 20px; }
      .empty-main h1 { font-size: 26px; }
    }
  </style>
</head>
<body class="${compact ? "compact" : ""}">
  <header class="site-header">
    <div><a class="brand" href="/">ai-watch</a><span class="brand-copy">日々の AI を、自分の次の一歩に。</span></div>
    <div class="header-meta"><strong>ダイジェスト</strong><span class="private">▣ 自分だけ</span></div>
    <a class="button mobile-list" href="/">日付一覧</a>
  </header>
  ${body}
</body>
</html>`;
}


function counts(digest) {
  const sections = digest.sections || {};
  return {
    try: sections.try?.length || 0,
    update: sections.update?.length || 0,
    read: sections.read?.length || 0,
    overflow: sections.overflow?.length || 0
  };
}


function sourceLabel(item) {
  const extra = Math.max(0, (item.mentions?.length || 1) - 1);
  return `${escapeHtml(item.source)}${extra ? ` +${extra}` : ""}`;
}


function checkControl(item, date, category, checked) {
  const uncheckedLabel = category === "try" ? "backlog に追加を予約" : "X 投稿待ちに予約";
  const label = checked ? "予約済み" : uncheckedLabel;
  return `<div class="check-row">
    <button type="button" class="button check-button${checked ? " is-saved" : ""}" data-check data-item-id="${escapeHtml(item.id)}" data-date="${date}" data-checked="${checked}" data-unchecked-label="${uncheckedLabel}" aria-pressed="${checked}">
      <span data-check-icon aria-hidden="true">${checked ? "✓" : "□"}</span><span data-check-label>${label}</span>
    </button>
    <span class="item-status" data-status aria-live="polite"></span>
  </div>`;
}


function tryCard(item, date, checked, expanded = false) {
  const hasDetails = item.try_plan || item.article_angle;
  return `<article class="try-card" data-item-id="${escapeHtml(item.id)}">
    <h3 class="item-title"><a href="${safeUrl(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a></h3>
    <p class="source">${sourceLabel(item)} ↗</p>
    <p class="reason">${escapeHtml(item.reason)}</p>
    ${hasDetails ? `<details class="item-details" ${expanded ? "open" : ""}><summary>試し方・記事の切り口</summary><dl>${item.try_plan ? `<dt>試し方</dt><dd>${escapeHtml(item.try_plan)}</dd>` : ""}${item.article_angle ? `<dt>記事の切り口</dt><dd>${escapeHtml(item.article_angle)}</dd>` : ""}</dl></details>` : ""}
    ${checkControl(item, date, "try", checked)}
  </article>`;
}


function summaryLines(lines, start = 0) {
  return (lines || []).slice(start).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
}


function updateRow(item, date, checked) {
  const summaries = item.summary || [];
  return `<article class="update-row" data-item-id="${escapeHtml(item.id)}">
    <h3 class="item-title"><a href="${safeUrl(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a></h3>
    <p class="source">${sourceLabel(item)} ↗</p>
    ${summaries[0] ? `<ul class="summary-lines"><li>${escapeHtml(summaries[0])}</li></ul>` : ""}
    ${summaries.length > 1 ? `<details><summary class="more-summary">ほか ${summaries.length - 1} 件の変更を表示</summary><ul class="summary-lines">${summaryLines(summaries, 1)}</ul></details>` : ""}
    ${checkControl(item, date, "share", checked)}
  </article>`;
}


function readingItem(item) {
  return `<article class="reading-item" data-item-id="${escapeHtml(item.id)}">
    <h3 class="item-title"><a href="${safeUrl(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a></h3>
    <p class="source">${sourceLabel(item)} ↗</p>
    <p class="reason">${escapeHtml((item.summary || [])[0] || item.reason)}</p>
  </article>`;
}


function trendBlock(trend) {
  const links = (trend.items || []).map((item) => `<article class="reading-item">
      <h3 class="item-title"><a href="${safeUrl(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a></h3>
      <p class="source">${escapeHtml(item.source)} ↗</p>
      ${(item.summary || [])[0] ? `<p class="reason">${escapeHtml(item.summary[0])}</p>` : ""}
    </article>`).join("");
  return `<div class="trend">
    <h3 class="trend-title">${escapeHtml(trend.label)} <span class="section-count">${Number(trend.count) || 0}件・${(trend.sources || []).length}ソース</span></h3>
    <div class="reading-list">${links}</div>
  </div>`;
}


function section(number, id, title, count, copy, content, open = true) {
  return `<details class="digest-section" id="${id}" data-digest-section ${open ? "open" : ""}>
    <summary class="section-summary"><span class="section-number">${number}</span><span class="section-title">${title}</span><span class="section-count">${count}件</span></summary>
    <p class="section-copy">${copy}</p>
    ${content}
  </details>`;
}


function clientScript(nonce) {
  return `<script nonce="${nonce}">
  (() => {
    const mobile = matchMedia("(max-width: 700px)");
    const arrange = () => document.querySelectorAll("[data-digest-section]").forEach((section, index) => {
      if (!section.dataset.touched) section.open = !mobile.matches || index === 0;
    });
    document.querySelectorAll("[data-digest-section] > summary").forEach((summary) => summary.addEventListener("click", () => {
      summary.parentElement.dataset.touched = "1";
    }));
    arrange();
    mobile.addEventListener("change", arrange);

    const setState = (button, checked) => {
      button.dataset.checked = String(checked);
      button.setAttribute("aria-pressed", String(checked));
      button.classList.toggle("is-saved", checked);
      button.querySelector("[data-check-icon]").textContent = checked ? "✓" : "□";
      button.querySelector("[data-check-label]").textContent = checked ? "予約済み" : button.dataset.uncheckedLabel;
    };
    const save = async (button, desired) => {
      if (button.disabled) return;
      const previous = button.dataset.checked === "true";
      const status = button.parentElement.querySelector("[data-status]");
      button.disabled = true;
      button.querySelector("[data-check-label]").textContent = "保存中…";
      status.className = "item-status";
      status.textContent = "保存中…";
      try {
        const response = await fetch("/api/checks/" + button.dataset.date + "/" + button.dataset.itemId, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ checked: desired })
        });
        if (!response.ok) throw new Error("save failed");
        setState(button, desired);
        status.textContent = desired ? "予約を保存しました。翌日の処理で反映されます。" : "予約の解除を保存しました。作成済みの項目は削除されません。";
      } catch {
        setState(button, previous);
        status.className = "item-status has-error";
        status.innerHTML = '保存できませんでした。選択は元に戻しました。<button type="button" class="button retry">再試行</button>';
        status.querySelector(".retry").addEventListener("click", () => save(button, desired), { once: true });
      } finally {
        button.disabled = false;
        button.focus();
      }
    };
    document.querySelectorAll("[data-check]").forEach((button) => button.addEventListener("click", () => save(button, button.dataset.checked !== "true")));
  })();
  </script>`;
}


export function renderDigestPage(digest, checkStates, nonce) {
  const date = escapeHtml(digest.date);
  const parts = dateParts(digest.date);
  const amount = counts(digest);
  const sections = digest.sections || {};
  const state = (id) => checkStates.get(id)?.checked === true;
  const overflow = sections.overflow || [];
  const trends = digest.trends || [];
  const overflowVisible = overflow.slice(0, 3);
  const overflowRest = overflow.slice(3);
  const warnings = (digest.warnings || []).map((warning) => escapeHtml(warning)).join(" / ");
  const main = `<main class="page digest-layout">
    <aside class="sidebar">
      <a class="button" href="/">← 日付一覧</a>
      <div class="sidebar-date">${escapeHtml(parts.full)}</div>
      <nav class="toc" aria-label="ページ内の目次">
        ${trends.length ? `<a href="#trends">今日の話題 <span>${trends.length}</span></a>` : ""}
        <a href="#try">試す候補 <span>${amount.try}</span></a>
        <a href="#update">公式アップデート <span>${amount.update}</span></a>
        <a href="#read">読む <span>${amount.read}</span></a>
        <a href="#overflow">注目 <span>${amount.overflow}</span></a>
      </nav>
      <p class="sidebar-note">気になる項目にチェック。<br>選択はその場で保存され、翌日の処理で反映されます。</p>
    </aside>
    <div class="digest-main">
      <header class="digest-head">
        <div class="eyebrow">${escapeHtml(parts.full)}</div>
        <h1>今日のダイジェスト</h1>
        <p class="metrics">${Number(digest.items_total) || 0}件から ${Number(digest.items_shown) || 0}件をピックアップ</p>
        <nav class="mobile-toc" aria-label="カテゴリへ移動">
          <a class="button primary" href="#try">試す候補 <span>${amount.try}</span></a>
          <a class="button" href="#update">公式更新 <span>${amount.update}</span></a>
          <a class="button" href="#read">読む <span>${amount.read}</span></a>
          <a class="button" href="#overflow">注目 <span>${amount.overflow}</span></a>
        </nav>
        ${digest.mode === "untriaged" ? '<div class="untriaged" role="status">トリアージ失敗・metrics 順で表示しています。</div>' : ""}
        ${warnings ? `<div class="warning" role="status">取得警告: ${warnings}</div>` : ""}
      </header>
      ${trends.length ? section("00", "trends", "今日の話題", trends.length, "複数のソースで同時に話題になっている固有名詞と、その代表記事。", trends.map(trendBlock).join("")) : ""}
      ${section("01", "try", "試す候補", amount.try, "手を動かして確かめたい記事。チェックすると backlog への追加を予約します。", `<div class="cards">${(sections.try || []).map((item, index) => tryCard(item, date, state(item.id), index === 0)).join("")}</div>`)}
      ${section("02", "update", "公式アップデート", amount.update, "発信したい更新にチェックすると、X 投稿待ちへの追加を予約します。", `<div class="updates">${(sections.update || []).map((item) => updateRow(item, date, state(item.id))).join("")}</div>`)}
      ${section("03", "read", "読む", amount.read, "じっくり読んでおきたい記事。タイトルから元の記事を開けます。", `<div class="reading-list">${(sections.read || []).map(readingItem).join("")}</div>`)}
      ${section("04", "overflow", "注目", amount.overflow, "気になる話題をまとめて確認。", `<div class="reading-list">${overflowVisible.map(readingItem).join("")}</div>${overflowRest.length ? `<details class="overflow-more"><summary class="button">残り${overflowRest.length}件を表示</summary><div class="reading-list">${overflowRest.map(readingItem).join("")}</div></details>` : ""}`)}
      <div class="actions"><a class="button" href="/">← 日付一覧に戻る</a></div>
    </div>
  </main>${clientScript(nonce)}`;
  return layout("今日のダイジェスト", main, nonce);
}


export function renderListPage(entries, nonce) {
  const latest = entries[0];
  const latestCounts = latest?.digest ? counts(latest.digest) : null;
  let currentMonth = "";
  const rows = entries.slice(1).map((entry) => {
    const parts = dateParts(entry.date);
    const heading = parts.month !== currentMonth ? `<h2 class="month">${escapeHtml(parts.month)}</h2>` : "";
    currentMonth = parts.month;
    return `${heading}<a class="date-row" href="/${entry.date}"><span>${escapeHtml(parts.short)}</span><span aria-hidden="true">↗</span></a>`;
  }).join("");
  const latestCard = latest ? `<section class="latest">
    <div class="latest-label">最新のダイジェスト</div>
    <h2>${escapeHtml(dateParts(latest.date).full)}</h2>
    ${latestCounts ? `<p>試す候補 ${latestCounts.try}件 ・ 公式アップデート ${latestCounts.update}件 ・ 読む ${latestCounts.read}件 ・ 注目 ${latestCounts.overflow}件</p>` : ""}
    <a class="button primary" href="/${latest.date}">↗ 今日の${Number(latest.digest?.items_shown) || 0}件を読む</a>
  </section>` : '<p class="muted">生成済みのダイジェストはありません。</p>';
  return layout("ダイジェスト", `<main class="list-main"><h1>ダイジェスト</h1><p class="lede">日付を選んで、その日に集まった AI の話題を読む。</p>${latestCard}${rows}<p class="footer-note">生成済みの日付を新しい順に表示しています。</p></main>`, nonce, { compact: true });
}


export function renderMissingPage(date, nonce) {
  const label = dateParts(date).full;
  return layout("未生成のダイジェスト", `<main class="empty-main"><div class="empty-icon" aria-hidden="true">◴</div><p class="eyebrow">${escapeHtml(label)}</p><h1>この日のダイジェストは\nまだありません</h1><p class="muted">生成されたダイジェストは、日付一覧から読むことができます。時間をおいて、もう一度確認してください。</p><div class="actions"><a class="button primary" href="/">← 日付一覧へ</a><a class="button" href="/${escapeHtml(date)}">↻ もう一度確認</a></div></main>`, nonce, { compact: true });
}
