"""Additive v4 -> v5; old tables and semantic authority remain intact."""
from sqlalchemy import text
from .db import engine, Base, SchemaVersion
from .pipeline_models import PIPELINE_TABLES


def main():
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(419091005)"))
        versions = set(conn.execute(text("SELECT version FROM wb_schema_version")).scalars())
        if 4 not in versions:
            raise RuntimeError("Schema v4 is required")
        Base.metadata.create_all(conn, tables=PIPELINE_TABLES)
        conn.execute(text("""CREATE OR REPLACE FUNCTION wb_pipeline_immutable() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Immutable pipeline evidence'; END; $$ LANGUAGE plpgsql"""))
        for name in ("wb_pipeline_revision", "wb_catalog_observation", "wb_data_batch"):
            conn.execute(text(f"DROP TRIGGER IF EXISTS immutable_evidence ON {name}"))
            conn.execute(text(f"CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION wb_pipeline_immutable()"))
        if 5 not in versions:
            conn.execute(SchemaVersion.__table__.insert().values(version=5))
    print("Schema v5 installed; immutable revisions, observations and committed batches protected.")


if __name__ == "__main__":
    main()
