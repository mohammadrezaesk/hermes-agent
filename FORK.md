# Algonet fork notes

This repository is a fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) with the **Gateway UI** CEO assistant platform.

| Repo | URL |
|------|-----|
| Hermes fork (this repo) | https://github.com/mohammadrezaesk/hermes-agent |
| Gateway UI (React client) | https://github.com/mohammadrezaesk/boss-agent |

## What's added

- `plugins/platforms/gateway_ui/` — WebSocket adapter, projects, dashboard, cron UI API, settings
- Toolsets: `project_manager`, `dashboard_issues`, `dashboard_actions`
- Gateway integration for document attachments (xlsx, pdf, …)

See [plugins/platforms/gateway_ui/README.md](plugins/platforms/gateway_ui/README.md) for configuration.

## Sync with upstream

```bash
git remote add upstream git@github.com:NousResearch/hermes-agent.git  # once
git fetch upstream
git merge upstream/main
# Resolve conflicts — keep `plugins/platforms/gateway_ui/` and fork-specific toolset wiring.
git push origin main
```

## VPS deployment (summary)

1. **Hermes** — build/run from this fork; persist `~/.hermes` (config, API keys, plugin data).
2. **Gateway UI** — clone [boss-agent](https://github.com/mohammadrezaesk/boss-agent), `npm ci && npm run build`, serve `dist/` with nginx/Caddy.
3. **WebSocket** — reverse-proxy `wss://your-domain/ws` → Hermes gateway UI port (default `8765`).
4. **Build-time UI env** — `VITE_HERMES_WS_URL=wss://your-domain/ws`.

Example Hermes config:

```yaml
gateway:
  platforms:
    gateway-ui:
      enabled: true
      extra:
        ws_host: 127.0.0.1
        ws_port: 8765
        allow_all_users: false
        workspace_root: /path/to/workspace
```
