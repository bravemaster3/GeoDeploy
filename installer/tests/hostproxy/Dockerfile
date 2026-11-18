# A throwaway "shared machine" for test-hostproxy.sh — Debian, its own dockerd, and somebody else's
# web server already serving somebody else's sites.
#
# Why a whole machine in a container, rather than the developer's own: Apply's helper bind-mounts the
# Docker host's `/` and chroots into it. Pointed at this container's dockerd, the "host" it edits is
# THIS container — so nginx/Apache/Caddy configs can be broken, applied, rolled back and removed for
# real, and nothing on the machine running the tests is touched.
#
# One stage per proxy, because WHAT IS INSTALLED is an input to detection (an idle apache2 package
# next to a listening nginx is its own scenario — `mixed`).
FROM debian:bookworm-slim AS base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      docker.io iptables procps iproute2 net-tools curl ca-certificates openssl python3 \
    && rm -rf /var/lib/apt/lists/*
COPY entry.sh upstream.py snap.sh /opt/gdhp/
RUN sed -i 's/\r$//' /opt/gdhp/*.sh /opt/gdhp/*.py && chmod +x /opt/gdhp/*.sh
ENTRYPOINT ["/opt/gdhp/entry.sh"]

FROM base AS bare

FROM base AS nginx
RUN apt-get update && apt-get install -y --no-install-recommends nginx && rm -rf /var/lib/apt/lists/*

FROM base AS apache
RUN apt-get update && apt-get install -y --no-install-recommends apache2 && rm -rf /var/lib/apt/lists/*

FROM base AS caddy
RUN apt-get update && apt-get install -y --no-install-recommends caddy && rm -rf /var/lib/apt/lists/*

FROM base AS mixed
RUN apt-get update && apt-get install -y --no-install-recommends nginx apache2 && rm -rf /var/lib/apt/lists/*

# For test-upgrade.sh: a machine the REAL installer and updaters can run on — git to clone, sudo
# because every script says `sudo docker`. The compose plugin is mounted in from the test host.
FROM base AS machine
RUN apt-get update && apt-get install -y --no-install-recommends git sudo && rm -rf /var/lib/apt/lists/*
