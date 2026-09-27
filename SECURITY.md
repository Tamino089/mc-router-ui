# Security Policy

## Reporting a vulnerability

Report security issues through GitHub's private vulnerability reporting on this
repository (Security, then "Report a vulnerability"). Please do not open a public
issue for anything exploitable.

Include the affected version or commit, what an attacker can achieve, and the
steps to reproduce. You can expect an initial response within a few days.

## Supported versions

Only the latest commit on `master` is supported. Fixes are released as a new
container image on `ghcr.io/tamino089/mc-router-ui`.

## Deployment notes

This application is designed to run on a trusted network and to be reached
through a TLS-terminating reverse proxy when it is exposed more widely.

- Set `ADMIN_PASSWORD` before the first start and change it in Settings
  afterwards. The documented default is refused with a warning and a banner.
- Set `SESSION_HTTPS_ONLY=true` when serving over TLS. This also enables HSTS.
- Do not expose the mc-router API port (`API_PORT`, default 8080); it has no
  authentication of its own.
- Mounting the Docker socket grants the container control over the Docker
  daemon. It is mounted read-only for discovery, and the container runs as an
  unprivileged user, but Docker socket access remains a privileged capability.
- The credential fields in Settings accept a token the server then uses. Prefer
  a scoped Cloudflare token with only `Zone:DNS:Edit` for the target zone.
- In-memory rate limiting assumes a single uvicorn process, as started by the
  bundled supervisord configuration.

## Security-relevant behaviour

- Passwords are stored as PBKDF2-HMAC-SHA256 with a per-password random salt.
- Sessions are signed cookies, so a tampered or forged cookie is rejected.
- State-changing requests must carry an `Origin` or `Referer` matching the
  request host; this applies to every mutating route.
- A strict Content-Security-Policy is served with a per-response nonce. There
  are no inline event handlers or style attributes in the served markup.
- The fallback route requires the `manage_default_route` permission, which is
  not granted to new accounts by default.
