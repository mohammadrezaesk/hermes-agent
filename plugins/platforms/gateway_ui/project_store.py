"""In-memory project portfolio store shared by gateway-ui and agent tools."""

from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from plugins.platforms.gateway_ui.mock_projects import MOCK_PROJECTS

VALID_STATUSES = frozenset({"on_track", "at_risk", "off_track", "completed"})
MUTABLE_FIELDS = frozenset(
    {"title", "description", "lead", "due_date", "teams", "status", "progress", "budget_used"}
)
IMMUTABLE_FIELDS = frozenset({"id", "code", "logs"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_teams(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace("،", ",").split(",")]
        return [part for part in parts if part]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("teams must be a list of strings or a comma-separated string")


def _clamp_percent(value: Any, field_name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer between 0 and 100") from exc
    if number < 0 or number > 100:
        raise ValueError(f"{field_name} must be between 0 and 100")
    return number


def _validate_due_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("due_date is required")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("due_date must use YYYY-MM-DD format") from exc
    return text


def _normalize_log_entry(entry: Dict[str, Any]) -> Dict[str, str]:
    message = str(entry.get("message") or "").strip()
    if not message:
        raise ValueError("log message is required")
    author = str(entry.get("author") or "agent").strip() or "agent"
    return {
        "id": str(entry.get("id") or uuid.uuid4().hex[:12]),
        "at": str(entry.get("at") or _utc_now_iso()),
        "author": author,
        "message": message,
    }


def _seed_project(raw: Dict[str, Any]) -> Dict[str, Any]:
    project = {
        "id": str(raw["id"]).strip(),
        "code": str(raw["code"]).strip(),
        "title": str(raw["title"]).strip(),
        "description": str(raw["description"]).strip(),
        "status": str(raw["status"]).strip(),
        "progress": _clamp_percent(raw.get("progress", 0), "progress"),
        "budget_used": _clamp_percent(raw.get("budget_used", 0), "budget_used"),
        "lead": str(raw.get("lead") or "").strip(),
        "due_date": _validate_due_date(raw.get("due_date")),
        "teams": _normalize_teams(raw.get("teams")),
        "logs": [_normalize_log_entry(item) for item in raw.get("logs") or []],
    }
    if project["status"] not in VALID_STATUSES:
        raise ValueError(f"invalid status: {project['status']}")
    if not project["id"] or not project["code"] or not project["title"]:
        raise ValueError("project id, code, and title are required")
    return project


class ProjectStore:
    """Thread-safe in-memory portfolio. One global instance for gateway-ui."""

    def __init__(self, seed: Optional[List[Dict[str, Any]]] = None) -> None:
        self._lock = threading.RLock()
        self._projects: Dict[str, Dict[str, Any]] = {}
        self._code_index: Dict[str, str] = {}
        for item in seed or MOCK_PROJECTS:
            project = _seed_project(item)
            self._projects[project["id"]] = project
            self._code_index[project["code"].lower()] = project["id"]

    def _copy(self, project: Dict[str, Any]) -> Dict[str, Any]:
        return deepcopy(project)

    def _resolve_id(self, project_ref: str) -> str:
        ref = str(project_ref or "").strip()
        if not ref:
            raise ValueError("project_id is required")
        if ref in self._projects:
            return ref
        mapped = self._code_index.get(ref.lower())
        if mapped:
            return mapped
        raise KeyError(f"project not found: {ref}")

    def list_projects(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            projects = [self._copy(item) for item in self._projects.values()]
        if status:
            normalized = str(status).strip()
            if normalized not in VALID_STATUSES:
                raise ValueError(f"invalid status filter: {normalized}")
            projects = [item for item in projects if item["status"] == normalized]
        projects.sort(key=lambda item: item["code"])
        return projects

    def read_project(self, project_ref: str) -> Dict[str, Any]:
        with self._lock:
            project_id = self._resolve_id(project_ref)
            return self._copy(self._projects[project_id])

    def create_project(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        code = str(fields.get("code") or "").strip()
        title = str(fields.get("title") or "").strip()
        if not code:
            raise ValueError("code is required")
        if not title:
            raise ValueError("title is required")

        lead = str(fields.get("lead") or "").strip()
        if not lead:
            raise ValueError("lead cannot be empty")

        status = str(fields.get("status") or "on_track").strip()
        if status not in VALID_STATUSES:
            raise ValueError(
                f"status must be one of: {', '.join(sorted(VALID_STATUSES))}"
            )

        raw = {
            "id": f"prj-{uuid.uuid4().hex[:8]}",
            "code": code,
            "title": title,
            "description": str(fields.get("description") or "").strip(),
            "status": status,
            "progress": fields.get("progress", 0),
            "budget_used": fields.get("budget_used", 0),
            "lead": lead,
            "due_date": fields.get("due_date"),
            "teams": fields.get("teams"),
            "logs": [],
        }
        project = _seed_project(raw)

        with self._lock:
            if project["code"].lower() in self._code_index:
                raise ValueError(f"project code already exists: {project['code']}")
            self._projects[project["id"]] = project
            self._code_index[project["code"].lower()] = project["id"]
            return self._copy(project)

    def update_fields(self, project_ref: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        if not fields:
            raise ValueError("no fields provided")
        unknown = set(fields) - MUTABLE_FIELDS
        if unknown:
            raise ValueError(
                f"unsupported fields: {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(MUTABLE_FIELDS))}"
            )

        with self._lock:
            project_id = self._resolve_id(project_ref)
            project = self._projects[project_id]

            if "title" in fields:
                title = str(fields["title"] or "").strip()
                if not title:
                    raise ValueError("title cannot be empty")
                project["title"] = title
            if "description" in fields:
                project["description"] = str(fields["description"] or "").strip()
            if "lead" in fields:
                lead = str(fields["lead"] or "").strip()
                if not lead:
                    raise ValueError("lead cannot be empty")
                project["lead"] = lead
            if "due_date" in fields:
                project["due_date"] = _validate_due_date(fields["due_date"])
            if "teams" in fields:
                project["teams"] = _normalize_teams(fields["teams"])
            if "status" in fields:
                status = str(fields["status"] or "").strip()
                if status not in VALID_STATUSES:
                    raise ValueError(
                        f"status must be one of: {', '.join(sorted(VALID_STATUSES))}"
                    )
                project["status"] = status
            if "progress" in fields:
                project["progress"] = _clamp_percent(fields["progress"], "progress")
            if "budget_used" in fields:
                project["budget_used"] = _clamp_percent(
                    fields["budget_used"],
                    "budget_used",
                )

            return self._copy(project)

    def update_progress(self, project_ref: str, progress: Any) -> Dict[str, Any]:
        return self.update_fields(project_ref, {"progress": progress})

    def update_budget(self, project_ref: str, budget_used: Any) -> Dict[str, Any]:
        return self.update_fields(project_ref, {"budget_used": budget_used})

    def update_status(self, project_ref: str, status: str) -> Dict[str, Any]:
        return self.update_fields(project_ref, {"status": status})

    def add_log(self, project_ref: str, message: str, author: str = "agent") -> Dict[str, Any]:
        entry = _normalize_log_entry({"message": message, "author": author})
        with self._lock:
            project_id = self._resolve_id(project_ref)
            project = self._projects[project_id]
            project["logs"].append(entry)
            return self._copy(project)


_STORE: Optional[ProjectStore] = None
_STORE_LOCK = threading.Lock()


def get_project_store() -> ProjectStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ProjectStore()
        return _STORE
