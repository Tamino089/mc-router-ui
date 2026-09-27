# Changelog

All notable changes to this project are documented here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Fixed

- Real-time updates were completely broken. `broadcast()` assigned its cache
  variables without a `global` declaration, so every `connections` and
  `router-status` event raised `UnboundLocalError` and the emitter loop
  swallowed it into its backoff path. Verified fixed against a live server.
- Creating or editing a route with a subdomain-only hostname (for example
  `play` rather than `play.example.com`) raised `TypeError` and returned
  HTTP 500, because a synchronous helper was awaited.
- Database foreign keys were never enforced. `PRAGMA foreign_keys` is a
  per-connection setting, and only the migration connection enabled it, so
  `ON DELETE CASCADE` never fired and deleting a user or route left orphaned
  permission and health rows behind.
- Any user holding `create_route` could create or claim the fallback route and
  point all unmatched Minecraft traffic at an arbitrary internal address. The
  fallback now requires the separate `manage_default_route` permission.
- Saving an edit with a blank hostname stored an empty hostname and pushed it to
  mc-router.
- A route edit held a SQLite write transaction open across Cloudflare and
  mc-router calls, so concurrent writers failed with `database is locked` after
  the five second timeout.
- Deleting the fallback route left mc-router routing unmatched traffic to the
  removed backend.
- More than one fallback route could exist, and editing a route to become the
  fallback never demoted the previous one.
- Editing a user without supplying a role silently demoted them, so renaming an
  admin removed admin rights.
- Editing a user re-granted the default permission set, undoing an
  administrator's revocations.
- Posting a body without a permissions list erased every permission the user
  held, because an absent key was read as an empty list.
- `/api/cf/zones` required only authentication, letting any signed-in user
  enumerate Cloudflare zone names and identifiers.
- Cloudflare endpoints reported configuration state to anonymous callers before
  authenticating.
- Stored empty Cloudflare settings shadowed the corresponding environment
  variables, so saving a blank field disabled an env-configured deployment.
- An empty `ADMIN_PASSWORD` produced an admin account with a blank password.
  It now falls back to the documented default, which is warned about.
- The SSE connection map and `route-change` events were delivered unfiltered, so
  restricted users received other users' hostnames.
- `/readyz` returned internal error text to unauthenticated callers.
- `LOG_LEVEL` was documented but never read.
- Crafty responses that were not objects, and non-numeric port values, raised
  `AttributeError` and `ValueError` into HTTP 500 responses.
- A failed Crafty stop was ignored, so `server.properties` could be rewritten
  while the server was still running and then be overwritten on shutdown.
- `server.properties` was rewritten in place rather than atomically.
- Docker discovery never cached an empty result, re-querying the socket on every
  request and paying the full timeout when Docker was unavailable.
- Health history ordering was ambiguous within a one-second timestamp
  resolution, and pruning was skipped whenever a check pass failed.
- The Crafty zone-name cache was never invalidated, so a saved zone change had
  no effect until restart.
- The Crafty servers endpoint reported an unreachable Crafty as success.
- The error-toast Retry button and the SSE banner were white on white in the
  default dark theme.
- The button spinner rendered as a static ring due to a duplicated opacity
  declaration.
- A Crafty port change left the routes table showing the previous backend.
- A transient `EventSource` error forced a navigation to `/login`, discarding
  unsaved input.
- Clicking a modal overlay bypassed `closeModal`, leaving the focus trap
  targeting a hidden dialog.
- A failed Crafty server load could leave the backend field hidden and
  unusable.
- Docker rows emitted duplicate `conn-None` element ids.
- User initials were interpolated into markup unescaped.

### Added

- Strict Content-Security-Policy with a per-response nonce. Inline event
  handlers, style attributes and third-party font origins are gone; markup uses
  `data-action` attributes handled by one delegated dispatcher.
- Pytest suite covering authorisation, CSRF, the session model, route and user
  management, database integrity, SSE filtering and validation.
- CI pipeline running lint, tests, an image build with a container smoke test,
  and a Trivy scan.
- `pyproject.toml` with ruff and pytest configuration, and Dependabot updates.
- `SECURITY.md`, `CHANGELOG.md`, `LICENSE`, `.editorconfig`, `.gitattributes`.
- Configurable session cookie policy via `SESSION_HTTPS_ONLY`,
  `SESSION_SAME_SITE` and `SESSION_MAX_AGE`.
- `MIN_PASSWORD_LENGTH`, enforced on user creation and password changes.

### Changed

- The container runs as an unprivileged `app` user, includes `no-new-privileges`
  and drops all capabilities in Compose.
- Both base images are pinned by version and digest instead of `latest`. Note
  that the mc-router image tag has no `v` prefix even though its GitHub release
  tag does, so `v1.47.1` does not exist as an image tag.
- `.dockerignore` now excludes the virtualenv, tests and tooling caches. It
  previously omitted `.venv/`, so every build uploaded a 95 MB virtualenv as
  build context.
- `mc-router` no longer receives `--routes-config`; routes are provisioned
  through the REST API and persisted in SQLite.
- Google Fonts was replaced with a system font stack, so the UI works offline
  and makes no third-party requests.
- Comments and documentation were reduced to short plain notes without
  box-drawing characters, emoji or ASCII diagrams.
- The Unraid template gained the missing variables, host networking, and no
  longer defaults the admin password.
