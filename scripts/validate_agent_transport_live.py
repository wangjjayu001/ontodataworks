"""Real cookie/SSE/ownership/revocation tests with a disposable local test identity.

One real intent inference; no warehouse SQL execution or business asset approval.
Only the newly-created identity is demoted/disabled for revocation checks.
"""
from validate_v1_live import Api, ROOT
from runtime import sql
import json
import secrets
import time
import urllib.request
import uuid


def main():
    admin = Api(); admin.login()
    tester = Api(); other = Api()
    report = {"scope": "real HTTP transport and current identity revocation; no warehouse execution", "tests": []}
    uid = viewer_id = None
    def check(name, detail=None):
        report["tests"].append({"name": name, "passed": True, "detail": detail or {}})
        print("PASS", name, flush=True)
    try:
        username = "agent_http_v1_" + uuid.uuid4().hex[:10]
        password = secrets.token_urlsafe(24)
        created = admin.call("/users", "POST", {"username": username, "password": password, "display_name": "v1 本地传输验收身份", "role": "admin", "workspace_ids": [3]})
        uid = int(created["id"])
        tester.csrf = tester.call("/auth/login", "POST", {"username": username, "password": password})["csrf"]
        directory = tester.call("/workspaces/3/agent/models")["data"]
        model = next((m for m in directory["models"] if m.get("isDefault")), directory["models"][0])
        selection = {"catalog_version": directory["catalog_version"], "model": model.get("model", model["id"]), "reasoningEffort": model["defaultReasoningEffort"], "serviceTier": None}
        prefix = "/workspaces/3/agent"
        body = {"message": {"text": "2025年按下单时间统计订单量，按订单编号去重，使用源库时间。"}, "model_selection": selection, "execution_mode": "local_validation"}
        run = tester.call(prefix + "/runs", "POST", body, 202)["data"]["run"]
        deadline = time.monotonic() + 170
        while run["state"] == "DRAFT":
            assert time.monotonic() < deadline
            time.sleep(.5)
            run = tester.call(prefix + "/runs/" + run["id"])["data"]["run"]
        def command(action, extra=None, expected=200, key=None):
            req = run["requirements"][0]
            value = {"expected_run_version": run["run_version"], "expected_requirement_version": req["requirement_version"], "intent_frame_hash": req["intent_frame_hash"], **(extra or {})}
            route = prefix + "/runs/" + run["id"] + "/requirements/" + req["id"] + "/" + action
            return tester.call(route, "POST", value, expected, key), route, value
        while run["state"] == "NEED_CLARIFICATION":
            clarification = run["requirements"][0]["clarifications"][0]
            assert clarification["slot"] in {"timezone", "time_event"}, clarification
            answer = "SOURCE" if clarification["slot"] == "timezone" else "订单创建时间"
            value, _, _ = command("clarifications", {"clarification_id": clarification["id"], "answer": answer})
            run = value["data"]["run"]
        assert run["state"] == "TARGET_CONFIRMATION", run["state"]
        confirm_key = uuid.uuid4().hex
        confirmed, replay_route, replay_body = command("confirm-intent", key=confirm_key)
        run = confirmed["data"]["run"]
        assert run["state"] == "NEED_CONFIRMATION", run["requirements"][0].get("error")
        bundle = run["requirements"][0]["bundle"]
        assert bundle["request_id"] == run["request_id"] and bundle["trace_id"] == run["trace_id"]
        check("bundle_trace_matches_real_run", {"run_id": run["id"], "metric": bundle["selected_metric_ref"], "binding": bundle["selected_binding_ref"]})
        event_path = prefix + "/runs/" + run["id"] + "/events"
        def first_event(after):
            request = urllib.request.Request("http://127.0.0.1:19090/api" + event_path, headers={"Last-Event-ID": str(after)})
            with tester.opener.open(request, timeout=25) as response:
                assert response.status == 200 and response.headers["Content-Type"].startswith("text/event-stream")
                for raw in response:
                    line = raw.decode().strip()
                    if line.startswith("id:"):
                        return int(line.split(":", 1)[1])
            return None
        first = first_event(0)
        second = first_event(first)
        assert first == 1 and second == 2, (first, second)
        check("sse_resumes_after_last_event_id", {"first": first, "resumed": second})
        other.call(prefix + "/runs/" + run["id"], expected=401)
        check("unauthenticated_run_rejected")
        viewer_name = "agent_view_v1_" + uuid.uuid4().hex[:10]
        viewer_password = secrets.token_urlsafe(24)
        viewer = admin.call("/users", "POST", {"username": viewer_name, "password": viewer_password, "role": "viewer", "workspace_ids": [3]})
        viewer_id = int(viewer["id"])
        other.csrf = other.call("/auth/login", "POST", {"username": viewer_name, "password": viewer_password})["csrf"]
        other.call(prefix + "/runs/" + run["id"], expected=404)
        other.call(event_path, expected=404)
        sql("UPDATE wb_user SET enabled=0 WHERE id=" + str(viewer_id))
        check("other_actor_cannot_read_run_or_events")
        tester.call(prefix + "/runs/" + run["id"] + "/feedback", "POST", {"category": "TEST"}, expected=403, headers={"X-CSRF-Token": ""})
        check("real_cookie_without_csrf_cannot_write")
        sql("UPDATE wb_user SET role='viewer' WHERE id=" + str(uid))
        tester.call(prefix + "/runs/" + run["id"], expected=403)
        tester.call(event_path, expected=403)
        tester.call(replay_route, "POST", replay_body, expected=403, key=confirm_key)
        check("revocation_blocks_run_events_and_cached_command_replay")
        sql("UPDATE wb_session SET expires_at=now()-interval '1 minute' WHERE user_id=" + str(uid))
        tester.call("/auth/me", expected=401)
        check("expired_real_session_rejected")
        report["passed"] = True
    except Exception as exc:
        report.update(passed=False, error=str(exc)); raise
    finally:
        if uid is not None: sql("UPDATE wb_user SET enabled=0 WHERE id=" + str(uid))
        if viewer_id is not None: sql("UPDATE wb_user SET enabled=0 WHERE id=" + str(viewer_id))
        output = ROOT / "evidence/v1.0/agent-transport-acceptance.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "tests": len(report["tests"]), "report": str(output)}, ensure_ascii=False))


if __name__ == "__main__": main()
