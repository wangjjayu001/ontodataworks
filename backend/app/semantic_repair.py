"""Explicit, repeatable repair of the original local technical import.

Never overwrites revision 1, the frozen sources, test gold, policies or approvals.
Run after importing local-seed.json and before publishing a local technical index.
"""
import copy
import json
from sqlalchemy import select
from .semantic import (current_assets, ref, sha, canonical, save_asset, resolve_asset,
    resolve_physical, validate, reference_issues, record_event, failure, walk_refs)
from .semantic_models import SemanticEvent, SemanticRevision, SemanticGovernance

REPAIR_VERSION="typed-property-time-mapping/2"
RELATION_REPAIR_VERSION="typed-logical-join-mapping/3"
REASON="修复本地导入的逻辑属性/物理计算不一致，补显式时间、维度与复合粒度映射；保留未知业务证据、币种及源时区，不授予生产批准。"


def bridge_assets(wid,provenance):
    from .semantic_seed import make_asset,asset_ref
    eref=asset_ref(wid,"entity","commerce.OrderPromotion")
    order=asset_ref(wid,"entity","commerce.Order");promotion=asset_ref(wid,"entity","commerce.Promotion")
    def p(entity,pid):return {"entity_ref":entity,"property_id":pid}
    entity=make_asset(wid,"entity","commerce.OrderPromotion","订单促销关联","订单与促销活动的关联记录；复合业务键为订单ID和促销ID，一单可关联多个促销。",{"kind":"ENTITY","properties":[{"property_id":pid,"name":label,"data_type":"VARCHAR","semantic_role":"IDENTIFIER","nullable":None,"unit":None} for pid,label in [("order_id","订单ID"),("promotion_id","促销ID")]],"business_key":["order_id","promotion_id"],"grain":"每行一个订单与促销活动的关联"},provenance)
    relation=make_asset(wid,"relation","rel_order_promotion_link_promotion","关联记录对应促销","关联记录通过promotion_id对应促销活动；不能把订单ID直接等同于促销ID。",{"from_entity_ref":eref,"to_entity_ref":promotion,"predicate":"references_promotion","from_role":"promotion_link","to_role":"promotion","cardinality":"N:1","logical_key_pairs":[{"from":p(eref,"promotion_id"),"to":p(promotion,"promotion_id")}]},provenance)
    return entity,relation


def normalize_bridge_relation(asset,bridge_ref):
    result=copy.deepcopy(asset);spec=result["spec"]
    spec["to_entity_ref"]=copy.deepcopy(bridge_ref);spec["predicate"]="has_promotion_link";spec["to_role"]="promotion_link";spec["cardinality"]="1:N"
    spec["logical_key_pairs"]=[{"from":{"entity_ref":copy.deepcopy(spec["from_entity_ref"]),"property_id":"order_id"},"to":{"entity_ref":copy.deepcopy(bridge_ref),"property_id":"order_id"}}]
    result["definition"]="订单通过order_id关联订单促销记录，再由关联记录的promotion_id连接促销；原N:M桥不能伪装成订单与促销的直接等值键。"
    return result


def normalize_fresh_bridge_import(db,wid,assets):
    """Keep an existing bad r1 immutable, but never import new invalid refs."""
    source=next(a for a in assets if a["asset_id"]=="relation:rel_order_promotions")
    entity,relation=bridge_assets(wid,source["provenance_refs"])
    found=db.get(SemanticRevision,(wid,source["asset_id"],1))
    existing_normalized=found and found.content["spec"]["to_entity_ref"]["asset_id"]==entity["asset_id"]
    if not found or existing_normalized:
        index=next(i for i,a in enumerate(assets) if a["asset_id"]==source["asset_id"])
        assets[index]=normalize_bridge_relation(source,ref(entity))
    assets.extend([entity,relation])
    return assets


