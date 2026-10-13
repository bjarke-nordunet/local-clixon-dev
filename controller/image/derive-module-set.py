#!/usr/bin/env python3
"""Emit a NETCONF edit-config fragment giving every device-profile a module-set.

controller_connect() builds a device's yang-lib purely from its module-set
(xdev2yang_library). With no module-set the device handle gets no YANG, the
CS_CONNECTING hello handler reports "no local models found", and
controller_mount_yspec_set() is never reached - so the device config subtree
is never bound to YANG and nothing validates what a service writes into it.

A host's config usually carries no module-set at all, and that is not an
oversight there: real devices announce :netconf-monitoring, so the controller
pulls their schemas with get-schema and never needs a local list. Nothing here
can do that, hence this.

Consumes the device-schemas.json, obtained with ./harness/clixon_fetch.py revisions, pointed at a production controller.
That file is the answer to "which revision of each module did I resolve for this device" - which it learned from the
device's yang-library.

PRIME_DEVICES is what decides which devices should have their yang validation.
A comma-separated list of shell patterns for the devices worth mounting, default "*" here 
(prepare-controller.sh narrows an unset one to a single device). A device left out keeps no module-set, is
not opened, and stays at BIND, so its config subtree is simply unbound and
validation passes over it. Mount/prime just devices you are interested in validating for,
as there's a significant memory cost involved.

Writes the <devices> body to stdout, for the caller to wrap in <devices>,
and a summary to stderr.
"""
import fnmatch
import glob
import json
import os
import re
import sys

RUNNING = "/usr/local/var/controller/running_db"
SCHEMAS = "/fetched/device-schemas.json"


def top_level_modules(domain_dir):
    """namespace -> module name for every top-level module in one domain.

    Submodules are skipped: they carry no namespace of their own and cannot
    appear in a module-set. Only the newest revision of each name is used -
    two revisions of one module in a mount is ambiguous.
    """
    newest = {}
    for path in sorted(glob.glob(os.path.join(domain_dir, "*.yang"))):
        name = os.path.basename(path)[: -len(".yang")].split("@")[0]
        newest[name] = path  # sorted(), so the last revision wins

    index = {}
    for name, path in sorted(newest.items()):
        with open(path) as f:
            src = f.read(4000)
        if re.search(r"^\s*submodule\s", src, re.M):
            continue
        namespace = re.search(r'namespace\s+"([^"]+)"', src)
        if namespace is None:
            continue
        index.setdefault(namespace.group(1), name)
    return index


def module_at(domain_dir, name, revision):
    """(path, namespace) for one module at one revision, or None.

    An exact-revision miss is reported rather than silently newest-ed: it means
    mounts/ was fetched at a different time from device-schemas.json.
    """
    path = os.path.join(domain_dir, "%s@%s.yang" % (name, revision))
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        src = f.read(4000)
    if re.search(r"^\s*submodule\s", src, re.M):
        return None
    namespace = re.search(r'namespace\s+"([^"]+)"', src)
    if namespace is None:
        return None
    return path, namespace.group(1)


def emit_module_set(modules, indent):
    """modules: list of (name, revision_or_None, namespace)."""
    pad = " " * indent
    print('%s<module-set nc:operation="replace">' % pad)
    for name, revision, namespace in modules:
        rev = "<revision>%s</revision>" % revision if revision else ""
        print("%s  <module><name>%s</name>%s<namespace>%s</namespace></module>"
              % (pad, name, rev, namespace))
    print("%s</module-set>" % pad)


