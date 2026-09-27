# MC Router UI

[![CI](https://github.com/Tamino089/mc-router-ui/actions/workflows/ci.yml/badge.svg)](https://github.com/Tamino089/mc-router-ui/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)

A self-hosted web UI for [mc-router](https://github.com/itzg/mc-router), the
Minecraft reverse proxy. Manage routes, keep Cloudflare DNS pointed at your
public IP, and control Crafty Controller servers from one dashboard.

Ships as a single container: mc-router and the web UI are supervised together by
supervisord.

## Features

- Hostname routes with validation before anything is applied, plus an optional
  catch-all fallback backend
- Cloudflare DDNS: a DNS-only A record per route, updated when your IP changes
- Crafty Controller: server stats, start/stop/restart, and port changes that
  rewrite `server.properties` and re-point the affected routes
- Docker label discovery for `mc-router.host` containers
- TCP health checks with latency history
- Live updates over server-sent events
- Multi-user with granular permissions
- PBKDF2 passwords, signed sessions, same-origin enforcement, strict CSP, and an
  unprivileged container

## Quick start

```yaml
services:
  mc-router-ui:
    image: ghcr.io/tamino089/mc-router-ui:latest
    container_name: mc-router-ui
    restart: unless-stopped
    network_mode: host
    environment:
      ADMIN_PASSWORD: "change-me-to-something-long"
    volumes:
      - mc-router-ui-data:/data

volumes:
  mc-router-ui-data:
```

Open `http://<host>:8000` and sign in as `admin`. `ADMIN_PASSWORD` is required on
first start, needs at least 12 characters, and only bootstraps the account.

### Networking

Health checks and the Crafty integration connect directly from the container to
your backends. On the default bridge network Docker does not resolve other
containers by name, so `crafty:25565` will never become healthy. Use
`network_mode: host`, share a user-defined network with your servers, or use
routable LAN addresses such as `192.168.1.50:25565`.

### Docker socket

Route discovery needs `/var/run/docker.sock`. The container runs unprivileged, so
grant the socket's group id (`stat -c '%g' /var/run/docker.sock`, often 999):

```yaml
    group_add: ["999"]
```

### Unraid

Import `my-mc-router-ui.xml`, then install from **Docker, then Add Container,
then Template, then MC-Router-UI**. The template defaults to host networking.

## Configuration

Only `ADMIN_PASSWORD` is required, on first start.

| Variable | Default | Description |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | Bootstrap admin username |
| `ADMIN_PASSWORD` | *required* | Bootstrap password, 12+ characters |
| `SECRET_KEY` | *generated* | Session key, generated into the database if unset |
| `DB_PATH` | `/data/mcrouter-ui.db` | SQLite location |
| `MC_ROUTER_API` | `http://localhost:8080` | Router API to manage |
| `MC_PORT` / `API_PORT` | `25565` / `8080` | Minecraft and router API ports |
| `LOG_LEVEL` | `INFO` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG` |
| `MIN_PASSWORD_LENGTH` | `12` | Minimum account password length |
| `SESSION_HTTPS_ONLY` | `false` | Set `true` behind a TLS proxy; also sends HSTS |
| `SESSION_SAME_SITE` | `lax` | `SameSite` on the session cookie |
| `SESSION_MAX_AGE` | `1209600` | Session lifetime in seconds |
| `CLOUDFLARE_API_TOKEN` | | Token with `Zone:DNS:Edit` |
| `CLOUDFLARE_ZONE_ID` | | Zone id. Use this or the zone name. |
| `CLOUDFLARE_ZONE_NAME` | | Zone name, e.g. `example.com` |
| `DDNS_INTERVAL_SECONDS` | `300` | Public IP re-check interval |
| `CRAFTY_URL` | | Base URL, http(s), no embedded credentials |
| `CRAFTY_API_KEY` | | Crafty bearer token |
| `CRAFTY_CONTAINER_HOST` | | Host for TCP checks against Crafty servers |
| `CRAFTY_SERVERS_DIR` | | Path to Crafty's servers directory |
| `CRAFTY_INSECURE_SKIP_VERIFY` | `false` | Skip TLS verification; trusted networks only |
| `SERVER_PROPERTIES_PATH` | | Exact `server.properties` path |
| `HEALTH_CHECK_INTERVAL` | `30` | Seconds between health checks |
| `HEALTH_HISTORY_RETENTION_HOURS` | `24` | Latency history retention |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Socket for label discovery |

An empty field saved in Settings falls back to the environment rather than
overriding it.

## Permissions

Admins hold everything implicitly. Accounts can be granted: `see_own_routes`,
`see_all_routes`, `create_route`, `edit_own_route`, `delete_own_route`,
`manage_default_route`, `see_cloudflare`, `manage_cloudflare`, `see_servers`,
`manage_servers`, `see_all_users`, `manage_users`, `manage_settings`.

`manage_default_route` is not granted by default, because the fallback route
receives all unmatched traffic.

## Architecture

| Process | Port | Role |
|---|---|---|
| `mc-router` | 25565, 8080 | Routes Minecraft handshakes to backends by hostname |
| `uvicorn` | 8000 | Serves the FastAPI application and dashboard |

The router API is internal and unauthenticated, so do not publish port 8080.

| Path | Contents |
|---|---|
| `app/core/` | Config, security headers, CSRF, rate limiting, validation, hashing |
| `app/db/` | SQLite schema, in-place migrations, per-request connections |
| `app/routes/` | HTTP handlers |
| `app/services/` | mc-router, Cloudflare, Crafty, Docker, health, SSE |
| `app/templates/`, `app/static/` | Jinja2 templates, CSS, dashboard JavaScript |

Route mutations take a lock, apply the change to Cloudflare and mc-router, then
commit to SQLite and compensate in reverse on failure, so the database stays
authoritative. Database transactions are short and never held open across a
network call.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check app

DB_PATH=/tmp/mc-router-ui.db ADMIN_PASSWORD=a-long-enough-password \
  .venv/bin/uvicorn app.main:app --reload
```

CI lints, builds the image, smoke tests the container and scans it. Pushing to
`master` publishes to
[GHCR](https://github.com/Tamino089/mc-router-ui/pkgs/container/mc-router-ui)
tagged `latest` and by commit SHA.

## Before exposing this to the internet

- Set `ADMIN_PASSWORD` and change it in Settings afterwards.
- Set `SESSION_HTTPS_ONLY=true` when serving over TLS.
- Never publish the router API port.
- Prefer a Cloudflare token scoped to `Zone:DNS:Edit` for one zone.
- Mounting the Docker socket grants control over the Docker daemon. It is mounted
  read-only and the container is unprivileged, but it remains a privileged
  capability.

## Troubleshooting

**Routes never become reachable**: the container cannot reach the backend. See
Networking above.

**Cloudflare records are not created**: check the token has `Zone:DNS:Edit`, and
that a zone id or zone name is set.

**Crafty will not connect**: for a self-signed certificate set
`CRAFTY_INSECURE_SKIP_VERIFY=true`. Do not include `/api/v2` in `CRAFTY_URL`.

**Port changes fail**: mount the Crafty servers directory and set
`CRAFTY_CONTAINER_HOST`.

**Docker routes are missing**: mount the socket and grant its group id.

## License

MIT. See [LICENSE](./LICENSE).
