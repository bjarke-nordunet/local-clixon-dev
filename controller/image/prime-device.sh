#!/bin/sh
# Mount device YANG for one or more devices on a controller that is already
# running, without a restart.
#
#   docker exec clixon-dev prime-device.sh router1
#   docker exec clixon-dev prime-device.sh 'edge-*,router1' BIND
#
# Priming is not a startup-only trick: the mount is built when the device's
# hello is processed, so committing a module-set and opening the connection
# does it at any time. Only a restart will release the mounted devices' yang.
#
# Everything outside the named devices is left exactly as it is (ONLY_NAMED),
# so this will not re-disable, re-enable or re-mount the rest of the fleet.
set -u

CFG=/usr/local/etc/clixon/controller.xml
DOMAIN_DIR=/usr/local/share/controller/mounts
NCBASE='xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0"'

if [ $# -lt 1 ]; then
    echo "usage: $(basename "$0") <device-pattern>[,<pattern>...] [BIND|VALIDATE]" >&2
    exit 2
fi
DEVICES=$1
LEVEL=${2:-VALIDATE}

put() {
    label=$1
    out=$({
        printf '%s' "<rpc $NCBASE message-id=\"1\"><edit-config><target><candidate/></target><default-operation>merge</default-operation><config><devices xmlns=\"http://clicon.org/controller\">"
        cat
        printf '%s\n' '</devices></config></edit-config></rpc>]]>]]>'
        printf '%s\n' "<rpc $NCBASE message-id=\"2\"><commit/></rpc>]]>]]>"
    } | clixon_netconf -0 -f "$CFG" 2>&1)
    if printf '%s' "$out" | grep -q '<rpc-error'; then
        echo "$label: FAILED" >&2
        printf '%s' "$out" | grep -o '<error-message[^>]*>[^<]*' \
            | sed 's|<error-message[^>]*>|  |' >&2
        return 1
    fi
    return 0
}

body=$(YANG_CONFIG="$LEVEL" PRIME_DEVICES="$DEVICES" ONLY_NAMED=1 \
       python3 /usr/local/bin/derive-module-set.py "$DOMAIN_DIR")
if [ -z "$body" ]; then
    echo "no devices matched $DEVICES" >&2
    exit 1
fi

printf '%s\n' "$body" | put "enable" || exit 1
# One open per pattern: `connection open` takes a single shell pattern, not a
# list, so "router1,router2" as one argument matches nothing.
echo "$DEVICES" | tr ',' '\n' | while read -r pat; do
    [ -n "$pat" ] || continue
    echo "connection open $pat" | clixon_cli -f "$CFG" >/dev/null 2>&1 || true
done

# Back to disabled: datastore_diff_device() skips a device that is enabled but
# not OPEN, so `commit diff` would print nothing for it otherwise.
# Only the device names: a <name> at four spaces is the device's own, while a
# module's sits inside a <module> element on the same line. Matching any <name>
# picked up the module list and tried to disable a device called
# "ietf-inet-types".
for name in $(printf '%s' "$body" | grep -v '<module>' \
              | sed -n 's|^    <name>\(.*\)</name>$|\1|p'); do
    printf '  <device><name>%s</name><enabled>false</enabled></device>\n' "$name"
done | put "disable" || exit 1

echo "primed $DEVICES at $LEVEL"
