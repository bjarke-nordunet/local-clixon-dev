#!/usr/bin/env python3
"""Where this repo is, and the settings that are not in git.

Config goes in .env at the repo root. The environment wins over .env, so a
one-off run can override without editing the file:

    CLIXON_HOST_SSH=other-host venv/bin/python harness/clixon_fetch.py config
"""
import os
from typing import Dict, List, Optional

# harness/env.py -> harness -> the repo root.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT, ".env")

# Everything obtained from the clixon host lives under here (gitignored).
FETCHED = os.path.join(ROOT, "fetched")
CONFIGS = os.path.join(FETCHED, "configs")


def _read() -> Dict[str, str]:
    """KEY=VALUE lines from .env: comments, blank lines and quoted values,
    the subset of the format docker compose also reads."""
    values: Dict[str, str] = {}
    try:
        with open(ENV_FILE) as fh:
            lines = fh.readlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


_VALUES = _read()


def setting(name: str, default: str = "") -> str:
    """A value from the environment, else .env, else the default."""
    return os.environ.get(name) or _VALUES.get(name, default)


def _path(var: str, default: str) -> str:
    value = setting(var)
    if value:
        return os.path.abspath(os.path.join(ROOT, os.path.expanduser(value)))
    return os.path.join(ROOT, default)


# Bind-mounted into the container, so it may live anywhere (SNC_SERVICES).
SERVICES = _path("SNC_SERVICES", "checkouts/snc-services")
PACKAGES = os.path.join(SERVICES, "packages")

# Built into the image, so it has to be the real directory under checkouts/.
PYAPI = os.path.join(ROOT, "checkouts", "clixon-pyapi")

BACKEND_ADDR = "127.0.0.1:" + setting("BACKEND_PORT", "4535")


def package_dirs() -> List[str]:
    """Every directory the controller puts on sys.path, in load order.

    snc-services' install.sh flattens packages/<pkg>/ into one modules dir,
    and the pyapi appends every directory holding a .py to sys.path, so both
    "from filter import bgpq4" and "from lib import create_peer" resolve.
    Mirror that: the packages dir, each package, and each package's cli/.
    """
    if not os.path.isdir(PACKAGES):
        return []
    dirs = [PACKAGES]
    for entry in sorted(os.scandir(PACKAGES), key=lambda e: e.name):
        if not entry.is_dir() or entry.name.startswith((".", "__")):
            continue
        cli = os.path.join(entry.path, "cli")
        if os.path.isdir(cli) and any(n.endswith(".py") for n in os.listdir(cli)):
            dirs.append(cli)
        dirs.append(entry.path)
    return dirs


def python_paths() -> List[str]:
    """Everything that goes on sys.path to run services off a controller:
    the harness, the pyapi source, and the packages."""
    paths = [os.path.join(ROOT, "harness"), PYAPI] + package_dirs()
    return [p for p in paths if os.path.isdir(p)]


def controller_host(explicit: Optional[str] = None) -> str:
    """The clixon host to ssh to: the command line, else CLIXON_HOST_SSH."""
    host = explicit or setting("CLIXON_HOST_SSH")
    if not host:
        raise SystemExit(
            "No clixon host to obtain from. Pass --host, or set "
            f"CLIXON_HOST_SSH in {ENV_FILE} (copy .env.example to .env).")
    return host
