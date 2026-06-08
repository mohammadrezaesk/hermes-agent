"""Dashboard summary helpers shared by WebSocket handlers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from plugins.platforms.gateway_ui.dashboard_action_store import (
    get_dashboard_action_store,
)
from plugins.platforms.gateway_ui.dashboard_issue_store import (
    get_dashboard_issue_store,
)
from plugins.platforms.gateway_ui.project_store import get_project_store


def calendar_connected() -> bool:
    """Google Calendar integration — not wired yet."""
    return False


def get_dashboard_summary() -> dict:
    issues = get_dashboard_issue_store().list_issues(include_resolved=False)
    pending_actions = get_dashboard_action_store().list_actions(status="pending")
    all_projects = get_project_store().list_projects()
    at_risk_projects = [item for item in all_projects if item["status"] == "at_risk"]
    critical_issues = [item for item in issues if item["severity"] == "critical"]

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    opened_recent = 0
    for issue in issues:
        try:
            opened = datetime.fromisoformat(str(issue.get("opened_at") or ""))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if opened >= cutoff:
            opened_recent += 1

    brief_time = now.strftime("%H:%M")

    return {
        "open_issues": len(issues),
        "open_issues_delta_24h": opened_recent,
        "critical_count": len(critical_issues),
        "projects_at_risk": len(at_risk_projects),
        "projects_total": len(all_projects),
        "awaiting_approval": len(pending_actions),
        "calendar_connected": calendar_connected(),
        "brief_time": brief_time,
    }