def repair_local_semantics(db,wid,user):
    if user.role!="admin":failure("BLOCKED_PERMISSION","本地导入修复仅管理员可运行",403)
    event_id="semantic-repair:"+str(wid)+":"+REPAIR_VERSION
    previous=db.get(SemanticEvent,event_id)
    if previous:return {**previous.content,"replayed":True}
    rows={r.asset_id:r for r in current_assets(db,wid)}
    if "relation:rel_order_promotions" not in rows:failure("REPAIR_SOURCE_INVALID","缺少原始促销关系",409)
    for extra in bridge_assets(wid,rows["relation:rel_order_promotions"].content["provenance_refs"]):
        if extra["asset_id"] not in rows:
            save_asset(db,wid,user,extra)
            rows[extra["asset_id"]]=db.get(SemanticRevision,(wid,extra["asset_id"],1))
    mids=[aid for aid,row in rows.items() if row.asset_type=="metric" and "binding:"+aid.split(":",1)[1]+".local_v1" in rows]
    if len(mids)!=12:failure("REPAIR_SOURCE_INVALID","本修复仅适用于完整的12指标冻结本地导入",409)
    target_ids=["entity:commerce.Order"]+[aid for aid,row in rows.items() if row.asset_type=="dimension"]+mids+["term:"+aid.split(":",1)[1] for aid in mids]+["binding:"+aid.split(":",1)[1]+".local_v1" for aid in mids]
    # References are exact revision identities. Cascade to a fixed point so a
    # local release never needs old and new revisions of one logical identity.
    affected=set(target_ids)
    while True:
        dependents={aid for aid,row in rows.items() if aid not in affected and any(kind=="ASSET" and target["asset_id"] in affected for _,kind,target in walk_refs(row.content))}
        if not dependents:break
        target_ids.extend(sorted(dependents));affected.update(dependents)
    if any(aid not in rows or rows[aid].revision!=1 for aid in target_ids):failure("REPAIR_CONFLICT","首次修复需要原始r1；已有人工修订时必须显式迁移，不能覆盖",409)
    original={aid:copy.deepcopy(rows[aid].content) for aid in target_ids}
    desired={aid:copy.deepcopy(value) for aid,value in original.items()}
    refs={aid:{**ref(value),"revision":2} for aid,value in desired.items()}

    def upgrade(value):
        if isinstance(value,dict):
            if set(value)=={"workspace_id","asset_id","asset_type","revision"}:
                return copy.deepcopy(refs.get(value["asset_id"],value))
            return {k:upgrade(v) for k,v in value.items()}
        if isinstance(value,list):return [upgrade(v) for v in value]
        return value
    desired={aid:upgrade(value) for aid,value in desired.items()}
    for value in desired.values():value["revision"]=2
    for value in desired.values():
        if value["asset_type"]=="dimension":
            value["spec"]["history_policy"]="CURRENT"
            value["spec"]["null_member"]="ERROR"
            value["definition"]+=" 本地技术查询采用当前源库快照中的维度值；不代表历史时点归属或业务批准。"
        if value["asset_type"]=="metric":value["spec"]["time_policy"]["history_policy"]="CURRENT"
    relation_id="relation:rel_order_promotions"
    if relation_id in desired:
        desired[relation_id]=normalize_bridge_relation(desired[relation_id],ref(rows["entity:commerce.OrderPromotion"].content))
    for value in desired.values():
        if value["asset_type"]=="relation":
            spec=value["spec"];left=spec["from_entity_ref"]["asset_id"];right=spec["to_entity_ref"]["asset_id"]
            lname=(desired.get(left) or rows[left].content)["name"];rname=(desired.get(right) or rows[right].content)["name"]
            value["name"]=lname+"关联"+rname
    order=desired["entity:commerce.Order"]
    order_binding=original["binding:gross_order_value.local_v1"]
    gross_target=order_binding["spec"]["calculation"]["args"][0]["field_ref"]
    gross_field=resolve_physical(db,wid,gross_target)
    if not any(p["property_id"]=="gross_order_value" for p in order["spec"]["properties"]):
        order["spec"]["properties"].append({"property_id":"gross_order_value","name":"含运费订单总额","data_type":gross_field["data_type"],"semantic_role":"MEASURE","nullable":gross_field["nullable"],"unit":None})
    def prop(entity_id,pid):return {"entity_ref":copy.deepcopy(refs.get(entity_id,ref(rows[entity_id].content))),"property_id":pid}
    def logical(p):return {"op":"property","property_ref":p}
    gross=desired["metric:gross_order_value"]["spec"]
    gross["expression"]={"op":"call","function":"sum","args":[logical(prop("entity:commerce.Order","gross_order_value"))]}
    active=desired["metric:active_customers"]["spec"]
    active["time_policy"]["property_ref"]=prop("entity:commerce.Order","purchased_at")
    active["grain"]={"description":"选定下单时间范围内有订单的客户账户，以customer_unique_id去重；不以注册时间过滤。","property_refs":[prop("entity:commerce.Customer","customer_unique_id")]}
    promo=desired["metric:promotion_orders"]["spec"]
    promo["entity_ref"]=copy.deepcopy(refs["entity:commerce.Order"])
    promo["expression"]={"op":"call","function":"count_distinct","args":[logical(prop("entity:commerce.Order","order_id"))]}
    promo["distinct_property_refs"]=[prop("entity:commerce.Order","order_id")]
    promo["grain"]={"description":"至少关联一次促销活动的订单，按order_id去重；一单多个促销只计一单。","property_refs":[prop("entity:commerce.Order","order_id")]}
    promo["time_policy"]["property_ref"]=prop("entity:commerce.Order","purchased_at")

    audit=[]
    def leaf_properties(expression,seen=None):
        seen=seen or set()
        if expression.get("op")=="property":return [expression["property_ref"]]
        if expression.get("op")=="metric":
            target=expression["metric_ref"];key=canonical(target)
            if key in seen:failure("REPAIR_SOURCE_INVALID","派生指标存在循环",409)
            metric=desired.get(target["asset_id"]) or resolve_asset(db,wid,target)
            return leaf_properties(metric["spec"]["expression"],seen|{key})
        result=[]
        for value in expression.values():
            if isinstance(value,dict):result.extend(leaf_properties(value,seen))
            elif isinstance(value,list):
                for child in value:
                    if isinstance(child,dict):result.extend(leaf_properties(child,seen))
        return result
    for mid in mids:
        metric=desired[mid];bid="binding:"+mid.split(":",1)[1]+".local_v1";binding=desired[bid];spec=binding["spec"]
        spec["applicability"]["description"]+=" 当前源库快照取值策略；不提供AS_OF或SCD2历史归属。JOIN空键ERROR为执行拒绝策略，不表示空键已通过质量核验。"
        for join in spec["joins"]:join["null_key_policy"]="ERROR"
        base=spec["base_dataset_ref"];dataset=resolve_physical(db,wid,base);base_fields={f["field_id"] for f in dataset["fields"]}
        mappings={}
        def add(p,expression):
            key=canonical(p)
            if key in mappings and mappings[key]["expression"]!=expression:failure("REPAIR_SOURCE_INVALID","一个逻辑属性存在冲突物理实现",409)
            mappings[key]={"property_ref":copy.deepcopy(p),"expression":copy.deepcopy(expression)}
        for p in leaf_properties(metric["spec"]["expression"])+metric["spec"]["grain"]["property_refs"]:
            if p["property_id"] not in base_fields:failure("REPAIR_SOURCE_INVALID","统计属性不在基础物理表，不能猜测映射："+p["property_id"],409)
            add(p,{"op":"field","field_ref":{**base,"field_id":p["property_id"]}})
        time_property=metric["spec"]["time_policy"]["property_ref"]
        if not time_property:failure("REPAIR_SOURCE_INVALID","缺少可证实的业务时间属性",409)
        add(time_property,{"op":"field","field_ref":spec["time_field_ref"]})
        for implementation in spec["dimension_implementations"]:
            dimension=desired.get(implementation["dimension_ref"]["asset_id"]) or resolve_asset(db,wid,implementation["dimension_ref"])
            add({"entity_ref":dimension["spec"]["entity_ref"],"property_id":dimension["spec"]["property_id"]},implementation["expression"])
        spec["property_mappings"]=[mappings[key] for key in sorted(mappings)]
        primary=next((key["field_ids"] for key in dataset["key_constraints"] if key["kind"]=="PRIMARY_KEY"),None)
        if primary:
            spec["physical_grain"]["key_field_refs"]=[{**base,"field_id":pid} for pid in primary]
            spec["physical_grain"]["description"]="基础物理表声明主键粒度："+", ".join(primary)+"；实时唯一性仍须执行前核验。"
        audit.append({"metric_id":mid,"metric_ref":ref(metric),"binding_ref":ref(binding),"before_logical_expression":original[mid]["spec"]["expression"],"after_logical_expression":metric["spec"]["expression"],"time_property_ref":time_property,"time_field_ref":spec["time_field_ref"],"property_mapping_count":len(mappings),"physical_key_fields":[f["field_id"] for f in spec["physical_grain"]["key_field_refs"]],"coverage_authority":"Binding.coverage; unknown remains unknown","production_approved":False})
    available={(a["asset_id"],a["revision"]):a for a in desired.values()}
    for asset in desired.values():
        validate(asset,"asset_revision")
        issues=reference_issues(db,wid,asset,available)
        if issues:failure("REPAIR_SOURCE_INVALID","修复后仍有无效引用",422,asset_id=asset["asset_id"],issue_codes=issues)
    # Metric dependencies are resolved through the exact available draft map;
    # no source/gold content or original revision is rewritten.
    saved=[]
    for aid in target_ids:
        gov=db.get(SemanticGovernance,(wid,aid))
        item=save_asset(db,wid,user,desired[aid],base_revision=1,expected_record_version=gov.record_version,available=available)
        saved.append(item["asset_ref"])
    content={"repair_version":REPAIR_VERSION,"reason":REASON,"workspace_id":wid,"created_revisions":saved,"metrics":audit,"preserved_revision_1":True,"business_approval_created":False,"production_approved":False,"execution_sidecars_created":0}
    db.add(SemanticEvent(id=event_id,workspace_id=wid,event_type="local_semantics.repaired",content=content))
    record_event(db,wid,user,"local_semantics.repair_completed",{"scope":"local_validation","reason":REASON,"revision_count":len(saved)})
    db.flush()
    return {**content,"replayed":False}


