"""Tests for gateway-ui cron WebSocket helpers."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins.platforms.gateway_ui import cron_api
from plugins.platforms.gateway_ui.cron_toolsets import list_cron_toolsets


@pytest.fixture
def cron_store(tmp_path, monkeypatch):
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir(parents=True)
    monkeypatch.setattr("cron.jobs.CRON_DIR", cron_dir)
    monkeypatch.setattr("cron.jobs.JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", cron_dir / "output")


def test_list_cron_toolsets_includes_project_manager():
    toolsets = list_cron_toolsets()
    ids = {item["id"] for item in toolsets}
    assert "project_manager" in ids
    assert "dashboard_issues" in ids
    assert "dashboard_actions" in ids


def test_create_lists_and_serializes_job(cron_store):
    job = cron_api.create_cron_job(
        name="Morning report",
        prompt="Review all open projects and summarize risks.",
        schedule="0 9 * * *",
        deliver="gateway-ui",
        enabled_toolsets=["project_manager"],
    )

    assert job["name"] == "Morning report"
    assert job["deliver"] == "gateway-ui"
    assert job["enabled_toolsets"] == ["project_manager"]
    assert job["schedule_display"]

    jobs = cron_api.list_cron_jobs()
    assert len(jobs) == 1
    assert jobs[0]["id"] == job["id"]


def test_create_ignores_unknown_toolsets(cron_store):
    job = cron_api.create_cron_job(
        name="Test",
        prompt="Do something",
        schedule="0 * * * *",
        enabled_toolsets=["project_manager", "unknown_tool"],
    )
    assert job["enabled_toolsets"] == ["project_manager"]


def test_pause_resume_trigger_and_delete(cron_store):
    job = cron_api.create_cron_job(
        name="Status check",
        prompt="Check project statuses.",
        schedule="0 * * * *",
    )

    paused = cron_api.pause_cron_job(job["id"])
    assert paused["state"] == "paused"

    resumed = cron_api.resume_cron_job(job["id"])
    assert resumed["state"] in {"scheduled", "enabled"}

    triggered = cron_api.trigger_cron_job(job["id"])
    assert triggered["id"] == job["id"]

    assert cron_api.delete_cron_job(job["id"]) is True
    assert cron_api.list_cron_jobs() == []


def test_job_for_ui_marks_running_jobs(cron_store, monkeypatch):
    job = cron_api.create_cron_job(
        name="Status probe",
        prompt="Check status.",
        schedule="0 * * * *",
    )
    monkeypatch.setattr(
        "cron.scheduler.is_job_running",
        lambda job_id: job_id == job["id"],
    )
    formatted = cron_api.job_for_ui(
        {
            "id": job["id"],
            "name": job["name"],
            "prompt": job["prompt"],
            "schedule": {"expr": "0 * * * *"},
            "enabled": True,
            "state": "scheduled",
        },
    )
    assert formatted["is_running"] is True
    assert formatted["state"] == "running"


def test_update_job_fields(cron_store):
    job = cron_api.create_cron_job(
        name="Old name",
        prompt="First prompt",
        schedule="0 9 * * *",
        enabled_toolsets=["project_manager"],
    )

    updated = cron_api.update_cron_job(
        job["id"],
        name="New name",
        prompt="Updated prompt",
        schedule="0 10 * * *",
        enabled_toolsets=[],
    )

    assert updated["name"] == "New name"
    assert updated["prompt"] == "Updated prompt"
    assert updated["enabled_toolsets"] == []
