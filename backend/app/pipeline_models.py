"""v1.1 pipeline authority; explicitly installed by migrate_v5, never on startup."""
from datetime import datetime
from sqlalchemy import String, Text, JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, now


class Definition(Base):
    __tablename__ = "wb_pipeline_definition"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(150))
    revision: Mapped[int] = mapped_column(default=1)
    config: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(default=False)
    deleted: Mapped[bool] = mapped_column(default=False)
    actor_id: Mapped[int] = mapped_column(ForeignKey("wb_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DefinitionRevision(Base):
    __tablename__ = "wb_pipeline_revision"
    definition_id: Mapped[int] = mapped_column(ForeignKey("wb_pipeline_definition.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[int] = mapped_column(ForeignKey("wb_user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PipelineRun(Base):
    __tablename__ = "wb_pipeline_run"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"), index=True)
    definition_id: Mapped[int] = mapped_column(ForeignKey("wb_pipeline_definition.id"))
    revision: Mapped[int] = mapped_column()
    actor_id: Mapped[int] = mapped_column(ForeignKey("wb_user.id"))
    batch_key: Mapped[str] = mapped_column(String(120))
    request_key: Mapped[str] = mapped_column(String(160))
    business_date: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    attempt: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    snapshot: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("workspace_id", "request_key"),)


class RunEvent(Base):
    __tablename__ = "wb_pipeline_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("wb_pipeline_run.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    delivered: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CatalogObservation(Base):
    __tablename__ = "wb_catalog_observation"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    datasource_id: Mapped[int] = mapped_column(ForeignKey("wb_datasource.id"))
    content: Mapped[list] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    changes: Mapped[list] = mapped_column(JSON)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SourceUse(Base):
    __tablename__ = "wb_source_use"
    datasource_id: Mapped[int] = mapped_column(ForeignKey("wb_datasource.id"), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    purpose: Mapped[str] = mapped_column(String(20), default="read")
    actor_id: Mapped[int] = mapped_column(ForeignKey("wb_user.id"))


class SourceEnrollment(Base):
    __tablename__ = "wb_source_enrollment"
    datasource_id: Mapped[int] = mapped_column(ForeignKey("wb_datasource.id"), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    identity_hash: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(default=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("wb_user.id"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Publication(Base):
    __tablename__ = "wb_physical_publication"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    model_id: Mapped[int] = mapped_column(ForeignKey("wb_modeling_document.id"))
    model_revision: Mapped[int] = mapped_column()
    datasource_id: Mapped[int] = mapped_column(ForeignKey("wb_datasource.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("wb_user.id"))
    status: Mapped[str] = mapped_column(String(24), default="PREVIEW")
    plan: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DataBatch(Base):
    __tablename__ = "wb_data_batch"
    run_id: Mapped[str] = mapped_column(ForeignKey("wb_pipeline_run.id"), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("wb_catalog_asset.id"), index=True)
    batch_key: Mapped[str] = mapped_column(String(120))
    data_as_of: Mapped[str | None] = mapped_column(String(100), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GovernanceItem(Base):
    __tablename__ = "wb_governance_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(150))
    revision: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(24), default="OPEN")
    content: Mapped[dict] = mapped_column(JSON)
    history: Mapped[list] = mapped_column(JSON, default=list)
    actor_id: Mapped[int] = mapped_column(ForeignKey("wb_user.id"))
    deleted: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ServiceConsumer(Base):
    __tablename__ = "wb_service_consumer"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    name: Mapped[str] = mapped_column(String(150))
    principal_id: Mapped[int] = mapped_column(ForeignKey("wb_user.id"))
    token_hash: Mapped[str] = mapped_column(String(64))
    service_ids: Mapped[list] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(default=True)


class ServiceCall(Base):
    __tablename__ = "wb_service_call"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("wb_workspace.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("wb_pipeline_definition.id"))
    consumer_id: Mapped[int | None] = mapped_column(ForeignKey("wb_service_consumer.id"), nullable=True)
    revision: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(20))
    duration_ms: Mapped[int] = mapped_column(default=0)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ServiceGrant(Base):
    __tablename__ = "wb_service_grant"
    consumer_id: Mapped[int] = mapped_column(ForeignKey("wb_service_consumer.id"), primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("wb_pipeline_definition.id"), primary_key=True)
    revision: Mapped[int] = mapped_column()


PIPELINE_TABLES = [Definition.__table__, DefinitionRevision.__table__, PipelineRun.__table__, RunEvent.__table__,
    CatalogObservation.__table__, SourceUse.__table__, Publication.__table__, DataBatch.__table__, GovernanceItem.__table__,
    ServiceConsumer.__table__, ServiceCall.__table__, ServiceGrant.__table__, SourceEnrollment.__table__]
