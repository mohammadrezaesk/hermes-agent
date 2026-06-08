"""Tests for the Gateway UI platform plugin."""

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datetime import datetime, timezone

from gateway.platforms.base import MessageType

from plugins.platforms.gateway_ui.adapter import (
    GatewayUIAdapter,
    NOTIFICATIONS_CHAT_ID,
    NOTIFICATIONS_CHAT_NAME,
    SESSION_KEY_PREFIX,
    _build_tool_full,
    _chat_id_from_session_key,
    _env_enablement,
    _history_to_thread,
    _message_type_for_media,
    _parse_message_for_ui,
    check_requirements,
    register,
    validate_config,
)
from gateway.session import SessionEntry, SessionSource
from gateway.config import Platform
from gateway.stream_events import ToolCallChunk


class _MockPluginContext:
    def __init__(self):
        self.registered_names = []

    def register_platform(self, *, name, label, adapter_factory, check_fn, **kwargs):
        from gateway.platform_registry import PlatformEntry, platform_registry

        platform_registry.register(
            PlatformEntry(
                name=name,
                label=label,
                adapter_factory=adapter_factory,
                check_fn=check_fn,
                **kwargs,
            ),
        )
        self.registered_names.append(name)


@pytest.fixture
def clean_registry():
    from gateway.platform_registry import platform_registry

    original = dict(platform_registry._entries)
    platform_registry._entries.clear()
    yield platform_registry
    platform_registry._entries.clear()
    platform_registry._entries.update(original)


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.extra = {"ws_host": "127.0.0.1", "ws_port": 8765, "allow_all_users": True}
    config.enabled = True
    config.token = None
    config.api_key = None
    config.home_channel = None
    config.reply_to_mode = "first"
    return config


def test_register_creates_platform_entry(clean_registry):
    ctx = _MockPluginContext()
    register(ctx)

    from gateway.platform_registry import platform_registry

    entry = platform_registry.get("gateway-ui")
    assert entry is not None
    assert entry.name == "gateway-ui"
    assert entry.label == "Gateway UI"


def test_check_requirements_returns_bool():
    assert isinstance(check_requirements(), bool)


def test_validate_config_always_true(mock_config):
    assert validate_config(mock_config) is True


def test_env_enablement_defaults():
    extra = _env_enablement()
    assert extra["ws_host"] == "127.0.0.1"
    assert extra["ws_port"] == 8765
    assert extra["allow_all_users"] is True


