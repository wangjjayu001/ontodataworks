"""Compact, permission-scoped provenance persisted with every delivered query."""
from sqlalchemy import select
from fastapi import HTTPException

from .db import Asset, CatalogMetadata, Datasource
from .semantic import (
    SemanticRevision, SemanticSnapshot, SemanticVerification, can_read_asset,
    resolve_asset, resolve_physical, sha, ref, canonical,
)


def build_query_provenance(db, wid, user, frame, bundle, checks):
    snapshot_cache = {}
    batches = {item.get("asset_id"): item for item in checks.get("data_batches", [])
               if isinstance(item, dict)}
    physical_refs = frame.get("physical_refs", [])
    tables = []
    for checked in checks.get("verified_sources", []):
        asset = db.get(Asset, checked.get("asset_id"))
        if not asset or asset.workspace_id != wid:
            continue
        dataset_ref = None
        fields = []
        for item in physical_refs:
            target = item.get("target_ref") or {}
            if not target.get("catalog_snapshot_id"):
                continue
            base_ref = {k: target[k] for k in ("catalog_snapshot_id", "datasource_id", "dataset_id")}
            try:
                dataset = resolve_physical(db, wid, base_ref, snapshot_cache)
            except HTTPException:
                continue
            physical_object = dataset["namespace"] + "." + dataset["physical_name"]
            if physical_object != checked.get("physical_object"):
                continue
            dataset_ref = base_ref
            if item.get("kind") == "FIELD" and target.get("field_id"):
                try:
                    field = resolve_physical(db, wid, target, snapshot_cache)
                except HTTPException:
                    continue
                definition = field.get("business_definition") or {}
                fields.append({
                    "field_ref": target,
                    "field_id": field["field_id"],
                    "physical_name": field["name"],
                    "name_zh": definition.get("name_zh") or field["name"],
                    "definition": (definition.get("definition") or "")[:1000],
                    "data_type": field["data_type"],
                    "semantic_role": field.get("semantic_role"),
                    "unit": field.get("unit"),
                    "classification": field.get("classification"),
                })
        metadata = db.scalar(select(CatalogMetadata).where(
            CatalogMetadata.workspace_id == wid, CatalogMetadata.asset_id == asset.id))
        table_spec = metadata.spec if metadata else {}
        snapshot_info = None
        if dataset_ref:
            snapshot = db.get(SemanticSnapshot, dataset_ref["catalog_snapshot_id"])
            if snapshot and snapshot.workspace_id == wid:
                snapshot_info = {
                    "catalog_snapshot_id": snapshot.id,
                    "content_hash": snapshot.content_hash,
                    "source_revision": snapshot.content.get("source_revision"),
                }
        datasource = db.get(Datasource, asset.datasource_id)
        tables.append({
            "asset_id": asset.id,
            "dataset_ref": dataset_ref,
            "table_label": table_spec.get("name_zh") or table_spec.get("table_name_zh") or asset.name,
            "table_definition": (table_spec.get("definition") or asset.description or "")[:1000],
            "physical_object": checked.get("physical_object"),
            "datasource_name": datasource.name if datasource else "",
            "schema_fingerprint": checked.get("fingerprint"),
            "catalog_snapshot": snapshot_info,
            "fields": fields,
            "data_batch": batches.get(asset.id),
            "verified": True,
        })

    semantic_refs = []
    if bundle:
        semantic_refs.extend(
            item.get("target_ref") for item in bundle.get("closure_refs", [])
            if item.get("kind") == "ASSET")
        semantic_refs.extend(
            target for target in [
                bundle.get("selected_metric_ref"),
                bundle.get("selected_binding_ref"),
                *bundle.get("requested_dimension_refs", []),
            ] if target)
    unique = {canonical(target): target for target in semantic_refs
              if isinstance(target, dict) and target.get("asset_id")}
    semantic_assets, evidence, verification = [], [], []
    for target in unique.values():
        try:
            content = resolve_asset(db, wid, target)
            if not can_read_asset(db, wid, user, content):
                continue
            revision = db.get(SemanticRevision, (wid, target["asset_id"], target["revision"]))
            semantic_assets.append({
                "asset_ref": ref(content),
                "asset_type": content["asset_type"],
                "name": content["name"],
                "definition": content.get("definition", ""),
                "content_hash": revision.content_hash if revision else sha(content),
            })
            if content["asset_type"] == "evidence":
                spec = content["spec"]
                source = resolve_asset(db, wid, spec["source_ref"])
                evidence.append({
                    "evidence_ref": ref(content),
                    "name": content["name"],
                    "assertion": spec.get("assertion", ""),
                    "excerpt": (spec.get("excerpt") or "")[:2000],
                    "evidence_kind": spec.get("evidence_kind"),
                    "source_ref": spec.get("source_ref"),
                    "source_name": source.get("name"),
                    "source_revision": source.get("spec", {}).get("source_revision"),
                    "source_uri": source.get("spec", {}).get("uri"),
                    "source_hash": source.get("spec", {}).get("content_sha256"),
                    "locator": spec.get("locator"),
                    "fragment_sha256": spec.get("fragment_sha256"),
                })
            elif content["asset_type"] == "claim":
                evidence.append({
                    "claim_ref": ref(content),
                    "name": content["name"],
                    "assertion": content["spec"].get("statement", ""),
                    "evidence_links": content["spec"].get("evidence_links", []),
                })
            receipts = db.scalars(select(SemanticVerification).where(
                SemanticVerification.workspace_id == wid,
                SemanticVerification.asset_id == target["asset_id"],
                SemanticVerification.revision == target["revision"],
            )).all()
            for receipt in receipts:
                item = receipt.content
                verification.append({
                    "verification_id": receipt.id,
                    "target_ref": target,
                    "rule_id": item.get("rule_id"),
                    "outcome": item.get("outcome"),
                    "reason": item.get("reason", ""),
                    "observed_at": item.get("observed_at"),
                    "coverage_description": item.get("coverage_description", ""),
                    "evidence_refs": item.get("evidence_refs", []),
                })
        except HTTPException:
            continue

    candidate = frame.get("metadata_candidate")
    return {
        "schema_version": "query-provenance/1.0.0",
        "scope": "local_validation",
        "tables": tables,
        "semantic_assets": semantic_assets,
        "evidence": evidence,
        "verification_receipts": verification,
        "data_coverage": checks.get("data_coverage"),
        "data_as_of": checks.get("data_as_of"),
        "data_batches": [table["data_batch"] for table in tables if table.get("data_batch")],
        "temporary_candidate": ({
            "candidate_id": candidate.get("candidate_id"),
            "name": candidate.get("name"),
            "definition": candidate.get("definition"),
            "operation": candidate.get("operation"),
            "content_hash": candidate.get("content_hash"),
            "business_approved": False,
        } if candidate else None),
        "query_ref": {
            "run_id": frame.get("run_id"),
            "requirement_id": frame.get("requirement_id"),
            "query_frame_hash": frame.get("content_hash"),
        },
    }
