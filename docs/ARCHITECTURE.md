# MC Router UI - Architecture

MC Router UI is a self-hosted FastAPI application that drives
[mc-router](https://github.com/itzg/mc-router) for Minecraft hostname routing,
with optional Cloudflare DDNS, Crafty Controller management, and Docker label
discovery. It ships as one container and keeps its state in one SQLite file.

This document covers the runtime topology, the code layers, the request and
event paths, the data model, and the configuration surface. The code in `app/`
is the source of truth; when this document and the code disagree, trust the code.

## Runtime topology

- The image is built in two stages: `itzg/mc-router:latest` contributes the
  static `mc-router` Go binary, and `python:3.12-slim` is the final base.
- PID 1 is `tini`, which execs `supervisord -c /etc/supervisor/conf.d/mcrouter.conf`.
  Supervisord runs two child programs and restarts either if it exits.
- `mc-router` (priority 10) runs
  `/usr/local/bin/mc-router --api-binding=0.0.0.0:$API_PORT --port=$MC_PORT --routes-config=/tmp/mc-routes.json`.
  It terminates Minecraft handshakes on `MC_PORT` (25565) and exposes a REST
  control API on `API_PORT` (8080). The UI manages routes only through that API;
  it never reads or writes `/tmp/mc-routes.json`.
- `web-ui` (priority 20) runs `uvicorn app.main:app --host 0.0.0.0 --port 8000`
  from `/srv`, with a single worker. That single process is what makes the
  in-process mutation lock, rate limiters, SSE subscriber map, and Docker cache
  coherent.
- The UI reaches mc-router over loopback HTTP at `MC_ROUTER_API`
  (default `http://localhost:8080`). Ports 25565 (Minecraft ingress) and 8000
  (web UI) are meant to be reachable; 8080 is internal.
- Volumes: `/data` holds the database; `/var/run/docker.sock` is mounted
  read-only for route discovery; the Crafty servers directory is mounted when
  `server.properties` editing is wanted.
- `docker-compose.yml` defaults to `network_mode: host`. On a plain bridge
  network, Docker's embedded DNS does not resolve sibling containers by name, so
  a backend such as `crafty:25565` never probes healthy unless the containers
  share a user-defined network or routable addresses are used.
- Logs go to stdout/stderr, captured by supervisord under `/var/log/supervisor/`.
  The application log format is `%(asctime)s - %(name)s - %(levelname)s - %(message)s`
  at the level returned by `config.log_level()`.

| Probe | Checks | Response |
|---|---|---|
| `GET /healthz` | Process is alive and serving | Always `200 {"status":"ok"}` |
| `GET /readyz` | `SELECT 1` on SQLite and `GET /routes` on mc-router | `200 {"status":"ok","checks":{...}}` or `503 {"status":"degraded","checks":{...}}` |

Both are unauthenticated and CSRF-exempt. The Docker `HEALTHCHECK` uses
`/healthz`; `/readyz` is the gate for an orchestrator that must not send traffic
before mc-router answers. `/readyz` reports only `ok` or `error` per check and
logs details instead of returning internal error text.

## Application layering

Dependencies point one way: routes use services and core, services use core and
the database, and core uses nothing inside the app.

| Layer | Modules | Responsibility |
|---|---|---|
| Transport | `app/main.py`, `app/routes/*` | App wiring, middleware, HTTP handlers, response envelopes, permission gates |
| Application services | `app/services/*` | External API clients, TCP probes, SSE fan-out, background loops, route payload assembly |
| Persistence | `app/db/*` | SQLite connections and PRAGMAs, DDL, migrations, bootstrap, permission lookups |
| Core | `app/core/*` | Environment config, password hashing and session identity, CSRF and security headers, shared validation, rate limiting |
| Presentation | `app/templates/*`, `app/static/*` | Jinja2 markup, CSS, dashboard JavaScript, favicon |

### File and endpoint reference

"Gate" is the permission a non-admin needs; admins bypass every permission check.

| File | Responsibility | Endpoints and gate |
|---|---|---|
| `app/main.py` | App construction, lifespan, middleware registration, exception handlers, dashboard renderer | `GET /` (session) |
| `app/core/config.py` | Environment to constants, `ALL_PERMISSIONS`, `DEFAULT_USER_PERMISSIONS`, `log_level()` | - |
| `app/core/security.py` | PBKDF2-SHA256 hashing and verification, `current_user()` | - |
| `app/core/csrf.py` | `SameOriginCsrfMiddleware`, `SecurityHeadersMiddleware`, CSP template | - |
| `app/core/validation.py` | Hostname, backend, and base-URL patterns; backend normalisation and parsing | - |
| `app/core/ratelimit.py` | `RateLimiter`, an asyncio-guarded sliding window | - |
| `app/db/database.py` | `get_db()` context manager and per-connection PRAGMAs | - |
| `app/db/schema.py` | `init_db()`, migrations, admin bootstrap, permission helpers | - |
| `app/routes/__init__.py` | `get_form_or_json()`, session flash helpers | - |
| `app/routes/auth.py` | Login form, login, logout, per-IP login limiter | `GET /login`, `POST /login`, `POST /logout` (public) |
| `app/routes/routes.py` | Route CRUD under the mutation lock, permission-aware route list | `POST /routes/add` (`create_route`), `POST /routes/edit/{id}` (`edit_own_route` + ownership), `POST /routes/delete/{id}` (`delete_own_route` + ownership), `GET /api/routes` (session) |
| `app/routes/users.py` | User CRUD, permission grants | `GET /api/users` (`see_all_users`); `POST /users/add`, `POST /users/edit/{id}`, `POST /users/delete/{id}`, `GET`/`POST /api/permissions/{id}` (`manage_users`) |
| `app/routes/settings.py` | Password change, Cloudflare and Crafty settings, setup wizard | `POST /settings/password` (session), `POST /settings/cloudflare` (`manage_cloudflare`), `POST /settings/crafty` (`manage_settings`), `POST /settings/wizard` (admin) |
| `app/routes/monitoring.py` | On-demand health checks, history, router status, connections, used ports | `GET /api/health/{id}` and `/history` (route visibility), `GET /api/router-status`, `GET /api/connections`, `GET /api/ports/used` (session, filtered) |
| `app/routes/cloudflare_api.py` | Cloudflare records and zones, live form validation | `GET /api/cf/records`, `GET /api/cf/zones` (`see_cloudflare`); `POST`/`DELETE /api/cf/records` (`manage_cloudflare`); `GET /api/validate-route` (session + `create_route` or `edit_own_route`) |
| `app/routes/crafty_api.py` | Crafty server listing, power actions, port changes | `GET /api/crafty/servers` (`see_servers`); `POST /api/crafty/servers/{id}/action`, `POST /api/crafty/servers/{id}/port` (`manage_servers`) |
| `app/routes/events.py` | Server-Sent Events stream | `GET /api/events` (session) |
| `app/routes/healthz.py` | Liveness and readiness | `GET /healthz`, `GET /readyz` (public, CSRF-exempt) |
| `app/services/mc_router.py` | Async mc-router REST client, startup route sync | - |
| `app/services/health.py` | `tcp_check()`, `check_all_routes()`, `prune_old_history()`, `health_loop()` | - |
| `app/services/cloudflare.py` | Cloudflare client, zone caching, domain validation, DDNS loop | - |
| `app/services/crafty.py` | Crafty client and `server.properties` reading and rewriting | - |
| `app/services/docker_watcher.py` | Docker label discovery, route cache, refresh loop | - |
| `app/services/sse.py` | Subscriber registry, snapshot, `broadcast()`, emitter loop | - |
| `app/services/routes_data.py` | Builds the permission-aware route list for the dashboard | - |
| `app/templates/*`, `app/static/*` | Markup, CSS, client JavaScript, favicon; served under `/static` | - |

Conventions for new endpoints:

- Most JSON endpoints return `{"success": true, ...}` or
  `{"success": false, "error": "..."}` with a matching status. Two exceptions
  predate the convention: `GET /api/users` returns a bare array and
  `GET /api/connections` a bare hostname-to-count object.
- Bodies are read with `get_form_or_json()`, so handlers accept JSON and
  form-encoded input and treat a malformed body as an empty dict.
- Status codes: `400` validation, `401` no session, `403` missing permission or
  an immutable Docker-managed route, `404` missing record, `409` duplicate name
  or Docker-managed hostname, `502` upstream failure, `500` unexpected failure.
- `HTTPException` keeps its status and becomes
  `{"success": false, "error": detail}`. The catch-all
  `@app.exception_handler(Exception)` logs a stack trace and returns
  `500 {"error":"internal","detail":null,"path":...}`.

## Request lifecycle: a route mutation

`POST /routes/add` exercises every layer. Edit and delete follow the same shape.

1. Middleware runs outer to inner: security headers, session load, then CSRF
   verification of the unsafe method.
2. `current_user(request)` returning `None` produces
   `401 {"success": false, "error": "Not authenticated"}`.
3. `user_has_perm(user, "create_route")` is checked; failure returns `403`.
4. The body is parsed with `get_form_or_json()`. `hostname` is trimmed and
   lowercased; `is_default` goes through `_as_bool()`, which accepts JSON
   booleans and checkbox values (`1`, `true`, `on`, `yes`).
5. For a default route, `_can_manage_default(user)` must hold (admin or
   `manage_default_route`), otherwise `403`.
6. `_prepare_backend()` requires the backend, matches `is_valid_backend()`,
   appends `:25565` when no port is given, and range-checks the port. Default
   routes are validated too, because an unchecked fallback would redirect all
   unmatched traffic to an arbitrary host.
7. `_resolve_hostname()` produces the routing key: `__default__` (a sentinel,
   not a DNS name) for the fallback, otherwise an FQDN matching `HOSTNAME_RE` or
   a bare label resolved against the configured Cloudflare zone. For non-default
   routes, domain validation follows and a hostname owned by a Docker label is
   rejected with `409`.
8. Steps 4 to 7 touch no database and hold no lock, so a slow Cloudflare or
   Docker lookup delays only the requesting client.
9. `async with _mutation_lock:` opens the critical section. The lock is a
   module-level `asyncio.Lock` in `app/routes/routes.py` that serialises every
   route mutation in the process.
10. A short read connection checks for a duplicate hostname (`409`) and records
    the current default row, then closes. No transaction is left open.
11. Outside any transaction, Cloudflare DNS is created or updated for
    non-default routes, then mc-router is pushed (`push_default()` or
    `push_route()`). A failed push deletes any DNS record created in this
    request and returns `500 {"success": false, "error": "mc-router sync failed: ..."}`.
12. A short write connection demotes any existing `is_default=1` row when
    needed and inserts the new row with `source='static'` and `owner_id` set to
    the acting user. If the write raises, compensation runs in reverse: the new
    DNS record is deleted, and mc-router is restored (previous default backend
    pushed back, or the new hostname route deleted).
13. After the lock is released, an immediate health check is scheduled as a
    fire-and-forget task and `broadcast("route-change", ...)` notifies connected
    dashboards. The response is `200 {"success": true, "message": ...}` with a
    short DNS status suffix.

The ordering is deliberate. SQLite write transactions are kept short and are
never held across an HTTP call to Cloudflare or mc-router, because holding one
would block every other writer for the duration of a network round trip;
mutual exclusion comes from the `asyncio.Lock` instead. The cost is that
mc-router is updated before the database, which is what the compensation steps
above exist to contain. The database stays authoritative across restarts,
because the lifespan sync pushes stored routes back to mc-router.

Edit and delete differ in the details:

- Edit re-reads the row under the lock, refuses Docker-managed rows (`403`),
  re-checks ownership and `manage_default_route`, pushes the new state, deletes
  the old mc-router hostname when the hostname changed, updates the row, and
  finally deletes the old DNS record. A failed router push returns
  `502 {"success": false, "status": "PARTIAL_FAILURE", "errors": [...]}`.
- Delete removes the row first, which cascades to `health_checks` and
  `health_history`, then best-effort deletes the mc-router route and the DNS
  record. mc-router has no DELETE for the fallback, so `clear_default()` posts an
  empty backend instead; when that fails the row is still gone from the database
  and the response warns that the fallback may stay active until mc-router
  restarts.

## Authentication and authorisation

### Login and sessions

- `GET /login` renders the form and redirects to `/` when already
  authenticated. `POST /login` takes form fields `username` and `password`,
  looks the user up case-insensitively, and verifies the password in a worker
  thread (`asyncio.to_thread`) because PBKDF2 is deliberately CPU-heavy.
- On success the session is cleared before the identity is written, so a
  pre-login session id cannot be reused after authentication, and the response
  is `303` to `/`. Failure re-renders the form with `401`, or `429` when the
  limiter trips. `POST /logout` clears the session and redirects to `/login`;
  there is no logout-by-GET route.
- Passwords are stored as `pbkdf2_sha256$100000$<base64 salt>$<base64 hash>`
  with a fresh 16-byte salt each time and `secrets.compare_digest` for
  comparison. `verify_password()` never raises; a malformed stored hash simply
  fails.
- The session is a signed cookie (`mc_router_ui_session`) managed by Starlette's
  `SessionMiddleware`, signed with the key resolved at startup.
  `SESSION_MAX_AGE`, `SESSION_SAME_SITE`, and `SESSION_HTTPS_ONLY` control it.
- `current_user()` treats the cookie as proof of continuity only: every request
  re-reads `id`, `username`, and `role` from `users`. A deleted account is
  logged out, and a rename or role change takes effect on the next request
  without a re-login.
- Login is limited to 5 attempts per 60 seconds per client IP, password changes
  to 5 per 60 seconds per user id, and `GET /api/validate-route` to 30 per 60
  seconds per user id because it performs a caller-supplied TCP probe.

### Permission model

Admins bypass every check: `user_has_perm()` returns `True` for
`role == "admin"`, and handlers that inline the check do the same. When an
account is promoted to admin, its stored permission rows are deleted because
they would be unused.

| Permission | Default for new users | Gates |
|---|---|---|
| `see_own_routes` | yes | Own route rows in the dashboard payload, route-scoped health endpoints, `/api/connections`, `/api/ports/used`, SSE subscription filter |
| `see_all_routes` | no | Unfiltered route list, connection map, port list, SSE stream, Docker-discovered routes |
| `create_route` | yes | `POST /routes/add`; `GET /api/validate-route` for a new route |
| `edit_own_route` | yes | `POST /routes/edit/{id}` for owned routes; `GET /api/validate-route` with a `route_id` |
| `delete_own_route` | yes | `POST /routes/delete/{id}` for owned routes |
| `manage_default_route` | no | Adding, editing, or deleting the fallback route |
| `see_cloudflare` | no | `GET /api/cf/records`, `GET /api/cf/zones` |
| `manage_cloudflare` | no | `POST`/`DELETE /api/cf/records`, `POST /settings/cloudflare` |
| `see_servers` | no | `GET /api/crafty/servers`, Crafty ports in `/api/ports/used` |
| `manage_servers` | no | Crafty power actions and port changes |
| `see_all_users` | no | `GET /api/users` |
| `manage_users` | no | User CRUD and permission grants |
| `manage_settings` | no | `POST /settings/crafty`; also unmask the Crafty token in the dashboard payload |

`DEFAULT_USER_PERMISSIONS` is exactly the four route-ownership permissions
above. `manage_default_route` is deliberately excluded: the fallback route
receives every unmatched Minecraft connection, so re-pointing it requires an
explicit grant or admin rights.

Ownership is enforced in addition to the permission: a non-admin may edit or
delete a route only when `owner_id` equals their user id and they hold
`edit_own_route` or `delete_own_route`. `source='docker'` routes are refused with
`403` by add, edit, delete, and validation, since they are derived from container
labels rather than stored.

Secrets are permission-scoped in the dashboard payload: the raw Cloudflare token
goes only to admins and holders of `manage_cloudflare`, and the raw Crafty token
only to admins and holders of `manage_settings`; everyone else sees a fixed
12-character mask. User creation and password changes enforce
`MIN_PASSWORD_LENGTH` (default 12) and a 1024-character maximum; the bootstrap
`ADMIN_PASSWORD` is not length-checked, but the literal default `changeme` raises
a critical log line, sets an `admin_password_is_default` marker, and shows a
dashboard banner until it is changed. The last admin cannot be deleted or
demoted.

## Middleware and security headers

Starlette makes the last middleware passed to `add_middleware()` the outermost.
Registration order in `app/main.py` is CSRF, session, security headers, which
produces this execution order:

| Order | Middleware | Behaviour |
|---|---|---|
| 1 (outermost) | `SecurityHeadersMiddleware` | Generates a CSP nonce per request, stores it on `request.state.csp_nonce`, stamps all security headers on the way out |
| 2 | `SessionMiddleware` | Loads and re-signs the `mc_router_ui_session` cookie; `request.session` holds `{"id", "username", "role"}` plus one-shot flash messages |
| 3 (innermost) | `SameOriginCsrfMiddleware` | For `POST`, `PUT`, `PATCH`, `DELETE`, compares `Origin` (falling back to `Referer`) with `Host`; a mismatch returns `403 {"error":"csrf_failed"}` |
| 4 | Router and handler | FastAPI dispatch, then `ExceptionMiddleware` for `HTTPException` |
| 5 (outside the stack above) | Starlette `ServerErrorMiddleware` | Produces the `500` from `@app.exception_handler(Exception)` |

CSRF details: `GET`, `HEAD`, `OPTIONS`, `TRACE`, `/healthz`, and `/readyz` are
exempt; a missing source header is a rejection rather than a pass; and the
comparison is exact string equality between the source netloc and `Host`,
including the port. A reverse proxy that rewrites `Host` while the browser sends
the public origin will produce `403 csrf_failed` unless the proxy preserves the
public host and port.

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'nonce-{nonce}'; img-src 'self' data:; font-src 'self'; connect-src 'self'` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=()` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains`, only when `SESSION_HTTPS_ONLY` is enabled |

The nonce is 16 random URL-safe bytes from `secrets.token_urlsafe(16)`,
regenerated per response. Scripts and styles are same-origin plus that nonce
only, with no `unsafe-inline` for either. The consequences are load-bearing:

- Templates contain no inline event handlers (`onclick` and friends), no `style`
  attributes, and no `<style>` blocks. CSS lives in `app/static/css/`;
  `main.css` and `login.css` are the only stylesheets.
- Markup declares intent with `data-action` attributes. `dashboard.js` installs
  a single delegated dispatcher (`bindActionDispatcher()`) on `document` for
  `click`, `change`, `input`, and `submit`, looks the action up in an `ACTIONS`
  table, calls the matching function, and prevents the default navigation or
  submit for anchors and forms. Keyboard activation of `role="checkbox"`
  controls goes through the same path.
- The only inline scripts are the theme bootstrap and the
  `window.MC_UI_CONFIG` payload in `index.html` and `login.html`, both tagged
  `nonce="{{ csp_nonce }}"`. `MC_UI_CONFIG` carries `isAdmin`, `userId`,
  `userPerms`, `allPerms`, `cfEnabled`, and `dockerEnabled`.
- `dashboard.js` is a same-origin file, so it needs no nonce.

## Database schema and migrations

### Connections

All database access goes through `get_db()` in `app/db/database.py`, a context
manager that opens a `sqlite3.Connection` with `row_factory = Row` and
`timeout=5`, applies PRAGMAs, yields it, and always closes it.

- `PRAGMA foreign_keys=ON` is set on every connection. Foreign-key enforcement
  is a per-connection setting, not a file-level one: enabling it only in
  `init_db()` would leave it off for the connections that serve requests, and
  `ON DELETE CASCADE` would silently never fire.
- `PRAGMA busy_timeout=5000` waits up to five seconds for a competing writer
  instead of failing immediately.
- `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` are set once in
  `init_db()`. Journal mode persists in the database file, which is why they are
  not repeated per connection.
- Handlers call `get_db()` for short, self-contained units of work and never
  hold a connection across an awaited network call. This is blocking `sqlite3`
  used from async handlers, so a statement occupies the event loop while it
  runs; keeping statements short and connections brief is the mitigation.

Delete behaviour that follows: deleting a route cascades to its `health_checks`
and `health_history` rows; deleting a user cascades to its `permissions` rows and
sets `routes.owner_id` to `NULL`. The user-delete handler also sets `owner_id`
to `NULL` explicitly before the delete.

### Table reference

| Table | Column | Type and constraints |
|---|---|---|
| `routes` | `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| | `hostname` | `TEXT UNIQUE NOT NULL`; `__default__` for the fallback route |
| | `backend` | `TEXT NOT NULL`; normalized to `host:port` |
| | `is_default` | `INTEGER NOT NULL DEFAULT 0`; at most one row at `1`, enforced by application logic rather than a constraint |
| | `source` | `TEXT NOT NULL DEFAULT 'static'`; `docker` appears only in dashboard payloads for discovered routes, which are not stored |
| | `owner_id` | `INTEGER REFERENCES users(id) ON DELETE SET NULL` |
| | `created_at` | `TEXT NOT NULL DEFAULT (datetime('now'))`, UTC |
| `users` | `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| | `username` | `TEXT UNIQUE NOT NULL`; compared case-insensitively on login |
| | `password_hash` | `TEXT NOT NULL`; `pbkdf2_sha256$<iterations>$<salt>$<hash>` |
| | `role` | `TEXT NOT NULL CHECK(role IN ('admin', 'user'))` |
| | `created_at` | `TEXT NOT NULL DEFAULT (datetime('now'))` |
| `permissions` | `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| | `user_id` | `INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE` |
| | `permission` | `TEXT NOT NULL`; one of `ALL_PERMISSIONS`, with `UNIQUE(user_id, permission)` |
| `settings` | `key` | `TEXT PRIMARY KEY` |
| | `value` | `TEXT NOT NULL` |
| `health_checks` | `route_id` | `INTEGER PRIMARY KEY REFERENCES routes(id) ON DELETE CASCADE`; one current row per route |
| | `healthy` | `INTEGER NOT NULL DEFAULT 0` |
| | `latency_ms` | `REAL`, `NULL` when unhealthy |
| | `checked_at` | `TEXT NOT NULL DEFAULT (datetime('now'))` |
| | `error` | `TEXT`, failure reason from the last probe |
| `health_history` | `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| | `route_id` | `INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE` |
| | `healthy`, `latency_ms`, `checked_at` | As in `health_checks` |

`health_history` carries the index `idx_health_history_route_time` on
`(route_id, checked_at DESC)`. The history endpoint reads the newest 60 rows by
`id DESC`, not by `checked_at`, because timestamps have one-second resolution and
rows written in the same second would otherwise come back in arbitrary order.

### Migrations and bootstrap

`init_db()` in `app/db/schema.py` runs at import time, when `app.main` is
imported, and returns the session secret key. In order it:

1. Creates the parent directory of `DB_PATH` and opens a connection with WAL,
   foreign keys, and `synchronous=NORMAL`.
2. Detects a pre-`hostname` schema (`SELECT hostname FROM routes`). A non-fresh
   database lacking that column is too old to migrate, so child tables are
   dropped first with foreign keys temporarily off and all tables are recreated.
   Databases that do have `hostname` are upgraded in place.
3. Runs `CREATE TABLE IF NOT EXISTS` for every table plus the history index.
4. Adds `owner_id` to `routes` when the column is missing, for databases created
   before route ownership existed.
5. Resolves the session key: `SECRET_KEY` from the environment wins and is also
   written into `settings`; otherwise the stored `secret_key` is reused;
   otherwise a new `secrets.token_hex(32)` value is generated and persisted.
   `config.SECRET_KEY` is then set in memory for `SessionMiddleware`. If this
   fails, `app/main.py` logs a critical error and refuses to start, because
   falling back to a fixed key would let anyone forge sessions.
6. Bootstraps the admin account when no user matches `ADMIN_USERNAME`
   case-insensitively, migrating and then deleting a legacy plaintext
   `admin_password` setting if present. `ADMIN_PASSWORD` is never reapplied to an
   existing account, so a password changed in the UI survives restarts.
7. Backfills `owner_id` on ownerless routes to the bootstrap admin.
8. Applies `CRAFTY_URL`, `CRAFTY_API_KEY`, and `CRAFTY_CONTAINER_HOST` from the
   environment over stored settings when the environment value is non-empty.

The `settings` table is a key-value store with these keys:

| Key | Meaning |
|---|---|
| `secret_key` | Session signing key, generated on first boot unless supplied |
| `cf_api_token`, `cf_zone_id`, `cf_zone_name` | Cloudflare credentials and zone, from the settings tab or wizard |
| `crafty_url`, `crafty_token`, `crafty_container_host` | Crafty endpoint, API token, and probe host |
| `last_public_ip` | Last public IP seen by the DDNS loop |
| `setup_wizard_done` | Set once the first-run wizard is completed or skipped |
| `admin_password_is_default` | Marker present while the bootstrap password is unchanged |
| `admin_password` | Legacy plaintext value, read once for migration and then deleted |

## Background workers

Four asyncio tasks are created in the FastAPI `lifespan` context manager and
cancelled, then awaited with `return_exceptions=True`, on shutdown. Each loop
catches `asyncio.CancelledError` to break cleanly and every other exception to
log and continue.

| Task name | Function | Interval | Responsibility |
|---|---|---|---|
| `health` | `health.health_loop()` | `HEALTH_CHECK_INTERVAL` (30 s) plus linear backoff | Probe every route backend over TCP, upsert `health_checks`, append `health_history`, then prune expired history |
| `ddns` | `cloudflare.ddns_loop()` | `DDNS_INTERVAL_SECONDS` (300 s) plus linear backoff | Read the public IP, and when it changed, upsert an A record for every non-default hostname and store `last_public_ip` |
| `sse-emitter` | `sse.sse_emitter_loop()` | 10 s plus linear backoff capped at 300 s | Poll `/connections` and `/routes` on mc-router, broadcast `connections` and `router-status` to SSE subscribers |
| `docker-watcher` | `docker_watcher.docker_watcher_loop()` | 10 s, no backoff | Force-refresh the Docker label discovery cache |

- Health probes run through `asyncio.to_thread` behind a semaphore capped at 32
  concurrent checks, so blocking `socket.create_connection` calls never stall
  the event loop and a large route table cannot exhaust the executor.
  `tcp_check()` distinguishes DNS failure, timeout, connection refused, and
  generic OS errors, because each points at a different fix. Latency is recorded
  only for successful probes.
- Pruning runs in its own try block after the check pass, so a failing pass
  cannot let history grow without bound. Retention is
  `HEALTH_HISTORY_RETENTION_HOURS` (24) measured against UTC `datetime('now')`
  timestamps.
- `ddns_loop` reads the route list on one connection, closes it, performs the
  Cloudflare awaits, and only then writes `last_public_ip` on a fresh
  connection, so no read snapshot is held across network calls.
- Backoff is linear in consecutive failures: the health loop sleeps
  `interval + min(errors * interval, 600)`, the DDNS loop
  `interval + min(errors * interval, 3600)`, and the SSE emitter
  `10 + min(errors * 10, 300)`.
- Two jobs are one-shot rather than loops. `mc_router.sync_routes_to_router()`
  runs in `lifespan` before the tasks start and pushes every stored route with
  single-attempt requests, so an unreachable router cannot stall startup.
  `_schedule_health_check()` in `app/routes/routes.py` fires an immediate probe
  after a route is created or edited without delaying the HTTP response, holding
  a strong reference in a module-level set so the task is not garbage collected.

## Server-Sent Events

`GET /api/events` is an authenticated SSE stream powering live connection
counts, router status, and route-table refreshes in the dashboard.

- The handler requires a session (`401` otherwise) and computes visibility once,
  at connect time: `None` (unrestricted) for admins and holders of
  `see_all_routes`, an empty set for users without `see_own_routes`, or the set
  of hostnames they own.
- `subscribe()` returns an `asyncio.Queue(maxsize=100)` and records it in the
  module-level `_subscribers` map alongside that visibility set.
- On connect the client gets `event: connected`, then a snapshot of the last
  connection map (filtered to its hostnames) and the last router status, so a
  page load does not wait for the next poll. The generator then yields queued
  messages, or a `: keepalive` comment every 30 seconds, and unsubscribes when
  the client disconnects or the generator closes.
- `broadcast()` iterates a copy of the subscriber map and, for `connections`
  events, applies `_visible()` to the payload per subscriber, so a restricted
  user only ever receives hostnames that user may see. A subscriber whose queue
  is full is dropped with a warning rather than blocking the emitter; its
  browser reconnects and receives a fresh snapshot.
- Repeated `connections` and `router-status` payloads are suppressed, so only a
  change is broadcast. `route-change` events are always delivered to
  subscribers allowed to see the affected hostname; events without a hostname
  are refresh triggers and reach everyone.
- The response is `text/event-stream` with `Cache-Control: no-cache`,
  `Connection: keep-alive`, and `X-Accel-Buffering: no`, so a reverse proxy does
  not buffer the stream.

| Event | Payload | Trigger |
|---|---|---|
| `connected` | `{}` | Stream opened |
| `connections` | `{"hostname": count, ...}`, filtered per subscriber | Emitter poll, or a change since the last broadcast |
| `router-status` | `{"online": bool, "error": str or null}` | Emitter poll, or a change since the last broadcast |
| `route-change` | `{"action": "add", "route_id": int, "hostname": str}` with action `add`, `edit`, or `delete`; or `{"action": "edit", "reason": "crafty_port_change"}` | Route mutations and Crafty port changes |

In the browser, `dashboard.js` opens one `EventSource('/api/events')`, updates
connection counts by hostname, shows a banner when mc-router is unreachable or
the stream drops, and reloads the route table on `route-change` except for the
Crafty port-change notification, which the local flow already handles.

## External integrations

### mc-router REST API

`app/services/mc_router.py` wraps the router API with `httpx.AsyncClient`, a
10-second timeout, `Connection: close` to avoid keep-alive resets from the Go
server, and up to three attempts with 0.5 s, 1 s, then 1.5 s delays for
transport errors and `5xx` responses.

| Call | Endpoint | Used for |
|---|---|---|
| `push_route(hostname, backend)` | `POST /routes` with `{"serverAddress", "backend"}` | Create or update a hostname route |
| `delete_route(hostname)` | `DELETE /routes/{hostname}` | Remove a hostname route; `404` counts as success |
| `push_default(backend)` | `POST /defaultRoute` with `{"backend"}` | Set the fallback backend |
| `clear_default()` | `POST /defaultRoute` with `{"backend": ""}` | Best-effort clear, since mc-router has no DELETE for the fallback |
| `get_connections()` | `GET /connections` | Live connection counts; `{}` on any error |
| `router_request("get", "/routes")` | `GET /routes` | Reachability for `/readyz` and `/api/router-status` |
| `sync_routes_to_router(db)` | The push calls above | One-shot startup sync of every stored route |

### Cloudflare

`app/services/cloudflare.py` calls `https://api.cloudflare.com/client/v4` with a
bearer token, a 10-second timeout, and no retries. A response whose `success`
field is false is reported as an error rather than parsed further.

| Call | Endpoint | Used for |
|---|---|---|
| `cf_get_zone_id()` | `GET /zones?name=<zone>` when only a name is configured | Resolve and cache the zone id |
| `cf_find_record()` | `GET /zones/{id}/dns_records?type=A&name=<host>` | Look up an existing A record |
| `cf_upsert_a_record()` | `POST /zones/{id}/dns_records`, or `PUT .../{record_id}` when one exists | Create or update a DNS-only (unproxied, `ttl=1`) A record; a no-op when the content already matches |
| `cf_delete_record_by_hostname()` / `..._by_id()` | `DELETE /zones/{id}/dns_records/{record_id}` | Remove records on route delete or rename |
| `get_public_ip()` | `https://api.ipify.org?format=json` | Public IP for DDNS and for records created without an explicit IP |

Configuration is read from `settings` first and falls back to the environment
when the stored value is empty, so saving a blank field cannot shadow an
env-configured deployment. Zone id and name lookups are cached in module state,
and `invalidate_zone_cache()` resets that cache when settings or the wizard are
saved. `validate_domain()` accepts the zone itself and any subdomain of it,
rejects other dotted names, passes bare labels through for resolution, and always
accepts the default route.

### Crafty Controller

`app/services/crafty.py` calls `{crafty_url}/api/v2` with a bearer token. TLS
verification is on unless `CRAFTY_INSECURE_SKIP_VERIFY` is truthy; a certificate
failure is reported with that specific hint rather than as a generic connection
error. HTTP `401`, `403`, and `404` become actionable messages, and a JSON body
with `status: "error"` is a failure even when the HTTP status was `2xx`.

| Call | Endpoint | Used for |
|---|---|---|
| List servers | `GET /servers` | Server cards; "not configured" is an empty configured state, anything else is `502` |
| Per-server stats | `GET /servers/{id}/stats` | Running state, players, CPU, memory |
| Server detail | `GET /servers/{id}` | Name and port before a port change |
| Set port | `PATCH /servers/{id}` with `{"server_port": port}` | Register the new port with Crafty |
| Power action | `POST /servers/{id}/action/{cmd}` with `start_server`, `stop_server`, or `restart_server` | Server control, retried with the raw action name for older Crafty builds |

A port change rewrites `server.properties` on the mounted Crafty volume as well
as calling the API. The file is located by trying `CRAFTY_SERVERS_DIR`, then
`/crafty/servers`, `/var/opt/crafty/servers`, `/app/crafty/servers`,
`/data/crafty/servers`, and `/data/servers`, matching on server id or name, and
finally falling back to `SERVER_PROPERTIES_PATH`. Successful lookups are cached
per server id; misses are not, so fixing a mount takes effect without a restart.
`server-port` and `query.port` are both updated, and the file is written to a
sibling temporary file and moved into place so a crash cannot leave a truncated
properties file. When a restart is requested and the server runs, it is stopped
first and the handler waits five seconds, because a live server would overwrite
the change on shutdown; a failed stop is reported in the response. Routes whose
backend exactly matches the old `CRAFTY_CONTAINER_HOST:port` are re-pointed to
the new port in one short transaction, and mc-router is synced afterwards,
outside that transaction.

### Docker label discovery

`app/services/docker_watcher.py` talks to the Docker socket over a Unix
transport (`DOCKER_SOCKET`, default `/var/run/docker.sock`) and lists containers
from `/containers/json`.

- A container becomes a route candidate when it carries
  `mc-router.itzg.me/externalServerName` or `mc-router.host`; the hostname is
  lowercased.
- The backend is the container's first published TCP port, or
  `<container_name>:25565` when none is published, which logs a warning because
  that only resolves on a shared Docker network.
- Results are cached for 10 seconds, including an empty result, so a Docker-less
  or failing host does not trigger a socket round trip per request.
- Discovered routes are display-only. They are merged into the dashboard payload
  with `source='docker'`, marked read-only, and never written to `routes`;
  `is_docker_managed()` makes add, edit, delete, and validation refuse those
  hostnames. Discovery is visible only to admins and holders of
  `see_all_routes`, and it is inactive when `DOCKER_SOCKET` does not exist.

## Configuration reference

Every variable below is read by the application. Most are read into module
constants when `config.py` is imported, and the rest (`LOG_LEVEL` through
`config.log_level()`, plus `DOCKER_SOCKET`, `CRAFTY_SERVERS_DIR`,
`SERVER_PROPERTIES_PATH`, and the `CLOUDFLARE_ZONE_NAME` fallback) are read when
first used. Either way they are read once during startup, so treat a restart as
required after any change.

| Variable | Default | Purpose |
|---|---|---|
| `DB_PATH` | `/data/mcrouter-ui.db` | SQLite file; its parent directory is created at startup |
| `ADMIN_USERNAME` | `admin` | Bootstrap admin username, created only when no such user exists |
| `ADMIN_PASSWORD` | `changeme` | Password for that first admin only, never reapplied. The literal default raises a warning and a UI banner |
| `SECRET_KEY` | empty | Session signing key; when empty, generated once and persisted in `settings` |
| `MC_PORT` | `25565` | Minecraft ingress port for the mc-router program |
| `API_PORT` | `8080` | mc-router REST API port, internal to the container |
| `MC_ROUTER_API` | `http://localhost:8080` | Base URL the UI uses to reach mc-router |
| `SESSION_HTTPS_ONLY` | `false` | Marks the session cookie `Secure` and enables HSTS; enable behind a TLS-terminating proxy, leave off for plain-HTTP LAN access |
| `SESSION_SAME_SITE` | `lax` | Session cookie `SameSite` value: `lax`, `strict`, or `none` |
| `SESSION_MAX_AGE` | `1209600` (14 days) | Session cookie lifetime in seconds |
| `CLOUDFLARE_API_TOKEN` | empty | Cloudflare token; enables DNS features and DDNS |
| `CLOUDFLARE_ZONE_ID` | empty | Cloudflare zone id, preferred over the name |
| `CLOUDFLARE_ZONE_NAME` | empty | Zone name, resolved to an id through the API when no id is set |
| `DDNS_INTERVAL_SECONDS` | `300` | DDNS loop interval |
| `CRAFTY_URL` | empty | Crafty base URL; must be `http` or `https` with no embedded credentials |
| `CRAFTY_API_KEY` | empty | Crafty API token |
| `CRAFTY_CONTAINER_HOST` | empty | Host or IP for TCP probes of Crafty servers and for re-pointing routes after a port change |
| `CRAFTY_SERVERS_DIR` | empty | Directory tried first when locating `server.properties` |
| `CRAFTY_INSECURE_SKIP_VERIFY` | empty | When truthy, disables Crafty TLS verification; only for a trusted LAN host with a self-signed certificate |
| `SERVER_PROPERTIES_PATH` | empty | Exact `server.properties` path, used when the directory search finds nothing |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Docker socket path; discovery is active only when it exists |
| `HEALTH_CHECK_INTERVAL` | `30` | Health loop interval in seconds |
| `HEALTH_HISTORY_RETENTION_HOURS` | `24` | Age at which `health_history` rows are pruned |
| `LOG_LEVEL` | `INFO` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`; anything else logs a warning and falls back to `INFO` |
| `MIN_PASSWORD_LENGTH` | `12` | Minimum length enforced for user creation and password changes |

`UI_PORT` (default `8090`) appears in `.env.example` and `docker-compose.yml` but
is not read by the application: it is the host port published to container port
8000 in the commented-out `ports` block, and it is irrelevant under the default
`network_mode: host`.

Precedence rules:

- Crafty: environment values are written over stored settings on every startup,
  so the environment wins. Cloudflare: a non-empty stored value wins, and an
  empty one falls through to the environment.
- The session key comes from `SECRET_KEY` when set, otherwise from the database,
  which is why the `/data` volume must persist.
- `ADMIN_PASSWORD` only bootstraps a missing admin account.
- Permission lists, password length limits, and validation patterns are code
  constants, not configuration.

## Known limitations

1. Synchronous SQLite in async handlers: `get_db()` uses the blocking `sqlite3`
   module, so each statement occupies the event loop. Statements are kept short
   and connections are never held across network calls, but a slow query still
   delays other requests.
2. `init_db()` runs at import time, so importing `app.main` performs database
   writes, migrations, and secret generation. This is a testability hazard; the
   fail-fast behaviour is intentional, since starting without a persistent
   session key would allow session forgery.
3. Responses from the catch-all `Exception` handler are produced by Starlette's
   `ServerErrorMiddleware`, which sits outside the user middleware stack, so an
   unhandled `500` carries no CSP or other security headers. Handled
   `HTTPException` responses do.
4. `route-change` events are filtered by hostname for restricted subscribers,
   but the subscriber's visible set is captured when the stream opens. A route
   created for that user by someone else therefore does not reach them until
   they reload the page.
5. Clearing the default route is best-effort, because mc-router exposes no
   DELETE for the fallback. `clear_default()` posts an empty backend, and the UI
   warns when that fails; until mc-router restarts, the old fallback may stay
   active.
6. There is no compensation if the process dies between an external call and the
   database write, so a crash mid-mutation can leave mc-router and SQLite
   disagreeing until the next restart sync or route edit. The startup sync
   re-pushes stored routes, so the database eventually wins.
7. The four loops are log-and-retry with capped linear backoff. Sustained
   failures appear only in the logs; the only failure surfaced in the UI is
   mc-router reachability through the `router-status` event.
8. Rate limiting is in memory, per process, and keyed on the direct peer IP
   (login) or user id (validation, password change), and it resets on restart.
   Behind a proxy that is not trusted for forwarded headers, every client shares
   one login bucket. Multiple uvicorn workers would each hold their own counters
   and their own mutation lock, so the design assumes the single worker
   supervisord starts.
9. Route mutations update mc-router before SQLite. Handled failures are
   compensated, but an unexpected outcome, such as mc-router accepting a request
   it does not apply, can still leave the two out of step until the next restart
   sync or edit.
10. Permission checks cost a connection each: `current_user()` re-reads the user
    row per request and `user_has_perm()` issues one query per check, which the
    dashboard multiplies per route and per permission toggle.
11. `setup_wizard_done` is stored per database rather than per admin, so the
    wizard is offered only while no Cloudflare token and no Crafty URL are
    configured and nobody has completed or skipped it.
12. Docker-discovered routes have no owner, cannot be edited from the UI, and
    exist only as cached label reads, so the watcher must run and the socket must
    be mounted for them to appear at all.
