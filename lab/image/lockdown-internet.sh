#!/bin/sh
# Egress profile "internet" for scenarios whose subject must download things (the
# quickstart: pip, herdr's installer). docs/lab.md, section 9.
# Public internet is allowed. Everything private is dropped: the Mac (host gateway),
# its LAN, RFC 1918, CGNAT (Tailscale-style VPNs), link-local (cloud metadata), and
# the public addresses the Mac routes through its VPN tunnel (passed as arguments).
# Usage: lockdown-internet.sh <host_gateway_ip> [vpn_routed_cidr ...]
set -eu
HOSTGW="${1:?host gateway ip}"; shift
VPN=""
for c in "$@"; do VPN="${VPN}${VPN:+, }$c"; done
nft -f - <<EOF
flush ruleset
table inet oflab {
  chain output {
    type filter hook output priority 0; policy accept;
    oif "lo" accept
    ct state established,related accept
    ip daddr ${HOSTGW} udp dport 53 accept
    udp dport { 67, 68 } accept
    ip daddr ${HOSTGW} tcp dport 53 accept
    ip daddr ${HOSTGW} drop
    ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16 } drop
    $( [ -n "$VPN" ] && echo "ip daddr { ${VPN} } drop" )
    ip6 daddr { fc00::/7, fe80::/10 } drop
  }
  chain forward { type filter hook forward priority 0; policy drop; }
}
EOF
nft list ruleset > /etc/nftables.conf
systemctl enable nftables >/dev/null 2>&1 || true
echo "egress: internet only (private, host and VPN-routed addresses dropped)"
