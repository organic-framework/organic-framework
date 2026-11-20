"""An optional retention per ClickHouse table (`schema.py --ttl table=days`; the server config's `retention`): a user
organization's of-server store grew 1.3 to 1.7 GiB a day with none (2026-11-20, of_server). None by default."""

import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ch_schema_ttl", os.path.join(HERE, "..", "deploy", "clickhouse", "schema.py"))
schema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schema)


class RetentionTest(unittest.TestCase):

    def test_none_by_default(self):
        self.assertNotIn("TTL", schema.schema(["db_a"], "db_v", "db_u"))

    def test_a_retention_on_new_and_existing_tables_and_zero_removes_it(self):
        text = schema.schema(["db_a"], "db_v", "db_u", ttl={"transcript_record": 30, "event": 365, "hook_record": 0})
        for db in ("db_a", "db_v", "db_u"):
            self.assertIn(f"ALTER TABLE {db}.transcript_record MODIFY TTL toDateTime(ts) + INTERVAL 30 DAY;", text)
            self.assertIn(f"ALTER TABLE {db}.event MODIFY TTL toDateTime(ts) + INTERVAL 365 DAY;", text)
            self.assertIn(f"ALTER TABLE {db}.hook_record REMOVE TTL;", text)
            self.assertNotIn(f"ALTER TABLE {db}.runtime_record MODIFY TTL", text)   # not named: untouched
        create = text.split(f"CREATE TABLE IF NOT EXISTS db_a.transcript_record\n", 1)[1].split(";", 1)[0]
        self.assertTrue(create.endswith("TTL toDateTime(ts) + INTERVAL 30 DAY"))

    def test_a_bad_table_or_number_is_refused(self):
        for bad in ({"messages": 30}, {"event": -1}, {"event": "30"}, {"event": True}):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                schema.schema(["db_a"], "db_v", "db_u", ttl=bad)


if __name__ == "__main__":
    unittest.main()
