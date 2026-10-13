#!/usr/bin/env bash
# One-time setup: clone what is missing, create the venv, create .env.
#
#   ./setup.sh
#
# Safe to re-run: existing checkouts, venv and .env are left alone.
#
# Pin the clixon checkouts to the versions your controller host runs
# afterwards (check with `show version` in its CLI); master is a moving target.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
cd "$ROOT"

clone() {  # url dir
    if [ -e "$2" ]; then
        echo "have $2"
    else
        git clone "$1" "$2"
    fi
}

mkdir -p checkouts
clone https://github.com/clicon/clixon.git            checkouts/clixon
clone https://github.com/clicon/clixon-controller.git checkouts/clixon-controller
clone https://github.com/clicon/clixon-pyapi.git      checkouts/clixon-pyapi

# Bind-mounted rather than built in, so an existing checkout elsewhere works:
# set SNC_SERVICES in .env and nothing is cloned.
snc=$( { grep -E '^SNC_SERVICES=' .env 2>/dev/null || true; } | tail -n1 | cut -d= -f2-)
if [ -n "$snc" ]; then
    echo "using SNC_SERVICES=$snc"
else
    clone https://github.com/SUNET/snc-services.git checkouts/snc-services
fi

# clixon-pyapi needs Python 3.11 or later; macOS's own python3 is older.
if [ ! -d venv ]; then
    py=""
    for candidate in ${PYTHON:-} python3.14 python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c \
                'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
            py=$candidate
            break
        fi
    done
    [ -n "$py" ] || { echo "need Python >= 3.11 (set PYTHON=/path/to/python3)" >&2; exit 1; }
    "$py" -m venv venv
fi
# The pyapi itself comes from checkouts/clixon-pyapi via PYTHONPATH, not from
# the venv, so that F12 lands in source you can set breakpoints in.
# requests: snc-services' ipclos imports it.
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q requests

if [ ! -f .env ]; then
    cp .env.example .env
    echo "wrote .env -- set CLIXON_HOST_SSH in it"
fi

venv/bin/python harness/sync_paths.py

cat <<'EOF'

Next:
  $EDITOR .env                                 # CLIXON_HOST_SSH, PRIME_DEVICES
  venv/bin/python harness/clixon_fetch.py all  # obtain YANG and config from the host
  ./build.sh                                   # slow the first time
  ./up.sh --seed controller.xml                # start, then run the services
EOF
