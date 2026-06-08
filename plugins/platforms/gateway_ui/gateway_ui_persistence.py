"""JSON persistence helpers for gateway-ui state under ~/.hermes/gateway-ui/."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List

from hermes_constants import get_hermes_home
from utils import atomic_replace

logger = logging.getLogger(__name__)

GATEWAY_UI_DIR = get_hermes_home().resolve() / "gateway-ui"
ISSUES_FILE = GATEWAY_UI_DIR / "issues.json"
ACTIONS_FILE = GATEWAY_UI_DIR / "actions.json"


def _ensure_dir() -> None:
    GATEWAY_UI_DIR.mkdir(parents=True, exist_ok=True)


def load_json_records(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return []
    if not isinstance(raw, list):
        logger.warning("Expected list in %s, got %s", path, type(raw).__name__)
        return []
    return [item for item in raw if isinstance(item, dict)]


def save_json_records(path: Path, records: List[dict]) -> None:
    _ensure_dir()
    payload = json.dumps(records, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    atomic_replace(tmp, path)
