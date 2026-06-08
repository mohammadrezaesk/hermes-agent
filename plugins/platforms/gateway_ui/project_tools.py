"""Agent tools for the gateway-ui project portfolio."""

from __future__ import annotations

import json
from typing import Any, Dict

from plugins.platforms.gateway_ui.project_store import get_project_store


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def check_project_manager_requirements() -> bool:
    return True


def handle_list_projects(args: Dict[str, Any], **_kw) -> str:
    try:
        status = args.get("status")
        projects = get_project_store().list_projects(status=status or None)
        return _ok({"projects": projects, "count": len(projects)})
    except Exception as exc:
        return _err(str(exc))


def handle_read_project(args: Dict[str, Any], **_kw) -> str:
    project_id = str(args.get("project_id") or "").strip()
    if not project_id:
        return _err("project_id is required")
    try:
        project = get_project_store().read_project(project_id)
        return _ok({"project": project})
    except KeyError:
        return _err(f"project not found: {project_id}")
    except Exception as exc:
        return _err(str(exc))


def handle_create_project(args: Dict[str, Any], **_kw) -> str:
    fields = args.get("fields")
    if not isinstance(fields, dict) or not fields:
        return _err("fields object is required")
    try:
        project = get_project_store().create_project(fields)
        return _ok({"project": project})
    except Exception as exc:
        return _err(str(exc))


def handle_update_progress_project(args: Dict[str, Any], **_kw) -> str:
    project_id = str(args.get("project_id") or "").strip()
    if not project_id:
        return _err("project_id is required")
    if "progress" not in args:
        return _err("progress is required")
    try:
        project = get_project_store().update_progress(project_id, args["progress"])
        return _ok({"project": project})
    except KeyError:
        return _err(f"project not found: {project_id}")
    except Exception as exc:
        return _err(str(exc))


def handle_update_budget_project(args: Dict[str, Any], **_kw) -> str:
    project_id = str(args.get("project_id") or "").strip()
    if not project_id:
        return _err("project_id is required")
    if "budget_used" not in args:
        return _err("budget_used is required")
    try:
        project = get_project_store().update_budget(project_id, args["budget_used"])
        return _ok({"project": project})
    except KeyError:
        return _err(f"project not found: {project_id}")
    except Exception as exc:
        return _err(str(exc))


def handle_update_status_project(args: Dict[str, Any], **_kw) -> str:
    project_id = str(args.get("project_id") or "").strip()
    status = str(args.get("status") or "").strip()
    if not project_id:
        return _err("project_id is required")
    if not status:
        return _err("status is required")
    try:
        project = get_project_store().update_status(project_id, status)
        return _ok({"project": project})
    except KeyError:
        return _err(f"project not found: {project_id}")
    except Exception as exc:
        return _err(str(exc))


def handle_add_log_to_project(args: Dict[str, Any], **_kw) -> str:
    project_id = str(args.get("project_id") or "").strip()
    message = str(args.get("message") or "").strip()
    author = str(args.get("author") or "agent").strip() or "agent"
    if not project_id:
        return _err("project_id is required")
    if not message:
        return _err("message is required")
    try:
        project = get_project_store().add_log(project_id, message, author=author)
        return _ok({"project": project})
    except KeyError:
        return _err(f"project not found: {project_id}")
    except Exception as exc:
        return _err(str(exc))


def handle_update_project_fields(args: Dict[str, Any], **_kw) -> str:
    project_id = str(args.get("project_id") or "").strip()
    fields = args.get("fields")
    if not project_id:
        return _err("project_id is required")
    if not isinstance(fields, dict) or not fields:
        return _err("fields object is required")
    try:
        project = get_project_store().update_fields(project_id, fields)
        return _ok({"project": project})
    except KeyError:
        return _err(f"project not found: {project_id}")
    except Exception as exc:
        return _err(str(exc))


_PROJECT_ID_PARAM = {
    "type": "string",
    "description": "Project id (e.g. prj-erp-01) or code (e.g. PRJ-ERP-01).",
}

LIST_PROJECTS_SCHEMA: Dict[str, Any] = {
    "name": "list_projects",
    "description": (
        "List CEO portfolio projects from the gateway-ui project manager. "
        "Returns the full JSON record for each project, including logs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["on_track", "at_risk", "off_track", "completed"],
                "description": "Optional status filter.",
            }
        },
        "additionalProperties": False,
    },
}

READ_PROJECT_SCHEMA: Dict[str, Any] = {
    "name": "read_project",
    "description": "Read one project record by id or code.",
    "parameters": {
        "type": "object",
        "properties": {"project_id": _PROJECT_ID_PARAM},
        "required": ["project_id"],
        "additionalProperties": False,
    },
}

