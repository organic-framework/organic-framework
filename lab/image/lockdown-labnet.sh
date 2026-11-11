#!/bin/sh
# Egress profile "labnet" (docs/lab.md, section 9): the other lab VMs on the vmnet subnet,
# DNS through the host resolver, and the public internet (packages, images). Dropped: the
# Mac (host gateway and its address on the vmnet subnet), its LAN, every other private
# range, link-local, and the public addresses the Mac routes through its VPN tunnel.
# Pods (k3s) are forwarded, so the forward chain repeats the drops; the cluster CIDRs
# 10.42/16 and 10.43/16 stay open.
# Usage: lockdown-labnet.sh <host_gateway_ip> <lab_cidr> <mac_ip_on_lab_net> [vpn_routed ...]
# Apply it detached (systemd-run), never inside the session that should outlive it: conntrack starts
# tracking only when this table is created, so a connection already open (the limactl/ssh session
# running the script) is first seen by its next outgoing packet as NEW to the host and dropped, and
# that session hangs for good. Sessions opened afterwards are tracked from their SYN and keep working,
# so the VM stays manageable with limactl shell (checked on a lab-net VM, 2026-11-07).
set -eu
HOSTGW="${1:?host gateway}"; LAB="${2:?lab cidr}"; MACIP="${3:?mac ip on lab net}"; shift 3
VPN=""
for c in "$@"; do VPN="${VPN}${VPN:+, }$c"; done
nft -f - <<NFT
add table inet oflab
delete table inet oflab
table inet oflab {
  chain output {
    type filter hook output priority 0; policy accept;
    oif "lo" accept
    ct state established,related accept
    udp dport { 67, 68 } accept
    ip daddr ${HOSTGW} udp dport 53 accept
    ip daddr ${HOSTGW} tcp dport 53 accept
    ip daddr ${HOSTGW} drop
    ip daddr ${MACIP} drop
    ip daddr ${LAB} accept
    ip daddr { 10.42.0.0/16, 10.43.0.0/16 } accept
    ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16 } drop
    $( [ -n "$VPN" ] && echo "ip daddr { ${VPN} } drop" )
    ip6 daddr { fc00::/7, fe80::/10 } drop
  }
  chain forward {
    type filter hook forward priority 0; policy accept;
    ct state established,related accept
    ip daddr ${HOSTGW} udp dport 53 accept
    ip daddr ${HOSTGW} drop
    ip daddr ${MACIP} drop
    ip daddr { ${LAB}, 10.42.0.0/16, 10.43.0.0/16 } accept
    ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16 } drop
    $( [ -n "$VPN" ] && echo "ip daddr { ${VPN} } drop" )
  }
}
NFT
# Only our own table is replaced: k3s keeps its iptables rules. Not persisted (disposable VMs).
echo "egress: labnet (${LAB} except ${MACIP}) + internet"
