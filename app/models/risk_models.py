# -*- coding: utf-8 -*-
"""
Risk & Anomaly Intelligence Data Models for NODE SENTINEL.
Provides explainable, evidence-backed investigative risk scoring models
integrating Graph, CDR, Financial, Case, Location, and Police Registry feeds.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field, model_validator


class RiskCategory(str, Enum):
    GRAPH = "GRAPH"
    CDR = "CDR"
    FINANCIAL = "FINANCIAL"
    CASE = "CASE"
    LOCATION = "LOCATION"
    BEHAVIORAL = "BEHAVIORAL"
    REGISTRY = "REGISTRY"


class RiskSeverity(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"


class RiskFactor(BaseModel):
    factor_id: str = Field(..., description="Unique identifier for the risk factor")
    category: RiskCategory = Field(..., description="Category of the indicator")
    title: str = Field(..., description="Short descriptive title of the risk factor")
    score_contribution: float = Field(..., description="Reproducible points contributed to composite score")
    severity: str = Field(..., description="Severity level: LOW, MODERATE, ELEVATED, HIGH")
    explanation: str = Field(..., description="Clear contextual narrative explaining why this indicator matters")
    evidence: str = Field(..., description="Concrete, traceable empirical evidence from project data")
    source: str = Field(..., description="Specific analysis module or data registry source")
    entity_id: str = Field(..., description="Subject or related entity identifier")
    timestamp: Optional[str] = Field(None, description="Event or observation timestamp where available")
    confidence: Optional[float] = Field(0.95, description="Confidence metric of indicator observation (0.0 - 1.0)")
    timeline_event_type: Optional[str] = Field(None, description="Linked Timeline event type (e.g. FINANCIAL, CALL, CASE)")
    action_hint: Optional[str] = Field(None, description="Suggested UI investigation action hook")

    # Backward-compatibility fields for existing templates expecting f.factor / f.points
    factor: Optional[str] = Field(None, description="Legacy factor summary string")
    points: Optional[float] = Field(None, description="Legacy points value")

    @model_validator(mode="after")
    def populate_legacy_fields(self) -> RiskFactor:
        if self.factor is None:
            self.factor = f"{self.title}: {self.evidence}"
        if self.points is None:
            self.points = self.score_contribution
        return self


class RiskIntelligenceResult(BaseModel):
    entity_id: str
    entity_name: str
    entity_type: str
    risk_score: float = Field(..., description="Composite risk score bounded between 0.0 and 100.0")
    risk_level: str = Field(..., description="LOW (0-24), MODERATE (25-49), ELEVATED (50-74), HIGH (75-100)")
    overall_score: float = Field(0.0, description="Legacy alias for risk_score")
    severity_level: str = Field("LOW", description="Legacy alias for risk_level")
    factors: List[RiskFactor] = Field(default_factory=list, description="Ordered list of explainable risk factors")
    evidence_summary: List[str] = Field(default_factory=list, description="Bullet summary of key observed facts")
    conclusion: str = Field(
        default="Requires Investigator Verification",
        description="Objective conclusion emphasizing decision-support nature"
    )
    thresholds: Dict[str, str] = Field(
        default_factory=lambda: {
            "LOW": "0–24: Baseline investigative priority. Minimal observed risk indicators.",
            "MODERATE": "25–49: Moderate investigative priority. Multiple indicators present.",
            "ELEVATED": "50–74: Elevated investigative priority based on observed multi-source indicators.",
            "HIGH": "75–100: High investigative priority based on observed indicators. Requires investigator verification."
        }
    )
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    @model_validator(mode="after")
    def sync_legacy_fields(self) -> RiskIntelligenceResult:
        if not self.overall_score:
            self.overall_score = self.risk_score
        if not self.severity_level or self.severity_level == "LOW":
            self.severity_level = self.risk_level
        return self
