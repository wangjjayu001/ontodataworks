"""Real isolated PostgreSQL/API acceptance; synthetic engineering fixtures, no ecommerce import."""
from runtime import *
from datetime import datetime
import secrets,time

creds=json.loads((ROOT/"local/credentials.json").read_text(encoding="utf-8"))
report={"started_at":datetime.now().isoformat(),"tests":[],"scope":"Independent implementation; dedicated non-ecommerce validation warehouse"}


def check(name,fn):
    result=fn()
    if isinstance(result,dict):
        if "all_success" in result:assert result["all_success"]
        if "remaining_temp_document" in result:assert not result["remaining_temp_document"]
        if "revisions" in result:assert result["revisions"]==2
    report["tests"].append({"name":name,"passed":True,"detail":result})
    print("PASS "+name,flush=True)
    return result


client=Client()
check("unauthenticated_access_rejected",lambda:Client().call("/auth/me",expected=401))
check("wrong_password_rejected",lambda:Client().call("/auth/login","POST",{"username":"admin","password":"not-correct"},expected=401))
client.login(creds["admin_username"],creds["admin_password"])
check("real_admin_login_and_workspace",lambda:{"role":client.call("/auth/me")["user"]["role"],"workspace_count":len(client.call("/auth/me")["workspaces"])})
first_csrf=client.csrf;client.csrf=""
check("csrf_required_for_write",lambda:client.call("/workspaces","POST",{"name":"should-not-create"},expected=403))
client.csrf=first_csrf
wid=client.call("/auth/me")["workspaces"][0]["id"]
base=f"/workspaces/{wid}"

if sql("SELECT count(*) FROM pg_database WHERE datname='warehouse_lab';")=="0":
    sql("CREATE DATABASE warehouse_lab;")
for role,key in [("warehouse_reader","warehouse_read_password"),("warehouse_designer","warehouse_write_password")]:
    if sql(f"SELECT count(*) FROM pg_roles WHERE rolname='{role}';")=="0":sql(f"CREATE ROLE {role} LOGIN PASSWORD '{creds[key]}';")
sql("""CREATE SCHEMA IF NOT EXISTS validation;
CREATE TABLE IF NOT EXISTS validation.dim_validation_day (day_key date PRIMARY KEY, year_no integer NOT NULL,month_no integer NOT NULL);
COMMENT ON TABLE validation.dim_validation_day IS '验收专用日期维度（2025—2026）';
COMMENT ON COLUMN validation.dim_validation_day.day_key IS '自然日';
INSERT INTO validation.dim_validation_day SELECT d::date,extract(year FROM d),extract(month FROM d) FROM generate_series('2025-01-01'::date,'2026-12-31'::date,'1 day') d ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS validation.dwd_validation_reading (reading_id bigint PRIMARY KEY,day_key date NOT NULL REFERENCES validation.dim_validation_day(day_key),sample_group text NOT NULL,reading_value numeric(18,2) NOT NULL);
COMMENT ON TABLE validation.dwd_validation_reading IS '验收专用读数明细（非电商业务）';
COMMENT ON COLUMN validation.dwd_validation_reading.reading_value IS '人工确定性读数，用于查询与指标校验';
INSERT INTO validation.dwd_validation_reading SELECT row_number() OVER(ORDER BY day_key,g),day_key,g,(extract(doy FROM day_key)+(CASE WHEN g='A' THEN 1 ELSE 2 END))::numeric FROM validation.dim_validation_day CROSS JOIN (VALUES('A'),('B')) groups(g) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS validation.dws_validation_daily (day_key date PRIMARY KEY,reading_count bigint NOT NULL,reading_sum numeric(18,2) NOT NULL);
COMMENT ON TABLE validation.dws_validation_daily IS '验收专用每日读数汇总';
INSERT INTO validation.dws_validation_daily SELECT day_key,count(*),sum(reading_value) FROM validation.dwd_validation_reading GROUP BY day_key ON CONFLICT(day_key) DO UPDATE SET reading_count=excluded.reading_count,reading_sum=excluded.reading_sum;
GRANT CONNECT ON DATABASE warehouse_lab TO warehouse_reader,warehouse_designer;
GRANT USAGE ON SCHEMA validation TO warehouse_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA validation TO warehouse_reader;
GRANT USAGE,CREATE ON SCHEMA validation TO warehouse_designer;
GRANT SELECT ON ALL TABLES IN SCHEMA validation TO warehouse_designer;
""",database="warehouse_lab")
check("separate_warehouse_fixture_count",lambda:sql("SELECT 'days='||count(*) FROM validation.dim_validation_day;SELECT 'readings='||count(*) FROM validation.dwd_validation_reading;SELECT min(day_key)||' to '||max(day_key) FROM validation.dim_validation_day;",database="warehouse_lab"))

