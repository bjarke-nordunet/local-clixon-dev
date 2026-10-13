#!/usr/bin/env bash
# List service instances whose created paths touch primed (YANG-mounted) devices.
#
#   ./primed-services.sh                          # PRIME_DEVICES from .env
#   PRIME_DEVICES='dk-uni,fi-*' ./primed-services.sh
set -euo pipefail
. "$(dirname "$0")/controller/env.sh"

primed=${PRIME_DEVICES:-}
echo "primed: ${primed:-none}"
[ -n "$primed" ] || exit 0

docker exec -i -e PRIMED="$primed" "$CONTAINER" python3 - <<'EOF'
import fnmatch, os, re
pats = os.environ["PRIMED"].split(",")
db = open("/usr/local/var/controller/running_db").read()
start = db.index('<services xmlns="http://clicon.org/controller">') + 1
svcs = db[start:db.index('<devices xmlns="http://clicon.org/controller">')]
for m in re.finditer(r'<([\w-]+) xmlns="[^"]+">\s*<([\w-]+)>([^<]+)</\2>(.*?)</\1>', svcs, re.S):
    devs = set(re.findall(r'/devices/device\[name="([^"]+)"\]', m.group(4)))
    hit = sorted(d for d in devs if any(fnmatch.fnmatch(d, p) for p in pats))
    if hit:
        rest = len(devs) - len(hit)
        print(f"{m.group(1)} {m.group(3)}: {', '.join(hit)}" + (f"  (+{rest} unprimed)" if rest else ""))
EOF
