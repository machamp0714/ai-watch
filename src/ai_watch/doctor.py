from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from .adapters import ADAPTERS
from .config import Settings


def _check_bin(path: str) -> tuple[bool, str]:
    resolved = shutil.which(path) or (path if os.access(path, os.X_OK) else None)
    if not resolved:
        return False, f"not found / not executable: {path}"
    return True, resolved


def run_checks(settings: Settings) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    ok, detail = _check_bin(settings.claude_bin)
    if ok:
        try:
            proc = subprocess.run([settings.claude_bin, "--version"], capture_output=True, text=True, timeout=20)
            ver = proc.stdout.strip()
            if proc.returncode != 0 or not ver:
                ok, detail = False, f"{detail}: --version failed (rc={proc.returncode}): {proc.stderr.strip()[:120]}"
            else:
                detail = f"{detail} ({ver})"
        except Exception as e:  # バージョン取得失敗は NG 扱い
            ok, detail = False, f"{detail}: --version failed: {e}"
    out.append(("claude_bin", ok, detail))

    out.append(("npx_bin", *_check_bin(settings.npx_bin)))

    if settings.vault_dir.is_dir():
        missing = [n for n in ("profile.md", "backlog.md", "outputs.md", "log.md") if not (settings.vault_dir / n).exists()]
        out.append(("vault_dir", not missing, str(settings.vault_dir) + (f" (missing {missing}; run init-vault)" if missing else "")))
    else:
        out.append(("vault_dir", False, f"{settings.vault_dir} does not exist; run `ai-watch init-vault`"))

    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.data_dir / ".doctor-probe"
        probe.write_text("ok")
        probe.unlink()
        out.append(("data_dir", True, str(settings.data_dir)))
    except Exception as e:
        out.append(("data_dir", False, f"{settings.data_dir}: {e}"))

    profile = settings.data_dir / "playwright-profile"
    uses_x = any(s.type == "x_mcp" for s in settings.sources)
    if profile.is_dir():
        detail = str(profile)
    elif uses_x:
        detail = "missing: run Task 0 login (headed) first"
    else:
        detail = "not required (no x_mcp source)"
    out.append(("playwright_profile", (profile.is_dir() or not uses_x), detail))

    unknown = sorted({s.type for s in settings.sources if s.type not in ADAPTERS})
    out.append(("adapters", not unknown, f"{len(settings.sources)} sources" + (f"; unknown types: {unknown}" if unknown else "")))

    required = ["prompts/triage.md", "prompts/x_collect.md", "schemas/triage.schema.json",
                "schemas/x_items.schema.json", "config/playwright-mcp.json", "templates/vault/profile.md"]
    missing_files = [r for r in required if not (settings.root / r).exists()]
    out.append(("prompts", not missing_files, "all present" if not missing_files else f"missing: {missing_files}"))
    return out


def cmd_doctor(settings: Settings, args: argparse.Namespace) -> int:
    results = run_checks(settings)
    for name, ok, detail in results:
        print(f"{'OK ' if ok else 'NG '} {name:<20} {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1
