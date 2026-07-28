# MC Router UI — Architecture & Logic Walkthrough

This document describes the runtime architecture, the request/data flow, and the
end-to-end logic of the webapp. It is the canonical reference for contributors.

---

## 1. Runtime Topology

The project ships as a **single Docker container** supervised by `supervisord`.
Two processes run side-by-side and communicate over loopback HTTP.

```
                         ┌─────────────────────────────────────────┐
                         │            CONTAINER (PID 1)             │
                         │            supervisord + tini            │
                         └───────────────┬─────────────┬───────────┘
                                         │             │
                    ┌────────────────────┘             └──────────────────────┐
                    ▼                                                         ▼
        ┌───────────────────────┐                              ┌────────────────────────┐
        │  mc-router (Go binary) │  REST :8080                 │  uvicorn app.main:app  │  :8000
        │  :25565 MC handshake   │ ◄────────────────────────── │  (FastAPI)             │
        └───────────┬───────────┘   /routes /defaultRoute       └──────────┬─────────────┘
                    │                          /connections                  │
                    ▼                                                         ▼
              Minecraft client                                           Browser :8090
```

- **mc-router** (from `itzg/mc-router`) listens on `:25565` for Minecraft
  client handshakes and routes them to backends by hostname. It also exposes a
  REST API on `:8080` used by the UI to push/delete routes and read live
  connection counts.
- **uvicorn** runs the FastAPI app on `:8000`. The host maps `UI_PORT` (default
  `8090`) → `8000` for browser access. The mc-router API port `8080` is
  internal-only and never exposed.

---

## 2. Application Layering

```
        ┌───────────────────────────────┐
        │  MIDDLEWARE STACK (outer→inner) │
        │  1. SameOriginCsrfMiddleware     │   blocks CSRF on unsafe methods
        │  2. SessionMiddleware (cookie)   │   itsdangerous signed cookie
        │  3. Routing → Router → Handler   │
        │  4. Exception handler (500)      │   global catch-all → JSON 500
        └───────────────┬───────────────┘
                        │
        ┌───────────────┴────────────────────────────────────────┐
        │                       ROUTER DISPATCH                      │
        │   /healthz  /readyz  /login  /logout  /  (dashboard)     │
        │   /routes/{add,edit,delete}  /users/*  /settings/*         │
        │   /api/users /api/permissions /api/health/* /api/router-status
        │   /api/connections /api/ports/used /api/validate-route   │
        │   /api/cf/records  /api/crafty/servers{,/action,/port}   │
        └───────────────────────────────────────────────────────────┘
                        │
                        ▼
   ┌─────────────────────────┐                              ┌──────────────────────────────────┐
   │  AUTHN/AUTHZ            │                              │  HANDLERS (transport layer)       │
   │  current_user(session)  │                              │  • auth.py      • monitoring.py    │
   │  user_has_perm(perm)    │── gate ──► 403 if no perm     │  • routes.py    • cloudflare_api  │
   │  admin bypasses perms   │                              │  • users.py     • crafty_api.py    │
   └─────────────────────────┘                              │  • settings.py  • healthz.py      │
                                                            └──────────────────┬───────────────┘
                                                                               │
                                                                               ▼
        ┌────────────────────────────────────────────────────────────────────────────┐
        │  SERVICES (application layer) — business logic                             │
        │                                                                            │
        │  ┌─────────────────┐  push/pull  ┌──────────────┐                          │
        │  │ mc_router.py    │ ──────────► │ mc-router :8080│  (httpx async, 5s)      │
        │  │ router_request  │ ◄────────── │ /routes /conn  │                          │
        │  └─────────────────┘             └──────────────┘                           │
        │                                                                            │
        │  ┌─────────────────┐  upsert/del  ┌──────────────┐  ┌──────────────┐       │
        │  │ cloudflare.py   │ ───────────► │ CF API        │  │ ipify.org    │       │
        │  │ cf_request      │ ◄────────── │ /zones/dns    │  │ (public IP)  │       │
        │  └────────┬────────┘             └──────────────┘  └──────────────┘       │
        │           │ validate_domain (against zone)                                  │
        │  ┌────────▼────────┐  get/patch   ┌──────────────┐  ┌──────────────┐       │
        │  │ crafty.py        │ ───────────► │ Crafty API   │  │ filesystem   │       │
        │  │ crafty_request   │              │ /api/v2      │  │ server.prop. │       │
        │  └─────────────────┘              └──────────────┘  └──────────────┘       │
        │                                                                            │
        │  ┌─────────────────┐  TCP probe   (socket.create_connection, 2s)           │
        │  │ health.py       │ ───────────► each route backend host:port             │
        │  │ tcp_check        │                                                          │
        │  └─────────────────┘                                                          │
        └─────────────────────────────────┬────────────────────────────────────────────┘
                                          │  read/write
                                          ▼
        ┌────────────────────────────────────────────────────────────────────────────┐
        │  PERSISTENCE  (SQLite, WAL + FK on)                                         │
        │  get_db() context manager → sqlite3.Connection                              │
        │  tables: routes · users · permissions · settings · health_checks            │
        │          health_history                                                     │
        │  schema.init_db() runs at IMPORT TIME → DDL + migrations + admin bootstrap  │
        └────────────────────────────────────────────────────────────────────────────┘

        ┌────────────────────────────────────────────────────────────────────────────┐
        │  BACKGROUND WORKERS (lifespan-spawned asyncio tasks)                        │
        │   health_loop()  ── every 30s ──► check_all_routes + prune_old_history      │
        │   ddns_loop()    ── every 300s ─► get_public_ip; if changed → upsert all    │
        │   (on exception: log + continue forever, no backoff)                        │
        └────────────────────────────────────────────────────────────────────────────┘

        ┌────────────────────────────────────────────────────────────────────────────┐
        │  PRESENTATION                                                                │
        │  Jinja2: index.html + login.html       static: main.css + dashboard.js      │
        │  window.MC_UI_CONFIG injected server-side → consumed by dashboard.js        │
        └────────────────────────────────────────────────────────────────────────────┘
```

