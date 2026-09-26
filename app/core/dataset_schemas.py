# -*- coding: utf-8 -*-
"""
Structured dataset schemas, quality analysis, and graph import for cases.

Canonical datasets:
- cdr            : calling_number*, receiving_number*, call_start_time* ...
- financial      : sender_account*, receiver_account*, amount*,
                   transaction_time*, transaction_id* ...
- vehicle        : vehicle_registration* ...
- location_event : event_time* ...
- generic        : anything else (stored, previewed, not graphed)

Flow: stage_structured_upload() parses + sniffs type + auto-maps columns +
writes a data-quality report (required/found/missing/invalid/duplicates/
ignored) and returns a preview. confirm_import() re-reads the preserved
original, enforces minimum requirements, and wires evidence-linked graph
nodes/edges (provenance: case, import, record id, reviewer).
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CANONICAL: Dict[str, Dict[str, List[str]]] = {
    "cdr": {
        "required": ["calling_number", "receiving_number", "call_start_time"],
        "optional": ["call_end_time", "duration_seconds", "call_type", "cell_tower_id",
                     "latitude", "longitude", "source_record_id"],
    },
    "financial": {
        "required": ["sender_account", "receiver_account", "amount", "transaction_time", "transaction_id"],
        "optional": ["sender_name", "receiver_name", "currency", "transaction_type",
                     "bank_or_provider", "source_record_id"],
    },
    "vehicle": {
        "required": ["vehicle_registration"],
        "optional": ["owner_name", "user_name", "date_from", "date_to", "transfer_date", "source_record_id"],
    },
    "location_event": {
        "required": ["event_time"],
        "optional": ["entity_id", "entity_name", "location", "latitude", "longitude",
                     "event_type", "source_record_id"],
    },
    "generic": {"required": [], "optional": []},
}

TYPE_HINTS = {
    "cdr": ["calling_number", "caller", "receiving_number", "receiver", "call_start", "call_time", "cdr"],
    "financial": ["sender_account", "receiver_account", "amount", "transaction", "ledger", "payment"],
    "vehicle": ["vehicle_registration", "registration", "plate", "owner_name", "vehicle"],
    "location_event": ["event_time", "latitude", "longitude", "location", "event"],
}


def _norm_header(h: str) -> str:
    return "".join(c for c in h.strip().lower().replace(" ", "_") if c.isalnum() or c == "_")


def sniff_dataset_type(headers: List[str]) -> str:
    normed = [_norm_header(h) for h in headers]
    scores = {}
    for dtype, hints in TYPE_HINTS.items():
        scores[dtype] = sum(1 for h in normed for hint in hints if hint in h)
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "generic"


def read_table_rows(stored_path: Path, ext: str, cap: int) -> Tuple[List[str], List[Dict[str, str]], bool]:
    """Returns (headers, row dicts, truncated). Raises FileRejected on garbage."""
    from app.core.evidence_files import FileRejected, read_csv_preview, read_xlsx_preview

    if ext == ".csv":
        headers, preview = read_csv_preview(stored_path.read_bytes(), max_rows=cap + 1)
        rows = [dict(zip(headers, r + [""] * (len(headers) - len(r)))) for r in preview]
        return headers, rows, len(rows) > cap
    if ext == ".xlsx":
        headers, preview = read_xlsx_preview(stored_path.read_bytes(), max_rows=cap + 1)
        rows = [dict(zip(headers, r + [""] * (len(headers) - len(r)))) for r in preview]
        return headers, rows, len(rows) > cap
    if ext == ".json":
        try:
            payload = json.loads(stored_path.read_text(encoding="utf-8"))
        except Exception:
            try:
                payload = json.loads(stored_path.read_text(encoding="latin-1"))
            except Exception as ex:
                raise FileRejected(f"Invalid JSON: {ex}")
        items = payload if isinstance(payload, list) else payload.get("records", payload.get("rows", []))
        if not isinstance(items, list) or not items:
            return [], [], False
        headers = sorted({k for it in items[:50] if isinstance(it, dict) for k in it})
        rows = [{h: ("" if it.get(h) is None else str(it.get(h))) for h in headers}
                for it in items[:cap + 1] if isinstance(it, dict)]
        return headers, rows, len(rows) > cap
    raise FileRejected(f"Extension '{ext}' is not a structured dataset (use csv, xlsx, or json).")


def _looks_number(v: str) -> bool:
    try:
        float(v.replace(",", "").replace("₹", "").replace("$", "").strip())
        return True
    except (ValueError, AttributeError):
        return False


def _looks_time(v: str) -> bool:
    if not v or not v.strip():
        return False
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y",
                "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            datetime.strptime(v.strip()[:19], fmt)
            return True
        except ValueError:
            continue
    return False


def auto_map(headers: List[str], dataset_type: str) -> Dict[str, Optional[str]]:
    """Map canonical field -> source column (exact normalized match)."""
    norm_to_src = {_norm_header(h): h for h in headers}
    mapping: Dict[str, Optional[str]] = {}
    for field in CANONICAL[dataset_type]["required"] + CANONICAL[dataset_type]["optional"]:
        mapping[field] = norm_to_src.get(field)
    return mapping


def quality_report(headers: List[str], rows: List[Dict[str, str]],
                   dataset_type: str, mapping: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """Data-quality screen payload: required/found/missing/invalid/dupes/ignored."""
    canon = CANONICAL[dataset_type]
    required, optional = canon["required"], canon["optional"]
    found = [f for f in required + optional if mapping.get(f)]
    missing = [f for f in required if not mapping.get(f)]
    invalid: List[Dict[str, Any]] = []
    seen: Dict[tuple, int] = {}
    dupes = 0
    for i, row in enumerate(rows, start=2):  # 1-based incl. header
        key = tuple(sorted((k, v) for k, v in row.items()))
        if key in seen:
            dupes += 1
        else:
            seen[key] = i
        if dataset_type == "financial":
            for col in ("amount",):
                src = mapping.get(col)
                if src and row.get(src, "").strip() and not _looks_number(row[src]):
                    invalid.append({"row": i, "column": src, "issue": "not_numeric",
                                    "value": row[src][:60]})
            for col in ("transaction_time",):
                src = mapping.get(col)
                if src and row.get(src, "").strip() and not _looks_time(row[src]):
                    invalid.append({"row": i, "column": src, "issue": "bad_timestamp",
                                    "value": row[src][:60]})
        if dataset_type == "cdr":
            for col in ("call_start_time", "call_end_time"):
                src = mapping.get(col)
                if src and row.get(src, "").strip() and not _looks_time(row[src]):
                    invalid.append({"row": i, "column": src, "issue": "bad_timestamp",
                                    "value": row[src][:60]})
    empty_required = 0
    for i, row in enumerate(rows, start=2):
        for f in required:
            src = mapping.get(f)
            if src is not None and not row.get(src, "").strip():
                empty_required += 1
                break
    return {"required": required, "optional": optional, "found": found, "missing": missing,
            "invalid": invalid[:50], "invalid_total": len(invalid),
            "duplicate_rows": dupes, "rows_empty_required": empty_required,
            "row_count": len(rows),
            "can_continue": not missing and dataset_type != "generic"}


def stage_structured_upload(store, doc: Dict[str, Any], dataset_type: Optional[str] = None) -> Dict[str, Any]:
    """Parse + sniff + auto-map + quality-issue persistence. Returns screen payload."""
    from app.config import settings as _settings

    stored = Path(_settings.DATA_DIR) / "cases" / doc["case_id"] / "originals" / doc["filename_stored"]
    headers, rows, truncated = read_table_rows(stored, doc["ext"], _settings.MAX_DATASET_ROWS)
    if not headers:
        raise ValueError("No header row found — the file looks empty or headerless.")
    dtype = dataset_type or sniff_dataset_type(headers)
    if dtype not in CANONICAL:
        dtype = "generic"
    mapping = auto_map(headers, dtype)
    quality = quality_report(headers, rows, dtype, mapping)
    quality["truncated"] = truncated
    if truncated:
        quality["ignored"] = f"Rows beyond {settings.MAX_DATASET_ROWS} were ignored."
    imp = store.add_import(case_id=doc["case_id"], document_id=doc["id"], dataset_type=dtype,
                           mapping_json=json.dumps(mapping), total_rows=len(rows),
                           imported_rows=0, rejected_rows=0, status="awaiting_mapping",
                           created_by=doc["uploader_id"])
    for inv in quality["invalid"][:200]:
        store.add_quality_issue(imp["id"], inv["row"], inv["column"], inv["issue"], inv.get("value", ""))
    if quality["duplicate_rows"]:
        store.add_quality_issue(imp["id"], 0, "", "duplicate_rows",
                                f"{quality['duplicate_rows']} exact-duplicate rows detected")
    store.set_doc_status(doc["id"], "awaiting_review")
    return {"import_id": imp["id"], "dataset_type": dtype, "headers": headers,
            "mapping": mapping, "quality": quality,
            "preview": rows[:10],
            "message": ("Map columns and confirm to import." if quality["can_continue"]
                        else "Minimum requirements not met — map the missing columns first.")}


def confirm_import(store, graph, imp: Dict[str, Any], mapping: Dict[str, str],
                   reviewer_id: str) -> Dict[str, Any]:
    """Enforce requirements, import rows into the evidence graph. Returns counts."""
    from app.config import settings as _settings

    dtype = imp["dataset_type"]
    if dtype == "generic":
        raise ValueError("Generic tables cannot be graphed. Upload a CDR, financial, vehicle, or location/event dataset.")
    canon = CANONICAL[dtype]
    missing = [f for f in canon["required"] if not mapping.get(f)]
    if missing:
        raise ValueError(f"Cannot import: required columns missing: {missing}")
    doc = store.get_document(imp["document_id"])
    stored = Path(_settings.DATA_DIR) / "cases" / doc["case_id"] / "originals" / doc["filename_stored"]
    headers, rows, _ = read_table_rows(stored, doc["ext"], _settings.MAX_DATASET_ROWS)
    unknown = [c for c in mapping.values() if c not in headers]
    if unknown:
        raise ValueError(f"Mapped columns not in file: {unknown}")

    if dtype == "cdr":
        made, rejected = _import_cdr(graph, rows, mapping, imp, doc)
    elif dtype == "financial":
        made, rejected = _import_financial(graph, rows, mapping, imp, doc)
    elif dtype == "vehicle":
        made, rejected = _import_vehicle(graph, rows, mapping, imp, doc)
    else:
        made, rejected = _import_location_event(graph, rows, mapping, imp, doc)
    store.update_import(imp["id"], mapping_json=json.dumps(mapping),
                        imported_rows=made, rejected_rows=rejected, status="imported")
    return {"imported": made, "rejected": rejected, "total": len(rows)}


def _prov(imp: Dict[str, Any], doc: Dict[str, Any], record_id: str) -> Dict[str, Any]:
    return {"case_id": doc["case_id"], "source_dataset_id": imp["id"],
            "source_record_id": record_id, "source_document_id": doc["id"],
            "review_status": "analyst-reviewed", "confidence": 1.0,
            "provider": "dataset-import-v1"}


def _import_cdr(graph, rows, mapping, imp, doc) -> Tuple[int, int]:
    from app.core.cdr_parser import CDRParser
    from app.core.cdr_analytics import CDRService

    g = mapping.get
    norm_rows = []
    for i, row in enumerate(rows, start=2):
        # NOTE: keys below are CDRParser.parse_record_dict field names
        # (caller/receiver/timestamp/...), mapped from canonical columns.
        rec = {"caller": row.get(g("calling_number") or "", ""),
               "receiver": row.get(g("receiving_number") or "", ""),
               "timestamp": row.get(g("call_start_time") or "", "")}
        if g("duration_seconds") and row.get(g("duration_seconds")):
            rec["duration_seconds"] = row[g("duration_seconds")]
        if g("call_type") and row.get(g("call_type")):
            rec["call_type"] = row[g("call_type")]
        if g("cell_tower_id") and row.get(g("cell_tower_id")):
            rec["cell_tower"] = row[g("cell_tower_id")]
        src = (g("source_record_id") and row.get(g("source_record_id"))) or f"row-{i}"
        rec["call_id"] = f"{imp['id']}-{src}"
        rec["case_id"] = doc["case_id"]
        norm_rows.append(rec)
    valid, rejected, _, errors = CDRParser.parse_json(
        {"records": norm_rows}, source_name=doc["filename_original"])
    if errors and not valid:
        raise ValueError("All CDR rows failed validation: " + "; ".join(errors[:5]))
    svc = CDRService(graph_engine=graph)
    added, _, _ = svc.ingest_records_into_graph(valid)
    # Tag provenance + case on the created CALL edges.
    for rec in valid:
        eid = f"CALL_{rec.call_id}"
        try:
            edge = next((x for x in graph.get_all_edges() if x.id == eid), None)
            if edge is not None:
                edge.properties.update(_prov(imp, doc, rec.call_id))
                edge.properties["case_id"] = doc["case_id"]
        except Exception:
            continue
    return added, rejected


def _import_financial(graph, rows, mapping, imp, doc) -> Tuple[int, int]:
    from app.core.financial_parser import FinancialParser
    from app.core.financial_analytics import FinancialService

    g = mapping.get
    norm_rows = []
    for i, row in enumerate(rows, start=2):
        src = (g("source_record_id") and row.get(g("source_record_id"))) or f"row-{i}"
        norm_rows.append({
            "sender": row.get(g("sender_account") or "", ""),
            "receiver": row.get(g("receiver_account") or "", ""),
            "amount": row.get(g("amount") or "", ""),
            "timestamp": row.get(g("transaction_time") or "", ""),
            "transaction_id": f"{imp['id']}-{src}",
            "currency": row.get(g("currency") or "", "") or "INR",
            "transaction_type": row.get(g("transaction_type") or "", "") or "TRANSFER",
            "case_id": doc["case_id"],
        })
    valid, rejected, _, errors = FinancialParser.parse_json(
        {"records": norm_rows}, source_name=doc["filename_original"])
    if errors and not valid:
        raise ValueError("All financial rows failed validation: " + "; ".join(errors[:5]))
    svc = FinancialService(graph_engine=graph)
    added, _, _ = svc.ingest_records_into_graph(valid)
    for rec in valid:
        for e in graph.get_all_edges():
            rel = e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)
            if rel != "TRANSFERRED_MONEY":
                continue
            props = e.properties or {}
            if props.get("transaction_id") == rec.transaction_id or (
                    e.source == rec.sender and e.target == rec.receiver
                    and str(props.get("amount", "")) == str(rec.amount)):
                e.properties.update(_prov(imp, doc, rec.transaction_id))
                e.properties["case_id"] = doc["case_id"]
    return added, rejected


def _import_vehicle(graph, rows, mapping, imp, doc) -> Tuple[int, int]:
    from app.models.graph_models import Edge, EdgeType, Node, NodeType

    g = mapping.get
    made = rejected = 0
    for i, row in enumerate(rows, start=2):
        reg = (row.get(g("vehicle_registration") or "", "") or "").strip().upper().replace(" ", "")
        if not reg:
            rejected += 1
            continue
        vid = f"VEH_{reg}"
        if not graph.get_node(vid):
            graph.add_node(Node(vid, NodeType.VEHICLE, reg,
                                {"registration": reg, **_prov(imp, doc, f"row-{i}")}))
        owner = (g("owner_name") and row.get(g("owner_name"), "").strip()) or ""
        user = (g("user_name") and row.get(g("user_name"), "").strip()) or ""
        linked = False
        for name, rel, eid in ((owner, EdgeType.OWNS, f"{imp['id']}-own-{i}"),
                               (user, EdgeType.OPERATES, f"{imp['id']}-use-{i}")):
            if not name:
                continue
            pid = f"PERSON_{name.replace(' ', '_')}"
            if not graph.get_node(pid):
                graph.add_node(Node(pid, NodeType.PERSON, name,
                                    {"case_id": doc["case_id"], "source_dataset_id": imp["id"],
                                     "review_status": "analyst-reviewed"}))
            graph.add_edge(Edge(pid, vid, rel,
                                {"case_id": doc["case_id"], **_prov(imp, doc, f"row-{i}")}, id=eid))
            linked = True
        made += 1 if linked else 0
        rejected += 0 if linked else 1
    case_node(graph, doc)
    return made, rejected


def _import_location_event(graph, rows, mapping, imp, doc) -> Tuple[int, int]:
    from app.models.graph_models import Edge, EdgeType, Node, NodeType

    g = mapping.get
    made = rejected = 0
    for i, row in enumerate(rows, start=2):
        when = (row.get(g("event_time") or "", "") or "").strip()
        if not when:
            rejected += 1
            continue
        loc = (g("location") and row.get(g("location"), "").strip()) or "Unknown location"
        lid = f"LOC_{loc.replace(' ', '_')[:60]}"
        if not graph.get_node(lid):
            graph.add_node(Node(lid, NodeType.LOCATION, loc,
                                {"case_id": doc["case_id"], **_prov(imp, doc, f"row-{i}")}))
        name = ((g("entity_name") and row.get(g("entity_name"), "").strip())
                or (g("entity_id") and row.get(g("entity_id"), "").strip()) or "")
        if name:
            pid = name if graph.get_node(name) else f"PERSON_{name.replace(' ', '_')}"
            if not graph.get_node(pid):
                graph.add_node(Node(pid, NodeType.PERSON, name,
                                    {"case_id": doc["case_id"], "source_dataset_id": imp["id"],
                                     "review_status": "analyst-reviewed"}))
            graph.add_edge(Edge(pid, lid, EdgeType.LOCATED_AT,
                                {"event_time": when, "case_id": doc["case_id"],
                                 **_prov(imp, doc, f"row-{i}")}, id=f"{imp['id']}-loc-{i}"))
        made += 1
    case_node(graph, doc)
    return made, rejected


def case_node(graph, doc) -> None:
    """Ensure a Case node exists for the investigation case and return it."""
    from app.models.graph_models import Node, NodeType

    cid = f"CASE_{doc['case_id']}"
    if not graph.get_node(cid):
        graph.add_node(Node(cid, NodeType.CASE, doc["case_id"],
                            {"case_code": doc["case_id"], "case_id": doc["case_id"],
                             "source": "case-platform"}))
