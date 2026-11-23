import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Text, Integer, DateTime, ForeignKey, UniqueConstraint, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True, pool_size=8)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "wb_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="developer")
    enabled: Mapped[int] = mapped_column(Integer, default=1)


class Workspace(Base):
    __tablename__ = "wb_workspace"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Membership(Base):
    __tablename__ = "wb_membership"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("wb_user.id"))
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    __table_args__ = (UniqueConstraint("user_id", "workspace_id"),)


class LoginSession(Base):
    __tablename__ = "wb_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("wb_user.id"))
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Audit(Base):
    __tablename__ = "wb_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Resource(Base):
    """Versioned modeling documents; typed validators own each document schema."""
    __tablename__ = "wb_modeling_document"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    kind: Mapped[str] = mapped_column(String(24))
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    domain_id: Mapped[int | None] = mapped_column(ForeignKey("wb_modeling_document.id"), nullable=True)
    layer: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("workspace_id", "kind", "code"),)


class ResourceRevision(Base):
    __tablename__ = "wb_modeling_revision"
    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("wb_modeling_document.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Datasource(Base):
    __tablename__ = "wb_datasource"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    name: Mapped[str] = mapped_column(String(100))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(default=5432)
    database: Mapped[str] = mapped_column(String(100))
    username: Mapped[str] = mapped_column(String(100))
    secret: Mapped[str] = mapped_column(Text)
    schemas: Mapped[list] = mapped_column(JSON, default=lambda: ["public"])
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Asset(Base):
    __tablename__ = "wb_catalog_asset"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    datasource_id: Mapped[int] = mapped_column(ForeignKey("wb_datasource.id"))
    namespace: Mapped[str] = mapped_column(String(100))
    table_name: Mapped[str] = mapped_column(String(150))
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    layer: Mapped[str] = mapped_column(String(10), default="")
    domain_id: Mapped[int | None] = mapped_column(ForeignKey("wb_modeling_document.id"), nullable=True)
    columns: Mapped[list] = mapped_column(JSON, default=list)
    row_estimate: Mapped[int | None] = mapped_column(nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="active")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("datasource_id", "namespace", "table_name"),)


class QueryRun(Base):
    __tablename__ = "wb_query_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    datasource_id: Mapped[int] = mapped_column(ForeignKey("wb_datasource.id"))
    actor: Mapped[str] = mapped_column(String(80))
    sql: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    row_count: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int] = mapped_column(default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Lineage(Base):
    __tablename__ = "wb_lineage_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    query_id: Mapped[int] = mapped_column(ForeignKey("wb_query_run.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("wb_catalog_asset.id"))
    source: Mapped[str] = mapped_column(String(250))
    target: Mapped[str] = mapped_column(String(100))
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SchemaVersion(Base):
    __tablename__ = "wb_schema_version"
    version: Mapped[int] = mapped_column(primary_key=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TransformRun(Base):
    __tablename__ = "wb_transform_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    datasource_id: Mapped[int] = mapped_column(ForeignKey("wb_datasource.id"))
    target_asset_id: Mapped[int] = mapped_column(ForeignKey("wb_catalog_asset.id"))
    run_key: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    select_sql: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("workspace_id", "run_key"),)


class TransformEdge(Base):
    __tablename__ = "wb_transform_edge"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    run_id: Mapped[int] = mapped_column(ForeignKey("wb_transform_run.id"))
    source_asset_id: Mapped[int] = mapped_column(ForeignKey("wb_catalog_asset.id"))
    target_asset_id: Mapped[int] = mapped_column(ForeignKey("wb_catalog_asset.id"))


class CatalogMetadata(Base):
    """Steward-maintained Chinese definitions, independent of physical schema fingerprints."""
    __tablename__ = "wb_catalog_metadata"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("wb_catalog_asset.id"), unique=True)
    spec: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(default=1)
    actor: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CatalogMetadataRevision(Base):
    __tablename__ = "wb_catalog_metadata_revision"
    id: Mapped[int] = mapped_column(primary_key=True)
    metadata_id: Mapped[int] = mapped_column(ForeignKey("wb_catalog_metadata.id"))
    revision: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("metadata_id", "revision"),)
