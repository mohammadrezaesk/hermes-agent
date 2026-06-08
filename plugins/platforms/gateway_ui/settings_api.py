"""Settings helpers for the gateway-ui WebSocket API."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"


def soul_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / SOUL_FILENAME


def read_soul_md() -> Dict[str, Any]:
    path = soul_path()
    if not path.exists():
        return {
            "content": "",
            "path": str(path),
            "exists": False,
        }
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Could not read SOUL.md: {exc}") from exc
    return {
        "content": content,
        "path": str(path),
        "exists": True,
    }


def write_soul_md(content: str) -> Dict[str, Any]:
    path = soul_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content is not None else "", encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Could not write SOUL.md: {exc}") from exc
    return {
        "ok": True,
        "path": str(path),
    }


def _resolve_hermes_bin() -> Optional[list[str]]:
    import shutil

    hermes_bin = shutil.which("hermes")
    if hermes_bin:
        return [hermes_bin]
    try:
        import importlib.util

        if importlib.util.find_spec("hermes_cli") is not None:
            return [sys.executable, "-m", "hermes_cli.main"]
    except Exception:
        pass
    return None


def _restart_via_subprocess() -> bool:
    argv = _resolve_hermes_bin()
    if not argv:
        raise ValueError("Could not locate hermes CLI for gateway restart")

    cmd = [*argv, "gateway", "restart", "--all"]
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        raise ValueError(f"Could not start gateway restart: {exc}") from exc
    return True


def request_gateway_restart(
    restart_fn: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    under_service = bool(os.environ.get("INVOCATION_ID"))
    in_container = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")

    if restart_fn is not None:
        started = bool(restart_fn())
        if not started:
            raise ValueError("Gateway restart is already in progress")
        return {
            "ok": True,
            "method": "service" if (under_service or in_container) else "detached",
        }

    _restart_via_subprocess()
    return {"ok": True, "method": "cli"}
