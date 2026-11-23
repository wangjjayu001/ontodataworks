"""Metadata drift and database-level read-only enforcement beyond SQL parsing."""
from runtime import *
from datetime import datetime

credentials=json.loads((ROOT/"local/credentials.json").read_text(encoding="utf-8"))
c=Client();c.login(credentials["admin_username"],credentials["admin_password"])
b="/workspaces/1"
sources=c.call(b+"/datasources")
reader=next(s for s in sources if s["username"]=="warehouse_reader")
writer=next(s for s in sources if s["username"]=="warehouse_designer")
evidence_count=len(c.call(b+"/lineage"))
sql("ALTER TABLE validation.dwd_validation_reading ADD COLUMN IF NOT EXISTS drift_probe text;",database="warehouse_lab")
try:
    result=c.call(b+"/queries","POST",{"datasource_id":reader["id"],"sql":"SELECT * FROM validation.dwd_validation_reading LIMIT 1"},expected=409)
    assert "结构已变化" in result["detail"]
    assert len(c.call(b+"/lineage"))==evidence_count
finally:
    sql("ALTER TABLE validation.dwd_validation_reading DROP COLUMN IF EXISTS drift_probe;",database="warehouse_lab")
c.call(b+f"/datasources/{reader['id']}/collect","POST",{})
sql("""CREATE OR REPLACE FUNCTION validation.probe_forbidden_write() RETURNS integer LANGUAGE plpgsql AS $$ BEGIN INSERT INTO validation.ads_validation_probe(id,note) VALUES (987654321,'should_not_write'); RETURN 1; END; $$;
GRANT INSERT ON validation.ads_validation_probe TO warehouse_designer;
""",database="warehouse_lab")
try:
    denied=c.call(b+"/queries","POST",{"datasource_id":writer["id"],"sql":"SELECT validation.probe_forbidden_write()"},expected=400)
    assert "read-only" in denied["detail"].lower()
    assert sql("SELECT count(*) FROM validation.ads_validation_probe WHERE id=987654321;",database="warehouse_lab")=="0"
finally:
    sql("DROP FUNCTION validation.probe_forbidden_write();REVOKE INSERT ON validation.ads_validation_probe FROM warehouse_designer;",database="warehouse_lab")
result=c.call(b+"/queries","POST",{"datasource_id":reader["id"],"sql":"SELECT * FROM validation.dwd_validation_reading ORDER BY reading_id"})
assert result["row_count"]==1000 and result["truncated"]
assert len(c.call(b+"/lineage"))==evidence_count+1
report={"checked_at":datetime.now().isoformat(),"metadata_drift_blocks_query":True,"blocked_query_adds_no_lineage":True,"database_readonly_blocks_function_side_effect":True,"forbidden_write_row_count":0,"query_output_cap":1000,"truncation_indicated":True,"temporary_schema_and_function_changes_removed":True,"all_passed":True}
(ROOT/"evidence/drift-readonly-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report))
