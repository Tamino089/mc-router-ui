#!/bin/bash
# Fix the mc-router-ui crash loop:
#
#   sqlite3.OperationalError: attempt to write a readonly database
#     File "/srv/app/db/schema.py", line 165, in init_db
#
# Cause: the files in /data are not writable by the container's UID 1000 (user
# "app"). The database is readable, so every SELECT works, and the process only
# dies on the first write. The web UI therefore never binds port 8000, unRAID
# shows the container as unhealthy and "docker logs" shows supervisord giving up
# on 'web-ui'.
#
# Image 1.0.1 and later repair this by themselves on start, so you only need this
# script for older builds, or when the container is started with an explicit
# --user (then it cannot take ownership of /data from the inside).
#
# Run as root on the unRAID host:  bash /boot/fix-data-perms.sh

set -u

C="${CONTAINER:-mc-router-ui}"
UID_TARGET="${UID_TARGET:-1000}"
GID_TARGET="${GID_TARGET:-1000}"

line() { printf '\n=== %s ===\n' "$1"; }

docker inspect "$C" >/dev/null 2>&1 || { echo "!! no container named '$C'"; exit 1; }

line "locating the host path mounted at /data"
SRC_MODE="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}} {{.Mode}}{{end}}{{end}}' "$C")"
if [ -z "$SRC_MODE" ]; then
  echo "!! nothing is mounted at /data."
  echo "!! The container would then use its writable image layer, which cannot"
  echo "!! fail this way - so check the mount list yourself:"
  docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}}){{println}}{{end}}' "$C"
  exit 1
fi
SRC="${SRC_MODE% *}"
MODE="${SRC_MODE##* }"
echo "host path : $SRC"
echo "mount mode: $MODE"

case "$MODE" in
  *ro*)
    echo
    echo "!! /data is mounted READ-ONLY. chown cannot fix that."
    echo "!! In the unRAID Docker template, edit 'Data Directory' and remove the"
    echo "!! read-only flag (Mode must be rw), then restart the container."
    exit 1
    ;;
esac

[ -e "$SRC" ] || { echo "!! $SRC does not exist"; exit 1; }

line "current ownership (before)"
ls -ld "$SRC"
ls -la "$SRC"

line "what the container user can do right now"
docker exec -u "$UID_TARGET" "$C" python3 -c "
import os, stat
p = '/data/mcrouter-ui.db'
print('running as uid/gid:', os.getuid(), os.getgid())
for f in ('/data', p, p + '-wal', p + '-shm'):
    try:
        s = os.stat(f)
        print('  %-24s mode=%s owner=%s:%s' % (f, oct(stat.S_IMODE(s.st_mode)), s.st_uid, s.st_gid))
    except FileNotFoundError:
        print('  %-24s MISSING' % f)
try:
    open(p, 'r+b').close(); print('  open db for writing : OK')
except Exception as e:
    print('  open db for writing :', type(e).__name__, e)
try:
    open('/data/.write-probe', 'w').close(); os.remove('/data/.write-probe')
    print('  create file in /data: OK')
except Exception as e:
    print('  create file in /data:', type(e).__name__, e)
" 2>&1

line "fixing ownership and permissions"
chown -R "$UID_TARGET:$GID_TARGET" "$SRC" || { echo "!! chown failed - run this script as root"; exit 1; }
# chown does not add permission bits: a 444 database stays read-only for its new
# owner. u+rwX gives the owner rw on files and rwx on directories, and leaves
# executables executable.
chmod -R u+rwX "$SRC" || { echo "!! chmod failed"; exit 1; }
ls -ld "$SRC"
ls -la "$SRC"

line "restarting the container"
docker restart "$C" >/dev/null && echo "restarted"

line "waiting for the web UI"
HP="$(docker inspect -f '{{range $p, $conf := .NetworkSettings.Ports}}{{if eq $p "8000/tcp"}}{{(index $conf 0).HostPort}}{{end}}{{end}}' "$C")"
[ -n "$HP" ] || HP=8000
echo "probing http://127.0.0.1:$HP/healthz"
CODE=000
for _ in $(seq 1 20); do
  sleep 3
  CODE="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$HP/healthz" 2>/dev/null || echo 000)"
  [ "$CODE" = "200" ] && break
done

line "supervisor status inside the container"
docker exec "$C" supervisorctl -c /etc/supervisor/conf.d/mcrouter.conf status 2>&1

line "result"
echo "host port     : $HP"
echo "HTTP /healthz : $CODE"
if [ "$CODE" = "200" ]; then
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "$IP" ] || IP="<unraid-ip>"
  echo
  echo "Web UI is up:  http://$IP:$HP"
else
  echo
  echo "still failing - last 30 lines of the container log:"
  docker logs --tail 30 "$C" 2>&1
  echo
  echo "and what the app user can do with the database now:"
  docker exec -u "$UID_TARGET" "$C" python3 -c "
import os, stat
p = '/data/mcrouter-ui.db'
print('running as uid/gid:', os.getuid(), os.getgid())
try:
    s = os.stat(p)
    print('  %s mode=%s owner=%s:%s' % (p, oct(stat.S_IMODE(s.st_mode)), s.st_uid, s.st_gid))
    open(p, 'r+b').close(); print('  open for writing: OK')
except Exception as e:
    print(' ', type(e).__name__, e)
" 2>&1
fi
