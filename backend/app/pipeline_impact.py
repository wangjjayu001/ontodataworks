"""Exact catalog references and transitive immutable semantic revision impact."""
from sqlalchemy import select
from .db import Asset
from .semantic_models import SemanticSnapshot, SemanticReference, SemanticRevision
from .semantic import can_read_asset
from .pipeline_catalog import physical_aliases


def semantic_impact(db, wid, user, assets):
    aliases = set()
    for aid in assets:
        asset = db.get(Asset, aid)
        if asset: aliases.update(physical_aliases(db, wid, asset))
    physical = {(str(a.datasource_id), a.namespace, a.table_name) for a in db.scalars(select(Asset).where(Asset.id.in_(aliases)))}
    snapshots = {s.id: s for s in db.scalars(select(SemanticSnapshot).where(SemanticSnapshot.workspace_id == wid))}
    references = list(db.scalars(select(SemanticReference).where(SemanticReference.workspace_id == wid)))
    affected = {}; changed = True
    while changed:
        changed = False
        for reference in references:
            key = (reference.asset_id, reference.revision)
            if key in affected: continue
            target = reference.target.get("target_ref", {})
            direct = False; reason = None
            if "catalog_snapshot_id" in target:
                snapshot = snapshots.get(target["catalog_snapshot_id"])
                dataset = next((d for d in snapshot.content["datasets"] if d["dataset_id"] == target["dataset_id"] and str(d["datasource_id"]) == str(target["datasource_id"])), None) if snapshot else None
                if dataset and (str(dataset["datasource_id"]), dataset["namespace"], dataset["physical_name"]) in physical:
                    direct = True; reason = {"kind": "physical_reference", "slot": reference.slot, "target": target}
            elif (target.get("asset_id"), target.get("revision")) in affected:
                direct = True; reason = {"kind": "semantic_dependency", "slot": reference.slot, "target": target}
            if direct:
                revision = db.get(SemanticRevision, (wid, *key))
                if revision and can_read_asset(db, wid, user, revision.content):
                    affected[key] = {"asset_id": key[0], "revision": key[1], "asset_type": revision.asset_type, "name": revision.content["name"], "reason": reason}; changed = True
    return list(affected.values())
