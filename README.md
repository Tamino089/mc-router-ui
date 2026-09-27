# MC Router UI

[![CI](https://github.com/Tamino089/mc-router-ui/actions/workflows/ci.yml/badge.svg)](https://github.com/Tamino089/mc-router-ui/actions/workflows/ci.yml)
[![Publish Docker image](https://github.com/Tamino089/mc-router-ui/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Tamino089/mc-router-ui/actions/workflows/docker-publish.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)

A self-hosted web UI for [mc-router](https://github.com/itzg/mc-router), the
Minecraft reverse proxy: manage routes, keep Cloudflare DNS in sync with your
public IP, and control Crafty Controller servers from one dashboard.

Runs as a single container. mc-router and the web UI are supervised together by
supervisord, so the router, its API and the dashboard share one filesystem and
one lifecycle.

## Features

| Area | What it does |
|---|---|
| Route management | Add, edit and delete hostname routes, validated before anything is applied |
| Fallback route | Optional catch-all backend for clients requesting an unknown hostname |
| Cloudflare DDNS | Creates DNS-only A records per route and updates them when your public IP changes |
| Crafty Controller | Lists servers with live CPU/RAM, starts, stops and restarts them, and changes their port |
| Docker discovery | Surfaces routes from `mc-router.host` container labels as read-only entries |
| Health monitoring | Background TCP checks with latency history and a per-route sparkline |
| Live updates | Server-sent events push health, connection counts and route changes to the browser |
| Multi-user | Role-based access with granular per-account permissions |
| Security | PBKDF2 passwords, signed sessions, same-origin enforcement, strict CSP, unprivileged container |

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
      MC_PORT: "25565"
      API_PORT: "8080"
      # Optional: Cloudflare DDNS
      CLOUDFLARE_API_TOKEN: ""
      CLOUDFLARE_ZONE_NAME: ""
      # Optional: Crafty Controller
      CRAFTY_URL: ""
      CRAFTY_API_KEY: ""
    volumes:
      - mc-router-ui-data:/data

volumes:
  mc-router-ui-data:
```

Then open `http://<host>:8000` and sign in as `admin`.

`ADMIN_PASSWORD` is required on first start and must be at least 12 characters.
It only bootstraps the account; later changes are made in Settings.

### Networking

Health checks and the Crafty integration open a direct connection from the
container to your backends. On the default bridge network, Docker's DNS does not
resolve other containers by name unless they share a user-defined network, so a
backend such as `crafty:25565` will never become healthy.

Use one of these:

1. `network_mode: host`, so the container reaches anything the host can reach.
2. The same user-defined network as your Minecraft and Crafty containers.
3. Routable LAN addresses, for example `192.168.1.50:25565`.

### Unraid

Import `my-mc-router-ui.xml` into
`/boot/config/plugins/docker/templates-user/`, then install it from
**Docker, then Add Container, then Template, then MC-Router-UI**. The template
defaults to host networking. To build locally instead of pulling, use
`scripts/update-unraid.sh` with `MODE=build`.

## Architecture

One container, two supervised processes:

| Process | Listens on | Role |
|---|---|---|
| `mc-router` | `25565` (Minecraft), `8080` (REST API) | Routes Minecraft handshakes to backends by hostname |
| `uvicorn` | `8000` | Serves the FastAPI application and the dashboard |

They communicate over loopback HTTP. The mc-router API port is internal and
should never be published; it has no authentication of its own.

```
Browser  ->  uvicorn :8000  ->  SQLite (/data/mcrouter-ui.db)
                   |
                   +-------->  mc-router API :8080   (push routes, read connections)
                   +-------->  Cloudflare API       (DNS records, DDNS)
                   +-------->  Crafty API           (server control, server.properties)
                   +-------->  Docker socket        (label discovery, read-only)

Minecraft client  ->  mc-router :25565  ->  your servers
```

### Layout

| Path | Contents |
|---|---|
| `app/main.py` | Application entry point, lifespan, dashboard route, CSP nonce plumbing |
| `app/core/` | Configuration, security headers and CSRF, rate limiting, validation, password hashing |
| `app/db/` | SQLite schema, in-place migrations, per-request connections |
| `app/routes/` | HTTP handlers: auth, routes, users, settings, Cloudflare, Crafty, monitoring, SSE |
| `app/services/` | mc-router, Cloudflare, Crafty, Docker, health checks, SSE engine |
| `app/templates/`, `app/static/` | Jinja2 templates and the dashboard's CSS and JavaScript |
| `tests/` | Pytest suite |

### How a route change is applied

A route mutation touches three systems that can each fail, so the order keeps the
database as the source of truth:

1. Validate the hostname and backend.
2. Serialise the mutation behind an `asyncio.Lock`, so concurrent requests
   cannot race the uniqueness check.
3. Create or update the Cloudflare record.
4. Push the route to mc-router.
5. Write to SQLite and demote any previous fallback route.
6. Compensate in reverse if a later step fails, then broadcast an SSE event.

SQLite transactions are kept short and are never held open across a network
call, which is what previously caused `database is locked` under load.

### Data model

| Table | Purpose |
|---|---|
| `routes` | Hostname to backend mappings, ownership, and the single fallback row |
| `users` | Accounts and PBKDF2 password hashes |
| `permissions` | Per-user permission grants, cascading on user deletion |
| `settings` | Cloudflare, Crafty and session configuration |
| `health_checks` | Latest health result per route |
| `health_history` | Rolling latency history, pruned past the retention window |

WAL journalling and foreign keys are enabled. `PRAGMA foreign_keys` is set per
connection, so `ON DELETE CASCADE` and `SET NULL` behave as declared.

### Security model

- Passwords are PBKDF2-HMAC-SHA256 with a random per-password salt.
- Sessions are signed cookies. A tampered cookie is rejected, and the account is
  re-validated against the database on every request, so deleted users and role
  changes take effect immediately.
- Every state-changing request must be same-origin, checked against the `Origin`
  or `Referer` header. This covers all mutating routes.
- A strict Content-Security-Policy is served with a per-response nonce. There
  are no inline event handlers or style attributes in the served markup, and no
  third-party origins.
- The fallback route requires the `manage_default_route` permission, which is
  not granted to new accounts. It receives all unmatched traffic, so it is
  guarded separately.
- The container runs as an unprivileged user with all capabilities dropped and
  `no-new-privileges` set.

See [SECURITY.md](./SECURITY.md) for reporting and deployment guidance.

## Configuration

Every setting is optional except `ADMIN_PASSWORD` on first start.

### Core

| Variable | Default | Description |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | Bootstrap admin username |
| `ADMIN_PASSWORD` | *required* | Bootstrap admin password, at least 12 characters |
| `SECRET_KEY` | *generated* | Session signing key. Generated and stored in the database when unset. |
| `DB_PATH` | `/data/mcrouter-ui.db` | SQLite database location |
| `MC_ROUTER_API` | `http://localhost:8080` | mc-router REST API to manage. Point elsewhere to manage an external router. |
| `MC_PORT` | `25565` | Minecraft ingress port |
| `API_PORT` | `8080` | mc-router REST API port |
| `LOG_LEVEL` | `INFO` | `CRITICAL`, `ERROR`, `WARNING`, `INFO` or `DEBUG` |
| `MIN_PASSWORD_LENGTH` | `12` | Minimum password length for accounts |

### Sessions

| Variable | Default | Description |
|---|---|---|
| `SESSION_HTTPS_ONLY` | `false` | Set `true` behind a TLS-terminating proxy. Also sends HSTS. |
| `SESSION_SAME_SITE` | `lax` | `SameSite` attribute on the session cookie |
| `SESSION_MAX_AGE` | `1209600` | Session lifetime in seconds (14 days) |

### Cloudflare DDNS

| Variable | Default | Description |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | | Token with `Zone:DNS:Edit` for the target zone |
| `CLOUDFLARE_ZONE_ID` | | Zone identifier. Use this or the zone name. |
| `CLOUDFLARE_ZONE_NAME` | | Zone or domain, for example `example.com` |
| `DDNS_INTERVAL_SECONDS` | `300` | How often the public IP is re-checked |

Records are created DNS-only (unproxied), because Minecraft needs a direct TCP
connection. An empty field saved in Settings falls back to the environment
rather than overriding it.

### Crafty Controller

| Variable | Default | Description |
|---|---|---|
| `CRAFTY_URL` | | Base URL, for example `https://192.168.1.50:8443`. Must be http or https with no embedded credentials. |
| `CRAFTY_API_KEY` | | Bearer token |
| `CRAFTY_CONTAINER_HOST` | | Hostname or IP used for TCP checks against Crafty servers |
| `CRAFTY_SERVERS_DIR` | | Path to Crafty's servers directory |
| `CRAFTY_INSECURE_SKIP_VERIFY` | `false` | Skip TLS verification. Only on a network you trust. |
| `SERVER_PROPERTIES_PATH` | | Exact `server.properties` path, overriding auto-detection |

Changing a server port rewrites `server.properties` atomically and re-points any
route using that backend. Mount the Crafty servers directory for this to work.

### Health checks and Docker

| Variable | Default | Description |
|---|---|---|
| `HEALTH_CHECK_INTERVAL` | `30` | Seconds between background checks |
| `HEALTH_HISTORY_RETENTION_HOURS` | `24` | How long latency history is kept |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Socket used for label discovery |

## Docker label discovery

Containers carrying an mc-router hostname label appear on the dashboard as
read-only routes:

```yaml
labels:
  mc-router.host: "play.example.com"
  # or the itzg variant
  mc-router.itzg.me/externalServerName: "play.example.com"
```

Discovery requires the socket to be mounted. The container is unprivileged, so
grant access with `group_add` using the socket's group id
(`stat -c '%g' /var/run/docker.sock`, commonly 999), or override the container
user to root.

## Permissions

Admins implicitly hold every permission. Regular accounts can be granted any
combination of:

| Permission | Allows |
|---|---|
| `see_own_routes` | View routes you own |
| `see_all_routes` | View every route, including Docker-discovered ones |
| `create_route` | Add routes |
| `edit_own_route` | Edit routes you own |
| `delete_own_route` | Delete routes you own |
| `manage_default_route` | Create, edit or delete the fallback route |
| `see_cloudflare` | View DNS records and zones |
| `manage_cloudflare` | Change Cloudflare settings and records |
| `see_servers` | View Crafty servers |
| `manage_servers` | Start, stop, restart and re-port Crafty servers |
| `see_all_users` | View the user list |
| `manage_users` | Create, edit and delete users and their permissions |
| `manage_settings` | Change Crafty settings |

## HTTP API

The dashboard uses these endpoints. Every mutating request must be same-origin.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/login` | Sign-in form, redirects to the dashboard when already signed in |
| `POST` | `/login` | Authenticate and issue a session cookie |
| `POST` | `/logout` | Clear the session |
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/readyz` | Readiness probe: database and mc-router |
| `GET` | `/api/events` | Server-sent event stream |
| `GET` | `/api/routes` | Permission-aware route list |
| `POST` | `/routes/add` | Add a route |
| `POST` | `/routes/edit/{id}` | Edit a route |
| `POST` | `/routes/delete/{id}` | Delete a route |
| `GET` | `/api/validate-route` | Live validation for the route form |
| `GET` | `/api/health/{id}` | On-demand health check |
| `GET` | `/api/health/{id}/history` | Latency history for the sparkline |
| `GET` | `/api/connections` | Active connections per hostname |
| `GET` | `/api/router-status` | mc-router reachability |
| `GET` | `/api/ports/used` | Ports in use by routes and Crafty servers |
| `GET` | `/api/cf/records`, `/api/cf/zones` | Cloudflare DNS records and zones |
| `POST` | `/api/cf/records` | Create an A record |
| `DELETE` | `/api/cf/records/{id}` | Delete a record |
| `GET` | `/api/crafty/servers` | Crafty servers with stats |
| `POST` | `/api/crafty/servers/{id}/action` | Start, stop or restart a server |
| `POST` | `/api/crafty/servers/{id}/port` | Change a server port |
| `GET` | `/api/users`, `/api/permissions/{id}` | Users and their permissions |
| `POST` | `/users/add`, `/users/edit/{id}`, `/users/delete/{id}` | User management |
| `POST` | `/api/permissions/{id}` | Set permissions |
| `POST` | `/settings/password`, `/settings/cloudflare`, `/settings/crafty`, `/settings/wizard` | Settings forms |

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/ruff check .      # lint
.venv/bin/pytest            # tests
.venv/bin/uvicorn app.main:app --reload
```

Running locally needs a writable database path:

```bash
DB_PATH=/tmp/mcrouter-ui.db ADMIN_PASSWORD=a-long-enough-password \
  .venv/bin/uvicorn app.main:app --reload
```

CI lints, tests, builds the image and smoke tests the container, then reports a
Trivy scan to the Security tab. Pushing to `master` publishes a new image to
[GHCR](https://github.com/Tamino089/mc-router-ui/pkgs/container/mc-router-ui)
tagged `latest` and by commit SHA.

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the request lifecycle,
middleware order, database columns and known limitations.

## Troubleshooting

**Routes never show as reachable.** The container cannot reach the backend. See
Networking above; a container name only resolves on a shared network.

**Cloudflare records are not created.** Check that the token has `Zone:DNS:Edit`
for the zone, and that either `CLOUDFLARE_ZONE_ID` or `CLOUDFLARE_ZONE_NAME` is
set. An empty field saved in Settings falls back to the environment.

**Crafty will not connect.** For a self-signed certificate set
`CRAFTY_INSECURE_SKIP_VERIFY=true` on a trusted network. `CRAFTY_URL` must not
include `/api/v2`; it is appended automatically. Certificate errors are reported
distinctly so they are not mistaken for a network problem.

**Port changes fail.** Mount the Crafty servers directory and set
`CRAFTY_CONTAINER_HOST` so routes can be re-pointed at the new port.

**Docker routes do not appear.** Mount the socket and grant the container its
group id. See Docker label discovery above.

## License

MIT. See [LICENSE](./LICENSE).
