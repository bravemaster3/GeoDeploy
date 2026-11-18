#!/bin/bash
# snap.sh TAG — photograph everything a neighbour could notice, into /root/snap/TAG.*
#
#   TAG.files   every file and symlink under the web servers' config trees (and a containerised
#               proxy's mount), with its sha256 or link target. Byte-level: a reformatted line, a
#               touched-up comment or a stray backup file all show up.
#   TAG.http    what each neighbour site answers, over the scheme it is served on.
#   TAG.pid     the proxy's master PID — the same number before and after proves reload, not restart.
#
# Neighbours are listed one per line in /root/neighbours as `scheme host path`.
set -u
TAG="$1"; D=/root/snap; mkdir -p "$D"

for d in /etc/nginx /etc/apache2 /etc/caddy /srv/edge; do
  [ -d "$d" ] || continue
  find "$d" \( -type f -o -type l \) | LC_ALL=C sort | while read -r f; do
    if [ -L "$f" ]; then echo "L $f -> $(readlink "$f")"; else echo "F $f $(sha256sum < "$f" | cut -c1-16)"; fi
  done
done > "$D/$TAG.files"

: > "$D/$TAG.http"
if [ -f /root/neighbours ]; then
  while read -r scheme host path; do
    [ -n "$scheme" ] || continue
    port=80; [ "$scheme" = https ] && port=443
    body=$(curl -sk --max-time 5 --resolve "$host:$port:127.0.0.1" "$scheme://$host$path" | sha256sum | cut -c1-16)
    code=$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' --resolve "$host:$port:127.0.0.1" "$scheme://$host$path")
    echo "$scheme://$host$path $code $body" >> "$D/$TAG.http"
  done < /root/neighbours
fi

pid=""
for p in /run/nginx.pid /var/run/apache2/apache2.pid; do [ -s "$p" ] && pid=$(cat "$p") && break; done
[ -z "$pid" ] && pid=$(pgrep -o -x caddy 2>/dev/null || true)
echo "${pid:-none}" > "$D/$TAG.pid"
