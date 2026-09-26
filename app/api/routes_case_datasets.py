# -*- coding: utf-8 -*-
"""
Case dataset import API: data-quality screen + column mapping + confirm.

- GET import detail returns the mapping screen payload: canonical schema
  (required/optional), found/missing columns, invalid values, duplicates,
  ignored rows, first-row preview, and whether minimum requirements pass.
- POST confirm enforces minimum requirements, then wires evidence-linked
  graph nodes/edges (case_id + import + record provenance). Confirming IS
  the analyst review for datasets; every created edge carries
  review_status="analyst-reviewed".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.core.audit_logger import audit_logger
from app.core.auth_service import get_current_user
from app.core.case_access import require_case_member, require_case_writer
from app.core.case_store import get_case_store
from app.core.dataset_schemas import (
    CANONICAL,
    auto_map,
    confirm_import,
    quality_report,
    read_table_rows,
)
from app.core.graph_engine import get_graph_engine
from app.models.auth_models import User

router = APIRouter(tags=["case-datasets"])


class ConfirmMapping(BaseModel):
    mapping: Dict[str, Optional[str]] = Field(default_factory=dict)
    dataset_type: Optional[str] = None


def _audit(request: Request, action: str, user: User, case_id: str,
           resource_id: str = "", details: Optional[dict] = None) -> None:
    ip = request.client.host if request.client else None
    d = {"case_id": case_id}
    if details:
        d.update(details)
    audit_logger.log(action=action, user_id=user.user_id, username=user.username,
                     role=user.role, resource_type="dataset", resource_id=resource_id or case_id,
                     ip_address=ip, status="SUCCESS", details=d)


def _get_import(case_id: str, import_id: str) -> dict:
    imp = get_case_store().get_import(import_id)
    if not imp or imp["case_id"] != case_id:
        raise HTTPException(status_code=404, detail="Dataset import not found in this case")
    return imp


@router.get("/cases/{case_id}/imports")
def list_imports(case_id: str, user: User = Depends(get_current_user)):
    require_case_member(case_id, user)
    return {"case_id": case_id, "imports": get_case_store().list_imports(case_id)}


@router.get("/cases/{case_id}/imports/{import_id}")
def import_detail(case_id: str, import_id: str, user: User = Depends(get_current_user)):
    """Data-quality / column-mapping screen payload (recomputed from the preserved original)."""
    require_case_member(case_id, user)
    store = get_case_store()
    imp = _get_import(case_id, import_id)
    doc = store.get_document(imp["document_id"])
    if not doc:
        raise HTTPException(status_code=410, detail="Source file no longer stored")
    stored = Path(settings.DATA_DIR) / "cases" / case_id / "originals" / doc["filename_stored"]
    try:
        headers, rows, truncated = read_table_rows(stored, doc["ext"], settings.MAX_DATASET_ROWS)
    except Exception as ex:
        raise HTTPException(status_code=422, detail=f"Cannot re-read source file: {ex}")
    mapping = json.loads(imp.get("mapping_json") or "{}") or auto_map(headers, imp["dataset_type"])
    quality = quality_report(headers, rows, imp["dataset_type"], mapping)
    quality["truncated"] = truncated
    return {"import": imp, "document": doc,
            "canonical": CANONICAL.get(imp["dataset_type"], CANONICAL["generic"]),
            "headers": headers, "mapping": mapping, "quality": quality,
            "preview": rows[:10],
            "issues": store.list_quality_issues(import_id)}


@router.post("/cases/{case_id}/imports/{import_id}/confirm")
def confirm_mapping(case_id: str, import_id: str, payload: ConfirmMapping,
                    request: Request, user: User = Depends(get_current_user)):
    """Validate minimum requirements and import into the evidence graph."""
    require_case_writer(case_id, user)
    store = get_case_store()
    imp = _get_import(case_id, import_id)
    if imp["status"] == "imported":
        raise HTTPException(status_code=409, detail="Already imported. Delete and re-upload to re-import.")
    dtype = payload.dataset_type or imp["dataset_type"]
    if dtype not in CANONICAL or dtype == "generic":
        raise HTTPException(status_code=422,
                            detail="Choose a concrete dataset type (cdr, financial, vehicle, location_event).")
    if dtype != imp["dataset_type"]:
        doc = store.get_document(imp["document_id"])
        stored = Path(settings.DATA_DIR) / "cases" / case_id / "originals" / doc["filename_stored"]
        headers, _, _ = read_table_rows(stored, doc["ext"], 5)
        base = auto_map(headers, dtype)
        base.update({k: v for k, v in payload.mapping.items() if v})
        mapping = {k: v for k, v in base.items() if v}
        store.update_import(import_id, dataset_type=dtype, mapping_json=json.dumps(mapping))
        imp = store.get_import(import_id)
    else:
        stored_map = json.loads(imp.get("mapping_json") or "{}")
        mapping = {k: v for k, v in stored_map.items() if v}
        mapping.update({k: v for k, v in payload.mapping.items() if v})
    try:
        result = confirm_import(store, get_graph_engine(), imp, mapping, user.user_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    doc = store.get_document(imp["document_id"])
    if doc:
        store.set_doc_status(doc["id"], "approved")
    _audit(request, "DATASET_IMPORTED", user, case_id, resource_id=import_id, details=result)
    return {"status": "imported", "import_id": import_id, **result}
