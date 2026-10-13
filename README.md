# local-clixon-dev

# Development setup created with Claude to be able to iterate service development faster locally

Develop and debug [clixon-controller](https://github.com/clicon/clixon-controller)
Python services on your laptop, against a real controller that is seeded with
your production config but **cannot reach a device**.

The services here are [SUNET/snc-services](https://github.com/SUNET/snc-services),
but nothing in the approach is specific to them.

## What you get

- **YANG validation of what services write**, including the device config
  under schema mount, against each device's real module revisions.
- **The controller's own `commit diff`** per device, without touching a device.
- **Real transaction semantics**: hook ordering, `transaction-error`
  propagation, the actions datastore round trip.
- **Breakpoints anywhere**, including `clixon/modules.py`, with service code
  loaded straight from your checkout.

Three properties of the controller make this work:

1. **`commit diff` and `commit local` never push** (push=NONE). Services run,
   the device diff is computed, and no device is contacted.
2. **Device YANG comes from disk.** The controller resolves device mount points
   from `CLICON_YANG_DOMAIN_DIR`, so a copy of the host's YANG cache is enough
   to bind and validate device config.
3. **A captured config is a datastore file.** Seeding is a copy.

There is no `ssh` binary in the image, only a stub that execs nothing. The
build fails if that ever changes. See [docs/how-it-works.md](docs/how-it-works.md)
for the reasoning, with source references.

## Requirements

- Docker with enough memory: about 2GB plus about 2GB per distinct device
  module set you prime (see [Priming devices](#priming-devices)).
- Python 3.11 or later (the pyapi needs it).
- ssh access to a clixon host, as root or as a user in the `clicon` group (to
  open the backend socket).
- VS Code is optional. Launch targets are provided in `.vscode/launch.json`.

## Quick start

```sh
./setup.sh                    # clones the checkouts, creates venv/ and .env
$EDITOR .env                  # CLIXON_HOST_SSH, PRIME_DEVICES

venv/bin/python harness/clixon_fetch.py all      # obtain everything from the host
./build.sh                    # compiles cligen, clixon, the controller: slow once
./up.sh --seed controller.xml # start the controller, then run the services
```

`up.sh` ends by running the pyapi in the foreground. In a second terminal:

```sh
./cli.sh
configure
set services <service> <instance> ...
commit diff        # run services, show the device diff, change nothing
validate local     # run services and validate, change nothing
commit local       # commit to the local datastore only
```

**Pin the checkouts.** `setup.sh` clones master. The point is to test against
the controller you deploy, so check `checkouts/clixon*` out at the versions
your host runs (`show version` in its CLI) and rebuild.

## Obtaining things from the clixon host

Everything taken from the host goes into `fetched/`, which is gitignored: it
holds real device config, secrets and vendor YANG.

```sh
venv/bin/python harness/clixon_fetch.py <what> [--host H] [--config PATH] [--ssh-option OPT]
```

| what | from the host | into | used for |
| --- | --- | --- | --- |
| `yang` | `CLICON_YANG_DOMAIN_DIR` | `fetched/mounts/` | binding and validating device config |
| `main` | `CLICON_YANG_MAIN_DIR` | `fetched/main/` | the host's own model set, including YANG installed outside your checkout |
| `revisions` | `get-device-schema` per device | `fetched/device-schemas.json` | pinning each device to the module revisions it really runs |
| `config` | the running datastore (`--xpath` for part) | `fetched/configs/<--name>.xml` | seeding the controller |
| `all` | all of the above | | |
| `status` | | | what is in `fetched/`, from which host, and when |

The paths are read from the host's controller config rather than assumed.
`yang` uses rsync when both ends have it, which makes refetching after a
device upgrade cheap. Fetch `yang` and `revisions` together; if they drift
apart, the controller reports it at startup.

## Running services: three modes

Set `PYAPI` in `.env` or on the command line.

- **`external`** (default): the container runs no service daemon. Services
  run in `harness/clixon_pyapi_local.py` on your machine, connected over TCP.
  Use `./up.sh`, or `./up.sh --no-pyapi` with the VS Code target **"Clixon:
  run pyapi locally"**. You get breakpoints anywhere, service code straight
  from your checkout, and a restart in a second. Without it, `commit diff`
  waits until the service timeout.
- **`internal`**: the backend runs the pyapi itself, as production does. Use
  this for scripted checks: `./up.sh --internal`.
- **`debugpy`**: like `internal`, under debugpy on port 5678. Attach with
  **"Clixon: attach to pyapi in container"**.

The service timeout is raised to 3600s at startup, so sitting on a
breakpoint does not fail the transaction.

Every run, the local pyapi logs each subtree a service generated, in full
(`harness/clixon_service_dump.py`). Turn it off with `--no-dump`.

### After an edit

| you changed | do |
| --- | --- |
| service Python, in `external` mode | restart the pyapi |
| service Python in other modes, or service YANG | `docker compose restart` |
| clixon C, controller YANG, or `controller/` | `./build.sh` |

The datastore survives restarts. `./up.sh --seed X.xml` reseeds it, and
`docker compose down -v` discards it.

## Priming devices

Device config is validated only for primed devices, the ones whose YANG is
mounted. Each distinct module set costs about 2GB resident (Juniper models), so
prime what you are testing, not the fleet:

```sh
./up.sh --devices 'router1,edge-*'            # at start (or PRIME_DEVICES in .env)
docker exec clixon-dev prime-device.sh router2  # add to a running controller
```

Unset, `PRIME_DEVICES` primes the first device in the datastore. Unpriming
needs a restart, because parsed YANG is never released.

`YANG_CONFIG=VALIDATE` (the default) checks types, ranges and must/when as
well as structure. `BIND` checks structure only, which is the controller's
default. VALIDATE needs `fetched/device-schemas.json`; without it, use BIND.

## Editor paths

After adding a service package, run `venv/bin/python harness/sync_paths.py`. It
regenerates the path list that VS Code (Pylance and the debugger) uses.

## Layout

```
compose.yml            the controller service; settings from .env
setup.sh build.sh up.sh cli.sh
controller/
  Dockerfile           builds from checkouts/, never from GitHub master
  env.sh               .env loader for the scripts
  image/               baked into the image: entrypoint, priming, no-ssh stub
harness/               Python, run on your machine (fetch, local pyapi)
.vscode/               launch targets and generated paths
docs/how-it-works.md   why each piece is the way it is
checkouts/             clixon, clixon-controller, clixon-pyapi, snc-services (gitignored)
fetched/               everything obtained from the host (gitignored)
```

## Settings

All in `.env` (see `.env.example`). Any of them can also be set for one run in
the environment.

| setting | default | |
| --- | --- | --- |
| `CLIXON_HOST_SSH` | | host to obtain things from |
| `PRIME_DEVICES` | first device | devices to mount YANG for |
| `SNC_SERVICES` | `checkouts/snc-services` | services checkout (bind-mounted, may be anywhere) |
| `PYAPI` | `external` | `external`, `internal` or `debugpy` |
| `YANG_CONFIG` | `VALIDATE` | `VALIDATE` or `BIND` |
| `SEED_CONFIG` | | file in `fetched/configs/` to seed from |
| `SERVICE_TIMEOUT` | `3600` | seconds |
| `CONTAINER_NAME` | `clixon-dev` | |
| `BACKEND_PORT`, `DEBUGPY_PORT` | `4535`, `5678` | loopback only |
