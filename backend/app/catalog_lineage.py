"""Read-only table/column lineage derived from recorded SQL and scoped catalog."""
import hashlib
from types import SimpleNamespace
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlglot import exp
from sqlglot.lineage import lineage
from sqlglot.optimizer.qualify import qualify
from .db import Asset, CatalogMetadata, TransformRun, TransformEdge, QueryRun, Lineage
from .security import identity, db_session, workspace_access
from .pipeline_catalog import guard_asset
from .warehouse import read_query, table_refs
from .pipeline_models import PipelineRun

router = APIRouter(prefix='/api/workspaces/{wid}', tags=['catalog-lineage-v1.2'])


def physical(asset):
    return asset.namespace + '.' + asset.table_name


def derive_columns(sql, assets):
    """Projection dependencies, never guessed by matching output names."""
    schema = {}
    by_name = {(a.namespace, a.table_name): a for a in assets}
    for asset in assets:
        schema.setdefault(asset.namespace, {})[asset.table_name] = {c['name']: c['type'] for c in asset.columns}
    tree = qualify(read_query(sql), dialect='postgres', schema=schema, validate_qualify_columns=True)
    result = []
    output_names = [p.alias_or_name for p in tree.selects]
    if len(output_names) != len(set(output_names)):
        raise ValueError('输出字段重名，无法唯一定位')
    for projection in tree.selects:
        name = projection.alias_or_name
        if not name or isinstance(projection, exp.Star):
            raise ValueError('输出列未命名，不能建立唯一字段引用')
        node = lineage(name, tree, schema=schema, dialect='postgres', trim_selects=False)
        sources = []
        unresolved = False
        for leaf in node.walk():
            if leaf.downstream:
                continue
            if isinstance(leaf.expression, exp.Table):
                table = leaf.expression
                asset = by_name.get((table.db or 'public', table.name))
                column = leaf.name.rsplit('.', 1)[-1].strip('"')
                if not asset or column not in {c['name'] for c in asset.columns}:
                    unresolved = True
                    continue
                ref = {'asset_id': asset.id, 'column': column}
                if ref not in sources:
                    sources.append(ref)
            elif isinstance(leaf.expression, exp.Placeholder):
                unresolved = True
        expression = projection.this if isinstance(projection, exp.Alias) else projection
        row_dependent = any(isinstance(n, exp.Count) and n.find(exp.Star) for n in expression.walk())
        result.append({'name': name, 'expression': expression.sql(dialect='postgres'), 'sources': sources,
                       'status': 'UNRESOLVED' if unresolved else 'RESOLVED' if sources else 'ROW_COUNT' if row_dependent else 'CONSTANT',
                       'row_dependency': row_dependent})
    # Qualification resolves scope aliases; condition columns are separate from value arrows.
    conditions = []
    from sqlglot.optimizer.scope import traverse_scope
    for scope in traverse_scope(tree):
        for clause in [scope.expression.args.get(k) for k in ('where', 'having', 'qualify', 'joins')]:
            for expression in clause if isinstance(clause, list) else [clause]:
                if expression is None:
                    continue
                for column in expression.find_all(exp.Column):
                    source = scope.sources.get(column.table)
                    if not isinstance(source, exp.Table):
                        continue
                    asset = by_name.get((source.db or 'public', source.name))
                    if asset and column.name in {c['name'] for c in asset.columns}:
                        item = {'asset_id': asset.id, 'column': column.name}
                        if item not in conditions:
                            conditions.append(item)
    return result, conditions


