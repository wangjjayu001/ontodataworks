"""Narrow model gateway. No CLI credentials, filesystem RPCs or database access.

The current Windows bridge is explicitly for public/synthetic local validation.
Production context is rejected until a separately isolated runtime is accepted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from urllib.parse import quote


class ModelRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _nullable(kind="string"):
    return {"type": [kind, "null"]}


MODEL_CONTRACT_VERSION = "semantic-model/1.2"
PERIOD_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["start", "end_exclusive"],
    "properties": {"start": _nullable(), "end_exclusive": _nullable()}}
ANALYSIS_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["kind", "base_period", "comparison_period", "measures"],
    "properties": {
        "kind": {"type": "string", "enum": ["NONE", "PERIOD_COMPARISON"]},
        "base_period": PERIOD_SCHEMA, "comparison_period": PERIOD_SCHEMA,
        "measures": {"type": "array", "maxItems": 2,
            "items": {"type": "string", "enum": ["GROWTH_RATE", "ABSOLUTE_CHANGE"]}}}}


INTENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["requirements", "clarification_needed"],
    "properties": {
        "requirements": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["question", "metric_terms", "dimension_terms", "start", "end_exclusive", "timezone", "time_event", "filters", "negative_terms", "clarifications", "depends_on_requirement_indexes", "analysis"],
                "properties": {
                    "question": {"type": "string"},
                    "metric_terms": {"type": "array", "items": {"type": "string"}},
                    "dimension_terms": {"type": "array", "items": {"type": "string"}},
                    "start": {**_nullable(), "description": "For a whole-day question use YYYY-MM-DD only. Never append a midnight time. Preserve explicit hour/minute requests instead of rounding them."},
                    "end_exclusive": {**_nullable(), "description": "Exclusive end. Whole-day questions use YYYY-MM-DD only, with no T00:00:00 suffix."},
                    "timezone": _nullable(), "time_event": _nullable(),
                    "analysis": ANALYSIS_SCHEMA,
                    "negative_terms": {"type": "array", "description": "Only business exclusions explicitly requested in input.question. System/context warnings, parser rules, unknown approval or timezone are never user business exclusions. Use [] when the user asks for no exclusion.", "items": {"type": "string"}},
                    "depends_on_requirement_indexes": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 7}},
                    "filters": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["field", "operator", "value"],
                        "properties": {"field": {"type": "string"}, "operator": {"type": "string"}, "value": {"type": "string"}},
                    }},
                    "clarifications": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["slot", "question", "options"],
                        "properties": {"slot": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}},
                    }},
                },
            },
        },
        "clarification_needed": {"type": "boolean"},
    },
}

# A clarification refines exactly one existing requirement. It cannot create a
# new requirements array, change run IDs, grant approval or provide SQL.
CLARIFICATION_INTENT_SCHEMA = {
    **INTENT_SCHEMA["properties"]["requirements"]["items"],
    "required": [k for k in INTENT_SCHEMA["properties"]["requirements"]["items"]["required"] if k != "depends_on_requirement_indexes"],
    "properties": {k: v for k, v in INTENT_SCHEMA["properties"]["requirements"]["items"]["properties"].items() if k != "depends_on_requirement_indexes"}}
CLARIFICATION_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["intent"], "properties": {"intent": CLARIFICATION_INTENT_SCHEMA}}

METADATA_FILTER_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["field_id", "operator", "value"],
    "properties": {"field_id": {"type": "string"},
        "operator": {"type": "string", "enum": ["="]}, "value": {"type": "string"}}}
METADATA_CANDIDATE_PLAN_SCHEMA = {"type": ["object", "null"], "additionalProperties": False,
    "required": ["name", "definition", "catalog_snapshot_id", "datasource_id", "dataset_id",
        "operation", "measure_field_id", "time_field_id", "dimension_field_ids", "filters",
        "unit_label", "null_policy"],
    "properties": {
        "name": {"type": "string"}, "definition": {"type": "string"},
        "catalog_snapshot_id": {"type": "string"}, "datasource_id": {"type": "string"},
        "dataset_id": {"type": "string"},
        "operation": {"type": "string", "enum": ["COUNT_ROWS", "COUNT_DISTINCT", "SUM", "AVG"]},
        "measure_field_id": _nullable(), "time_field_id": _nullable(),
        "dimension_field_ids": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
        "filters": {"type": "array", "maxItems": 5, "items": METADATA_FILTER_SCHEMA},
        "unit_label": {"type": "string"},
        "null_policy": {"type": "string", "enum": ["EXCLUDE_AND_WARN"]}}}
METADATA_CANDIDATE_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["candidate_plan"],
    "properties": {"candidate_plan": METADATA_CANDIDATE_PLAN_SCHEMA}}

EXPLAIN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "limitations", "cited_ids"],
    "properties": {
        "summary": {"type": "string"},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "cited_ids": {"type": "array", "items": {"type": "string"}},
    },
}
STAGE_SCHEMAS = {"intent": INTENT_SCHEMA, "clarification": CLARIFICATION_SCHEMA,
    "metadata_candidate": METADATA_CANDIDATE_SCHEMA, "explain": EXPLAIN_SCHEMA}


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def validate_output(value, schema, path="output"):
    """Validate the deliberately small schema subset used by these two stages."""
    kind = schema.get("type")
    if "enum" in schema and value not in schema["enum"]:
        raise ModelRuntimeError("MODEL_OUTPUT_INVALID", f"{path} 含未登记取值")
    if isinstance(kind, list):
        if value is None and "null" in kind:
            return
        kind = next(x for x in kind if x != "null")
    if kind == "object":
        if not isinstance(value, dict):
            raise ModelRuntimeError("MODEL_OUTPUT_INVALID", f"{path} 应为对象")
        if set(schema.get("required", [])) - set(value):
            raise ModelRuntimeError("MODEL_OUTPUT_INVALID", f"{path} 缺少必要字段")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ModelRuntimeError("MODEL_OUTPUT_INVALID", f"{path} 含未声明字段")
        for key, item in value.items():
            if key in props:
                validate_output(item, props[key], path + "." + key)
    elif kind == "array":
        if not isinstance(value, list) or len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 100):
            raise ModelRuntimeError("MODEL_OUTPUT_INVALID", f"{path} 数组数量不符合约束")
        for i, item in enumerate(value):
            validate_output(item, schema["items"], f"{path}[{i}]")
    elif kind == "string":
        if not isinstance(value, str) or len(value) > 12000:
            raise ModelRuntimeError("MODEL_OUTPUT_INVALID", f"{path} 文本不符合约束")
    elif kind == "boolean" and not isinstance(value, bool):
        raise ModelRuntimeError("MODEL_OUTPUT_INVALID", f"{path} 应为布尔值")
    elif kind == "integer" and (not isinstance(value, int) or isinstance(value, bool) or value < schema.get("minimum", -1000000) or value > schema.get("maximum", 1000000)):
        raise ModelRuntimeError("MODEL_OUTPUT_INVALID", f"{path} 应为约束内整数")


def _bridge_call(path, body=None, timeout=None):
    base = os.environ.get("CODEX_BRIDGE_URL", "").rstrip("/")
    token_path = os.environ.get("CODEX_BRIDGE_TOKEN_FILE", "")
    if not base or not token_path:
        raise ModelRuntimeError("MODEL_RUNTIME_UNAVAILABLE", "模型运行服务尚未配置", True)
    try:
        token = Path(token_path).read_text(encoding="utf-8").strip()
    except OSError:
        raise ModelRuntimeError("MODEL_RUNTIME_UNAVAILABLE", "模型运行凭据不可用", True)
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(base + path, data=payload, headers=headers, method="POST" if body is not None else "GET")
    try:
        # Internal bridge requests must not follow a workstation HTTP proxy.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout or 170) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            error = json.load(exc).get("error", {})
        except Exception:
            error = {}
        raise ModelRuntimeError(error.get("code", "MODEL_RUNTIME_ERROR"), error.get("message", "模型服务请求失败"), bool(error.get("retryable")))
    except (OSError, ValueError):
        raise ModelRuntimeError("MODEL_RUNTIME_UNAVAILABLE", "模型运行服务暂时不可用", True)
    if not isinstance(result, dict):
        raise ModelRuntimeError("MODEL_RUNTIME_ERROR", "模型服务返回格式错误")
    return result


def list_models():
    result = _bridge_call("/models", timeout=25)
    if result.get("model_contract_version") != MODEL_CONTRACT_VERSION or result.get("stage_schema_hashes") != stage_schema_hashes():
        raise ModelRuntimeError("MODEL_CONTRACT_MISMATCH", "模型CLI桥接与后端的数据契约不一致，请通过统一入口重启模型桥接和服务", True)
    return result


def stage_schema_hashes():
    return {stage: canonical_hash(schema) for stage, schema in STAGE_SCHEMAS.items()}


def invoke(stage, input_payload, output_schema=None, model_selection=None, request_id=None, data_classification="synthetic", progress_callback=None):
    if stage not in STAGE_SCHEMAS:
        raise ModelRuntimeError("MODEL_STAGE_UNSUPPORTED", "不支持的模型处理阶段")
    schema = STAGE_SCHEMAS[stage]
    if output_schema is not None and canonical_hash(output_schema) != canonical_hash(schema):
        raise ModelRuntimeError("MODEL_SCHEMA_UNSUPPORTED", "模型输出契约未登记")
    if data_classification not in {"synthetic", "public"}:
        raise ModelRuntimeError("MODEL_PRODUCTION_DISABLED", "当前模型运行仅开放本地合成或公开材料验证")
    ident = request_id or "model_" + uuid.uuid4().hex
    body = {
        "request_id": ident,
        "stage": stage, "input": input_payload,
        "model_contract_version": MODEL_CONTRACT_VERSION,
        "output_schema_hash": canonical_hash(schema),
        "model_selection": model_selection or {},
        "data_classification": data_classification,
    }
    if progress_callback is None:
        result = _bridge_call('/invoke', body)
    else:
        last_phase = 'CONNECTING'
        progress_callback(last_phase)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(_bridge_call, '/invoke', body)
            while True:
                try:
                    result = pending.result(timeout=1)
                    break
                except FutureTimeout:
                    try:
                        status = _bridge_call('/requests/' + quote(ident, safe=':'), timeout=2)
                    except ModelRuntimeError:
                        continue
                    phase = status.get('phase')
                    if phase in {'CONNECTING', 'MODEL_RUNNING', 'VALIDATING'} and phase != last_phase:
                        progress_callback(phase)
                        last_phase = phase
        progress_callback('VALIDATING')
    if result.get("model_contract_version") != MODEL_CONTRACT_VERSION or result.get("output_schema_hash") != canonical_hash(schema):
        raise ModelRuntimeError("MODEL_CONTRACT_MISMATCH", "模型CLI返回的数据契约与本次请求不一致", True)
    validate_output(result.get("output"), schema)
    return result


def parse_intent(text, context=None, model_selection=None, request_id=None, data_classification="synthetic", progress_callback=None):
    if not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise ModelRuntimeError("MODEL_INPUT_INVALID", "问题应为1至6000字文本")
    # Context must contain permitted semantic labels, not raw source rows or secrets.
    result = invoke("intent", {"question": text, "context": context or {}}, INTENT_SCHEMA, model_selection, request_id, data_classification, progress_callback=progress_callback)
    return result


def refine_intent(text, context=None, model_selection=None, request_id=None, data_classification="synthetic", progress_callback=None):
    if not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise ModelRuntimeError("MODEL_INPUT_INVALID", "澄清输入应为1至6000字文本")
    return invoke("clarification", {"question": text, "context": context or {}},
        CLARIFICATION_SCHEMA, model_selection, request_id, data_classification, progress_callback=progress_callback)


def derive_metadata_candidate(text, context=None, model_selection=None, request_id=None, data_classification="synthetic", progress_callback=None):
    if not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise ModelRuntimeError("MODEL_INPUT_INVALID", "候选口径输入应为1至6000字文本")
    return invoke("metadata_candidate", {"question": text, "context": context or {}},
        METADATA_CANDIDATE_SCHEMA, model_selection, request_id, data_classification, progress_callback=progress_callback)


def cancel(request_id):
    return _bridge_call("/cancel", {"request_id": request_id}, timeout=10)
