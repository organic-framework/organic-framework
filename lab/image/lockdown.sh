#!/bin/sh
# Lock a lab machine's egress (run as root inside the VM, after provisioning,
# before any agent starts). docs/lab.md, section 9, "Isolation from the Mac".
#
# Default deny for everything leaving the VM. The single exception is the model
# gateway on the Mac: GATEWAY_IP:GATEWAY_PORT. That blocks the Mac's other loopback
# services (ClickHouse, of-server, port-forwards, proxies), its LAN and its VPN
# routes (Acme's networks), DNS, and the public internet.
# Replies to inbound connections (Lima's SSH) are allowed as established traffic.
set -eu
GATEWAY_IP="${1:?gateway ip (host.lima.internal)}"
GATEWAY_PORT="${2:?gateway port}"

nft -f - <<EOF
flush ruleset
table inet oflab {
  chain output {
    type filter hook output priority 0; policy drop;
    oif "lo" accept
    ct state established,related accept
    ip daddr ${GATEWAY_IP} tcp dport ${GATEWAY_PORT} accept
    counter comment "dropped egress"
  }
  chain forward {
    type filter hook forward priority 0; policy drop;
  }
}
EOF
nft list ruleset > /etc/nftables.conf
systemctl enable nftables >/dev/null 2>&1 || true
echo "egress locked: only ${GATEWAY_IP}:${GATEWAY_PORT}"
