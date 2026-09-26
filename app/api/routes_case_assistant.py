# -*- coding: utf-8 -*-
"""
Case-scoped investigation assistant API.

POST /api/cases/{caseId}/assistant/query runs ONLY on the case's reviewed
evidence (membership enforced). Missing required data yields the structured
missing-data format — never an invented answer. Conversations persist per
case; every query is audit-logged.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.audit_logger import audit_logger
from app.core.auth_service import get_current_user
from app.core.case_access import require_case_member
from app.core.case_assistant import answer_query
from app.core.case_store import get_case_store
from app.core.graph_engine import get_graph_engine
from app.models.auth_models import User

router = APIRouter(tags=["case-assistant"])


class CaseQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None


@router.post("/cases/{case_id}/assistant/query")
def case_query(case_id: str, payload: CaseQuery, request: Request,
               user: User = Depends(get_current_user)):
    require_case_member(case_id, user)
    store = get_case_store()
    session_id = payload.session_id
    if session_id:
        sess = store.get_session(session_id)
        if not sess or sess["case_id"] != case_id:
            raise HTTPException(status_code=404, detail="Chat session not found in this case")
    else:
        sess = store.add_session(case_id, user.user_id)
        session_id = sess["id"]
    result = answer_query(get_graph_engine(), store, case_id, payload.query.strip())
    store.add_message(session_id, "user", payload.query.strip())
    import json as _json
    store.add_message(session_id, "assistant", result["answer"],
                      _json.dumps(result.get("evidence", []), default=str))
    ip = request.client.host if request.client else None
    audit_logger.log(action="AI_QUERY", user_id=user.user_id, username=user.username,
                     role=user.role, resource_type="case", resource_id=case_id,
                     ip_address=ip, status="SUCCESS",
                     details={"case_id": case_id, "query": payload.query.strip()[:300],
                              "evidence_count": len(result.get("evidence", [])),
                              "missing": result.get("missing", [])})
    return {"session_id": session_id, "case_id": case_id, **result}


@router.get("/cases/{case_id}/assistant/sessions")
def list_sessions(case_id: str, user: User = Depends(get_current_user)):
    require_case_member(case_id, user)
    store = get_case_store()
    rows = store.list_sessions(case_id, user.user_id)
    return {"case_id": case_id, "sessions": rows}


@router.get("/cases/{case_id}/assistant/sessions/{session_id}")
def session_history(case_id: str, session_id: str, user: User = Depends(get_current_user)):
    require_case_member(case_id, user)
    store = get_case_store()
    sess = store.get_session(session_id)
    if not sess or sess["case_id"] != case_id:
        raise HTTPException(status_code=404, detail="Chat session not found in this case")
    return {"session_id": session_id, "messages": store.list_messages(session_id)}


@router.get("/cases/{case_id}/readiness")
def case_readiness(case_id: str, user: User = Depends(get_current_user)):
    """Data-readiness panel: what the case has vs what each analysis needs."""
    from app.core.case_assistant import INTENT_REQUIREMENTS, case_readiness, case_scope_ids

    require_case_member(case_id, user)
    store = get_case_store()
    graph = get_graph_engine()
    scope = case_scope_ids(graph, case_id)
    ready = case_readiness(graph, scope, store, case_id)
    gaps = {}
    for intent, req in INTENT_REQUIREMENTS.items():
        missing = [n for n in req.get("needs", []) if not ready["has"].get(n)]
        if missing:
            gaps[intent] = {"missing": missing, "upload": req.get("upload")}
    return {"case_id": case_id, "readiness": ready, "gaps": gaps,
            "requirements": INTENT_REQUIREMENTS}
