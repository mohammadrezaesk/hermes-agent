"""Tests for gateway-ui project store and agent tools."""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.platforms.gateway_ui.project_store import ProjectStore
from plugins.platforms.gateway_ui.project_tools import (
    handle_add_log_to_project,
    handle_create_project,
    handle_list_projects,
    handle_update_progress_project,
)


@pytest.fixture
def store():
    return ProjectStore(
        seed=[
            {
                "id": "prj-test-01",
                "code": "PRJ-TEST-01",
                "title": "پروژه آزمایشی",
                "description": "توضیحات اولیه",
                "status": "on_track",
                "progress": 10,
                "budget_used": 20,
                "lead": "رضا کاظمی",
                "due_date": "2026-12-01",
                "teams": ["فناوری اطلاعات"],
            }
        ]
    )


def test_list_and_update_progress(store):
    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0]["title"] == "پروژه آزمایشی"
    assert projects[0]["logs"] == []

    updated = store.update_progress("PRJ-TEST-01", 42)
    assert updated["progress"] == 42


def test_add_log_by_code(store):
    project = store.add_log(
        "PRJ-TEST-01",
        "جلسه بررسی ریسک برگزار شد.",
        author="مریم رضایی",
    )
    assert len(project["logs"]) == 1
    assert project["logs"][0]["message"] == "جلسه بررسی ریسک برگزار شد."
    assert project["logs"][0]["author"] == "مریم رضایی"


def test_create_project(store):
    created = store.create_project(
        {
            "code": "PRJ-NEW-99",
            "title": "پروژه تازه",
            "description": "توضیحات",
            "status": "on_track",
            "progress": 0,
            "budget_used": 5,
            "lead": "علی محمدی",
            "due_date": "2027-01-15",
            "teams": ["فناوری اطلاعات"],
        }
    )
    assert created["code"] == "PRJ-NEW-99"
    assert created["title"] == "پروژه تازه"
    assert len(created["logs"]) == 0

    projects = store.list_projects()
    assert len(projects) == 2
    assert store.read_project("PRJ-NEW-99")["lead"] == "علی محمدی"


def test_create_rejects_duplicate_code(store):
    store.create_project(
        {
            "code": "PRJ-DUP",
            "title": "اول",
            "lead": "رضا کاظمی",
            "due_date": "2027-01-15",
        }
    )
    with pytest.raises(ValueError, match="already exists"):
        store.create_project(
            {
                "code": "prj-dup",
                "title": "دوم",
                "lead": "رضا کاظمی",
                "due_date": "2027-01-15",
            }
        )


def test_rejects_unknown_fields(store):
    with pytest.raises(ValueError, match="unsupported fields"):
        store.update_fields("prj-test-01", {"code": "HACKED"})


def test_create_project(store):
    created = store.create_project(
        {
            "code": "PRJ-NEW-99",
            "title": "پروژه تازه",
            "description": "توضیحات",
            "status": "on_track",
            "progress": 0,
            "budget_used": 5,
            "lead": "علی احمدی",
            "due_date": "2027-01-15",
            "teams": ["فناوری اطلاعات"],
        }
    )
    assert created["code"] == "PRJ-NEW-99"
    assert created["title"] == "پروژه تازه"
    assert created["logs"] == []
    assert len(store.list_projects()) == 2

    with pytest.raises(ValueError, match="already exists"):
        store.create_project(
            {
                "code": "PRJ-NEW-99",
                "title": "تکراری",
                "lead": "علی احمدی",
                "due_date": "2027-01-15",
            }
        )


def test_tool_handlers_return_json(store, monkeypatch):
    monkeypatch.setattr(
        "plugins.platforms.gateway_ui.project_tools.get_project_store",
        lambda: store,
    )

    listed = json.loads(handle_list_projects({}))
    assert listed["ok"] is True
    assert listed["count"] == 1

    updated = json.loads(
        handle_update_progress_project(
            {"project_id": "prj-test-01", "progress": 55},
        )
    )
    assert updated["ok"] is True
    assert updated["project"]["progress"] == 55

    logged = json.loads(
        handle_add_log_to_project(
            {
                "project_id": "prj-test-01",
                "message": "پیشرفت فاز اول تایید شد.",
            }
        )
    )
    assert logged["ok"] is True
    assert logged["project"]["logs"][-1]["message"] == "پیشرفت فاز اول تایید شد."


def test_create_project_tool(store, monkeypatch):
    monkeypatch.setattr(
        "plugins.platforms.gateway_ui.project_tools.get_project_store",
        lambda: store,
    )

    created = json.loads(
        handle_create_project(
            {
                "fields": {
                    "code": "PRJ-TOOL-01",
                    "title": "پروژه ابزار",
                    "lead": "رضا کاظمی",
                    "due_date": "2027-03-01",
                    "description": "ساخته‌شده توسط ابزار",
                    "teams": ["فناوری اطلاعات"],
                }
            }
        )
    )
    assert created["ok"] is True
    assert created["project"]["code"] == "PRJ-TOOL-01"
    assert created["project"]["title"] == "پروژه ابزار"
