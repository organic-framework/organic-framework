#!/bin/sh
# Isolation assertions for a lab machine (docs/lab.md, section 10). Run as the user
# `lab` inside the VM. Every probe must fail except the model gateway.
# Usage: isolation.sh <gateway_ip> <gateway_port> <lan_gateway> <vpn_addr>...
GW="$1"; GWPORT="$2"; LAN="$3"; shift 3
fail=0
probe() {  # probe <label> <expect: open|closed> <host> <port>
  if timeout 3 bash -c "exec 3<>/dev/tcp/$3/$4" 2>/dev/null; then got=open; else got=closed; fi
  if [ "$got" = "$2" ]; then echo "PASS $1 ($3:$4 $got)"; else echo "FAIL $1 ($3:$4 $got, expected $2)"; fail=1; fi
}
for port in 18123 19000 5432 1080 19092 5000; do probe "mac-loopback" closed "$GW" "$port"; done
probe "lan-gateway" closed "$LAN" 80
probe "lan-gateway-dns" closed "$LAN" 53
for a in "$@"; do probe "vpn-route" closed "$a" 22; probe "vpn-route" closed "$a" 443; done
probe "public-https" closed 1.1.1.1 443
if timeout 3 getent hosts example.com >/dev/null 2>&1; then echo "FAIL dns (resolved)"; fail=1; else echo "PASS dns (no resolution)"; fi
if sudo -n true 2>/dev/null; then echo "FAIL sudo (allowed)"; fail=1; else echo "PASS sudo (denied)"; fi
probe "model-gateway" open "$GW" "$GWPORT"
[ "$fail" = 0 ] && echo "ISOLATION OK" || echo "ISOLATION FAILED"
exit "$fail"
