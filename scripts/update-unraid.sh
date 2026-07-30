#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  MC Router UI — Unraid auto-update script
#
#  Two modes:
#    1. GHCR (default) — pulls the pre-built image from GitHub Container
#       Registry. Fast, no build tools needed. Works with the Unraid
#       "Update" button if the template repo is set correctly.
#    2. Build — clones/pulls source from GitHub and builds locally.
#       Slower, but allows local modifications.
#
#  Usage:   bash /mnt/user/appdata/mc-router-ui-src/scripts/update-unraid.sh
#           (or set up as a User Script in Unraid with a cron schedule)
#
#  Set MODE=build to rebuild from source instead of pulling from GHCR:
#    MODE=build bash /mnt/user/appdata/mc-router-ui-src/scripts/update-unraid.sh
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
: "${MODE:=ghcr}"                          # "ghcr" or "build"
GHCR_IMAGE="ghcr.io/tamino089/mc-router-ui:latest"
GIT_REPO="https://github.com/Tamino089/mc-router-ui.git"
GIT_BRANCH="master"
SOURCE_DIR="/mnt/user/appdata/mc-router-ui-src"
CONTAINER_NAME="mc-router-ui"
LOCAL_IMAGE="mc-router-ui:latest"
LOCAL_IMAGE_PREV="mc-router-ui:previous"

# ── Helpers ──────────────────────────────────────────────────────────────────
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { log "FEHLER: $*"; exit 1; }

# ═══════════════════════════════════════════════════════════════════════════
#  MODE 1: GHCR PULL — pull pre-built image from GitHub Container Registry
# ═══════════════════════════════════════════════════════════════════════════
if [ "$MODE" = "ghcr" ]; then
    log "Modus: GHCR Pull — ziehe $GHCR_IMAGE ..."

    if docker image inspect "$LOCAL_IMAGE" >/dev/null 2>&1; then
        docker tag "$LOCAL_IMAGE" "$LOCAL_IMAGE_PREV"
        log "Altes Image als '$LOCAL_IMAGE_PREV' gesichert."
    fi

    docker pull "$GHCR_IMAGE" 2>&1 || fail "docker pull fehlgeschlagen"
    docker tag "$GHCR_IMAGE" "$LOCAL_IMAGE"

    log "GHCR-Pull erfolgreich."

    # Restart container
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log "Container '$CONTAINER_NAME' gefunden. Starte neu..."
        docker restart "$CONTAINER_NAME" 2>&1 || fail "docker restart fehlgeschlagen"
        log "Container neu gestartet."
    else
        log "Container '$CONTAINER_NAME' nicht gefunden."
    fi

    log "Fertig! Image $GHCR_IMAGE aktuell."
    log ""
    log "Rollback (falls nötig):"
    log "  docker tag $LOCAL_IMAGE_PREV $LOCAL_IMAGE && docker restart $CONTAINER_NAME"
    exit 0
fi

# ═══════════════════════════════════════════════════════════════════════════
#  MODE 2: BUILD — clone/pull source and build image locally
# ═══════════════════════════════════════════════════════════════════════════
log "Modus: Build — baue Image aus Quellcode..."

mkdir -p "$SOURCE_DIR"

if [ -d "$SOURCE_DIR/.git" ]; then
    log "Pull latest from GitHub ($GIT_BRANCH)..."
    cd "$SOURCE_DIR"
    git fetch origin "$GIT_BRANCH" 2>&1 || fail "git fetch fehlgeschlagen"
    OLD_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    git reset --hard "origin/$GIT_BRANCH" 2>&1 || fail "git reset fehlgeschlagen"
    NEW_COMMIT=$(git rev-parse --short HEAD)
    if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
        log "Bereits aktuell (commit $NEW_COMMIT)."
    else
        log "Update: $OLD_COMMIT → $NEW_COMMIT"
        git log --oneline -3 2>/dev/null || true
    fi
else
    log "Kein Git-Repo. Clone von GitHub..."
    rm -rf "$SOURCE_DIR"/* 2>/dev/null || true
    git clone --branch "$GIT_BRANCH" "$GIT_REPO" "$SOURCE_DIR" 2>&1 || fail "git clone fehlgeschlagen"
    cd "$SOURCE_DIR"
    NEW_COMMIT=$(git rev-parse --short HEAD)
fi

if docker image inspect "$LOCAL_IMAGE" >/dev/null 2>&1; then
    docker tag "$LOCAL_IMAGE" "$LOCAL_IMAGE_PREV"
fi

log "Baue Image aus commit $NEW_COMMIT ..."
docker build -t "$LOCAL_IMAGE" . 2>&1 || fail "docker build fehlgeschlagen"
log "Build erfolgreich."

if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log "Container '$CONTAINER_NAME' gefunden. Starte neu..."
    docker restart "$CONTAINER_NAME" 2>&1 || fail "docker restart fehlgeschlagen"
    log "Container neu gestartet."
else
    log "Container '$CONTAINER_NAME' nicht gefunden."
fi

log "Fertig! Image gebaut aus commit $NEW_COMMIT."
log ""
log "Rollback (falls nötig):"
log "  docker tag $LOCAL_IMAGE_PREV $LOCAL_IMAGE && docker restart $CONTAINER_NAME"
