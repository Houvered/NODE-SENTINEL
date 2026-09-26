# -*- coding: utf-8 -*-
"""
Human review queue API for case evidence.

- Reviewers see every extraction with evidence sentence, page, offsets,
  confidence, provider, and current review status.
- Approve (analyst-reviewed) / verify / reject / mark uncertain / edit the
  normalized value. Review requires the REVIEW_EXTRACTIONS permission.
- Duplicate suggestions group identical normalized values; merging needs
  explicit confirmation and re-points already-promoted graph edges.
- Graph build promotes only analyst-reviewed|verified rows with full
  provenance; rejected/uncertain rows never enter the graph.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.audit_logger import audit_logger
from app.core.auth_service import get_current_user
from app.core.case_access import require_case_member, require_case_writer
from app.core.case_store import REVIEW_STATUSES, get_case_store
from app.core.graph_engine import get_graph_engine
from app.core.graph_promote import promote_case_graph
from app.models.auth_models import Permission, ROLE_PERMISSIONS, User

router = APIRouter(tags=["case-review"])


class ReviewAction(BaseModel):
    review_status: str = Field(..., description="analyst-reviewed | verified | rejected | uncertain")
    normalized: Optional[str] = Field(None, description="Corrected canonical value")


class MergeRequest(BaseModel):
    keep_id: str
    merge_ids: List[str] = Field(default_factory=list)


def _audit(request: Request, action: str, user: User, case_id: str,
           resource_id: str = "", details: Optional[dict] = None) -> None:
    ip = request.client.host if request.client else None
    d = {"case_id": case_id}
    if details:
        d.update(details)
    audit_logger.log(action=action, user_id=user.user_id, username=user.username,
                     role=user.role, resource_type="extraction", resource_id=resource_id or case_id,
                     ip_address=ip, status="SUCCESS", details=d)


def _need_reviewer(user: User) -> None:
    if Permission.REVIEW_EXTRACTIONS not in ROLE_PERMISSIONS.get(user.role, []):
        raise HTTPException(status_code=403,
                            detail="Access denied: role lacks REVIEW_EXTRACTIONS permission")


@router.get("/cases/{case_id}/extractions")
def list_extractions(case_id: str, review_status: Optional[str] = Query(None),
                     kind: Optional[str] = Query(None),
                     limit: int = Query(200, ge=1, le=1000),
                     user: User = Depends(get_current_user)):
    require_case_member(case_id, user)
    if review_status and review_status not in REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown review status '{review_status}'")
    items = get_case_store().list_extractions(case_id, review_status=review_status, kind=kind, limit=limit)
    counts = {s: 0 for s in REVIEW_STATUSES}
    for e in get_case_store().list_extractions(case_id, limit=1000):
        counts[e["review_status"]] = counts.get(e["review_status"], 0) + 1
    return {"case_id": case_id, "counts": counts, "extractions": items}


@router.post("/cases/{case_id}/extractions/{extraction_id}/review")
def review_extraction(case_id: str, extraction_id: str, payload: ReviewAction,
                      request: Request, user: User = Depends(get_current_user)):
    require_case_writer(case_id, user)
    _need_reviewer(user)
    if payload.review_status not in ("analyst-reviewed", "verified", "rejected", "uncertain"):
        raise HTTPException(status_code=422, detail="Review must be analyst-reviewed | verified | rejected | uncertain")
    store = get_case_store()
    ext = store.get_extraction(extraction_id)
    if not ext or ext["case_id"] != case_id:
        raise HTTPException(status_code=404, detail="Extraction not found in this case")
    norm = payload.normalized.strip().upper() if payload.normalized else None
    updated = store.set_review(extraction_id, payload.review_status, user.user_id, normalized=norm)
    _audit(request, "RELATIONSHIP_APPROVED" if ext["kind"] == "relationship" else "ENTITY_APPROVED",
           user, case_id, resource_id=extraction_id,
           details={"kind": ext["kind"], "from": ext["review_status"], "to": payload.review_status})
    return {"extraction": updated}


@router.get("/cases/{case_id}/duplicates")
def duplicate_suggestions(case_id: str, user: User = Depends(get_current_user)):
    """Possible duplicates (same normalized value). Merging needs confirmation."""
    require_case_member(case_id, user)
    return {"case_id": case_id, "groups": get_case_store().duplicate_suggestions(case_id)}


@router.post("/cases/{case_id}/extractions/merge")
def merge_extractions(case_id: str, payload: MergeRequest,
                      request: Request, user: User = Depends(get_current_user)):
    """Confirmed merge: re-point promoted graph edges, drop duplicate nodes/rows."""
    require_case_writer(case_id, user)
    _need_reviewer(user)
    if not payload.merge_ids or payload.keep_id in payload.merge_ids:
        raise HTTPException(status_code=422, detail="Provide keep_id plus at least one different merge_id")
    store = get_case_store()
    keep = store.get_extraction(payload.keep_id)
    if not keep or keep["case_id"] != case_id or keep["kind"] != "entity":
        raise HTTPException(status_code=404, detail="keep_id must be an entity extraction in this case")
    from app.core.graph_promote import entity_node_id
    keep_node = entity_node_id(keep["etype"], keep["value"], keep["normalized"])
    if not keep_node:
        raise HTTPException(status_code=422, detail="keep_id entity type cannot be graphed; merge it by editing instead")
    graph = get_graph_engine()
    merged, edges_moved, nodes_dropped = [], 0, 0
    from app.models.graph_models import Edge as GEdge
    for mid in payload.merge_ids:
        row = store.get_extraction(mid)
        if not row or row["case_id"] != case_id or row["kind"] != "entity":
            raise HTTPException(status_code=404, detail=f"merge_id '{mid}' not found in this case")
        if row["etype"] != keep["etype"]:
            raise HTTPException(status_code=422,
                                detail=f"Type mismatch: {row['etype']} cannot merge into {keep['etype']}")
        dupe_node = entity_node_id(row["etype"], row["value"], row["normalized"])
        _audit(request, "ENTITY_MERGED", user, case_id, resource_id=mid,
               details={"keep_id": payload.keep_id, "keep_node": keep_node,
                        "merged_extraction": {k: row.get(k) for k in
                                              ("value", "normalized", "etype", "evidence_text",
                                               "confidence", "provider", "review_status")},
                        "dropped_node": dupe_node})
        if dupe_node and dupe_node != keep_node and graph.get_node(dupe_node):
            for e in [x for x in graph.get_all_edges()
                      if x.source == dupe_node or x.target == dupe_node]:
                src = keep_node if e.source == dupe_node else e.source
                tgt = keep_node if e.target == dupe_node else e.target
                if src == tgt:
                    continue
                try:
                    graph.add_edge(GEdge(src, tgt, e.relationship, dict(e.properties or {})))
                    edges_moved += 1
                except ValueError:
                    continue
            if graph.remove_node(dupe_node):
                nodes_dropped += 1
        store._exec("DELETE FROM extractions WHERE id=?", (mid,))
        merged.append(mid)
    return {"status": "merged", "keep_id": payload.keep_id, "keep_node": keep_node,
            "merged": merged, "edges_moved": edges_moved, "nodes_dropped": nodes_dropped}


@router.post("/cases/{case_id}/graph/build")
def build_case_graph(case_id: str, request: Request, user: User = Depends(get_current_user)):
    """Promote approved extractions into the evidence-linked graph."""
    require_case_writer(case_id, user)
    _need_reviewer(user)
    result = promote_case_graph(get_case_store(), get_graph_engine(), case_id)
    ip = request.client.host if request.client else None
    audit_logger.log(action="GRAPH_UPDATED", user_id=user.user_id, username=user.username,
                     role=user.role, resource_type="case", resource_id=case_id,
                     ip_address=ip, status="SUCCESS",
                     details={"case_id": case_id, **result})
    return {"case_id": case_id, **result}
