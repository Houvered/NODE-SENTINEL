# -*- coding: utf-8 -*-
"""
Case management API for NODE SENTINEL.

All routes enforce case membership (ADMIN bypasses). Every mutation is
audit-logged with the case ID as resource_id for per-case trail queries.
Deletion is retention-guarded: only ADMIN, only archived or past-retention
cases, explicit confirm=true.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.config import settings
from app.core.audit_logger import audit_logger
from app.core.auth_service import auth_service, get_current_user
from app.core.case_access import (
    get_case_or_404,
    my_case_role,
    require_case_manager,
    require_case_member,
)
from app.core.case_store import get_case_store
from app.models.auth_models import Role, User
from app.models.case_models import CaseCreate, CaseMemberAdd, CaseResponse, CaseUpdate, MemberResponse

router = APIRouter(prefix="/cases", tags=["cases"])


def _to_response(case: dict, user: User) -> CaseResponse:
    return CaseResponse(
        id=case["id"], title=case["title"], description=case.get("description") or "",
        status=case["status"], fir_no=case.get("fir_no") or "",
        police_station=case.get("police_station") or "",
        created_by=case["created_by"], created_at=case["created_at"],
        updated_at=case["updated_at"], archived_at=case.get("archived_at"),
        retention_until=case.get("retention_until"),
        my_role=my_case_role(case["id"], user))


def _audit(request: Request, action: str, user: User, case_id: str,
           status_: str = "SUCCESS", details: Optional[dict] = None) -> None:
    ip = request.client.host if request.client else None
    d = {"case_id": case_id}
    if details:
        d.update(details)
    audit_logger.log(action=action, user_id=user.user_id, username=user.username,
                     role=user.role, resource_type="case", resource_id=case_id,
                     ip_address=ip, status=status_, details=d)


@router.post("", response_model=CaseResponse, status_code=201)
def create_case(payload: CaseCreate, request: Request,
                user: User = Depends(get_current_user)):
    """Create a case. Creator becomes case lead. Any authenticated user may create."""
    store = get_case_store()
    case = store.create_case(title=payload.title, description=payload.description,
                             created_by=user.user_id, fir_no=payload.fir_no,
                             police_station=payload.police_station)
    _audit(request, "CASE_CREATED", user, case["id"], details={"title": payload.title})
    return _to_response(case, user)


@router.get("", response_model=List[CaseResponse])
def list_cases(user: User = Depends(get_current_user)):
    """Cases visible to the caller (membership; ADMIN sees all)."""
    store = get_case_store()
    return [_to_response(c, user)
            for c in store.list_cases_for_user(user.user_id, user.role == Role.ADMIN)]


@router.get("/{case_id}", response_model=CaseResponse)
def view_case(case_id: str, request: Request, ctx: dict = Depends(require_case_member),
             user: User = Depends(get_current_user)):
    _audit(request, "CASE_ACCESSED", user, case_id)
    return _to_response(ctx["case"], user)


@router.patch("/{case_id}", response_model=CaseResponse)
def update_case(case_id: str, payload: CaseUpdate, request: Request,
                ctx: dict = Depends(require_case_member),
                user: User = Depends(get_current_user)):
    """Update metadata/status. Viewers are read-only."""
    if ctx["role"] != "admin":
        from app.core.case_store import WRITE_ROLES
        if ctx["role"] not in WRITE_ROLES:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Access denied: case role 'viewer' is read-only")
    store = get_case_store()
    try:
        case = store.update_case(case_id, payload.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    _audit(request, "CASE_UPDATED", user, case_id,
           details={"fields": list(payload.model_dump(exclude_unset=True).keys())})
    return _to_response(case, user)


@router.delete("/{case_id}")
def delete_case(case_id: str, request: Request, confirm: bool = Query(False),
                user: User = Depends(get_current_user)):
    """Retention-guarded deletion: ADMIN only, archived or past-retention, confirm=true."""
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied: only ADMIN may delete cases")
    case = get_case_or_404(case_id)
    if not confirm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Add ?confirm=true to delete. Deleted evidence cannot be recovered.")
    retention_ok = False
    try:
        if case.get("retention_until"):
            retention_ok = datetime.now(timezone.utc).isoformat() >= case["retention_until"]
    except Exception:
        retention_ok = False
    if case.get("status") != "archived" and not retention_ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Retention policy: archive the case (or wait for retention expiry) before deletion")
    get_case_store().delete_case(case_id)
    case_dir = Path(settings.DATA_DIR) / "cases" / case_id
    if case_dir.exists():
        shutil.rmtree(case_dir, ignore_errors=True)
    _audit(request, "CASE_DELETED", user, case_id)
    return {"status": "deleted", "case_id": case_id}


@router.get("/{case_id}/members", response_model=List[MemberResponse])
def list_members(case_id: str, ctx: dict = Depends(require_case_member)):
    store = get_case_store()
    out = []
    for m in store.list_members(case_id):
        u = auth_service.repo.get_by_id(m["user_id"])
        out.append(MemberResponse(case_id=case_id, user_id=m["user_id"],
                                 username=u.username if u else m["user_id"],
                                 case_role=m["case_role"], added_by=m.get("added_by"),
                                 added_at=m["added_at"]))
    return out


@router.post("/{case_id}/members", response_model=MemberResponse, status_code=201)
def add_member(case_id: str, payload: CaseMemberAdd, request: Request,
               ctx: dict = Depends(require_case_manager),
               user: User = Depends(get_current_user)):
    """Case lead / ADMIN adds a user by username with a case role."""
    target = auth_service.repo.get_by_username(payload.username)
    if not target:
        raise HTTPException(status_code=404, detail=f"User '{payload.username}' not found")
    store = get_case_store()
    try:
        m = store.add_member(case_id, target.user_id, payload.case_role, user.user_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    _audit(request, "CASE_MEMBER_ADDED", user, case_id,
           details={"username": target.username, "case_role": payload.case_role})
    return MemberResponse(case_id=case_id, user_id=target.user_id, username=target.username,
                          case_role=m["case_role"], added_by=m.get("added_by"), added_at=m["added_at"])


@router.delete("/{case_id}/members/{user_id}")
def remove_member(case_id: str, user_id: str, request: Request,
                  ctx: dict = Depends(require_case_manager),
                  user: User = Depends(get_current_user)):
    store = get_case_store()
    if not store.get_membership(case_id, user_id):
        raise HTTPException(status_code=404, detail="Membership not found")
    if user_id == ctx["case"].get("created_by") and user_id != user.user_id:
        raise HTTPException(status_code=409, detail="Cannot remove the case creator")
    store.remove_member(case_id, user_id)
    _audit(request, "CASE_MEMBER_REMOVED", user, case_id, details={"user_id": user_id})
    return {"status": "removed", "case_id": case_id, "user_id": user_id}


@router.get("/{case_id}/audit-logs")
def case_audit_logs(case_id: str, page_size: int = Query(100, ge=1, le=500),
                     ctx: dict = Depends(require_case_member)):
    """Per-case activity trail (filtered from the global tamper-evident log)."""
    from app.models.audit_models import AuditQueryFilter

    res = audit_logger.query_logs(AuditQueryFilter(resource_type="case", resource_id=case_id,
                                                   page=1, page_size=page_size))
    return res
