"""Call `services/hostproxy.py` exactly as the admin router does, against the fake machine's dockerd.

    python driver.py detect|plan|apply|remove|state [domain]

Runs in the geodeploy/api image (it has the Docker SDK and pydantic-settings) with the BRANCH's
`api/` mounted at /repo/api and put first on the path, DOCKER_HOST pointing at the fake machine, and
GEODEPLOY_DATA_DIR at a scratch directory — so the state file is the scenario's, never an instance's.
Prints one line `@@JSON@@{...}` for the runner to parse.
"""
import json
import os
import sys

sys.path.insert(0, "/repo/api")
from geodeploy.services import hostproxy  # noqa: E402

action = sys.argv[1]
domain = sys.argv[2] if len(sys.argv) > 2 else ""
intent = {"mode": "behind-proxy", "bind": os.environ.get("GD_BIND", "127.0.0.1"),
          "port": os.environ.get("GD_PORT", "8080")}
try:
    if action == "detect":
        out = hostproxy.detect()
    elif action == "plan":
        out = hostproxy.plan(domain, intent)
    elif action == "apply":
        out = hostproxy.apply(domain, intent)
    elif action == "remove":
        out = hostproxy.remove()
    elif action == "state":
        out = hostproxy.read_state()
    else:
        raise SystemExit(f"unknown action {action}")
except hostproxy.ProxyError as exc:
    out = {"proxy_error": str(exc)}
print("@@JSON@@" + json.dumps(out, default=str))
