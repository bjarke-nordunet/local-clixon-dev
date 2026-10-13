#!/usr/bin/env python3
"""Obtain things from a clixon host into fetched/.

    venv/bin/python harness/clixon_fetch.py all          # everything below
    venv/bin/python harness/clixon_fetch.py yang         # device YANG -> fetched/mounts/
    venv/bin/python harness/clixon_fetch.py main         # controller YANG -> fetched/main/
    venv/bin/python harness/clixon_fetch.py revisions    # -> fetched/device-schemas.json
    venv/bin/python harness/clixon_fetch.py config [--name N] [--xpath X]
                                                         # -> fetched/configs/N.xml
    venv/bin/python harness/clixon_fetch.py status       # what is here, from where, how old

The host is CLIXON_HOST_SSH from .env, or --host. Everything goes over plain
ssh with BatchMode (so a passphrase-protected key needs an agent); for user,
port and key, prefer a Host entry in ~/.ssh/config. Paths on the host are read
from its controller config (--config) rather than assumed. Nothing is written
on the host.

What each is for:

  yang       CLICON_YANG_DOMAIN_DIR: the YANG the controller resolves device
             mount points from. With it, a controller that cannot reach any
             device can still bind, validate and diff device config.
  main       CLICON_YANG_MAIN_DIR: the host's own model set, including service
             YANG installed from outside your checkout.
  revisions  which revision of each module every device runs, asked of the
             controller with get-device-schema. The domain dir is a merged
             cache of every revision ever seen, and nothing in it says which
             one a device uses; without this, validation runs against the
             newest file and fails on config the device really has.
  config     the running datastore (or an --xpath part of it), wrapped as a
             <config> file, to seed the local controller. Needs the ssh user to open the backend socket
             (root, or in the correct group).

Fetch yang and revisions together: a mismatch is reported at startup as
"module@revision the domain has at another revision".

fetched/ is gitignored. It holds real device config, secrets and vendor
YANG; keep it that way.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional

from clixon_env import CONFIGS, FETCHED, PACKAGES, PYAPI, ROOT, controller_host

DEFAULT_CONFIG = "/usr/local/etc/clixon/controller.xml"
MANIFEST = os.path.join(FETCHED, "FETCHED.json")

# Fallbacks if a setting is not in the host's config file: the values
# controller.xml.in installs with DATADIR=/usr/local/share.
DEFAULTS = {
    "CLICON_YANG_DOMAIN_DIR": "/usr/local/share/controller/mounts",
    "CLICON_YANG_MAIN_DIR": "/usr/local/share/controller/main",
}

# Element.dumps() serialises the children but not the namespace declarations
# on the root, so a config full of nc:operation / cl:creator would come back
# with unbound prefixes. Declare them on the wrapper.
NS = {
    "nc": "urn:ietf:params:xml:ns:netconf:base:1.0",
    "cl": "http://clicon.org/lib",
}
OPEN_TAG = "<config " + " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items()) + ">"

# Runs on the host, with the pyapi installed there. CLICON_SOCK is read from
# the config, as clixon_server.py itself does, since it moves with RUNSTATEDIR.
#
# source="running" matters: Clixon() reads the actions datastore by default,
# which is what a service sees mid-transaction. Outside one it holds whatever
# the last service run left there, or nothing on a fresh controller.
REMOTE_CONFIG = '''
import sys
from clixon.clixon import Clixon
from clixon.parser import parse_file

sock = {sock!r}
if not sock:
    sock = parse_file({config!r}).clixon_config.CLICON_SOCK.cdata
    print("socket from config:", sock, file=sys.stderr)

with Clixon(sockpath=sock, source="running", standalone=True) as cd:
    sys.stdout.write(cd.get_root(xpath={xpath!r}).dumps())
'''


class Host:
    """The clixon host, over ssh."""

    def __init__(self, name: str, config: str, options: List[str]):
        self.name = name
        self.config = config
        self.options = options
        self._config_text: Optional[str] = None

    def ssh(self) -> List[str]:
        # BatchMode: an auth prompt would just hang behind the pipe.
        cmd = ["ssh", "-o", "BatchMode=yes"]
        for opt in self.options:
            cmd += ["-o", opt]
        return cmd

    def run(self, args: List[str], stdin: str = "", timeout: int = 300):
        try:
            return subprocess.run(self.ssh() + [self.name] + args, input=stdin,
                                  capture_output=True, text=True,
                                  timeout=timeout)
        except FileNotFoundError:
            raise SystemExit("ssh not found")
        except subprocess.TimeoutExpired:
            raise SystemExit(f"timed out talking to {self.name}")

    def setting(self, name: str) -> str:
        """One CLICON_* value from the host's config, else the default.

        Asked rather than assumed: these move with DATADIR at build time, and
        a deployment can override them from CLICON_CONFIGDIR.
        """
        if self._config_text is None:
            done = self.run(["cat", self.config], timeout=30)
            if done.returncode != 0:
                print(f"could not read {self.config} on {self.name}: "
                      f"{done.stderr.strip()}", file=sys.stderr)
            self._config_text = done.stdout if done.returncode == 0 else ""
        found = re.findall(rf"<{name}>([^<]*)</{name}>", self._config_text)
        if found:
            return found[-1].strip()  # last wins, as clixon resolves it
        print(f"{name} not in {self.config}; assuming {DEFAULTS[name]}")
        return DEFAULTS[name]

    def copy_dir(self, remote: str, local: str) -> bool:
        """rsync when available (the device YANG is hundreds of MB and is
        refetched after every upgrade), scp otherwise."""
        os.makedirs(local, exist_ok=True)
        if shutil.which("rsync"):
            cmd = ["rsync", "-a", "--delete", "-e", " ".join(self.ssh()),
                   f"{self.name}:{remote.rstrip('/')}/", local + os.sep]
            print("  " + " ".join(cmd))
            if subprocess.run(cmd).returncode == 0:
                return True
            print("  rsync failed, falling back to scp", file=sys.stderr)
        cmd = ["scp", "-r", "-o", "BatchMode=yes"]
        for opt in self.options:
            cmd += ["-o", opt]
        # /. copies the contents, so a refetch does not nest mounts/mounts.
        cmd += [f"{self.name}:{remote.rstrip('/')}/.", local]
        print("  " + " ".join(cmd))
        return subprocess.run(cmd).returncode == 0

    def netconf(self, rpcs: List[str]) -> Optional[str]:
        """RPCs against the host's own NETCONF, in one session.

        No client hello: -0 means the server does not expect one, and sending
        one anyway makes it drop the RPC that follows.
        """
        done = self.run(["clixon_netconf", "-0", "-f", self.config],
                        stdin="\n".join(rpcs) + "\n")
        if done.returncode != 0:
            print(f"netconf on {self.name} failed: {done.stderr.strip()}",
                  file=sys.stderr)
            return None
        return done.stdout


def record(what: str, host: Host, detail: str) -> None:
    """Note where and when each thing came from, for `status`."""
    manifest = {}
    try:
        with open(MANIFEST) as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        pass
    manifest[what] = {
        "host": host.name,
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "detail": detail,
    }
    os.makedirs(FETCHED, exist_ok=True)
    with open(MANIFEST, "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")


def count_yang(local: str) -> Dict[str, int]:
    """.yang files per domain (or "" for a flat dir)."""
    counts: Dict[str, int] = {}
    if not os.path.isdir(local):
        return counts
    for entry in sorted(os.listdir(local)):
        path = os.path.join(local, entry)
        if os.path.isdir(path):
            counts[entry] = sum(n.endswith(".yang")
                                for _, _, names in os.walk(path) for n in names)
        elif entry.endswith(".yang"):
            counts[""] = counts.get("", 0) + 1
    return counts


# --- the things to obtain --------------------------------------------------

def fetch_yang(host: Host, args) -> int:
    remote = host.setting("CLICON_YANG_DOMAIN_DIR")
    local = os.path.join(FETCHED, "mounts")
    print(f"\ndevice YANG {host.name}:{remote} -> {os.path.relpath(local, ROOT)}")
    if not host.copy_dir(remote, local):
        print("fetch failed", file=sys.stderr)
        return 1
    counts = count_yang(local)
    for domain, n in counts.items():
        print(f"  {domain or '(top level)'}: {n} .yang files")
    record("yang", host, ", ".join(f"{d}: {n}" for d, n in counts.items()))
    return 0


def fetch_main(host: Host, args) -> int:
    remote = host.setting("CLICON_YANG_MAIN_DIR")
    local = os.path.join(FETCHED, "main")
    print(f"\ncontroller YANG {host.name}:{remote} -> {os.path.relpath(local, ROOT)}")
    if not host.copy_dir(remote, local):
        print("fetch failed", file=sys.stderr)
        return 1
    n = count_yang(local).get("", 0)
    print(f"  {n} .yang files")
    record("main", host, f"{n} .yang files")
    return 0


def fetch_revisions(host: Host, args) -> int:
    print(f"\nasking {host.name} which module revisions its devices run")
    out = host.netconf([
        '<rpc xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" message-id="1">'
        '<get-config><source><running/></source><filter type="xpath" '
        'xmlns:ctrl="http://clicon.org/controller" '
        'select="/ctrl:devices/ctrl:device/ctrl:name"/></get-config></rpc>]]>]]>'
    ])
    if out is None:
        return 1
    devices = re.findall(r"<name>([^<]+)</name>", out)
    if not devices:
        print("the controller reported no devices", file=sys.stderr)
        return 1

    # One rpc per device: the reply does not name the device, so a wildcard
    # would merge every device's schemas with nothing to attribute them to.
    rpcs = [f'<rpc xmlns="urn:ietf:params:xml:ns:netconf:base:1.0" '
            f'message-id="{i}"><get-device-schema '
            f'xmlns="http://clicon.org/controller"><device>{name}</device>'
            f'</get-device-schema></rpc>]]>]]>'
            for i, name in enumerate(devices, start=2)]
    out = host.netconf(rpcs)
    if out is None:
        return 1

    # The server greets us even though we do not greet it, so the first frame
    # is its hello and the replies follow in order.
    replies = out.split("]]>]]>")[1:]
    schemas: Dict[str, Dict[str, str]] = {}
    for name, reply in zip(devices, replies):
        mods = dict(re.findall(
            r"<schema[^>]*><name>([^<]+)</name><revision>([^<]*)</revision>",
            reply.replace("\n", "")))
        if mods:
            schemas[name] = mods
    if not schemas:
        print("no schemas came back; is the controller new enough for "
              "get-device-schema?", file=sys.stderr)
        return 1

    path = os.path.join(FETCHED, "device-schemas.json")
    os.makedirs(FETCHED, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(schemas, fh, indent=2, sort_keys=True)
        fh.write("\n")
    counts = sorted({len(m) for m in schemas.values()})
    print(f"  wrote {os.path.relpath(path, ROOT)}: {len(schemas)}/{len(devices)} "
          f"devices, {counts[0]}-{counts[-1]} modules each")
    signatures = {tuple(sorted(m.items())) for m in schemas.values()}
    print(f"  {len(signatures)} distinct module sets (each primed one costs "
          f"its own parsed copy of the YANG in memory)")
    record("revisions", host, f"{len(schemas)} devices")
    return 0


def fetch_config(host: Host, args) -> int:
    script = REMOTE_CONFIG.format(sock=args.sock, config=host.config,
                                  xpath=args.xpath)
    if args.dry_run:
        print(" ".join(host.ssh() + [host.name, args.python, "-"])
              + f" <<'EOF'{script}EOF")
        return 0

    print(f"\nconfig {args.xpath} from {host.name}")
    done = host.run([args.python, "-"], stdin=script, timeout=300)
    if done.returncode != 0:
        print(f"failed on {host.name} (exit {done.returncode}):",
              file=sys.stderr)
        print(done.stderr.strip() or "(no stderr)", file=sys.stderr)
        print("\nIf the pyapi is not importable there or the config is "
              "elsewhere, pass --python/--config (or --sock). --dry-run "
              "prints the script to run by hand.", file=sys.stderr)
        return 1
    body = done.stdout.strip()
    if not body:
        print(f"{host.name} returned an empty config; nothing written.",
              file=sys.stderr)
        return 1

    os.makedirs(CONFIGS, exist_ok=True)
    path = os.path.join(CONFIGS, f"{args.name}.xml")
    text = OPEN_TAG + "\n" + body + "\n</config>\n"
    if args.pretty:
        text = indent(text)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    record(f"config:{args.name}", host, f"xpath {args.xpath}")
    return verify_config(path)


def indent(text: str) -> str:
    """Indent without reparsing. An ElementTree round trip rewrites default
    namespaces into ns0: prefixes, and the pyapi, which matches children by
    name, then finds nothing. This only inserts whitespace between tags."""
    tokens = [t for t in re.split(r"(<[^>]*>)", text) if t and t.strip()]
    out, depth, i = [], 0, 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("<"):
            i += 1
            continue
        if token.startswith("</"):
            depth = max(depth - 1, 0)
            out.append("  " * depth + token)
            i += 1
            continue
        if token.endswith("/>") or token.startswith(("<?", "<!")):
            out.append("  " * depth + token)
            i += 1
            continue
        # A leaf -- opening tag, text, closing tag -- stays on one line.
        if (i + 2 < len(tokens) and not tokens[i + 1].startswith("<")
                and tokens[i + 2].startswith("</")):
            out.append("  " * depth + token + tokens[i + 1].strip() + tokens[i + 2])
            i += 3
            continue
        out.append("  " * depth + token)
        depth += 1
        i += 1
    return "\n".join(out) + "\n"


def verify_config(path: str) -> int:
    """Parse what was written the way the harness will, so an empty or
    truncated dump shows up now rather than at the first odd breakpoint."""
    # The pyapi parses sys.argv at import and exits when its default module
    # path does not exist.
    sys.argv = [sys.argv[0], "-m", PACKAGES]
    sys.path.insert(0, PYAPI)
    rel = os.path.relpath(path, ROOT)
    try:
        from clixon.parser import parse_string
    except ImportError:
        print(f"  wrote {rel} (not verified: the pyapi is not importable)")
        return 0
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        root = parse_string(text).config
    except Exception as exc:
        print(f"  wrote {rel}, but it does not parse as a config: {exc}",
              file=sys.stderr)
        return 1
    top = sorted({c.origname() for c in getattr(root, "_children", []) or []})
    print(f"  wrote {rel} ({os.path.getsize(path):,} bytes)")
    print(f"  top level: {', '.join(top) or '(empty)'}")
    return 0


def check_domains(config_path: str) -> int:
    """Every device-domain the config uses should have a directory in
    fetched/mounts; otherwise those devices cannot be bound, and the startup
    error does not name the domain."""
    try:
        with open(config_path, encoding="utf-8", errors="replace") as fh:
            data = fh.read()
    except OSError:
        return 0
    wanted = sorted(set(re.findall(r"<device-domain>([^<]*)</device-domain>", data)))
    present = set(count_yang(os.path.join(FETCHED, "mounts")))
    missing = [d for d in wanted if d not in present]
    name = os.path.relpath(config_path, ROOT)
    if missing:
        print(f"  {name}: domains with no YANG in fetched/mounts: "
              f"{', '.join(missing)}", file=sys.stderr)
        return 1
    if wanted:
        print(f"  {name}: domains {', '.join(wanted)} all present")
    return 0


def fetch_all(host: Host, args) -> int:
    for step in (fetch_yang, fetch_main, fetch_revisions, fetch_config):
        if step(host, args):
            return 1
    return check_domains(os.path.join(CONFIGS, f"{args.name}.xml"))


def status(args) -> int:
    manifest = {}
    try:
        with open(MANIFEST) as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        pass
    if not manifest:
        print("nothing fetched yet; run: venv/bin/python harness/clixon_fetch.py all")
        return 1
    width = max(len(k) for k in manifest)
    for what in sorted(manifest):
        entry = manifest[what]
        print(f"{what:<{width}}  {entry['at']}  {entry['host']}  {entry['detail']}")
    rc = 0
    if os.path.isdir(CONFIGS):
        for name in sorted(os.listdir(CONFIGS)):
            if name.endswith(".xml"):
                rc |= check_domains(os.path.join(CONFIGS, name))
    if "yang" in manifest and "revisions" in manifest:
        if manifest["yang"]["at"][:10] != manifest["revisions"]["at"][:10]:
            print("yang and revisions were fetched on different days; "
                  "refetch both if devices were upgraded in between")
    return rc


def main(argv) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", help="The clixon host, as ssh takes it "
                        "(default: CLIXON_HOST_SSH from .env)")
    common.add_argument("--config", default=DEFAULT_CONFIG,
                        help=f"Controller config on the host (default: {DEFAULT_CONFIG})")
    common.add_argument("--ssh-option", action="append", default=[],
                        metavar="OPT", dest="ssh_options",
                        help="Passed to ssh as -o OPT; repeatable")

    config_opts = argparse.ArgumentParser(add_help=False)
    config_opts.add_argument("--name", default="controller",
                             help="File name in fetched/configs/, without .xml "
                                  "(default: controller)")
    config_opts.add_argument("--xpath", default="/",
                             help="Fetch only part of the tree, e.g. /services "
                                  "(default: everything)")
    config_opts.add_argument("--pretty", action="store_true",
                             help="Indent the XML (costs memory on a big config)")
    config_opts.add_argument("--sock", default="",
                             help="Backend socket on the host, instead of "
                                  "reading it from --config")
    config_opts.add_argument("--python", default="python3",
                             help="Interpreter on the host with the pyapi "
                                  "importable (default: python3)")
    config_opts.add_argument("--dry-run", action="store_true",
                             help="Print what would run on the host and stop")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="what", required=True)
    sub.add_parser("yang", parents=[common], help="device YANG -> fetched/mounts/")
    sub.add_parser("main", parents=[common], help="controller YANG -> fetched/main/")
    sub.add_parser("revisions", parents=[common],
                   help="per-device module revisions -> fetched/device-schemas.json")
    sub.add_parser("config", parents=[common, config_opts],
                   help="running config -> fetched/configs/NAME.xml")
    sub.add_parser("all", parents=[common, config_opts], help="all of the above")
    sub.add_parser("status", help="what has been fetched, from where, and when")
    args = parser.parse_args(argv)

    if args.what == "status":
        return status(args)

    host = Host(controller_host(args.host), args.config, args.ssh_options)
    steps = {"yang": fetch_yang, "main": fetch_main,
             "revisions": fetch_revisions, "config": fetch_config,
             "all": fetch_all}
    return steps[args.what](host, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
