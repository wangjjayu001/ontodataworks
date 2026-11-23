import hashlib
import json
import os
import re
from cryptography.fernet import Fernet
import psycopg
from psycopg import sql
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
from fastapi import HTTPException

cipher = Fernet(os.environ["ENCRYPTION_KEY"].encode())
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
LAYERS = {"ODS", "DWD", "DWS", "DIM", "ADS"}
FIELD_TYPES = {"text", "varchar(100)", "varchar(255)", "bigint", "integer", "double precision", "numeric(18,2)", "numeric(18,4)", "numeric(22,2)", "numeric(38,2)", "date", "timestamp", "boolean"}


def connect(source):
    return psycopg.connect(host=source.host, port=source.port, dbname=source.database, user=source.username,
        password=cipher.decrypt(source.secret.encode()).decode(), connect_timeout=5,
        options="-c statement_timeout=10000 -c lock_timeout=2000", application_name="independent_workbench")


def read_query(query):
    try:
        statements = sqlglot.parse(query, read="postgres")
    except sqlglot.errors.ParseError:
        raise HTTPException(400, "SQL 语法解析失败")
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise HTTPException(400, "查询台仅支持单条 SELECT / WITH 查询")
    tree = statements[0]
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Into, exp.Copy)
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise HTTPException(400, "查询中包含写入或管理语句")
    if re.search(r"\b(pg_sleep|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_terminate_backend|lo_import|lo_export|dblink|set_config)\s*\(", query, re.I):
        raise HTTPException(400, "查询包含不允许的管理函数")
    return tree


def table_refs(tree):
    # Scope resolution distinguishes physical tables from CTE aliases, including
    # a CTE that deliberately shares its name with a qualified physical table.
    return sorted({(source.db or "public", source.name)
        for scope in traverse_scope(tree) for source in scope.sources.values()
        if isinstance(source, exp.Table) and source.name})


def inspect_catalog(source):
    assets = []
    with connect(source) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        for namespace in source.schemas:
            tables = conn.execute("""SELECT c.relname, coalesce(obj_description(c.oid),'') AS comment,
                CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END
                FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
                WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m')
                AND has_table_privilege(c.oid,'SELECT') ORDER BY c.relname""", (namespace,)).fetchall()
            for name, comment, estimate in tables:
                columns = conn.execute("""SELECT a.attname, format_type(a.atttypid,a.atttypmod),
                    NOT a.attnotnull, coalesce(col_description(c.oid,a.attnum),'')
                    FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (namespace,name)).fetchall()
                constraints = conn.execute("""SELECT con.contype, a.attname, con.conname,
                    rn.nspname, rc.relname, ra.attname, k.ord
                    FROM pg_constraint con
                    JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace ns ON ns.oid=c.relnamespace
                    CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY k(attnum,ord)
                    JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
                    LEFT JOIN pg_class rc ON rc.oid=con.confrelid
                    LEFT JOIN pg_namespace rn ON rn.oid=rc.relnamespace
                    LEFT JOIN pg_attribute ra ON ra.attrelid=rc.oid AND ra.attnum=con.confkey[k.ord]
                    WHERE ns.nspname=%s AND c.relname=%s AND con.contype IN ('p','f')
                    ORDER BY con.conname,k.ord""", (namespace,name)).fetchall()
                fields = []
                for n,t,nullable,note in columns:
                    refs = [{"namespace": ns,"table": tb,"column": col,"constraint": cn}
                        for kind,cnm,cn,ns,tb,col,ord in constraints if kind=='f' and cnm==n]
                    fields.append({"name":n,"type":t,"nullable":nullable,"comment":note,
                        "primary_key":any(kind=='p' and cnm==n for kind,cnm,*_ in constraints),
                        "primary_key_position":next((order for kind,cnm,cn,ns,tb,col,order in constraints if kind=='p' and cnm==n),None),
                        "foreign_keys":refs})
                fingerprint = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
                prefix = name.split("_")[0].upper()
                assets.append({"namespace": namespace, "table_name": name, "name": comment.splitlines()[0].strip() if comment.strip() else name,
                    "description": comment, "columns": fields, "row_estimate": estimate, "fingerprint": fingerprint,
                    "layer": prefix if prefix in LAYERS else namespace.upper() if namespace.upper() in LAYERS else ""})
    return assets


def model_ddl(model):
    fields = model.spec.get("fields", [])
    if not fields:
        raise HTTPException(400, "模型至少需要一个字段")
    namespace = model.spec.get("namespace", "public")
    if not IDENTIFIER.fullmatch(namespace):
        raise HTTPException(400, "Schema 名称不符合规则")
    chunks = []
    for field in fields:
        name, dtype = field.get("name", ""), field.get("type", "text").lower()
        if not IDENTIFIER.fullmatch(name) or dtype not in FIELD_TYPES:
            raise HTTPException(400, "字段名或字段类型不符合规则")
        chunks.append(f'  "{name}" {dtype}' + (" NOT NULL" if not field.get("nullable", True) else ""))
    keys = model.spec.get("primary_keys", [])
    if keys:
        if not set(keys).issubset({f["name"] for f in fields}):
            raise HTTPException(400, "主键必须引用模型内字段")
        chunks.append("  PRIMARY KEY (" + ", ".join(f'"{k}"' for k in keys) + ")")
    for fk in model.spec.get('foreign_keys',[]):
        local=fk.get('columns',[]); remote=fk.get('reference_columns',[])
        identifiers=local+remote+[fk.get('reference_schema',''),fk.get('reference_table','')]
        if not local or len(local)!=len(remote) or not set(local).issubset({f['name'] for f in fields}) or any(not IDENTIFIER.fullmatch(n) for n in identifiers):
            raise HTTPException(400,'外键字段或目标标识不符合规则')
        chunks.append('  FOREIGN KEY ('+', '.join(f'"{k}"' for k in local)+') REFERENCES "'+fk['reference_schema']+'"."'+fk['reference_table']+'" ('+', '.join(f'"{k}"' for k in remote)+')')
    command=f'CREATE TABLE "{namespace}"."{model.code}" (\n' + ",\n".join(chunks) + "\n);"
    def literal(value):return "'"+str(value).replace("'","''")+"'"
    if getattr(model,'name',None):command+='\nCOMMENT ON TABLE "'+namespace+'"."'+model.code+'" IS '+literal(model.name)+';'
    for field in fields:
        if field.get('comment'):command+='\nCOMMENT ON COLUMN "'+namespace+'"."'+model.code+'"."'+field['name']+'" IS '+literal(field['comment'])+';'
    return command
