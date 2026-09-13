# -*- coding: utf-8 -*-
"""
NODE SENTINEL - AI Investigation Assistant API Routes (STEP 13)
Provides REST endpoints for investigator queries, session context,
suggested inquiries, and conversation resets.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status

from app.core.assistant_engine import AssistantEngine, get_assistant_engine
from app.core.audit_logger import audit_logger
from app.models.assistant_models import (
    InvestigationAssistantRequest,
    InvestigationAssistantResponse,
)
from app.models.audit_models import AuditAction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/query", response_model=InvestigationAssistantResponse)
def ask_assistant(req: InvestigationAssistantRequest) -> InvestigationAssistantResponse:
    """
    Process an investigative inquiry using grounded multi-source intelligence.
    Guarantees:
    - Zero hallucination (strictly relies on indexed telemetry)
    - Explainable evidence traceability
    - Multi-turn follow-up support
    """
    if not req.query or not req.query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query cannot be empty or whitespace.",
        )

    engine = get_assistant_engine()
    res = engine.process_query(req)
    audit_logger.log(
        action=AuditAction.AI_QUERY,
        resource_type="ENTITY",
        resource_id=req.selected_entity_id,
        status="SUCCESS",
        details={
            "query": req.query,
            "target_entity": (res.selected_entity or {}).get("name") or req.selected_entity_id,
            "evidence_count": len(res.evidence),
        },
    )
    return res


@router.get("/context/{conversation_id}")
def get_conversation_context(conversation_id: str) -> Dict[str, Any]:
    """Retrieve active session state and selected entity context."""
    engine = get_assistant_engine()
    cid, session = engine.get_or_create_conversation(conversation_id)
    return {
        "conversation_id": cid,
        "selected_entity_id": session.get("selected_entity_id"),
        "selected_entity_name": session.get("selected_entity_name"),
        "history_length": len(session.get("history", [])),
    }


@router.post("/reset")
def reset_assistant_conversation(conversation_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Clear conversation history and selected entity context."""
    engine = get_assistant_engine()
    if conversation_id:
        success = engine.reset_conversation(conversation_id)
        return {"status": "ok", "conversation_id": conversation_id, "cleared": success}
    return {"status": "ok", "message": "Specify conversation_id to reset a specific session."}


@router.get("/suggestions")
def get_suggested_queries(entity_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Return suggested queries tailored to current investigation context."""
    global_suggestions = [
        "Summarize this investigation.",
        "Show unusual call activity.",
        "Show unusual financial activity.",
        "Find the shortest connection between Tariq Ahmad and DL01AB9988.",
    ]
    entity_suggestions = [
        "Show key connections",
        "Analyze calls",
        "Analyze finances",
        "Why is risk elevated?",
        "Which cases are connected to this person?",
        "Summarize timeline",
    ]
    return {
        "entity_id": entity_id,
        "suggested_queries": entity_suggestions if entity_id else global_suggestions,
        "all_suggestions": global_suggestions + entity_suggestions,
    }
