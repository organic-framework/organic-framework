#!/usr/bin/env python3
"""Print the ClickHouse schema one of-server needs (docs/storage.md, docs/server.md).

For each organization database: the tables event, transcript_record, hook_record and
runtime_record (ReplacingMergeTree on the natural keys), the views v_* without raw content, an
insert-only user `<db>_ingest`, a delete-only user `<db>_redact` (redaction, docs/server.md) and a
read-only user `<db>_read` on the views (`v_event` without finance facts, which are owner-only). The vault
and the unassigned database get the same tables
and the `_ingest` and `_redact` users; only the tenant reads them.

Passwords are never printed: with --password-dir the users get `sha256_hash` with a random salt,
computed from `<dir>/<user>.password`; without it the CREATE USER lines are left for the keeper.

  python3 schema.py --org of_acme --vault of_acme_vault \
      --unassigned of_acme_unassigned [--password-dir DIR] [--cluster NAME]
"""

import argparse
import hashlib
import os
import secrets
import sys

TABLES = {
    "event": """(
    tenant_id LowCardinality(String), org_id LowCardinality(String), host_id LowCardinality(String),
    event_id String, ts DateTime64(3, 'UTC'), name LowCardinality(String), session_id String,
    position LowCardinality(String), source LowCardinality(String), harness_name LowCardinality(String),
    harness_version LowCardinality(String), model LowCardinality(String),
    payload String CODEC(ZSTD(6)), raw_ref String, record String CODEC(ZSTD(6))
) ENGINE = ReplacingMergeTree PARTITION BY toYYYYMM(ts) ORDER BY (org_id, name, ts, event_id)""",
    "transcript_record": """(
    tenant_id LowCardinality(String), org_id LowCardinality(String), host_id LowCardinality(String),
    session_id String, record_uuid String, parent_uuid String, type LowCardinality(String),
    ts DateTime64(3, 'UTC'), model LowCardinality(String), tool_names String,
    input_tokens UInt32, output_tokens UInt32, cache_read_tokens UInt32, cache_write_tokens UInt32,
    harness LowCardinality(String), source_path String, line_offset UInt64, sha256 FixedString(64),
    raw String CODEC(ZSTD(9))
) ENGINE = ReplacingMergeTree PARTITION BY toYYYYMM(ts) ORDER BY (session_id, record_uuid)""",
    "hook_record": """(
    tenant_id LowCardinality(String), host_id LowCardinality(String), local_id UInt64,
    received_at DateTime64(3, 'UTC'), hook_event LowCardinality(String), session_id String,
    sha256 FixedString(64), raw String CODEC(ZSTD(9))
) ENGINE = ReplacingMergeTree ORDER BY (host_id, local_id)""",
    "runtime_record": """(
    tenant_id LowCardinality(String), host_id LowCardinality(String), received_at DateTime64(3, 'UTC'),
    runtime LowCardinality(String), type LowCardinality(String), pane_id String,
    raw String CODEC(ZSTD(9))
) ENGINE = ReplacingMergeTree PARTITION BY toYYYYMM(received_at) ORDER BY (host_id, received_at, type, pane_id)""",
}
RAW = ("transcript_record", "hook_record", "runtime_record")
# The columns a redaction's condition reads (natural key, host, the event name it never deletes).
REDACT_COLUMNS = {"event": ("event_id", "host_id", "name"), "transcript_record": ("session_id", "record_uuid", "host_id"),
                  "hook_record": ("host_id", "local_id"), "runtime_record": ("host_id", "received_at", "type", "pane_id")}


FINANCE = "of.finance_fact"  # owner-only rows of the event table


def user_sql(user, password_dir, on):
    path = os.path.join(password_dir, f"{user}.password") if password_dir else None
    if path and os.path.exists(path):
        with open(path, "rb") as fh:
            pw = fh.read().strip()
        salt = secrets.token_hex(16)
        h = hashlib.sha256(pw + salt.encode()).hexdigest()
        return f"CREATE USER IF NOT EXISTS {user}{on} IDENTIFIED WITH sha256_hash BY '{h}' SALT '{salt}';"
    return f"-- CREATE USER IF NOT EXISTS {user}{on} IDENTIFIED BY '<from the secret manager>';"


def users(org_dbs, vault, unassigned):
    """Every user the schema grants to: its password must exist before the GRANTs run."""
    return [f"{db}_{r}" for db in [*org_dbs, vault, unassigned] for r in ("ingest", "redact")] + \
        [f"{db}_read" for db in org_dbs]


def schema(org_dbs, vault, unassigned, password_dir=None, cluster=None):
    if password_dir:
        # A missing file would leave its CREATE USER commented and the GRANT failing with
        # UNKNOWN_ROLE halfway through the script (found by the Lab, 2026-11-07): refuse up front.
        missing = [u for u in users(org_dbs, vault, unassigned)
                   if not os.path.exists(os.path.join(password_dir, f"{u}.password"))]
        if missing:
            raise SystemExit(f"schema.py: no password file in {password_dir} for: {', '.join(missing)}")
    on = f" ON CLUSTER {cluster}" if cluster else ""
    out = []
    for db in [*org_dbs, vault, unassigned]:
        out.append(f"CREATE DATABASE IF NOT EXISTS {db}{on};")
        for t, body in TABLES.items():
            out.append(f"CREATE TABLE IF NOT EXISTS {db}.{t}{on}\n{body};")
        out.append(user_sql(f"{db}_ingest", password_dir, on))
        out.append(f"GRANT{on} INSERT ON {db}.* TO {db}_ingest;")
        # Redaction (docs/server.md): delete only, on the four tables, reading only the columns a
        # redaction's condition names. The ingest user stays insert-only.
        out.append(user_sql(f"{db}_redact", password_dir, on))
        for t, cols in REDACT_COLUMNS.items():
            out.append(f"GRANT{on} SELECT({', '.join(cols)}), ALTER DELETE ON {db}.{t} TO {db}_redact;")
    for db in org_dbs:
        for t in TABLES:
            sel = "SELECT * EXCEPT raw" if t in RAW else "SELECT *"
            if t == "event":
                # Finance facts are owner-only (docs/events.md, Finance facts): the colleagues' view leaves them
                # out. A row policy on the table would not reach `_read`: the views run as their definer.
                # OR REPLACE, so an existing installation gets the filter too.
                out.append(f"CREATE OR REPLACE VIEW {db}.v_event{on} SQL SECURITY DEFINER AS {sel} FROM {db}.event"
                           f" WHERE name != '{FINANCE}';")
                continue
            out.append(f"CREATE VIEW IF NOT EXISTS {db}.v_{t}{on} SQL SECURITY DEFINER AS {sel} FROM {db}.{t};")
        out.append(user_sql(f"{db}_read", password_dir, on))
        out += [f"GRANT{on} SELECT ON {db}.v_{t} TO {db}_read;" for t in TABLES]
    return "\n\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--org", action="append", required=True, help="an organization database (repeatable)")
    ap.add_argument("--vault", required=True)
    ap.add_argument("--unassigned", required=True)
    ap.add_argument("--password-dir")
    ap.add_argument("--cluster")
    a = ap.parse_args()
    print(schema(a.org, a.vault, a.unassigned, a.password_dir, a.cluster), end="")
    if not a.password_dir:
        print("schema.py: without --password-dir the CREATE USER lines are comments; create these users "
              f"before running it: {', '.join(users(a.org, a.vault, a.unassigned))}", file=sys.stderr)


if __name__ == "__main__":
    main()
