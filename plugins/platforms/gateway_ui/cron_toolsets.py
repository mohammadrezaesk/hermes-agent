"""Registry of optional toolsets cron jobs may enable via the gateway-ui dashboard.

Add an entry here whenever the adapter exposes a new custom toolset that
scheduled jobs should be allowed to opt into.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Each entry: id (stored on the job), label + description for the UI.
CRON_TOOLSET_REGISTRY: List[Dict[str, Any]] = [
    {
        "id": "project_manager",
        "label": "Project Manager",
        "description": "Read and update the CEO project portfolio",
    },
    {
        "id": "dashboard_issues",
        "label": "Critical Issues",
        "description": "Create and update dashboard critical issues after scans or incidents",
    },
    {
        "id": "dashboard_actions",
        "label": "Action Center",
        "description": "Queue pending CEO approvals on the dashboard",
    },
]


def list_cron_toolsets() -> List[Dict[str, Any]]:
    return [dict(item) for item in CRON_TOOLSET_REGISTRY]


def known_cron_toolset_ids() -> set[str]:
    return {str(item["id"]) for item in CRON_TOOLSET_REGISTRY}


def normalize_enabled_toolsets(raw: Any) -> List[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return None

    allowed = known_cron_toolset_ids()
    normalized: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text in allowed and text not in normalized:
            normalized.append(text)
    return normalized or None
