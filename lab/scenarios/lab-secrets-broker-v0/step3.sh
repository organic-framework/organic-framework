#!/bin/bash
# Step 3: no service-account token on this holder machine (files + environment).
H=/home/lab; hits=0
for t in tok-reader tok-keeper tok-k8s; do
  n=$(grep -rl "$t" "$H" 2>/dev/null | grep -v organic-framework/lab | wc -l | tr -d ' ')
  [ "$n" != 0 ] && { echo "  FILE hit: $t in $n file(s)"; hits=$((hits+n)); }
done
tokfiles=$(find "$H" -name '*.token' 2>/dev/null | wc -l | tr -d ' ')
[ "$tokfiles" != 0 ] && { echo "  .token files: $tokfiles"; hits=$((hits+tokfiles)); }
env | grep -qi OP_SERVICE_ACCOUNT_TOKEN && { echo "  OP_SERVICE_ACCOUNT_TOKEN in env"; hits=$((hits+1)); }
[ "$hits" = 0 ] && echo "PASS: no service-account token in files or environment" || echo "FAIL: $hits token trace(s)"
