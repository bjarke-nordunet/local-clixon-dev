#!/usr/bin/env bash
# (Re)start the controller, wait until it is ready, then run the services.
#
#   ./up.sh                         # keep the datastore, run the pyapi here
#   ./up.sh --seed controller.xml   # fresh datastore from fetched/configs/
#   ./up.sh --devices 'r1,edge-*'   # which devices get YANG (PRIME_DEVICES)
#   ./up.sh --no-pyapi              # stop once ready, e.g. to start the
#                                   # pyapi from VS Code instead
#   ./up.sh --internal              # the container runs the services itself
#
# Ctrl-C stops the pyapi, and with it every service run; the controller keeps
# running. `docker compose down` stops it.
set -euo pipefail
. "$(dirname "$0")/controller/env.sh"

RUN_PYAPI=1
TIMEOUT=600

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --seed)      export SEED_CONFIG=${2:?--seed needs a file name}; shift 2 ;;
        --devices)   export PRIME_DEVICES=${2:?--devices needs a list}; shift 2 ;;
        --no-pyapi)  RUN_PYAPI=0; shift ;;
        --internal)  export PYAPI=internal; RUN_PYAPI=0; shift ;;
        -h|--help)   usage ;;
        *)           echo "unknown option: $1" >&2; usage 2 ;;
    esac
done
export PYAPI=${PYAPI:-external}
[ "$PYAPI" = external ] || RUN_PYAPI=0

say() { printf '\n== %s\n' "$*"; }

if [ -n "${SEED_CONFIG:-}" ] && [ ! -f "$ROOT/fetched/configs/$SEED_CONFIG" ]; then
    echo "no fetched/configs/$SEED_CONFIG; have:" >&2
    ls "$ROOT/fetched/configs" 2>/dev/null | sed 's/^/   /' >&2
    exit 1
fi
[ -d "$ROOT/fetched/mounts" ] \
    || echo "warning: no fetched/mounts; device config will not be validated" >&2

# A pyapi from an earlier run holds a pid lock and a dead socket.
if pgrep -f harness/clixon_pyapi_local.py >/dev/null; then
    say "stopping the pyapi from an earlier run"
    pkill -f harness/clixon_pyapi_local.py || true
    sleep 1
fi

say "recreating $CONTAINER (PYAPI=$PYAPI${SEED_CONFIG:+, seeding from $SEED_CONFIG})"
cd "$ROOT"
docker compose up -d --force-recreate

# Wait for the last line prepare-controller.sh prints, or anything that means
# the start failed.
waited=0
while :; do
    logs=$(docker logs "$CONTAINER" 2>&1 || true)
    if printf '%s' "$logs" | grep -q "service-timeout set to"; then
        break
    fi
    if printf '%s' "$logs" | grep -qE "clixon_backend exited|Daemon error|REFUSING"; then
        printf '%s\n' "$logs" | tail -15
        echo "controller failed to start (full log: docker logs $CONTAINER)" >&2
        exit 1
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != true ]; then
        printf '%s\n' "$logs" | tail -15
        echo "container stopped (full log: docker logs $CONTAINER)" >&2
        exit 1
    fi
    if [ "$waited" -ge "$TIMEOUT" ]; then
        printf '%s\n' "$logs" | tail -15
        echo "not ready after ${TIMEOUT}s (still starting? docker logs -f $CONTAINER)" >&2
        exit 1
    fi
    sleep 3
    waited=$((waited + 3))
done
docker logs "$CONTAINER" 2>&1 \
    | grep -E "installed .* packages|PRIME_DEVICES|primed YANG|not mounting|WARNING" \
    | sed 's/^/   /'
echo "   ready after ${waited}s"

if [ "$RUN_PYAPI" = 0 ]; then
    say "controller ready"
    [ "$PYAPI" = external ] && {
        echo "   start the services with VS Code: \"Clixon: run pyapi locally\""
        echo "   or: venv/bin/python harness/clixon_pyapi_local.py"
    }
    echo "   CLI: ./cli.sh"
    exit 0
fi

say "starting the pyapi (Ctrl-C stops it, and with it every service run)"
echo "   in another terminal: ./cli.sh"
export PYTHONUNBUFFERED=1
exec venv/bin/python harness/clixon_pyapi_local.py
