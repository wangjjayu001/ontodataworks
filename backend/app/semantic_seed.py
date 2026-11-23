"""Idempotent local metadata migration. Never grants business approval.

Run explicitly after the v4 migration. Input is frozen metadata, not result rows.
"""
import copy
import hashlib
import json
import re
from pathlib import Path
from sqlalchemy import select
from .db import Asset, CatalogMetadata, Datasource, User
from .semantic import (canonical, sha, ref, validate, save_asset, create_release, build_index,
    activate, get_deployment, AUTHORITY, TYPES)
from .semantic_models import (SemanticStaging, SemanticSnapshot, SemanticSourceBlob,
    SemanticRevision, SemanticContract, SemanticGovernance)


def asset_ref(wid,kind,key):
    return {"workspace_id":wid,"asset_id":kind+":"+key,"asset_type":kind,"revision":1}


def entity_key(value):
    return value.replace(":",".")


def make_asset(wid,kind,key,name,definition,spec,provenance=None,aliases=None,domain=None):
    return {"workspace_id":wid,"asset_id":kind+":"+key,"asset_type":kind,"revision":1,
        "name":name,"definition":definition or "待业务审校",
        "domain_ref":asset_ref(wid,"domain",domain) if domain else None,
        "aliases":[{"text":a,"language":"zh-CN","kind":"EXACT_ALIAS","scope":"local-synthetic-metadata"} for a in aliases or []],
        "owner_ids":[],"steward_ids":[],"validity":{"from":None,"to":None},
        "provenance_refs":provenance or [],"policy_ref":{"policy_id":None,"revision":None,"resolution":"UNKNOWN"},"spec":spec}


def migrate_catalog(db,wid,mapping,source_revision):
    live=db.scalars(select(Asset).where(Asset.workspace_id==wid,Asset.state=="active")).all()
    reverse={v:k for k,v in mapping.items()}
    datasets=[]
    for a in live:
        physical=a.namespace+"."+a.table_name
        logical=reverse.get(physical,physical)
        metadata=db.scalar(select(CatalogMetadata).where(CatalogMetadata.workspace_id==wid,CatalogMetadata.asset_id==a.id))
        maintained=metadata.spec if metadata else {}
        defs=maintained.get("columns",maintained.get("fields",{}))
        if isinstance(defs,list):defs={f.get("name",f.get("column_name")):f for f in defs}
        fields=[]
        for c in a.columns:
            definition=defs.get(c["name"],{}) if isinstance(defs,dict) else {}
            legacy_unit=definition.get("unit")
            if isinstance(legacy_unit,str):legacy_unit=legacy_unit.strip() or None
            fields.append({"field_id":c["name"],"name":c["name"],"data_type":c["type"],"nullable":c.get("nullable"),"semantic_role":"IDENTIFIER" if c.get("primary_key") else "ATTRIBUTE","unit":None,"classification":"SENSITIVE" if c["name"] in ("email","postal_code") else "INTERNAL","business_definition":{"name_zh":definition.get("name_zh") or c.get("comment") or c["name"],"definition":definition.get("definition") or c.get("comment") or "待维护释义","aliases":[],"grain":None,"legacy_unit_text":legacy_unit,"owner_ids":[],"steward_ids":[],"quality_rule_refs":[],"provenance_refs":[],"policy_ref":{"policy_id":None,"revision":None,"resolution":"UNKNOWN"}}})
        constraints=[]
        keys=sorted([c for c in a.columns if c.get("primary_key")],key=lambda c:c.get("primary_key_position") or 0)
        if keys:constraints.append({"constraint_id":"pk:"+logical,"kind":"PRIMARY_KEY","field_ids":[c["name"] for c in keys],"target_dataset_ref":None,"target_field_ids":[],"observed_status":"UNKNOWN"})
        datasets.append({"dataset_id":logical,"datasource_id":str(a.datasource_id),"connector_kind":"POSTGRESQL","namespace":a.namespace,"physical_name":a.table_name,"logical_locator":logical,"schema_fingerprint":a.fingerprint,"fingerprint_algorithm":"platform-catalog-fields-sha256-v1","fields":fields,"key_constraints":constraints,"policy_ref":{"policy_id":None,"revision":None,"resolution":"UNKNOWN"},"constraints_state":"DECLARED" if constraints else "UNKNOWN"})
    sid="catalog:live:"+sha(datasets)[:24]
    snapshot={"workspace_id":wid,"catalog_snapshot_id":sid,"source_revision":source_revision,"datasets":datasets}
    validate(snapshot,"catalog_snapshot")
    if not db.get(SemanticSnapshot,sid):db.add(SemanticSnapshot(id=sid,workspace_id=wid,content=snapshot,content_hash=sha(snapshot)));db.flush()
    return snapshot


