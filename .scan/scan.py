import os, pwd, sys
skip = {os.getpid(), os.getppid()}
found = {}
for pid in filter(str.isdigit, os.listdir('/proc')):
    if int(pid) in skip:
        continue
    try:
        with open('/proc/%s/cmdline' % pid, 'rb') as handle:
            cmdline = handle.read().decode('utf-8', 'replace').replace(chr(0), ' ')
        uid = os.stat('/proc/' + pid).st_uid
    except OSError:
        continue
    if 'app.main:app' in cmdline:
        found['uvicorn'] = uid
    elif 'api-binding' in cmdline:
        found['mc-router'] = uid
for name, uid in sorted(found.items()):
    print('  %-10s uid=%s (%s)' % (name, uid, pwd.getpwuid(uid).pw_name))
missing = {'uvicorn', 'mc-router'} - set(found)
wrong = {n: u for n, u in found.items() if u != 1000}
print('  RESULT:', 'PASS' if not (missing or wrong) else 'FAIL missing=%s wrong=%s' % (missing, wrong))
