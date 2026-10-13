#!/bin/sh
# Container entrypoint. Installs the service packages from the mounted
# checkout, optionally seeds the startup datastore from an obtained config, and
# starts the backend. The pyapi is never launched here: in PYAPI=internal and
# PYAPI=debugpy the backend starts clixon_server.py itself via
# CONTROLLER_ACTION_COMMAND, and in the default PYAPI=external nothing in the
# container runs it at all.
#
# Packages are installed at start rather than baked into the image, so editing
# a service is a restart, not a rebuild.
set -u

MAIN=/usr/local/share/controller/main
MODULES=/usr/local/share/controller/modules
XMLDIR=/usr/local/share/controller
EXTENSIONS=/usr/local/share/controller/common/extensions
CLISPEC=/usr/local/lib/controller/clispec
DBDIR=/usr/local/var/controller
CFG=/usr/local/etc/clixon/controller.xml
CONFIGDIR=/usr/local/etc/clixon/controller
FETCHED=/fetched

DBG=${DBG:-0}
SEED=${SEED_CONFIG:-}
PYAPI=${PYAPI:-external}

# Before anything else: this image must not be able to reach a real device.
# Checked at build time too, but repeated here because a bind mount over
# /opt/no-ssh would get past a build gate.
/usr/local/bin/assert-no-ssh.sh || exit 1

mkdir -p "$MAIN" "$MODULES" "$CLISPEC" "$DBDIR" "$EXTENSIONS"

if [ -z "$(ls /usr/local/share/controller/mounts 2>/dev/null)" ]; then
    echo "WARNING: no device YANG in fetched/mounts; device config will not be" \
         "bound or validated (harness/clixon_fetch.py yang)" >&2
fi

# 1. The host's own YANG set, when it has been fetched. Brings in any model
#    installed on the host from outside the services checkout.
if [ -n "$(ls "$FETCHED"/main/*.yang 2>/dev/null)" ]; then
    echo "seeding $MAIN from fetched/main"
    cp -a "$FETCHED"/main/. "$MAIN"/
fi

# 2. The controller's own YANG back on top. fetched/main includes the host's
#    clixon-controller.yang, which would shadow the one this image was built
#    from, so a YANG change in the checkout would have no effect. Every
#    revision of each module goes first: two revisions of one module in $MAIN
#    is ambiguous.
if [ -d /opt/checkout-main ]; then
    n=0
    for f in /opt/checkout-main/*.yang; do
        [ -f "$f" ] || continue
        mod=$(basename "$f" | sed 's/[@.].*$//')
        rm -f "$MAIN/$mod.yang" "$MAIN/$mod"@*.yang
        cp -f "$f" "$MAIN"/
        n=$((n + 1))
    done
    echo "restored $n controller YANG file(s) from the checkout"
fi

# 3. The services checkout on top, so local edits win over what the host has.
#    Mirrors snc-services/scripts/install.sh, which flattens every
#    packages/<pkg>/ into one modules dir.
if [ -d /services/packages ]; then
    for pkg in /services/packages/*/; do
        name=$(basename "$pkg")

        if [ -d "$pkg/yang" ]; then
            cp -f "$pkg"yang/*.yang "$MAIN"/ 2>/dev/null
        fi

        mkdir -p "$MODULES/$name"
        for py in "$pkg"*.py; do
            [ -f "$py" ] || continue
            case $(basename "$py") in test_*) continue ;; esac
            cp -f "$py" "$MODULES/$name"/
        done

        if [ -d "$pkg/xml" ]; then
            cp -f "$pkg"xml/*.xml "$XMLDIR"/ 2>/dev/null
        fi

        if [ -d "$pkg/cli" ]; then
            cp -f "$pkg"cli/*.cli "$CLISPEC"/ 2>/dev/null
            mkdir -p "$MODULES/$name/cli"
            for py in "$pkg"cli/*.py; do
                [ -f "$py" ] || continue
                case $(basename "$py") in test_*) continue ;; esac
                cp -f "$py" "$MODULES/$name/cli"/
            done
        fi
    done
    # Not $MAIN: these augment vendor models, and anything in
    # CLICON_YANG_MAIN_DIR is loaded eagerly at startup, where those imports
    # cannot resolve. They belong beside the controller's own extensions,
    # which are resolved inside the mount domain.
    cp -f /services/extensions/*.yang "$EXTENSIONS"/ 2>/dev/null
    echo "installed $(ls -d /services/packages/*/ 2>/dev/null | wc -l) packages from /services"
