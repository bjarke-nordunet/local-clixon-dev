#!/usr/bin/env python3
"""Log the config a service run produced, as config rather than as a diff.

Activated by clixon_pyapi_local.py. Every time a service run sends its result
to the controller, this prints the subtrees the service generated.

Where the knowledge comes from
------------------------------
Nothing here inspects templates or guesses. clixon/parser.py:141 flags the root
of every parse_string() result as _modified, so each parse_template() render
arrives pre-marked, and Element.create() marks hand-built nodes the same way.
By the time rpc_config_set() serialises the tree, the roots of everything the
service produced are already marked and nothing else is.

That is a different question from the one cl:creator answers. A creator tag
says "we own this, delete it when the service goes away", which is deliberately
conservative: ipclos merges router-id into <routing-options> without claiming
the container, so `commit diff` shows the leaf only while it is changing and
`created` never mentions it at all. _modified says "we generated this, this
run" -- which is what you want while writing the service, and it covers merges
and sub-services without either one having to tag anything.

The controller never sees these flags and does not need to: this runs in the
pyapi process, before the edit-config leaves it.
"""
import sys

# Only nodes below this are service output; the tree root itself carries a
# _modified flag left by parse_string when the config was read back in.
DEVICE_MARKER = "devices"


def _elements(el):
    try:
        return el.get_elements()
    except Exception:
        return []


def _name(el):
    try:
        return el.origname()
    except Exception:
        return str(el.get_name())


def _key(el):
    """Key of a list entry, for readable paths: interface[et-0/0/55]."""
    for child in _elements(el):
        if _name(child) in ("name", "device-name", "service-name"):
            text = str(child.cdata).strip()
            if text:
                return text
    return None


def generated_subtrees(root):
    """Yield (device, path, element) for each subtree the service generated.

    A marked node inside an already-marked subtree is skipped -- ssh_users
    marks both the new <user> and an <ssh-ed25519> within it, and the outer one
    already carries the inner.
    """
    def walk(el, path, device, under_devices, marked_above):
        name = _name(el)
        key = _key(el)
        here = f"{path}/{name}" + (f"[{key}]" if key else "")

        if name == DEVICE_MARKER:
            under_devices = True
        elif under_devices and name == "device" and key:
            device = key

        emit = (
            getattr(el, "_modified", False)
            and under_devices
            and device is not None
            and not marked_above
        )
        if emit:
            yield device, here, el

        for child in _elements(el):
            yield from walk(
                child, here, device, under_devices, marked_above or emit
            )

    yield from walk(root, "", None, False, False)


def _xml(el):
    """Serialise el including its own tag -- dumps() only emits the children."""
    name = _name(el)
    attrs = ""
    try:
        attrs = el.get_attributes_str()
    except Exception:
        pass

    inner = (el.cdata or "") + el.dumps()
    if not inner.strip():
        return f"<{name}{attrs}/>"

    return f"<{name}{attrs}>{inner}</{name}>"


# Service config carries nc:/cl: prefixes whose declarations live further up the
# real document, so a fragment on its own is not well-formed. Lend them to the
# parser via a throwaway wrapper that is dropped again straight after.
_PREFIXES = (
    'xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0" '
    'xmlns:cl="http://clicon.org/lib"'
)


def _pretty(xmlstr):
    from xml.dom import minidom

    try:
        doc = minidom.parseString(f"<_wrap {_PREFIXES}>{xmlstr}</_wrap>")
    except Exception:
        return xmlstr  # a half-built WIP fragment may still not parse

    for node in doc.documentElement.childNodes:
        if node.nodeType == node.ELEMENT_NODE:
            out = node.toprettyxml(indent="  ")
            return "\n".join(l for l in out.splitlines() if l.strip())

    return xmlstr


def _relative(path):
    """Trim the /config/devices/device[x]/config/... prefix off a path."""
    for cut in ("/config/configuration/", "/config/"):
        _, sep, tail = path.partition(cut)
        if sep and tail:
            return tail
    return path.lstrip("/")


def render(root, pretty=True):
    """Return the log block for one outgoing config tree, or "" if nothing."""
    by_device = {}
    for device, path, el in generated_subtrees(root):
        by_device.setdefault(device, []).append((path, el))

    if not by_device:
        return ""

    out = []
    total = sum(len(v) for v in by_device.values())
    out.append(f"service generated {total} subtree(s) on {len(by_device)} device(s)")

    for device in sorted(by_device):
        out.append("")
        out.append(f"--- {device} " + "-" * max(0, 60 - len(device)))
        for path, el in by_device[device]:
            out.append("")
            out.append(f"# {_relative(path)}")
            try:
                body = _xml(el)
                if pretty:
                    body = _pretty(body)
            except Exception as exc:  # a half-built WIP tree should not kill the run
                body = f"<!-- could not render: {exc} -->"
            out.append(body.strip("\n"))

    return "\n".join(out)


def install(log=None, pretty=True):
    """Wrap rpc_config_set so each outgoing service result is logged.

    clixon/clixon.py did `from clixon.netconf import rpc_config_set` and so
    holds its own reference; rebinding clixon.netconf alone would miss it.
    """
    import clixon.clixon
    import clixon.netconf

    if getattr(clixon.netconf.rpc_config_set, "_service_dump", False):
        return

    original = clixon.netconf.rpc_config_set

    def emit(text):
        if log is not None:
            log(text)
        else:
            print(text, file=sys.stderr, flush=True)

    def wrapper(config, *args, **kwargs):
        try:
            block = render(config, pretty=pretty)
            if block:
                emit("\n" + block + "\n")
        except Exception as exc:
            emit(f"service dump failed: {exc}")

        return original(config, *args, **kwargs)

    wrapper._service_dump = True
    wrapper.__name__ = getattr(original, "__name__", "rpc_config_set")

    clixon.netconf.rpc_config_set = wrapper
    clixon.clixon.rpc_config_set = wrapper
