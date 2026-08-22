from __future__ import annotations

import subprocess
from datetime import date
from typing import Callable


def notify(title: str, message: str, run: Callable[..., object] = subprocess.run) -> None:
    """macOS 通知。失敗しても握りつぶす（通知のために夜間ジョブを落とさない）。"""
    safe = message.replace('"', "'")
    try:
        run(["osascript", "-e", f'display notification "{safe}" with title "{title}"'], check=False, timeout=10)
    except Exception:
        pass


def log_line(day: date, *, collected: int, new: int, n_try: int, mode: str, cost_usd: float, warnings: list[str]) -> str:
    failed = ", ".join(w.split(":", 1)[0] for w in warnings) or "なし"
    return (f"## [{day.isoformat()}] nightly | 取得 {collected} / 新規 {new} / try {n_try} "
            f"/ mode {mode} / cost ${cost_usd:.2f} / 失敗: {failed}")
