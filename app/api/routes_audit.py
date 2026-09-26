"""Audit Log API endpoints for NODE SENTINEL.

Provides investigative audit log querying and filtering.
Accessible only to roles with AUDIT_VIEW permission (ADMIN, VIEWER).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.core.audit_logger import audit_logger
from app.core.auth_service import require_permission
from app.models.audit_models import (
    AuditAction,
    AuditLogResponse,
    AuditQueryFilter,
)
from app.models.auth_models import (
    Permission,
    User,
)

router = APIRouter(prefix="/audit", tags=["Audit Logging"])


@router.get("", response_model=AuditLogResponse)
@router.get("/", response_model=AuditLogResponse, include_in_schema=False)
async def query_audit_logs(
    request: Request,
    action: Optional[str] = Query(None, description="Filter by action type"),
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    username: Optional[str] = Query(None, description="Filter by username"),
    role: Optional[str] = Query(None, description="Filter by role"),
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    resource_id: Optional[str] = Query(None, description="Filter by resource ID"),
    status: Optional[str] = Query(None, description="Filter by status (SUCCESS, DENIED, FAILED)"),
    start_time: Optional[str] = Query(None, description="Filter ISO timestamp start"),
    end_time: Optional[str] = Query(None, description="Filter ISO timestamp end"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    current_user: User = Depends(require_permission(Permission.AUDIT_VIEW)),
) -> AuditLogResponse:
    """Retrieve filtered, paginated audit log entries."""
    query_filter = AuditQueryFilter(
        action=action,
        user_id=user_id,
        username=username,
        role=role,
        resource_type=resource_type,
        resource_id=resource_id,
        status=status,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )

    return audit_logger.query_logs(query_filter)


@router.get("/logs", response_model=AuditLogResponse, include_in_schema=False)
async def query_audit_logs_alias(
    request: Request,
    action: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    resource_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    current_user: User = Depends(require_permission(Permission.AUDIT_VIEW)),
) -> AuditLogResponse:
    """Docs-compatible alias of GET /audit (ARCHITECTURE.md /audit/logs)."""
    return await query_audit_logs(request, action, user_id, username, role, resource_type, resource_id, status, start_time, end_time, page, page_size, current_user)


@router.get("/stats", include_in_schema=False)
async def audit_stats_alias(
    current_user: User = Depends(require_permission(Permission.AUDIT_VIEW)),
):
    """Docs-compatible alias (ARCHITECTURE.md /audit/stats)."""
    recent = audit_logger.get_recent_logs(limit=500)
    by_action: dict = {}
    by_status: dict = {}
    for e in recent:
        by_action[str(e.action)] = by_action.get(str(e.action), 0) + 1
        by_status[str(e.status)] = by_status.get(str(e.status), 0) + 1
    return {"total_recent": len(recent), "by_action": by_action, "by_status": by_status}