def repair_local_relation_mappings(db,wid,user):
    """Add exact logical JOIN keys as new Binding revisions, never guess them."""
    if user.role!="admin":failure("BLOCKED_PERMISSION","本地关联映射修复仅管理员可运行",403)
    event_id="semantic-repair:"+str(wid)+":"+RELATION_REPAIR_VERSION
    previous=db.get(SemanticEvent,event_id)
    if previous:return {**previous.content,"replayed":True}
    rows={row.asset_id:row for row in current_assets(db,wid)}
    roots=[aid for aid,row in rows.items() if row.asset_type=="binding" and row.content["spec"]["joins"]]
    if len(roots)!=5 or any(rows[aid].revision!=2 for aid in roots):failure("REPAIR_CONFLICT","关联修复仅适用于原始5个r2本地Binding，人工修订不能自动覆盖",409)
    changed={aid:copy.deepcopy(rows[aid].content) for aid in roots}
    audit=[]
    for aid,asset in changed.items():
        spec=asset["spec"];mappings={canonical(item["property_ref"]):copy.deepcopy(item) for item in spec["property_mappings"]}
        before=copy.deepcopy(spec["calculation"])
        for join in spec["joins"]:
            relation=resolve_asset(db,wid,join["logical_relation_ref"])
            if len(join["key_pairs"])!=1 or len(relation["spec"]["logical_key_pairs"])!=1:failure("REPAIR_SOURCE_INVALID","冻结本地关联修复仅处理已核对的单键关系",409)
            physical=join["key_pairs"][0];logical_pair=relation["spec"]["logical_key_pairs"][0]
            identities=(logical_pair["from"]["entity_ref"]["asset_id"],logical_pair["from"]["property_id"],logical_pair["to"]["entity_ref"]["asset_id"],logical_pair["to"]["property_id"])
            if relation["asset_id"]=="relation:rel_customer_orders" and identities==("entity:commerce.Customer","customer_id","entity:commerce.Order","customer_id"):
                assignments=[(logical_pair["from"],physical["to"]),(logical_pair["to"],physical["from"])]
            elif relation["asset_id"]=="relation:rel_order_promotions" and identities==("entity:commerce.Order","order_id","entity:commerce.OrderPromotion","order_id"):
                assignments=[(logical_pair["from"],physical["to"]),(logical_pair["to"],physical["from"])]
                if aid!="binding:promotion_orders.local_v1":failure("REPAIR_SOURCE_INVALID","促销键修复出现未登记Binding",409)
                # Count actual Order identities through the verified N:1 JOIN.
                # INNER orphan and uniqueness gates prove equivalence to the
                # previous bridge order_id count without inventing a second
                # physical implementation for the same logical property.
                spec["calculation"]={"op":"call","function":"count_distinct","args":[{"op":"field","field_ref":copy.deepcopy(physical["to"])}]}
            else:failure("REPAIR_SOURCE_INVALID","关联逻辑两端与已核对冻结本地结构不同，禁止猜测",409)
            for prop,field_ref in assignments:
                mappings[canonical(prop)]={"property_ref":copy.deepcopy(prop),"expression":{"op":"field","field_ref":copy.deepcopy(field_ref)}}
        spec["property_mappings"]=[mappings[key] for key in sorted(mappings)]
        spec["applicability"]["description"]+=" 逻辑关系键和方向经明确属性映射后与物理JOIN一致；真实性仍由执行前唯一性、空键和孤儿核验。"
        audit.append({"binding_id":aid,"from_revision":rows[aid].revision,"to_revision":rows[aid].revision+1,"before_calculation":before,"after_calculation":spec["calculation"],"logical_join_refs":[join["logical_relation_ref"] for join in spec["joins"]],"property_mapping_count":len(mappings)})
    while True:
        dependents={aid:copy.deepcopy(row.content) for aid,row in rows.items() if aid not in changed and any(kind=="ASSET" and target["asset_id"] in changed for _,kind,target in walk_refs(row.content))}
        if not dependents:break
        changed.update(dependents)
    refs={aid:{**ref(asset),"revision":rows[aid].revision+1} for aid,asset in changed.items()}
    def upgrade(value):
        if isinstance(value,dict):
            if set(value)=={"workspace_id","asset_id","asset_type","revision"}:return copy.deepcopy(refs.get(value["asset_id"],value))
            return {key:upgrade(child) for key,child in value.items()}
        if isinstance(value,list):return [upgrade(child) for child in value]
        return value
    changed={aid:upgrade(asset) for aid,asset in changed.items()}
    for aid,asset in changed.items():asset["revision"]=refs[aid]["revision"]
    available={(asset["asset_id"],asset["revision"]):asset for asset in changed.values()}
    saved=[]
    for aid,asset in changed.items():
        gov=db.get(SemanticGovernance,(wid,aid))
        item=save_asset(db,wid,user,asset,base_revision=rows[aid].revision,expected_record_version=gov.record_version,available=available)
        saved.append(item["asset_ref"])
    from .semantic_compiler import derive_contract
    for aid in roots:derive_contract(db,wid,changed[aid])
    content={"repair_version":RELATION_REPAIR_VERSION,"workspace_id":wid,"reason":"逻辑关系键必须通过显式属性映射展开为物理JOIN，校验方向与基数；为现有正确业务关系补完整可维护实现。","created_revisions":saved,"bindings":audit,"preserved_prior_revisions":True,"business_approval_created":False,"production_approved":False,"execution_sidecars_created":0}
    db.add(SemanticEvent(id=event_id,workspace_id=wid,event_type="local_semantic_relations.repaired",content=content));db.flush()
    return {**content,"replayed":False}


def main():
    import argparse
    from pathlib import Path
    from .db import SessionLocal,User
    parser=argparse.ArgumentParser();parser.add_argument("--workspace",type=int,default=3);parser.add_argument("--output");args=parser.parse_args()
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.role=="admin",User.enabled==1).order_by(User.id))
        result=repair_local_semantics(db,args.workspace,user)
        result["relation_mapping_repair"]=repair_local_relation_mappings(db,args.workspace,user);db.commit()
    if args.output:Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"repair_version":REPAIR_VERSION,"replayed":result["replayed"],"created_revisions":len(result["created_revisions"]),"production_approved":False}))


if __name__=="__main__":main()
