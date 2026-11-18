#!/bin/bash
# Boot the fake machine: its own dockerd (what Apply's helper runs on) and a stand-in GeoDeploy on
# 127.0.0.1:8080 (what the proxy is pointed at). No web server is started here — each scenario's
# setup script decides what the machine already runs, exactly as the operator would have.

# cgroup v2 nesting, as docker:dind does it: move ourselves out of the root cgroup so dockerd may
# enable controllers for its children.
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
  mkdir -p /sys/fs/cgroup/init
  xargs -rn1 < /sys/fs/cgroup/cgroup.procs > /sys/fs/cgroup/init/cgroup.procs 2>/dev/null || :
  sed -e 's/ / +/g' -e 's/^/+/' < /sys/fs/cgroup/cgroup.controllers \
    > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || :
fi
sysctl -qw net.ipv6.conf.all.disable_ipv6=0 2>/dev/null || :
sysctl -qw net.ipv6.conf.default.disable_ipv6=0 2>/dev/null || :

# tcp:2375 without TLS is acceptable ONLY because this daemon lives on a throwaway, test-only Docker
# network and dies with the scenario. vfs because overlay-on-overlay is unreliable under WSL2.
dockerd --host=unix:///var/run/docker.sock --host=tcp://0.0.0.0:2375 --tls=false \
        --storage-driver=vfs >/var/log/dockerd.log 2>&1 &
python3 /opt/gdhp/upstream.py >/var/log/upstream.log 2>&1 &
exec sleep infinity
