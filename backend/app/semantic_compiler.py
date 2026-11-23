"""Derive execution inputs from immutable semantic revisions and catalog refs.

The legacy seed sidecar is an import audit artifact, never an execution authority.
Only the supported typed AST subset is compiled; unsupported semantics fail closed.
"""
import copy
import re


def derive_contract(db, wid, binding):
    from .semantic import resolve_asset, resolve_physical, sha, failure
    spec = binding["spec"]
    if spec.get("freshness_sla_minutes") is not None:
        failure("BINDING_NOT_EXECUTABLE", "当前本地执行器尚未实现新鲜度 SLA 核验，不能忽略维护的非空 SLA 后执行")
    metric = resolve_asset(db, wid, spec["metric_ref"])
    ms = metric["spec"]
    base = spec["base_dataset_ref"]
    datasets, aliases, mapping = {}, {}, {}

    def key(target):
        return tuple(target[k] for k in ("catalog_snapshot_id", "datasource_id", "dataset_id"))

    def register(target, alias):
        identity = key(target)
        if identity in datasets:
            return aliases[identity]
        if target["datasource_id"] != base["datasource_id"] or target["catalog_snapshot_id"] != base["catalog_snapshot_id"]:
            failure("BINDING_NOT_EXECUTABLE", "当前执行器不支持跨数据源或跨目录快照映射")
        dataset = resolve_physical(db, wid, target)
        if dataset["connector_kind"] != "POSTGRESQL":
            failure("BINDING_NOT_EXECUTABLE", "结构夹具不能作为真实查询数据源")
        logical = dataset["logical_locator"]
        physical = dataset["namespace"] + "." + dataset["physical_name"]
        if not all(re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", value) for value in (logical, physical)):
            failure("BINDING_NOT_EXECUTABLE", "当前执行器仅支持规范化的 PostgreSQL 表标识")
        if logical in mapping and mapping[logical] != physical:
            failure("BINDING_NOT_EXECUTABLE", "逻辑数据集存在不唯一物理定位")
        datasets[identity], aliases[identity], mapping[logical] = dataset, alias, physical
        return alias

    register(base, "b")
    pending = list(spec["joins"])
    ordered_joins, join_sql, join_checks = [], {}, {}
    while pending:
        progressed = False
        for join in list(pending):
            if key(join["from_dataset_ref"]) not in datasets:
                continue
            if key(join["to_dataset_ref"]) in datasets:
                failure("BINDING_NOT_EXECUTABLE", "循环或重复数据集 JOIN 尚未支持")
            if join["expected_cardinality"] not in {"N:1", "1:1"} or join["fanout_strategy"] != "REJECT" or join["temporal_condition"] is not None:
                failure("BINDING_NOT_EXECUTABLE", "JOIN 需要明确 N:1 / 1:1、防膨胀拒绝策略且无未支持时间条件")
            if join["null_key_policy"] not in {"KEEP", "ERROR"}:
                failure("BINDING_NOT_EXECUTABLE", "JOIN 空键必须明确选择保留或拒绝；未知及未实现的丢弃策略不能默默执行")
            register(join["to_dataset_ref"], "j" + str(len(ordered_joins) + 1))
            ordered_joins.append(join)
            pending.remove(join)
            progressed = True
        if not progressed:
            failure("BINDING_NOT_EXECUTABLE", "JOIN 路径不能从主数据集顺序解析")

    def field(target):
        identity = key(target)
        if identity not in aliases:
            failure("BINDING_NOT_EXECUTABLE", "表达式字段不在当前 Binding 的数据集和 JOIN 范围内")
        found = resolve_physical(db, wid, target)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", found["name"]):
            failure("BINDING_NOT_EXECUTABLE", "当前执行器不支持该字段标识")
        return aliases[identity] + "." + found["name"]

    for join in ordered_joins:
        pairs = join["key_pairs"]
        if any(key(p["from"]) != key(join["from_dataset_ref"]) or key(p["to"]) != key(join["to_dataset_ref"]) for p in pairs):
            failure("BINDING_NOT_EXECUTABLE", "JOIN 键必须归属声明的两端数据集")
        conditions = [field(p["from"]) + " = " + field(p["to"]) for p in pairs]
        target = datasets[key(join["to_dataset_ref"])]
        join_sql[join["join_id"]] = join["join_type"] + " JOIN " + target["logical_locator"] + " " + aliases[key(join["to_dataset_ref"])] + " ON " + " AND ".join(conditions)
        join_checks[join["join_id"]] = copy.deepcopy(join)

    property_map = {sha(p["property_ref"]): p["expression"] for p in spec["property_mappings"]}
    if len(property_map) != len(spec["property_mappings"]):
        failure("BINDING_NOT_EXECUTABLE", "同一逻辑属性不能存在多个物理实现")
    for join in ordered_joins:
        relation = resolve_asset(db, wid, join["logical_relation_ref"])
        logical_pairs = []
        for pair in relation["spec"]["logical_key_pairs"]:
            left, right = property_map.get(sha(pair["from"])), property_map.get(sha(pair["to"]))
            if not left or not right or left.get("op") != "field" or right.get("op") != "field":
                failure("BINDING_NOT_EXECUTABLE", "逻辑关系键缺少对应的明确物理字段映射，不能只维护关系名称后执行 JOIN")
            logical_pairs.append((sha(left["field_ref"]), sha(right["field_ref"])))
        physical_pairs = [(sha(pair["from"]), sha(pair["to"])) for pair in join["key_pairs"]]
        direct = sorted(logical_pairs) == sorted(physical_pairs)
        reverse = sorted((right, left) for left, right in logical_pairs) == sorted(physical_pairs)
        cardinality = relation["spec"]["cardinality"]
        inverse = {"1:1": "1:1", "1:N": "N:1", "N:1": "1:N", "N:M": "N:M", "UNKNOWN": "UNKNOWN"}[cardinality]
        if not ((direct and cardinality == join["expected_cardinality"]) or (reverse and inverse == join["expected_cardinality"])):
            failure("BINDING_NOT_EXECUTABLE", "逻辑关系的键映射、方向或基数与 Binding 物理 JOIN 不一致")

    def substitute(expression, ancestors=()):
        if expression.get("op") == "metric":
            target_ref = expression["metric_ref"]
            identity = sha(target_ref)
            if identity in ancestors or len(ancestors) >= 12:
                failure("BINDING_NOT_EXECUTABLE", "派生指标依赖循环或超出受控展开深度")
            dependency = resolve_asset(db, wid, target_ref)
            dependency_spec = dependency["spec"]
            if dependency_spec["default_filter"] is not None or dependency_spec["time_policy"] != ms["time_policy"]:
                failure("BINDING_NOT_EXECUTABLE", "派生指标依赖含筛选或不同时间口径，当前执行器不能仅展开表达式而丢失条件")
            return substitute(dependency["spec"]["expression"], ancestors + (identity,))
        if expression.get("op") == "property":
            target = property_map.get(sha(expression["property_ref"]))
            if target is None:
                failure("BINDING_NOT_EXECUTABLE", "指标计算属性缺少对应的物理字段映射")
            return copy.deepcopy(target)
        return {k: substitute(v, ancestors) if isinstance(v, dict) else [substitute(x, ancestors) if isinstance(x, dict) else x for x in v] if isinstance(v, list) else v for k, v in expression.items()}

    if substitute(ms["expression"]) != spec["calculation"]:
        failure("BINDING_NOT_EXECUTABLE", "指标的逻辑计算与 Binding 物理计算不一致；请修复属性映射")

    def expression(node):
        if node["op"] == "field":
            return field(node["field_ref"])
        if node["op"] == "call" and node["function"] in {"count", "count_distinct", "sum", "avg", "min", "max"} and len(node["args"]) == 1:
            name = "COUNT" if node["function"] == "count_distinct" else node["function"].upper()
            return name + "(" + ("DISTINCT " if node["function"] == "count_distinct" else "") + expression(node["args"][0]) + ")"
        if node["op"] == "binary" and node["operator"] in {"+", "-", "*", "/"}:
            denominator = expression(node["right"])
            if node["operator"] == "/":
                denominator = "NULLIF(" + denominator + ", 0)"
            return "(" + expression(node["left"]) + " " + node["operator"] + " " + denominator + ")"
        failure("BINDING_NOT_EXECUTABLE", "该结构化表达式尚未实现受控编译")

    def joins(ids):
        if len(ids) != len(set(ids)) or any(jid not in join_sql for jid in ids):
            failure("BINDING_NOT_EXECUTABLE", "计算或维度引用了无效 JOIN")
        return [join_sql[j["join_id"]] for j in ordered_joins if j["join_id"] in ids]

    def expression_fields(value):
        if isinstance(value,dict):
            if value.get("op")=="field":yield value["field_ref"]
            else:
                for child in value.values():yield from expression_fields(child)
        elif isinstance(value,list):
            for child in value:yield from expression_fields(child)

    def require_join_scope(ids, fields):
        selected=set(ids);reachable={key(base)}
        if selected-set(join_sql):failure("BINDING_NOT_EXECUTABLE","计算或维度引用了无效 JOIN")
        for join in ordered_joins:
            if join["join_id"] not in selected:continue
            if key(join["from_dataset_ref"]) not in reachable:
                failure("BINDING_NOT_EXECUTABLE","所需 JOIN 集合缺少上游连接，不能生成依赖未绑定别名的查询")
            reachable.add(key(join["to_dataset_ref"]))
        if any(key(target) not in reachable for target in fields):
            failure("BINDING_NOT_EXECUTABLE","计算、业务时间或维度字段需要未纳入当前作用域的 JOIN")
    base_fields=list(expression_fields(spec["calculation"]))
    if spec["time_field_ref"]:base_fields.append(spec["time_field_ref"])
    require_join_scope(spec["base_join_ids"],base_fields)
    allowed = {sha(target): target for target in ms["allowed_dimension_refs"]}
    dimensions = {}
    for implementation in spec["dimension_implementations"]:
        target = implementation["dimension_ref"]
        if sha(target) not in allowed:
            failure("BINDING_NOT_EXECUTABLE", "物理维度实现不属于指标业务允许的维度修订")
        dimension = resolve_asset(db, wid, target)
        dimension_property = {"entity_ref": dimension["spec"]["entity_ref"], "property_id": dimension["spec"]["property_id"]}
        if property_map.get(sha(dimension_property)) != implementation["expression"]:
            failure("BINDING_NOT_EXECUTABLE", "维度的逻辑属性缺少对应映射，或与 Binding 物理维度表达式不一致")
        require_join_scope(set(spec["base_join_ids"])|set(implementation["required_join_ids"]),expression_fields(implementation["expression"]))
        did = dimension["asset_id"].split(":", 1)[-1]
        dimensions[did] = {"name": dimension["name"], "expression": expression(implementation["expression"]), "joins": joins(implementation["required_join_ids"]), "dimension_ref": target,
            "history_policy": dimension["spec"]["history_policy"], "null_member": dimension["spec"]["null_member"]}

    if not spec["time_field_ref"]:
        failure("BINDING_NOT_EXECUTABLE", "当前时间范围执行器要求明确的业务时间字段")
    time_property = ms["time_policy"]["property_ref"]
    if not time_property or property_map.get(sha(time_property)) != {"op": "field", "field_ref": spec["time_field_ref"]}:
        failure("BINDING_NOT_EXECUTABLE", "业务时间属性与物理时间字段需要显式且一致的映射，不能从表名或字段名推断")
    time_dataset = datasets[key(spec["time_field_ref"])]
    time_field = resolve_physical(db, wid, spec["time_field_ref"])
    if not re.search(r"date|timestamp", time_field["data_type"], re.I):
        failure("BINDING_NOT_EXECUTABLE", "业务时间映射必须引用日期或时间字段")
    event_aliases = {time_field["name"], time_field["business_definition"]["name_zh"]}
    if time_property:
        propref = time_property
        entity = resolve_asset(db, wid, propref["entity_ref"])
        prop = next((p for p in entity["spec"]["properties"] if p["property_id"] == propref["property_id"]), None)
        if prop:
            event_aliases.update({prop["name"], prop["property_id"]})
    # Synonyms are derived from this revision's documented event meaning. A
    # stable metric ID never grants an old event meaning to a newly edited map.
    documented = " ".join(event_aliases)
    for marker, synonyms in {
        "下单": {"下单", "下单时间", "订单创建", "订单创建时间", "订单下单时间", "order_placed", "order_purchase", "purchase"},
        "会话开始": {"会话开始", "会话开始时间", "浏览会话开始时间", "session_started"},
        "发货": {"发货", "发货时间", "shipment_shipped"},
        "评价": {"评价", "评价时间", "评价创建时间", "review_created"},
        "支付": {"支付", "支付时间", "付款时间", "payment_paid"},
    }.items():
        if marker in documented:
            event_aliases.update(synonyms)

    metric_id = metric["asset_id"].split(":", 1)[-1]
    unit = ms["unit"]
    calc = spec["calculation"]
    physical_grain = spec["physical_grain"]["key_field_refs"]
    if not physical_grain or len({sha(target) for target in physical_grain}) != len(physical_grain) or any(key(target) != key(base) for target in physical_grain):
        failure("BINDING_NOT_EXECUTABLE", "物理粒度必须由基础数据集内非空且不重复的字段引用完整定义")
    physical_grain_expressions = [field(target) for target in physical_grain]
    physical_grain_fields = [resolve_physical(db,wid,target)["name"] for target in physical_grain]
    if len(set(physical_grain_fields)) != len(physical_grain_fields):
        failure("BINDING_NOT_EXECUTABLE", "物理粒度不能用不同字段引用重复指向同一真实列")
    metric_grain_expressions = []
    for target in ms["grain"]["property_refs"]:
        mapped = property_map.get(sha(target))
        if mapped is None:
            failure("BINDING_NOT_EXECUTABLE", "指标业务粒度属性缺少对应的明确物理映射")
        metric_grain_expressions.append(expression(mapped))
    if not metric_grain_expressions or len(set(metric_grain_expressions)) != len(metric_grain_expressions):
        failure("BINDING_NOT_EXECUTABLE", "指标业务粒度必须明确且不能重复")
    distinct = [property_map.get(sha(target)) for target in ms["distinct_property_refs"]]
    if calc.get("op") == "call" and calc.get("function") == "count_distinct":
        if len(distinct) != 1 or distinct[0] != calc["args"][0]:
            failure("BINDING_NOT_EXECUTABLE", "去重属性声明经映射后必须与 count_distinct 的唯一统计参数一致")
    elif distinct:
        failure("BINDING_NOT_EXECUTABLE", "当前非去重聚合不能忽略已声明的去重业务属性")
    measure_expression = expression(calc["args"][0]) if calc.get("op") == "call" and calc.get("function") in {"count","count_distinct","avg"} and len(calc["args"]) == 1 else None
    result_kind = "COUNT" if calc.get("op") == "call" and calc.get("function") in {"count", "count_distinct"} and unit["kind"] == "COUNT" and unit["scale"] == 1 else "SCORE" if calc.get("op") == "call" and calc.get("function") == "avg" and unit["kind"] == "NUMBER" and unit["code"] == "score" and unit["scale"] == 1 else "UNSUPPORTED"
    if unit["authority"] not in {"CONFIRMED", "SYNTHETIC_ONLY"}:
        result_kind = "UNSUPPORTED"
    unsupported = any((ms["default_filter"] is not None, spec["base_filter"] is not None, spec["applicability"]["scope_filter"] is not None))
    source = datasets[key(base)]
    result = {"metric_id": metric_id, "name": metric["name"], "definition": metric["definition"],
        "metric_ref": spec["metric_ref"], "metric_content_hash": sha(metric), "binding_content_hash": sha(binding),
        "metric_expression": ms["expression"],
        "source_object": source["logical_locator"], "expression": expression(calc), "time_expression": field(spec["time_field_ref"]),
        "temporal_mode": "event_range" if ms["time_policy"]["kind"] == "EVENT_RANGE" else "snapshot_exact_day",
        "base_joins": joins(spec["base_join_ids"]), "dimensions": dimensions, "physical_mapping": mapping,
        "catalog_fingerprints": {dataset["namespace"] + "." + dataset["physical_name"]: dataset["schema_fingerprint"] for dataset in datasets.values()},
        "datasource_id": int(base["datasource_id"]), "catalog_snapshot_id": base["catalog_snapshot_id"],
        "scope": "local_validation", "unit": "count" if unit["kind"] == "COUNT" else unit["code"], "unit_definition": unit,
        "result_kind": result_kind, "unsupported_filters": unsupported, "max_dimensions": ms["max_dimensions"],
        "forbidden_dimension_sets": ms["forbidden_dimension_sets"], "time_event_aliases": sorted(event_aliases),
        "time_physical_source": time_dataset["namespace"] + "." + time_dataset["physical_name"], "time_field_name": time_field["name"],
        "join_checks": join_checks, "base_join_ids": spec["base_join_ids"],
        "ordered_join_ids":[join["join_id"] for join in ordered_joins],"join_sql_by_id":join_sql,
        "dimension_join_ids": {d["dimension_ref"]["asset_id"].split(":", 1)[-1]: d["required_join_ids"] for d in spec["dimension_implementations"]},
        "parameter_bindings": spec["parameter_bindings"], "authority": "AssetRevision+CatalogSnapshot", "compiler_version": "typed-binding/1.0.0"}
    result.update(metric_null_policy=ms["null_policy"],empty_result_policy=ms["empty_result_policy"],measure_expression=measure_expression,
        physical_grain_fields=physical_grain_fields,physical_grain_expressions=physical_grain_expressions,metric_grain_expressions=metric_grain_expressions)
    result["time_policy"] = copy.deepcopy(ms["time_policy"])
    result["source_contract_hash"] = sha(result)
    return result
