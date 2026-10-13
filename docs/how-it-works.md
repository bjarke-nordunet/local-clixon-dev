# How it works

## Get started

Once:

```sh
./setup.sh                                    # checkouts, venv/, .env
$EDITOR .env                                  # CLIXON_HOST_SSH, PRIME_DEVICES
venv/bin/python harness/clixon_fetch.py all   # YANG, revisions, config from the host
./build.sh                                    # slow the first time
```

- Pin `checkouts/clixon*` to the versions the host runs (`show version` in
  its CLI), then `./build.sh` again.

Each session:

```sh
./up.sh --seed controller.xml   # start, then run the services in the foreground
./cli.sh                        # in a second terminal
configure
set services <service> <instance> ...
commit diff                     # services run, device diff shown, nothing changes
```

- `./up.sh` without `--seed` keeps the datastore from the last run.
- To debug, start with `./up.sh --no-pyapi` and launch **"Clixon: run pyapi
  locally"** in VS Code.
- After editing service Python, restart the pyapi. After editing service YANG,
  `docker compose restart`. After editing clixon C or `controller/`,
  `./build.sh`.
- An empty `commit diff` can mean the service never ran: check the pyapi log.

The rest of this page is why it works this way.

## Validating services without real devices

- `commit diff` and `commit local` use push=NONE: services run and the device
  diff is computed, but nothing is pushed.
- Device YANG is read from disk (`CLICON_YANG_DOMAIN_DIR`), copied from the
  host with `clixon_fetch.py yang`. No `get-schema` against devices.
- Each device is pinned to the module revisions it really runs
  (`fetched/device-schemas.json`, from `get-device-schema` on the host).
- A config obtained from the host is a datastore file, so seeding is a copy
  (`CLICON_XMLDB_MULTI` is off in `local.xml` for this).
- `YANG_CONFIG=VALIDATE` is used to check types, ranges and must/when, rather than the controller's default `BIND`.

## Priming device YANG

- The YANG mount is built only while a device connects, so at startup
  `prepare-controller.sh`:
  1. commits a module-set per primed device (`derive-module-set.py`),
  2. runs `connection open`, which reaches the ssh stub, receives a hello and
     builds the mount,
  3. disables every device again.
- Only primed devices are validated (`PRIME_DEVICES`, or `prime-device.sh` at
  runtime).
- Each distinct module set costs about 2GB of memory. Removing primed devices
  needs a restart.

## Devices must be disabled

- `commit diff` skips devices that are enabled but not connected, so with
  devices enabled, the diff is always empty.
- Disabled devices are included in the diff.

## It cannot reach a device

- There is no ssh in the image. `SSH_BIN` points at `no-ssh.sh`, which prints
  a NETCONF hello and exits non-zero.
- `assert-no-ssh.sh` runs at build time and at every start. It refuses if
  `SSH_BIN` points anywhere else, if the stub can exec or open a connection,
  or if any real ssh or `openssh-client` is present.
- Every device is disabled, and nothing attempts to connect unless you run
  `connection open`.
- The container still has outbound network, e.g. for service code calls.

## Services run on your machine (PYAPI=external)

- The backend runs no service daemon of its own.
- Its socket is served over TCP on loopback.
- `clixon_pyapi_local.py` patches pyapi to use TCP and loads the services
  from your checkout, for example in a vscode debugger.
- The service timeout is raised to 3600s, so a breakpoint doesn't fail the
  transaction.