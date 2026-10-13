#!/bin/sh
# Refuse to build or start an image that could reach a real device.
#
# This is the one property of the local controller that must not be allowed to
# rot. Everything else here is a convenience; this is the reason it is safe to
# point the thing at a production config and press commit. The Dockerfile runs
# it as a build gate and start.sh runs it again before the backend starts, so
# neither a careless edit nor a bind mount over /opt/no-ssh gets through.
#
# The invariant: clixon reaches a device only by exec'ing SSH_BIN, SSH_BIN is
# baked at build time by AC_PATH_PROG(SSH_BIN, ssh), and the only thing on PATH
# named ssh is a stub that execs nothing. Break any link in that chain and a
# `connection open` against the seeded production config would try to reach the
# real routers.
#
# Content properties rather than a checksum, so that editing no-ssh.sh for a
# good reason does not mean updating a hash in two places.
set -u

STUB=/opt/no-ssh/ssh
CONFIG_H=/build/clixon/include/clixon_config.h
fail=0

die() {
    echo "REFUSING: $*" >&2
    fail=1
}

# 1. SSH_BIN must be the stub. This is what clixon actually exec's; everything
#    else below is defence in depth.
if [ -r "$CONFIG_H" ]; then
    bin=$(sed -n 's/^#define SSH_BIN "\(.*\)"$/\1/p' "$CONFIG_H")
    [ "$bin" = "$STUB" ] || die "SSH_BIN is '$bin', expected '$STUB'"
else
    die "$CONFIG_H is missing, cannot verify SSH_BIN"
fi

# 2. The stub must exist and be a shell script.
if [ ! -f "$STUB" ]; then
    die "$STUB is missing"
else
    head -n1 "$STUB" | grep -q '^#!/bin/sh$' || die "$STUB is not a /bin/sh script"

    # 3. Small. A real ssh, or a wrapper that pulls one in, is not 2KB.
    size=$(wc -c < "$STUB")
    [ "$size" -lt 2048 ] || die "$STUB is ${size}B, too big to be the stub"

    # 4. Nothing in it can open a connection or hand off to something that can.
    #    grep -w so that `printf` is not mistaken for `perl` and so on.
    for bad in exec nc ncat socat telnet curl wget python python3 perl ssh; do
        grep -vE '^\s*#' "$STUB" | grep -qw -- "$bad" \
            && die "$STUB mentions '$bad' outside a comment"
    done
    grep -vE '^\s*#' "$STUB" | grep -q '/dev/tcp' \
        && die "$STUB uses /dev/tcp"

    # 5. It must actually fail. A stub that succeeded would leave the device in
    #    CONNECTING rather than closing it.
    if "$STUB" >/dev/null 2>&1; then
        die "$STUB exited 0; it must fail"
    fi
fi

# 6. No other ssh client anywhere on the filesystem, whatever it is called.
others=$(find / -xdev -type f -perm -u+x \
             \( -name ssh -o -name scp -o -name sftp -o -name ssh-agent \) \
             ! -path "$STUB" 2>/dev/null)
[ -z "$others" ] || die "other ssh binaries present:
$others"

# 7. And the package that would install one must not be there. Catches an
#    apt-get line added to the Dockerfile even if PATH order still favours the
#    stub at configure time.
if command -v dpkg >/dev/null 2>&1; then
    dpkg -l openssh-client 2>/dev/null | grep -q '^ii' \
        && die "openssh-client is installed, remove it from the spec."
fi

if [ "$fail" -ne 0 ]; then
    echo "" >&2
    echo "This image could contact real devices. See the NOTE in the" >&2
    echo "Dockerfile: the stub at $STUB is what makes that impossible." >&2
    exit 1
fi

echo "no-ssh: ok (SSH_BIN=$STUB, execs nothing, no other ssh present)"
