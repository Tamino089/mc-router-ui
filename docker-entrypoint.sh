#!/bin/sh
# Container entrypoint.
#
# Both services run as the unprivileged user "app" (UID 1000), but /data is a bind
# mount whose ownership is decided by the host. A data directory that UID 1000
# cannot write makes SQLite fail with "attempt to write a readonly database"
# after a successful read, so the web UI exits before it can serve anything and
# supervisord gives up on it. Repair ownership first, drop privileges, then hand
# over to the command (supervisord).
#
# The services log to files under LOG_DIR and this script streams those files to
# the container log, because a non-root user cannot re-open /dev/stdout in Docker.
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

LOG_DIR="${LOG_DIR:-/var/log/supervisor}"

use_writable_log_dir() {
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    if [ ! -w "$LOG_DIR" ]; then
        LOG_DIR=/tmp/mc-router-logs
        mkdir -p "$LOG_DIR" 2>/dev/null || true
        log "$LOG_DIR is not writable, using /tmp/mc-router-logs for service logs"
    fi
    for name in mc-router.log mc-router-err.log web-ui.log web-ui-err.log; do
        : >> "$LOG_DIR/$name" 2>/dev/null || true
    done
    export LOG_DIR
}

# Streams the service logs to the container log. `tail` writes to the stdout it
# inherited, which any user may do; it never opens /dev/stdout itself. The
# subshell exits straight away, so the streamer is reparented to PID 1 instead of
# becoming a stray child that supervisord reports as "reaped unknown pid".
start_log_streamer() {
    (
        sh -c "while :; do tail -F -q -n +1 \"\$@\"; sleep 1; done" sh \
            "$LOG_DIR/mc-router.log" "$LOG_DIR/mc-router-err.log" \
            "$LOG_DIR/web-ui.log" "$LOG_DIR/web-ui-err.log" &
    )
}

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
    use_writable_log_dir
    chown -R "$APP_UID:$APP_GID" "$LOG_DIR" 2>/dev/null || true
    log "starting as $APP_USER ($APP_UID:$APP_GID)"
    # Drop privileges here so supervisord itself never runs as root. Supplementary
    # groups are kept on purpose, so --group-add for the Docker socket still works.
    # The log streamer is started by the same process after the switch, so it runs
    # unprivileged too.
    exec python3 -c '
import os, sys
gid, uid, log_dir = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
logs = [os.path.join(log_dir, name) for name in
        ("mc-router.log", "mc-router-err.log", "web-ui.log", "web-ui-err.log")]
os.setgid(gid)
os.setuid(uid)
stream = "while :; do tail -F -q -n +1 \"$@\"; sleep 1; done"
pid = os.fork()
if pid == 0:
    # Grandchild becomes the streamer and is reparented to PID 1; this process
    # exits at once so supervisord never sees it as a stray child.
    if os.fork() == 0:
        os.execvp("sh", ["sh", "-c", stream, "sh"] + logs)
    os._exit(0)
os.waitpid(pid, 0)
os.execvp(sys.argv[4], sys.argv[4:])
' "$APP_GID" "$APP_UID" "$LOG_DIR" "$@"
fi

# Started with an explicit user (docker run --user, compose "user:", PUID/PGID
# style). Ownership cannot be repaired from here, so report it instead.
use_writable_log_dir
start_log_streamer

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
