# Stage 1: the mc-router binary, pinned to a released version rather than
# "latest" so a rebuild cannot silently change the router underneath the UI.
FROM itzg/mc-router:v1.47.1 AS mcrouter

# Stage 2: runtime image.
FROM python:3.12-slim

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends supervisor tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=mcrouter /mc-router /usr/local/bin/mc-router
RUN chmod +x /usr/local/bin/mc-router

WORKDIR /srv

# Dependencies first so application edits do not invalidate this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY supervisord.conf /etc/supervisor/conf.d/mcrouter.conf

# The container runs unprivileged. /data must be writable by APP_UID, and the
# Docker socket requires either group_add or an explicit `user: root` override.
RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /var/log/supervisor /var/run \
    && chown -R "${APP_UID}:${APP_GID}" /data /var/log/supervisor /srv

VOLUME ["/data"]

EXPOSE 25565 8000 8080

# Empty by default: an unset password falls back to the documented default,
# which is warned about at startup and flagged in the UI until it is changed.
ENV MC_PORT=25565 \
    API_PORT=8080 \
    MC_ROUTER_API=http://localhost:8080 \
    DB_PATH=/data/mcrouter-ui.db \
    ADMIN_USERNAME=admin \
    ADMIN_PASSWORD="" \
    SECRET_KEY="" \
    CLOUDFLARE_API_TOKEN="" \
    CLOUDFLARE_ZONE_ID="" \
    CLOUDFLARE_ZONE_NAME="" \
    DDNS_INTERVAL_SECONDS=300 \
    CRAFTY_URL="" \
    CRAFTY_API_KEY="" \
    CRAFTY_SERVERS_DIR="" \
    CRAFTY_INSECURE_SKIP_VERIFY="" \
    CRAFTY_CONTAINER_HOST="" \
    SERVER_PROPERTIES_PATH="" \
    HEALTH_CHECK_INTERVAL="30" \
    HEALTH_HISTORY_RETENTION_HOURS="24" \
    LOG_LEVEL=INFO

# Liveness: the web UI is serving. /readyz additionally checks the database and
# mc-router, but it is unsuitable here because pointing MC_ROUTER_API at an
# external router is supported and would report the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=4)"]

LABEL org.opencontainers.image.title="MC Router UI" \
      org.opencontainers.image.description="Self-hosted web UI for mc-router with Cloudflare DDNS, Crafty Controller integration and Docker label discovery" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/Tamino089/mc-router-ui"

STOPSIGNAL SIGTERM

USER app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/mcrouter.conf"]
