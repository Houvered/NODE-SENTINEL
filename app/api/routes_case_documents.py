# -*- coding: utf-8 -*-
"""
Case evidence documents API for NODE SENTINEL.

- Upload validates (size/MIME/extension/magic), hashes (SHA-256), scans
  (optional hook), stores privately, detects duplicates per case.
- Processing runs the evidence pipeline into a review queue; extracted
  rows are never auto-merged into the graph.
- Downloads stream the preserved original through an authorized endpoint.
  Raw vault files are never mounted statically.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.config import settings
from app.core.audit_logger import audit_logger
from app.core.auth_service import get_current_user
from app.core.case_access import get_case_or_404, my_case_role, require_case_member, require_case_writer
from app.core.case_store import get_case_store
from app.core.evidence_files import (
    FileRejected,
    run_malware_scan,
    store_original,
    validate_upload,
)
from app.models.auth_models import User

router = APIRouter(tags=["case-documents"])


def _audit(request: Request, action: str, user: User, case_id: str,
           resource_id: str = "", status_: str = "SUCCESS",
           details: Optional[dict] = None) -> None:
    ip = request.client.host if request.client else None
    d = {"case_id": case_id}
    if details:
        d.update(details)
    audit_logger.log(action=action, user_id=user.user_id, username=user.username,
                     role=user.role, resource_type="document", resource_id=resource_id or case_id,
                     ip_address=ip, status=status_, details=d)


def _resolve_doc(document_id: str, user: User) -> tuple[dict, str]:
    """Return (doc, role); 404 unknown doc, 403 non-member (never leak case)."""
    doc = get_case_store().get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")
    get_case_or_404(doc["case_id"])
    role = my_case_role(doc["case_id"], user)
    if role is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied: you are not a member of this case")
    return doc, role


def _need_writer(role: str) -> None:
    from app.core.case_store import WRITE_ROLES
    if role != "admin" and role not in WRITE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied: case role 'viewer' is read-only")


@router.post("/cases/{case_id}/documents", status_code=201)
async def upload_document(case_id: str, request: Request,
                          file: UploadFile = File(...),
                          doc_type: str = Form("report"),
                          user: User = Depends(get_current_user)):
    """Validate, hash, scan, and privately store an evidence file."""
    require_case_writer(case_id, user)
    content = await file.read()
    try:
        meta = validate_upload(file.filename or "", content)
    except FileRejected as e:
        _audit(request, "DOCUMENT_UPLOAD_REJECTED", user, case_id, status_="FAILED",
               details={"filename": file.filename, "reason": str(e)})
        raise HTTPException(status_code=422, detail=str(e))
    store = get_case_store()
    existing = store.find_by_sha(case_id, meta["sha256"])
    if existing:
        _audit(request, "DOCUMENT_UPLOAD_DUPLICATE", user, case_id,
               resource_id=existing["id"], details={"filename": file.filename})
        return {"status": "duplicate", "duplicate_of": existing["id"], "document": existing}
    try:
        stored_path = store_original(case_id, meta["ext"], content)
        run_malware_scan(stored_path)
    except FileRejected as e:
        raise HTTPException(status_code=422, detail=str(e))
    doc = store.add_document(
        case_id=case_id, filename_original=file.filename or "upload",
        filename_stored=stored_path.name, mime=meta["mime"], ext=meta["ext"],
        size_bytes=meta["size_bytes"], sha256=meta["sha256"],
        uploader_id=user.user_id)
    _audit(request, "DOCUMENT_UPLOADED", user, case_id, resource_id=doc["id"],
           details={"filename": file.filename, "sha256": meta["sha256"][:16] + "…",
                    "size_bytes": meta["size_bytes"], "doc_type": doc_type})
    return {"status": "uploaded", "document": doc}


@router.get("/cases/{case_id}/documents")
def list_documents(case_id: str, user: User = Depends(get_current_user)):
    require_case_member(case_id, user)
    return {"case_id": case_id, "documents": get_case_store().list_documents(case_id)}


@router.get("/documents/{document_id}")
def document_meta(document_id: str, user: User = Depends(get_current_user)):
    doc, _ = _resolve_doc(document_id, user)
    return {"document": doc}


@router.get("/documents/{document_id}/download")
def download_document(document_id: str, request: Request, user: User = Depends(get_current_user)):
    doc, _ = _resolve_doc(document_id, user)
    path = Path(settings.DATA_DIR) / "cases" / doc["case_id"] / "originals" / doc["filename_stored"]
    if not path.exists():
        raise HTTPException(status_code=410, detail="Original file no longer stored")
    _audit(request, "DOCUMENT_DOWNLOADED", user, doc["case_id"], resource_id=document_id,
           details={"filename": doc["filename_original"]})
    return FileResponse(path, media_type=doc.get("mime") or "application/octet-stream",
                        filename=doc["filename_original"])


@router.post("/documents/{document_id}/process")
def process_document(document_id: str, request: Request, user: User = Depends(get_current_user)):
    """Run extraction into the review queue (job-tracked, sync worker)."""
    from app.core.evidence_pipeline import run_document_job

    doc, role = _resolve_doc(document_id, user)
    _need_writer(role)
    if doc["status"] not in ("uploaded", "failed"):
        raise HTTPException(status_code=409,
                            detail=f"Document already processed (status={doc['status']}). Delete and re-upload to reprocess.")
    result = run_document_job(get_case_store(), doc)
    _audit(request, "DOCUMENT_PROCESSED", user, doc["case_id"], resource_id=document_id,
           status_="SUCCESS" if result["status"] == "succeeded" else "FAILED",
           details={k: v for k, v in result.items() if k in ("entities", "relationships", "pages", "import_id", "error")})
    return result


@router.get("/cases/{case_id}/jobs/{job_id}")
def job_status(case_id: str, job_id: str, user: User = Depends(get_current_user)):
    require_case_member(case_id, user)
    job = get_case_store().get_job(job_id)
    if not job or job["case_id"] != case_id:
        raise HTTPException(status_code=404, detail="Job not found in this case")
    return {"job": job}


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, request: Request, user: User = Depends(get_current_user)):
    """Delete evidence file + derived extractions (audit-kept, file purged)."""
    doc, role = _resolve_doc(document_id, user)
    _need_writer(role)
    path = Path(settings.DATA_DIR) / "cases" / doc["case_id"] / "originals" / doc["filename_stored"]
    if path.exists():
        path.unlink()
    get_case_store().delete_document(document_id)
    _audit(request, "DOCUMENT_DELETED", user, doc["case_id"], resource_id=document_id,
           details={"filename": doc["filename_original"]})
    return {"status": "deleted", "document_id": document_id}
