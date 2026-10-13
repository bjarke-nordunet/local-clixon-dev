#!/bin/sh
# Stand-in for ssh in the local controller image. Reaches nothing: it speaks
# just enough NETCONF for the controller to mount the device's YANG off local
# files, then goes away.
#
# Why it says anything at all rather than exiting immediately:
# controller_yang_mount() serves device YANG out of the in-memory device
# handle, and the only code that fills the mount in is the CS_CONNECTING arm of
# device_state_handler(), which runs when the device's hello arrives. That arm
# takes the local-file path only when the device does NOT announce
# :netconf-monitoring - so the hello below announces base:1.0 and nothing
# else. The controller then resolves the device-profile's module-set against
# CLICON_YANG_DOMAIN_DIR/<device-domain> and calls controller_mount_yspec_set().
# No get-schema, no device.
#
# NETCONF_SSH_EOM framing is set before the connect, hence the ]]>]]> terminator.
# device_recv_hello() wants the name "hello", the base namespace, and a
# capabilities element; session-id is not required.
#
# The sleep is not cosmetic. Immediately after processing the hello the
# controller writes its own hello back (clixon_client_hello). Exiting first
# would make that a write to a closed socket. Once it has, the process exits
# and the EOF closes the device the ordinary way - device_input_cb reports
# what it reads on the stderr socketpair, which is the message below.
printf '%s' '<hello xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"><capabilities><capability>urn:ietf:params:netconf:base:1.0</capability></capabilities></hello>]]>]]>'
sleep 3
echo "local-controller: device contact is not possible in this image" >&2
exit 1
