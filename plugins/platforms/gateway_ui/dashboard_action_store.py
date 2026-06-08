"""Persistent action-center store for the gateway-ui dashboard."""

from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from plugins.platforms.gateway_ui.gateway_ui_persistence import (
    ACTIONS_FILE,
    load_json_records,
    save_json_records,
)

VALID_STATUSES = frozenset({"pending", "approved", "declined"})
MUTABLE_FIELDS = frozenset(
    {
        "title",
        "description",
        "issue_id",
        "project_id",
        "primary_label",
        "status",
        "decision_note",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_action(raw: Dict[str, Any], *, new: bool = False) -> Dict[str, Any]:
    action_id = str(raw.get("id") or "").strip() or uuid.uuid4().hex[:12]
    status = str(raw.get("status") or "pending").strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")

    title = str(raw.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")

    action = {
        "id": action_id,
        "status": status,
        "title": title,
        "description": str(raw.get("description") or "").strip(),
        "issue_id": str(raw.get("issue_id") or "").strip() or None,
        "project_id": str(raw.get("project_id") or "").strip() or None,
        "primary_label": str(raw.get("primary_label") or "Approve").strip() or "Approve",
        "created_at": str(raw.get("created_at") or _utc_now_iso()),
        "decided_at": str(raw.get("decided_at") or "").strip() or None,
        "decision_note": str(raw.get("decision_note") or "").strip() or None,
    }
    if new and not raw.get("created_at"):
        action["created_at"] = _utc_now_iso()
    return action


class DashboardActionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._actions: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        for item in load_json_records(ACTIONS_FILE):
            try:
                action = _normalize_action(item)
            except ValueError:
                continue
            self._actions[action["id"]] = action

    def _persist(self) -> None:
        records = [deepcopy(item) for item in self._actions.values()]
        records.sort(key=lambda item: item["created_at"], reverse=True)
        save_json_records(ACTIONS_FILE, records)

    def _copy(self, action: Dict[str, Any]) -> Dict[str, Any]:
        return deepcopy(action)

    def list_actions(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            actions = [self._copy(item) for item in self._actions.values()]
        if status:
            normalized = str(status).strip().lower()
            if normalized not in VALID_STATUSES:
                raise ValueError(f"invalid status filter: {normalized}")
            actions = [item for item in actions if item["status"] == normalized]
        actions.sort(key=lambda item: item["created_at"], reverse=True)
        return actions

    def read_action(self, action_id: str) -> Dict[str, Any]:
        ref = str(action_id or "").strip()
        if not ref:
            raise ValueError("action_id is required")
        with self._lock:
            if ref not in self._actions:
                raise KeyError(f"action not found: {ref}")
            return self._copy(self._actions[ref])

    def create_action(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        action = _normalize_action(fields, new=True)
        with self._lock:
            self._actions[action["id"]] = action
            self._persist()
            return self._copy(action)

    def update_fields(self, action_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        if not fields:
            raise ValueError("no fields provided")
        unknown = set(fields) - MUTABLE_FIELDS
        if unknown:
            raise ValueError(
                f"unsupported fields: {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(MUTABLE_FIELDS))}"
            )
        with self._lock:
            ref = str(action_id or "").strip()
            if ref not in self._actions:
                raise KeyError(f"action not found: {ref}")
            action = self._actions[ref]
            if "status" in fields:
                action["status"] = _normalize_action(
                    {**action, "status": fields["status"]}
                )["status"]
            for key in ("title", "description", "issue_id", "project_id", "primary_label", "decision_note"):
                if key in fields:
                    value = str(fields[key] or "").strip()
                    if key == "title" and not value:
                        raise ValueError("title cannot be empty")
                    if key in {"issue_id", "project_id"}:
                        action[key] = value or None
                    else:
                        action[key] = value
            self._persist()
            return self._copy(action)

    def decide(self, action_id: str, decision: str, note: str = "") -> Dict[str, Any]:
        normalized = str(decision or "").strip().lower()
        if normalized not in {"approved", "declined"}:
            raise ValueError("decision must be approved or declined")
        with self._lock:
            ref = str(action_id or "").strip()
            if ref not in self._actions:
                raise KeyError(f"action not found: {ref}")
            action = self._actions[ref]
            if action["status"] != "pending":
                raise ValueError(f"action is already {action['status']}")
            action["status"] = normalized
            action["decided_at"] = _utc_now_iso()
            action["decision_note"] = str(note or "").strip() or None
            self._persist()
            return self._copy(action)


_STORE: Optional[DashboardActionStore] = None
_STORE_LOCK = threading.Lock()


def get_dashboard_action_store() -> DashboardActionStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = DashboardActionStore()
        return _STORE
