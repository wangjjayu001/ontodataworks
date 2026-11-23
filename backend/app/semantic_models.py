"""Semantic v1 storage. Revision payloads are append-only; control state is separate."""
from sqlalchemy import String, Integer, Text, DateTime, JSON, UniqueConstraint, ForeignKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, now


class SemanticRevision(Base):
    __tablename__ = "wb_semantic_asset_revision"
    workspace_id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_type: Mapped[str] = mapped_column(String(24), index=True)
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticGovernance(Base):
    __tablename__ = "wb_semantic_governance"
    workspace_id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    dependency_hash: Mapped[str] = mapped_column(String(64))
    workflow_state: Mapped[str] = mapped_column(String(24), default="DRAFT")
    validation_state: Mapped[str] = mapped_column(String(24), default="NOT_RUN")
    record_version: Mapped[int] = mapped_column(Integer, default=1)
    issue_codes: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (ForeignKeyConstraint(["workspace_id", "asset_id", "revision"], ["wb_semantic_asset_revision.workspace_id", "wb_semantic_asset_revision.asset_id", "wb_semantic_asset_revision.revision"]),)


class SemanticReference(Base):
    __tablename__ = "wb_semantic_revision_ref"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    asset_id: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer)
    slot: Mapped[str] = mapped_column(Text)
    target: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (ForeignKeyConstraint(["workspace_id", "asset_id", "revision"], ["wb_semantic_asset_revision.workspace_id", "wb_semantic_asset_revision.asset_id", "wb_semantic_asset_revision.revision"]),)


class SemanticVerification(Base):
    __tablename__ = "wb_semantic_verification"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    asset_id: Mapped[str] = mapped_column(String(200), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticApproval(Base):
    __tablename__ = "wb_semantic_approval"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    asset_id: Mapped[str] = mapped_column(String(200), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticSnapshot(Base):
    __tablename__ = "wb_semantic_catalog_snapshot"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticRelease(Base):
    __tablename__ = "wb_semantic_release"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    scope: Mapped[str] = mapped_column(String(24))
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticIndex(Base):
    __tablename__ = "wb_semantic_index_snapshot"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    release_id: Mapped[str] = mapped_column(String(100))
    release_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict] = mapped_column(JSON)
    documents: Mapped[list] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticDeployment(Base):
    __tablename__ = "wb_semantic_deployment"
    workspace_id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(24), primary_key=True)
    pointer_version: Mapped[int] = mapped_column(Integer, default=1)
    release_id: Mapped[str] = mapped_column(String(100))
    release_hash: Mapped[str] = mapped_column(String(64))
    index_snapshot_id: Mapped[str] = mapped_column(String(100))
    index_snapshot_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[int] = mapped_column(Integer)
    activated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticEvent(Base):
    __tablename__ = "wb_semantic_outbox"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticStaging(Base):
    __tablename__ = "wb_semantic_migration_staging"
    workspace_id: Mapped[int] = mapped_column(primary_key=True)
    old_uid: Mapped[str] = mapped_column(String(250), primary_key=True)
    role: Mapped[str] = mapped_column(String(24), index=True)
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    migration_status: Mapped[str] = mapped_column(String(40), default="DRAFT_MAPPING")


class SemanticContract(Base):
    __tablename__ = "wb_semantic_execution_contract"
    workspace_id: Mapped[int] = mapped_column(primary_key=True)
    binding_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))


class SemanticSourceBlob(Base):
    __tablename__ = "wb_semantic_source_snapshot"
    workspace_id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[dict] = mapped_column(JSON)


class SemanticCommand(Base):
    __tablename__ = "wb_semantic_command"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticBundle(Base):
    __tablename__ = "wb_agent_retrieval_bundle"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    requirement_id: Mapped[str] = mapped_column(String(100))
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticAssignment(Base):
    __tablename__ = "wb_semantic_role_assignment"
    workspace_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(24), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    active: Mapped[int] = mapped_column(Integer, default=1)
    assigned_by: Mapped[int] = mapped_column(Integer)
    record_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class SemanticPolicy(Base):
    __tablename__ = "wb_semantic_policy"
    workspace_id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    active: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


SEMANTIC_TABLES = [c.__table__ for c in (SemanticRevision, SemanticGovernance, SemanticReference,
    SemanticVerification, SemanticApproval, SemanticSnapshot, SemanticRelease, SemanticIndex,
    SemanticDeployment, SemanticEvent, SemanticStaging, SemanticContract, SemanticSourceBlob, SemanticCommand, SemanticBundle,
    SemanticAssignment, SemanticPolicy)]


def migrate_semantic(engine):
    """Explicit additive migration; caller records v4 only after all v4 modules succeed."""
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text
            conn.execute(text("SELECT pg_advisory_xact_lock(190901004)"))
        Base.metadata.create_all(conn, tables=SEMANTIC_TABLES)
        if conn.dialect.name == "postgresql":
            from sqlalchemy import text
            conn.execute(text("""CREATE OR REPLACE FUNCTION wb_semantic_revision_immutable() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Semantic revisions are immutable'; END $$"""))
            for table in ("wb_semantic_asset_revision", "wb_semantic_approval", "wb_semantic_verification", "wb_semantic_catalog_snapshot", "wb_semantic_source_snapshot", "wb_semantic_release", "wb_semantic_index_snapshot", "wb_semantic_execution_contract"):
                conn.execute(text(f"DROP TRIGGER IF EXISTS immutable_content ON {table}"))
                conn.execute(text(f"CREATE TRIGGER immutable_content BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION wb_semantic_revision_immutable()"))
    return {"migration": "semantic-v4-additive", "tables": len(SEMANTIC_TABLES), "schema_version_recorded": False}
