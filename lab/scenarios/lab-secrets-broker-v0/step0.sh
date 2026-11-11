#!/bin/bash
# Step 0: 1Password's boundary holds without the broker (the fake op enforces each token's vaults).
cd /home/lab/op
r="op://t-k8s/kube-admin/credential"; g="op://t-obs/grafana-viewer/credential"
OP_SERVICE_ACCOUNT_TOKEN=tok-reader ./op read --no-newline "$r" >/dev/null 2>&1 && echo "step0a FAIL: reader read a t-k8s vault" || echo "step0a PASS: reader token refused the t-k8s vault"
OP_SERVICE_ACCOUNT_TOKEN=tok-reader ./op read --no-newline "$g" >/dev/null 2>&1 && echo "step0b PASS: reader token read its own t-obs vault" || echo "step0b FAIL: reader refused its own vault"
