"""An optional retention per ClickHouse table (`schema.py --ttl table=days`; the server config's `retention`): a user
organization's of-server store grew 1.3 to 1.7 GiB a day with none (2026-11-20, of_server). None by default."""

import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ch_schema_ttl", os.path.join(HERE, "..", "deploy", "clickhouse", "schema.py"))
schema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schema)


class RetentionTest(unittest.TestCase):

    def test_none_by_default(self):
        self.assertNotIn("TTL", schema.schema(["db_a"], "db_v", "db_u"))

    def test_a_retention_on_one_organizations_database_alone_and_zero_removes_it(self):
        ttl = {("db_a", "transcript_record"): 30, ("db_a", "event"): 365, ("db_a", "hook_record"): 0}
        text = schema.schema(["db_a", "db_b"], "db_v", "db_u", ttl=ttl)
        self.assertIn("ALTER TABLE db_a.transcript_record MODIFY TTL toDateTime(ts) + INTERVAL 30 DAY "
                      "DELETE WHERE toUnixTimestamp(ts) >= 86400;", text)       # a row with no time never ages
        self.assertIn("ALTER TABLE db_a.event MODIFY TTL toDateTime(ts) + INTERVAL 365 DAY "
                      "DELETE WHERE toUnixTimestamp(ts) >= 86400;", text)
        self.assertIn("ALTER TABLE db_a.hook_record REMOVE TTL;", text)
        self.assertNotIn("ALTER TABLE db_a.runtime_record MODIFY TTL", text)        # not named: untouched
        for db in ("db_b", "db_v", "db_u"):                                        # another organization's, the
            self.assertNotIn(f"ALTER TABLE {db}.transcript_record MODIFY TTL", text)   # vault, the unassigned
            self.assertNotIn(f"ALTER TABLE {db}.event MODIFY TTL", text)
            self.assertNotIn(f"ALTER TABLE {db}.hook_record REMOVE TTL", text)
        create = text.split("CREATE TABLE IF NOT EXISTS db_a.transcript_record\n", 1)[1].split(";", 1)[0]
        self.assertTrue(create.endswith("TTL toDateTime(ts) + INTERVAL 30 DAY DELETE WHERE toUnixTimestamp(ts) >= 86400"))
        other = text.split("CREATE TABLE IF NOT EXISTS db_b.transcript_record\n", 1)[1].split(";", 1)[0]
        self.assertNotIn("TTL", other)

    def test_the_vault_the_unassigned_database_and_a_bare_table_are_refused(self):
        """of_secrets-review on organic-framework PR 438: one --ttl reached every database."""
        for bad in ({("db_v", "event"): 30}, {("db_u", "hook_record"): 30}, {"event": 30}, {("db_x", "event"): 30},
                    {("db_a", "messages"): 30}, {("db_a", "event"): -1}, {("db_a", "event"): "30"},
                    {("db_a", "event"): True}):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                schema.schema(["db_a"], "db_v", "db_u", ttl=bad)

    def test_the_command_line_names_the_database(self):
        import subprocess
        path = os.path.join(HERE, "..", "deploy", "clickhouse", "schema.py")
        base = [sys.executable, path, "--org", "db_a", "--vault", "db_v", "--unassigned", "db_u"]
        ok = subprocess.run(base + ["--ttl", "db_a.event=90"], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0)
        self.assertIn("ALTER TABLE db_a.event MODIFY TTL toDateTime(ts) + INTERVAL 90 DAY DELETE WHERE", ok.stdout)
        for bad in ("event=90", "db_v.event=90", "db_a.event=x"):
            with self.subTest(bad=bad):
                self.assertNotEqual(subprocess.run(base + ["--ttl", bad], capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
