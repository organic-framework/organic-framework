#!/bin/bash
# Counts in the server's ClickHouse, per database, table and machine (host id), deduplicated.
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
Q() { kubectl -n clickhouse exec deploy/clickhouse -- clickhouse-client --user admin --password "$(cat /root/lab-secrets/admin.password)" -q "$1" 2>&1; }
Q "SHOW DATABASES" | tr '\n' ' '; echo
for db in of_lab_p2 of_lab_p2_vault of_lab_p2_unassigned; do
  for t in event transcript_record hook_record; do
    echo "$db.$t: $(Q "SELECT count() FROM $db.$t FINAL") rows; by host: $(Q "SELECT host_id AS h, count() FROM $db.$t FINAL GROUP BY h ORDER BY h FORMAT CSV" | tr '\n' ' ')"
  done
done
