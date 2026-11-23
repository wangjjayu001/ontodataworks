"""Restore both independent databases to isolated test DBs, then recreate containers."""
from runtime import *
from datetime import datetime
import hashlib,time

stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
folder=ROOT/"backups";folder.mkdir(exist_ok=True)
backups=[]
for database,verify in [("workbench","SELECT count(*) FROM wb_modeling_document;"),("warehouse_lab","SELECT count(*) FROM validation.dwd_validation_reading;")]:
    path=folder/f"{database}_{stamp}.dump"
    with path.open("xb") as stream:
        p=subprocess.run(ARGS+["exec","-T","db","pg_dump","-U","workbench","-Fc",database],stdout=stream,stderr=subprocess.PIPE)
    assert p.returncode==0 and path.stat().st_size>1000
    temporary=database+"_restore_"+stamp.replace("_","")
    expected=sql(verify,database=database)
    sql(f"CREATE DATABASE {temporary};")
    success=False
    try:
        with path.open("rb") as stream:
            p=subprocess.run(ARGS+["exec","-T","db","pg_restore","-U","workbench","-d",temporary,"--no-owner","--exit-on-error"],stdin=stream,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        assert p.returncode==0,p.stderr.decode(errors="replace")[:1000]
        actual=sql(verify,database=temporary)
        assert actual==expected
        assert sql("SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema');",database=temporary)==sql("SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema');",database=database)
        success=True
    finally:
        if success:sql(f"DROP DATABASE {temporary};")
    backups.append({"database":database,"file":str(path),"bytes":path.stat().st_size,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"restored_independently":success,"table_count_matches":True,"verification_query":verify,"verified_count":expected,"temporary_restore_removed":success})

def lock():
    ids=compose("ps","-aq").split()
    containers=json.loads(subprocess.check_output([str(CLI),"inspect",*ids],encoding="utf-8"))
    return [{"service":c["Config"]["Labels"]["com.docker.compose.service"],"id":c["Id"],"image":c["Image"],"state":c["State"]["Status"],"health":c["State"].get("Health",{}).get("Status"),"restart_count":c["RestartCount"],"oom_killed":c["State"]["OOMKilled"],"volumes":[m["Name"] for m in c["Mounts"] if m["Type"]=="volume"],"started_at":c["State"]["StartedAt"]} for c in containers]

before=lock()
compose("up","-d","--force-recreate","--wait","--wait-timeout","180")
after=lock();old={r["service"]:r for r in before}
assert len(after)==3 and all(r["id"]!=old[r["service"]]["id"] for r in after)
assert all(r["volumes"]==old[r["service"]]["volumes"] for r in after)
credentials=json.loads((ROOT/"local/credentials.json").read_text(encoding="utf-8"))
c=Client();c.login(credentials["admin_username"],credentials["admin_password"])
assert c.call("/workspaces/1/documents/metric")
assert len(c.call("/workspaces/1/assets"))>=4
assert sql("SELECT count(*) FROM validation.dwd_validation_reading;",database="warehouse_lab")=="1460"
samples=[]
start=time.monotonic()
for i in range(4):
    snap=lock();assert all(r["state"]=="running" and r["health"]=="healthy" and r["restart_count"]==0 and not r["oom_killed"] for r in snap)
    assert c.call("/health")["status"]=="ok"
    samples.append({"checked_at":datetime.now().isoformat(),"containers":snap})
    print(f"stability sample {i+1}/4 passed",flush=True)
    if i<3:time.sleep(15)
report={"checked_at":datetime.now().isoformat(),"all_passed":True,"backups":backups,"all_3_containers_recreated":True,"data_volume_preserved":True,"login_after_recreate":True,"models_metrics_and_assets_preserved":True,"warehouse_readings_preserved":1460,"before":before,"after":after,"samples":samples,"observation_seconds":round(time.monotonic()-start,2),"scope":"Local functional persistence, two database restoration tests, short stability observation; not production HA"}
(ROOT/"evidence/backup-recreation-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"all_passed":True,"databases_restored":2,"containers_recreated":3}))
