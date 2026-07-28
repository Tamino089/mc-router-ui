# ── Stage 1: mc-router binary ────────────────────────────────────────────────
FROM itzg/mc-router:latest AS mcrouter

# ── Stage 2: Final image ──────────────────────────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends supervisor && \
    rm -rf /var/lib/apt/lists/*

COPY --from=mcrouter /mc-router /usr/local/bin/mc-router
RUN chmod +x /usr/local/bin/mc-router

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY supervisord.conf /etc/supervisor/conf.d/mcrouter.conf

RUN mkdir -p /data /var/log/supervisor

VOLUME ["/data"]

EXPOSE 25565
EXPOSE 8000
EXPOSE 8080

# ── Configurable environment variables ───────────────────────────────────────
ENV MC_PORT=25565 \
    API_PORT=8080 \
    MC_ROUTER_API=http://localhost:8080 \
    DB_PATH=/data/mcrouter-ui.db \
    ADMIN_USERNAME=admin \
    ADMIN_PASSWORD=changeme \
    SECRET_KEY="" \
    CLOUDFLARE_API_TOKEN="" \
    CLOUDFLARE_ZONE_ID="" \
    CLOUDFLARE_ZONE_NAME="" \
    DDNS_INTERVAL_SECONDS=300 \
    CRAFTY_URL="" \
    CRAFTY_API_KEY="" \
    CRAFTY_SERVERS_DIR="" \
    SERVER_PROPERTIES_PATH="" \
    HEALTH_CHECK_INTERVAL="30" \
    HEALTH_HISTORY_RETENTION_HOURS="24"

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=5)" || exit 1

LABEL org.opencontainers.image.title="MC Router UI" \
      org.opencontainers.image.description="Minecraft Reverse-Proxy Web UI with Cloudflare DDNS and Crafty Controller integration" \
      org.opencontainers.image.licenses="MIT"

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/mcrouter.conf"]
