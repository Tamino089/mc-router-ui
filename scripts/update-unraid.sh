#!/bin/bash
# Unraid auto-update script for MC Router UI.
#
# Two modes:
#   1. ghcr (default): pull the pre-built image from the GitHub Container
#      Registry. Fast, and needs no build tooling.
#   2. build: clone or pull the source from GitHub and build the image locally.
#      Slower, but picks up local modifications.
#
# Usage:
#   bash /mnt/user/appdata/mc-router-ui-src/scripts/update-unraid.sh
#   MODE=build bash /mnt/user/appdata/mc-router-ui-src/scripts/update-unraid.sh
#
# Can also be scheduled as an Unraid User Script.
set -euo pipefail

MODE="${MODE:-ghcr}"
GHCR_IMAGE="ghcr.io/tamino089/mc-router-ui:latest"
GIT_REPO="https://github.com/Tamino089/mc-router-ui.git"
GIT_BRANCH="master"
SOURCE_DIR="/mnt/user/appdata/mc-router-ui-src"
CONTAINER_NAME="mc-router-ui"
LOCAL_IMAGE="mc-router-ui:latest"
LOCAL_IMAGE_PREV="mc-router-ui:previous"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { log "ERROR: $*"; exit 1; }

restart_container() {
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log "Restarting container '$CONTAINER_NAME'..."
        docker restart "$CONTAINER_NAME" >/dev/null 2>&1 || fail "docker restart failed"
        log "Container restarted."
    else
        log "Container '$CONTAINER_NAME' not found; nothing to restart."
    fi
}

rollback_hint() {
    log ""
    log "To roll back:"
    log "  docker tag $LOCAL_IMAGE_PREV $LOCAL_IMAGE && docker restart $CONTAINER_NAME"
}

# Keep the current image so a bad update can be rolled back.
if docker image inspect "$LOCAL_IMAGE" >/dev/null 2>&1; then
    docker tag "$LOCAL_IMAGE" "$LOCAL_IMAGE_PREV"
    log "Previous image saved as '$LOCAL_IMAGE_PREV'."
fi

if [ "$MODE" = "ghcr" ]; then
    log "Mode: ghcr pull."
    docker pull "$GHCR_IMAGE" >/dev/null 2>&1 || fail "docker pull failed"
    docker tag "$GHCR_IMAGE" "$LOCAL_IMAGE"
    log "Pulled $GHCR_IMAGE."
    restart_container
    log "Done. Image $GHCR_IMAGE is current."
    rollback_hint
    exit 0
fi

if [ "$MODE" != "build" ]; then
    fail "Unknown MODE '$MODE' (expected 'ghcr' or 'build')"
fi

log "Mode: local build."
mkdir -p "$SOURCE_DIR"

if [ -d "$SOURCE_DIR/.git" ]; then
    log "Fetching $GIT_BRANCH from GitHub..."
    cd "$SOURCE_DIR"
    git fetch origin "$GIT_BRANCH" >/dev/null 2>&1 || fail "git fetch failed"
    OLD_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
    git reset --hard "origin/$GIT_BRANCH" >/dev/null 2>&1 || fail "git reset failed"
    NEW_COMMIT=$(git rev-parse --short HEAD)
    if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
        log "Already at commit $NEW_COMMIT."
    else
        log "Updated $OLD_COMMIT -> $NEW_COMMIT"
        git log --oneline -3 2>/dev/null || true
    fi
else
    log "No git repository found; cloning $GIT_REPO..."
    rm -rf "${SOURCE_DIR:?}"/* 2>/dev/null || true
    git clone --branch "$GIT_BRANCH" "$GIT_REPO" "$SOURCE_DIR" >/dev/null 2>&1 || fail "git clone failed"
    cd "$SOURCE_DIR"
    NEW_COMMIT=$(git rev-parse --short HEAD)
fi

log "Building image from commit $NEW_COMMIT..."
docker build -t "$LOCAL_IMAGE" . || fail "docker build failed"
log "Build succeeded."

restart_container
log "Done. Image built from commit $NEW_COMMIT."
rollback_hint
