from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlparse

import httpx

from .adapters import ADAPTERS
from .config import Settings
from .r2_sync import R2Config, R2SyncError


def _check_bin(path: str) -> tuple[bool, str]:
    resolved = shutil.which(path) or (path if os.access(path, os.X_OK) else None)
    if not resolved:
        return False, f"not found / not executable: {path}"
    return True, resolved


def run_checks(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
    ci: bool = False,
    http_get: Callable[..., object] = httpx.get,
) -> list[tuple[str, bool, str]]:
    environ = os.environ if environ is None else environ
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

    anthropic_key = environ.get("ANTHROPIC_API_KEY", "")
    out.append(
        (
            "anthropic_api_key",
            bool(anthropic_key.strip()),
            "設定済み" if anthropic_key.strip() else "ANTHROPIC_API_KEYが未設定",
        )
    )

    r2_names = (
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "CLOUDFLARE_ACCOUNT_ID",
        "R2_BUCKET_NAME",
    )
    missing_r2 = [name for name in r2_names if not environ.get(name, "").strip()]
    if missing_r2:
        out.append(("r2_environment", False, f"未設定: {', '.join(missing_r2)}"))
    else:
        try:
            R2Config.from_env(environ)
            out.append(("r2_environment", True, "必要な4変数を設定済み"))
        except R2SyncError as exc:
            out.append(("r2_environment", False, str(exc)))

    aws_ok, aws_detail = _check_bin("aws")
    if aws_ok:
        try:
            proc = subprocess.run(
                ["aws", "--version"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            version = f"{proc.stdout}\n{proc.stderr}".strip()
            aws_ok = proc.returncode == 0 and version.startswith("aws-cli/2.")
            aws_detail = version if aws_ok else "AWS CLI v2を利用できません"
        except Exception:
            aws_ok, aws_detail = False, "AWS CLI v2の確認に失敗しました"
    out.append(("aws_cli", aws_ok, aws_detail))

    if ci:
        out.append(("npx_bin", True, "CIでは不要（x_collectを省略）"))
    else:
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

    uses_x = any(s.type == "x_mcp" for s in settings.sources)
    profile = settings.data_dir / "playwright-profile"
    if ci:
        out.append(("playwright_profile", True, "CIでは不要（x_collectを省略）"))
    else:
        if profile.is_dir():
            detail = str(profile)
        elif uses_x:
            detail = "missing: run Task 0 login (headed) first"
        else:
            detail = "not required (no x_mcp source)"
        out.append(("playwright_profile", (profile.is_dir() or not uses_x), detail))

    unknown = sorted({s.type for s in settings.sources if s.type not in ADAPTERS})
    out.append(("adapters", not unknown, f"{len(settings.sources)} sources" + (f"; unknown types: {unknown}" if unknown else "")))

    required = [
        "prompts/triage.md",
        "schemas/triage.schema.json",
        "schemas/digest.schema.json",
        "templates/vault/profile.md",
    ]
    if uses_x and not ci:
        required += [
            "prompts/x_collect.md",
            "schemas/x_items.schema.json",
            "config/playwright-mcp.json",
        ]
    missing_files = [r for r in required if not (settings.root / r).exists()]
    out.append(("prompts", not missing_files, "all present" if not missing_files else f"missing: {missing_files}"))

    worker_url = environ.get("AI_WATCH_WORKER_URL", "").strip()
    if not worker_url:
        out.append(("worker", False, "AI_WATCH_WORKER_URLが未設定"))
    else:
        try:
            response = http_get(worker_url, follow_redirects=False, timeout=10)
            status = int(getattr(response, "status_code"))
            location = str(getattr(response, "headers", {}).get("location", ""))
            access_redirect = status in (301, 302, 303, 307, 308) and urlparse(location).hostname and urlparse(location).hostname.endswith(".cloudflareaccess.com")
            if status == 200:
                out.append(("worker", True, "到達できました"))
            elif access_redirect:
                out.append(("worker", True, "Cloudflare Accessで保護されています"))
            else:
                out.append(("worker", False, f"応答が不正です（status={status}）"))
        except Exception:
            out.append(("worker", False, "Workerへ到達できません"))
    return out


def cmd_doctor(settings: Settings, args: argparse.Namespace) -> int:
    results = run_checks(settings, ci=getattr(args, "ci", False))
    for name, ok, detail in results:
        print(f"{'OK ' if ok else 'NG '} {name:<20} {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1
