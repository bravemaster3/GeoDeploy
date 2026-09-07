"""The plugin must work when the `geodeploy` module in memory is not the one it ships.

REPORTED AS A HARD STOP ON CONNECT: `Client.__init__() got an unexpected keyword argument
'on_throttled'`, from a user who had just installed a new plugin version.

Putting `vendor/` on `sys.path` only decides where `geodeploy` is imported FROM the first time.
After that every `import geodeploy` is answered from `sys.modules`. So upgrading the plugin without
restarting QGIS runs the NEW `connection.py` against the OLD client, and a keyword argument the new
one added becomes a TypeError at the moment the user presses Connect. A user with `geodeploy`
pip-installed can land in the same place from a different direction.

The rule this pins: **anything the plugin passes to the vendored client beyond the long-standing
arguments is OFFERED, not required.** A copy that does not understand it must still connect.

Run it:

    python3 scripts/test_stale_client.py
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..", "geodeploy_qgis")

FAILURES = []
CHECKS = [0]


def check(name, condition, detail=""):
    CHECKS[0] += 1
    print(("  ok   " if condition else "  FAIL ") + name +
          ("" if condition else "  — " + str(detail)[:300]))
    if not condition:
        FAILURES.append(name)


class OldClient:
    """A `Client` from before the newest keyword existed — the long-standing arguments only."""

    def __init__(self, url, token=None, jwt=None, transport=None, timeout=120.0):
        self.url, self.token = url, token


class TwoBehind:
    """Older still — it predates BOTH of the recent keywords."""

    def __init__(self, url, token=None):
        self.url, self.token = url, token


class NoTokenClient:
    """Not a client this plugin can use at all: it has no `token`.

    The boundary matters. Dropping an unknown EXTRA keeps the plugin working; dropping `token`
    would connect anonymously to an instance the user signed in to, and the failure would show up
    later as "my private layers are missing" rather than here as an error.
    """

    def __init__(self, url):
        self.url = url


def connection_module(client_class):
    """`connection.py` executed against a pre-seeded `geodeploy`, as an in-place upgrade leaves it."""
    for name in [n for n in sys.modules if n == "geodeploy" or n.startswith("geodeploy.")]:
        del sys.modules[name]

    root = types.ModuleType("geodeploy")
    root.Client = client_class
    sys.modules["geodeploy"] = root

    config = types.ModuleType("geodeploy.config")
    config.Config = object
    config.load_credential = lambda *a, **k: None
    config.normalize_url = lambda u: str(u).rstrip("/")
    sys.modules["geodeploy.config"] = config

    errors = types.ModuleType("geodeploy.errors")

    class GeoDeployError(Exception):
        pass

    errors.GeoDeployError = GeoDeployError
    sys.modules["geodeploy.errors"] = errors

    path = os.path.join(PLUGIN, "connection.py")
    source = open(path, encoding="utf-8").read()
    # `from . import symbology` is a package-relative import this harness has no package for, and
    # it only guards a log line.
    source = source.replace("from . import symbology", "symbology = None")
    module = types.ModuleType("conn")
    module.__dict__["__name__"] = "conn"
    module.__dict__["__file__"] = path
    exec(compile(source, path, "exec"), module.__dict__)          # noqa: S102
    return module


def main():
    print("A stale or foreign `geodeploy` must not stop the plugin connecting")

    for label, client_class in (("one version behind", OldClient),
                                ("two versions behind", TwoBehind)):
        module = connection_module(client_class)
        check("{0}: that copy is the one in use".format(label),
              module.Client is client_class, module.Client)
        try:
            instance = module.Instance("https://example.invalid", "tok")
            built, error = instance.client, None
        except Exception as exc:                                   # noqa: BLE001
            built, error = None, "{0}: {1}".format(type(exc).__name__, exc)
        check("{0}: an Instance is still built".format(label), built is not None, error)
        if built is not None:
            check("{0}: it is that client".format(label), isinstance(built, client_class),
                  type(built).__name__)
            check("{0}: with the url and token it was given".format(label),
                  built.url == "https://example.invalid" and built.token == "tok",
                  (getattr(built, "url", None), getattr(built, "token", None)))

    # …and with a client that DOES understand the keyword, it is actually passed.
    class NewClient:
        def __init__(self, url, token=None, on_throttled=None, **kw):
            self.url, self.token, self.on_throttled = url, token, on_throttled

    module = connection_module(NewClient)
    instance = module.Instance("https://example.invalid", "tok")
    check("a client that takes the keyword is given it",
          callable(getattr(instance.client, "on_throttled", None)),
          getattr(instance.client, "on_throttled", None))

    # A CLIENT WITH NO `token` IS NOT USABLE, and must fail loudly. Only the extras are optional:
    # quietly dropping the token would connect anonymously to an instance the user had signed in
    # to, and that shows up later as "my private layers are missing" instead of here as an error.
    module = connection_module(NoTokenClient)
    try:
        module.Instance("https://example.invalid", "tok")
        refused = None
    except Exception as exc:                                       # noqa: BLE001
        refused = "{0}".format(type(exc).__name__)
    check("a client that cannot take a token is refused, not worked around",
          refused == "TypeError", refused or "it was accepted")

    # A TypeError that is NOT about a keyword must still be raised: swallowing every TypeError here
    # would turn a real bug into a silent half-configured client.
    class BrokenClient:
        def __init__(self, url, token=None, on_throttled=None):
            raise TypeError("something else entirely")

    module = connection_module(BrokenClient)
    try:
        module.Instance("https://example.invalid", "tok")
        raised = None
    except TypeError as exc:
        raised = str(exc)
    except Exception as exc:                                       # noqa: BLE001
        raised = "{0}: {1}".format(type(exc).__name__, exc)
    check("an unrelated TypeError is not swallowed", raised == "something else entirely", raised)

    print("\n{0} checks, {1} failed".format(CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("   FAILED: {0}".format(name))
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