### Layer responsibilities

| Layer | Files | Responsibility |
|-------|-------|----------------|
| **Transport / HTTP** | `app/main.py`, `app/routes/*` | FastAPI wiring, middleware, route handlers, error envelopes |
| **Application / Services** | `app/services/*` | Business logic: external API clients, health probes, DDNS loop |
| **Persistence** | `app/db/database.py`, `app/db/schema.py` | SQLite connection (WAL, FK on), schema + migrations, permission helpers |
| **Core / Security** | `app/core/config.py`, `app/core/security.py`, `app/core/csrf.py` | ENV config, password hashing, session helpers, CSRF middleware |
| **Presentation** | `app/templates/*`, `app/static/*` | Jinja2 templates, CSS, dashboard JS |

---

## 3. Request Lifecycle — `POST /routes/add` (worked example)

```
Browser form
   │ (cookie: session; Origin header)
   ▼
[CSRF middleware] ── Origin ≠ Host? ──► 403 csrf_failed
   │ (same-origin OK)
   ▼
[Session middleware] ── loads request.session
   ▼
[Router] /routes/add → routes.add_route
   │
   ├─ current_user(request) ── no session? ──► 303 → /login
   ├─ user_has_perm(user,"create_route") ── no? ──► 303 → /?error=Permission denied
   │
   ├─ with get_db() as con:           ◄── WAL, FK on (blocking call in async ctx)
   │     SELECT existing route → duplicate? ──► 303 → /?error=…
   │     INSERT routes (owner_id=user.id); commit
   │
   ├─ mc_router.push_route(hostname, backend)   ◄── httpx → localhost:8080/routes
   │     err? ──► 303 → /?error=…sync failed
   │
   ├─ cloudflare.sync_dns_for_route(hostname)
   │     validate_domain → cf_upsert_a_record → CF API   (best-effort)
   │
   └─ RedirectResponse → /?success=…

  ⚠ Unhandled Exception → global handler → JSON 500 {error:"internal"}
```