def test_decode_attachments_handles_reference_and_image(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    ref_payload = base64.b64encode(
        json.dumps({"id": "file-1", "type": "file", "path": "/reports/q1.xlsx"}).encode(),
    ).decode()
    image_payload = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()

    media_urls, media_types, ref_lines, attachment_errors = adapter._decode_attachments(
        [
            {
                "name": "q1.xlsx",
                "mime_type": "application/x-hermes-reference",
                "data_b64": ref_payload,
            },
            {
                "name": "chart.png",
                "mime_type": "image/png",
                "data_b64": image_payload,
            },
        ],
    )

    assert ref_lines == ["[File reference: /reports/q1.xlsx]"]
    assert len(media_urls) == 1
    assert media_types == ["image/png"]
    assert attachment_errors == []


def test_message_type_for_media_marks_documents():
    assert _message_type_for_media([]) == MessageType.TEXT
    assert _message_type_for_media(["image/png"]) == MessageType.PHOTO
    assert _message_type_for_media(["audio/ogg"]) == MessageType.VOICE
    assert (
        _message_type_for_media(
            ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
        )
        == MessageType.DOCUMENT
    )


def test_decode_attachments_handles_xlsx_document(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    xlsx_payload = base64.b64encode(b"PK\x03\x04fake-xlsx").decode()

    media_urls, media_types, ref_lines, attachment_errors = adapter._decode_attachments(
        [
            {
                "name": "income.xlsx",
                "mime_type": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
                "data_b64": xlsx_payload,
            },
        ],
    )

    assert ref_lines == []
    assert len(media_urls) == 1
    assert media_types == [
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ]
    assert attachment_errors == []
    assert Path(media_urls[0]).exists()


def test_parse_message_for_ui_extracts_document_attachment():
    parsed = _parse_message_for_ui(
        "[The user sent a document: 'income.xlsx'. "
        "The file is saved at: /root/.hermes/cache/documents/doc_abc_income.xlsx. "
        "Ask the user what you'd like you to do with it.]\n\n"
        "read this file"
    )
    assert parsed["text"] == "read this file"
    assert parsed["attachments"] == [{"type": "file", "name": "income.xlsx"}]


def test_decode_attachments_reports_invalid_image(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    bad_image = base64.b64encode(b"not-an-image").decode()

    media_urls, media_types, ref_lines, attachment_errors = adapter._decode_attachments(
        [
            {
                "name": "broken.png",
                "mime_type": "image/png",
                "data_b64": bad_image,
            },
        ],
    )

    assert media_urls == []
    assert media_types == []
    assert ref_lines == []
    assert len(attachment_errors) == 1
    assert "broken.png" in attachment_errors[0]


@pytest.mark.asyncio
async def test_send_draft_emits_text_chunk_delta(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    adapter._chat_sockets["chat-1"] = MagicMock()
    adapter._chat_sockets["chat-1"].send = pytest.AsyncMock()

    await adapter.send_draft("chat-1", 1, "Hello")
    await adapter.send_draft("chat-1", 1, "Hello world")

    calls = adapter._chat_sockets["chat-1"].send.await_args_list
    first = json.loads(calls[0].args[0])
    second = json.loads(calls[1].args[0])

    assert first == {"type": "text_chunk", "text": "Hello", "chat_id": "chat-1"}
    assert second == {"type": "text_chunk", "text": " world", "chat_id": "chat-1"}


@pytest.mark.asyncio
async def test_send_emits_text_done(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    adapter._chat_sockets["chat-1"] = MagicMock()
    adapter._chat_sockets["chat-1"].send = pytest.AsyncMock()

    result = await adapter.send("chat-1", "Final answer")

    assert result.success is True
    payload = json.loads(adapter._chat_sockets["chat-1"].send.await_args.args[0])
    assert payload == {
        "type": "text_done",
        "text": "Final answer",
        "chat_id": "chat-1",
    }


@pytest.mark.asyncio
async def test_send_persists_notification_without_bound_socket(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    session_id = "20260608_014900_notify01"
    appended: list = []

    class _MockStore:
        def get_or_create_session(self, source):
            assert source.chat_id == NOTIFICATIONS_CHAT_ID
            assert source.chat_name == NOTIFICATIONS_CHAT_NAME
            return MagicMock(session_id=session_id)

        def append_to_transcript(self, sid, message):
            assert sid == session_id
            appended.append(message)

    adapter._session_store = _MockStore()

    result = await adapter.send(NOTIFICATIONS_CHAT_ID, "Daily report ready")

    assert result.success is True
    assert appended == [{"role": "assistant", "content": "Daily report ready"}]


@pytest.mark.asyncio
async def test_send_broadcasts_notification_to_connected_clients(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()
    adapter._client_websockets.add(websocket)

    result = await adapter.send(NOTIFICATIONS_CHAT_ID, "Ping from cron")

    assert result.success is True
    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["type"] == "notification_message"
    assert payload["chat_id"] == NOTIFICATIONS_CHAT_ID
    assert payload["item"]["type"] == "message"
    assert payload["item"]["role"] == "assistant"
    assert payload["item"]["content"] == "Ping from cron"


def test_format_tool_event_queues_running_tool(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    adapter._active_stream_chat_id = "chat-1"

    event = ToolCallChunk(
        tool_name="read_file",
        preview="/reports/q1.xlsx",
        args={"path": "/reports/q1.xlsx"},
        index=2,
    )
    rendered = adapter.format_tool_event(event, preview_max_len=40)

    assert rendered is None
    assert len(adapter._queued_tool_emits["chat-1"]) == 1
    payload = adapter._queued_tool_emits["chat-1"][0]
    assert payload["type"] == "tool_call"
    assert payload["tool"] == "read_file"
    assert payload["status"] == "running"
    assert payload["id"] == "tool-chat-1-2"


@pytest.mark.asyncio
async def test_emit_tool_started_streams_running_tool(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    adapter._chat_sockets["chat-1"] = MagicMock()
    adapter._chat_sockets["chat-1"].send = pytest.AsyncMock()

    await adapter.emit_tool_started(
        "chat-1",
        "write_file",
        "notes/todo.md",
        {"path": "notes/todo.md"},
    )

    payload = json.loads(adapter._chat_sockets["chat-1"].send.await_args.args[0])
    assert payload["type"] == "tool_call"
    assert payload["status"] == "running"
    assert payload["tool"] == "write_file"
    assert payload["chat_id"] == "chat-1"


@pytest.mark.asyncio
async def test_emit_tool_completed_updates_tool_status(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    adapter._chat_sockets["chat-1"] = MagicMock()
    adapter._chat_sockets["chat-1"].send = pytest.AsyncMock()

    await adapter.emit_tool_started(
        "chat-1",
        "read_file",
        "notes/todo.md",
        {"path": "notes/todo.md"},
    )
    adapter._chat_sockets["chat-1"].send.reset_mock()

    await adapter.emit_tool_completed("chat-1", "read_file", is_error=False)

    payload = json.loads(adapter._chat_sockets["chat-1"].send.await_args.args[0])
    assert payload["type"] == "tool_call"
    assert payload["status"] == "success"
    assert payload["tool"] == "read_file"


@pytest.mark.asyncio
async def test_format_tool_event_emits_running_tool_immediately(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    adapter._active_stream_chat_id = "chat-1"
    adapter._chat_sockets["chat-1"] = MagicMock()
    adapter._chat_sockets["chat-1"].send = pytest.AsyncMock()

    event = ToolCallChunk(
        tool_name="write_file",
        preview="notes/todo.md",
        args={"path": "notes/todo.md"},
        index=0,
    )
    adapter.format_tool_event(event, preview_max_len=40)
    await asyncio.sleep(0)

    adapter._chat_sockets["chat-1"].send.assert_awaited()
    payload = json.loads(adapter._chat_sockets["chat-1"].send.await_args.args[0])
    assert payload["type"] == "tool_call"
    assert payload["status"] == "running"
    assert payload["tool"] == "write_file"


def test_chat_id_from_session_key():
    chat_id = "550e8400-e29b-41d4-a716-446655440000"
    session_key = f"{SESSION_KEY_PREFIX}{chat_id}"
    assert _chat_id_from_session_key(session_key) == chat_id
    assert _chat_id_from_session_key("agent:main:telegram:dm:123") is None


def test_history_to_thread_maps_roles_and_tools():
    chat_id = "chat-abc"
    history = [
        {"role": "user", "content": "Hello"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "/reports/q1.xlsx"}',
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc-1",
            "content": "ok",
        },
        {"role": "assistant", "content": "Here is the summary."},
    ]

    thread = _history_to_thread(history, chat_id)

    assert thread[0]["type"] == "message"
    assert thread[0]["role"] == "user"
    assert thread[1]["type"] == "tool_call"
    assert thread[1]["tool"] == "read_file"
    assert thread[1]["status"] == "success"
    assert thread[2]["type"] == "message"
    assert thread[2]["role"] == "assistant"


def test_history_to_thread_includes_sent_at():
    thread = _history_to_thread(
        [
            {
                "role": "user",
                "content": "Hello",
                "sent_at": "2026-06-08T12:30:00+00:00",
            },
        ],
        "chat-1",
    )
    assert thread[0]["sent_at"] == "2026-06-08T12:30:00+00:00"


@pytest.mark.asyncio
async def test_handle_list_chats_returns_gateway_ui_sessions(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    chat_id = "chat-list-1"
    session_key = f"{SESSION_KEY_PREFIX}{chat_id}"
    now = datetime.now(tz=timezone.utc)
    origin = SessionSource(
        platform=Platform("gateway-ui"),
        chat_type="dm",
        chat_id=chat_id,
        user_id=chat_id,
    )
    entry = SessionEntry(
        session_key=session_key,
        session_id="20260101_120000_abcd1234",
        created_at=now,
        updated_at=now,
        origin=origin,
        platform=Platform("gateway-ui"),
        chat_type="dm",
    )

    class _MockDB:
        def list_sessions_rich(self, **kwargs):
            return [
                {
                    "id": "20260101_120000_abcd1234",
                    "title": "Q1 review",
                    "preview": "Summarize Q1",
                    "last_active": 1_700_000_000,
                    "message_count": 4,
                },
            ]

    class _MockStore:
        def _ensure_loaded(self):
            return None

        _entries = {session_key: entry}
        _db = _MockDB()

    adapter._session_store = _MockStore()
    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_list_chats(websocket)

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["type"] == "chats_list"
    assert payload["chats"][0]["id"] == chat_id
    assert payload["chats"][0]["title"] == "Q1 review"
    assert payload["chats"][0]["message_count"] == 4


@pytest.mark.asyncio
async def test_handle_load_history_emits_thread(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    chat_id = "chat-history-1"
    session_key = f"{SESSION_KEY_PREFIX}{chat_id}"
    now = datetime.now(tz=timezone.utc)
    entry = SessionEntry(
        session_key=session_key,
        session_id="20260101_120000_hist01",
        created_at=now,
        updated_at=now,
    )

    class _MockDB:
        def get_messages_as_conversation(self, session_id, include_ancestors=False):
            assert session_id == "20260101_120000_hist01"
            return [
                {"role": "user", "content": "Ping"},
                {"role": "assistant", "content": "Pong"},
            ]

    class _MockStore:
        def _ensure_loaded(self):
            return None

        _entries = {session_key: entry}
        _db = _MockDB()

        def load_transcript(self, session_id):
            return []

    adapter._session_store = _MockStore()
    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_load_history(websocket, chat_id)

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload == {
        "type": "history",
        "chat_id": chat_id,
        "thread": [
            {
                "id": f"hist-{chat_id}-msg-0",
                "type": "message",
                "role": "user",
                "content": "Ping",
            },
            {
                "id": f"hist-{chat_id}-msg-1",
                "type": "message",
                "role": "assistant",
                "content": "Pong",
            },
        ],
    }


@pytest.mark.asyncio
async def test_emit_includes_chat_id(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    adapter._chat_sockets["chat-1"] = MagicMock()
    adapter._chat_sockets["chat-1"].send = pytest.AsyncMock()

    await adapter._emit("chat-1", {"type": "typing"})

    payload = json.loads(adapter._chat_sockets["chat-1"].send.await_args.args[0])
    assert payload == {"type": "typing", "chat_id": "chat-1"}


def test_safe_workspace_path_blocks_traversal(mock_config, tmp_path):
    adapter = GatewayUIAdapter(mock_config)
    root = tmp_path / "workspace"
    root.mkdir()
    adapter._resolve_workspace_root = lambda: root  # type: ignore[method-assign]

    assert adapter._safe_workspace_path("reports/q1.md") == root / "reports/q1.md"
    assert adapter._safe_workspace_path("../etc/passwd") is None


def test_parse_message_for_ui_extracts_references_and_text():
    parsed = _parse_message_for_ui(
        "Save this file\n\n[File reference: media/report.json]",
    )
    assert parsed["text"] == "Save this file"
    assert parsed["attachments"] == [
        {
            "type": "reference",
            "path": "media/report.json",
            "name": "report.json",
        },
    ]


def test_history_to_thread_includes_image_only_message():
    data_url = "data:image/png;base64,iVBORw0KGgo="
    thread = _history_to_thread(
        [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "chat-1",
    )
    assert len(thread) == 1
    assert thread[0]["content"] == ""
    assert thread[0]["attachments"] == [
        {"type": "image", "src": data_url, "name": "image"},
    ]


@pytest.mark.asyncio
async def test_handle_set_home_channel_persists_env(mock_config, monkeypatch, tmp_path):
    adapter = GatewayUIAdapter(mock_config)
    env_file = tmp_path / ".env"
    monkeypatch.setattr("hermes_cli.config.get_env_path", lambda: env_file)
    monkeypatch.setattr("hermes_cli.config.ensure_hermes_home", lambda: None)

    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_set_home_channel(
        websocket,
        "chat-home-1",
        "CEO inbox",
        "req-home",
    )

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["type"] == "home_channel"
    assert payload["chat_id"] == "chat-home-1"
    assert payload["name"] == "CEO inbox"
    assert os.environ["GATEWAY_UI_HOME_CHANNEL"] == "chat-home-1"
    assert env_file.read_text(encoding="utf-8").startswith(
        "GATEWAY_UI_HOME_CHANNEL=chat-home-1",
    )


@pytest.mark.asyncio
async def test_handle_get_home_channel_returns_current(mock_config, monkeypatch):
    adapter = GatewayUIAdapter(mock_config)
    monkeypatch.setenv("GATEWAY_UI_HOME_CHANNEL", "chat-home-2")

    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_get_home_channel(websocket, "req-get")

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload == {
        "type": "home_channel",
        "request_id": "req-get",
        "chat_id": "chat-home-2",
        "name": "chat-home-2",
    }


@pytest.mark.asyncio
async def test_handle_ensure_notifications_sets_home_channel(
    mock_config,
    monkeypatch,
    tmp_path,
):
    adapter = GatewayUIAdapter(mock_config)
    env_file = tmp_path / ".env"
    monkeypatch.setattr("hermes_cli.config.get_env_path", lambda: env_file)
    monkeypatch.setattr("hermes_cli.config.ensure_hermes_home", lambda: None)

    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_ensure_notifications(websocket, "req-notify")

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload == {
        "type": "home_channel",
        "request_id": "req-notify",
        "chat_id": NOTIFICATIONS_CHAT_ID,
        "name": NOTIFICATIONS_CHAT_NAME,
    }
    assert os.environ["GATEWAY_UI_HOME_CHANNEL"] == NOTIFICATIONS_CHAT_ID


@pytest.mark.asyncio
async def test_handle_load_notifications_history(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    adapter._load_chat_thread = pytest.AsyncMock(  # type: ignore[method-assign]
        return_value=[{"id": "msg-1", "type": "message", "role": "assistant", "content": "Ping"}],
    )

    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_load_notifications_history(websocket, "req-history")

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["type"] == "notifications_history"
    assert payload["request_id"] == "req-history"
    assert payload["chat_id"] == NOTIFICATIONS_CHAT_ID
    assert payload["thread"][0]["content"] == "Ping"


@pytest.mark.asyncio
async def test_handle_list_chats_excludes_notifications_channel(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    adapter._collect_gateway_ui_chats = lambda: [  # type: ignore[method-assign]
        {"id": "chat-1", "title": "CEO", "preview": "", "updated_at": 2},
        {
            "id": NOTIFICATIONS_CHAT_ID,
            "title": NOTIFICATIONS_CHAT_NAME,
            "preview": "",
            "updated_at": 1,
        },
    ]

    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_list_chats(websocket)

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["type"] == "chats_list"
    assert [chat["id"] for chat in payload["chats"]] == ["chat-1"]


@pytest.mark.asyncio
async def test_handle_read_file_returns_image_preview(mock_config, tmp_path):
    adapter = GatewayUIAdapter(mock_config)
    root = tmp_path / "workspace"
    root.mkdir()
    image_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (root / "logo.png").write_bytes(image_bytes)
    adapter._resolve_workspace_root = lambda: root  # type: ignore[method-assign]

    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_read_file(websocket, "logo.png", "req-img")

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["type"] == "file_content"
    assert payload["previewable"] is True
    assert payload["preview_kind"] == "image"
    assert payload["mime"] == "image/png"
    assert base64.b64decode(payload["data_b64"]) == image_bytes


def test_detect_workspace_changes_notifies_watched_dir(mock_config, tmp_path):
    adapter = GatewayUIAdapter(mock_config)
    root = tmp_path / "workspace"
    docs = root / "docs"
    docs.mkdir(parents=True)
    adapter._resolve_workspace_root = lambda: root  # type: ignore[method-assign]
    adapter._track_watched_dir("docs")
    adapter._client_websockets.add(object())

    assert adapter._detect_workspace_changes() == []
    (docs / "new-file.md").write_text("hello", encoding="utf-8")
    assert adapter._detect_workspace_changes() == ["docs"]


@pytest.mark.asyncio
async def test_handle_list_files_registers_watch_dir(mock_config, tmp_path):
    adapter = GatewayUIAdapter(mock_config)
    root = tmp_path / "workspace"
    target = root / "notes"
    target.mkdir(parents=True)
    adapter._resolve_workspace_root = lambda: root  # type: ignore[method-assign]

    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_list_files(websocket, "notes", "req-watch")

    assert "notes" in adapter._watch_dirs


@pytest.mark.asyncio
async def test_handle_list_files_returns_entries(mock_config, tmp_path):
    adapter = GatewayUIAdapter(mock_config)
    root = tmp_path / "workspace"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "todo.md").write_text("hello", encoding="utf-8")
    adapter._resolve_workspace_root = lambda: root  # type: ignore[method-assign]

    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_list_files(websocket, "", "req-1")

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["type"] == "files_list"
    names = {entry["name"] for entry in payload["entries"]}
    assert "notes" in names


@pytest.mark.asyncio
async def test_handle_list_projects_returns_mock_data(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    websocket = MagicMock()
    websocket.send = pytest.AsyncMock()

    await adapter._handle_list_projects(websocket, "req-projects")

    payload = json.loads(websocket.send.await_args.args[0])
    assert payload["type"] == "projects_list"
    assert payload["request_id"] == "req-projects"
    assert len(payload["projects"]) >= 4
    assert payload["projects"][0]["title"]
    assert payload["projects"][0]["status"] in {
        "on_track",
        "at_risk",
        "off_track",
        "completed",
    }


def test_adapter_uses_stable_user_id_not_chat_id(mock_config):
    adapter = GatewayUIAdapter(mock_config)
    assert adapter.enforces_own_access_policy is True
    assert adapter._stable_user_id == "gateway-ui-user"

    source = adapter.build_source(
        chat_id="new-chat-uuid-123",
        chat_name="Gateway UI",
        chat_type="dm",
        user_id=adapter._stable_user_id,
        user_name="CEO",
    )
    assert source.chat_id == "new-chat-uuid-123"
    assert source.user_id == "gateway-ui-user"
    assert source.user_id != source.chat_id


def test_build_tool_full_with_args():
    event = ToolCallChunk(
        tool_name="terminal",
        preview="ls -la",
        args={"command": "ls -la"},
        index=0,
    )
    full = _build_tool_full(event)
    assert full.startswith("terminal(")
    assert "ls -la" in full
