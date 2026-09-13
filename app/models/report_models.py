# -*- coding: utf-8 -*-
"""
NODE SENTINEL - Report Generation Models (STEP 14)
Data models for automated investigation reports covering all 13 standard sections,
multi-format export specifications, and structured evidence references.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ReportFormat(str, Enum):
    PDF = "pdf"
    HTML = "html"
    JSON = "json"


class ReportSection(str, Enum):
    METADATA = "metadata"
    EXECUTIVE_SUMMARY = "executive_summary"
    SUBJECT_PROFILE = "subject_profile"
    ASSOCIATED_ENTITIES = "associated_entities"
    GRAPH_SUMMARY = "graph_summary"
    CDR_ANALYSIS = "cdr_analysis"
    FINANCIAL_ANALYSIS = "financial_analysis"
    TIMELINE = "timeline"
    RISK_ASSESSMENT = "risk_assessment"
    CONNECTED_CASES = "connected_cases"
    EVIDENCE_REFERENCES = "evidence_references"
    INVESTIGATOR_NOTES = "investigator_notes"
    DISCLAIMER = "disclaimer"


class EvidenceReferenceItem(BaseModel):
    """Specific verifiable evidence citation mapping back to primary telemetry/sources."""
    source: str = Field(..., description="File, registry, or endpoint source (e.g. demo_financial.csv)")
    entity_id: Optional[str] = Field(None, description="Entity node or identifier")
    timestamp: Optional[str] = Field(None, description="Timestamp of record if applicable")
    case_id: Optional[str] = Field(None, description="Associated FIR / Case ID if applicable")
    record_id: Optional[str] = Field(None, description="Transaction ID, Call ID, or FIR registration number")
    description: str = Field(..., description="Brief summary of the verifiable record")


class ReportMetadata(BaseModel):
    """Section 1: Investigation Report Metadata."""
    report_id: str
    generated_at: str
    investigator_agency: str = "NODE SENTINEL Intelligence Unit"
    classification: str = "LAW ENFORCEMENT SENSITIVE // RESTRICTED INVESTIGATIVE MATERIAL"
    target_type: str = "entity"  # "entity" or "case"
    target_id: str
    target_name: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    sections_included: List[str] = Field(default_factory=list)


class SubjectProfile(BaseModel):
    """Section 3: Subject Profile."""
    entity_id: str
    name: str
    node_type: str
    risk_score: float = 0.0
    risk_level: str = "LOW"
    aliases: List[str] = Field(default_factory=list)
    phone_numbers: List[str] = Field(default_factory=list)
    email_addresses: List[str] = Field(default_factory=list)
    registered_addresses: List[str] = Field(default_factory=list)
    national_id: Optional[str] = None
    vehicle_plates: List[str] = Field(default_factory=list)
    bank_accounts: List[str] = Field(default_factory=list)
    status: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    attributes: Dict[str, Any] = Field(default_factory=dict)


class AssociatedEntityItem(BaseModel):
    """Section 4: Associated Entities."""
    node_id: str
    label: str
    node_type: str
    relationship: str
    weight: float = 1.0
    frequency: Optional[int] = None
    amount: Optional[float] = None
    shared_cases: List[str] = Field(default_factory=list)


class GraphSummary(BaseModel):
    """Section 5: Knowledge Graph Summary."""
    node_count: int = 0
    edge_count: int = 0
    density: float = 0.0
    degree_centrality: float = 0.0
    betweenness_centrality: float = 0.0
    community_id: Optional[str] = None
    ego_nodes_count: int = 0
    ego_edges_count: int = 0
    graph_image_base64: Optional[str] = None


class CDRAnalysisSummary(BaseModel):
    """Section 6: CDR / Communication Analysis."""
    target_phone: Optional[str] = None
    total_calls: int = 0
    total_duration_sec: int = 0
    formatted_duration: str = "0s"
    incoming_count: int = 0
    outgoing_count: int = 0
    top_contacts: List[Dict[str, Any]] = Field(default_factory=list)
    burst_alerts: List[Dict[str, Any]] = Field(default_factory=list)
    off_hours_calls_count: int = 0
    source: str = "demo_cdr.csv"


class FinancialAnalysisSummary(BaseModel):
    """Section 7: Financial Analysis."""
    target_account: Optional[str] = None
    total_transactions: int = 0
    total_inflow: float = 0.0
    total_outflow: float = 0.0
    net_flow: float = 0.0
    top_counterparties: List[Dict[str, Any]] = Field(default_factory=list)
    high_value_transactions: List[Dict[str, Any]] = Field(default_factory=list)
    velocity_alerts: List[Dict[str, Any]] = Field(default_factory=list)
    source: str = "demo_financial.csv"


class TimelineEventItem(BaseModel):
    """Section 8: Investigation Timeline Item."""
    event_id: str
    timestamp: str
    event_type: str
    title: str
    description: str
    severity: str = "info"
    source: Optional[str] = None
    record_id: Optional[str] = None
    case_id: Optional[str] = None


class RiskAssessmentSummary(BaseModel):
    """Section 9: Risk & Anomaly Assessment."""
    score: int = 0
    rating: str = "LOW"
    conclusion: str = "Requires Routine Monitoring"
    factors: List[Dict[str, Any]] = Field(default_factory=list)
    indicators: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    recommended_action: str = "Routine periodic review"


class ConnectedCaseItem(BaseModel):
    """Section 10: Connected Cases."""
    case_id: str
    title: str
    crime_category: Optional[str] = None
    statutes: List[str] = Field(default_factory=list)
    registration_date: Optional[str] = None
    status: Optional[str] = None
    role: Optional[str] = None
    co_accused: List[str] = Field(default_factory=list)


class InvestigationReportRequest(BaseModel):
    """Incoming request to generate an investigation report."""
    entity_id: Optional[str] = Field(None, description="Target entity ID to generate report for")
    case_id: Optional[str] = Field(None, description="Target case ID if generating case-centric report")
    date_from: Optional[str] = Field(None, description="Optional start ISO date YYYY-MM-DD")
    date_to: Optional[str] = Field(None, description="Optional end ISO date YYYY-MM-DD")
    sections: Optional[List[str]] = Field(None, description="List of sections to include, or None for all")
    output_format: ReportFormat = Field(ReportFormat.PDF, description="pdf, html, or json")
    investigator_notes: Optional[str] = Field(None, description="Investigator field notes or remarks")


class InvestigationReportData(BaseModel):
    """Structured data payload containing all generated sections."""
    metadata: ReportMetadata
    executive_summary: Optional[str] = None
    executive_summary_type: str = "deterministic"  # "ai_grounded" or "deterministic"
    subject_profile: Optional[SubjectProfile] = None
    associated_entities: List[AssociatedEntityItem] = Field(default_factory=list)
    graph_summary: Optional[GraphSummary] = None
    cdr_analysis: Optional[CDRAnalysisSummary] = None
    financial_analysis: Optional[FinancialAnalysisSummary] = None
    timeline: List[TimelineEventItem] = Field(default_factory=list)
    risk_assessment: Optional[RiskAssessmentSummary] = None
    connected_cases: List[ConnectedCaseItem] = Field(default_factory=list)
    evidence_references: List[EvidenceReferenceItem] = Field(default_factory=list)
    investigator_notes: Optional[str] = None
    disclaimer: str = (
        "NOTICE: This document is an automated investigative decision-support artifact "
        "generated by NODE SENTINEL. All scores, anomaly flags, and relationship linkages "
        "are analytical indicators intended solely to assist human investigators. They do "
        "not constitute a legal finding of fact or declaration of criminal guilt. All information "
        "is subject to independent verification and formal judicial procedures."
    )


class InvestigationReportResponse(BaseModel):
    """Response returned by the investigation report API."""
    report_id: str
    generated_at: str
    target_id: str
    target_name: str
    target_type: str
    output_format: str
    sections_included: List[str]
    download_url: Optional[str] = None
    data: Optional[InvestigationReportData] = None
    html_content: Optional[str] = None
    pdf_base64: Optional[str] = None
