from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True)
class Vault:
    """vault 内の ai-watch ディレクトリ。ここ以外は vault に触らない。書き込みは常に atomic。"""
    root: Path

    @property
    def digests(self) -> Path: return self.root / "digests"
    @property
    def experiments(self) -> Path: return self.root / "experiments"
    @property
    def backlog(self) -> Path: return self.root / "backlog.md"
    @property
    def outputs(self) -> Path: return self.root / "outputs.md"
    @property
    def log(self) -> Path: return self.root / "log.md"
    @property
    def profile(self) -> Path: return self.root / "profile.md"
    @property
    def claude_md(self) -> Path: return self.root / "CLAUDE.md"

    def digest_path(self, day: date) -> Path:
        return self.digests / f"{day.isoformat()}.md"

    def read_digest(self, day: date) -> str | None:
        p = self.digest_path(day)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def recent_digests(self, today: date, days: int = 7) -> list[tuple[date, str]]:
        out = []
        for back in range(1, days + 1):
            d = today - timedelta(days=back)
            text = self.read_digest(d)
            if text is not None:
                out.append((d, text))
        return out

    def read_profile(self) -> str:
        return self.profile.read_text(encoding="utf-8") if self.profile.exists() else ""

    @staticmethod
    def write_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def append_line(self, path: Path, line: str) -> None:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current and not current.endswith("\n"):
            current += "\n"
        self.write_atomic(path, current + line.rstrip("\n") + "\n")

    def insert_under_heading(self, path: Path, heading: str, line: str, *, dedupe_key: str | None = None) -> bool:
        """heading セクションの末尾（次の ## の直前）に line を足す。dedupe_key が既にあれば何もしない。"""
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if dedupe_key and dedupe_key in text:
            return False
        lines = text.splitlines()
        try:
            start = next(i for i, l in enumerate(lines) if l.strip() == heading)
        except StopIteration:
            self.write_atomic(path, (text.rstrip("\n") + "\n\n" if text.strip() else "") + f"{heading}\n{line}\n")
            return True
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, line)
        self.write_atomic(path, "\n".join(lines).rstrip("\n") + "\n")
        return True


def init_vault(vault: Vault, templates_dir: Path) -> list[Path]:
    """テンプレートから vault の初期ファイルを作る。既存ファイルは触らない。"""
    created: list[Path] = []
    for d in (vault.digests, vault.experiments):
        if not d.exists():
            d.mkdir(parents=True)
            created.append(d)
    for name in ("CLAUDE.md", "profile.md", "backlog.md", "outputs.md", "log.md"):
        target = vault.root / name
        if not target.exists():
            vault.write_atomic(target, (templates_dir / name).read_text(encoding="utf-8"))
            created.append(target)
    return created
