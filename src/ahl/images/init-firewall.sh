#!/usr/bin/env sh
set -eu

# Firewall hook — permissive for now (full egress allowed).
# Re-enable default-deny egress here when isolation is needed again; the
# container is launched as `init-firewall.sh <cmd>` so this is the single
# place to add iptables rules (requires `--cap-add=NET_ADMIN` on docker run).

exec "$@"
