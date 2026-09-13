#!/usr/bin/env bash
# A SECOND GeoDeploy on the same machine must be refused, not half-installed.
#
# Its own suite because it is the one check that needs the machine to HAVE another installation —
# every other suite pauses them out of the way. It is also entirely read-only: preflight writes
# nothing and starts nothing, so this is safe to run against a live instance.
#
# Why refusing is right, rather than warning: five services carry a fixed container_name, and every
# instance joins the same external `geodeploy` network, where the first one's database answers to the
# generic alias `postgres`. A second instance's API would resolve `postgres` to the FIRST instance's
# database. That is not a collision you notice — it is two products sharing a datastore.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
harness_build

# The FIRST container built from a GeoDeploy image is not necessarily an installation: a stray
# `docker run` of geodeploy/api (a test container, a one-off psql) matches the image and carries no
# Compose label. The product code skips those; so must this, or OTHER_DIR comes back empty and
# `grep -q ""` passes against anything.
OTHER=""; OTHER_DIR=""
for _c in $(docker ps -aq --filter ancestor=geodeploy/api:latest 2>/dev/null; docker ps -aq --filter ancestor=geodeploy/ui:latest 2>/dev/null); do
  _wd="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$_c" 2>/dev/null)"
  [ -n "$_wd" ] || continue
  OTHER="$_c"; OTHER_DIR="$_wd"; break
done
if [ -z "$OTHER_DIR" ]; then
  echo ""
  echo "   SKIP  no other GeoDeploy on this machine to detect."
  echo "         Install one (or run this on a box that has one) to exercise this suite."
  summary
  exit 0
fi

hdr "from a DIFFERENT directory, with a GeoDeploy already at $OTHER_DIR"
run_preflight
chk "preflight blocks it"             'grep -qi "Another GeoDeploy" "$O"'
chk "names the other directory"       '[ -n "$OTHER_DIR" ] && grep -qF "$OTHER_DIR" "$O"'
chk "says why it matters"             'grep -qi "silently connect" "$O"'
chk "offers a way forward"            'grep -qi "reset.sh\|Update the existing" "$O"'
chk "counted as a blocking conflict"  'grep -qi "conflict(s) would stop an install" "$O"'
chk "--json reports it too"           'run_preflight --json; python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d[\"other_install\"] and d[\"blockers\"]>0 else 1)" "$O"'

hdr "and an installation does NOT flag itself"
# The precision that matters: keyed on Compose's working_dir label, so the instance that owns those
# containers reads as "none" rather than blocking its own re-run or update.
( cd "$OTHER_DIR" && bash installer/preflight.sh ) >"$O" 2>&1; strip_o
if grep -q "Another GeoDeploy" "$O"; then
  chk "self-check says 'none'"        'grep -q "Another GeoDeploy .*none" "$O"'
else
  echo "   SKIP  that install has no preflight.sh yet (older version)"
fi

hdr "the installer refuses too, not just preflight"
run_piped
chk "did not install"                 '! grep -q "GeoDeploy is running" "$O"'
chk "explained why"                   'grep -qi "Another GeoDeploy" "$O"'
chk "started nothing"                 '[ -z "$(dc ps -q nginx 2>/dev/null)" ]'
chk "wrote no .env"                   '[ ! -f "$REPO/.env" ]'

summary