### Middleware order (outer → inner)

1. **`SameOriginCsrfMiddleware`** — for `POST`/`PUT`/`PATCH`/`DELETE`, verifies
   the `Origin` header (fallback `Referer`) matches the `Host` header. Exempts
   safe methods and `/healthz`, `/readyz`. Returns `403 csrf_failed` on mismatch.
2. **`SessionMiddleware`** — Starlette signed-cookie sessions; `request.session`
   holds `{id, username, role}` for an authenticated user.
3. **Router dispatch** — FastAPI matches the path to a handler.
4. **Exception handler** — `@app.exception_handler(Exception)` catches anything
   unhandled, logs with stack trace, returns `500 {"error":"internal"}`.

---

## 4. Authentication & Authorization

```
Login flow:
  GET  /login         → login form (redirects to / if already authed)
  POST /login         → verify_password(pbkdf2_sha256) → session cookie → 303 /
  GET  /logout        → session.clear() → 303 /login

Per-request authz:
  current_user(request) → request.session.get("user")   # dict or None
  user_has_perm(user, perm):
      - user.role == "admin" → True (bypass)
      - else → SELECT FROM permissions WHERE user_id=? AND permission=?
```

- Passwords stored as `pbkdf2_sha256$100000$<salt>$<hash>`.
- Session is a signed cookie (itsdangerous); `SECRET_KEY` is auto-generated on
  first boot and persisted to the `settings` table so cookies survive restarts.
- **Permissions are granular** (`see_own_routes`, `create_route`, `manage_users`,
  …). Non-admin users get `DEFAULT_USER_PERMISSIONS` on creation.
- Admins bypass all permission checks.

---

## 5. Data Model

```
routes            users            permissions
─────────         ─────            ───────────
id (PK)           id (PK)          id (PK)
hostname (UQ)     username (UQ)    user_id (FK→users.id)
backend           password_hash    permission
is_default        role              UNIQUE(user_id, permission)
owner_id (FK→users.id) created_at
created_at

settings          health_checks    health_history
─────────         ────────────     ──────────────
key (PK)          route_id (PK,FK) id (PK)
value             healthy          route_id (FK→routes.id)
                  latency_ms       healthy
                  checked_at       latency_ms
                  error            checked_at
```

- `settings` is a key-value EAV table storing: `secret_key`, `crafty_url`,
  `crafty_token`, `crafty_container_host`, `cf_api_token`, `cf_zone_id`,
  `cf_zone_name`, `last_public_ip`, `setup_wizard_done`.
- `schema.init_db()` runs at import time: creates tables if missing, performs
  non-destructive migrations (e.g. adding `owner_id`), bootstraps the admin user
  from `ADMIN_USERNAME`/`ADMIN_PASSWORD` env vars, and resolves `SECRET_KEY`.

---

## 6. External Integrations

```
┌─────────────────────────────────────────────────────────────────────┐
│  mc-router API  (localhost:8080)                                     │
│    POST /routes            {serverAddress, backend}                  │
│    DELETE /routes/{host}                                             │
│    POST /defaultRoute       {backend}                                │
│    GET  /connections        → {hostname: count, …}                   │
│    GET  /routes             (used by /readyz + /api/router-status)   │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Cloudflare API  (api.cloudflare.com/client/v4)                      │
│    GET  /zones?name=…           → resolve zone_id from name          │
│    GET  /zones/{id}/dns_records  → list A records                    │
│    POST /zones/{id}/dns_records  → create A record (DNS-only)        │
│    PUT  /zones/{id}/dns_records/{rid}  → update A record             │
│    DELETE /zones/{id}/dns_records/{rid}                               │
│  + ipify.org  → public IP for DDNS                                    │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Crafty Controller API  ({CRAFTY_URL}/api/v2)                        │
│    GET  /servers                       → list servers                 │
│    GET  /servers/{id}/stats            → running/cpu/mem/players     │
│    GET  /servers/{id}                  → server_name                 │
│    PATCH /servers/{id}                 → update server_port in DB     │
│    POST /servers/{id}/action/{cmd}     → start/stop/restart          │
│  + filesystem  → /crafty/servers/.../server.properties port rewrite  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. Background Workers

Two `asyncio` tasks are spawned in `lifespan` and cancelled on shutdown:

```
health_loop()                          ddns_loop()
  while True:                             while True:
    check_all_routes()                      token, zid, zname = get_cf_config()
      for each route:                       if token and (zid or zname):
        tcp_check(host, port, 2s)             ip = get_public_ip()
        UPSERT health_checks                  if ip != last_public_ip:
        INSERT health_history                   for each non-default route:
    prune_old_history()                         cf_upsert_a_record(hostname, ip)
    sleep(30s)                                sleep(300s)
