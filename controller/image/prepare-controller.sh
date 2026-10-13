#!/bin/sh
# Prepare a freshly started controller: mount the device YANG, disable every
# device, and raise the service timeout so a breakpoint does not kill the
# transaction.
#
# 0. Prime the YANG mounts. Without this the device config subtree is not
#    bound to YANG at all, and a service can write any XML into it with
#    `commit diff` and `validate local` both reporting success.
#    The mount, and the validation level, are taken from the in-memory device
#    handle, which only controller_connect() fills in - and that returns early
#    for a disabled device. So: enable the devices, open their connections
#    (each one fails against the stub ssh, but only after the handle has
#    cached its yang-lib and yang-config), then disable them again. The cache
#    survives the failed connection.
#
# 1. Disable devices. datastore_diff_device() skips a device that is enabled
#    but not OPEN, and nothing here can ever be OPEN. Left enabled, `commit
#    diff` runs the services, succeeds, and prints nothing. Disabled devices
#    are included in the diff.
#
# 2. Service timeout. The backend waits services/service-timeout for the
#    daemon to answer; a breakpoint is "not answered yet". It only takes
#    effect through a commit, hence the edit-config + commit below rather than
#    writing it into the datastore.
#
# Every phase is one whole-tree NETCONF edit-config. Piping `set` lines into
# clixon_cli costs one full datastore write per line, which against a large
# config turns a 30s startup into many minutes.
set -u

CFG=/usr/local/etc/clixon/controller.xml
SERVICE_TIMEOUT=${SERVICE_TIMEOUT:-3600}
YANG_CONFIG=${YANG_CONFIG:-BIND}
YANG_DOMAIN_DIR=/usr/local/share/controller/mounts
PRIME_DEVICES=${PRIME_DEVICES:-}

# Wait for the backend to accept RPCs. A large startup datastore takes a while
# to parse and bind.
i=0
while [ "$i" -lt 600 ]; do
    if clixon_netconf -0 -f "$CFG" </dev/null 2>/dev/null | grep -q session-id; then
        break
    fi
    sleep 2
    i=$((i + 2))
done

names=$(clixon_netconf -0 -f "$CFG" 2>/dev/null <<'NCEOF' | python3 -c 'import re,sys; print("\n".join(re.findall(r"<name>([^<]+)</name>", sys.stdin.read())))'
<hello xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><capabilities><capability>urn:ietf:params:netconf:base:1.0</capability></capabilities></hello>]]>]]>
<rpc xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" message-id="1"><get-config><source><running/></source><filter type="xpath" xmlns:ctrl="http://clicon.org/controller" select="/ctrl:devices/ctrl:device/ctrl:name"/></get-config></rpc>]]>]]>
NCEOF
)

# Each primed device costs memory, so an unset PRIME_DEVICES is one device, not
# the fleet.
if [ -z "$PRIME_DEVICES" ] && [ -n "$names" ]; then
    PRIME_DEVICES=$(printf '%s\n' "$names" | head -n1)
    echo "PRIME_DEVICES is unset; priming only $PRIME_DEVICES (the first device)"
fi

# One edit-config + commit. stdin is the children of <config>. An rpc-error is
# reported rather than swallowed.
HELLO='<hello xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><capabilities><capability>urn:ietf:params:netconf:base:1.0</capability></capabilities></hello>]]>]]>'
NCBASE='xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0"'

apply_config() {
    label=$1
    body=$(cat)
    out=$({
        printf '%s\n' "$HELLO"
        printf '%s\n' "<rpc $NCBASE message-id=\"1\"><discard-changes/></rpc>]]>]]>"
        printf '%s' "<rpc $NCBASE message-id=\"2\"><edit-config><target><candidate/></target><default-operation>merge</default-operation><config>"
        printf '%s' "$body"
        printf '%s\n' '</config></edit-config></rpc>]]>]]>'
        printf '%s\n' "<rpc $NCBASE message-id=\"3\"><commit/></rpc>]]>]]>"
    } | clixon_netconf -0 -f "$CFG" 2>&1)

    if printf '%s' "$out" | grep -q '<rpc-error'; then
        echo "$label: FAILED" >&2
        printf '%s' "$out" | grep -o '<error-message[^>]*>[^<]*' \
            | sed 's|<error-message[^>]*>|  |' >&2
        return 1
    fi
    return 0
}

# Phase A: a module-set for every profile and primed device, and each primed
# device enabled at the wanted YANG level, in one commit. yang-config only
# takes effect at the `connection open` below, so this commit cannot fail
# against a mount that does not exist yet. The whole <devices> body comes from
# derive-module-set.py: a device may appear only once per edit-config.
prime=$(YANG_CONFIG="$YANG_CONFIG" PRIME_DEVICES="${PRIME_DEVICES:-*}" \
        python3 /usr/local/bin/derive-module-set.py "$YANG_DOMAIN_DIR")

if [ -z "$prime" ]; then
    echo "no devices or profiles to prime"
else
    {
        echo '<devices xmlns="http://clicon.org/controller">'
        printf '%s\n' "$prime"
        echo '</devices>'
    } | apply_config "prime"
fi

if [ -z "$names" ]; then
    echo "no devices to prime or disable"
else
    # Blocking, and every device fails to connect - that is the point - so the
    # exit status is ignored. `connection open` takes one shell pattern, not a
    # list, hence one call per pattern.
    echo "$PRIME_DEVICES" | tr ',' '\n' | while read -r pat; do
        [ -n "$pat" ] || continue
        echo "connection open $pat" | clixon_cli -f "$CFG" >/dev/null 2>&1 || true
    done

    echo "primed YANG mounts for PRIME_DEVICES=$PRIME_DEVICES (yang-config $YANG_CONFIG)"
fi

# Phase B: disable every device again and raise the service timeout, in one
# commit.
{
    if [ -n "$names" ]; then
        echo '<devices xmlns="http://clicon.org/controller">'
        for name in $names; do
            printf '  <device><name>%s</name><enabled>false</enabled></device>\n' "$name"
        done
        echo '</devices>'
    fi
    printf '<services xmlns="http://clicon.org/controller"><service-timeout>%s</service-timeout></services>\n' \
           "$SERVICE_TIMEOUT"
} | apply_config "settle"

[ -n "$names" ] && echo "disabled $(echo "$names" | wc -l | tr -d ' ') devices so commit diff has something to show"
# up.sh waits for this line.
echo "service-timeout set to ${SERVICE_TIMEOUT}s so breakpoints do not fail the transaction"
