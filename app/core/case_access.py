# -*- coding: utf-8 -*-
"""
Case access-control helpers for NODE SENTINEL.

- ADMIN bypasses membership everywhere (still audit-logged by callers).
- Viewers (case_role == 'viewer') may read but not write.
- Member management requires case lead or ADMIN.
- Never trust a frontend-supplied case ID without a membership check:
  use require_case_member / require_case_writer.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.core.auth_service import get_current_user
from app.core.case_store import MANAGE_ROLES, WRITE_ROLES, get_case_store
from app.models.auth_models import Role, User


def get_case_or_404(case_id: str) -> dict:
    case = get_case_store().get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
    return case


def my_case_role(case_id: str, user: User) -> str | None:
    """ADMIN returns 'admin'; members return their case_role; else None."""
    if user.role == Role.ADMIN:
        return "admin"
    m = get_case_store().get_membership(case_id, user.user_id)
    return m["case_role"] if m else None


def require_case_member(case_id: str, user: User = Depends(get_current_user)) -> dict:
    """Any member (or ADMIN). Returns {'case': ..., 'role': ...}."""
    case = get_case_or_404(case_id)
    role = my_case_role(case_id, user)
    if role is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied: you are not a member of this case")
    return {"case": case, "role": role}


def require_case_writer(case_id: str, user: User = Depends(get_current_user)) -> dict:
    """Members with write roles, or ADMIN. Viewers get 403."""
    ctx = require_case_member(case_id, user)
    if ctx["role"] != "admin" and ctx["role"] not in WRITE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied: case role 'viewer' is read-only")
    return ctx


def require_case_manager(case_id: str, user: User = Depends(get_current_user)) -> dict:
    """Case leads (or ADMIN) for member management."""
    ctx = require_case_member(case_id, user)
    if ctx["role"] != "admin" and ctx["role"] not in MANAGE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied: only the case lead (or admin) manages members")
    return ctx