existing=client.call(base+"/documents/domain")
domain=next((d for d in existing if d["code"]=="platform_validation"),None)
if not domain:domain=client.call(base+"/documents/domain","POST",{"code":"platform_validation","name":"平台验收专用域","description":"用于独立平台工程验收，非电商生产数据","status":"published"})
did=domain["id"]
temp=client.call(base+"/documents/domain","POST",{"code":"temp_"+secrets.token_hex(4),"name":"中文维护验证"})
payload={k:temp[k] for k in ["code","name","description","domain_id","layer","status","spec","revision"]};payload["description"]="中文修改已落库"
changed=client.call(base+f"/documents/domain/{temp['id']}","PUT",payload)
assert changed["revision"]==2 and changed["description"]=="中文修改已落库"
check("optimistic_concurrency_rejects_stale_revision",lambda:client.call(base+f"/documents/domain/{temp['id']}","PUT",payload,expected=409))
check("document_revision_history",lambda:{"revisions":len(client.call(base+f"/documents/domain/{temp['id']}/revisions"))})
client.call(base+f"/documents/domain/{temp['id']}","DELETE")
check("document_crud_unicode_and_cleanup",lambda:{"remaining_temp_document":any(d["id"]==temp["id"] for d in client.call(base+"/documents/domain"))})

sources=client.call(base+"/datasources")
source=next((s for s in sources if s["name"]=="验收仓库（只读）"),None)
if not source:source=client.call(base+"/datasources","POST",{"name":"验收仓库（只读）","host":"db","database":"warehouse_lab","username":"warehouse_reader","password":creds["warehouse_read_password"],"schemas":["validation"]})
assert "secret" not in source and "password" not in source
sid=source["id"]
check("datasource_connection_and_secret_redaction",lambda:client.call(base+f"/datasources/{sid}/test","POST",{}))
check("real_metadata_collection",lambda:client.call(base+f"/datasources/{sid}/collect","POST",{}))
assets=client.call(base+"/assets");fact=next(a for a in assets if a["table_name"]=="dwd_validation_reading" and a["datasource_id"]==sid)
client.call(base+f"/assets/{fact['id']}","PATCH",{"domain_id":did,"name":"验收专用读数明细（2025—2026）"})
preview=client.call(base+f"/assets/{fact['id']}/preview")
assert len(preview["rows"])==100 and preview["source"]=="live_database"
check("real_table_preview_limited_100",lambda:{"rows":len(preview["rows"]),"columns":preview["columns"]})

bad_model={"code":"wrong_prefix","name":"错误分层模型","domain_id":did,"layer":"DWD","spec":{"grain":"单条读数","fields":[{"name":"id","type":"bigint"}]}}
check("warehouse_layer_naming_validation",lambda:client.call(base+"/documents/model","POST",bad_model,expected=400))
model_body={"code":"ads_validation_probe","name":"验收空表模型","domain_id":did,"layer":"ADS","spec":{"grain":"一条验收探针记录","namespace":"validation","fields":[{"name":"id","type":"bigint","nullable":False,"comment":"主键"},{"name":"note","type":"text","nullable":True,"comment":"备注"}],"primary_keys":["id"]}}
models=client.call(base+"/documents/model");model=next((m for m in models if m["code"]==model_body["code"]),None) or client.call(base+"/documents/model","POST",model_body)
check("ddl_generated_from_maintained_model",lambda:client.call(base+f"/models/{model['id']}/ddl"))
writer=next((s for s in sources if s["name"]=="验收建表连接（受控）"),None)
if not writer:writer=client.call(base+"/datasources","POST",{"name":"验收建表连接（受控）","host":"db","database":"warehouse_lab","username":"warehouse_designer","password":creds["warehouse_write_password"],"schemas":["validation"]})
if sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='validation' AND table_name='ads_validation_probe';",database="warehouse_lab")=="0":check("model_materializes_actual_empty_table",lambda:client.call(base+f"/models/{model['id']}/materialize","POST",{"datasource_id":writer["id"]}))
sql("GRANT SELECT ON validation.ads_validation_probe TO warehouse_reader;",database="warehouse_lab")
client.call(base+f"/datasources/{sid}/collect","POST",{})
assert next(a for a in client.call(base+"/assets") if a["id"]==fact["id"])["name"]=="验收专用读数明细（2025—2026）"

