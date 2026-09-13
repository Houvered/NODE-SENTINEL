"""User Administration API endpoints for NODE SENTINEL.

Accessible exclusively by users with ADMIN role / USER_MANAGEMENT permission.
All administrative actions are strictly audit-logged.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.audit_logger import audit_logger
from app.core.auth_service import require_permission, user_repo
from app.models.audit_models import AuditAction
from app.models.auth_models import (
    CreateUserRequest,
    Permission,
    ROLE_PERMISSIONS,
    Role,
    UpdateUserRequest,
    User,
    UserResponse,
)

router = APIRouter(prefix="/users", tags=["User Administration"])


@router.get("", response_model=List[UserResponse])
async def list_all_users(
    admin_user: User = Depends(require_permission(Permission.USER_MANAGEMENT)),
) -> List[UserResponse]:
    """List all registered system users (Admin only)."""
    users = user_repo.list_users()
    return [
        UserResponse.from_user(
            u, permissions=[p.value for p in ROLE_PERMISSIONS.get(u.role, set())]
        )
        for u in users
    ]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request,
    payload: CreateUserRequest,
    admin_user: User = Depends(require_permission(Permission.USER_MANAGEMENT)),
) -> UserResponse:
    """Create a new user account (Admin only)."""
    ip = request.client.host if request.client else None
    try:
        new_user = user_repo.create_user(payload)
        audit_logger.log(
            action=AuditAction.USER_CREATE,
            user_id=admin_user.user_id,
            username=admin_user.username,
            role=admin_user.role,
            resource_type="user",
            resource_id=new_user.user_id,
            ip_address=ip,
            status="SUCCESS",
            details={
                "created_username": new_user.username,
                "assigned_role": new_user.role.value,
                "full_name": new_user.full_name,
            },
        )
        perms = [p.value for p in ROLE_PERMISSIONS.get(new_user.role, set())]
        return UserResponse.from_user(new_user, permissions=perms)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{user_id}", response_model=UserResponse)
async def get_user_by_id(
    user_id: str,
    admin_user: User = Depends(require_permission(Permission.USER_MANAGEMENT)),
) -> UserResponse:
    """Retrieve details for a specific user."""
    user = user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User '{user_id}' not found")
    perms = [p.value for p in ROLE_PERMISSIONS.get(user.role, set())]
    return UserResponse.from_user(user, permissions=perms)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    request: Request,
    user_id: str,
    payload: UpdateUserRequest,
    admin_user: User = Depends(require_permission(Permission.USER_MANAGEMENT)),
) -> UserResponse:
    """Update user account attributes, role, or credentials (Admin only)."""
    ip = request.client.host if request.client else None
    existing = user_repo.get_by_id(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User '{user_id}' not found")

    # Safety guard: admin cannot deactivate or demote own account
    if existing.user_id == admin_user.user_id:
        if payload.is_active is False:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate your own administrator account")
        if payload.role and payload.role != Role.ADMIN:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot demote your own administrator account")

    old_role = existing.role
    updated = user_repo.update_user(user_id, payload)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update user")

    # Audit role change or general update
    if payload.role and payload.role != old_role:
        audit_logger.log(
            action=AuditAction.USER_ROLE_CHANGE,
            user_id=admin_user.user_id,
            username=admin_user.username,
            role=admin_user.role,
            resource_type="user",
            resource_id=user_id,
            ip_address=ip,
            status="SUCCESS",
            details={"old_role": old_role.value, "new_role": payload.role.value, "target_username": updated.username},
        )
    else:
        audit_logger.log(
            action=AuditAction.USER_UPDATE,
            user_id=admin_user.user_id,
            username=admin_user.username,
            role=admin_user.role,
            resource_type="user",
            resource_id=user_id,
            ip_address=ip,
            status="SUCCESS",
            details={"target_username": updated.username, "updated_fields": list(payload.model_dump(exclude_unset=True).keys())},
        )

    perms = [p.value for p in ROLE_PERMISSIONS.get(updated.role, set())]
    return UserResponse.from_user(updated, permissions=perms)


@router.delete("/{user_id}")
async def delete_user(
    request: Request,
    user_id: str,
    admin_user: User = Depends(require_permission(Permission.USER_MANAGEMENT)),
) -> dict:
    """Delete a user account (Admin only)."""
    ip = request.client.host if request.client else None
    existing = user_repo.get_by_id(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User '{user_id}' not found")

    if existing.user_id == admin_user.user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own administrator account")

    deleted = user_repo.delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete user")

    audit_logger.log(
        action=AuditAction.USER_DELETE,
        user_id=admin_user.user_id,
        username=admin_user.username,
        role=admin_user.role,
        resource_type="user",
        resource_id=user_id,
        ip_address=ip,
        status="SUCCESS",
        details={"deleted_username": existing.username, "deleted_role": existing.role.value},
    )

    return {"status": "success", "message": f"User '{existing.username}' deleted successfully"}
