# -*- coding: utf-8 -*-
"""
Investigation Timeline Data Models for NODE SENTINEL.
Standardized schemas for chronological multi-source timeline events,
including Case/FIR, CDR, Financial, Location, Vehicle, Face, and Graph events.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class TimelineEventType(str, Enum):
    FINANCIAL = "FINANCIAL"
    CALL = "CALL"
    CASE = "CASE"
    LOCATION = "LOCATION"
    VEHICLE = "VEHICLE"
    FACE = "FACE"
    GRAPH = "GRAPH"
    DOCUMENT = "DOCUMENT"


class TimelineSeverity(str, Enum):
    INFO = "INFO"
    NOTICE = "NOTICE"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"


class TimelineEvent(BaseModel):
    event_id: str = Field(..., description="Unique event identifier")
    timestamp: datetime = Field(..., description="Date and time when the event occurred")
    event_type: TimelineEventType = Field(..., description="Category of the event")
    title: str = Field(..., description="Short summary title of the event")
    description: str = Field(..., description="Detailed objective description of the event")
    source: Optional[str] = Field(None, description="Data source name or document reference")
    entity_ids: List[str] = Field(default_factory=list, description="All associated entity IDs")
    related_case_ids: List[str] = Field(default_factory=list, description="Associated case or FIR codes")
    location: Optional[str] = Field(None, description="Physical location or cell tower where available")
    severity: TimelineSeverity = Field(TimelineSeverity.INFO, description="Investigative prominence severity")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional context and metrics")


class TimelineResponse(BaseModel):
    entity_id: str
    entity_name: str
    entity_type: str
    events: List[TimelineEvent] = Field(default_factory=list)
    total_events: int = 0
    date_range: Dict[str, Optional[str]] = Field(default_factory=dict)
    event_type_counts: Dict[str, int] = Field(default_factory=dict)
    severity_counts: Dict[str, int] = Field(default_factory=dict)
    high_priority_count: int = 0
