"""Real protocol/HTTP acceptance against the running narrow Codex bridge.

Only synthetic/public questions are sent. This does not accept production isolation.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("CODEX_BRIDGE_URL", "http://127.0.0.1:19191")
os.environ.setdefault("CODEX_BRIDGE_TOKEN_FILE", str(ROOT / "local/codex-bridge.token"))
from app.model_runtime import list_models, parse_intent, cancel, ModelRuntimeError, validate_output, INTENT_SCHEMA


def main():
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "scope": "Real Codex model/list + synthetic intent inference and cancellation; no production isolation acceptance, no database query.", "tests": []}
    def check(name, passed, detail):
        report["tests"].append({"name": name, "passed": bool(passed), "detail": detail})
        print(("PASS " if passed else "FAIL ") + name, flush=True)
    catalog = list_models()
    check("live_paged_model_directory", len(catalog["models"]) > 0 and catalog["source"] == "codex-app-server-model/list", {"count": len(catalog["models"]), "catalog_version": catalog["catalog_version"], "cli_version": catalog["cli_version"]})
    default = next(x for x in catalog["models"] if x["isDefault"])
    selection = {"catalog_version": catalog["catalog_version"], "model": default["model"], "reasoningEffort": default["defaultReasoningEffort"], "serviceTier": None}
    base = os.environ["CODEX_BRIDGE_URL"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(base + "/models", timeout=5)
        check("bridge_unauthenticated_denied", False, {})
    except urllib.error.HTTPError as exc:
        check("bridge_unauthenticated_denied", exc.code == 401, {"http_status": exc.code})
    token = Path(os.environ["CODEX_BRIDGE_TOKEN_FILE"]).read_text("utf-8").strip()
    try:
        opener.open(urllib.request.Request(base + "/command/exec", data=b'{}', headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}), timeout=5)
        check("raw_execution_endpoint_absent", False, {})
    except urllib.error.HTTPError as exc:
        check("raw_execution_endpoint_absent", exc.code == 404, {"http_status": exc.code})
    for name, override, classification, expected in [
        ("unknown_model_rejected", {"model": "model-does-not-exist"}, "synthetic", "MODEL_UNAVAILABLE"),
        ("unsupported_effort_rejected", {"reasoningEffort": "not-an-effort"}, "synthetic", "MODEL_EFFORT_UNSUPPORTED"),
        ("unsupported_tier_rejected", {"serviceTier": "not-a-tier"}, "synthetic", "MODEL_TIER_UNSUPPORTED"),
        ("stale_catalog_rejected", {"catalog_version": "0" * 64}, "synthetic", "MODEL_CATALOG_STALE"),
        ("production_context_rejected", {}, "confidential", "MODEL_PRODUCTION_DISABLED"),
    ]:
        try:
            parse_intent("2025年订单量", {}, {**selection, **override}, data_classification=classification)
            check(name, False, {"expected_code": expected})
        except ModelRuntimeError as exc:
            check(name, exc.code == expected, {"code": exc.code})
    try:
        validate_output({"requirements": [], "clarification_needed": False}, INTENT_SCHEMA)
        check("empty_structured_intent_rejected", False, {})
    except ModelRuntimeError as exc:
        check("empty_structured_intent_rejected", exc.code == "MODEL_OUTPUT_INVALID", {"code": exc.code})
    positive = parse_intent("2025年按买家所在地区（省/州）统计去重订单量。按源库timestamp时间，不做时区转换。", {"asset_terms": ["订单量", "买家所在地区（省/州）"], "data_scope": "synthetic_local_fixture"}, selection, "accept-intent-" + uuid.uuid4().hex)
    req = positive["output"]["requirements"][0]
    check("real_structured_geographic_intent", req["start"] == "2025-01-01" and req["end_exclusive"] == "2026-01-01" and bool(req["dimension_terms"]) and not positive["tool_events"], positive)
    negative = parse_intent("2025年按买家状态统计订单量。不要按省州或所在地区分组。", {"asset_terms": ["订单量", "买家所在地区（省/州）"], "buyer_status_dimension_available": False, "data_scope": "synthetic_local_fixture"}, selection, "accept-status-" + uuid.uuid4().hex)
    nreq = negative["output"]["requirements"][0]
    terms = " ".join(nreq["dimension_terms"])
    check("real_status_not_geography", ("状态" in terms or bool(nreq["clarifications"])) and "customer_state" not in terms and not negative["tool_events"], negative)
    rid = "accept-cancel-" + uuid.uuid4().hex
    cancelled_result = {}
    def infer():
        try:
            cancelled_result["result"] = parse_intent("分别拆分2025年订单量、2026年订单量、2025年商品销量、2026年商品销量、2025年买家数、2026年买家数、2025年促销订单量、2026年促销订单量，并明确各自缺少的槽位。", {}, selection, rid)
        except ModelRuntimeError as exc:
            cancelled_result["error"] = exc.code
    thread = threading.Thread(target=infer)
    thread.start()
    # Allow thread/start and turn/start to complete, then verify we cancelled an
    # actual started turn rather than only a queued/initializing model request.
    time.sleep(2.5)
    cancel_reply = None
    for _ in range(40):
        time.sleep(0.15)
        cancel_reply = cancel(rid)
        if cancel_reply["cancel_requested"]:
            break
    thread.join(timeout=20)
    check("real_turn_cancelled", not thread.is_alive() and bool(cancel_reply.get("turn_started")) and cancelled_result.get("error") == "MODEL_CANCELLED", {"reply": cancel_reply, "result": cancelled_result})
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    report["passed"] = all(t["passed"] for t in report["tests"])
    report["production_isolation_accepted"] = False
    out = ROOT / "evidence/v1.0/model-runtime-acceptance.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "tests": len(report["tests"]), "report": str(out)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
