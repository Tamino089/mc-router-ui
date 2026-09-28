#!/bin/bash
# mc-router-ui triage for unRAID.
#
# Read-only: it changes nothing, it only prints evidence.
# Usage (on the unRAID host, via SSH or the unRAID terminal):
#   bash /boot/troubleshoot.sh
# or paste the whole file into the terminal.

C="${CONTAINER:-mc-router-ui}"
IMG="${IMAGE:-ghcr.io/tamino089/mc-router-ui:latest}"
DATA="${APPDATA:-/mnt/user/appdata/mc-router-ui}"

line() { printf '\n=== %s ===\n' "$1"; }

line "container state"
docker ps -a --filter "name=^/${C}$" \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}' 2>&1

docker inspect "$C" >/dev/null 2>&1 || {
  echo "!! No container named '$C' exists. Set CONTAINER=<name> and re-run."
  docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
  exit 1
}

line "exit code / restart count / network mode / container user"
docker inspect -f \
  'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restarts={{.RestartCount}}
started={{.State.StartedAt}} finished={{.State.FinishedAt}}
network={{.HostConfig.NetworkMode}} user={{.Config.User}}
cmd={{.Config.Cmd}}' "$C" 2>&1

line "docker-side start error (why the process never ran at all)"
docker inspect -f 'state.error={{.State.Error}}' "$C" 2>&1

line "mounts (does /data point where you think?)"
docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}}){{"\n"}}{{end}}' "$C" 2>&1

line "docker logs"
echo "1.0.1 and later log everything here, including Python tracebacks."
echo "Completely empty here  ==> supervisord never ran: Docker-level start failure,"
echo "  container stopped/never created, or a stale recreated container."
docker logs --tail 60 "$C" 2>&1

line "entrypoint's own lines (data directory repair)"
docker logs "$C" 2>&1 | grep '\[entrypoint\]' || echo "(no entrypoint lines)"

line "service log files (LOG_DIR, streamed to the container log above)"
if [ "$(docker inspect -f '{{.State.Running}}' "$C")" = "true" ]; then
  LOGDIR="$(docker exec "$C" sh -c 'printf %s "${LOG_DIR:-/var/log/supervisor}"' 2>/dev/null)"
  echo "LOG_DIR=$LOGDIR"
  if docker exec "$C" sh -c "test -d '$LOGDIR'" 2>/dev/null; then
    docker exec "$C" sh -c "ls -la '$LOGDIR' 2>&1"
    for f in web-ui-err.log mc-router-err.log web-ui.log; do
      printf '\n--- %s (tail 40) ---\n' "$f"
      docker exec "$C" sh -c "tail -n 40 '$LOGDIR/$f' 2>&1"
    done
  else
    echo "no $LOGDIR in this build: everything is in docker logs above"
  fi
  line "processes (read from /proc: the image has no ps)"
  docker exec "$C" python3 -c "
import os, pwd
for pid in sorted(filter(str.isdigit, os.listdir('/proc')), key=int):
    try:
        with open('/proc/%s/cmdline' % pid, 'rb') as handle:
            cmdline = handle.read().decode('utf-8', 'replace').replace(chr(0), ' ').strip()
        uid = os.stat('/proc/' + pid).st_uid
    except OSError:
        continue
    if ('app.main:app' in cmdline or 'api-binding' in cmdline
            or 'supervisord' in cmdline or 'tail -F' in cmdline):
        print('  pid %-6s uid=%s (%s)  %s' % (pid, uid, pwd.getpwuid(uid).pw_name, cmdline[:90]))
" 2>&1
else
  echo "container is not running; copying any log files out instead"
  TMP="$(mktemp -d)"
  if docker cp "$C:/var/log/supervisor/." "$TMP/" 2>/dev/null; then
    ls -la "$TMP"
    for f in web-ui-err.log web-ui.log mc-router-err.log; do
      printf '\n--- %s (tail 40) ---\n' "$f"
      tail -n 40 "$TMP/$f" 2>&1
    done
  else
    echo "no log files in the container layer."
    echo "Combined with empty 'docker logs' this means the container process never"
    echo "ran: look at state.error above, the port allocations, and the mounts."
  fi
fi

if [ "$(docker inspect -f '{{.State.Running}}' "$C")" = "true" ]; then
  line "identity and write access to /data (as the app user, uid 1000)"
  docker exec -u 1000 "$C" sh -c 'id; echo "--- /data ---"; ls -ld /data; ls -la /data | head -20; if touch /data/.write-test 2>/dev/null; then rm -f /data/.write-test; echo "WRITE: ok"; else echo "WRITE: DENIED  <-- this kills the web UI at import time"; fi; if [ -e /data/mcrouter-ui.db ]; then echo "DB: present"; ls -l /data/mcrouter-ui.db*; else echo "DB: missing (never created)"; fi' 2>&1
  line "what the app user can do with the database"
  docker exec -u 1000 "$C" python3 -c "
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
" 2>&1
else
  line "identity and write access to /data"
  echo "skipped: container is not running (start it, or use the manual run below)"
fi

line "host-side appdata ownership (the container repairs this to 1000:1000)"
ls -ld "$DATA" 2>&1
stat -c 'owner uid=%u gid=%g mode=%a  %n' "$DATA" 2>&1
ls -la "$DATA" 2>&1 | head -15

line "what is listening on the host"
ss -tlnp 2>/dev/null | grep -E ':(8000|8080|8090|25565)\b' || netstat -tlnp 2>/dev/null | grep -E ':(8000|8080|8090|25565)\b' || echo "no ss/netstat"

line "HTTP probes: 8000 with host networking, otherwise the mapped host port"
for p in 8000 8090; do
  printf 'port %s: ' "$p"
  curl -sS -m 5 -o /dev/null -w 'HTTP %{http_code}\n' "http://127.0.0.1:$p/healthz" 2>&1
done
printf 'readiness: '
curl -sS -m 8 "http://127.0.0.1:8000/readyz" 2>&1
echo

line "image actually in use"
docker image inspect "$IMG" -f 'id={{.Id}}
created={{.Created}}
user={{.Config.User}}
entrypoint={{.Config.Entrypoint}}
cmd={{.Config.Cmd}}' 2>&1

line "manual foreground run - prints the real error on your terminal"
echo "If the UI is still missing, run this and read the output:"
echo
echo "  docker run --rm -it --network host \\"
echo "    -v $DATA:/data \\"
echo "    -e ADMIN_PASSWORD='a-long-enough-password' \\"
echo "    --entrypoint uvicorn $IMG app.main:app --host 0.0.0.0 --port 8000"
echo
echo "Ctrl-C to stop. 'attempt to write a readonly database' or 'Failed to"
echo "initialize the database' confirms a /data permission problem."

line "done"
