# -*- coding: utf-8 -*-
"""
Case-scoped report generation.

- Subject is either a case-scope entity or the case itself; out-of-case
  subjects are rejected (no cross-case reports).
- The engine's investigator-notes section carries the case data-readiness
  and missing-data summary plus analyst notes, so every download states
  its evidence limits in neutral language.
- Files persist in the private case vault; downloads are authorized and
  audit-logged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.core.audit_logger import audit_logger
from app.core.auth_service import get_current_user
from app.core.case_access import require_case_member, require_case_writer
from app.core.case_assistant import INTENT_REQUIREMENTS, case_readiness, case_scope_ids
from app.core.case_store import get_case_store
from app.core.graph_engine import get_graph_engine
from app.core.report_engine import get_report_engine
from app.models.auth_models import Permission, ROLE_PERMISSIONS, User
from app.models.report_models import InvestigationReportRequest, ReportFormat

router = APIRouter(tags=["case-reports"])


class CaseReportRequest(BaseModel):
    entity_id: Optional[str] = Field(None, description="Case-scope subject; omit for whole-case report")
    output_format: ReportFormat = Field(ReportFormat.PDF)
    notes: Optional[str] = Field(None, description="Analyst notes appended to the report")


def _need_report_perm(user: User) -> None:
    if Permission.GENERATE_REPORT not in ROLE_PERMISSIONS.get(user.role, []):
        raise HTTPException(status_code=403, detail="Access denied: role cannot generate reports")


def _readiness_notes(case_id: str) -> str:
    graph = get_graph_engine()
    scope = case_scope_ids(graph, case_id)
    ready = case_readiness(graph, scope, get_case_store(), case_id)
    gaps = []
    for intent, req in INTENT_REQUIREMENTS.items():
        missing = [n for n in req.get("needs", []) if not ready["has"].get(n)]
        if missing:
            up = req.get("upload")
            hint = f" (upload {up['file']}: {', '.join(up['columns'])})" if up else ""
            gaps.append(f"{intent}: missing {', '.join(missing)}{hint}")
    lines = [f"Case evidence: {ready['nodes']} entities, {ready['edges']} reviewed relationships, "
             f"{ready['persons']} people, {ready['documents']} documents "
             f"({ready['awaiting_review']} awaiting review)."]
    lines.append("Missing data: " + ("; ".join(gaps) if gaps else "none identified."))
    lines.append("Limitations: findings below derive only from analyst-reviewed or verified case "
                 "evidence with cited sources; unreviewed extractions are excluded; associations "
                 "require investigator interpretation and do not independently establish criminal "
                 "responsibility.")
    return "\n".join(lines)


@router.post("/cases/{case_id}/reports", status_code=201)
def generate_case_report(case_id: str, payload: CaseReportRequest, request: Request,
                         user: User = Depends(get_current_user)):
    require_case_writer(case_id, user)
    _need_report_perm(user)
    store = get_case_store()
    case = store.get_case(case_id)
    graph = get_graph_engine()
    scope = case_scope_ids(graph, case_id)
    entity_id = None
    if payload.entity_id:
        if payload.entity_id not in scope:
            raise HTTPException(status_code=404,
                                detail="Subject not found in this case's reviewed evidence")
        entity_id = payload.entity_id
    notes = _readiness_notes(case_id)
    if payload.notes and payload.notes.strip():
        notes += "\nAnalyst notes: " + payload.notes.strip()[:2000]
    engine = get_report_engine()
    try:
        resp = engine.generate_report(InvestigationReportRequest(
            entity_id=entity_id, case_id=None if entity_id else case_id,
            output_format=payload.output_format, investigator_notes=notes))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # Persist bytes in the private vault.
    vault = Path(settings.DATA_DIR) / "cases" / case_id / "reports"
    vault.mkdir(parents=True, exist_ok=True)
    fmt = (resp.output_format or payload.output_format.value).lower()
    fname = f"{resp.report_id}.{fmt}"
    cached = engine.get_report(resp.report_id) or {}
    data = cached.get("data")
    if fmt == "pdf" and data is not None:
        (vault / fname).write_bytes(engine.export_pdf(data, graph_image_bytes=cached.get("graph_png_bytes")))
    elif fmt == "html" and data is not None:
        (vault / fname).write_text(engine.export_html(data), encoding="utf-8")
    elif data is not None:
        (vault / fname).write_text(data.model_dump_json(indent=2), encoding="utf-8")
    rec = store.add_report(case_id=case_id, subject=resp.target_name, format=fmt,
                           file_name=fname, created_by=user.user_id,
                           meta_json=f'{{"engine_report_id": "{resp.report_id}", "case_title": "{(case or {}).get("title", "")}"}}')
    ip = request.client.host if request.client else None
    audit_logger.log(action="GENERATE_REPORT", user_id=user.user_id, username=user.username,
                     role=user.role, resource_type="report", resource_id=rec["id"],
                     ip_address=ip, status="SUCCESS",
                     details={"case_id": case_id, "subject": resp.target_name, "format": fmt})
    return {"report": rec, "engine_report_id": resp.report_id,
            "sections_included": resp.sections_included}


@router.get("/cases/{case_id}/reports")
def list_case_reports(case_id: str, user: User = Depends(get_current_user)):
    require_case_member(case_id, user)
    return {"case_id": case_id, "reports": get_case_store().list_reports(case_id)}


@router.get("/cases/{case_id}/reports/{report_id}/download")
def download_case_report(case_id: str, report_id: str, request: Request,
                         user: User = Depends(get_current_user)):
    require_case_member(case_id, user)
    rec = get_case_store().get_report(report_id)
    if not rec or rec["case_id"] != case_id:
        raise HTTPException(status_code=404, detail="Report not found in this case")
    path = Path(settings.DATA_DIR) / "cases" / case_id / "reports" / (rec["file_name"] or "")
    if not rec.get("file_name") or not path.exists():
        raise HTTPException(status_code=410, detail="Report file no longer stored")
    media = {"pdf": "application/pdf", "html": "text/html", "json": "application/json"}.get(rec["format"], "application/octet-stream")
    ip = request.client.host if request.client else None
    audit_logger.log(action="REPORT_DOWNLOADED", user_id=user.user_id, username=user.username,
                     role=user.role, resource_type="report", resource_id=report_id,
                     ip_address=ip, status="SUCCESS", details={"case_id": case_id})
    return FileResponse(path, media_type=media, filename=f"case-{case_id}-{rec['file_name']}")
