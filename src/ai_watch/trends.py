from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from .models import Item

# 1 件ごとの mentions では「別々の記事が同じ話題に集中している」ことが見えないので、
# タイトルに共通して現れる固有名詞を数えて当日の話題クラスタを作る。

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[.\-][A-Za-z0-9]+)*|[ァ-ヴー]{3,}")
_CAMEL = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")

# 一般語と、収集対象そのもの（毎日どのソースにも出るので話題にならない）の語
_STOPWORDS = frozenset("""
the and for with you your this that what how are was were from into about just now new not can
does did its our their them they his her she him will would should could than then there here when
where which who why all any some more most very one two over only also still even back been being
make made like want need via per but try really says anyone other real own run running next after
think part line low hour weekly report problem live release released research search researcher
plan max mini game human coding subs reset token tokens tok
claude code codex llm llms openai anthropic chatgpt chat gpt model models agent agents agentic mcp
api app apps usage limit limits tool tools prompt prompts skill skills hook hooks plugin plugins
subagent github google gemini local open source free build built building work working works day
days today time week way thing things people help test tests file files data
""".split()) | frozenset({
    "エージェント", "コーディング", "プロンプト", "ツール", "モデル", "スキル", "ワークフロー", "ハーネス",
    "サーバー", "データ", "レビュー", "チーム", "プロジェクト", "コンテキスト", "ドキュメント", "アプリ",
    "システム", "サービス", "ユーザー", "セッション", "プラグイン", "メモリ", "タスク", "ブラウザ", "フック",
    "エンジニア", "エンジニアリング", "アーキテクチャ", "ローカル", "コード", "テスト", "リリース",
    "アップデート", "ガイド", "ベンチマーク",
})


@dataclass(frozen=True)
class Trend:
    term: str            # 正規化（小文字）した語
    label: str           # 表示用（最も多い表記）
    item_ids: list[str]
    sources: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Trend":
        return cls(term=d["term"], label=d["label"], item_ids=list(d["item_ids"]), sources=list(d["sources"]))


def _terms(title: str) -> dict[str, list[tuple[str, bool]]]:
    """タイトル中の語 → [(表記, 文頭か)]。LocalJev / jev-ultrafast のような複合語は部分語でも数える。"""
    out: dict[str, list[tuple[str, bool]]] = {}
    for m in _WORD.finditer(title):
        word = m.group(0)
        forms = {word}
        if word.isascii():
            for part in re.split(r"[.\-]", word):
                forms.add(part)
                forms.update(_CAMEL.findall(part))
        for form in forms:
            key = form.lower()
            if key in _STOPWORDS or (key.isascii() and (len(key) < 3 or key.isdigit())):
                continue
            out.setdefault(key, []).append((form, m.start() == 0))
    return out


def _is_name_like(forms: list[tuple[str, bool]]) -> bool:
    """固有名詞らしさ: 文頭以外で大文字始まり・数字入り・カタカナの表記が 7 割以上。"""
    body = [f for f, at_start in forms if not at_start] or [f for f, _ in forms]
    named = sum(1 for f in body if not f.isascii() or not f.islower() or any(c.isdigit() for c in f))
    return named / len(body) >= 0.7


def detect_trends(items: list[Item], *, min_items: int = 5, min_sources: int = 3, top: int = 3) -> list[Trend]:
    """min_items 件以上・min_sources ソース以上のタイトルに現れる固有名詞を、件数の多い順に top 件。"""
    by_term: dict[str, list[Item]] = defaultdict(list)
    forms: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for it in items:
        for key, fs in _terms(it.title).items():
            by_term[key].append(it)
            forms[key].extend(fs)

    candidates: list[Trend] = []
    for key, its in by_term.items():
        sources = sorted({m for it in its for m in it.mentions})
        if len(its) < min_items or len(sources) < min_sources or not _is_name_like(forms[key]):
            continue
        label = Counter(f for f, _ in forms[key] if f.lower() == key).most_common(1)[0][0]
        candidates.append(Trend(term=key, label=label, item_ids=[it.id for it in its], sources=sources))
    candidates.sort(key=lambda t: (-len(t.item_ids), -len(t.sources), t.term))

    # TypeSafe と Jev のように同じ記事群を指す語は 1 つにまとめる（件数の多い方を残す）
    picked: list[Trend] = []
    for cand in candidates:
        ids = set(cand.item_ids)
        if any(len(ids & set(p.item_ids)) >= 0.5 * len(ids) for p in picked):
            continue
        picked.append(cand)
        if len(picked) >= top:
            break
    return picked
