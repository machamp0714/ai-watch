from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .decisions import LINE_RE
from .vault import Vault


class CheckFileError(ValueError):
    pass


_ITEM_ID = re.compile(r"^aw-[0-9a-f]{8}$")


def _event(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckFileError("チェック記録の形式が不正です")
    item_id = value.get("id")
    category = value.get("category")
    checked = value.get("checked")
    timestamp = value.get("timestamp")
    if (
        not isinstance(item_id, str)
        or _ITEM_ID.fullmatch(item_id) is None
    ):
        raise CheckFileError("チェック記録の項目IDが不正です")
    if category not in ("try", "share"):
        raise CheckFileError("チェック記録の種別が不正です")
    if not isinstance(checked, bool):
        raise CheckFileError("チェック記録の状態が不正です")
    if not isinstance(timestamp, str):
        raise CheckFileError("チェック記録の時刻が不正です")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CheckFileError("チェック記録の時刻が不正です") from exc
    return {
        "id": item_id,
        "category": category,
        "checked": checked,
        "timestamp": timestamp,
    }


def _latest_events(path: Path) -> dict[str, dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CheckFileError(f"チェック記録{path.name}を読み込めません") from exc
    latest: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CheckFileError(
                f"チェック記録{path.name}の{number}行目が不正です"
            ) from exc
        event = _event(value)
        latest[event["id"]] = event
    return latest


def _apply_to_markdown(markdown: str, events: dict[str, dict[str, Any]]) -> str:
    expected = {"🧪": "try", "📣": "share"}

    def replace(match):
        event = events.get(match["id"])
        if event is None or event["category"] != expected[match["emoji"]]:
            return match.group(0)
        mark = "x" if event["checked"] else " "
        relative = match.start("mark") - match.start()
        line = match.group(0)
        return line[:relative] + mark + line[relative + 1 :]

    return LINE_RE.sub(replace, markdown)


def apply_check_events(
    vault: Vault,
    checks_dir: Path,
    today: date,
    *,
    days: int = 7,
    include_today: bool = True,
) -> int:
    """Workerの最終チェック状態を対象日のMarkdownへ反映する。"""
    changed = 0
    start = 0 if include_today else 1
    for back in range(start, days + 1):
        digest_day = today - timedelta(days=back)
        digest = vault.read_digest(digest_day)
        path = Path(checks_dir) / f"{digest_day.isoformat()}.jsonl"
        if digest is None or not path.exists():
            continue
        updated = _apply_to_markdown(digest, _latest_events(path))
        if updated != digest:
            vault.write_atomic(vault.digest_path(digest_day), updated)
            changed += 1
    return changed