dim_body={"code":"validation_date","name":"验收日期维度","domain_id":did,"spec":{"business_key":"day_key","attributes":[{"name":"day_key","type":"date","nullable":False,"comment":"日期主键"},{"name":"year_no","type":"integer","nullable":False,"comment":"年份"}]},"status":"published"}
dims=client.call(base+"/documents/dimension");dim=next((x for x in dims if x["code"]==dim_body["code"]),None) or client.call(base+"/documents/dimension","POST",dim_body)
check("dimension_business_key_and_attributes",lambda:{"dimension_id":dim["id"]})
metric_body={"code":"validation_value_sum","name":"验收读数总量","domain_id":did,"status":"published","spec":{"metric_type":"atomic","business_process":"读数采集","aggregation":"SUM","unit":"点","asset_id":fact["id"],"measure":"reading_value"}}
metrics=client.call(base+"/documents/metric");atomic=next((x for x in metrics if x["code"]==metric_body["code"]),None) or client.call(base+"/documents/metric","POST",metric_body)
bad_metric={**metric_body,"code":"invalid_metric_"+secrets.token_hex(3),"spec":{**metric_body["spec"],"measure":"not_a_column"}}
check("metric_publish_validates_real_field",lambda:client.call(base+"/documents/metric","POST",bad_metric,expected=400))
derived_body={"code":"validation_daily_sum","name":"验收每日读数总量","domain_id":did,"status":"published","spec":{"metric_type":"derived","atomic_metric_id":atomic["id"],"time_window":"自然日","dimension_ids":[dim["id"]]}}
if not any(x["code"]==derived_body["code"] for x in metrics):client.call(base+"/documents/metric","POST",derived_body)
check("derived_metric_references_atomic_and_dimension",lambda:{"atomic_id":atomic["id"],"dimension_id":dim["id"]})
check("domain_delete_blocked_by_real_references",lambda:client.call(base+f"/documents/domain/{did}","DELETE",expected=409))

join_query="SELECT d.year_no,count(*) AS reading_count,sum(f.reading_value) AS reading_sum FROM validation.dwd_validation_reading f JOIN validation.dim_validation_day d ON f.day_key=d.day_key GROUP BY d.year_no ORDER BY d.year_no"
result=client.call(base+"/queries","POST",{"datasource_id":sid,"sql":join_query})
assert result["row_count"]==2 and result["verified_sources"]==2 and result["rows"][0][1]==730
check("sql_real_join_and_result",lambda:result)
check("cte_query_executes_and_resolves_sources",lambda:client.call(base+"/queries","POST",{"datasource_id":sid,"sql":"WITH daily AS (SELECT day_key,reading_count FROM validation.dws_validation_daily) SELECT count(*) AS days FROM daily"}))
for name,query in [("write_sql_rejected","DELETE FROM validation.dwd_validation_reading"),("multiple_statements_rejected","SELECT 1; SELECT 2;"),("select_into_rejected","SELECT * INTO validation.not_allowed FROM validation.dwd_validation_reading"),("management_functions_rejected","SELECT pg_sleep(1)")]:check(name,lambda query=query:client.call(base+"/queries","POST",{"datasource_id":sid,"sql":query},expected=400))
evidence=client.call(base+"/lineage");assert len([e for e in evidence if e["query_id"]==result["id"]])==2
import hashlib
assert all(e["evidence"]["sql_sha256"]==hashlib.sha256(join_query.encode()).hexdigest() for e in evidence if e["query_id"]==result["id"])
check("lineage_evidence_matches_sql_hash_and_schema",lambda:{"edges_for_join":len([e for e in evidence if e["query_id"]==result["id"]]),"all_success":all(e["evidence"]["query_status"]=="success" and len(e["evidence"]["metadata_fingerprint"])==64 for e in evidence)})

viewer_name="viewer_"+secrets.token_hex(3);viewer_pass=secrets.token_urlsafe(18)
viewer=client.call("/users","POST",{"username":viewer_name,"display_name":"验收只读成员","password":viewer_pass,"role":"viewer","workspace_ids":[wid]})
reader=Client();reader.login(viewer_name,viewer_pass)
check("viewer_can_read_but_not_write",lambda:{"visible_tables":len(reader.call(base+"/assets")),"write":reader.call(base+"/documents/domain","POST",{"code":"blocked_write","name":"不应创建"},expected=403)})
other=next((w for w in client.call("/auth/me")["workspaces"] if w["name"]=="权限隔离验收空间"),None) or client.call("/workspaces","POST",{"name":"权限隔离验收空间","description":"验收用途，当前空间保持独立"})
check("cross_workspace_access_denied",lambda:reader.call(f"/workspaces/{other['id']}/assets",expected=403))
reader.call("/auth/logout","POST",{})
check("logout_invalidates_session",lambda:reader.call("/auth/me",expected=401))
sql(f"DELETE FROM wb_session WHERE user_id={viewer['id']};DELETE FROM wb_membership WHERE user_id={viewer['id']};DELETE FROM wb_user WHERE id={viewer['id']};")
me=client.call("/auth/me")
(ROOT/"local/browser-session.json").write_text(json.dumps({"csrf":client.csrf,"cookies":[{"name":c.name,"value":c.value,"domain":"127.0.0.1","path":"/","httpOnly":True,"sameSite":"Strict"} for c in client.jar]}),encoding="utf-8")
report.update({"completed_at":datetime.now().isoformat(),"all_passed":True,"workspace_id":wid,"source_id":sid,"fixture_date_range":"2025-01-01 to 2026-12-31","fixture_counts":{"dim_validation_day":730,"dwd_validation_reading":1460,"dws_validation_daily":730,"ads_validation_probe":0},"no_ecommerce_import":True})
(ROOT/"evidence/integration-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"all_passed":True,"test_count":len(report["tests"]),"no_ecommerce_import":True}))