def main():
    domain_root = sys.argv[1]
    yang_config = os.environ.get("YANG_CONFIG", "BIND")
    patterns = [p.strip() for p in
                os.environ.get("PRIME_DEVICES", "*").split(",") if p.strip()]
    # ONLY_NAMED: emit nothing at all for devices outside PRIME_DEVICES, and no
    # profile fallbacks either. For priming an extra device into a backend that
    # is already running, where the rest of the datastore must not be touched.
    only_named = os.environ.get("ONLY_NAMED", "") not in ("", "0", "false")

    with open(RUNNING, "rb") as f:
        running = f.read().decode("utf-8", "replace")

    schemas = {}
    if os.path.isfile(SCHEMAS):
        with open(SCHEMAS) as f:
            schemas = json.load(f)
        print("device-schemas.json: %d devices" % len(schemas), file=sys.stderr)
    else:
        print("no %s; falling back to newest-revision profile sets "
              "(run harness/clixon_fetch.py revisions)" % SCHEMAS, file=sys.stderr)

    # profile -> domain, and device -> profile
    profiles = {}
    for body in re.findall(
        r"<device-profile>((?:(?!</device-profile>).)*)</device-profile>", running, re.S
    ):
        name = re.search(r"<name>([^<]+)</name>", body)
        domain = re.search(r"<device-domain>([^<]*)</device-domain>", body)
        if name and domain:
            profiles[name.group(1)] = domain.group(1)

    devices = []
    for m in re.finditer(r"<device>\s*<name>([^<]+)</name>(.*?)</device>", running, re.S):
        profile = re.search(r"<device-profile>([^<]+)</device-profile>", m.group(2))
        devices.append((m.group(1), profile.group(1) if profile else None))

    # Profiles first: clixon wants the lists in YANG order, and they are the
    # fallback for any device we cannot pin.
    for profile, domain in (() if only_named else sorted(profiles.items())):
        domain_dir = os.path.join(domain_root, domain)
        if not os.path.isdir(domain_dir):
            print("%s: no domain dir %s" % (profile, domain_dir), file=sys.stderr)
            continue
        index = top_level_modules(domain_dir)
        if not index:
            continue
        print("  <device-profile>")
        print("    <name>%s</name>" % profile)
        emit_module_set([(mod, None, ns) for ns, mod in sorted(index.items())], 4)
        print("  </device-profile>")
        print("%s (%s): %d modules, newest revision (fallback)"
              % (profile, domain, len(index)), file=sys.stderr)

    pinned = 0
    unpinned = []
    skipped = []
    missing = {}
    for device, profile in devices:
        if not any(fnmatch.fnmatch(device, pat) for pat in patterns):
            # Explicit rather than omitted: a previous run may have left this
            # device with a module-set and VALIDATE, and it would then still
            # mount and still be validated. "remove" is delete-if-present.
            skipped.append(device)
            if only_named:
                continue
            print("  <device>")
            print("    <name>%s</name>" % device)
            print('    <module-set nc:operation="remove"/>')
            print("    <enabled>false</enabled>")
            print("    <yang-config>BIND</yang-config>")
            print("  </device>")
            continue
        domain = profiles.get(profile)
        mods = []
        if domain and device in schemas:
            domain_dir = os.path.join(domain_root, domain)
            for name, revision in sorted(schemas[device].items()):
                hit = module_at(domain_dir, name, revision)
                if hit is None:
                    # A module absent from the domain at every revision is a
                    # standard one (ietf-yang-library, ietf-datastores, ...)
                    # that clixon resolves from its own CLICON_YANG_DIR, so it
                    # does not belong in a device module-set. Only a module the
                    # domain has at some *other* revision is real drift, and
                    # means mounts/ and device-schemas.json were fetched apart.
                    if glob.glob(os.path.join(domain_dir, "%s@*.yang" % name)):
                        missing.setdefault(device, []).append(
                            "%s@%s" % (name, revision))
                    continue
                mods.append((name, revision, hit[1]))

        print("  <device>")
        print("    <name>%s</name>" % device)
        if mods:
            emit_module_set(mods, 4)
            pinned += 1
        else:
            unpinned.append(device)
        print("    <enabled>true</enabled>")
        print("    <yang-config>%s</yang-config>" % yang_config)
        print("  </device>")

    print("pinned revisions for %d/%d devices (yang-config %s)"
          % (pinned, len(devices), yang_config), file=sys.stderr)
    if skipped:
        print("not mounting %d devices (PRIME_DEVICES=%s); their config stays "
              "unbound and unvalidated"
              % (len(skipped), ",".join(patterns)), file=sys.stderr)
    if unpinned:
        print("using the profile fallback for: %s" % ", ".join(unpinned[:8])
              + (" ..." if len(unpinned) > 8 else ""), file=sys.stderr)
    if missing:
        total = sum(len(v) for v in missing.values())
        device, examples = next(iter(missing.items()))
        print("%d module@revision the domain has at another revision - "
              "fetch yang and revisions together; e.g. %s: %s"
              % (total, device, ", ".join(examples[:3])), file=sys.stderr)


if __name__ == "__main__":
    main()
