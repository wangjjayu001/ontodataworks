"""Maintenance helpers: preserve source text, never infer business approval."""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone


def attachment_assets(wid, item, parent):
    """Build typed Source/Evidence revisions for text, inside the caller's transaction.

    A JSON envelope preserves the original text (including whitespace) as a string.
    Existing JSON_POINTER verification can therefore verify it without a second
    evidence format or trusting a client-provided hash.
    """
    from .semantic import canonical, failure, ref
    if not isinstance(item, dict):
        failure("SCHEMA_INVALID", "依据附件必须是对象", 422)
    title, raw = item.get("title"), item.get("text")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        failure("SCHEMA_INVALID", "请填写 200 字以内的依据名称", 422)
    if not isinstance(raw, str) or not raw.strip() or len(raw.encode()) > 2_000_000:
        failure("SCHEMA_INVALID", "依据需要为 2MB 以内的非空 UTF-8 文本", 422)
    kind = item.get("source_kind", "BUSINESS_SPEC")
    classification = item.get("classification", "INTERNAL")
    if kind not in {"BUSINESS_SPEC", "CATALOG", "SQL", "TECHNICAL_QA", "FEEDBACK"} or classification not in {"PUBLIC", "INTERNAL", "SENSITIVE"}:
        failure("SCHEMA_INVALID", "依据类别或敏感分类无效", 422)
    # Keep every original character. The hash describes this explicit envelope,
    # not a claim that an external document's original bytes have been archived.
    archived = canonical({"format": "UPLOADED_TEXT/1", "title": title.strip(), "text": raw})
    if len(archived.encode()) > 2_000_000:
        failure("SCHEMA_INVALID", "依据归档后超过 2MB，请缩小文本范围", 422)
    token = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).isoformat()
    common = dict(workspace_id=wid, revision=1, domain_ref=None, aliases=[],
        owner_ids=copy.deepcopy(parent.get("owner_ids", [])), steward_ids=copy.deepcopy(parent.get("steward_ids", [])),
        validity={"from": None, "to": None},
        policy_ref=copy.deepcopy(parent.get("policy_ref", {"policy_id":None,"revision":None,"resolution":"UNKNOWN"})))
    source = {**common, "asset_id":"source:attachment_"+token, "asset_type":"source",
        "name":title.strip(), "definition":"本次上传的依据材料："+title.strip(), "provenance_refs":[],
        "spec":{"source_kind":kind,"uri":"archive://attachment/"+token,"source_revision":stamp,
            "content_sha256":hashlib.sha256(archived.encode()).hexdigest(),"classification":classification,
            "authority":"UNREVIEWED"}}
    excerpt = canonical(raw)
    evidence = {**copy.deepcopy(common), "asset_id":"evidence:attachment_"+token, "asset_type":"evidence",
        "name":title.strip()+" · 依据", "definition":parent.get("definition") or title.strip(),
        "provenance_refs":[ref(source)], "spec":{"source_ref":ref(source),"locator":{"kind":"JSON_POINTER","pointer":"/text"},
            "excerpt":excerpt,"fragment_sha256":hashlib.sha256(excerpt.encode()).hexdigest(),
            "evidence_kind":{"BUSINESS_SPEC":"BUSINESS_TEXT","SQL":"SQL_LINEAGE","CATALOG":"CATALOG_OBSERVATION"}.get(kind,"TECHNICAL_QA"),
            "observed_at":stamp,"assertion":parent.get("definition") or title.strip()}}
    return source, evidence, archived


def save_with_attachments(db, wid, user, body, base_revision=None, expected_record_version=None):
    from .semantic import failure, save_asset, ref, record_event
    from .semantic_models import SemanticSourceBlob
    asset = copy.deepcopy(body.get("asset_revision", {}))
    attachments = body.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > 3:
        failure("SCHEMA_INVALID", "每次最多添加 3 份依据", 422)
    if not attachments:
        return save_asset(db,wid,user,asset,base_revision,expected_record_version)
    if sum(len(x.get("text", "").encode()) for x in attachments if isinstance(x,dict) and isinstance(x.get("text", ""),str)) > 2_000_000:
        failure("SCHEMA_INVALID", "本次依据总大小超过 2MB", 422)
    # Savepoint also guarantees no orphan attachments when parent validation/CAS
    # fails. command() commits the complete receipt only after this succeeds.
    with db.begin_nested():
        created = []
        for item in attachments:
            source,evidence,archived = attachment_assets(wid,item,asset)
            source_item = save_asset(db,wid,user,source)
            db.add(SemanticSourceBlob(workspace_id=wid,source_id=source["asset_id"],content_hash=source["spec"]["content_sha256"],content={"raw_text":archived,"json":json.loads(archived)}))
            db.flush()
            evidence_item = save_asset(db,wid,user,evidence)
            asset.setdefault("provenance_refs",[]).append(ref(evidence))
            created.extend([source_item["asset_ref"],evidence_item["asset_ref"]])
            record_event(db,wid,user,"source.archived",{"asset_id":source["asset_id"],"content_hash":source["spec"]["content_sha256"]})
        saved = save_asset(db,wid,user,asset,base_revision,expected_record_version)
        saved["attachment_refs"] = created
        return saved
