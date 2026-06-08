"""Persistent critical-issues store for the gateway-ui dashboard."""

from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from plugins.platforms.gateway_ui.gateway_ui_persistence import (
    ISSUES_FILE,
    load_json_records,
    save_json_records,
)

VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low"})
VALID_STATUSES = frozenset({"open", "in_progress", "blocked", "resolved"})
MUTABLE_FIELDS = frozenset(
    {
        "severity",
        "status",
        "source",
        "title",
        "description",
        "department",
        "impact",
        "project_id",
        "issue_ref",
    }
)
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def _normalize_issue(raw: Dict[str, Any], *, new: bool = False) -> Dict[str, Any]:
    issue_id = str(raw.get("id") or "").strip() or uuid.uuid4().hex[:12]
    severity = str(raw.get("severity") or "high").strip().lower()
    status = str(raw.get("status") or "open").strip().lower()
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"severity must be one of: {', '.join(sorted(VALID_SEVERITIES))}")
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")

    title = str(raw.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")

    issue = {
        "id": issue_id,
        "severity": severity,
        "status": status,
        "source": str(raw.get("source") or "hermes").strip() or "hermes",
        "title": title,
        "description": str(raw.get("description") or "").strip(),
        "department": str(raw.get("department") or "").strip(),
        "impact": str(raw.get("impact") or "").strip(),
        "opened_at": str(raw.get("opened_at") or _utc_now_iso()),
        "project_id": str(raw.get("project_id") or "").strip() or None,
        "issue_ref": str(raw.get("issue_ref") or "").strip() or None,
        "logs": [_normalize_log_entry(item) for item in raw.get("logs") or []],
    }
    if new and not raw.get("opened_at"):
        issue["opened_at"] = _utc_now_iso()
    return issue


class DashboardIssueStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._issues: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        for item in load_json_records(ISSUES_FILE):
            try:
                issue = _normalize_issue(item)
            except ValueError:
                continue
            self._issues[issue["id"]] = issue

    def _persist(self) -> None:
        records = [deepcopy(item) for item in self._issues.values()]
        records.sort(key=lambda item: item["opened_at"], reverse=True)
        save_json_records(ISSUES_FILE, records)

    def _copy(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        return deepcopy(issue)

    def list_issues(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        include_resolved: bool = True,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            issues = [self._copy(item) for item in self._issues.values()]

        if not include_resolved:
            issues = [item for item in issues if item["status"] != "resolved"]
        if status:
            normalized = str(status).strip().lower()
            if normalized not in VALID_STATUSES:
                raise ValueError(f"invalid status filter: {normalized}")
            issues = [item for item in issues if item["status"] == normalized]
        if severity:
            normalized = str(severity).strip().lower()
            if normalized not in VALID_SEVERITIES:
                raise ValueError(f"invalid severity filter: {normalized}")
            issues = [item for item in issues if item["severity"] == normalized]

        issues.sort(
            key=lambda item: (
                SEVERITY_ORDER.get(item["severity"], 9),
                item["opened_at"],
            )
        )
        return issues

    def read_issue(self, issue_id: str) -> Dict[str, Any]:
        ref = str(issue_id or "").strip()
        if not ref:
            raise ValueError("issue_id is required")
        with self._lock:
            if ref not in self._issues:
                raise KeyError(f"issue not found: {ref}")
            return self._copy(self._issues[ref])

    def create_issue(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        issue = _normalize_issue(fields, new=True)
        with self._lock:
            self._issues[issue["id"]] = issue
            self._persist()
            return self._copy(issue)

    def update_fields(self, issue_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        if not fields:
            raise ValueError("no fields provided")
        unknown = set(fields) - MUTABLE_FIELDS
        if unknown:
            raise ValueError(
                f"unsupported fields: {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(MUTABLE_FIELDS))}"
            )
        with self._lock:
            ref = str(issue_id or "").strip()
            if ref not in self._issues:
                raise KeyError(f"issue not found: {ref}")
            issue = self._issues[ref]
            if "severity" in fields:
                issue["severity"] = _normalize_issue(
                    {**issue, "severity": fields["severity"]}
                )["severity"]
            if "status" in fields:
                issue["status"] = _normalize_issue(
                    {**issue, "status": fields["status"]}
                )["status"]
            for key in ("source", "title", "description", "department", "impact", "project_id", "issue_ref"):
                if key in fields:
                    value = str(fields[key] or "").strip()
                    if key == "title" and not value:
                        raise ValueError("title cannot be empty")
                    issue[key] = value or (None if key in {"project_id", "issue_ref"} else "")
            self._persist()
            return self._copy(issue)

    def add_log(self, issue_id: str, message: str, author: str = "agent") -> Dict[str, Any]:
        entry = _normalize_log_entry({"message": message, "author": author})
        with self._lock:
            ref = str(issue_id or "").strip()
            if ref not in self._issues:
                raise KeyError(f"issue not found: {ref}")
            issue = self._issues[ref]
            issue["logs"].append(entry)
            self._persist()
            return self._copy(issue)


_STORE: Optional[DashboardIssueStore] = None
_STORE_LOCK = threading.Lock()


def get_dashboard_issue_store() -> DashboardIssueStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = DashboardIssueStore()
        return _STORE
