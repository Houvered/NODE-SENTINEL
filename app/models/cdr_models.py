# -*- coding: utf-8 -*-
"""
CDR (Call Detail Record) Data Models for NODE SENTINEL.
Standardized schemas for ingestion, call statistics, contact summaries,
and explainable communication indicators.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class CallType(str, Enum):
    INCOMING = "INCOMING"
    OUTGOING = "OUTGOING"
    MISSED = "MISSED"
    UNKNOWN = "UNKNOWN"


class CDRRecord(BaseModel):
    call_id: Optional[str] = Field(None, description="Unique identifier for the call event if provided")
    caller: str = Field(..., description="Normalized caller phone number")
    receiver: str = Field(..., description="Normalized receiver phone number")
    timestamp: datetime = Field(..., description="Date and time of the call")
    duration_seconds: int = Field(0, ge=0, description="Duration of call in integer seconds (0 for missed/unanswered)")
    call_type: CallType = Field(CallType.UNKNOWN, description="Call direction or state")
    cell_tower: Optional[str] = Field(None, description="Cell tower identifier or base station")
    location: Optional[str] = Field(None, description="Descriptive tower location or geographic area")
    case_id: Optional[str] = Field(None, description="Associated investigation case code")
    source_document: Optional[str] = Field(None, description="Name of source CSV, file, or subpoena document")


class CDRIngestResponse(BaseModel):
    status: str = "success"
    records_processed: int = 0
    records_rejected: int = 0
    entities_created: int = 0
    relationships_created: int = 0
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class CallStatistics(BaseModel):
    total_calls: int = 0
    incoming_calls: int = 0
    outgoing_calls: int = 0
    missed_calls: int = 0
    total_duration_seconds: int = 0
    formatted_total_duration: str = "0m 0s"
    avg_duration_seconds: float = 0.0
    longest_call_seconds: int = 0
    unique_contacts: int = 0
    calls_per_contact: Dict[str, int] = Field(default_factory=dict)
    first_call_timestamp: Optional[str] = None
    latest_call_timestamp: Optional[str] = None

    @property
    def total_duration_sec(self) -> int:
        return self.total_duration_seconds

    @property
    def mean_duration_sec(self) -> float:
        return self.avg_duration_seconds


class ContactSummary(BaseModel):
    contact_id: str
    phone_number: str
    contact_name: Optional[str] = None
    call_count: int = 0
    incoming_count: int = 0
    outgoing_count: int = 0
    missed_count: int = 0
    total_duration_seconds: int = 0
    formatted_duration: str = "0m 0s"
    avg_duration_seconds: float = 0.0
    first_interaction: Optional[str] = None
    latest_interaction: Optional[str] = None

    @property
    def name(self) -> Optional[str]:
        return self.contact_name

    @property
    def total_duration_sec(self) -> int:
        return self.total_duration_seconds


class CommunicationBurst(BaseModel):
    start_time: str
    end_time: str
    call_count: int
    unique_contacts: int
    total_duration_seconds: int
    severity: str = "MEDIUM"  # LOW, MEDIUM, HIGH
    indicator: str = "Communication Burst"
    description: str


class CommunicationIndicator(BaseModel):
    name: str
    level: str  # NEUTRAL, NOTICE, ELEVATED
    description: str
    metric_value: Any


class CDRTimelineItem(BaseModel):
    call_id: Optional[str] = None
    timestamp: str
    caller: str
    caller_name: Optional[str] = None
    receiver: str
    receiver_name: Optional[str] = None
    duration_seconds: int
    formatted_duration: str
    call_type: str
    location: Optional[str] = None
    cell_tower: Optional[str] = None
    case_id: Optional[str] = None


class CDRAnalysisResult(BaseModel):
    entity_id: str
    entity_type: str
    entity_name: str
    phone_numbers: List[str]
    statistics: CallStatistics
    top_contacts: List[ContactSummary]
    indicators: List[CommunicationIndicator]
    bursts: List[CommunicationBurst]
    graph_centrality: Dict[str, Any] = Field(default_factory=dict)
    call_timeline_preview: List[CDRTimelineItem] = Field(default_factory=list)