_CREATE_PROJECT_FIELDS = {
    "type": "object",
    "description": "New project fields.",
    "properties": {
        "code": {
            "type": "string",
            "description": "Unique project code (e.g. PRJ-NEW-01).",
        },
        "title": {"type": "string"},
        "description": {"type": "string"},
        "lead": {"type": "string", "description": "Project lead name."},
        "due_date": {
            "type": "string",
            "description": "Due date in YYYY-MM-DD format.",
        },
        "teams": {
            "type": "array",
            "items": {"type": "string"},
        },
        "status": {
            "type": "string",
            "enum": ["on_track", "at_risk", "off_track", "completed"],
        },
        "progress": {"type": "integer", "minimum": 0, "maximum": 100},
        "budget_used": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": ["code", "title", "lead", "due_date"],
    "additionalProperties": False,
}

CREATE_PROJECT_SCHEMA: Dict[str, Any] = {
    "name": "create_project",
    "description": (
        "Add a new project to the CEO portfolio. "
        "Requires code, title, lead, and due_date inside fields."
    ),
    "parameters": {
        "type": "object",
        "properties": {"fields": _CREATE_PROJECT_FIELDS},
        "required": ["fields"],
        "additionalProperties": False,
    },
}

UPDATE_PROGRESS_PROJECT_SCHEMA: Dict[str, Any] = {
    "name": "update_progress_project",
    "description": "Update a project's progress percentage (0-100).",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID_PARAM,
            "progress": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "New progress percentage.",
            },
        },
        "required": ["project_id", "progress"],
        "additionalProperties": False,
    },
}

UPDATE_BUDGET_PROJECT_SCHEMA: Dict[str, Any] = {
    "name": "update_budget_project",
    "description": "Update a project's budget_used percentage (0-100).",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID_PARAM,
            "budget_used": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "New budget-used percentage.",
            },
        },
        "required": ["project_id", "budget_used"],
        "additionalProperties": False,
    },
}

UPDATE_STATUS_PROJECT_SCHEMA: Dict[str, Any] = {
    "name": "update_status_project",
    "description": "Update a project's status.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID_PARAM,
            "status": {
                "type": "string",
                "enum": ["on_track", "at_risk", "off_track", "completed"],
            },
        },
        "required": ["project_id", "status"],
        "additionalProperties": False,
    },
}

ADD_LOG_TO_PROJECT_SCHEMA: Dict[str, Any] = {
    "name": "add_log_to_project",
    "description": (
        "Append a structured activity log entry to a project. "
        "Use for milestones, risks, decisions, or status notes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID_PARAM,
            "message": {
                "type": "string",
                "description": "Log message to append.",
            },
            "author": {
                "type": "string",
                "description": "Optional author label (defaults to agent).",
            },
        },
        "required": ["project_id", "message"],
        "additionalProperties": False,
    },
}

UPDATE_PROJECT_FIELDS_SCHEMA: Dict[str, Any] = {
    "name": "update_project_fields",
    "description": (
        "Update allowed project metadata fields in one call. "
        "Only these keys are accepted inside fields: title, description, lead, "
        "due_date, teams, status, progress, budget_used."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID_PARAM,
            "fields": {
                "type": "object",
                "description": "Subset of mutable project fields.",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "lead": {"type": "string"},
                    "due_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD",
                    },
                    "teams": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "status": {
                        "type": "string",
                        "enum": ["on_track", "at_risk", "off_track", "completed"],
                    },
                    "progress": {"type": "integer", "minimum": 0, "maximum": 100},
                    "budget_used": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "additionalProperties": False,
            },
        },
        "required": ["project_id", "fields"],
        "additionalProperties": False,
    },
}

_TOOLS = (
    ("list_projects", LIST_PROJECTS_SCHEMA, handle_list_projects, "📁"),
    ("read_project", READ_PROJECT_SCHEMA, handle_read_project, "📄"),
    ("create_project", CREATE_PROJECT_SCHEMA, handle_create_project, "➕"),
    (
        "update_progress_project",
        UPDATE_PROGRESS_PROJECT_SCHEMA,
        handle_update_progress_project,
        "📈",
    ),
    (
        "update_budget_project",
        UPDATE_BUDGET_PROJECT_SCHEMA,
        handle_update_budget_project,
        "💰",
    ),
    (
        "update_status_project",
        UPDATE_STATUS_PROJECT_SCHEMA,
        handle_update_status_project,
        "🚦",
    ),
    ("add_log_to_project", ADD_LOG_TO_PROJECT_SCHEMA, handle_add_log_to_project, "📝"),
    (
        "update_project_fields",
        UPDATE_PROJECT_FIELDS_SCHEMA,
        handle_update_project_fields,
        "✏️",
    ),
)


def register_project_tools(ctx) -> None:
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="project_manager",
            schema=schema,
            handler=handler,
            check_fn=check_project_manager_requirements,
            emoji=emoji,
        )
