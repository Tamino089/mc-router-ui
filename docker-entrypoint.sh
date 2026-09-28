#!/bin/sh
# Container entrypoint.
#
# Both services run as the unprivileged user "app" (UID 1000), but /data is a bind
# mount whose ownership is decided by the host. A data directory that UID 1000
# cannot write makes SQLite fail with "attempt to write a readonly database"
# after a successful read, so the web UI exits before it can serve anything and
# supervisord gives up on it. Repair ownership first, then drop privileges and
# hand over to the command (supervisord).
set -eu

APP_USER="${APP_USER:-app}"
CONF="/etc/supervisor/conf.d/mcrouter.conf"
log() { printf '[entrypoint] %s\n' "$*"; }

if [ "$#" -eq 0 ]; then
    log "no command given, starting supervisord"
    set -- /usr/bin/supervisord -c "$CONF"
fi

APP_UID="$(id -u "$APP_USER" 2>/dev/null || echo 1000)"
APP_GID="$(id -g "$APP_USER" 2>/dev/null || echo 1000)"

repair_data_dir() {
    [ -d /data ] || return 0
    owner="$(stat -c '%u:%g' /data 2>/dev/null || echo '')"
    if [ "$owner" != "$APP_UID:$APP_GID" ]; then
        log "/data is owned by ${owner:-an unknown user}, taking ownership for $APP_USER"
        chown -R "$APP_UID:$APP_GID" /data || log "WARNING: chown /data failed"
    fi
    # chown keeps the existing mode, and a 0444 database stays read-only for its
    # new owner. X adds execute only on directories and already-executable files.
    chmod -R u+rwX /data || true
}

if [ "$(id -u)" = "0" ]; then
    repair_data_dir
    log "starting as $APP_USER ($APP_UID:$APP_GID)"
    # Drop privileges here so supervisord itself never runs as root. Supplementary
    # groups are kept on purpose, so --group-add for the Docker socket still works.
    exec python3 -c '
import os, sys
gid, uid = int(sys.argv[1]), int(sys.argv[2])
os.setgid(gid)
os.setuid(uid)
try:
    os.execvp(sys.argv[3], sys.argv[3:])
except OSError as exc:
    sys.stderr.write("[entrypoint] cannot execute %s: %s\n" % (sys.argv[3], exc))
    raise SystemExit(127)
' "$APP_GID" "$APP_UID" "$@"
fi

# Started with an explicit user (docker run --user, compose "user:", PUID/PGID
# style), so ownership cannot be repaired from in here. Report it instead.
if [ -d /data ]; then
    if touch /data/.entrypoint-write-test 2>/dev/null; then
        rm -f /data/.entrypoint-write-test
        log "running as uid $(id -u), /data is writable"
    else
        log "ERROR: /data is not writable by uid $(id -u), the web UI will exit"
        log "       run the container as root so it can repair ownership itself,"
        log "       or on the host: chown -R $APP_UID:$APP_GID <appdata directory>"
    fi
fi

exec "$@"
