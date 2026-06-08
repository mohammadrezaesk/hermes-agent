"""Cron job helpers for the gateway-ui WebSocket API."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from cron.jobs import (
    create_job,
    list_jobs,
    pause_job,
    remove_job,
    resume_job,
    trigger_job,
    update_job,
)
from plugins.platforms.gateway_ui.cron_toolsets import (
    list_cron_toolsets,
    normalize_enabled_toolsets,
)
from tools.cronjob_tools import _format_job, _normalize_deliver_param, _scan_cron_prompt

DEFAULT_DELIVER = "gateway-ui"
GATEWAY_UI_ORIGIN = {
    "platform": "gateway-ui",
    "chat_id": "gateway-ui-notifications",
    "chat_name": "Notifications",
}


def job_for_ui(job: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a stored cron job record for the React dashboard."""
    formatted = _format_job(job)
    job_id = formatted["job_id"]
    is_running = False
    try:
        from cron.scheduler import is_job_running

        is_running = is_job_running(job_id)
    except Exception:
        pass
    state = formatted["state"]
    if is_running:
        state = "running"
    return {
        "id": job_id,
        "name": formatted["name"],
        "prompt": str(job.get("prompt") or ""),
        "schedule": job.get("schedule") or {},
        "schedule_display": formatted["schedule"],
        "deliver": formatted["deliver"],
        "enabled": formatted["enabled"],
        "state": state,
        "is_running": is_running,
        "next_run_at": formatted["next_run_at"],
        "last_run_at": formatted["last_run_at"],
        "last_status": formatted.get("last_status"),
        "last_error": job.get("last_delivery_error") or job.get("last_error"),
        "enabled_toolsets": job.get("enabled_toolsets") or [],
    }


def list_cron_jobs(*, include_disabled: bool = True) -> List[Dict[str, Any]]:
    return [job_for_ui(job) for job in list_jobs(include_disabled=include_disabled)]


def list_delivery_targets() -> List[Dict[str, Any]]:
    targets = [
        {
            "id": "local",
            "name": "Local (save only)",
            "home_target_set": True,
        }
    ]
    try:
        from cron.scheduler import cron_delivery_targets

        for item in cron_delivery_targets():
            targets.append(
                {
                    "id": item["id"],
                    "name": item.get("name") or item["id"],
                    "home_target_set": bool(item.get("home_target_set")),
                    "home_env_var": item.get("home_env_var"),
                }
            )
    except Exception:
        pass
    return targets


def create_cron_job(
    *,
    prompt: str,
    schedule: str,
    name: str = "",
    deliver: Optional[str] = None,
    enabled_toolsets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    prompt_text = str(prompt or "").strip()
    schedule_text = str(schedule or "").strip()
    if not prompt_text:
        raise ValueError("prompt is required")
    if not schedule_text:
        raise ValueError("schedule is required")

    scan_error = _scan_cron_prompt(prompt_text)
    if scan_error:
        raise ValueError(scan_error)

    job = create_job(
        prompt=prompt_text,
        schedule=schedule_text,
        name=str(name or "").strip() or None,
        deliver=_normalize_deliver_param(deliver) or DEFAULT_DELIVER,
        origin=GATEWAY_UI_ORIGIN,
        enabled_toolsets=normalize_enabled_toolsets(enabled_toolsets),
    )
    return job_for_ui(job)


def update_cron_job(
    job_id: str,
    *,
    prompt: Optional[str] = None,
    schedule: Optional[str] = None,
    name: Optional[str] = None,
    deliver: Optional[str] = None,
    enabled_toolsets: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not str(job_id or "").strip():
        raise ValueError("job_id is required")

    updates: Dict[str, Any] = {}
    if prompt is not None:
        prompt_text = str(prompt).strip()
        if not prompt_text:
            raise ValueError("prompt cannot be empty")
        scan_error = _scan_cron_prompt(prompt_text)
        if scan_error:
            raise ValueError(scan_error)
        updates["prompt"] = prompt_text
    if name is not None:
        updates["name"] = str(name).strip()
    if deliver is not None:
        updates["deliver"] = _normalize_deliver_param(deliver)
    if enabled_toolsets is not None:
        updates["enabled_toolsets"] = normalize_enabled_toolsets(enabled_toolsets)
    if schedule is not None:
        from cron.jobs import parse_schedule

        schedule_text = str(schedule).strip()
        if not schedule_text:
            raise ValueError("schedule cannot be empty")
        parsed = parse_schedule(schedule_text)
        updates["schedule"] = parsed
        updates["schedule_display"] = parsed.get("display", schedule_text)
        updates["state"] = "scheduled"
        updates["enabled"] = True

    if not updates:
        raise ValueError("no updates provided")

    updated = update_job(job_id, updates)
    if not updated:
        raise ValueError(f"Job not found: {job_id}")
    return job_for_ui(updated)


def pause_cron_job(job_id: str) -> Dict[str, Any]:
    updated = pause_job(job_id)
    if not updated:
        raise ValueError(f"Job not found: {job_id}")
    return job_for_ui(updated)


def resume_cron_job(job_id: str) -> Dict[str, Any]:
    updated = resume_job(job_id)
    if not updated:
        raise ValueError(f"Job not found: {job_id}")
    return job_for_ui(updated)


def _kick_cron_tick(adapters: Any = None, loop: Any = None) -> None:
    """Run a scheduler tick immediately so manual triggers don't wait 60s."""
    try:
        from cron.scheduler import tick

        tick(verbose=False, adapters=adapters, loop=loop, sync=False)
    except Exception:
        logger.debug("Gateway UI: cron tick kick failed", exc_info=True)


def trigger_cron_job(
    job_id: str,
    *,
    adapters: Any = None,
    loop: Any = None,
) -> Dict[str, Any]:
    updated = trigger_job(job_id)
    if not updated:
        raise ValueError(f"Job not found: {job_id}")
    threading.Thread(
        target=_kick_cron_tick,
        kwargs={"adapters": adapters, "loop": loop},
        daemon=True,
        name=f"cron-kick-{job_id}",
    ).start()
    return job_for_ui(updated)


def delete_cron_job(job_id: str) -> bool:
    return bool(remove_job(job_id))
