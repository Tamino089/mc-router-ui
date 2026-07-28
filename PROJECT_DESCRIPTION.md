# Project Description

MC Router UI is a FastAPI-based administrative web application for managing `mc-router` route configuration and runtime routing state for Minecraft infrastructure. The project is engineered as a self-hosted, containerized control plane that runs alongside `mc-router` in the same Docker image and is supervised by `supervisord`. Its primary responsibility is to expose a web interface for operational routing changes while keeping configuration state persistent and recoverable across container restarts.

## Architectural Role

The application functions as a management layer above `mc-router`, not as a replacement for it. `mc-router` remains the network-facing routing engine that intercepts Minecraft connection handshakes and forwards clients to the appropriate backend based on the requested hostname. This project provides the UI, authentication, database-backed state persistence, and operational automation needed to manage that routing layer from a browser.

At runtime, the service is structured around the following responsibilities:

- `mc-router` exposes the routing and status API on an internal port, typically `8080`.
- The FastAPI application receives user requests through the browser UI and calls the `mc-router` API directly.
- A local SQLite database stores persistent routing data, configuration settings, and user/permission records.
- `supervisord` ensures the `mc-router` process and the FastAPI service run side-by-side inside the same container.
- Environment variables control deployment parameters such as ports, admin credentials, database path, and optional external integrations.

## Runtime Behavior

The application can be described as a stateful control panel for route administration:

1. Route definitions are created and managed through the UI.
2. Route changes are persisted in SQLite and pushed to `mc-router` through HTTP requests.
3. `mc-router` uses the hostname from the Minecraft handshake to choose a backend target.
4. Backend health is evaluated by making outbound connection checks to known backend endpoints.
5. Active connection counts are fetched from the `mc-router` API to expose current load and status information.
6. User-level access is enforced through a session-based authentication model and a permission system stored in the database.

## Data Model

The database is the source of truth for administrative state. The implementation uses SQLite with a small set of relational tables:

- `routes`: stores hostname/backend mappings, default route indicators, ownership metadata, and creation timestamps.
- `settings`: holds persistent application settings such as the session secret key, Crafty configuration values, and Cloudflare-related state.
- `users`: stores username, password hash, and role metadata for application authentication.
- `permissions`: stores per-user grant entries used for role separation and feature-level authorization.

The password handling is intentionally more secure than a plain-text storage model. User credentials are stored as `pbkdf2_sha256` password hashes using a per-user salt, and verification occurs at login time. The application also supports a persistent secret key stored in the database, preventing session cookie instability across container restarts.

## Authentication and Authorization

The service implements a browser-based authentication workflow using FastAPI and Starlette session middleware. A user logs in through the UI, receives a session cookie, and the application uses that session to determine access to protected endpoints and pages.

Authorization is permission-driven rather than purely role-driven:

- Admin users can bypass most restrictions and manage the full application surface.
- Regular users receive a default set of permissions that allow them to manage only their own routes.
- Additional permissions can be granted for functionality such as viewing all routes, managing Cloudflare DNS records, viewing server status, managing users, or changing global settings.

This design allows the project to support multi-user operation in environments where route ownership and administrative boundaries matter.

## API Integration Pattern

The application communicates with `mc-router` through a thin wrapper around `httpx` and the `mc-router` REST API. The wrapper abstracts transport concerns, including connection errors, timeouts, HTTP status failures, and generic exceptions. Requests are sent to endpoints such as:

- route creation
- route deletion
- default route update
- connection and status inspection

This keeps the UI logic clean and centralizes error handling for availability issues such as `mc-router` being offline or the API endpoint not responding.

## Health Monitoring

One of the major operational features is backend health evaluation. The UI displays a per-backend health indicator based on live TCP connectivity tests. These checks are not a replacement for deeper application-level checks, but they provide a lightweight way to confirm whether an upstream backend is reachable on the expected port.

The application also queries the `mc-router` connection endpoint to surface live connection counts, giving operators a direct view of how much traffic is currently flowing through the router state.

## Persistence and Migration Model

The SQLite database is designed to survive restarts and maintain operator state. On startup, the application initializes the database if needed and performs non-destructive migrations where possible. That includes adding missing columns such as `owner_id` to existing route records, as well as carrying forward or migrating legacy credentials and settings.

This approach ensures the app behaves predictably when redeployed or when the container is restarted without wiping the persistent data volume.

## Deployment Model

The service is designed around a single-container Docker deployment model:

- the web UI and `mc-router` run in the same container
- `supervisord` starts both processes
- SQLite persists on a mounted volume path such as `/data`
- external ports are mapped to expose Minecraft traffic and the UI over host ports

The deployment is especially suited to self-hosted environments like Unraid, where the operator wants a simple Docker-based route control panel without splitting services across multiple containers.
And the user need an unraid docker template for easy deployment ./my-mc-router-ui(-v2).xml

## Optional External Integrations

The codebase also includes integration paths for external systems:

- Cloudflare API support for dynamic DNS automation
- Crafty environment variable plumbing into the persistent settings table
- optional automatic synchronization of route and infrastructure settings from environment variables into the database

These integrations make the project more flexible in larger self-hosted Minecraft hosting setups, but the core runtime responsibility remains route administration and operational visibility.

## Infrastructure Summary

In practical terms, the project is a Python-based, database-backed management front end that sits in front of a host-level routing proxy. It provides a persistent, user-authenticated interface to:

- manage DNS-based route mappings
- expose a default backend fallback
- validate backend availability
- observe routing state
- secure administrative access with per-user permissions

This makes it useful for operators who need reliable multi-backend Minecraft traffic composition without relying on a more complex full-stack control panel.
