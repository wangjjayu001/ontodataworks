"""Validated v1.1 contracts. No arbitrary write SQL or executable rule strings."""
from typing import Literal
from datetime import date
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .warehouse import IDENTIFIER


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rule(Contract):
    kind: Literal["not_null", "unique", "range", "enum", "row_count", "reference"]
    column: str | None = None
    severity: Literal["error", "warning"] = "error"
    minimum: float | None = None
    maximum: float | None = None
    values: list[str | int | float | bool] = Field(default_factory=list, max_length=200)
    reference_asset_id: int | None = None
    reference_column: str | None = None


class Schedule(Contract):
    cron: str = Field(pattern=r"^[0-9*/ ,\-]+$", max_length=100)
    timezone: str
    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, value):
        try: ZoneInfo(value)
        except (KeyError, ValueError): raise ValueError("未知调度时区")
        return value


class TaskConfig(Contract):
    datasource_id: int
    connection_identities: dict[str, str] = Field(default_factory=dict)
    catalog_fingerprints: dict[str, str] = Field(default_factory=dict)
    standard_refs: list[dict] = Field(default_factory=list, max_length=50)
    source_asset_id: int | None = None
    target_asset_id: int | None = None
    sql: str | None = Field(default=None, max_length=40000)
    columns: list[str] = Field(default_factory=list, max_length=200)
    mapping: dict[str, str] = Field(default_factory=dict)
    strategy: Literal["replace", "upsert"] = "replace"
    keys: list[str] = Field(default_factory=list, max_length=20)
    watermark_column: str | None = None
    data_as_of_column: str | None = None
    dependencies: list[int] = Field(default_factory=list, max_length=30)
    rules: list[Rule] = Field(default_factory=list, max_length=50)
    quality_definition_ids: list[int] = Field(default_factory=list, max_length=30)
    schedule: Schedule | None = None
    max_rows: int = Field(default=10000, ge=1, le=100000)
    freshness_minutes: int | None = Field(default=None, ge=1, le=525600)


class Parameter(Contract):
    type: Literal["string", "integer", "number", "date", "boolean"]
    minimum: float | None = None
    maximum: float | None = None
    max_length: int = Field(default=200, ge=1, le=2000)
    allowed_values: list = Field(default_factory=list, max_length=200)


class ServiceConfig(Contract):
    datasource_id: int
    connection_identities: dict[str, str] = Field(default_factory=dict)
    catalog_fingerprints: dict[str, str] = Field(default_factory=dict)
    sql: str = Field(min_length=1, max_length=40000)
    parameters: dict[str, Parameter] = Field(default_factory=dict)
    calls_per_minute: int = Field(default=30, ge=1, le=300)
    max_rows: int = Field(default=1000, ge=1, le=1000)
    contract_version: str = Field(min_length=1, max_length=40)


class DefinitionBody(Contract):
    kind: Literal["collect", "sync", "sql", "quality", "service"]
    name: str = Field(min_length=1, max_length=150)
    revision: int = Field(default=0, ge=0)
    config: dict
    enabled: bool = False


class RunBody(Contract):
    request_key: str = Field(min_length=1, max_length=160)
    batch_key: str = Field(min_length=1, max_length=120)
    business_date: date


class GovernanceBody(Contract):
    kind: Literal["issue", "standard", "analysis", "access_policy"]
    name: str = Field(min_length=1, max_length=150)
    revision: int = Field(default=0, ge=0)
    status: Literal["OPEN", "ASSIGNED", "FIXED", "CLOSED", "DRAFT", "REVIEW", "APPROVED", "WITHDRAWN"] = "OPEN"
    content: dict


class IssueContent(Contract):
    description: str = Field(min_length=1, max_length=4000)
    category: Literal["quality", "task", "schema", "semantic", "feedback"] = "quality"
    resolution: str = Field(default="", max_length=4000)
    verification_note: str = Field(default="", max_length=4000)
    verification_observation_id: int | None = None
    semantic_ref: dict | None = None
    verification_actor_id: int | None = None
    verified_at: str | None = None
    owner_id: int | None = None
    asset_id: int | None = None
    run_id: str | None = None
    repair_run_id: str | None = None
    retest_run_id: str | None = None
    event_key: str | None = Field(default=None, max_length=180)


class StandardContent(Contract):
    category: Literal["unit", "currency", "timezone", "null", "codebook"]
    definition: str = Field(min_length=1, max_length=4000)
    value: str | None = Field(default=None, max_length=200)
    owner_id: int | None = None
    source_ref: dict | None = None
    valid_until: date | None = None
    values: dict[str, str] = Field(default_factory=dict)
    bindings: list[dict] = Field(default_factory=list, max_length=100)
    rules: list[Rule] = Field(default_factory=list, max_length=50)


class AnalysisContent(Contract):
    question: str = Field(default="", max_length=2000)
    datasource_id: int
    sql: str = Field(min_length=1, max_length=40000)
    chart: Literal["table", "bar", "line", "pie"] = "table"
    source_run_id: str | None = None


class AccessContent(Contract):
    asset_id: int
    user_ids: list[int] = Field(min_length=1, max_length=100)
    allowed_columns: list[str] | None = None
    row_equals: dict[str, str | int | float | bool] = Field(default_factory=dict)
    deny: bool = False


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("标识符只支持小写英文、数字及下划线")
    return value
