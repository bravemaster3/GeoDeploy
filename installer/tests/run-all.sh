#!/usr/bin/env bash
# Every installer suite, in increasing order of how much they disturb the machine.
#
#   bash installer/tests/run-all.sh          # everything except the port-80 suite
#   GD_PORT80=1 bash installer/tests/run-all.sh   # …including it — see the warning in test-port80.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SRC="${SRC:-$(cd "$HERE/../.." && pwd)}"
FAILED=""

run() { # name script
  echo ""; echo "########## $1 ##########"
  if bash "$HERE/$2"; then echo "########## $1: OK"; else FAILED="${FAILED:+$FAILED, }$1"; fi
}

run "unit"    test-unit.sh
run "proxy"   test-proxy.sh
run "install" test-install.sh
run "ports"   test-ports.sh
# Read-only, and needs another GeoDeploy present to mean anything; skips itself otherwise.
run "second"  test-second-install.sh
# Opt-in: it takes port 80, and stops any GeoDeploy already using it.
if [ "${GD_PORT80:-0}" = 1 ]; then run "port80" test-port80.sh; fi

echo ""
if [ -n "$FAILED" ]; then echo "FAILED: $FAILED"; exit 1; fi
echo "All suites passed."
