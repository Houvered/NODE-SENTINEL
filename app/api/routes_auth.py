"""Authentication API endpoints for NODE SENTINEL."""

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from app.core.auth_service import (
    auth_service,
    get_current_user,
    get_token_from_request,
)
from app.config import settings
from app.core.audit_logger import audit_logger
from app.models.audit_models import AuditAction
from app.models.auth_models import (
    LoginRequest,
    LoginResponse,
    ROLE_PERMISSIONS,
    Role,
    User,
    UserResponse,
)
from app.models.case_models import RegisterRequest

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(request: Request, payload: RegisterRequest) -> UserResponse:
    """Self-service registration. Disabled unless ALLOW_PUBLIC_REGISTER=true.

    New accounts are created read-only (VIEWER) and inactive-safe: an
    administrator must assign an operational role before case access.
    """
    if not settings.ALLOW_PUBLIC_REGISTER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Public registration is disabled. Ask an administrator for an account.")
    ip = request.client.host if request.client else None
    try:
        user = auth_service.register_viewer(username=payload.username,
                                            password=payload.password,
                                            full_name=payload.full_name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    audit_logger.log(action=AuditAction.USER_CREATED, user_id=user.user_id, username=user.username,
                     role=user.role, resource_type="user", resource_id=user.user_id,
                     ip_address=ip, status="SUCCESS",
                     details={"via": "public-register", "role": Role.VIEWER.value})
    return UserResponse.from_user(user)


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
) -> LoginResponse:
    """Authenticate user with username and password, returning a signed bearer token."""
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    login_res = auth_service.login(payload, ip=ip, user_agent=user_agent)

    # Set secure HTTP-only cookie for optional browser session persistence
    response.set_cookie(
        key="nodesentinel_token",
        value=login_res.access_token,
        max_age=login_res.expires_in,
        httponly=True,
        samesite="lax",
    )

    return login_res


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    token: Optional[str] = Depends(get_token_from_request),
) -> dict:
    """Revoke current access token and clear session."""
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    if token:
        auth_service.logout(token, user=current_user, ip=ip, user_agent=user_agent)

    response.delete_cookie(key="nodesentinel_token")
    return {"status": "success", "message": "Successfully logged out"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Retrieve profile and assigned permissions for the currently authenticated user."""
    permissions = [p.value for p in ROLE_PERMISSIONS.get(current_user.role, set())]
    return UserResponse.from_user(current_user, permissions=permissions)
