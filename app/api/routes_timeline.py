# -*- coding: utf-8 -*-
"""
Timeline API Routes for NODE SENTINEL.
Exposes unified multi-source chronological investigation event timeline for any entity or case.
"""
from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from app.core.timeline_engine import get_timeline_engine
from app.models.timeline_models import TimelineResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("/{entity_id}", response_model=TimelineResponse)
def get_entity_timeline(
    entity_id: str,
    start: Optional[str] = Query(None, description="Start date/time filter (ISO format, e.g. 2024-07-01)"),
    end: Optional[str] = Query(None, description="End date/time filter (ISO format, e.g. 2024-07-31)"),
    event_type: Optional[str] = Query(None, description="Filter by event type: ALL, FINANCIAL, CALL, CASE, LOCATION, VEHICLE, FACE (or comma-separated)"),
    severity: Optional[str] = Query(None, description="Filter by severity: ALL, INFO, NOTICE, ELEVATED, HIGH"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum events to return"),
    sort_order: str = Query("desc", pattern="^(desc|asc)$", description="Sort chronological direction ('desc' for newest first, 'asc' for oldest first)"),
    search: Optional[str] = Query(None, description="Keyword search across title, description, location, or source"),
):
    """
    Retrieve unified chronological investigation timeline for a Person, Account, Phone, Vehicle, Location, or Case.
    Aggregates:
    - Financial transactions & transfers
    - CDR call records & cell tower hits
    - Case & FIR filings / statutory sections
    - Physical surveillance location observations
    - Registered vehicle operations
    - Biometric face registry enrollments
    """
    clean_id = (entity_id or "").strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Entity ID cannot be blank")

    engine = get_timeline_engine()
    try:
        response = engine.get_entity_timeline(
            entity_id=clean_id,
            start=start,
            end=end,
            event_type=event_type,
            severity=severity,
            limit=limit,
            sort_order=sort_order,
            search=search,
        )
        return response
    except Exception as e:
        logger.error(f"Error generating timeline for {clean_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate timeline: {str(e)}")