else
    echo "WARNING: no packages/ in /services; no services installed" \
         "(set SNC_SERVICES, or run setup.sh)" >&2
fi

# 4. Datastore. An obtained config is <config>...</config>, which is what a
#    clixon datastore file is, so seeding is a copy (local.xml turns
#    XMLDB_MULTI off for this).
#    $DBDIR is a named volume, so running_db outlives the container. `-s init`
#    would write an empty datastore straight back over it, so an unseeded
#    restart keeps the last run's config instead, and only starts empty when
#    there is nothing to keep.
STARTUP="-s init"
if [ -n "$SEED" ]; then
    if [ -f "$FETCHED/configs/$SEED" ]; then
        echo "seeding startup datastore from fetched/configs/$SEED"
        cp "$FETCHED/configs/$SEED" "$DBDIR/startup_db"
        STARTUP="-s startup"
    else
        echo "SEED_CONFIG=$SEED not found in fetched/configs - starting empty" >&2
    fi
elif [ -s "$DBDIR/running_db" ]; then
    echo "keeping the running datastore from the last run (set SEED_CONFIG to reseed)"
    STARTUP="-s running"
fi

# CLICON_CONFIGDIR is read after controller.xml, so this overrides it. Written
# fresh each start.
rm -f "$CONFIGDIR/zz-pyapi.xml"
case "$PYAPI" in
    internal)
        echo "pyapi run by the backend"
        ;;
    debugpy)
        wait_flag=""
        if [ "${DEBUGPY_WAIT:-false}" = "true" ]; then
            wait_flag=" --wait-for-client"
        fi
        echo "pyapi under debugpy on 5678 (wait-for-client: ${DEBUGPY_WAIT:-false})"
        cat > "$CONFIGDIR/zz-pyapi.xml" <<XMLEOF
<?xml version="1.0" encoding="utf-8"?>
<clixon-config xmlns="http://clicon.org/config">
  <CONTROLLER_ACTION_COMMAND xmlns="http://clicon.org/controller-config">/usr/bin/python3 -m debugpy --listen 0.0.0.0:5678$wait_flag /usr/local/bin/clixon_server.py -f /usr/local/etc/clixon/controller.xml</CONTROLLER_ACTION_COMMAND>
</clixon-config>
XMLEOF
        ;;
    external)
        # Empty CONTROLLER_ACTION_COMMAND: controller_backend.c registers no
        # services process at all, so nothing runs services unless you start
        # one yourself.
        #
        # The socket moves to TCP because the pyapi is AF_UNIX-only and a unix
        # socket does not cross the Docker Desktop VM boundary.
        echo "no in-container pyapi; backend socket on TCP 4535"
        cat > "$CONFIGDIR/zz-pyapi.xml" <<'XMLEOF'
<?xml version="1.0" encoding="utf-8"?>
<clixon-config xmlns="http://clicon.org/config">
  <CLICON_SOCK_FAMILY>IPv4</CLICON_SOCK_FAMILY>
  <CLICON_SOCK>0.0.0.0</CLICON_SOCK>
  <CLICON_SOCK_PORT>4535</CLICON_SOCK_PORT>
  <CONTROLLER_ACTION_COMMAND xmlns="http://clicon.org/controller-config"></CONTROLLER_ACTION_COMMAND>
</clixon-config>
XMLEOF
        ;;
    *)
        echo "PYAPI=$PYAPI: expected external, internal or debugpy" >&2
        exit 1
        ;;
esac

# Mounts the device YANG (without which nothing validates what services write
# into device config) and then disables every device, which `commit diff`
# needs to show anything at all. Backgrounded because it has to wait for the
# backend to accept RPCs.
if [ "${DISABLE_DEVICES:-true}" = "true" ]; then
    YANG_CONFIG="${YANG_CONFIG:-BIND}" PRIME_DEVICES="${PRIME_DEVICES:-}" \
        /usr/local/bin/prepare-controller.sh &
fi

echo "starting clixon_backend $STARTUP"
# -F foreground, -D debug level, -l e log to stderr so docker logs shows it.
/usr/local/sbin/clixon_backend -F -D "$DBG" -f "$CFG" -l e $STARTUP &
BACKEND=$!

# Keep the container alive after a backend crash so its logs can be read.
wait $BACKEND
echo "clixon_backend exited ($?)" >&2
exec /bin/sleep infinity
