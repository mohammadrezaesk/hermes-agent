"""Gateway UI platform adapter (Hermes plugin).

Runs a local WebSocket server that the React ``gateway-ui`` client connects
to.  Inbound UI messages are converted to :class:`MessageEvent` objects and
forwarded to the Hermes agent pipeline; outbound agent output is streamed
back as JSON events matching the UI contract:

  * ``text_chunk`` / ``text_done`` — assistant text
  * ``tool_call`` — tool invocation logs
  * ``typing`` — agent is generating
  * ``error`` — delivery / parse errors
  * ``chats_list`` — available conversations (response to ``list_chats``)
  * ``history`` — prior thread for a chat (response to ``load_history``)
  * ``workspace_info`` — Hermes workspace root metadata
  * ``files_list`` — directory listing (response to ``list_files``)
  * ``file_content`` — file preview body (response to ``read_file``)
  * ``projects_list`` — project portfolio (response to ``list_projects``)
  * ``project_detail`` — single project (response to ``read_project``)
  * ``project_updated`` — saved project (response to ``update_project``)
  * ``dashboard_summary`` — dashboard stats (response to ``get_dashboard_summary``)
  * ``dashboard_issues_list`` — critical issues (response to ``list_dashboard_issues``)
  * ``dashboard_actions_list`` — action center items (response to ``list_dashboard_actions``)
  * ``dashboard_action_updated`` — decided action (response to approve/decline)
  * ``cron_jobs_list`` — scheduled jobs (response to ``list_cron_jobs``)
  * ``cron_delivery_targets`` — delivery options (response to ``list_cron_delivery_targets``)
  * ``cron_toolsets_list`` — optional toolsets cron jobs may enable (response to ``list_cron_toolsets``)
  * ``cron_job_created`` / ``cron_job_updated`` / ``cron_job_deleted`` — cron mutations
  * ``workspace_changed`` — workspace directory changed (push, no request)

Inbound control messages::

  * ``list_chats`` — request conversation list
  * ``load_history`` — request ``{ chat_id }`` transcript hydration
  * ``workspace_info`` — request workspace root path
  * ``list_files`` — request ``{ path }`` directory listing (relative)
  * ``read_file`` — request ``{ path }`` text or image preview
  * ``ensure_notifications`` — configure the fixed notifications inbox as home channel
  * ``load_notifications_history`` — load the notifications inbox transcript
  * ``list_projects`` — request project portfolio data (shared with agent tools)
  * ``read_project`` — request one project by id or code
  * ``update_project`` — update mutable project fields from the UI
  * ``create_project`` — create a new project from the UI
  * ``get_dashboard_summary`` — dashboard overview stats
  * ``list_dashboard_issues`` — list critical dashboard issues
  * ``list_dashboard_actions`` — list action-center items
  * ``approve_dashboard_action`` / ``decline_dashboard_action`` — decide pending actions
  * ``list_cron_jobs`` — list scheduled cron jobs
  * ``list_cron_delivery_targets`` — list cron delivery targets
  * ``list_cron_toolsets`` — list optional toolsets for cron jobs
  * ``create_cron_job`` — create a scheduled job
  * ``update_cron_job`` — update an existing job
  * ``pause_cron_job`` / ``resume_cron_job`` / ``trigger_cron_job`` — job controls
  * ``delete_cron_job`` — remove a job
  * ``read_soul_md`` / ``write_soul_md`` — read or update ``SOUL.md``
  * ``restart_gateway`` — restart Hermes gateways after settings changes

Configuration in ``config.yaml``::

    gateway:
      platforms:
        gateway-ui:
          enabled: true
          extra:
            ws_host: 127.0.0.1
            ws_port: 8765
            allow_all_users: true
            workspace_root: /path/to/JooJoo

Environment variables (env wins over config.yaml ``extra``):

    GATEWAY_UI_WS_HOST          Listen host (default: 127.0.0.1)
    GATEWAY_UI_WS_PORT          Listen port (default: 8765)
    GATEWAY_UI_ALLOWED_USERS    Comma-separated chat_id allowlist
    GATEWAY_UI_ALLOW_ALL_USERS  Allow any session (default: true)
    GATEWAY_UI_USER_ID          Stable Hermes user id for all UI chats
    GATEWAY_UI_WORKSPACE_ROOT   Workspace directory for the file browser
    GATEWAY_UI_HOME_CHANNEL     Default chat_id for cron delivery

Requires the ``websockets`` package (``pip install websockets``).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from gateway.config import HomeChannel, Platform, PlatformConfig
from gateway.session import build_session_key
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_document_from_bytes,
    cache_image_from_bytes,
    get_image_cache_dir,
)

from plugins.platforms.gateway_ui import cron_api, settings_api
from plugins.platforms.gateway_ui.cron_toolsets import list_cron_toolsets
from plugins.platforms.gateway_ui.dashboard_action_store import (
    get_dashboard_action_store,
)
from plugins.platforms.gateway_ui.dashboard_action_tools import (
    register_dashboard_action_tools,
)
from plugins.platforms.gateway_ui.dashboard_api import get_dashboard_summary
from plugins.platforms.gateway_ui.dashboard_issue_store import (
    get_dashboard_issue_store,
)
from plugins.platforms.gateway_ui.dashboard_issue_tools import (
    register_dashboard_issue_tools,
)
from plugins.platforms.gateway_ui.project_store import get_project_store
from plugins.platforms.gateway_ui.project_tools import register_project_tools

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_STABLE_USER_ID = "gateway-ui-user"
NOTIFICATIONS_CHAT_ID = "gateway-ui-notifications"
NOTIFICATIONS_CHAT_NAME = "Notifications"
HOME_CHANNEL_ENV = "GATEWAY_UI_HOME_CHANNEL"
HOME_CHANNEL_THREAD_ENV = f"{HOME_CHANNEL_ENV}_THREAD_ID"
REFERENCE_MIME = "application/x-hermes-reference"
MAX_MESSAGE_LENGTH = 65_536
MAX_WS_PAYLOAD_BYTES = 32 * 1024 * 1024
SESSION_KEY_PREFIX = "agent:main:gateway-ui:dm:"
MAX_LIST_ENTRIES = 500
WORKSPACE_WATCH_INTERVAL_SEC = 1.5
MAX_FILE_READ_CHARS = 256_000
MAX_IMAGE_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_HISTORY_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_PREVIEW_MIMES = frozenset({"image/jpeg", "image/png"})
IMAGE_PREVIEW_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
FILE_REFERENCE_RE = re.compile(r"\[File reference:\s*(.+?)\s*\]", re.IGNORECASE)
PROJECT_REFERENCE_RE = re.compile(
    r"\[Project reference:\s*(.+?)\s*\]",
    re.IGNORECASE,
)
IMAGE_ATTACHED_RE = re.compile(
    r"\[Image attached(?:\s+at)?:\s*(.+?)\s*\]",
    re.IGNORECASE,
)
DOCUMENT_ATTACHED_RE = re.compile(
    r"\[The user sent a (?:text )?document: '([^']+)'\.[^\]]*\]",
    re.IGNORECASE,
)


def _message_type_for_media(media_types: List[str]) -> MessageType:
    """Map cached attachment MIME types to the gateway MessageType."""
    if not media_types:
        return MessageType.TEXT
    if any(mt.startswith("image/") for mt in media_types):
        return MessageType.PHOTO
    if any(mt.startswith("audio/") for mt in media_types):
        return MessageType.VOICE
    return MessageType.DOCUMENT
TEXT_PREVIEW_MIMES = frozenset({
    "application/json",
    "application/javascript",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "text/x-python",
})


def _coerce_port(value: Any, default: int = DEFAULT_PORT) -> int:
    try:
        port = int(value)
        return port if 1 <= port <= 65535 else default
    except (TypeError, ValueError):
        return default


def _parse_comma_list(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def check_requirements() -> bool:
    """Return True when the websockets package is installed."""
    try:
        import websockets  # noqa: F401
        return True
    except ImportError:
        return False


def validate_config(config: PlatformConfig) -> bool:
    """Gateway UI always has sane defaults — no mandatory env vars."""
    return True


def is_connected(config: PlatformConfig) -> bool:
    """Configured when host/port resolve."""
    extra = getattr(config, "extra", {}) or {}
    host = os.getenv("GATEWAY_UI_WS_HOST") or extra.get("ws_host") or DEFAULT_HOST
    port = _coerce_port(os.getenv("GATEWAY_UI_WS_PORT") or extra.get("ws_port"))
    return bool(host and port)


def _read_yaml_workspace_root() -> str:
    """Read ``gateway.platforms.gateway-ui.extra.workspace_root`` from config.yaml.

    Hermes' ``load_gateway_config`` only merges the top-level ``platforms:``
    block; plugin settings under ``gateway.platforms`` are otherwise invisible
    to ``PlatformConfig.extra``.  Read them here so the file browser can still
    follow the operator's configured workspace root.
    """
    try:
        import yaml
        from hermes_constants import get_hermes_home

        config_path = get_hermes_home() / "config.yaml"
        if not config_path.exists():
            return ""
        with open(config_path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        gateway_ui = (
            raw.get("gateway", {})
            .get("platforms", {})
            .get("gateway-ui", {})
        )
        extra = gateway_ui.get("extra") or {}
        return str(extra.get("workspace_root") or "").strip()
    except Exception:
        logger.debug("Gateway UI: failed to read workspace_root from config.yaml", exc_info=True)
        return ""


def _env_enablement() -> Dict[str, Any]:
    """Seed PlatformConfig.extra for env-only / default setups."""
    allow_all_env = os.getenv("GATEWAY_UI_ALLOW_ALL_USERS")
    allow_all = _truthy(allow_all_env, default=True) if allow_all_env is not None else True
    extra: Dict[str, Any] = {
        "ws_host": os.getenv("GATEWAY_UI_WS_HOST", DEFAULT_HOST),
        "ws_port": _coerce_port(os.getenv("GATEWAY_UI_WS_PORT", DEFAULT_PORT)),
        "allow_all_users": allow_all,
    }
    workspace = (
        os.getenv("GATEWAY_UI_WORKSPACE_ROOT", "").strip()
        or _read_yaml_workspace_root()
    )
    if workspace:
        extra["workspace_root"] = workspace
    home = os.getenv("GATEWAY_UI_HOME_CHANNEL", "").strip()
    if home:
        extra["home_channel"] = {"chat_id": home, "name": "Gateway UI"}
    return extra


def _coerce_message_text(content: Any) -> str:
    return _parse_message_for_ui(content)["text"]


def _safe_cache_image_data_url(path_str: str) -> Optional[str]:
    """Read a cached gateway image and return a data URL for UI rendering."""
    if not path_str:
        return None
    cleaned = path_str.strip().replace("file://", "")
    try:
        path = Path(cleaned).expanduser().resolve()
        cache_root = get_image_cache_dir().resolve()
        if os.path.commonpath([str(path), str(cache_root)]) != str(cache_root):
            return None
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_HISTORY_IMAGE_BYTES:
            return None
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        if mime not in IMAGE_PREVIEW_MIMES:
            return None
        data = path.read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        logger.debug("Gateway UI: could not load cache image %s", path_str, exc_info=True)
        return None


def _image_attachment_from_url(url: str) -> Optional[Dict[str, str]]:
    cleaned = (url or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("data:"):
        return {"type": "image", "src": cleaned, "name": "image"}
    src = _safe_cache_image_data_url(cleaned)
    if not src:
        return None
    name = Path(cleaned).name or "image"
    return {"type": "image", "src": src, "name": name}


def _parse_message_for_ui(content: Any) -> Dict[str, Any]:
    """Extract display text and UI attachments from a stored message body."""
    attachments: List[Dict[str, str]] = []
    text_parts: List[str] = []

    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    text_parts.append(text)
            elif block_type == "image_url":
                image_url = block.get("image_url") or {}
                url = str(image_url.get("url") or "").strip()
                attachment = _image_attachment_from_url(url)
                if attachment:
                    attachments.append(attachment)
    elif isinstance(content, str):
        if content.strip():
            text_parts.append(content)
    elif content is not None:
        text_parts.append(str(content))

    combined = "\n".join(text_parts).strip()

    for match in FILE_REFERENCE_RE.finditer(combined):
        path = match.group(1).strip()
        name = Path(path).name or path
        attachments.append({"type": "reference", "path": path, "name": name})
    combined = FILE_REFERENCE_RE.sub("", combined).strip()

    for match in PROJECT_REFERENCE_RE.finditer(combined):
        label = match.group(1).strip()
        attachments.append({"type": "reference", "path": label, "name": label})
    combined = PROJECT_REFERENCE_RE.sub("", combined).strip()

    for match in IMAGE_ATTACHED_RE.finditer(combined):
        path = match.group(1).strip()
        attachment = _image_attachment_from_url(path)
        if attachment and not any(
            item.get("type") == "image" and item.get("src") == attachment["src"]
            for item in attachments
        ):
            attachments.append(attachment)
    combined = IMAGE_ATTACHED_RE.sub("", combined).strip()

    for match in DOCUMENT_ATTACHED_RE.finditer(combined):
        name = match.group(1).strip()
        if name and not any(
            item.get("type") == "file" and item.get("name") == name
            for item in attachments
        ):
            attachments.append({"type": "file", "name": name})
    combined = DOCUMENT_ATTACHED_RE.sub("", combined).strip()
    combined = re.sub(r"\n{3,}", "\n\n", combined).strip()

    return {"text": combined, "attachments": attachments}


def _chat_id_from_session_key(session_key: str) -> Optional[str]:
    if not session_key.startswith(SESSION_KEY_PREFIX):
        return None
    remainder = session_key[len(SESSION_KEY_PREFIX):]
    chat_id = remainder.split(":", 1)[0].strip()
    return chat_id or None


def _session_key_for_chat(
    chat_id: str,
    user_id: str = DEFAULT_STABLE_USER_ID,
) -> str:
    return build_session_key(_session_source_for_chat(chat_id, user_id=user_id))


def _session_source_for_chat(
    chat_id: str,
    chat_name: Optional[str] = None,
    user_id: str = DEFAULT_STABLE_USER_ID,
) -> "SessionSource":
    from gateway.session import SessionSource

    resolved_name = (chat_name or "").strip()
    if not resolved_name:
        resolved_name = (
            NOTIFICATIONS_CHAT_NAME
            if chat_id == NOTIFICATIONS_CHAT_ID
            else "Gateway UI"
        )
    return SessionSource(
        platform=Platform("gateway-ui"),
        chat_type="dm",
        chat_id=chat_id,
        user_id=user_id,
        user_name="CEO",
        chat_name=resolved_name,
    )


def _tool_preview_from_args(tool_name: str, args: Dict[str, Any]) -> str:
    if not args:
        return tool_name
    for key in ("path", "command", "query", "url", "name"):
        value = args.get(key)
        if value:
            text = str(value)
            return text[:40] + ("..." if len(text) > 40 else "")
    try:
        encoded = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        encoded = str(args)
    return encoded[:40] + ("..." if len(encoded) > 40 else "")


def _history_to_thread(history: List[Dict[str, Any]], chat_id: str) -> List[Dict[str, Any]]:
    """Map Hermes conversation rows to gateway-ui thread items."""
    thread: List[Dict[str, Any]] = []
    tool_call_args: Dict[str, tuple] = {}
    seq = 0

    for message in history:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            continue

        parsed = _parse_message_for_ui(message.get("content"))
        content_text = parsed["text"]
        attachments = parsed["attachments"]

        if role == "assistant" and message.get("tool_calls"):
            for tool_call in message["tool_calls"]:
                fn = tool_call.get("function", {})
                tool_id = tool_call.get("id", "")
                name = fn.get("name") or "tool"
                if not tool_id:
                    continue
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_call_args[tool_id] = (name, args)
            if not content_text:
                continue

        if role == "tool":
            tool_call_id = message.get("tool_call_id", "")
            tool_info = tool_call_args.get(tool_call_id)
            tool_name = (tool_info[0] if tool_info else None) or message.get("tool_name") or "tool"
            args = (tool_info[1] if tool_info else None) or {}
            preview = _tool_preview_from_args(tool_name, args)
            try:
                args_repr = json.dumps(args, ensure_ascii=False, default=str)
            except Exception:
                args_repr = str(args)
            full = f"{tool_name}({args_repr})" if args else f"{tool_name}()"
            thread.append(
                {
                    "id": f"hist-{chat_id}-tool-{seq}",
                    "type": "tool_call",
                    "tool": tool_name,
                    "preview": preview,
                    "full": full,
                    "status": "success",
                },
            )
            seq += 1
            continue

        if not content_text and not attachments:
            continue

        thread_item: Dict[str, Any] = {
            "id": f"hist-{chat_id}-msg-{seq}",
            "type": "message",
            "role": role,
            "content": content_text,
        }
        if attachments:
            thread_item["attachments"] = attachments
        sent_at = message.get("sent_at")
        if sent_at:
            thread_item["sent_at"] = sent_at
        thread.append(thread_item)
        seq += 1

    return thread


def _build_tool_full(event: Any) -> str:
    tool_name = getattr(event, "tool_name", "tool")
    args = getattr(event, "args", None) or {}
    preview = getattr(event, "preview", None)
    return _build_tool_full_from_name_args(tool_name, preview, args)


def _build_tool_full_from_name_args(
    tool_name: str,
    preview: Optional[str],
    args: Dict[str, Any],
) -> str:
    if args:
        try:
            args_repr = json.dumps(args, ensure_ascii=False, default=str)
        except Exception:
            args_repr = str(args)
        return f"{tool_name}({args_repr})"
    if preview:
        return f'{tool_name}("{preview}")'
    return f"{tool_name}()"


class GatewayUIAdapter(BasePlatformAdapter):
    """WebSocket server adapter for the React gateway-ui client."""

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig, **kwargs: Any):
        platform = Platform("gateway-ui")
        super().__init__(config=config, platform=platform)

        extra = getattr(config, "extra", {}) or {}
        self._host = (
            os.getenv("GATEWAY_UI_WS_HOST")
            or extra.get("ws_host")
            or DEFAULT_HOST
        )
        self._port = _coerce_port(
            os.getenv("GATEWAY_UI_WS_PORT") or extra.get("ws_port", DEFAULT_PORT),
        )

        allow_all_env = os.getenv("GATEWAY_UI_ALLOW_ALL_USERS")
        if allow_all_env is not None:
            self._allow_all_users = _truthy(allow_all_env, default=True)
        else:
            self._allow_all_users = bool(extra.get("allow_all_users", True))

        allowed = extra.get("allowed_users") or []
        if os.getenv("GATEWAY_UI_ALLOWED_USERS"):
            allowed = _parse_comma_list(os.getenv("GATEWAY_UI_ALLOWED_USERS", ""))
        self._allowed_users: Set[str] = {item for item in allowed if item}
        self._stable_user_id = (
            os.getenv("GATEWAY_UI_USER_ID")
            or extra.get("user_id")
            or DEFAULT_STABLE_USER_ID
        ).strip() or DEFAULT_STABLE_USER_ID
        self._sync_gateway_access_env()

        self._server = None
        self._stop_event = asyncio.Event()
        self._chat_sockets: Dict[str, Any] = {}
        self._socket_chats: Dict[int, str] = {}
        self._last_draft_text: Dict[str, str] = {}
        self._active_stream_chat_id: Optional[str] = None
        self._pending_tools: Dict[str, Dict[int, Dict[str, Any]]] = {}
        self._queued_tool_emits: Dict[str, List[Dict[str, Any]]] = {}
        self._live_tool_index: Dict[str, int] = {}
        self._live_tool_stack: Dict[str, List[str]] = {}
        self._live_tool_entries: Dict[str, Dict[str, Any]] = {}
        self._client_websockets: Set[Any] = set()
        self._cron_adapters: Any = None
        self._cron_loop: Any = None
        self._gateway_restart_fn: Any = None
        self._watch_dirs: Set[str] = set()
        self._last_dir_snapshots: Dict[str, Dict[str, Tuple[int, int, bool]]] = {}
        self._watch_task: Optional[asyncio.Task[Any]] = None

    @property
    def name(self) -> str:
        return "Gateway UI"

    @property
    def enforces_own_access_policy(self) -> bool:
        """Adapter gates access via ``allow_all_users`` / ``allowed_users``."""
        return True

    def _sync_gateway_access_env(self) -> None:
        """Mirror adapter access config into env vars the gateway reads.

        ``config.yaml`` ``extra.allow_all_users`` is enforced at the WebSocket
        layer, but ``GatewayRunner._is_user_authorized`` only checks
        ``GATEWAY_UI_ALLOW_ALL_USERS`` in the process environment.  Bridge the
        two so local CEO UI setups work without a separate env file entry.
        """
        if self._allow_all_users:
            os.environ.setdefault("GATEWAY_UI_ALLOW_ALL_USERS", "true")
            return

        if self._allowed_users:
            existing = os.getenv("GATEWAY_UI_ALLOWED_USERS", "").strip()
            merged = {item.strip() for item in existing.split(",") if item.strip()}
            merged.update(self._allowed_users)
            merged.add(self._stable_user_id)
            os.environ.setdefault(
                "GATEWAY_UI_ALLOWED_USERS",
                ",".join(sorted(merged)),
            )

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        self._sync_gateway_access_env()
        try:
            import websockets
        except ImportError:
            logger.error(
                "Gateway UI: 'websockets' package not installed. "
                "Run: pip install websockets",
            )
            return False

        self._stop_event.clear()
        try:
            self._server = await websockets.serve(
                self._ws_handler,
                self._host,
                self._port,
                ping_interval=20,
                ping_timeout=20,
                max_size=MAX_WS_PAYLOAD_BYTES,
            )
        except OSError as exc:
            logger.error(
                "Gateway UI: failed to bind %s:%s — %s",
                self._host,
                self._port,
                exc,
            )
            return False

        self._mark_connected()
        self._ensure_notifications_home()
        self._watch_task = asyncio.create_task(
            self._workspace_watch_loop(),
            name="gateway-ui-workspace-watch",
        )
        logger.info(
            "Gateway UI: WebSocket server listening on ws://%s:%s",
            self._host,
            self._port,
        )
        return True

    async def disconnect(self) -> None:
        self._stop_event.set()
        self._mark_disconnected()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

        self._chat_sockets.clear()
        self._socket_chats.clear()
        self._last_draft_text.clear()
        self._pending_tools.clear()
        self._client_websockets.clear()
        self._watch_dirs.clear()
        self._last_dir_snapshots.clear()
        logger.info("Gateway UI: WebSocket server stopped")

    # ── WebSocket server ─────────────────────────────────────────────────

    async def _ws_handler(self, websocket: Any) -> None:
        socket_id = id(websocket)
        self._client_websockets.add(websocket)
        try:
            async for raw in websocket:
                await self._handle_client_payload(websocket, raw)
        except Exception:
            logger.debug("Gateway UI: client handler error", exc_info=True)
        finally:
            self._client_websockets.discard(websocket)
            chat_id = self._socket_chats.pop(socket_id, None)
            if chat_id and self._chat_sockets.get(chat_id) is websocket:
                self._chat_sockets.pop(chat_id, None)

    async def _handle_client_payload(self, websocket: Any, raw: Any) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            await self._emit_to_socket(
                websocket,
                {"type": "error", "message": "Invalid JSON payload"},
            )
            return

        msg_type = message.get("type")
        request_id = message.get("request_id")

        if msg_type == "workspace_info":
            await self._handle_workspace_info(websocket, request_id)
            return
        if msg_type == "list_files":
            await self._handle_list_files(
                websocket,
                str(message.get("path") or ""),
                request_id,
            )
            return
        if msg_type == "read_file":
            await self._handle_read_file(
                websocket,
                str(message.get("path") or ""),
                request_id,
            )
            return
        if msg_type == "list_chats":
            await self._handle_list_chats(websocket)
            return
        if msg_type == "load_history":
            chat_id = str(message.get("chat_id") or "").strip()
            if not chat_id:
                await self._emit_to_socket(
                    websocket,
                    {"type": "error", "message": "chat_id is required"},
                )
                return
            if not self._is_chat_allowed(chat_id):
                await self._emit_to_socket(
                    websocket,
                    {"type": "error", "message": "Unauthorized chat_id"},
                )
                return
            self._bind_socket_chat(websocket, chat_id)
            await self._handle_load_history(websocket, chat_id)
            return
        if msg_type == "get_home_channel":
            await self._handle_get_home_channel(websocket, request_id)
            return
        if msg_type == "set_home_channel":
            await self._handle_set_home_channel(
                websocket,
                str(message.get("chat_id") or "").strip(),
                str(message.get("name") or "").strip(),
                request_id,
            )
            return
        if msg_type == "ensure_notifications":
            await self._handle_ensure_notifications(websocket, request_id)
            return
        if msg_type == "load_notifications_history":
            await self._handle_load_notifications_history(websocket, request_id)
            return
        if msg_type == "list_projects":
            await self._handle_list_projects(websocket, request_id)
            return
        if msg_type == "read_project":
            await self._handle_read_project(
                websocket,
                str(message.get("project_id") or "").strip(),
                request_id,
            )
            return
        if msg_type == "update_project":
            await self._handle_update_project(
                websocket,
                str(message.get("project_id") or "").strip(),
                message.get("fields") or {},
                request_id,
            )
            return
        if msg_type == "create_project":
            await self._handle_create_project(
                websocket,
                message.get("fields") or {},
                request_id,
            )
            return
        if msg_type == "get_dashboard_summary":
            await self._handle_get_dashboard_summary(websocket, request_id)
            return
        if msg_type == "list_dashboard_issues":
            await self._handle_list_dashboard_issues(websocket, request_id)
            return
        if msg_type == "list_dashboard_actions":
            await self._handle_list_dashboard_actions(websocket, request_id)
            return
        if msg_type == "approve_dashboard_action":
            await self._handle_decide_dashboard_action(
                websocket,
                str(message.get("action_id") or "").strip(),
                "approved",
                str(message.get("note") or "").strip(),
                request_id,
            )
            return
        if msg_type == "decline_dashboard_action":
            await self._handle_decide_dashboard_action(
                websocket,
                str(message.get("action_id") or "").strip(),
                "declined",
                str(message.get("note") or "").strip(),
                request_id,
            )
            return
        if msg_type == "list_cron_jobs":
            await self._handle_list_cron_jobs(websocket, request_id)
            return
        if msg_type == "list_cron_delivery_targets":
            await self._handle_list_cron_delivery_targets(websocket, request_id)
            return
        if msg_type == "list_cron_toolsets":
            await self._handle_list_cron_toolsets(websocket, request_id)
            return
        if msg_type == "create_cron_job":
            await self._handle_create_cron_job(websocket, message, request_id)
            return
        if msg_type == "update_cron_job":
            await self._handle_update_cron_job(websocket, message, request_id)
            return
        if msg_type == "pause_cron_job":
            await self._handle_pause_cron_job(
                websocket,
                str(message.get("job_id") or "").strip(),
                request_id,
            )
            return
        if msg_type == "resume_cron_job":
            await self._handle_resume_cron_job(
                websocket,
                str(message.get("job_id") or "").strip(),
                request_id,
            )
            return
        if msg_type == "trigger_cron_job":
            await self._handle_trigger_cron_job(
                websocket,
                str(message.get("job_id") or "").strip(),
                request_id,
            )
            return
        if msg_type == "delete_cron_job":
            await self._handle_delete_cron_job(
                websocket,
                str(message.get("job_id") or "").strip(),
                request_id,
            )
            return
        if msg_type == "read_soul_md":
            await self._handle_read_soul_md(websocket, request_id)
            return
        if msg_type == "write_soul_md":
            await self._handle_write_soul_md(
                websocket,
                message.get("content"),
                request_id,
            )
            return
        if msg_type == "restart_gateway":
            await self._handle_restart_gateway(websocket, request_id)
            return
        if msg_type != "message":
            return

        chat_id = str(message.get("chat_id") or "").strip()
        if not chat_id:
            await self._emit_to_socket(
                websocket,
                {"type": "error", "message": "chat_id is required"},
            )
            return

        if not self._is_chat_allowed(chat_id):
            await self._emit_to_socket(
                websocket,
                {"type": "error", "message": "Unauthorized chat_id"},
            )
            return

        self._bind_socket_chat(websocket, chat_id)

        client_message_id = str(message.get("client_message_id") or "").strip()
        text = str(message.get("text") or "").strip()
        attachments = message.get("attachments") or []
        media_urls, media_types, ref_lines, attachment_errors = (
            self._decode_attachments(attachments)
        )

        if ref_lines:
            ref_block = "\n".join(ref_lines)
            text = f"{text}\n\n{ref_block}".strip() if text else ref_block

        if attachment_errors and not text and not media_urls:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "message": "; ".join(attachment_errors),
                    "client_message_id": client_message_id or None,
                },
            )
            return

        if not text and not media_urls:
            await self._emit_to_socket(
                websocket,
                {"type": "error", "message": "Empty message"},
            )
            return

        source = self.build_source(
            chat_id=chat_id,
            chat_name="Gateway UI",
            chat_type="dm",
            user_id=self._stable_user_id,
            user_name="CEO",
        )

        msg_type_enum = _message_type_for_media(media_types)

        event = MessageEvent(
            text=text,
            message_type=msg_type_enum,
            source=source,
            message_id=f"gw-ui-{uuid.uuid4().hex[:12]}",
            media_urls=media_urls,
            media_types=media_types,
            timestamp=datetime.now(tz=timezone.utc),
            raw_message=message,
        )

        self._active_stream_chat_id = chat_id
        await self._emit_to_socket(
            websocket,
            {
                "type": "message_accepted",
                "chat_id": chat_id,
                "client_message_id": client_message_id or None,
                "attachment_errors": attachment_errors,
            },
        )
        await self.handle_message(event)

    def _bind_socket_chat(self, websocket: Any, chat_id: str) -> None:
        self._chat_sockets[chat_id] = websocket
        self._socket_chats[id(websocket)] = chat_id

    def _get_session_store(self) -> Any:
        return getattr(self, "_session_store", None)

    def set_cron_tick_context(self, adapters: Any, loop: Any) -> None:
        """Allow manual cron triggers to kick the scheduler immediately."""
        self._cron_adapters = adapters
        self._cron_loop = loop

    def set_gateway_control(self, restart: Any = None) -> None:
        """Wire gateway lifecycle controls from the running GatewayRunner."""
        self._gateway_restart_fn = restart

    def _resolve_session_id(self, chat_id: str) -> Optional[str]:
        store = self._get_session_store()
        if store is None:
            return None
        store._ensure_loaded()
        session_key = _session_key_for_chat(chat_id)
        entry = store._entries.get(session_key)
        if entry is not None:
            return entry.session_id
        return None

    def _persist_outbound_message(self, chat_id: str, content: str) -> None:
        """Persist assistant output so history survives when no client is bound."""
        if not content:
            return
        store = self._get_session_store()
        if store is None:
            return
        try:
            source = _session_source_for_chat(chat_id)
            entry = store.get_or_create_session(source)
            store.append_to_transcript(
                entry.session_id,
                {"role": "assistant", "content": content},
            )
        except Exception:
            logger.debug(
                "Gateway UI: failed to persist outbound message for %s",
                chat_id,
                exc_info=True,
            )

    async def _broadcast_notification_message(self, content: str) -> None:
        if not content:
            return
        sent_at = datetime.now(tz=timezone.utc).isoformat()
        thread_item = {
            "id": f"live-{NOTIFICATIONS_CHAT_ID}-msg-{int(time.time() * 1000)}",
            "type": "message",
            "role": "assistant",
            "content": content,
            "sent_at": sent_at,
        }
        payload = {
            "type": "notification_message",
            "chat_id": NOTIFICATIONS_CHAT_ID,
            "item": thread_item,
        }
        for websocket in list(self._client_websockets):
            await self._emit_to_socket(websocket, payload)

    async def _handle_list_chats(self, websocket: Any) -> None:
        chats = [
            chat
            for chat in self._collect_gateway_ui_chats()
            if chat.get("id") != NOTIFICATIONS_CHAT_ID
        ]
        await self._emit_to_socket(
            websocket,
            {"type": "chats_list", "chats": chats},
        )

    def _ensure_notifications_home(self) -> None:
        try:
            self._apply_home_channel(NOTIFICATIONS_CHAT_ID, NOTIFICATIONS_CHAT_NAME)
        except Exception:
            logger.debug(
                "Gateway UI: failed to ensure notifications home channel",
                exc_info=True,
            )

    async def _handle_ensure_notifications(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        try:
            self._apply_home_channel(NOTIFICATIONS_CHAT_ID, NOTIFICATIONS_CHAT_NAME)
        except Exception as exc:
            logger.debug("Gateway UI: failed to ensure notifications", exc_info=True)
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": f"Could not configure notifications channel: {exc}",
                },
            )
            return

        await self._emit_to_socket(
            websocket,
            {
                "type": "home_channel",
                "request_id": request_id,
                "chat_id": NOTIFICATIONS_CHAT_ID,
                "name": NOTIFICATIONS_CHAT_NAME,
            },
        )

    async def _handle_list_projects(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        await self._emit_to_socket(
            websocket,
            {
                "type": "projects_list",
                "request_id": request_id,
                "projects": get_project_store().list_projects(),
            },
        )

    async def _handle_read_project(
        self,
        websocket: Any,
        project_id: str,
        request_id: Optional[str],
    ) -> None:
        if not project_id:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "project_id is required",
                },
            )
            return
        try:
            project = get_project_store().read_project(project_id)
        except KeyError:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": f"Project not found: {project_id}",
                },
            )
            return
        except Exception as exc:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": str(exc),
                },
            )
            return

        await self._emit_to_socket(
            websocket,
            {
                "type": "project_detail",
                "request_id": request_id,
                "project": project,
            },
        )

    async def _handle_create_project(
        self,
        websocket: Any,
        fields: Dict[str, Any],
        request_id: Optional[str],
    ) -> None:
        if not isinstance(fields, dict) or not fields:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "fields object is required",
                },
            )
            return
        try:
            project = get_project_store().create_project(fields)
        except Exception as exc:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": str(exc),
                },
            )
            return

        await self._emit_to_socket(
            websocket,
            {
                "type": "project_created",
                "request_id": request_id,
                "project": project,
            },
        )

    async def _handle_update_project(
        self,
        websocket: Any,
        project_id: str,
        fields: Dict[str, Any],
        request_id: Optional[str],
    ) -> None:
        if not project_id:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "project_id is required",
                },
            )
            return
        if not isinstance(fields, dict) or not fields:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "fields object is required",
                },
            )
            return
        try:
            project = get_project_store().update_fields(project_id, fields)
        except KeyError:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": f"Project not found: {project_id}",
                },
            )
            return
        except Exception as exc:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": str(exc),
                },
            )
            return

        await self._emit_to_socket(
            websocket,
            {
                "type": "project_updated",
                "request_id": request_id,
                "project": project,
            },
        )

    async def _handle_get_dashboard_summary(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        await self._emit_to_socket(
            websocket,
            {
                "type": "dashboard_summary",
                "request_id": request_id,
                "summary": get_dashboard_summary(),
            },
        )

    async def _handle_list_dashboard_issues(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        await self._emit_to_socket(
            websocket,
            {
                "type": "dashboard_issues_list",
                "request_id": request_id,
                "issues": get_dashboard_issue_store().list_issues(
                    include_resolved=False,
                ),
            },
        )

    async def _handle_list_dashboard_actions(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        await self._emit_to_socket(
            websocket,
            {
                "type": "dashboard_actions_list",
                "request_id": request_id,
                "actions": get_dashboard_action_store().list_actions(
                    status="pending",
                ),
            },
        )

    async def _handle_decide_dashboard_action(
        self,
        websocket: Any,
        action_id: str,
        decision: str,
        note: str,
        request_id: Optional[str],
    ) -> None:
        if not action_id:
            await self._emit_cron_error(
                websocket,
                request_id,
                "action_id is required",
            )
            return
        try:
            action = get_dashboard_action_store().decide(
                action_id,
                decision,
                note=note,
            )
        except KeyError:
            await self._emit_cron_error(
                websocket,
                request_id,
                f"Action not found: {action_id}",
            )
            return
        except Exception as exc:
            await self._emit_cron_error(websocket, request_id, str(exc))
            return

        await self._emit_to_socket(
            websocket,
            {
                "type": "dashboard_action_updated",
                "request_id": request_id,
                "action": action,
            },
        )

    async def _emit_cron_error(
        self,
        websocket: Any,
        request_id: Optional[str],
        message: str,
    ) -> None:
        await self._emit_to_socket(
            websocket,
            {
                "type": "error",
                "request_id": request_id,
                "message": message,
            },
        )

    async def _handle_list_cron_jobs(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        try:
            jobs = cron_api.list_cron_jobs()
        except Exception as exc:
            await self._emit_cron_error(websocket, request_id, str(exc))
            return
        await self._emit_to_socket(
            websocket,
            {
                "type": "cron_jobs_list",
                "request_id": request_id,
                "jobs": jobs,
            },
        )

    async def _handle_list_cron_delivery_targets(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        await self._emit_to_socket(
            websocket,
            {
                "type": "cron_delivery_targets",
                "request_id": request_id,
                "targets": cron_api.list_delivery_targets(),
            },
        )

    async def _handle_list_cron_toolsets(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        await self._emit_to_socket(
            websocket,
            {
                "type": "cron_toolsets_list",
                "request_id": request_id,
                "toolsets": list_cron_toolsets(),
            },
        )

    async def _handle_create_cron_job(
        self,
        websocket: Any,
        message: Dict[str, Any],
        request_id: Optional[str],
    ) -> None:
        try:
            job = cron_api.create_cron_job(
                prompt=str(message.get("prompt") or ""),
                schedule=str(message.get("schedule") or ""),
                name=str(message.get("name") or ""),
                deliver=message.get("deliver"),
                enabled_toolsets=message.get("enabled_toolsets"),
            )
        except Exception as exc:
            await self._emit_cron_error(websocket, request_id, str(exc))
            return
        await self._emit_to_socket(
            websocket,
            {
                "type": "cron_job_created",
                "request_id": request_id,
                "job": job,
            },
        )

    async def _handle_update_cron_job(
        self,
        websocket: Any,
        message: Dict[str, Any],
        request_id: Optional[str],
    ) -> None:
        job_id = str(message.get("job_id") or "").strip()
        if not job_id:
            await self._emit_cron_error(websocket, request_id, "job_id is required")
            return
        fields = message.get("fields") or {}
        if not isinstance(fields, dict):
            await self._emit_cron_error(websocket, request_id, "fields object is required")
            return
        try:
            job = cron_api.update_cron_job(
                job_id,
                prompt=fields.get("prompt"),
                schedule=fields.get("schedule"),
                name=fields.get("name"),
                deliver=fields.get("deliver"),
                enabled_toolsets=fields.get("enabled_toolsets"),
            )
        except Exception as exc:
            await self._emit_cron_error(websocket, request_id, str(exc))
            return
        await self._emit_to_socket(
            websocket,
            {
                "type": "cron_job_updated",
                "request_id": request_id,
                "job": job,
            },
        )

    async def _handle_pause_cron_job(
        self,
        websocket: Any,
        job_id: str,
        request_id: Optional[str],
    ) -> None:
        if not job_id:
            await self._emit_cron_error(websocket, request_id, "job_id is required")
            return
        try:
            job = cron_api.pause_cron_job(job_id)
        except Exception as exc:
            await self._emit_cron_error(websocket, request_id, str(exc))
            return
        await self._emit_to_socket(
            websocket,
            {
                "type": "cron_job_updated",
                "request_id": request_id,
                "job": job,
            },
        )

    async def _handle_resume_cron_job(
        self,
        websocket: Any,
        job_id: str,
        request_id: Optional[str],
    ) -> None:
        if not job_id:
            await self._emit_cron_error(websocket, request_id, "job_id is required")
            return
        try:
            job = cron_api.resume_cron_job(job_id)
        except Exception as exc:
            await self._emit_cron_error(websocket, request_id, str(exc))
            return
        await self._emit_to_socket(
            websocket,
            {
                "type": "cron_job_updated",
                "request_id": request_id,
                "job": job,
            },
        )

    async def _handle_trigger_cron_job(
        self,
        websocket: Any,
        job_id: str,
        request_id: Optional[str],
    ) -> None:
        if not job_id:
            await self._emit_cron_error(websocket, request_id, "job_id is required")
            return
        try:
            job = cron_api.trigger_cron_job(
                job_id,
                adapters=getattr(self, "_cron_adapters", None),
                loop=getattr(self, "_cron_loop", None),
            )
        except Exception as exc:
            await self._emit_cron_error(websocket, request_id, str(exc))
            return
        await self._emit_to_socket(
            websocket,
            {
                "type": "cron_job_updated",
                "request_id": request_id,
                "job": job,
            },
        )

    async def _handle_delete_cron_job(
        self,
        websocket: Any,
        job_id: str,
        request_id: Optional[str],
    ) -> None:
        if not job_id:
            await self._emit_cron_error(websocket, request_id, "job_id is required")
            return
        try:
            removed = cron_api.delete_cron_job(job_id)
        except Exception as exc:
            await self._emit_cron_error(websocket, request_id, str(exc))
            return
        if not removed:
            await self._emit_cron_error(websocket, request_id, f"Job not found: {job_id}")
            return
        await self._emit_to_socket(
            websocket,
            {
                "type": "cron_job_deleted",
                "request_id": request_id,
                "job_id": job_id,
            },
        )

    async def _handle_read_soul_md(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        try:
            payload = settings_api.read_soul_md()
        except Exception as exc:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": str(exc),
                },
            )
            return
        await self._emit_to_socket(
            websocket,
            {
                "type": "soul_md",
                "request_id": request_id,
                **payload,
            },
        )

    async def _handle_write_soul_md(
        self,
        websocket: Any,
        content: Any,
        request_id: Optional[str],
    ) -> None:
        if content is None:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "content is required",
                },
            )
            return
        try:
            payload = settings_api.write_soul_md(str(content))
        except Exception as exc:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": str(exc),
                },
            )
            return
        await self._emit_to_socket(
            websocket,
            {
                "type": "soul_md_saved",
                "request_id": request_id,
                **payload,
            },
        )

    async def _handle_restart_gateway(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        try:
            payload = settings_api.request_gateway_restart(
                restart_fn=self._gateway_restart_fn,
            )
        except Exception as exc:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": str(exc),
                },
            )
            return
        await self._emit_to_socket(
            websocket,
            {
                "type": "gateway_restart_started",
                "request_id": request_id,
                **payload,
            },
        )

    async def _handle_load_notifications_history(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        thread = await self._load_chat_thread(NOTIFICATIONS_CHAT_ID)
        await self._emit_to_socket(
            websocket,
            {
                "type": "notifications_history",
                "request_id": request_id,
                "chat_id": NOTIFICATIONS_CHAT_ID,
                "thread": thread,
            },
        )

    def _resolve_home_channel_name(self, chat_id: str) -> str:
        if chat_id == NOTIFICATIONS_CHAT_ID:
            return NOTIFICATIONS_CHAT_NAME
        for chat in self._collect_gateway_ui_chats():
            if chat.get("id") == chat_id:
                return (
                    str(chat.get("title") or "").strip()
                    or str(chat.get("preview") or "").strip()
                    or chat_id
                )
        return chat_id

    def _apply_home_channel(self, chat_id: str, chat_name: str) -> None:
        from hermes_cli.config import save_env_value

        save_env_value(HOME_CHANNEL_ENV, chat_id)
        save_env_value(HOME_CHANNEL_THREAD_ENV, "")
        os.environ[HOME_CHANNEL_ENV] = chat_id
        os.environ.pop(HOME_CHANNEL_THREAD_ENV, None)
        self.config.home_channel = HomeChannel(
            platform=Platform("gateway-ui"),
            chat_id=chat_id,
            name=chat_name or chat_id,
        )

    async def _handle_get_home_channel(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        chat_id = os.getenv(HOME_CHANNEL_ENV, "").strip()
        if not chat_id and self.config.home_channel:
            chat_id = str(self.config.home_channel.chat_id or "").strip()

        payload: Dict[str, Any] = {
            "type": "home_channel",
            "request_id": request_id,
            "chat_id": chat_id or None,
            "name": None,
        }
        if chat_id:
            payload["name"] = self._resolve_home_channel_name(chat_id)
        await self._emit_to_socket(websocket, payload)

    async def _handle_set_home_channel(
        self,
        websocket: Any,
        chat_id: str,
        chat_name: str,
        request_id: Optional[str],
    ) -> None:
        if not chat_id:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "chat_id is required",
                },
            )
            return

        if not self._is_chat_allowed(chat_id):
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "Unauthorized chat_id",
                },
            )
            return

        resolved_name = chat_name or self._resolve_home_channel_name(chat_id)
        try:
            self._apply_home_channel(chat_id, resolved_name)
        except Exception as exc:
            logger.debug("Gateway UI: failed to set home channel", exc_info=True)
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": f"Could not save home channel: {exc}",
                },
            )
            return

        await self._emit_to_socket(
            websocket,
            {
                "type": "home_channel",
                "request_id": request_id,
                "chat_id": chat_id,
                "name": resolved_name,
            },
        )

    def _collect_gateway_ui_chats(self) -> List[Dict[str, Any]]:
        store = self._get_session_store()
        if store is None:
            return []

        store._ensure_loaded()
        db = getattr(store, "_db", None)
        rich_by_id: Dict[str, Dict[str, Any]] = {}
        if db is not None:
            try:
                rows = db.list_sessions_rich(
                    source="gateway-ui",
                    order_by_last_active=True,
                    limit=200,
                )
                rich_by_id = {row["id"]: row for row in rows}
            except Exception:
                logger.debug("Gateway UI: failed to list sessions", exc_info=True)

        chats: Dict[str, Dict[str, Any]] = {}
        for session_key, entry in store._entries.items():
            chat_id = _chat_id_from_session_key(session_key)
            if not chat_id:
                continue

            rich = rich_by_id.get(entry.session_id, {})
            title = (rich.get("title") or "").strip()
            preview = (rich.get("preview") or "").strip()
            updated_at = rich.get("last_active") or rich.get("started_at") or 0
            if hasattr(entry.updated_at, "timestamp"):
                updated_at = max(updated_at, entry.updated_at.timestamp())

            chats[chat_id] = {
                "id": chat_id,
                "title": title,
                "preview": preview,
                "updated_at": updated_at,
                "message_count": int(rich.get("message_count") or 0),
            }

        return sorted(
            chats.values(),
            key=lambda item: item.get("updated_at") or 0,
            reverse=True,
        )

    async def _load_chat_thread(self, chat_id: str) -> List[Dict[str, Any]]:
        thread: List[Dict[str, Any]] = []
        store = self._get_session_store()
        session_id = self._resolve_session_id(chat_id)

        if store is not None and session_id and getattr(store, "_db", None) is not None:
            try:
                history = store._db.get_messages_as_conversation(
                    session_id,
                    include_ancestors=True,
                )
                thread = _history_to_thread(history, chat_id)
            except Exception:
                logger.debug(
                    "Gateway UI: failed to load history for %s",
                    chat_id,
                    exc_info=True,
                )
        elif store is not None and session_id:
            try:
                history = store.load_transcript(session_id)
                thread = _history_to_thread(history, chat_id)
            except Exception:
                logger.debug(
                    "Gateway UI: failed to load transcript for %s",
                    chat_id,
                    exc_info=True,
                )

        return thread

    async def _handle_load_history(self, websocket: Any, chat_id: str) -> None:
        thread = await self._load_chat_thread(chat_id)
        await self._emit_to_socket(
            websocket,
            {
                "type": "history",
                "chat_id": chat_id,
                "thread": thread,
            },
        )

    def _is_chat_allowed(self, chat_id: str) -> bool:
        if self._allow_all_users:
            return True
        if not self._allowed_users:
            return True
        return chat_id in self._allowed_users

    def _resolve_workspace_root(self) -> Path:
        extra = getattr(self.config, "extra", {}) or {}
        configured = (
            os.getenv("GATEWAY_UI_WORKSPACE_ROOT")
            or extra.get("workspace_root")
            or _read_yaml_workspace_root()
            or ""
        ).strip()
        if configured:
            candidate = Path(configured).expanduser().resolve()
            if candidate.is_dir():
                return candidate

        try:
            from agent.runtime_cwd import resolve_agent_cwd

            return resolve_agent_cwd().resolve()
        except Exception:
            return Path.home().resolve()

    @staticmethod
    def _normalize_relative_path(rel_path: str) -> str:
        return str(rel_path or "").strip().replace("\\", "/").strip("/")

    def _track_watched_dir(self, rel_path: str) -> None:
        self._watch_dirs.add(self._normalize_relative_path(rel_path))

    def _snapshot_directory(
        self,
        directory: Path,
    ) -> Dict[str, Tuple[int, int, bool]]:
        snap: Dict[str, Tuple[int, int, bool]] = {}
        try:
            for child in directory.iterdir():
                if child.name in {".", ".."}:
                    continue
                try:
                    stat = child.stat()
                    snap[child.name] = (
                        int(stat.st_mtime_ns),
                        int(stat.st_size),
                        child.is_dir(),
                    )
                except OSError:
                    continue
        except OSError:
            return {}
        return snap

    def _detect_workspace_changes(self) -> List[str]:
        if not self._client_websockets:
            self._last_dir_snapshots.clear()
            return []

        changed: List[str] = []
        for rel_dir in sorted(self._watch_dirs):
            target = self._safe_workspace_path(rel_dir)
            if target is None or not target.is_dir():
                continue

            current = self._snapshot_directory(target)
            previous = self._last_dir_snapshots.get(rel_dir)
            if previous is not None and current != previous:
                changed.append(rel_dir)
            self._last_dir_snapshots[rel_dir] = current

        return changed

    async def _workspace_watch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=WORKSPACE_WATCH_INTERVAL_SEC,
                )
                break
            except asyncio.TimeoutError:
                pass

            try:
                changed_paths = self._detect_workspace_changes()
                if changed_paths:
                    await self._broadcast_workspace_changed(changed_paths)
            except Exception:
                logger.debug("Gateway UI: workspace watch failed", exc_info=True)

    async def _broadcast_workspace_changed(self, paths: List[str]) -> None:
        if not paths or not self._client_websockets:
            return

        payload = {
            "type": "workspace_changed",
            "paths": paths,
        }
        for websocket in list(self._client_websockets):
            await self._emit_to_socket(websocket, payload)

    def _safe_workspace_path(self, rel_path: str) -> Optional[Path]:
        root = self._resolve_workspace_root()
        normalized = str(rel_path or "").strip().replace("\\", "/").strip("/")
        target = (root / normalized).resolve() if normalized else root
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return target

    def _workspace_entry(self, entry_path: Path, root: Path) -> Dict[str, Any]:
        rel = "" if entry_path == root else str(entry_path.relative_to(root))
        is_dir = entry_path.is_dir()
        stat = entry_path.stat()
        mime = None
        if not is_dir:
            mime = mimetypes.guess_type(entry_path.name)[0] or "application/octet-stream"
        return {
            "id": rel or entry_path.name,
            "name": entry_path.name,
            "path": rel,
            "type": "folder" if is_dir else "file",
            "size": None if is_dir else stat.st_size,
            "modified": datetime.fromtimestamp(
                stat.st_mtime,
                tz=timezone.utc,
            ).isoformat(),
            "mime": mime,
        }

    def _is_image_previewable(self, path: Path, mime: Optional[str]) -> bool:
        if mime in IMAGE_PREVIEW_MIMES:
            return True
        return path.suffix.lower() in IMAGE_PREVIEW_SUFFIXES

    def _image_preview_mime(self, path: Path, mime: Optional[str]) -> str:
        if mime in IMAGE_PREVIEW_MIMES:
            return mime
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".png":
            return "image/png"
        return mime or "application/octet-stream"

    def _is_text_previewable(self, path: Path, mime: Optional[str]) -> bool:
        if mime and (mime.startswith("text/") or mime in TEXT_PREVIEW_MIMES):
            return True
        suffix = path.suffix.lower()
        return suffix in {
            ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".py",
            ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".xml",
            ".sh", ".toml", ".ini", ".env", ".log",
        }

    async def _handle_workspace_info(
        self,
        websocket: Any,
        request_id: Optional[str],
    ) -> None:
        root = self._resolve_workspace_root()
        await self._emit_to_socket(
            websocket,
            {
                "type": "workspace_info",
                "request_id": request_id,
                "root": str(root),
                "name": root.name or str(root),
            },
        )

    async def _handle_list_files(
        self,
        websocket: Any,
        rel_path: str,
        request_id: Optional[str],
    ) -> None:
        root = self._resolve_workspace_root()
        target = self._safe_workspace_path(rel_path)
        if target is None or not target.exists():
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "Path not found",
                },
            )
            return
        if not target.is_dir():
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "Not a directory",
                },
            )
            return

        entries: List[Dict[str, Any]] = []
        try:
            children = sorted(
                target.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.lower()),
            )
            for child in children[:MAX_LIST_ENTRIES]:
                if child.name in {".", ".."}:
                    continue
                try:
                    entries.append(self._workspace_entry(child, root))
                except OSError:
                    continue
        except OSError as exc:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": f"Could not list directory: {exc}",
                },
            )
            return

        normalized_path = self._normalize_relative_path(rel_path)
        self._track_watched_dir(normalized_path)

        await self._emit_to_socket(
            websocket,
            {
                "type": "files_list",
                "request_id": request_id,
                "path": normalized_path,
                "root": str(root),
                "entries": entries,
            },
        )

    async def _handle_read_file(
        self,
        websocket: Any,
        rel_path: str,
        request_id: Optional[str],
    ) -> None:
        target = self._safe_workspace_path(rel_path)
        if target is None or not target.exists() or not target.is_file():
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": "File not found",
                },
            )
            return

        rel = str(rel_path or "").strip().replace("\\", "/").strip("/")
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"

        if self._is_image_previewable(target, mime):
            try:
                size = target.stat().st_size
            except OSError as exc:
                await self._emit_to_socket(
                    websocket,
                    {
                        "type": "error",
                        "request_id": request_id,
                        "message": f"Could not read file: {exc}",
                    },
                )
                return

            if size > MAX_IMAGE_PREVIEW_BYTES:
                await self._emit_to_socket(
                    websocket,
                    {
                        "type": "file_content",
                        "request_id": request_id,
                        "path": rel,
                        "previewable": False,
                        "mime": self._image_preview_mime(target, mime),
                    },
                )
                return

            try:
                data_b64 = base64.b64encode(target.read_bytes()).decode("ascii")
            except OSError as exc:
                await self._emit_to_socket(
                    websocket,
                    {
                        "type": "error",
                        "request_id": request_id,
                        "message": f"Could not read file: {exc}",
                    },
                )
                return

            await self._emit_to_socket(
                websocket,
                {
                    "type": "file_content",
                    "request_id": request_id,
                    "path": rel,
                    "previewable": True,
                    "preview_kind": "image",
                    "mime": self._image_preview_mime(target, mime),
                    "data_b64": data_b64,
                },
            )
            return

        if not self._is_text_previewable(target, mime):
            await self._emit_to_socket(
                websocket,
                {
                    "type": "file_content",
                    "request_id": request_id,
                    "path": rel,
                    "previewable": False,
                    "mime": mime,
                },
            )
            return

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            await self._emit_to_socket(
                websocket,
                {
                    "type": "error",
                    "request_id": request_id,
                    "message": f"Could not read file: {exc}",
                },
            )
            return

        truncated = len(text) > MAX_FILE_READ_CHARS
        if truncated:
            text = text[:MAX_FILE_READ_CHARS]

        await self._emit_to_socket(
            websocket,
            {
                "type": "file_content",
                "request_id": request_id,
                "path": rel,
                "previewable": True,
                "preview_kind": "text",
                "mime": mime,
                "text": text,
                "truncated": truncated,
            },
        )

    def _decode_attachments(
        self,
        attachments: List[Dict[str, Any]],
    ) -> tuple[List[str], List[str], List[str], List[str]]:
        media_urls: List[str] = []
        media_types: List[str] = []
        ref_lines: List[str] = []
        attachment_errors: List[str] = []

        for attachment in attachments:
            name = str(attachment.get("name") or "file")
            mime = str(attachment.get("mime_type") or "application/octet-stream")
            data_b64 = attachment.get("data_b64") or ""
            if not data_b64:
                attachment_errors.append(f"Attachment '{name}' was empty")
                continue

            try:
                data = base64.b64decode(data_b64)
            except Exception:
                logger.debug("Gateway UI: invalid base64 attachment for %s", name)
                attachment_errors.append(f"Attachment '{name}' could not be decoded")
                continue

            if mime == REFERENCE_MIME:
                try:
                    ref = json.loads(data.decode("utf-8"))
                    ref_type = str(ref.get("type") or "file").strip().lower()
                    if ref_type == "project":
                        project_id = str(
                            ref.get("id") or ref.get("path") or name
                        ).strip()
                        code = str(ref.get("code") or "").strip()
                        title = str(ref.get("name") or "").strip()
                        label = (
                            f"{code} ({project_id})"
                            if code
                            else project_id
                        )
                        hint = (
                            f"{label} — {title}"
                            if title and title not in label
                            else label
                        )
                        ref_lines.append(
                            "[Project reference: "
                            f"{hint}] Use read_project(project_id="
                            f"{project_id!r}) and project_manager tools."
                        )
                    else:
                        path = ref.get("path") or name
                        ref_lines.append(f"[File reference: {path}]")
                except Exception:
                    ref_lines.append(f"[File reference: {name}]")
                continue

            try:
                if mime.startswith("image/"):
                    ext = "." + mime.split("/", 1)[1].replace("jpeg", "jpg")
                    cached = cache_image_from_bytes(data, ext)
                else:
                    cached = cache_document_from_bytes(data, name)
            except Exception as exc:
                logger.debug(
                    "Gateway UI: failed to cache attachment %s",
                    name,
                    exc_info=True,
                )
                attachment_errors.append(
                    f"Attachment '{name}' could not be processed ({exc})",
                )
                continue

            if cached:
                media_urls.append(cached)
                media_types.append(mime)

        return media_urls, media_types, ref_lines, attachment_errors

    # ── Outbound streaming ───────────────────────────────────────────────

    def _schedule_tool_emit(self, chat_id: str, payload: Dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._emit(chat_id, payload))
        except RuntimeError:
            self._queued_tool_emits.setdefault(chat_id, []).append(payload)

    def _prepare_tool_started_payload(
        self,
        chat_id: str,
        tool_name: Optional[str],
        preview: Optional[str],
        args: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not chat_id or not tool_name:
            return None

        self._active_stream_chat_id = chat_id
        tool_args = args or {}
        seq = self._live_tool_index.get(chat_id, 0)
        self._live_tool_index[chat_id] = seq + 1
        tool_id = f"tool-{chat_id}-{seq}"

        preview_text = (preview or "").strip() or _tool_preview_from_args(
            tool_name,
            tool_args,
        )
        if len(preview_text) > 40:
            preview_text = preview_text[:37] + "..."

        entry = {
            "id": tool_id,
            "tool": tool_name,
            "preview": preview_text,
            "full": _build_tool_full_from_name_args(tool_name, preview, tool_args),
            "status": "running",
        }
        self._live_tool_entries[tool_id] = entry
        self._live_tool_stack.setdefault(chat_id, []).append(tool_id)
        return {
            "type": "tool_call",
            **entry,
        }

    def _prepare_tool_completed_payload(
        self,
        chat_id: str,
        tool_name: Optional[str],
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        if not chat_id or not tool_name:
            return None

        stack = self._live_tool_stack.get(chat_id, [])
        tool_id: Optional[str] = None
        for index in range(len(stack) - 1, -1, -1):
            candidate_id = stack[index]
            entry = self._live_tool_entries.get(candidate_id)
            if (
                entry
                and entry.get("tool") == tool_name
                and entry.get("status") == "running"
            ):
                tool_id = candidate_id
                del stack[index]
                break

        if not tool_id:
            return None

        entry = self._live_tool_entries[tool_id]
        status = "error" if kwargs.get("is_error") else "success"
        entry["status"] = status
        return {
            "type": "tool_call",
            "id": entry["id"],
            "tool": entry["tool"],
            "preview": entry["preview"],
            "full": entry["full"],
            "status": status,
        }

    async def emit_tool_started(
        self,
        chat_id: str,
        tool_name: Optional[str],
        preview: Optional[str],
        args: Optional[Dict[str, Any]],
    ) -> None:
        payload = self._prepare_tool_started_payload(
            chat_id,
            tool_name,
            preview,
            args,
        )
        if payload is not None:
            await self._emit(chat_id, payload)

    async def emit_tool_completed(
        self,
        chat_id: str,
        tool_name: Optional[str],
        **kwargs: Any,
    ) -> None:
        payload = self._prepare_tool_completed_payload(
            chat_id,
            tool_name,
            **kwargs,
        )
        if payload is not None:
            await self._emit(chat_id, payload)

    async def _emit(self, chat_id: str, payload: Dict[str, Any]) -> None:
        websocket = self._chat_sockets.get(chat_id)
        if websocket is None:
            return
        enriched = dict(payload)
        enriched.setdefault("chat_id", chat_id)
        await self._emit_to_socket(websocket, enriched)

    async def _emit_to_socket(self, websocket: Any, payload: Dict[str, Any]) -> None:
        try:
            await websocket.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            logger.debug("Gateway UI: failed to send payload", exc_info=True)

    def supports_draft_streaming(
        self,
        chat_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return True

    async def _flush_queued_tool_emits(self, chat_id: str) -> None:
        queued = self._queued_tool_emits.pop(chat_id, [])
        for payload in queued:
            await self._emit(chat_id, payload)

    async def send_draft(
        self,
        chat_id: str,
        draft_id: int,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        self._active_stream_chat_id = chat_id
        await self._flush_queued_tool_emits(chat_id)
        previous = self._last_draft_text.get(chat_id, "")
        if content.startswith(previous):
            delta = content[len(previous):]
        else:
            delta = content
        self._last_draft_text[chat_id] = content

        if delta:
            await self._emit(chat_id, {"type": "text_chunk", "text": delta})

        return SendResult(success=True, message_id=f"draft-{draft_id}")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        self._active_stream_chat_id = chat_id
        await self._flush_queued_tool_emits(chat_id)
        self._last_draft_text.pop(chat_id, None)

        if content:
            await self._emit(chat_id, {"type": "text_done", "text": content})
            self._persist_outbound_message(chat_id, content)
            if chat_id == NOTIFICATIONS_CHAT_ID:
                await self._broadcast_notification_message(content)

        await self._finalize_tools(chat_id)
        return SendResult(success=True, message_id=f"gw-ui-{int(time.time() * 1000)}")

    async def send_typing(
        self,
        chat_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._active_stream_chat_id = chat_id
        await self._flush_queued_tool_emits(chat_id)
        await self._emit(chat_id, {"type": "typing"})

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {
            "name": "Gateway UI",
            "type": "dm",
            "chat_id": chat_id,
            "host": self._host,
            "port": self._port,
        }

    def format_tool_event(
        self,
        event: Any,
        *,
        mode: str = "all",
        preview_max_len: int = 40,
    ) -> Optional[str]:
        from gateway.stream_events import ToolCallChunk

        if not isinstance(event, ToolCallChunk):
            return None

        chat_id = self._active_stream_chat_id
        if not chat_id:
            return None

        preview = event.preview or ""
        if preview and preview_max_len > 0 and len(preview) > preview_max_len:
            preview = preview[: preview_max_len - 3] + "..."

        tool_id = f"tool-{chat_id}-{event.index}"
        pending = self._pending_tools.setdefault(chat_id, {})
        pending[event.index] = {
            "id": tool_id,
            "tool": event.tool_name,
            "preview": preview,
            "full": _build_tool_full(event),
        }

        payload = {
            "type": "tool_call",
            "id": tool_id,
            "tool": event.tool_name,
            "preview": preview,
            "full": pending[event.index]["full"],
            "status": "running",
        }

        self._schedule_tool_emit(chat_id, payload)
        return None

    async def _finalize_tools(self, chat_id: str) -> None:
        pending = self._pending_tools.pop(chat_id, {})
        for entry in pending.values():
            await self._emit(
                chat_id,
                {
                    "type": "tool_call",
                    "id": entry["id"],
                    "tool": entry["tool"],
                    "preview": entry["preview"],
                    "full": entry["full"],
                    "status": "success",
                },
            )


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system at startup."""
    register_project_tools(ctx)
    register_dashboard_issue_tools(ctx)
    register_dashboard_action_tools(ctx)
    ctx.register_platform(
        name="gateway-ui",
        label="Gateway UI",
        adapter_factory=lambda cfg: GatewayUIAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        install_hint="pip install websockets",
        env_enablement_fn=_env_enablement,
        allowed_users_env="GATEWAY_UI_ALLOWED_USERS",
        allow_all_env="GATEWAY_UI_ALLOW_ALL_USERS",
        cron_deliver_env_var="GATEWAY_UI_HOME_CHANNEL",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji="🖥️",
        pii_safe=True,
        allow_update_command=True,
        platform_hint=(
            "You are assisting a CEO through the Gateway UI web client. "
            "Use clear, executive-friendly language. Markdown is supported. "
            "When referencing workspace files, be specific about paths and "
            "actionable next steps. "
            "Use the project_manager tools to read, create, and update the CEO project "
            "portfolio shown in the Projects page. Only mutate fields allowed "
            "by those tools and append updates with add_log_to_project when "
            "recording decisions or risks. "
            "Use dashboard_issues tools to create and update critical issues "
            "shown on the Dashboard (e.g. after cron scans or incidents). "
            "Use dashboard_actions tools to queue pending CEO approvals in the "
            "Action Center; link issue_id and project_id when relevant."
        ),
    )
