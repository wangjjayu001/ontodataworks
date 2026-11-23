"""Explicit HTTP publication replacing only five repaired Binding revisions.

Preserves the existing deployed membership; unrelated current drafts stay out.
"""
import json
from pathlib import Path
import time
from validate_semantic_live import Client

ROOT=Path(__file__).resolve().parent.parent


def main():
    client=Client("http://127.0.0.1:19090");credentials=json.loads((ROOT/"local/credentials.json").read_text("utf-8"));client.login(credentials["admin_username"],credentials["admin_password"]);del credentials
    base="/workspaces/3/semantic";receipts=[]
    def call(path,method="GET",body=None,version=None):
        value,receipt=client.request(base+path,method,body,version=version);receipts.append(receipt);return value
    before=call("/deployments/local_validation")["deployment"]
    release=next(item["release"] for item in call("/releases")["items"] if item["release"]["id"]==before["release_id"])
    current={item["asset_ref"]["asset_id"]:item for item in call("/assets")["items"]}
    expected={"binding:"+mid+".local_v1" for mid in ("active_customers","average_order_value","gross_order_value","order_count","promotion_orders")}
    selected=[];changes=[]
    for member in release["items"]:
        previous=member["asset_ref"];head=current[previous["asset_id"]]
        assert head["governance_record"]["workflow_state"]=="DRAFT",head["asset_ref"]
        if head["asset_ref"]!=previous:
            assert previous["asset_id"] in expected and previous["revision"]==2 and head["asset_ref"]["revision"]==3,(previous,head["asset_ref"])
            changes.append({"before":previous,"after":head["asset_ref"],"new_content_hash":head["content_hash"]})
        selected.append(head["asset_ref"])
    assert {item["after"]["asset_id"] for item in changes}==expected,changes
    assert len(selected)==len(release["items"])==187
    result=call("/deployments/local_validation/rebuild","POST",{"scope":"local_validation","draft_asset_refs":selected,"expected_pointer_version":before["pointer_version"],"reason":"保持现有187个已部署业务成员，仅替换五个显式修复关系键映射的Binding r3；不纳入无关验收来源草稿，不创建业务或生产审批。"},version=before["pointer_version"])
    ids=[item["asset_ref"]["asset_id"] for item in result["release"]["items"]];assert len(ids)==len(set(ids))==187
    report={"suite":"repaired-binding-local-publication/3","completed_at":time.strftime("%Y-%m-%dT%H:%M:%S%z"),"passed":True,"before_deployment":before,"after_deployment":result["deployment"],"changes":changes,"selected_member_count":len(selected),"excluded_unpublished_drafts":[aid for aid in current if aid not in ids],"business_approval_created":result["business_approval_created"],"production_approved":result["production_approved"],"http_receipts":receipts}
    path=ROOT/"evidence/v1.0/semantic-relation-repair-live.json";path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":True,"changed_bindings":len(changes),"members":187,"pointer_version":result["deployment"]["pointer_version"],"report":str(path)},ensure_ascii=False))


if __name__=="__main__":main()
