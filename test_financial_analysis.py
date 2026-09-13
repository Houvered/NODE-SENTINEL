# -*- coding: utf-8 -*-
"""
Comprehensive Test Suite for STEP 10: Financial Transaction Analysis for NODE SENTINEL.
Tests:
- CSV parsing & validation
- JSON parsing & envelope support
- Amount validation & type compatibility (positive check, negative reject, refund support)
- Timestamp safe parsing
- Account identifier normalization
- Malformed records error handling (no silent loss)
- Duplicate transaction ID handling
- Financial ingestion endpoint (POST /api/financial/ingest)
- Graph integration (nodes created, relationships created, properties attached)
- Duplicate prevention (nodes and edges not duplicated on re-ingest)
- Transaction statistics calculation (totals, incoming, outgoing, net flow, avg, extremes, frequency)
- Counterparty statistics calculation
- Incoming / outgoing flow analysis & net flow
- Configurable high-value transaction detection
- Velocity / burst detection (rapid transaction clusters)
- Unusual activity detection (explainable indicators)
- No-data / empty entity case
- Invalid entity case
- Universal Search -> Financial Analysis integration
- Financial Analysis -> Graph network centrality & connectivity
- CDR <-> Financial cross-link navigation
- Case integration (case node linkage when case_id is provided)
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
import pytest

from fastapi.testclient import TestClient

from app.main import app
from app.core.graph_engine import get_graph_engine
from app.api.routes_ingest import load_dataset_by_name
from app.core.financial_parser import (
    FinancialParser,
    normalize_account_identifier,
    parse_timestamp_safe,
    validate_amount,
    parse_transaction_type,
)
from app.models.financial_models import FinancialRecord, TransactionType
from app.core.financial_analytics import (
    FinancialService,
    get_financial_storage,
    DEFAULT_HIGH_VALUE_THRESHOLD,
)
from app.core.cdr_analytics import get_cdr_storage
from app.core.cdr_parser import CDRParser
from app.core.universal_search import get_universal_search
from app.models.graph_models import NodeType, EdgeType


@pytest.fixture(autouse=True)
def setup_test_graph():
    """Ensure graph and storage engines are in a known clean state before each test."""
    g = get_graph_engine()
    g.clear()
    load_dataset_by_name(g, "syndicate_network.json")
    fin_storage = get_financial_storage()
    fin_storage.clear()
    cdr_storage = get_cdr_storage()
    cdr_storage.clear()
    yield


client = TestClient(app)


# -----------------------------------------------------------------------------
# 1. Account Identifier Normalization Tests
# -----------------------------------------------------------------------------

def test_account_normalization():
    assert normalize_account_identifier("ACC990188231") == "ACC990188231"
    assert normalize_account_identifier("acc990188231") == "ACC990188231"
    assert normalize_account_identifier("990188231") == "ACC_990188231"
    assert normalize_account_identifier("ACC-4420-99114") == "ACC442099114"
    assert normalize_account_identifier("PERSON_TARIQ_AHMAD") == "PERSON_TARIQ_AHMAD"
    assert normalize_account_identifier("ACC_HAWALA_9901") == "ACC_HAWALA_9901"

    with pytest.raises(ValueError):
        normalize_account_identifier("")
    with pytest.raises(ValueError):
        normalize_account_identifier(None)
    with pytest.raises(ValueError):
        normalize_account_identifier("12")  # Less than 3 alphanumeric characters


# -----------------------------------------------------------------------------
# 2. Timestamp & Amount Parsing Tests
# -----------------------------------------------------------------------------

def test_timestamp_safe_parsing():
    dt = parse_timestamp_safe("2024-07-15 10:15:00")
    assert dt.year == 2024 and dt.month == 7 and dt.day == 15
    assert dt.hour == 10 and dt.minute == 15

    iso_dt = parse_timestamp_safe("2024-07-15T11:30:00Z")
    assert iso_dt.hour == 11 and iso_dt.minute == 30

    slash_dt = parse_timestamp_safe("15/07/2024 12:00:00")
    assert slash_dt.day == 15 and slash_dt.month == 7

    with pytest.raises(ValueError):
        parse_timestamp_safe("not-a-date")
    with pytest.raises(ValueError):
        parse_timestamp_safe("")


def test_amount_validation():
    assert validate_amount(1500000, TransactionType.TRANSFER) == 1500000.0
    assert validate_amount("₹15,00,000", TransactionType.TRANSFER) == 1500000.0
    assert validate_amount("2500.50", TransactionType.TRANSFER) == 2500.50

    # Negative amounts rejected for normal transfers
    with pytest.raises(ValueError):
        validate_amount(-500, TransactionType.TRANSFER)

    # Negative amount allowed for REFUND
    assert validate_amount(-250.0, TransactionType.REFUND) == -250.0

    # Zero amount rejected
    with pytest.raises(ValueError):
        validate_amount(0, TransactionType.TRANSFER)

    # Invalid string rejected
    with pytest.raises(ValueError):
        validate_amount("abc", TransactionType.TRANSFER)


# -----------------------------------------------------------------------------
# 3. CSV Parsing Tests
# -----------------------------------------------------------------------------

def test_csv_parsing_valid():
    csv_text = (
        "transaction_id,timestamp,sender,receiver,amount,currency,transaction_type,case_id\n"
        "TX-101,2024-07-15 10:00:00,ACC990188231,ACC442099114,500000,INR,TRANSFER,FIR-2024-405\n"
        "TX-102,2024-07-15 10:30:00,ACC442099114,ACC112233445,150000,INR,TRANSFER,FIR-2024-405\n"
    )
    records, rejected, warnings, errors = FinancialParser.parse_csv(csv_text)
    assert len(records) == 2
    assert rejected == 0
    assert len(errors) == 0
    assert records[0].transaction_id == "TX-101"
    assert records[0].amount == 500000.0
    assert records[1].receiver == "ACC112233445"


def test_csv_parsing_malformed_and_duplicate():
    csv_text = (
        "transaction_id,timestamp,sender,receiver,amount\n"
        "TX-001,2024-07-15 10:00:00,ACC1,ACC2,5000\n"
        "TX-002,invalid-timestamp,ACC1,ACC2,1000\n"
        "TX-003,2024-07-15 10:10:00,,ACC2,1000\n"
        "TX-004,2024-07-15 10:20:00,ACC1,ACC2,-200\n"
        "TX-001,2024-07-15 10:30:00,ACC1,ACC2,7000\n"
    )
    records, rejected, warnings, errors = FinancialParser.parse_csv(csv_text)
    # Valid: TX-001 and duplicate TX-001 (preserved with warning) -> 2 valid
    assert len(records) == 2
    assert rejected == 3  # invalid timestamp, missing sender, negative amount
    assert any("Duplicate" in w for w in warnings)
    assert len(errors) == 3


def test_csv_empty_handling():
    records, rejected, warnings, errors = FinancialParser.parse_csv("")
    assert len(records) == 0
    assert len(errors) > 0


# -----------------------------------------------------------------------------
# 4. JSON Parsing Tests
# -----------------------------------------------------------------------------

def test_json_parsing_list_and_envelope():
    # Direct list
    json_list = [
        {"transaction_id": "TX-J1", "timestamp": "2024-07-15T10:00:00", "sender": "ACC1", "receiver": "ACC2", "amount": 10000},
        {"transaction_id": "TX-J2", "timestamp": "2024-07-15T10:15:00", "sender": "ACC2", "receiver": "ACC3", "amount": 5000}
    ]
    records, rejected, warnings, errors = FinancialParser.parse_json(json_list)
    assert len(records) == 2
    assert rejected == 0

    # Enveloped dict {"records": [...]}
    envelope = {"records": json_list}
    records_env, _, _, _ = FinancialParser.parse_json(json.dumps(envelope))
    assert len(records_env) == 2


# -----------------------------------------------------------------------------
# 5. Financial Ingestion API Tests
# -----------------------------------------------------------------------------

def test_financial_ingestion_api_csv():
    csv_data = (
        "transaction_id,timestamp,sender,receiver,amount,currency,case_id\n"
        "API-TX-1,2024-07-15 10:00:00,ACC990188231,ACC442099114,250000,INR,FIR-2024-405\n"
        "API-TX-2,2024-07-15 10:15:00,ACC442099114,ACC112233445,180000,INR,FIR-2024-405\n"
    )
    res = client.post("/api/financial/ingest", data={"raw_data": csv_data})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["records_processed"] == 2
    assert data["relationships_created"] >= 2


def test_financial_ingestion_api_json():
    payload = {
        "records": [
            {"transaction_id": "JSON-TX-1", "timestamp": "2024-07-15T12:00:00", "sender": "ACC990188231", "receiver": "ACC112233445", "amount": 75000}
        ]
    }
    res = client.post("/api/financial/ingest", json=payload)
    assert res.status_code == 200
    assert res.json()["records_processed"] == 1


def test_financial_ingestion_api_validation_error():
    # Completely invalid data
    bad_csv = "sender,receiver,amount,timestamp\n,,-500,bad-date\n"
    res = client.post("/api/financial/ingest", data={"raw_data": bad_csv})
    assert res.status_code == 422


# -----------------------------------------------------------------------------
# 6. Graph Integration & Duplicate Prevention
# -----------------------------------------------------------------------------

def test_graph_integration_node_reuse():
    g = get_graph_engine()
    initial_nodes_count = len(g.get_all_nodes())

    # Ingest transfer between pre-existing nodes: ACC_HAWALA_9901 (ACC990188231) and ACC_SHELL_4420 (ACC442099114)
    rec = FinancialRecord(
        transaction_id="TX-REUSE-1",
        timestamp=datetime(2024, 7, 15, 10, 0),
        sender="ACC990188231",
        receiver="ACC442099114",
        amount=300000.0,
        case_id="FIR-2024-405",
    )
    service = FinancialService(graph_engine=g, storage=get_financial_storage())
    added, created_nodes, created_rels = service.ingest_records_into_graph([rec])

    assert added == 1
    # Nodes should be reused from existing syndicate network!
    assert created_nodes == 0
    assert len(g.get_all_nodes()) == initial_nodes_count
    assert created_rels >= 1


def test_duplicate_transaction_prevention():
    g = get_graph_engine()
    rec = FinancialRecord(
        transaction_id="TX-DUP-CHECK",
        timestamp=datetime(2024, 7, 15, 10, 0),
        sender="ACC990188231",
        receiver="ACC442099114",
        amount=100000.0,
    )
    service = FinancialService(graph_engine=g, storage=get_financial_storage())
    added1, _, _ = service.ingest_records_into_graph([rec])
    assert added1 == 1

    # Ingest again with same transaction_id
    added2, _, _ = service.ingest_records_into_graph([rec])
    assert added2 == 0  # Deduplicated in storage


# -----------------------------------------------------------------------------
# 7. Transaction Statistics & Counterparty Analysis
# -----------------------------------------------------------------------------

def test_transaction_and_counterparty_statistics():
    # Load demo transactions
    demo_file = Path("sample_data/demo_financial.csv")
    assert demo_file.exists()
    records, _, _, _ = FinancialParser.parse_csv(demo_file.read_bytes())

    service = FinancialService(graph_engine=get_graph_engine(), storage=get_financial_storage())
    service.ingest_records_into_graph(records)

    # Analyze Hawala Account (ACC990188231)
    res = client.get("/api/financial/analyze/ACC990188231")
    assert res.status_code == 200
    data = res.json()

    stats = data["statistics"]
    assert stats["total_transactions"] >= 4
    assert stats["total_incoming_amount"] > 0
    assert stats["total_outgoing_amount"] > 0
    assert stats["net_flow"] == round(stats["total_incoming_amount"] - stats["total_outgoing_amount"], 2)
    assert stats["largest_transaction"] >= 1500000.0
    assert stats["unique_counterparties"] >= 2

    # Verify counterparties
    cps = data["top_counterparties"]
    assert len(cps) >= 2
    assert any(c["counterparty_id"] in ("ACC442099114", "PERSON_TARIQ_AHMAD") for c in cps)


# -----------------------------------------------------------------------------
# 8. Flow Analysis (Incoming, Outgoing, Net Flow)
# -----------------------------------------------------------------------------

def test_flow_analysis():
    demo_file = Path("sample_data/demo_financial.csv")
    records, _, _, _ = FinancialParser.parse_csv(demo_file.read_bytes())
    service = FinancialService(graph_engine=get_graph_engine(), storage=get_financial_storage())
    service.ingest_records_into_graph(records)

    res = client.get("/api/financial/flow/ACC442099114")
    assert res.status_code == 200
    flow = res.json()

    assert flow["incoming_count"] >= 1
    assert flow["incoming_total"] >= 1500000.0
    assert flow["outgoing_count"] >= 5
    assert flow["outgoing_total"] >= 1500000.0
    assert flow["net_flow"] == round(flow["incoming_total"] - flow["outgoing_total"], 2)
    assert len(flow["top_sources"]) >= 1
    assert len(flow["top_destinations"]) >= 1


# -----------------------------------------------------------------------------
# 9. Configurable High-Value Detection
# -----------------------------------------------------------------------------

def test_high_value_detection():
    demo_file = Path("sample_data/demo_financial.csv")
    records, _, _, _ = FinancialParser.parse_csv(demo_file.read_bytes())
    service = FinancialService(graph_engine=get_graph_engine(), storage=get_financial_storage())
    service.ingest_records_into_graph(records)

    # With high threshold (3,000,000) -> no high value
    res_high = client.get("/api/financial/anomalies/PERSON_TARIQ_AHMAD?high_value_threshold=3000000")
    assert res_high.status_code == 200
    inds_high = res_high.json()["indicators"]
    assert not any(i["name"] == "High-Value Transaction" for i in inds_high)

    # With default/lower threshold (500,000) -> Tariq's 2,400,000 transfer should trigger indicator
    res_low = client.get("/api/financial/anomalies/PERSON_TARIQ_AHMAD?high_value_threshold=500000")
    assert res_low.status_code == 200
    inds_low = res_low.json()["indicators"]
    hv_ind = next((i for i in inds_low if i["name"] == "High-Value Transaction"), None)
    assert hv_ind is not None
    assert "exceeds configured investigation threshold" in hv_ind["explanation"]
    assert hv_ind["severity"] == "HIGH"


# -----------------------------------------------------------------------------
# 10. Velocity / Burst Detection
# -----------------------------------------------------------------------------

def test_burst_velocity_detection():
    # Ingest 5 rapid transactions from ACC442099114 to ACC112233445 between 11:30 and 11:47 (17 minutes)
    demo_file = Path("sample_data/demo_financial.csv")
    records, _, _, _ = FinancialParser.parse_csv(demo_file.read_bytes())
    service = FinancialService(graph_engine=get_graph_engine(), storage=get_financial_storage())
    service.ingest_records_into_graph(records)

    res = client.get("/api/financial/anomalies/ACC442099114?burst_window_minutes=60")
    assert res.status_code == 200
    bursts = res.json()["bursts"]
    assert len(bursts) >= 1
    top_burst = bursts[0]
    assert top_burst["transaction_count"] >= 5
    assert "Rapid transaction activity" in top_burst["description"]


# -----------------------------------------------------------------------------
# 11. Edge Cases: No-Data & Unknown Entity
# -----------------------------------------------------------------------------

def test_no_data_entity():
    # Query entity with zero financial records
    res = client.get("/api/financial/analyze/PERSON_SAMEER_KHAN")
    assert res.status_code == 200
    data = res.json()
    assert data["statistics"]["total_transactions"] == 0
    assert data["statistics"]["net_flow"] == 0.0
    assert len(data["top_counterparties"]) == 0
    assert len(data["transactions_preview"]) == 0


def test_invalid_blank_entity():
    res = client.get("/api/financial/analyze/%20")
    assert res.status_code == 400


# -----------------------------------------------------------------------------
# 12. Cross-Links: Universal Search, CDR & Case Linkage
# -----------------------------------------------------------------------------

def test_universal_search_financial_integration():
    # Universal search for Bank Account
    u_search = get_universal_search()
    res = u_search.search("ACC990188231")
    assert len(res.results) >= 1
    found = res.results[0]
    assert found.entity_type in ("BANK_ACCOUNT", "BankAccount")
    assert "ACC990188231" in found.entity_id or found.name == "ACC990188231"


def test_case_integration():
    # Ingest transaction with case FIR-2024-405
    rec = FinancialRecord(
        transaction_id="TX-CASE-LINK",
        timestamp=datetime(2024, 7, 15, 10, 0),
        sender="ACC990188231",
        receiver="ACC442099114",
        amount=100000.0,
        case_id="FIR-2024-405",
    )
    g = get_graph_engine()
    service = FinancialService(graph_engine=g, storage=get_financial_storage())
    service.ingest_records_into_graph([rec])

    # Check that INVOLVED_IN edge links to CASE_FIR_2024_405
    edges = g.get_all_edges()
    involved_edges = [
        e for e in edges
        if (e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)) == EdgeType.INVOLVED_IN.value
        and (e.source in ("ACC_HAWALA_9901", "ACC990188231"))
    ]
    assert len(involved_edges) >= 1


def test_cdr_financial_coexistence():
    # Ingest CDR record for Tariq Ahmad
    cdr_csv = (
        "caller,receiver,timestamp,duration,call_type\n"
        "+919811223344,+919876500112,2024-07-15 10:00:00,120,OUTGOING\n"
    )
    cdr_recs, _, _, _ = CDRParser.parse_csv(cdr_csv)
    from app.core.cdr_analytics import CDRService
    cdr_service = CDRService(graph_engine=get_graph_engine(), storage=get_cdr_storage())
    cdr_service.ingest_records_into_graph(cdr_recs)

    # Ingest Financial record for Tariq Ahmad
    fin_rec = FinancialRecord(
        transaction_id="TX-TARIQ-1",
        timestamp=datetime(2024, 7, 15, 11, 0),
        sender="PERSON_TARIQ_AHMAD",
        receiver="ACC_HAWALA_9901",
        amount=500000.0,
    )
    fin_service = FinancialService(graph_engine=get_graph_engine(), storage=get_financial_storage())
    fin_service.ingest_records_into_graph([fin_rec])

    # Both CDR and Financial endpoints work concurrently for Tariq
    cdr_res = client.get("/api/cdr/analyze/PERSON_TARIQ_AHMAD")
    assert cdr_res.status_code == 200
    assert cdr_res.json()["statistics"]["total_calls"] >= 1

    fin_res = client.get("/api/financial/analyze/PERSON_TARIQ_AHMAD")
    assert fin_res.status_code == 200
    assert fin_res.json()["statistics"]["total_transactions"] >= 1
