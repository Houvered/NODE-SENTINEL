# -*- coding: utf-8 -*-
"""
Financial Transaction Data Models for NODE SENTINEL.
Standardized schemas for ingestion, account statistics, counterparty summaries,
money-flow breakdowns, and explainable investigative indicators.
Strictly adheres to neutral decision-support terminology.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class TransactionType(str, Enum):
    TRANSFER = "TRANSFER"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    UNKNOWN = "UNKNOWN"


class FinancialRecord(BaseModel):
    transaction_id: Optional[str] = Field(None, description="Unique identifier for the financial transaction")
    timestamp: datetime = Field(..., description="Timestamp of the transaction")
    sender: str = Field(..., description="Normalized sender account ID or entity ID")
    receiver: str = Field(..., description="Normalized receiver account ID or entity ID")
    amount: float = Field(..., description="Transaction monetary amount")
    currency: str = Field("INR", description="Three-letter currency code, e.g. INR")
    transaction_type: TransactionType = Field(TransactionType.TRANSFER, description="Type of transaction")
    account_id: Optional[str] = Field(None, description="Specific bank account identifier where available")
    case_id: Optional[str] = Field(None, description="Associated investigation case code")
    location: Optional[str] = Field(None, description="Geographic or branch location where available")
    source_document: Optional[str] = Field(None, description="Source statement, subpoena file, or ledger name")
    description: Optional[str] = Field(None, description="Transaction reference or narrative description")


class FinancialIngestResponse(BaseModel):
    status: str = "success"
    records_processed: int = 0
    records_rejected: int = 0
    entities_created: int = 0
    relationships_created: int = 0
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class TransactionStatistics(BaseModel):
    total_transactions: int = 0
    total_outgoing_amount: float = 0.0
    total_incoming_amount: float = 0.0
    net_flow: float = 0.0
    average_transaction_amount: float = 0.0
    largest_transaction: float = 0.0
    smallest_transaction: float = 0.0
    unique_counterparties: int = 0
    transaction_frequency: float = 0.0
    first_transaction: Optional[str] = None
    latest_transaction: Optional[str] = None


class CounterpartySummary(BaseModel):
    counterparty_id: str
    counterparty_name: Optional[str] = None
    counterparty_type: str = "BankAccount"
    transaction_count: int = 0
    total_amount: float = 0.0
    average_amount: float = 0.0
    incoming_amount: float = 0.0
    outgoing_amount: float = 0.0
    first_interaction: Optional[str] = None
    latest_interaction: Optional[str] = None


class FinancialIndicator(BaseModel):
    name: str
    severity: str = "NOTICE"  # NOTICE, ELEVATED, HIGH
    explanation: str
    supporting_values: Dict[str, Any] = Field(default_factory=dict)
    timestamp: Optional[str] = None
    time_window: Optional[str] = None


class FinancialBurst(BaseModel):
    start_time: str
    end_time: str
    transaction_count: int
    total_amount: float
    unique_counterparties: int
    average_amount: float
    severity: str = "ELEVATED"  # NOTICE, ELEVATED, HIGH
    indicator: str = "Rapid Transaction Sequence"
    description: str


class FlowBreakdownItem(BaseModel):
    counterparty_id: str
    counterparty_name: Optional[str] = None
    amount: float = 0.0
    transaction_count: int = 0


class FinancialFlowSummary(BaseModel):
    incoming_count: int = 0
    incoming_total: float = 0.0
    top_sources: List[FlowBreakdownItem] = Field(default_factory=list)
    outgoing_count: int = 0
    outgoing_total: float = 0.0
    top_destinations: List[FlowBreakdownItem] = Field(default_factory=list)
    net_flow: float = 0.0


class TransactionItem(BaseModel):
    transaction_id: Optional[str] = None
    timestamp: str
    sender: str
    sender_name: Optional[str] = None
    receiver: str
    receiver_name: Optional[str] = None
    amount: float
    currency: str = "INR"
    transaction_type: str = "TRANSFER"
    account_id: Optional[str] = None
    case_id: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None


class FinancialAnalysisResult(BaseModel):
    entity_id: str
    entity_type: str
    entity_name: str
    accounts: List[str] = Field(default_factory=list)
    statistics: TransactionStatistics
    top_counterparties: List[CounterpartySummary] = Field(default_factory=list)
    indicators: List[FinancialIndicator] = Field(default_factory=list)
    bursts: List[FinancialBurst] = Field(default_factory=list)
    flow: FinancialFlowSummary
    graph_centrality: Dict[str, Any] = Field(default_factory=dict)
    transactions_preview: List[TransactionItem] = Field(default_factory=list)
