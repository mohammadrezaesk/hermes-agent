"""Tests for gateway-ui dashboard stores, tools, and summary."""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.platforms.gateway_ui.dashboard_action_store import DashboardActionStore
from plugins.platforms.gateway_ui.dashboard_action_tools import (
    handle_create_dashboard_action,
    handle_decline_dashboard_action,
)
from plugins.platforms.gateway_ui.dashboard_api import get_dashboard_summary
from plugins.platforms.gateway_ui.dashboard_issue_store import DashboardIssueStore
from plugins.platforms.gateway_ui.dashboard_issue_tools import (
    handle_create_dashboard_issue,
    handle_list_dashboard_issues,
)


@pytest.fixture
def issue_store(tmp_path, monkeypatch):
    issues_file = tmp_path / "issues.json"
    monkeypatch.setattr(
        "plugins.platforms.gateway_ui.dashboard_issue_store.ISSUES_FILE",
        issues_file,
    )
    return DashboardIssueStore()


@pytest.fixture
def action_store(tmp_path, monkeypatch):
    actions_file = tmp_path / "actions.json"
    monkeypatch.setattr(
        "plugins.platforms.gateway_ui.dashboard_action_store.ACTIONS_FILE",
        actions_file,
    )
    return DashboardActionStore()


def test_create_and_list_issues(issue_store, monkeypatch):
    monkeypatch.setattr(
        "plugins.platforms.gateway_ui.dashboard_issue_tools.get_dashboard_issue_store",
        lambda: issue_store,
    )

    created = issue_store.create_issue(
        {
            "title": "Payroll delay",
            "description": "ACH rejected",
            "severity": "critical",
            "source": "cron",
            "department": "Finance",
            "impact": "412 employees",
        }
    )
    assert created["title"] == "Payroll delay"
    assert created["severity"] == "critical"

    listed = json.loads(handle_list_dashboard_issues({}))
    assert listed["ok"] is True
    assert listed["count"] == 1


def test_create_issue_tool(issue_store, monkeypatch):
    monkeypatch.setattr(
        "plugins.platforms.gateway_ui.dashboard_issue_tools.get_dashboard_issue_store",
        lambda: issue_store,
    )

    result = json.loads(
        handle_create_dashboard_issue(
            {
                "title": "Vendor blocked",
                "severity": "high",
                "source": "hermes",
            }
        )
    )
    assert result["ok"] is True
    assert result["issue"]["title"] == "Vendor blocked"


def test_action_decide_flow(action_store, monkeypatch):
    monkeypatch.setattr(
        "plugins.platforms.gateway_ui.dashboard_action_tools.get_dashboard_action_store",
        lambda: action_store,
    )

    action = action_store.create_action(
        {
            "title": "Approve transfer",
            "description": "Release emergency funds",
            "primary_label": "Approve $1.42M",
        }
    )

    declined = json.loads(
        handle_decline_dashboard_action({"action_id": action["id"], "note": "Not now"})
    )
    assert declined["ok"] is True
    assert declined["action"]["status"] == "declined"
    assert declined["action"]["decision_note"] == "Not now"


def test_dashboard_summary_counts(issue_store, action_store, monkeypatch):
    monkeypatch.setattr(
        "plugins.platforms.gateway_ui.dashboard_api.get_dashboard_issue_store",
        lambda: issue_store,
    )
    monkeypatch.setattr(
        "plugins.platforms.gateway_ui.dashboard_api.get_dashboard_action_store",
        lambda: action_store,
    )

    issue_store.create_issue({"title": "Critical outage", "severity": "critical"})
    issue_store.create_issue({"title": "Minor delay", "severity": "high"})
    action_store.create_action({"title": "Approve budget"})

    summary = get_dashboard_summary()
    assert summary["open_issues"] == 2
    assert summary["critical_count"] == 1
    assert summary["awaiting_approval"] == 1
    assert summary["calendar_connected"] is False
