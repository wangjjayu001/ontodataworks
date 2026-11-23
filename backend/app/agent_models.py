"""Persistent Agent runtime. Content snapshots are append-only in the run payload;
the state head is guarded by run_version / requirement_version CAS.
"""
from sqlalchemy import String, Text, Integer, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, now


class AgentConversation(Base):
    __tablename__ = "wb_agent_conversation"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    actor_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class AgentRun(Base):
    __tablename__ = "wb_agent_run"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    actor_id: Mapped[int] = mapped_column(Integer, index=True)
    conversation_id: Mapped[str] = mapped_column(String(80), index=True)
    state: Mapped[str] = mapped_column(String(40), default="DRAFT")
    run_version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class AgentCommand(Base):
    __tablename__ = "wb_agent_command"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[int] = mapped_column(Integer)
    route: Mapped[str] = mapped_column(String(250))
    idempotency_key: Mapped[str] = mapped_column(String(180))
    input_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("workspace_id", "actor_id", "route", "idempotency_key"),)


class AgentEvent(Base):
    __tablename__ = "wb_agent_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, index=True)
    run_id: Mapped[str] = mapped_column(String(80), index=True)
    event_id: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("run_id", "event_id"),)


class AgentJob(Base):
    __tablename__ = "wb_agent_job"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str] = mapped_column(String(80), index=True)
    requirement_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    lease_until: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class AgentExecution(Base):
    __tablename__ = "wb_agent_execution"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str] = mapped_column(String(80), index=True)
    requirement_id: Mapped[str] = mapped_column(String(80))
    authorization_id: Mapped[str] = mapped_column(String(80), unique=True)
    frame_hash: Mapped[str] = mapped_column(String(64))
    sql_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    backend_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentFeedback(Base):
    __tablename__ = "wb_agent_feedback"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str] = mapped_column(String(80))
    actor_id: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(30), default="OPEN")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


AGENT_TABLES = [cls.__table__ for cls in (AgentConversation, AgentRun, AgentCommand,
    AgentEvent, AgentJob, AgentExecution, AgentFeedback)]


def migrate_agent(connection):
    """Explicit additive v4 migration; called by deployment, never normal startup."""
    for table in AGENT_TABLES:
        table.create(connection, checkfirst=True)
