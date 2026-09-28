# Changelog

Notable changes. This project follows [Semantic Versioning](https://semver.org).

## 1.0.1

### Fixed

- The container now repairs the ownership of its data directory on start. It
  starts as root, takes ownership of `/data`, drops back to UID 1000 and only
  then runs supervisord, so neither service ever runs as root. A data directory
  left behind by an older install (root-owned, or a database with mode `0444`)
  used to make the web UI exit at import time with `sqlite3.OperationalError:
  attempt to write a readonly database`, leaving the container "running" and
  unhealthy with no web UI and an empty log in the unRAID UI.
- supervisord and both services now log to stdout and stderr, so `docker logs`
  and the unRAID log viewer show the real error. Previously supervisord's
  activity log was the only thing on stdout and every Python traceback went to a
  file inside the container, which made a crash loop look like silence.
- The startup failure message now names the uid the web UI runs as and the
  `chown` command that fixes it, instead of only "check that DB_PATH is
  writable".
- supervisord no longer carries a `user=` directive, so running the container
  with an explicit `--user` (for example `--user 99:100`) no longer aborts at
  startup with `Can't drop privilege as nonroot user`. The entrypoint drops
  privileges itself when it starts as root and preserves supplementary groups,
  so `--group-add` for the Docker socket keeps working.
- CI now asserts that the services run as the unprivileged user, that the
  services' output reaches `docker logs`, and that a root-owned, read-only data
  directory is repaired before the web UI starts.

## 1.0.0

First public release.

### Highlights

- Hostname routing with an optional catch-all fallback, Cloudflare DDNS, Crafty
  Controller management, Docker label discovery, health monitoring and live
  updates over server-sent events.
- Multi-user accounts with granular permissions, PBKDF2 passwords and signed
  sessions.
- Strict Content-Security-Policy with a per-response nonce. No inline handlers,
  no inline styles, no third-party origins.
- Unprivileged container with all capabilities dropped; base images pinned by
  digest.

### Security fixes

These were found and fixed before the first public release:

- Any user holding `create_route` could create or claim the fallback route and
  point all unmatched Minecraft traffic at an arbitrary internal address. It now
  requires a separate `manage_default_route` permission.
- Real-time updates never worked: the SSE broadcast assigned its cache variables
  without a `global` declaration, so every event raised `UnboundLocalError` and
  the emitter swallowed it into its backoff path.
- Database foreign keys were never enforced, because `PRAGMA foreign_keys` is a
  per-connection setting and only the migration connection enabled it. Deleting a
  user or route left orphaned permission and health rows behind.
- `/api/cf/zones` required only authentication, letting any signed-in user
  enumerate Cloudflare zone names and ids.
- Restricted users received other users' hostnames through the SSE connection map
  and route-change events.
- Posting a body without a permissions list erased every permission a user held.
- Editing a user without a role silently demoted them, and editing a user
  re-granted permissions an administrator had revoked.
- An empty `ADMIN_PASSWORD` produced an account with a blank password.
- `/readyz` returned internal error text to unauthenticated callers.

### Other notable fixes

- Creating a subdomain-only route returned HTTP 500, because a synchronous helper
  was awaited. Subdomain-only routes never worked.
- A route edit held a SQLite write transaction open across network calls, causing
  `database is locked` for concurrent writers.
- Deleting the fallback route left mc-router routing unmatched traffic to the
  removed backend.
- A failed Crafty stop was ignored, so `server.properties` could be rewritten
  while the server was still running.
- The error-toast Retry button was white on white in the default dark theme.
- A Crafty port change left the routes table showing the previous backend.
- A transient `EventSource` error forced a navigation to `/login`, discarding
  unsaved input.
