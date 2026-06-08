"""Agent tools for gateway-ui action center (pending approvals)."""

from __future__ import annotations

import json
from typing import Any, Dict

from plugins.platforms.gateway_ui.dashboard_action_store import (
    get_dashboard_action_store,
)


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def check_dashboard_actions_requirements() -> bool:
    return True


def handle_list_dashboard_actions(args: Dict[str, Any], **_kw) -> str:
    try:
        actions = get_dashboard_action_store().list_actions(
            status=args.get("status") or None,
        )
        return _ok({"actions": actions, "count": len(actions)})
    except Exception as exc:
        return _err(str(exc))


def handle_read_dashboard_action(args: Dict[str, Any], **_kw) -> str:
    action_id = str(args.get("action_id") or "").strip()
    if not action_id:
        return _err("action_id is required")
    try:
        action = get_dashboard_action_store().read_action(action_id)
        return _ok({"action": action})
    except KeyError:
        return _err(f"action not found: {action_id}")
    except Exception as exc:
        return _err(str(exc))


def handle_create_dashboard_action(args: Dict[str, Any], **_kw) -> str:
    fields = {key: args[key] for key in args if key != "type"}
    if not fields.get("title"):
        return _err("title is required")
    try:
        action = get_dashboard_action_store().create_action(fields)
        return _ok({"action": action})
    except Exception as exc:
        return _err(str(exc))


def handle_update_dashboard_action(args: Dict[str, Any], **_kw) -> str:
    action_id = str(args.get("action_id") or "").strip()
    fields = args.get("fields")
    if not action_id:
        return _err("action_id is required")
    if not isinstance(fields, dict) or not fields:
        return _err("fields object is required")
    try:
        action = get_dashboard_action_store().update_fields(action_id, fields)
        return _ok({"action": action})
    except KeyError:
        return _err(f"action not found: {action_id}")
    except Exception as exc:
        return _err(str(exc))


def handle_approve_dashboard_action(args: Dict[str, Any], **_kw) -> str:
    action_id = str(args.get("action_id") or "").strip()
    note = str(args.get("note") or "").strip()
    if not action_id:
        return _err("action_id is required")
    try:
        action = get_dashboard_action_store().decide(action_id, "approved", note=note)
        return _ok({"action": action})
    except KeyError:
        return _err(f"action not found: {action_id}")
    except Exception as exc:
        return _err(str(exc))


def handle_decline_dashboard_action(args: Dict[str, Any], **_kw) -> str:
    action_id = str(args.get("action_id") or "").strip()
    note = str(args.get("note") or "").strip()
    if not action_id:
        return _err("action_id is required")
    try:
        action = get_dashboard_action_store().decide(action_id, "declined", note=note)
        return _ok({"action": action})
    except KeyError:
        return _err(f"action not found: {action_id}")
    except Exception as exc:
        return _err(str(exc))


_ACTION_ID_PARAM = {
    "type": "string",
    "description": "Dashboard action id.",
}

LIST_DASHBOARD_ACTIONS_SCHEMA: Dict[str, Any] = {
    "name": "list_dashboard_actions",
    "description": "List action-center items awaiting CEO approval.",
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pending", "approved", "declined"],
            },
        },
        "additionalProperties": False,
    },
}

READ_DASHBOARD_ACTION_SCHEMA: Dict[str, Any] = {
    "name": "read_dashboard_action",
    "description": "Read one dashboard action item by id.",
    "parameters": {
        "type": "object",
        "properties": {"action_id": _ACTION_ID_PARAM},
        "required": ["action_id"],
        "additionalProperties": False,
    },
}

CREATE_DASHBOARD_ACTION_SCHEMA: Dict[str, Any] = {
    "name": "create_dashboard_action",
    "description": (
        "Create a pending approval in the dashboard action center. "
        "Link to issue_id and/or project_id when relevant."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "primary_label": {
                "type": "string",
                "description": "Primary button label, e.g. Approve transfer.",
            },
            "issue_id": {"type": "string"},
            "project_id": {"type": "string"},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}

UPDATE_DASHBOARD_ACTION_SCHEMA: Dict[str, Any] = {
    "name": "update_dashboard_action",
    "description": "Update allowed fields on a dashboard action item.",
    "parameters": {
        "type": "object",
        "properties": {
            "action_id": _ACTION_ID_PARAM,
            "fields": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "primary_label": {"type": "string"},
                    "issue_id": {"type": "string"},
                    "project_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "approved", "declined"],
                    },
                    "decision_note": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "required": ["action_id", "fields"],
        "additionalProperties": False,
    },
}

_APPROVE_SCHEMA: Dict[str, Any] = {
    "name": "approve_dashboard_action",
    "description": "Approve a pending dashboard action item.",
    "parameters": {
        "type": "object",
        "properties": {
            "action_id": _ACTION_ID_PARAM,
            "note": {"type": "string"},
        },
        "required": ["action_id"],
        "additionalProperties": False,
    },
}

_DECLINE_SCHEMA: Dict[str, Any] = {
    "name": "decline_dashboard_action",
    "description": "Decline a pending dashboard action item.",
    "parameters": {
        "type": "object",
        "properties": {
            "action_id": _ACTION_ID_PARAM,
            "note": {"type": "string"},
        },
        "required": ["action_id"],
        "additionalProperties": False,
    },
}

_TOOLS = (
    ("list_dashboard_actions", LIST_DASHBOARD_ACTIONS_SCHEMA, handle_list_dashboard_actions, "✅"),
    ("read_dashboard_action", READ_DASHBOARD_ACTION_SCHEMA, handle_read_dashboard_action, "📄"),
    ("create_dashboard_action", CREATE_DASHBOARD_ACTION_SCHEMA, handle_create_dashboard_action, "➕"),
    ("update_dashboard_action", UPDATE_DASHBOARD_ACTION_SCHEMA, handle_update_dashboard_action, "✏️"),
    ("approve_dashboard_action", _APPROVE_SCHEMA, handle_approve_dashboard_action, "👍"),
    ("decline_dashboard_action", _DECLINE_SCHEMA, handle_decline_dashboard_action, "👎"),
)


def register_dashboard_action_tools(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="dashboard_actions",
            schema=schema,
            handler=handler,
            check_fn=check_dashboard_actions_requirements,
            emoji=emoji,
        )
