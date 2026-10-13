#!/usr/bin/env python3
"""Run the pyapi service daemon on your machine, against the controller in
the container.

    venv/bin/python harness/clixon_pyapi_local.py [--addr=HOST:PORT] [--no-dump]

Or the "Clixon: run pyapi locally" launch target, or ./up.sh, which ends by
running this.

This is the debug experience attaching to a daemon in the container cannot
give you: your own process, breakpoints anywhere including clixon/modules.py,
edit and restart in a second, and service code loaded straight from the
checkout instead of a copy installed in the image.

It inverts who is in charge. With PYAPI=external (the default) the backend
registers no service daemon at all, so nothing runs services except this
process. Stop it and `commit diff` waits for a transaction-actions-done that
never comes, failing after the service timeout.

The one piece of glue: clixon.sock.create_socket is AF_UNIX-only. So the container serves
its backend socket over TCP (CLICON_SOCK_FAMILY=IPv4) and create_socket is
swapped for a TCP one here. Nothing else in the pyapi cares; it only send()s
and recv()s.
"""
import os
import runpy
import socket
import sys

from clixon_env import BACKEND_ADDR, PACKAGES, PYAPI, python_paths

SERVER = os.path.join(PYAPI, "clixon_server.py")

for _p in reversed(python_paths()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import clixon.client  # noqa: E402  (all three after the path is set up)
import clixon.clixon  # noqa: E402
import clixon.sock  # noqa: E402

import clixon_service_dump  # noqa: E402


def tcp_socket(sockpath: str) -> socket.socket:
    """Stand-in for clixon.sock.create_socket that speaks TCP.

    Connects blocking and only then goes non-blocking: the original relies on
    an AF_UNIX connect completing at once, which a TCP connect does not
    promise even to loopback.
    """
    host, _, port = sockpath.rpartition(":")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host or "127.0.0.1", int(port)))
    sock.setblocking(False)
    return sock


def main(argv) -> int:
    addr = BACKEND_ADDR
    dump = True
    extra = []
    for arg in argv:
        if arg.startswith("--addr="):
            addr = arg.split("=", 1)[1]
        elif arg == "--no-dump":
            dump = False
        else:
            extra.append(arg)

    if ":" not in addr:
        print(f"--addr must be host:port, got {addr!r}", file=sys.stderr)
        return 1
    if not os.path.isdir(PACKAGES):
        print(f"no services at {PACKAGES} (set SNC_SERVICES, or run setup.sh)",
              file=sys.stderr)
        return 1

    # client.py and clixon.py each did `from clixon.sock import
    # create_socket`, so each holds its own reference to rebind.
    clixon.sock.create_socket = tcp_socket
    clixon.client.create_socket = tcp_socket
    clixon.clixon.create_socket = tcp_socket

    if dump:
        # Logs the config each service run generates, in full. See
        # clixon_service_dump.py.
        clixon_service_dump.install()

    print(f"pyapi -> {addr}, modules from {PACKAGES}"
          + ("" if dump else " (service dump off)"))
    print("Stopping this stops services running at all: the controller has no "
          "daemon of its own in this mode.")

    # clixon_server.py parses sys.argv at import time and has to run as
    # __main__ for its PIDLock/main block to execute.
    sys.argv = [SERVER, "-s", addr, "-m", PACKAGES,
                "-p", "/tmp/clixon-pyapi-local.pid"] + extra
    runpy.run_path(SERVER, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
