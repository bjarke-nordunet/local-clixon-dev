# Sourced by the scripts at the repo root. Reads .env the way compose does,
# except that a variable already set in the environment wins, so a one-off
# `PRIME_DEVICES=x ./up.sh` overrides the file.
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if [ -f "$ROOT/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        key=${line%%=*}
        value=${line#*=}
        value=${value%\"}; value=${value#\"}
        value=${value%\'}; value=${value#\'}
        [ -n "${!key+x}" ] || export "$key=$value"
    done < "$ROOT/.env"
fi

CONTAINER=${CONTAINER_NAME:-clixon-dev}
CLI_CFG=/usr/local/etc/clixon/controller.xml
