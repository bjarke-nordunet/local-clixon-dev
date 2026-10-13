#!/usr/bin/env bash
# The controller's CLI. Interactive from a terminal, or commands on stdin:
#
#   ./cli.sh
#   printf 'configure\nset services ...\ncommit diff\n' | ./cli.sh
set -euo pipefail
. "$(dirname "$0")/controller/env.sh"

if [ -t 0 ]; then
    exec docker exec -it "$CONTAINER" clixon_cli -f "$CLI_CFG"
fi
exec docker exec -i "$CONTAINER" clixon_cli -f "$CLI_CFG"
