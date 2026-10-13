# A clixon controller that cannot reach a device.
#
# Built from the checkouts in checkouts/ rather than from GitHub master: the
# point is to validate service output against the controller you deploy, and
# clixon, clixon-controller and clixon-pyapi move fast enough that master is a
# different program. Check the checkouts out at the versions your host runs.
# Only cligen is cloned here.
#
# Build context is the repo root - see .dockerignore there.
FROM debian:bookworm-slim

ARG CLIGEN_TAG=master
# configure calls version.sh, which runs `git describe` unless a .version file
# is present. .git is stripped from the build context, so build.sh reads the
# versions off the checkouts and passes them in.
ARG CLIXON_VERSION
ARG CONTROLLER_VERSION
# What the services need at run time beyond clixon. snc-services' filter
# package shells out to bgpq4, and ipclos imports requests.
ARG EXTRA_APT_PACKAGES="bgpq4 python3-requests"

# build-essential, not bare gcc: gcc alone has no libc headers or crt objects,
# and configure fails at "C compiler cannot create executables".
RUN apt-get update && apt-get install -y --no-install-recommends \
        bison build-essential ca-certificates curl flex git \
        libnghttp2-dev libssl-dev procps python3 python3-pip sudo \
        ${EXTRA_APT_PACKAGES} \
    && rm -rf /var/lib/apt/lists/*

# NOTE: openssh-client is deliberately NOT installed, and must not be. What
# stands in for it is no-ssh.sh, which execs nothing and can reach nothing, so
# device contact is impossible by construction rather than by a setting
# someone can flip.
#
# Why a stub rather than no ssh at all. Two reasons:
#
# 1. clixon reaches a device by exec'ing SSH_BIN, and configure resolves that
#    with AC_PATH_PROG(SSH_BIN, ssh) at build time. With no ssh on PATH it
#    bakes `#define SSH_BIN ""`, every connect fails in
#    clixon_client_connect_ssh's stat(), and that error path calls
#    clixon_client_disconnect() on a controller device handle: wrong handle
#    type, the magic check asserts, the backend aborts. `connection open`
#    would kill the backend.
#
# 2. Device YANG is only mounted as a side effect of a connection: the mount is
#    built in device_state_handler()'s CS_CONNECTING arm, while the device's
#    hello is processed. So the stub answers with a hello (see no-ssh.sh),
#    which is enough for the controller to build the mount from the local
#    module-set, and then drops the connection.
#
# Copied early because both ./configure runs below must find it on PATH.
COPY controller/image/no-ssh.sh /opt/no-ssh/ssh
RUN chmod 0755 /opt/no-ssh/ssh
# SSH_BIN is an absolute path baked at build time and stat()ed on every
# connection attempt, so the stub stays on PATH in the runtime image too.
ENV PATH="/opt/no-ssh:${PATH}"

# clixon's install creates directories owned by this user.
RUN useradd -m -d /home/clicon clicon

WORKDIR /build
# Full clone: cligen's configure runs `git describe` too, and a shallow clone
# has no tags.
RUN git clone -b ${CLIGEN_TAG} https://github.com/clicon/cligen.git \
    && cd cligen && ./configure && make -j"$(nproc)" && make install && ldconfig

COPY checkouts/clixon /build/clixon
RUN cd /build/clixon && echo "${CLIXON_VERSION}" > .version && ./configure && make -j"$(nproc)" && make install && ldconfig

COPY checkouts/clixon-controller /build/clixon-controller
RUN cd /build/clixon-controller && echo "${CONTROLLER_VERSION}" > .version && ./configure && make -j"$(nproc)" && make install && ldconfig
# extensions is commented out of the top-level SUBDIRS, so `make install` skips
# it. Service clispecs that set CLICON_PLUGIN to controller_cli_command need
# what it builds; without the .so the CLI refuses to start.
RUN cd /build/clixon-controller && make extensions && (cd extensions && make install) && ldconfig
# Stash the controller's own YANG as its install left it. start.sh seeds main/
# from fetched/main, which carries the host's clixon-controller.yang, and that
# copy would otherwise shadow the one this image was built from.
RUN cp -a /usr/local/share/controller/main /opt/checkout-main

COPY checkouts/clixon-pyapi /build/clixon-pyapi
# pip rather than setup.py install: it installs to dist-packages/clixon, a
# path with no version in it, which the debugpy pathMapping in
# .vscode/launch.json relies on.
RUN cd /build/clixon-pyapi \
    && pip3 install --break-system-packages --no-deps . \
    && cp clixon_server.py /usr/local/bin/

# For PYAPI=debugpy. Unused in the other modes.
RUN pip3 install --break-system-packages debugpy

# Overrides controller.xml. CLICON_CONFIGDIR is read after the main config, so
# this is the supported way to specialize it.
COPY controller/image/local.xml /usr/local/etc/clixon/controller/local.xml
COPY controller/image/start.sh controller/image/prepare-controller.sh \
     controller/image/derive-module-set.py controller/image/prime-device.sh \
     controller/image/assert-no-ssh.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/start.sh /usr/local/bin/prepare-controller.sh \
             /usr/local/bin/derive-module-set.py /usr/local/bin/prime-device.sh \
             /usr/local/bin/assert-no-ssh.sh

# Build gate: fails the build if anything in this image could reach a real
# device. start.sh runs the same check at every start, which also catches a
# bind mount over /opt/no-ssh. Do not remove either call.
RUN /usr/local/bin/assert-no-ssh.sh

ENTRYPOINT ["/usr/local/bin/start.sh"]