```

- Health probes run on the default executor (`run_in_executor`) so blocking
  `socket.create_connection` does not stall the event loop.
- Both loops catch+log+continue on exception; no backoff, no operator surface.

---

## 8. Health Endpoints

| Endpoint | Check | Status |
|----------|-------|--------|
| `GET /healthz` | Process alive + loop responsive | `200 {"status":"ok"}` |
| `GET /readyz` | DB openable **and** mc-router API answering | `200` if both ok, else `503` with per-check detail |

The Docker `HEALTHCHECK` targets `/healthz` (liveness). Orchestrators should use
`/readyz` for readiness gates so traffic is only routed when mc-router is up.

---

## 9. Known Logic Defects (next batch)

These are tracked for the phased refactor and do not block the current commit:

1. **No rollback on partial route sync** — `routes.py` commits DB before pushing
   to mc-router; if the push fails, DB and router drift.
2. **Blocking SQLite in async handlers** — `with get_db()` is a synchronous
   `sqlite3` call inside `async def` handlers; stalls the event loop under load.
3. **Role read from session cookie** — `current_user()` returns the cached dict;
   role changes don't propagate until the user re-logs in.
4. **`init_db()` runs at import time** — importing `app.main` triggers DB writes
   and secret generation (testability hazard).
5. **Domain validation bypassable** — only `/api/validate-route` (frontend live
   check) calls `validate_domain`; a direct `POST /routes/add` does not.
6. **Background loops have no backoff / no liveness signal** — persistent
   failures retry forever with no operator visibility.
7. **`user_has_perm` opens a DB connection per check** — now with 3 extra
   PRAGMAs per call; overhead in the dashboard which checks perms per row.

---

## 10. File Map

```
app/
├── main.py                 FastAPI app, lifespan, middleware, / dashboard
├── core/
│   ├── config.py           ENV → constants (ports, paths, perms list)
│   ├── security.py         hash_password, verify_password, current_user
│   └── csrf.py             SameOriginCsrfMiddleware
├── db/
│   ├── database.py         get_db() context manager (WAL, FK on, synchronous=NORMAL)
│   └── schema.py           init_db(), migrations, user_has_perm, grant_default_permissions
├── routes/
│   ├── auth.py             /login, /logout
│   ├── routes.py           /routes/{add,edit,delete}
│   ├── users.py            /users/*, /api/users, /api/permissions
│   ├── settings.py         /settings/{password,cloudflare,crafty,wizard}
│   ├── monitoring.py       /api/health/*, /api/router-status, /api/connections, /api/ports/used
│   ├── cloudflare_api.py   /api/cf/records, /api/validate-route
│   ├── crafty_api.py       /api/crafty/servers{,/action,/port}
│   └── healthz.py          /healthz, /readyz
├── services/
│   ├── mc_router.py        httpx wrapper for mc-router REST API
│   ├── health.py           tcp_check + health_loop background worker
│   ├── cloudflare.py       CF API client + ddns_loop + domain validation
│   └── crafty.py           Crafty API client + server.properties I/O
├── templates/  index.html, login.html
└── static/     css/{main,login}.css, js/dashboard.js, img/favicon.svg
```
