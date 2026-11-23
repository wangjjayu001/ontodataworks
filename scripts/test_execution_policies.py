"""Execute controlled previews against adversarial in-memory data policies."""
import copy
import re
import unittest
import duckdb
from test_agent_security import fixtures
from app.agent_planner import compile_preview, PlanningBlocked, verify_physical_grain, observe_measure_rows, apply_result_policies


class DuckConnection:
    def __init__(self):
        self.db = duckdb.connect(":memory:")
        self.db.execute("CREATE SCHEMA dwd; CREATE TABLE dwd.orders(order_id VARCHAR, value VARCHAR, purchased_at TIMESTAMP)")

    def execute(self, sql, parameters=None):
        text = sql if isinstance(sql, str) else sql.as_string()
        text = re.sub(r"%\(([a-z_][a-z0-9_]*)\)s", r"$\1", text)
        return self.db.execute(text, parameters) if parameters else self.db.execute(text)


class ExecutionPolicies(unittest.TestCase):
    def setUp(self):
        self.contract, self.bundle, self.intent = fixtures()
        self.contract.update(authority="AssetRevision+CatalogSnapshot", result_kind="COUNT", source_object="dwd.orders",
            physical_mapping={"dwd.orders": "dwd.orders"}, expression="COUNT(DISTINCT b.value)",
            metric_null_policy="REJECT_ROW_AND_WARN", empty_result_policy="NO_DATA", measure_expression="b.value",
            physical_grain_fields=["order_id"], physical_grain_expressions=["b.order_id"], metric_grain_expressions=["b.order_id"],
            time_event_aliases=["下单时间"], ordered_join_ids=[], join_sql_by_id={}, base_join_ids=[], dimension_join_ids={})
        self.conn = DuckConnection()
        self.addCleanup(self.conn.db.close)

    def preview(self):
        return compile_preview(self.contract, self.intent, self.bundle, "run", "req", {})

    def test_unknown_or_unimplemented_policies_never_use_sql_defaults(self):
        for field, values in (("metric_null_policy", ["UNKNOWN", "PROPAGATE"]), ("empty_result_policy", ["UNKNOWN", "ZERO_IF_COMPLETE"])):
            for value in values:
                with self.subTest(field=field, value=value):
                    original = self.contract[field]; self.contract[field] = value
                    with self.assertRaises(PlanningBlocked): self.preview()
                    self.contract[field] = original

    def test_null_rows_excluded_and_reported_without_losing_zero_like_values(self):
        self.conn.execute("INSERT INTO dwd.orders VALUES ('a','0','2025-06-01'),('b',NULL,'2025-06-01'),('c','2','2025-06-01')")
        frame, preview = self.preview()
        verify_physical_grain(self.conn, frame)
        counts = observe_measure_rows(self.conn, frame, preview["parameters"])
        rows = self.conn.execute(preview["sql"], preview["parameters"]).fetchall()
        values, no_data, warnings = apply_result_policies(frame, counts, rows)
        self.assertEqual(values, [(2,)])
        self.assertFalse(no_data)
        self.assertEqual(counts, {"source_rows": 3, "valid_measure_rows": 2, "null_measure_rows": 1})
        self.assertEqual(warnings[0]["row_count"], 1)

    def test_no_source_records_cannot_be_reported_as_count_zero(self):
        frame, preview = self.preview()
        counts = observe_measure_rows(self.conn, frame, preview["parameters"])
        sql_rows = self.conn.execute(preview["sql"], preview["parameters"]).fetchall()
        self.assertEqual(sql_rows, [(0,)])
        values, no_data, warnings = apply_result_policies(frame, counts, sql_rows)
        self.assertEqual(values, [])
        self.assertTrue(no_data)
        self.assertEqual(warnings, [])

    def test_all_null_measure_records_are_no_data_with_rejection_warning(self):
        self.conn.execute("INSERT INTO dwd.orders VALUES ('a',NULL,'2025-06-01')")
        frame, preview = self.preview()
        counts = observe_measure_rows(self.conn, frame, preview["parameters"])
        values, no_data, warnings = apply_result_policies(frame, counts, self.conn.execute(preview["sql"], preview["parameters"]).fetchall())
        self.assertEqual(values, [])
        self.assertTrue(no_data)
        self.assertEqual(warnings[0]["row_count"], 1)

    def test_duplicate_base_grain_cannot_be_hidden_by_count_distinct(self):
        self.conn.execute("INSERT INTO dwd.orders VALUES ('a','X','2025-06-01'),('a','X','2025-06-01')")
        frame, _ = self.preview()
        with self.assertRaisesRegex(PlanningBlocked, "粒度键存在空值或重复"):
            verify_physical_grain(self.conn, frame)

    def test_null_base_grain_is_rejected(self):
        self.conn.execute("INSERT INTO dwd.orders VALUES (NULL,'X','2025-06-01')")
        frame, _ = self.preview()
        with self.assertRaises(PlanningBlocked): verify_physical_grain(self.conn, frame)

    def test_average_cannot_use_a_different_business_grain(self):
        self.contract.update(result_kind="SCORE", metric_grain_expressions=["b.customer_id"])
        with self.assertRaises(PlanningBlocked): self.preview()


if __name__ == "__main__": unittest.main(verbosity=2)
