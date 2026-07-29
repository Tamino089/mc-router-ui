# mc-router-ui

> A self-hosted web UI for [mc-router](https://github.com/itzg/mc-router) — the Minecraft reverse proxy — with Cloudflare DDNS, Crafty Controller integration, real-time health monitoring, and Docker label discovery.  
> Runs as a **single container** managed by supervisord.

---

## Features

| Category | Capabilities |
|---|---|
| **Route Management** | Add, edit, delete Minecraft server routes with full validation |
| **Default Route** | Set a fallback backend for unmatched hostnames |
| **Cloudflare DDNS** | Auto-sync DNS A-records when your public IP changes |
| **Crafty Controller** | List servers, start/stop/restart, change ports via API |
| **Docker Discovery** | Auto-discovers routes from container labels (read-only) |
| **Health Monitoring** | Background TCP health checks with latency history & sparklines |
| **Real-time Updates** | Server-Sent Events (SSE) — no polling needed |
| **Multi-user** | Role-based access (admin / user) with granular permissions |
| **Security** | PBKDF2-SHA256 passwords, CSRF protection, security headers |
| **Setup Wizard** | First-run wizard for Cloudflare & Crafty configuration |

---

## Quick Start

### Docker Compose (recommended)

```yaml
services:
  mc-router:
    image: itzg/mc-router
    ports:
      - "25565:25565"
    environment:
      API_BINDING: "0.0.0.0:8080"
    networks:
      - mc

  mc-router-ui:
    image: tamino089/mc-router-ui:latest   # or build from source
    ports:
      - "8000:8000"
    environment:
      MC_ROUTER_API: http://mc-router:8080
      ADMIN_USERNAME: admin
      ADMIN_PASSWORD: changeme            # Change this!
      # Cloudflare DDNS (optional)
      CLOUDFLARE_API_TOKEN: ""
      CLOUDFLARE_ZONE_ID: ""             # or CLOUDFLARE_ZONE_NAME
      # Crafty Controller (optional)
      CRAFTY_URL: ""
      CRAFTY_API_KEY: ""
    volumes:
      - mc-router-ui-data:/data
    depends_on:
      - mc-router
    networks:
      - mc

volumes:
  mc-router-ui-data:

networks:
  mc:
```

Then open [http://localhost:8000](http://localhost:8000) and log in with the credentials above.

### Build from Source

```bash
git clone https://github.com/Tamino089/mc-router-ui.git
cd mc-router-ui
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> **Note:** Requires a running [mc-router](https://github.com/itzg/mc-router) instance with the REST API enabled (`API_BINDING=0.0.0.0:8080`).

---

## Environment Variables

### Core

| Variable | Default | Description |
|---|---|---|
| `MC_ROUTER_API` | `http://localhost:8080` | mc-router REST API URL |
| `MC_PORT` | `25565` | Default Minecraft port |
| `ADMIN_USERNAME` | `admin` | Initial admin username |
| `ADMIN_PASSWORD` | `changeme` | Initial admin password — **change before deployment** |
| `SECRET_KEY` | *(auto-generated)* | Session signing key — generated & persisted to DB on first run |
| `DB_PATH` | `/data/mcrouter-ui.db` | SQLite database file path |

### Cloudflare DDNS (optional)

| Variable | Default | Description |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | `` | Cloudflare API token with DNS edit permissions |
| `CLOUDFLARE_ZONE_ID` | `` | Zone ID (use either this or `ZONE_NAME`) |
| `CLOUDFLARE_ZONE_NAME` | `` | Zone name, e.g. `example.com` |
| `DDNS_INTERVAL_SECONDS` | `300` | How often to check for IP changes (seconds) |

When configured, mc-router-ui will:
1. Detect your public IP via `api.ipify.org`
2. Create or update DNS A-records for all configured routes automatically
3. Re-sync all records when your IP changes

### Crafty Controller (optional)

| Variable | Default | Description |
|---|---|---|
| `CRAFTY_URL` | `` | Crafty web URL, e.g. `https://crafty:8443` |
| `CRAFTY_API_KEY` | `` | Crafty API key |
| `CRAFTY_SERVERS_DIR` | `` | Override path to Crafty's servers directory |
| `CRAFTY_INSECURE_SKIP_VERIFY` | `false` | Set to `1` or `true` to skip TLS verification (for self-signed certs) |

### Docker Integration (optional)

| Variable | Default | Description |
|---|---|---|
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Path to the Docker socket |

Mount the socket to enable auto-discovery:

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

### Health Checks

| Variable | Default | Description |
|---|---|---|
| `HEALTH_CHECK_INTERVAL` | `30` | Seconds between background health checks |
| `HEALTH_HISTORY_RETENTION_HOURS` | `24` | How long to keep health history |

---

## Docker Label Discovery

mc-router-ui reads the following container labels and surfaces them as read-only routes on the dashboard:

```yaml
# Standard mc-router label
labels:
  mc-router.host: "play.example.com"

# Alternative itzg label
labels:
  mc-router.itzg.me/externalServerName: "play.example.com"
```

Docker-managed routes appear on the dashboard but **cannot be edited or deleted** from the UI — they are controlled entirely by the container labels.

---

## User Permissions

Admins have full access. Regular users can be assigned any combination of:

| Permission | Description |
|---|---|
| `see_own_routes` | View routes they own |
| `see_all_routes` | View all routes |
| `create_route` | Add new routes |
| `edit_own_route` | Edit routes they own |
| `delete_own_route` | Delete routes they own |
| `see_cloudflare` | View Cloudflare DNS records |
| `manage_cloudflare` | Edit Cloudflare settings |
| `see_servers` | View Crafty servers |
| `manage_servers` | Control Crafty servers (start/stop/restart/port) |
| `see_all_users` | View user list |
| `manage_users` | Create/edit/delete users and permissions |
| `manage_settings` | Edit Crafty settings |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/readyz` | Readiness probe (DB + mc-router) |
| `GET` | `/api/events` | SSE stream (health, connections, route changes) |
| `GET` | `/api/connections` | Active connections per hostname |
| `GET` | `/api/router-status` | mc-router reachability check |
| `GET` | `/api/health/{id}` | On-demand health check for a route |
| `GET` | `/api/health/{id}/history` | Last 60 health records for sparkline |
| `POST` | `/routes/add` | Add a route |
| `POST` | `/routes/edit/{id}` | Edit a route |
| `POST` | `/routes/delete/{id}` | Delete a route |
| `GET` | `/api/users` | List users |
| `POST` | `/users/add` | Create user |
| `POST` | `/users/edit/{id}` | Edit user |
| `POST` | `/users/delete/{id}` | Delete user |
| `GET/POST` | `/api/permissions/{id}` | Get/set user permissions |
| `GET` | `/api/crafty/servers` | List Crafty servers with stats |
| `POST` | `/api/crafty/servers/{id}/action` | Start/stop/restart server |
| `POST` | `/api/crafty/servers/{id}/port` | Change server port |
| `GET` | `/api/cf/records` | List Cloudflare A-records |
| `POST` | `/api/cf/records` | Create Cloudflare A-record |
| `DELETE` | `/api/cf/records/{record_id}` | Delete Cloudflare record |
| `GET` | `/api/validate-route` | Live validation of hostname + backend |
| `GET` | `/api/ports/used` | List ports in use |

---

## Unraid Setup

See [UNRAID_SETUP.md](./UNRAID_SETUP.md) for the full Unraid Community Applications template walkthrough and the XML template files in the repository root.

---

## Architecture

```
supervisord
├── uvicorn (FastAPI app — port 8000)
│   ├── /app/main.py          ← Application entry, lifespan, dashboard
│   ├── /app/core/            ← Config, CSRF middleware, security helpers
│   ├── /app/db/              ← SQLite schema, migrations, connection manager
│   ├── /app/routes/          ← All HTTP handlers (auth, routes, users, etc.)
│   └── /app/services/        ← Business logic (Cloudflare, Crafty, health, SSE)
└── mc-router (Minecraft reverse proxy — port 25565, API 8080)
```

Data is persisted in a single SQLite file at `/data/mcrouter-ui.db`.  
Static assets are served from `/app/static/`. Templates use Jinja2.

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for a deep-dive.

---

## Troubleshooting

**App won't start / `SyntaxError` in crafty.py**  
Ensure you're running from a clean checkout without merge conflict markers.

**mc-router routes not syncing on startup**  
Check that `MC_ROUTER_API` points to a reachable mc-router instance with `API_BINDING` set.

**Cloudflare DNS not updating**  
- Verify `CLOUDFLARE_API_TOKEN` has `Zone:DNS:Edit` permissions
- Provide either `CLOUDFLARE_ZONE_ID` or `CLOUDFLARE_ZONE_NAME`
- Check logs for `DDNS loop` entries

**Crafty integration failing**  
- For self-signed certs set `CRAFTY_INSECURE_SKIP_VERIFY=1`
- Ensure `CRAFTY_URL` does not include `/api/v2` (it's appended automatically)
- Mount the Crafty servers directory to `/crafty/servers` for port changes to work

**Docker routes not appearing**  
- Mount `/var/run/docker.sock:/var/run/docker.sock:ro`
- Add `mc-router.host` label to your Minecraft server containers

---

## License

MIT
