FROM itzg/mc-router:1.47.1@sha256:177433dd91507339924d60205a19ae9a289afd0e69dbad54e1a55d497f7523f8 AS mcrouter

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

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

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY supervisord.conf /etc/supervisor/conf.d/mcrouter.conf

RUN set -eux; \
    groupadd --gid "${APP_GID}" app || groupadd app; \
    useradd --uid "${APP_UID}" --gid app --no-create-home \
        --shell /usr/sbin/nologin app; \
    mkdir -p /data /var/log/supervisor; \
    chown -R app:app /data /var/log/supervisor /srv

VOLUME ["/data"]

EXPOSE 25565 8000 8080

ENV MC_PORT=25565 \
    API_PORT=8080 \
    MC_ROUTER_API=http://localhost:8080 \
    DB_PATH=/data/mcrouter-ui.db \
    ADMIN_USERNAME=admin \
    CLOUDFLARE_ZONE_ID="" \
    CLOUDFLARE_ZONE_NAME="" \
    DDNS_INTERVAL_SECONDS=300 \
    CRAFTY_URL="" \
    CRAFTY_SERVERS_DIR="" \
    CRAFTY_INSECURE_SKIP_VERIFY="" \
    CRAFTY_CONTAINER_HOST="" \
    SERVER_PROPERTIES_PATH="" \
    SESSION_HTTPS_ONLY=false \
    SESSION_SAME_SITE=lax \
    HEALTH_CHECK_INTERVAL="30" \
    HEALTH_HISTORY_RETENTION_HOURS="24" \
    MIN_PASSWORD_LENGTH="12" \
    LOG_LEVEL=INFO

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
