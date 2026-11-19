"""hook_record and runtime_record keep their organization (2026-11-19, of_server): the tenant's vault holds several
organizations' raw rows, and without org_id they could not be told apart when one organization's records moved to its
own server (organic-framework-build's server move plan; of_architect, msg 01a1f61f-53c3: no reader breaks)."""

import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_route  # noqa: E402

spec = importlib.util.spec_from_file_location("ch_schema", os.path.join(HERE, "..", "deploy", "clickhouse", "schema.py"))
schema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schema)


class R:
    db_by_org = {"zeta": "db_zeta"}
    unassigned = "db_un"

    def database(self, org):
        return self.db_by_org.get(org, "db_un")

    def builder_of(self, *a):
        return None


class OrgIdTest(unittest.TestCase):

    def test_routing_keeps_the_organization_on_hook_and_runtime_rows(self):
        for table, row in (("hook_record", {"org_id": "zeta", "host_id": "h", "local_id": 1, "session_id": "S",
                                            "raw": "x"}),
                           ("runtime_record", {"org_id": "zeta", "host_id": "h", "type": "t", "pane_id": "p",
                                               "raw": "x"})):
            out = of_route.fanout(table, [row], R(), {"vault_database": "db_vault"}, copies=False)
            self.assertEqual(sorted(out), ["db_vault", "db_zeta"], table)
            for db, rows in out.items():
                self.assertEqual([r["org_id"] for r in rows], ["zeta"], (table, db))

    def test_the_schema_adds_the_column_to_tables_made_before_it(self):
        text = schema.schema(["db_zeta"], "db_vault", "db_un")
        for t in ("hook_record", "runtime_record"):
            self.assertIn("org_id LowCardinality(String) DEFAULT ''", schema.TABLES[t])
            for db in ("db_zeta", "db_vault", "db_un"):
                self.assertIn(f"ALTER TABLE {db}.{t} ADD COLUMN IF NOT EXISTS org_id LowCardinality(String) "
                              "DEFAULT '' AFTER tenant_id;", text)


if __name__ == "__main__":
    unittest.main()