def seed_local(db,wid,user,payload,deploy=True):
    if user.role!="admin":raise ValueError("Seed requires administrator")
    nodes=payload["virtual_model"]["nodes"]
    for n in nodes:
        key=(wid,n["uid"])
        old=db.get(SemanticStaging,key)
        if old:
            if old.content_hash!=sha(n):raise ValueError("Frozen migration source drift: "+n["uid"])
        else:db.add(SemanticStaging(workspace_id=wid,old_uid=n["uid"],role=n["role"],content=n,content_hash=sha(n),migration_status="DRAFT_MAPPING"))
    raw_catalog=payload["metadata_catalog_text"]
    raw_contracts=payload["query_contracts_text"]
    catalog=json.loads(raw_catalog);contracts=json.loads(raw_contracts)
    source_revision=contracts["revision"]
    mapping=payload["physical_mapping"]
    snapshot=migrate_catalog(db,wid,mapping,source_revision)
    datasets={d["dataset_id"]:d for d in snapshot["datasets"]}
    def ds(logical):
        d=datasets[logical]
        return {"catalog_snapshot_id":snapshot["catalog_snapshot_id"],"datasource_id":d["datasource_id"],"dataset_id":logical}
    def field(logical,column):return {**ds(logical),"field_id":column}
    def prop(entity,column):return {"entity_ref":asset_ref(wid,"entity",entity_key(entity)),"property_id":column}
    sources=[]
    for key,raw in [("metadata_catalog",raw_catalog),("query_contracts",raw_contracts)]:
        content_hash=hashlib.sha256(raw.encode()).hexdigest()
        content={"raw_text":raw,"json":json.loads(raw)}
        if not db.get(SemanticSourceBlob,(wid,"source:"+key,content_hash)):
            db.add(SemanticSourceBlob(workspace_id=wid,source_id="source:"+key,content_hash=content_hash,content=content))
        sources.append(make_asset(wid,"source",key,key+".json","冻结本地技术材料，不构成业务审批",{"source_kind":"CATALOG","uri":"archive://semantic/"+key,"source_revision":source_revision,"content_sha256":content_hash,"classification":"INTERNAL","authority":"TECHNICAL_ONLY"}))
    provenance=[asset_ref(wid,"source","metadata_catalog")]
    qprov=[asset_ref(wid,"source","query_contracts")]
    entities={e["entity_id"]:e for e in catalog["entities"]}
    metrics={m["metric_id"]:m for m in catalog["metrics"]}
    entity_nodes={n["native_id"]:n for n in nodes if n["role"]=="entity"}
    metric_nodes={n["native_id"]:n for n in nodes if n["role"]=="metric"}
    assets=list(sources)
    domains=sorted({n.get("payload",{}).get("business_domain") for n in nodes if n.get("payload",{}).get("business_domain")})
    for i,domain in enumerate(domains):
        assets.append(make_asset(wid,"domain","local_"+str(i+1),domain,"本地元数据登记的业务域；边界待Owner审核",{"parent_ref":None,"scope_notes":"仅本地技术验证"},provenance))
    for eid,e in entities.items():
        properties=[]
        for p in e["properties"]:
            role=p.get("semantic_role","attribute").upper()
            if role not in ("IDENTIFIER","ATTRIBUTE","MEASURE","EVENT_TIME","SNAPSHOT_TIME","BUSINESS_DATE"):role="ATTRIBUTE"
            properties.append({"property_id":p["property_id"],"name":p["label"],"data_type":p["data_type"],"semantic_role":role,"nullable":None,"unit":None})
        assets.append(make_asset(wid,"entity",entity_key(eid),e["label"],e["definition"],{"kind":"ENTITY","properties":properties,"business_key":e["key_fields"],"grain":e["grain"]},provenance,e.get("aliases",[])))
    for r in catalog["relationships"]:
        pairs=[]
        for c in r["join_conditions"]:
            # The source relation retains logical key identities; execution JOIN
            # mappings remain separately scoped to individual Binding revisions.
            left=c["left"].split(".")[-1];right=c["right"].split(".")[-1]
            source_fields={p["property_id"] for p in entities[r["source_entity"]]["properties"]}
            target_fields={p["property_id"] for p in entities[r["target_entity"]]["properties"]}
            from_key=left if left in source_fields else right
            to_key=right if right in target_fields else left
            pairs.append({"from":prop(r["source_entity"],from_key),"to":prop(r["target_entity"],to_key)})
        assets.append(make_asset(wid,"relation",r["relationship_id"],r["predicate"],"源目录声明的业务关系；执行等价性仍需核验",{"from_entity_ref":asset_ref(wid,"entity",entity_key(r["source_entity"])),"to_entity_ref":asset_ref(wid,"entity",entity_key(r["target_entity"])),"predicate":r["predicate"],"from_role":"source","to_role":"target","cardinality":r["cardinality"],"logical_key_pairs":pairs},provenance))
    dimspecs={"customer_state":("commerce:Customer","state","买家所在地区（省/州）","地域代码，不是买家业务状态",["买家所在地区","买家地区","客户所在地区","省份","客户所在州"]),"product_category":("commerce:Product","category_name","商品类目","商品类别分组属性",["商品类别","商品品类"])}
    for did,(eid,pid,name,definition,aliases) in dimspecs.items():
        assets.append(make_asset(wid,"dimension",did,name,definition,{"entity_ref":asset_ref(wid,"entity",entity_key(eid)),"property_id":pid,"value_type":"STRING","null_member":"UNKNOWN","parent_dimension_refs":[],"value_dictionary_ref":None,"history_policy":"UNKNOWN"},qprov,aliases))
    # Evidence is a lossless JSON-pointer slice of the exact archived source.
    for i,e in enumerate(catalog["evidence"]):
        excerpt=canonical(e)
        evidence=make_asset(wid,"evidence",e["evidence_id"],e["evidence_id"],"本地技术QA观察，不是业务审批",{"source_ref":provenance[0],"locator":{"kind":"JSON_POINTER","pointer":"/evidence/"+str(i)},"excerpt":excerpt,"fragment_sha256":hashlib.sha256(excerpt.encode()).hexdigest(),"evidence_kind":"TECHNICAL_QA","observed_at":None,"assertion":e["assertion"]},provenance)
        assets.append(evidence)
        assets.append(make_asset(wid,"claim",e["evidence_id"]+".technical",e["evidence_id"]+"技术观察",e["assertion"],{"subject_ref":provenance[0],"claim_kind":"TECHNICAL_OBSERVATION","statement":e["assertion"],"condition":None,"evidence_links":[{"evidence_ref":ref(evidence),"stance":"SUPPORT"}]},provenance))
    bindings=[]
    for contract in contracts["contracts"]:
        mid=contract["metric_id"];m=metrics[mid];entity_id=contract["entity_ids"][0]
        base=contract["source_object"]
        key_field=next((c["field_id"] for c in datasets[base]["fields"] if c["semantic_role"]=="IDENTIFIER"),datasets[base]["fields"][0]["field_id"])
        expression_text=contract["expression"]
        expr_match=re.fullmatch(r"(count|sum|avg)\((DISTINCT )?(?:b\.)?(\w+)\)",expression_text,re.I)
        logical_fields={p["property_id"] for p in entities[entity_id]["properties"]}
        expression_field=expr_match.group(3) if expr_match else "gross_order_value"
        logical_property=expression_field if expression_field in logical_fields else next(iter(entities[entity_id]["key_fields"]))
        if expr_match:
            fn="count_distinct" if expr_match.group(2) else expr_match.group(1).lower()
            metric_expr={"op":"call","function":fn,"args":[{"op":"property","property_ref":prop(entity_id,logical_property)}]}
            calc_expr={"op":"call","function":fn,"args":[{"op":"field","field_ref":field(base,expression_field)}]}
        else:
            metric_expr={"op":"binary","operator":"/","left":{"op":"metric","metric_ref":asset_ref(wid,"metric","gross_order_value")},"right":{"op":"metric","metric_ref":asset_ref(wid,"metric","order_count")}}
            calc_expr={"op":"binary","operator":"/","left":{"op":"call","function":"sum","args":[{"op":"field","field_ref":field(base,"gross_order_value")}]},"right":{"op":"call","function":"count_distinct","args":[{"op":"field","field_ref":field(base,"order_id")}]}}
        unit_kind="CURRENCY" if "CNY" in contract["unit"] else "COUNT" if contract["unit"]=="count" else "NUMBER"
        unit={"kind":unit_kind,"code":None if unit_kind=="CURRENCY" else "count" if unit_kind=="COUNT" else "score","currency":None,"scale":1,"authority":"UNKNOWN" if unit_kind=="CURRENCY" else "SYNTHETIC_ONLY"}
        time_property=contract["time_expression"].split(".")[-1]
        logical_time=time_property if time_property in logical_fields else None
        metric=make_asset(wid,"metric",mid,m["metric_name"],contract["definition"],{"metric_kind":"DERIVED" if not expr_match else "ATOMIC","entity_ref":asset_ref(wid,"entity",entity_key(entity_id)),"grain":{"description":m.get("semantic",{}).get("grain") or entities[entity_id]["grain"],"property_refs":[prop(entity_id,k) for k in entities[entity_id]["key_fields"]]},"expression":metric_expr,"depends_on_metric_refs":[asset_ref(wid,"metric",x) for x in ("gross_order_value","order_count")] if not expr_match else [],"distinct_property_refs":[prop(entity_id,logical_property)] if expr_match and expr_match.group(2) else [],"default_filter":None,"allowed_dimension_refs":[asset_ref(wid,"dimension",d) for d in contract["dimensions"]],"forbidden_dimension_sets":[],"max_dimensions":len(contract["dimensions"]),"time_policy":{"kind":"SNAPSHOT_DAY" if contract["temporal_mode"]=="snapshot_exact_day" else "EVENT_RANGE","property_ref":prop(entity_id,logical_time) if logical_time else None,"timezone":None,"interval":"EXACT_DAY" if contract["temporal_mode"]=="snapshot_exact_day" else "[start,end)","history_policy":"UNKNOWN"},"unit":unit,"additivity":{"across_time":"SEMI_ADDITIVE" if contract["temporal_mode"]=="snapshot_exact_day" else "UNKNOWN","across_dimensions":"UNKNOWN"},"null_policy":"REJECT_ROW_AND_WARN","empty_result_policy":"NO_DATA"},provenance+[asset_ref(wid,"claim",e+".technical") for e in contract["evidence_refs"]],metric_nodes[mid].get("aliases",[]))
        assets.append(metric)
        assets.append(make_asset(wid,"term",mid,m["metric_name"]+"术语",contract["definition"],{"target_refs":[ref(metric)],"target_relation":"EQUIVALENT","excluded_meanings":["不是未经审核的同名口径"]},provenance))
        aliases={"b":base}
        join_texts=list(contract["base_joins"])
        for d in contract["dimensions"].values():join_texts+=d["joins"]
        for jt in join_texts:
            match=re.search(r"JOIN\s+(\w+\.\w+)\s+(\w+)\s+ON",jt,re.I)
            if match:aliases[match.group(2)]=match.group(1)
        joins=[];by_text={}
        for jt in dict.fromkeys(join_texts):
            match=re.fullmatch(r"JOIN\s+(\w+\.\w+)\s+(\w+)\s+ON\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)",jt,re.I)
            if not match:raise ValueError("Unsupported known JOIN "+jt)
            table,alias,la,lf,ra,rf=match.groups()
            left=aliases[la];right=aliases[ra]
            rid=next(iter(contract["relationship_ids"]),None)
            for d in contract["dimensions"].values():
                if jt in d["joins"]:rid=next(iter(d["relationship_ids"]),rid)
            jid="join_"+str(len(joins)+1);by_text[jt]=jid
            joins.append({"join_id":jid,"logical_relation_ref":asset_ref(wid,"relation",rid),"from_dataset_ref":ds(left),"to_dataset_ref":ds(right),"key_pairs":[{"from":field(left,lf),"to":field(right,rf)}],"join_type":"INNER","expected_cardinality":"N:1","fanout_strategy":"REJECT","allocation_rule_ref":None,"null_key_policy":"UNKNOWN","temporal_condition":None,"equivalence_evidence_refs":[]})
        implementations=[]
        for dim,d in contract["dimensions"].items():
            alias,col=d["expression"].split(".");target=field(aliases[alias],col)
            fields=[target]
            for j in joins:
                if j["join_id"] in [by_text[t] for t in d["joins"]]:
                    for pair in j["key_pairs"]:fields.extend([pair["from"],pair["to"]])
            implementations.append({"dimension_ref":asset_ref(wid,"dimension",dim),"expression":{"op":"field","field_ref":target},"required_join_ids":[by_text[t] for t in d["joins"]],"required_field_refs":fields})
        time_alias,time_col=contract["time_expression"].split(".")
        time_ref=field(aliases[time_alias],time_col)
        property_mappings=[{"property_ref":prop(entity_id,logical_property),"expression":{"op":"field","field_ref":field(base,expression_field)}}]
        binding=make_asset(wid,"binding",mid+".local_v1",m["metric_name"]+"本地技术映射",contract["definition"]+" 仅本地合成数据技术测试，不构成生产认证。",{"metric_ref":ref(metric),"base_dataset_ref":ds(base),"calculation":calc_expr,"property_mappings":property_mappings,"physical_grain":{"description":entities[entity_id]["grain"],"key_field_refs":[field(base,key_field)]},"base_join_ids":[by_text[t] for t in contract["base_joins"]],"joins":joins,"dimension_implementations":implementations,"base_filter":None,"time_field_ref":time_ref,"partition_field_refs":[],"coverage":{"start":None,"end_exclusive":None,"data_as_of":None,"evidence_refs":[asset_ref(wid,"evidence",e) for e in contract["evidence_refs"]]},"freshness_sla_minutes":None,"verification_rule_ids":["TYPE_MAPPING","GRAIN","TEMPORAL_COVERAGE","POLICY"],"applicability":{"scope_filter":None,"description":"local_validation_only：只用于本地合成样本的技术验证；源timestamp时区未知，不转换时区"},"parameter_bindings":[{"name":"time_start","data_type":"TIMESTAMP","source":"INTENT_TIME_START","required":True},{"name":"time_end","data_type":"TIMESTAMP","source":"INTENT_TIME_END","required":True}]},qprov)
        assets.append(binding);bindings.append((binding,contract))
    from .semantic_repair import normalize_fresh_bridge_import
    assets=normalize_fresh_bridge_import(db,wid,assets)
    available={(a["asset_id"],a["revision"]):a for a in assets}
    for a in assets:validate(a,"asset_revision")
    for a in assets:
        old=db.get(SemanticRevision,(wid,a["asset_id"],1))
        if old:
            if old.content_hash!=sha(a):raise ValueError("Seed revision differs; create reviewed new revision: "+a["asset_id"])
        else:save_asset(db,wid,user,a,available=available)
    db.flush()
    # The original import remains an immutable draft for audit. Apply the
    # explicit typed mapping repair before its first technical deployment.
    # Historical sidecars are retained but never created or used as authority.
    from .semantic_repair import repair_local_semantics,repair_local_relation_mappings
    repaired=repair_local_semantics(db,wid,user)
    relation_repaired=repair_local_relation_mappings(db,wid,user)
    db.flush()
    if deploy and not get_deployment(db,wid,user):
        rel=create_release(db,wid,user,{"scope":"local_validation"})
        idx=build_index(db,wid,user,rel["release_ref"])
        activate(db,wid,user,"local_validation",{"release_ref":rel["release_ref"],"index_snapshot_ref":idx["index_snapshot_ref"],"expected_pointer_version":0})
    db.commit()
    return {"staged":len(nodes),"typed_revisions":len(assets),"catalog_datasets":len(datasets),"catalog_fields":sum(len(d["fields"]) for d in datasets.values()),"execution_contracts_created":0,"mapping_repair":repaired,"relation_mapping_repair":relation_repaired,"scope":"local_validation","production_ready_paths":0,"deployment":get_deployment(db,wid,user)}


def main():
    import argparse
    from .db import SessionLocal
    parser=argparse.ArgumentParser();parser.add_argument("--workspace",type=int,default=3);parser.add_argument("--input",default=str(Path(__file__).parent/"contracts"/"local-seed.json"));args=parser.parse_args()
    payload=json.loads(Path(args.input).read_text("utf-8"))
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.role=="admin",User.enabled==1).order_by(User.id))
        print(json.dumps(seed_local(db,args.workspace,user,payload),ensure_ascii=False))


if __name__=="__main__":main()
