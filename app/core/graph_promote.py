# -*- coding: utf-8 -*-
"""
Review-gated graph promotion for case evidence.

Only extractions with review_status analyst-reviewed|verified are promoted.
Every node/edge carries provenance: case_id, source_document_id,
extraction_id, evidence_text, page, confidence, provider, review_status.
IDs are deterministic (value-derived for entities, EXT_<id> for edges) so
re-running build overwrites instead of duplicating.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

PROMOTABLE = {"analyst-reviewed", "verified"}

ETYPE_TO_NODE = {
    "Person": "Person", "Phone": "Phone", "Vehicle": "Vehicle",
    "BankAccount": "BankAccount", "Case": "Case", "Location": "Location",
    "Organization": "Organization",
}

ID_PREFIX = {"Person": "PERSON_", "Phone": "PHONE_", "Vehicle": "VEH_",
             "BankAccount": "ACC_", "Case": "CASE_", "Location": "LOC_",
             "Organization": "ORG_"}


def entity_node_id(etype: str, value: str, normalized: str) -> Optional[str]:
    """Deterministic node ID from an approved entity extraction."""
    if etype not in ETYPE_TO_NODE:
        return None  # DATE/AMOUNT/ALIAS/etc. are not graph nodes
    base = (normalized or value or "").strip().upper()
    if not base:
        return None
    if etype == "Phone":
        digits = re.sub(r"\D", "", base)
        if len(digits) == 10:
            digits = "91" + digits
        base = "+" + digits if digits else base
        return f"PHONE_{digits or base}"
    slug = re.sub(r"[^A-Z0-9+_]", "", base.replace(" ", "_")).strip("_")
    if not slug:
        return None
    return f"{ID_PREFIX[etype]}{slug}"


def promote_case_graph(store, graph, case_id: str) -> Dict[str, Any]:
    """Promote approved extractions. Returns counts + skips."""
    from app.models.graph_models import Edge, EdgeType, Node, NodeType

    nodes_created = nodes_reused = edges_created = edges_skipped = 0
    skipped_non_node = 0
    exts = store.list_extractions(case_id, limit=1000)
    approved = [e for e in exts if e["review_status"] in PROMOTABLE]

    def node_id_for(ext: Dict[str, Any]) -> Optional[str]:
        return entity_node_id(ext["etype"], ext["value"], ext["normalized"])

    # 1. entities
    for ext in approved:
        if ext["kind"] != "entity":
            continue
        nid = node_id_for(ext)
        if nid is None:
            skipped_non_node += 1
            continue
        label = NodeType(ext["etype"])
        existed = graph.get_node(nid) is not None
        props = {"case_id": case_id, "source_document_id": ext["document_id"],
                 "extraction_id": ext["id"], "evidence_text": (ext["evidence_text"] or "")[:500],
                 "page": ext["page"], "confidence": ext["confidence"],
                 "provider": ext["provider"], "review_status": ext["review_status"]}
        if label == NodeType.PHONE:
            props["phone_number"] = ext["value"]
        elif label == NodeType.VEHICLE:
            props["registration"] = ext["value"]
        elif label == NodeType.BANK_ACCOUNT:
            props["account_number"] = ext["value"]
        elif label == NodeType.CASE:
            props["case_code"] = ext["value"]
        graph.add_node(Node(nid, label, ext["value"], props))
        if existed:
            nodes_reused += 1
        else:
            nodes_created += 1

    # 2. case anchor + person links
    anchor_id = f"CASE_{case_id}"
    if graph.get_node(anchor_id) is None:
        from app.core.case_store import get_case_store
        case = get_case_store().get_case(case_id) or {}
        graph.add_node(Node(anchor_id, NodeType.CASE, case.get("title", case_id),
                            {"case_code": case_id, "case_id": case_id, "source": "case-platform"}))

    # 3. relationships
    for ext in approved:
        if ext["kind"] != "relationship":
            continue
        shaped = _shape_edge(store, graph, ext, case_id)
        if shaped is None:
            edges_skipped += 1
            continue
        src, tgt, rel, props = shaped
        eid = f"EXT_{ext['id']}"
        if any(e.id == eid for e in graph.get_all_edges()):
            edges_skipped += 1
            continue
        try:
            graph.add_edge(Edge(src, tgt, rel, props, id=eid))
            edges_created += 1
        except ValueError:
            edges_skipped += 1

    # 4. person -> case anchor links
    for ext in approved:
        if ext["kind"] != "entity" or ext["etype"] != "Person":
            continue
        nid = node_id_for(ext)
        if nid and graph.get_node(nid):
            try:
                graph.add_edge(Edge(nid, anchor_id, EdgeType.INVOLVED_IN,
                                    {"case_id": case_id, "source_document_id": ext["document_id"],
                                     "extraction_id": ext["id"], "review_status": ext["review_status"],
                                     "role": "Associated in the available records"},
                                    id=f"EXT_CASE_{ext['id']}"))
            except ValueError:
                pass

    # 5. mark source documents graph_updated when fully promoted
    for doc in store.list_documents(case_id):
        if doc["status"] != "approved":
            continue
        remaining = [e for e in store.list_extractions(case_id, limit=1000)
                     if e["document_id"] == doc["id"] and e["review_status"] not in PROMOTABLE
                     and e["kind"] in ("entity", "relationship")]
        promotable_left = [e for e in store.list_extractions(case_id, limit=1000)
                           if e["document_id"] == doc["id"] and e["review_status"] in PROMOTABLE]
        if not remaining and promotable_left:
            store.set_doc_status(doc["id"], "graph_updated")

    return {"nodes_created": nodes_created, "nodes_reused": nodes_reused,
            "edges_created": edges_created, "edges_skipped": edges_skipped,
            "skipped_non_node_types": skipped_non_node,
            "approved_extractions": len(approved)}


def _shape_edge(store, graph, ext: Dict[str, Any], case_id: str) -> Optional[Tuple[str, str, Any, Dict[str, Any]]]:
    from app.models.graph_models import EdgeType

    subj, obj = (ext.get("subject") or "").strip(), (ext.get("object") or "").strip()
    if not subj or not obj:
        return None
    # Subjects/objects are NLP entity IDs; map via the same document's
    # entity rows (external_id) to canonical promoted node IDs.
    src = _resolve_endpoint(store, graph, case_id, subj)
    tgt = _resolve_endpoint(store, graph, case_id, obj)
    if not src or not tgt or not graph.get_node(src) or not graph.get_node(tgt):
        return None
    try:
        rel = EdgeType(str(ext.get("predicate") or "INVOLVED_IN"))
    except ValueError:
        rel = EdgeType.INVOLVED_IN
    props = {"case_id": case_id, "source_document_id": ext["document_id"],
             "extraction_id": ext["id"], "evidence_text": (ext["evidence_text"] or "")[:500],
             "page": ext["page"], "confidence": ext["confidence"],
             "provider": ext["provider"], "review_status": ext["review_status"],
             "original_predicate": str(ext.get("predicate") or "")}
    return src, tgt, rel, props


def _resolve_endpoint(store, graph, case_id: str, nlp_id: str) -> Optional[str]:
    """Map an NLP entity ID to its canonical promoted node ID."""
    if graph.get_node(nlp_id):
        return nlp_id
    for ext in store.list_extractions(case_id, limit=1000):
        if ext["kind"] == "entity" and (ext.get("external_id") or "") == nlp_id:
            nid = entity_node_id(ext["etype"], ext["value"], ext["normalized"])
            if nid:
                return nid
    return None
