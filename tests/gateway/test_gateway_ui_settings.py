"""Tests for gateway-ui settings helpers."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.platforms.gateway_ui import settings_api


def test_read_and_write_soul_md(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir(parents=True)
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: hermes_home)

    missing = settings_api.read_soul_md()
    assert missing["exists"] is False
    assert missing["content"] == ""

    settings_api.write_soul_md("You are a focused CEO assistant.")
    saved = settings_api.read_soul_md()
    assert saved["exists"] is True
    assert saved["content"] == "You are a focused CEO assistant."


def test_request_gateway_restart_uses_callback():
    called = {"value": False}

    def _restart():
        called["value"] = True
        return True

    result = settings_api.request_gateway_restart(restart_fn=_restart)
    assert result["ok"] is True
    assert called["value"] is True


def test_request_gateway_restart_rejects_duplicate(monkeypatch):
    monkeypatch.setattr(
        settings_api,
        "_restart_via_subprocess",
        lambda: (_ for _ in ()).throw(AssertionError("should not spawn CLI")),
    )

    with pytest.raises(ValueError, match="already in progress"):
        settings_api.request_gateway_restart(restart_fn=lambda: False)
