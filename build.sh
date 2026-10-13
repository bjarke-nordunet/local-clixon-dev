#!/usr/bin/env bash
# Build the controller image from the checkouts in checkouts/.
#
#   ./build.sh                   # extra args go to `docker compose build`
#   ./build.sh --no-cache
#
# clixon and clixon-controller get their version from `git describe` at
# configure time, and .git is not in the build context, so the versions are
# read here and passed in as build args. Use this, not `docker compose build`.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)

# COPYed out of a build context rooted here, and the classic builder does not
# follow a symlink that leaves the context, so these have to be real
# directories. Checked up front rather than failing minutes into a build.
problems=()
for d in clixon clixon-controller clixon-pyapi; do
    if [ -L "$ROOT/checkouts/$d" ]; then
        problems+=("  checkouts/$d is a symlink -- the build context cannot follow it;"
                   "    clone it there instead of linking to it")
    elif [ ! -d "$ROOT/checkouts/$d" ]; then
        problems+=("  checkouts/$d is missing -- run ./setup.sh")
    fi
done
if [ ${#problems[@]} -gt 0 ]; then
    echo "The image is built from checkouts in this repo:" >&2
    printf '%s\n' "${problems[@]}" >&2
    exit 1
fi

CLIXON_VERSION=$(git -C "$ROOT/checkouts/clixon" describe --tags)
CONTROLLER_VERSION=$(git -C "$ROOT/checkouts/clixon-controller" describe --tags)
export CLIXON_VERSION CONTROLLER_VERSION

echo "clixon            $CLIXON_VERSION"
echo "clixon-controller $CONTROLLER_VERSION"
echo "clixon-pyapi      $(git -C "$ROOT/checkouts/clixon-pyapi" describe --tags)"
echo

cd "$ROOT"
exec docker compose build "$@"
