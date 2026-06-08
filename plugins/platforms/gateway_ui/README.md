# Gateway UI platform plugin

WebSocket server adapter for the [boss-agent](https://github.com/mohammadrezaesk/boss-agent) React client.

## Enable

Add to `~/.hermes/config.yaml`:

```yaml
gateway:
  platforms:
    gateway-ui:
      enabled: true
      extra:
        ws_host: 127.0.0.1
        ws_port: 8765
        allow_all_users: true
        workspace_root: /path/to/JooJoo
```

Environment variables (override config `extra`):

| Variable | Default | Description |
|----------|---------|-------------|
| `GATEWAY_UI_WS_HOST` | `127.0.0.1` | WebSocket listen host |
| `GATEWAY_UI_WS_PORT` | `8765` | WebSocket listen port |
| `GATEWAY_UI_ALLOW_ALL_USERS` | `true` | Allow any browser session |
| `GATEWAY_UI_WORKSPACE_ROOT` | Hermes `terminal.cwd` | File browser root |

Requires `websockets` (`pip install websockets`).

## Run

```bash
hermes gateway run
```

Point the UI at `ws://127.0.0.1:8765` (`VITE_HERMES_WS_URL` at build time for production).

## Features exposed to the UI

- Chat (streaming, tool calls, attachments)
- Workspace file browser
- Projects portfolio (`list_projects`, `read_project`, `create_project`, `update_project`)
- Dashboard issues & action center
- Cron job management
- Settings (`SOUL.md`, gateway restart)

Agent toolsets: `project_manager`, `dashboard_issues`, `dashboard_actions` (enable in session/cron as needed).

## Data persistence

JSON stores under `~/.hermes/gateway-ui/`:

- `issues.json`
- `actions.json`

Project portfolio is in-memory (seeded from mock data on first start; mutations persist for the gateway process lifetime).
