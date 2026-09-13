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
