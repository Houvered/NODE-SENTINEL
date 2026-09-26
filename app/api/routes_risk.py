# -*- coding: utf-8 -*-
"""
Risk Intelligence API Routes for NODE SENTINEL.
Exposes explainable risk factor breakdown, evidence traceability,
and investigative risk scores for any entity.
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException

from app.core.anomaly_detector import AnomalyDetector
from app.core.graph_engine import get_graph_engine
from app.core.risk_intelligence import RiskIntelligenceEngine
from app.models.risk_models import RiskIntelligenceResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/summary")
def risk_summary(top_k: int = 20):
    """Ranked riskiest Person entities (powers dashboard + docs /risk/summary)."""
    try:
        engine = RiskIntelligenceEngine(get_graph_engine())
        items = engine.summarize(top_k=max(1, min(top_k, 100)))
        return {"count": len(items), "items": items}
    except Exception as e:
        logger.error(f"Error building risk summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to build risk summary: {str(e)}")


@router.get("/evaluate/{entity_id}", response_model=RiskIntelligenceResult)
def evaluate_entity_risk_alias(entity_id: str):
    """Docs-compatible alias of GET /risk/{entity_id} (ARCHITECTURE.md)."""
    return get_entity_risk(entity_id)


@router.get("/{entity_id}", response_model=RiskIntelligenceResult)
def get_entity_risk(entity_id: str):
    """
    Retrieve comprehensive, explainable investigative risk assessment for an entity.
    Returns:
    - risk_score (0 - 100)
    - risk_level (LOW, MODERATE, ELEVATED, HIGH)
    - factors (structured factor list with source, evidence, points, explanation)
    - evidence_summary (traceable empirical observations)
    - conclusion ('Requires Investigator Verification')
    - thresholds (transparent score level boundaries)
    """
    clean_id = (entity_id or "").strip()
    if not clean_id:
        raise HTTPException(status_code=400, detail="Entity ID cannot be blank")

    try:
        detector = AnomalyDetector(get_graph_engine())
        result = detector.calculate_investigative_risk_score(clean_id)
        return result
    except Exception as e:
        logger.error(f"Error evaluating risk intelligence for {clean_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to evaluate risk intelligence: {str(e)}")
