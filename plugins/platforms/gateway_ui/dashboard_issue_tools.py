"""Agent tools for gateway-ui critical issues."""

from __future__ import annotations

import json
from typing import Any, Dict

from plugins.platforms.gateway_ui.dashboard_issue_store import (
    get_dashboard_issue_store,
)


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def check_dashboard_issues_requirements() -> bool:
    return True


def handle_list_dashboard_issues(args: Dict[str, Any], **_kw) -> str:
    try:
        include_resolved = bool(args.get("include_resolved", False))
        issues = get_dashboard_issue_store().list_issues(
            status=args.get("status") or None,
            severity=args.get("severity") or None,
            include_resolved=include_resolved,
        )
        return _ok({"issues": issues, "count": len(issues)})
    except Exception as exc:
        return _err(str(exc))


def handle_read_dashboard_issue(args: Dict[str, Any], **_kw) -> str:
    issue_id = str(args.get("issue_id") or "").strip()
    if not issue_id:
        return _err("issue_id is required")
    try:
        issue = get_dashboard_issue_store().read_issue(issue_id)
        return _ok({"issue": issue})
    except KeyError:
        return _err(f"issue not found: {issue_id}")
    except Exception as exc:
        return _err(str(exc))


def handle_create_dashboard_issue(args: Dict[str, Any], **_kw) -> str:
    fields = {key: args[key] for key in args if key != "type"}
    if not fields.get("title"):
        return _err("title is required")
    try:
        issue = get_dashboard_issue_store().create_issue(fields)
        return _ok({"issue": issue})
    except Exception as exc:
        return _err(str(exc))


def handle_update_dashboard_issue(args: Dict[str, Any], **_kw) -> str:
    issue_id = str(args.get("issue_id") or "").strip()
    fields = args.get("fields")
    if not issue_id:
        return _err("issue_id is required")
    if not isinstance(fields, dict) or not fields:
        return _err("fields object is required")
    try:
        issue = get_dashboard_issue_store().update_fields(issue_id, fields)
        return _ok({"issue": issue})
    except KeyError:
        return _err(f"issue not found: {issue_id}")
    except Exception as exc:
        return _err(str(exc))


def handle_add_log_to_dashboard_issue(args: Dict[str, Any], **_kw) -> str:
    issue_id = str(args.get("issue_id") or "").strip()
    message = str(args.get("message") or "").strip()
    author = str(args.get("author") or "agent").strip() or "agent"
    if not issue_id:
        return _err("issue_id is required")
    if not message:
        return _err("message is required")
    try:
        issue = get_dashboard_issue_store().add_log(issue_id, message, author=author)
        return _ok({"issue": issue})
    except KeyError:
        return _err(f"issue not found: {issue_id}")
    except Exception as exc:
        return _err(str(exc))


_ISSUE_ID_PARAM = {
    "type": "string",
    "description": "Critical issue id.",
}

LIST_DASHBOARD_ISSUES_SCHEMA: Dict[str, Any] = {
    "name": "list_dashboard_issues",
    "description": (
        "List critical issues shown on the CEO dashboard. "
        "By default excludes resolved issues."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["open", "in_progress", "blocked", "resolved"],
            },
            "severity": {
                "type": "string",
                "enum": ["critical", "high", "medium", "low"],
            },
            "include_resolved": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
}

READ_DASHBOARD_ISSUE_SCHEMA: Dict[str, Any] = {
    "name": "read_dashboard_issue",
    "description": "Read one dashboard critical issue by id.",
    "parameters": {
        "type": "object",
        "properties": {"issue_id": _ISSUE_ID_PARAM},
        "required": ["issue_id"],
        "additionalProperties": False,
    },
}

CREATE_DASHBOARD_ISSUE_SCHEMA: Dict[str, Any] = {
    "name": "create_dashboard_issue",
    "description": (
        "Create a critical issue on the CEO dashboard. "
        "Use after cron scans, incidents, or executive escalations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "severity": {
                "type": "string",
                "enum": ["critical", "high", "medium", "low"],
            },
            "status": {
                "type": "string",
                "enum": ["open", "in_progress", "blocked"],
            },
            "source": {
                "type": "string",
                "description": "Origin label, e.g. hermes, pagerduty, jira, cron.",
            },
            "department": {"type": "string"},
            "impact": {"type": "string"},
            "project_id": {"type": "string"},
            "issue_ref": {"type": "string"},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}

UPDATE_DASHBOARD_ISSUE_SCHEMA: Dict[str, Any] = {
    "name": "update_dashboard_issue",
    "description": "Update allowed fields on a dashboard critical issue.",
    "parameters": {
        "type": "object",
        "properties": {
            "issue_id": _ISSUE_ID_PARAM,
            "fields": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "status": {
                        "type": "string",
                        "enum": ["open", "in_progress", "blocked", "resolved"],
                    },
                    "source": {"type": "string"},
                    "department": {"type": "string"},
                    "impact": {"type": "string"},
                    "project_id": {"type": "string"},
                    "issue_ref": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "required": ["issue_id", "fields"],
        "additionalProperties": False,
    },
}

ADD_LOG_TO_DASHBOARD_ISSUE_SCHEMA: Dict[str, Any] = {
    "name": "add_log_to_dashboard_issue",
    "description": "Append an activity log entry to a dashboard critical issue.",
    "parameters": {
        "type": "object",
        "properties": {
            "issue_id": _ISSUE_ID_PARAM,
            "message": {"type": "string"},
            "author": {"type": "string"},
        },
        "required": ["issue_id", "message"],
        "additionalProperties": False,
    },
}

_TOOLS = (
    ("list_dashboard_issues", LIST_DASHBOARD_ISSUES_SCHEMA, handle_list_dashboard_issues, "🚨"),
    ("read_dashboard_issue", READ_DASHBOARD_ISSUE_SCHEMA, handle_read_dashboard_issue, "📄"),
    ("create_dashboard_issue", CREATE_DASHBOARD_ISSUE_SCHEMA, handle_create_dashboard_issue, "➕"),
    ("update_dashboard_issue", UPDATE_DASHBOARD_ISSUE_SCHEMA, handle_update_dashboard_issue, "✏️"),
    ("add_log_to_dashboard_issue", ADD_LOG_TO_DASHBOARD_ISSUE_SCHEMA, handle_add_log_to_dashboard_issue, "📝"),
)


def register_dashboard_issue_tools(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="dashboard_issues",
            schema=schema,
            handler=handler,
            check_fn=check_dashboard_issues_requirements,
            emoji=emoji,
        )
