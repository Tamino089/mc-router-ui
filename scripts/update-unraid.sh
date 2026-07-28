#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  MC Router UI — Unraid auto-update script
#
#  Pulls the latest code from GitHub, rebuilds the Docker image, archives
#  the old image for rollback, and restarts the running container.
#
#  Usage:   bash /mnt/user/appdata/mc-router-ui-src/scripts/update-unraid.sh
#           (or set up as a User Script in Unraid with a cron schedule)
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
GIT_REPO="https://github.com/Tamino089/mc-router-ui.git"
GIT_BRANCH="master"
SOURCE_DIR="/mnt/user/appdata/mc-router-ui-src"
TARGET_DIR="/mnt/user/appdata/mc-router-ui"
TARGET_FILE="$TARGET_DIR/mc-router-ui.tar.gz"
BACKUP_FILE="$TARGET_DIR/mc-router-ui.tar.gz.bak"
CONTAINER_NAME="mc-router-ui"          # Docker container name to restart
IMAGE_NAME="mc-router-ui:latest"
IMAGE_PREV="mc-router-ui:previous"

# ── Helpers ──────────────────────────────────────────────────────────────────
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { log "FEHLER: $*"; exit 1; }

# ═══════════════════════════════════════════════════════════════════════════
# 1. SOURCE VERZEICHNIS VORBEREITEN — clone or pull from GitHub
# ═══════════════════════════════════════════════════════════════════════════
mkdir -p "$SOURCE_DIR"

if [ -d "$SOURCE_DIR/.git" ]; then
    # Repo exists — pull latest
    log "Pull latest from GitHub ($GIT_BRANCH)..."
    cd "$SOURCE_DIR"
    git fetch origin "$GIT_BRANCH" 2>&1 || fail "git fetch fehlgeschlagen"

    OLD_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    git reset --hard "origin/$GIT_BRANCH" 2>&1 || fail "git reset fehlgeschlagen"
    NEW_COMMIT=$(git rev-parse --short HEAD)

    if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
        log "Bereits aktuell (commit $NEW_COMMIT). Keine Änderungen."
        # Continue anyway — user might want to rebuild even if no changes
    else
        log "Update: $OLD_COMMIT → $NEW_COMMIT"
        log "Letzte Commits:"
        git log --oneline -3 2>/dev/null || true
    fi
else
    # No git repo — clone fresh
    log "Kein Git-Repo gefunden. Clone von GitHub..."
    rm -rf "$SOURCE_DIR"/* 2>/dev/null || true
    git clone --branch "$GIT_BRANCH" "$GIT_REPO" "$SOURCE_DIR" 2>&1 || fail "git clone fehlgeschlagen"
    cd "$SOURCE_DIR"
    NEW_COMMIT=$(git rev-parse --short HEAD)
    log "Clone erfolgreich (commit $NEW_COMMIT)."
fi

# ═══════════════════════════════════════════════════════════════════════════
# 2. ALTES IMAGE ALS 'previous' MARKIEREN (für Rollback)
# ═══════════════════════════════════════════════════════════════════════════
if docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    log "Markiere aktuelles Image als 'previous' für Rollback..."
    docker tag "$IMAGE_NAME" "$IMAGE_PREV"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 3. NEUES IMAGE BAUEN
# ═══════════════════════════════════════════════════════════════════════════
log "Baue neues Image aus $SOURCE_DIR (commit $NEW_COMMIT)..."
docker build -t "$IMAGE_NAME" . 2>&1 || fail "docker build fehlgeschlagen"
log "Build erfolgreich."

# ═══════════════════════════════════════════════════════════════════════════
# 4. ALTES ARCHIV ROTIEREN
# ═══════════════════════════════════════════════════════════════════════════
mkdir -p "$TARGET_DIR"
if [ -f "$TARGET_FILE" ]; then
    log "Altes Archiv gefunden. Erstelle Backup: mc-router-ui.tar.gz.bak"
    mv "$TARGET_FILE" "$BACKUP_FILE"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 5. NEUES IMAGE ALS TARBALL SPEICHERN
# ═══════════════════════════════════════════════════════════════════════════
log "Speichere neues Docker-Image als Tarball..."
docker save "$IMAGE_NAME" | gzip > "$TARGET_FILE" || fail "docker save fehlgeschlagen"

# ═══════════════════════════════════════════════════════════════════════════
# 6. IMAGE NEU LADEN (Sicherheits-Reload)
# ═══════════════════════════════════════════════════════════════════════════
if [ -f "$TARGET_FILE" ]; then
    log "Lade MC-Router-UI Docker-Image..."
    docker load < "$TARGET_FILE"
fi

# ═══════════════════════════════════════════════════════════════════════════
# 7. CONTAINER NEU STARTEN (falls er läuft)
# ═══════════════════════════════════════════════════════════════════════════
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log "Container '$CONTAINER_NAME' gefunden. Starte neu..."
    docker restart "$CONTAINER_NAME" 2>&1 || fail "docker restart fehlgeschlagen"
    log "Container neu gestartet."
else
    log "Container '$CONTAINER_NAME' nicht gefunden — überspringe Restart."
    log "Starte ihn manuell:  docker run -d --name $CONTAINER_NAME ..."
fi

# ═══════════════════════════════════════════════════════════════════════════
# 8. FERTIG
# ═══════════════════════════════════════════════════════════════════════════
log "Fertig! Image gebaut aus commit $NEW_COMMIT, altes Image archiviert."

# Rollback-Hinweis anzeigen
log ""
log "Rollback (falls nötig):"
log "  docker tag $IMAGE_PREV $IMAGE_NAME && docker restart $CONTAINER_NAME"
