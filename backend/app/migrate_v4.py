"""Explicit additive v3 -> v4 deployment command, with a transaction/advisory lock.
Run `docker compose run --rm --no-deps api python -m app.migrate_v4` before API v1.
"""
from sqlalchemy import text
from .db import engine, SchemaVersion
from .semantic_models import migrate_semantic
from .agent_models import migrate_agent


def main():
    with engine.connect() as conn:
        versions = set(conn.execute(text("SELECT version FROM wb_schema_version")).scalars())
        if 3 not in versions:
            raise RuntimeError("Schema v3 baseline is required before v4 migration")
    # semantic migration owns its transaction, and must be idempotent.
    migrate_semantic(engine)
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(419091004)"))
        versions = set(conn.execute(text("SELECT version FROM wb_schema_version")).scalars())
        if 3 not in versions:
            raise RuntimeError("Schema v3 baseline is required before v4 migration")
        migrate_agent(conn)
        if 4 not in versions:
            conn.execute(SchemaVersion.__table__.insert().values(version=4))
    print("Schema v4 additive migration completed; existing catalog and data retained.")


if __name__ == "__main__": main()