def build_lineage(db, wid, user, aid, depth=1, include_queries=True):
    workspace_access(db, user, wid)
    focus = db.get(Asset, aid)
    if not focus or focus.workspace_id != wid:
        raise HTTPException(404, '数据表不存在或不属于当前空间')
    guard_asset(db, wid, user, focus, raw=True)
    visible = {}
    for asset in db.scalars(select(Asset).where(Asset.workspace_id == wid)):
        try:
            guard_asset(db, wid, user, asset, raw=True)
            visible[asset.id] = asset
        except HTTPException:
            continue
    metadata = {m.asset_id: m.spec for m in db.scalars(select(CatalogMetadata).where(CatalogMetadata.workspace_id == wid))}
    runs = list(db.scalars(select(TransformRun).where(TransformRun.workspace_id == wid).order_by(TransformRun.id.desc()).limit(501)))
    latest = {}; hidden = False
    for run in runs[:500]:
        latest.setdefault((run.target_asset_id, run.kind), run)
    edges = list(db.scalars(select(TransformEdge).where(TransformEdge.workspace_id == wid)))
    parents = {}
    for edge in edges:
        parents.setdefault(edge.run_id, set()).add(edge.source_asset_id)
    pipeline_runs = list(db.scalars(select(PipelineRun).where(PipelineRun.workspace_id == wid,
        PipelineRun.status == 'SUCCEEDED').order_by(PipelineRun.completed_at.desc()).limit(501)))
    for run in pipeline_runs[:500]:
        config = run.snapshot.get('config', {}); kind = run.snapshot.get('kind')
        target = config.get('target_asset_id'); evidence = run.evidence or {}
        if kind not in {'sync','sql'} or not target or not evidence.get('committed'):
            continue
        key = (target, 'pipeline_' + kind)
        if key in latest:
            continue
        inputs = evidence.get('inputs', [])
        ids = {item.get('asset_id') for item in inputs}
        if not ids <= visible.keys() or target not in visible:
            continue
        query = config.get('sql', '')
        if kind == 'sync':
            origin = visible.get(config.get('source_asset_id'))
            if not origin or origin.id not in ids:
                continue
            query = exp.select(*[exp.alias_(exp.column(src, quoted=True), dst, quoted=True)
                for src,dst in config.get('mapping', {}).items()]).from_(
                    exp.Table(this=exp.to_identifier(origin.table_name, quoted=True),
                              db=exp.to_identifier(origin.namespace, quoted=True))).sql(dialect='postgres')
        proof = {**evidence, 'input_snapshots': inputs, 'origin': 'pipeline_commit_receipt'}
        proxy = SimpleNamespace(id='pipeline-' + run.id, target_asset_id=target, kind='pipeline_'+kind,
            select_sql=query, evidence=proof, status='committed', created_at=run.completed_at,
            datasource_id=visible[target].datasource_id)
        latest[key] = proxy; parents[proxy.id] = ids
    usable = []
    for run in latest.values():
        ids = parents.get(run.id, set()) | {run.target_asset_id}
        if ids <= visible.keys() and (run.kind == 'pipeline_sync' or
                all(visible[x].datasource_id==run.datasource_id for x in ids)):
            usable.append((run, ids))
    selected = {aid}; frontier = {aid}
    for _ in range(depth):
        following = set()
        for _, ids in usable:
            if ids & frontier:
                following |= ids
        frontier = following - selected
        selected |= following
    records = []; table_edges = []; column_edges = []; warnings = []; nodes = {}

    def node(asset):
        key = 'table:' + str(asset.id)
        if key not in nodes:
            spec = metadata.get(asset.id, {})
            definitions = {c['name']: c for c in spec.get('columns', [])}
            nodes[key] = {'id': key, 'asset_id': asset.id, 'kind': 'table', 'name': asset.name,
                'physical_name': physical(asset), 'layer': asset.layer, 'state': asset.state,
                'columns': [{'name': c['name'], 'label': definitions.get(c['name'], {}).get('chinese_name') or c['name'],
                             'type': c['type'], 'description': definitions.get(c['name'], {}).get('definition') or c.get('comment', '')}
                            for c in asset.columns]}
        return key

    node(focus)

    def record(key, sql, inputs, target_key, target_columns, kind, evidence, status, at):
        nonlocal hidden
        by_physical = {(a.namespace, a.table_name): a for a in visible.values() if a.datasource_id == inputs[0].datasource_id} if inputs else {}
        try:
            tree = read_query(sql)
            if any(table.catalog for table in tree.find_all(exp.Table)):
                hidden = True
                return
            refs = table_refs(tree)
            if any(ref not in by_physical for ref in refs):
                hidden = True
                return
            sql_inputs = [by_physical[ref] for ref in refs]
            if {a.id for a in sql_inputs} != {a.id for a in inputs}:
                warnings.append('一条记录的 SQL 来源与登记关系不一致，已省略该关系。')
                return
        except HTTPException:
            warnings.append('一条历史 SQL 不符合只读解析契约，已省略。')
            return
        fingerprint_stale = any(a.state != 'active' for a in inputs)
        for item in evidence.get('input_snapshots', []):
            asset = visible.get(item.get('asset_id'))
            if not asset or item.get('fingerprint') != asset.fingerprint:
                fingerprint_stale = True
        target_asset = visible.get(int(target_key.split(':')[1])) if target_key.startswith('table:') else None
        if target_asset and evidence.get('target_fingerprint') != target_asset.fingerprint:
            fingerprint_stale = True
        digest = hashlib.sha256(sql.encode()).hexdigest()
        if evidence.get('sql_sha256') and evidence['sql_sha256'] != digest:
            warnings.append('一条 SQL 证据指纹不一致，已省略。')
            return
        source_snapshot = evidence.get('metadata_fingerprint')
        if source_snapshot and any(a.fingerprint != source_snapshot for a in inputs):
            fingerprint_stale = True
        rec = {'id': key, 'kind': kind, 'status': 'SOURCE_CHANGED' if fingerprint_stale else status,
            'created_at': at, 'sql': sql, 'sql_sha256': digest, 'evidence': {k:evidence[k] for k in
                ('verified_at','origin','target_count','expected_count','mismatched_rows','compared_columns','historical_execution_proven',
                 'metadata_collected_at','physical_schema_verified_at') if k in evidence},
            'column_status': 'PENDING', 'condition_fields': []}
        try:
            columns, conditions = derive_columns(sql, sql_inputs)
            if target_columns and (any(c['name'] not in target_columns for c in columns) or
                    (kind not in {'pipeline_sync','pipeline_sql'} and [c['name'] for c in columns] != target_columns)):
                raise ValueError('SQL 输出列与目标目录字段顺序不一致')
            if not target_key.startswith('table:'):
                nodes[target_key]['columns'] = [{'name':c['name'],'label':c['name'],'type':'查询输出','description':c['expression']} for c in columns]
            rec.update(column_status='PARSED', condition_fields=conditions,
                       outputs=[{k:v for k,v in c.items() if k != 'sources'} for c in columns])
            if not fingerprint_stale:
                for column in columns:
                    if column['status'] == 'UNRESOLVED':
                        continue
                    for source in column['sources']:
                        source_key = node(visible[source['asset_id']])
                        column_edges.append({'id': f"{key}:{source_key}.{source['column']}:{column['name']}",
                            'source': source_key, 'source_column': source['column'], 'target': target_key,
                            'target_column': column['name'], 'expression': column['expression'], 'record_id': key})
        except Exception:
            rec.update(column_status='UNRESOLVED', column_notice='当前 SQL 无法唯一解析到目录字段；保留表级来源，不生成猜测的字段连线。')
        records.append(rec)
        for asset in inputs:
            source_key = node(asset)
            table_edges.append({'id': key + ':' + source_key, 'source': source_key, 'target': target_key, 'record_id': key})

    for run, ids in usable:
        if not (ids & {aid}) and not (ids <= selected):
            continue
        if not ids <= selected:
            continue
        target = visible[run.target_asset_id]
        record('transform:' + str(run.id), run.select_sql, [visible[x] for x in sorted(parents.get(run.id, set()))],
               node(target), [c['name'] for c in target.columns], run.kind, run.evidence or {}, run.status, run.created_at)
    if include_queries:
        query_ids = list(db.scalars(select(Lineage.query_id).where(Lineage.workspace_id == wid,
            Lineage.asset_id == aid).distinct().order_by(Lineage.query_id.desc()).limit(101)))
        query_edges = list(db.scalars(select(Lineage).where(Lineage.workspace_id == wid,
            Lineage.query_id.in_(query_ids[:100])).order_by(Lineage.id.desc()))) if query_ids else []
        groups = {}
        for edge in query_edges:
            groups.setdefault(edge.query_id, []).append(edge)
        for qid, group in list(groups.items())[:100]:
            if not any(e.asset_id == aid for e in group):
                continue
            query = db.get(QueryRun, qid)
            if not query or query.workspace_id != wid or query.status not in {'success', 'succeeded'} or any(e.asset_id not in visible for e in group):
                continue
            inputs = [visible[e.asset_id] for e in group]
            if any(a.datasource_id != query.datasource_id for a in inputs):
                continue
            qkey = 'query:' + str(qid)
            nodes[qkey] = {'id': qkey, 'kind': 'query', 'name': '查询结果 #' + str(qid), 'physical_name': qkey, 'columns': []}
            before = len(records)
            # Each source has its own structure receipt; do not apply one to every source.
            proof = {'sql_sha256': group[0].evidence.get('sql_sha256'),
                     'input_snapshots':[{'asset_id':e.asset_id,'fingerprint':e.evidence.get('metadata_fingerprint')} for e in group]}
            record(qkey, query.sql, inputs, qkey, None, 'query', proof, 'executed', query.created_at)
            if len(records) == before:
                nodes.pop(qkey, None)
    linked = {n for e in table_edges for n in (e['source'], e['target'])} | {'table:' + str(aid)}
    return {'api_version': 'catalog-lineage/1.2', 'workspace_id': wid, 'focus': 'table:' + str(aid), 'depth': depth,
        'nodes': [n for k,n in nodes.items() if k in linked], 'table_edges': table_edges, 'column_edges': column_edges,
        'records': records, 'warnings': list(dict.fromkeys(warnings)), 'restricted': hidden,
        'truncated': len(runs)>500 or len(pipeline_runs)>500 or (include_queries and len(query_ids)>100),
        'notice': '字段连线来自已记录 SQL 的静态值依赖解析；重算验证与静态声明在每条关系的证据中分别标记。JOIN/筛选字段单独列出，不冒充值依赖。'}


@router.get('/assets/{aid}/lineage')
def asset_lineage(wid:int, aid:int, depth:int=Query(1, ge=1, le=3), include_queries:bool=True,
                  user=Depends(identity), db=Depends(db_session)):
    return build_lineage(db, wid, user, aid, depth, include_queries)
