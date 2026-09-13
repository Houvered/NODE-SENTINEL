# -*- coding: utf-8 -*-
"""
Test Suite for STEP 9A: CDR (Call Detail Record) Analysis Foundation.
Tests:
- CSV parsing & validation
- JSON parsing & envelope support
- Phone number normalization (Indian format, +91, stripped hyphens/spaces)
- Timestamp safe parsing
- Malformed records error handling (no silent loss)
- CDR ingestion endpoint (POST /api/cdr/ingest)
- Graph relationship creation & node reuse
- Duplicate call ID handling
- Call statistics calculation
- Contact-level statistics
- Communication burst detection
- Empty CDR / No-data case
- Invalid entity case
- Universal Search -> CDR integration
- CDR -> Graph analytics integration
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
from app.core.cdr_parser import (
    CDRParser,
    normalize_phone_number,
    parse_timestamp_safe,
    parse_duration_seconds,
    parse_call_type,
)
from app.models.cdr_models import CallType, CDRRecord
from app.core.cdr_analytics import CDRService, get_cdr_storage


@pytest.fixture(autouse=True)
def setup_test_graph():
    """Ensure graph and CDR storage are in a known fresh state."""
    g = get_graph_engine()
    g.clear()
    load_dataset_by_name(g, "syndicate_network.json")
    storage = get_cdr_storage()
    storage.clear()
    yield


client = TestClient(app)


# -----------------------------------------------------------------------------
# 1. Phone Normalization Tests
# -----------------------------------------------------------------------------

def test_phone_normalization():
    assert normalize_phone_number("9811223344") == "+919811223344"
    assert normalize_phone_number("+919811223344") == "+919811223344"
    assert normalize_phone_number("919811223344") == "+919811223344"
    assert normalize_phone_number("09811223344") == "+919811223344"
    assert normalize_phone_number("9811-223-344") == "+919811223344"
    assert normalize_phone_number("+91 (981) 122 3344") == "+919811223344"
    assert normalize_phone_number("+14155552671") == "+14155552671"

    with pytest.raises(ValueError):
        normalize_phone_number("123")  # too short (< 7 digits)
    with pytest.raises(ValueError):
        normalize_phone_number("")
    with pytest.raises(ValueError):
        normalize_phone_number(None)


# -----------------------------------------------------------------------------
# 2. Timestamp & Duration Parsing Tests
# -----------------------------------------------------------------------------

def test_timestamp_parsing():
    dt1 = parse_timestamp_safe("2024-07-16T14:30:00")
    assert dt1.year == 2024 and dt1.month == 7 and dt1.day == 16 and dt1.hour == 14

    dt2 = parse_timestamp_safe("2024-07-16 14:30:00")
    assert dt2.year == 2024 and dt2.hour == 14

    dt3 = parse_timestamp_safe("16-07-2024 14:30:00")
    assert dt3.year == 2024 and dt3.month == 7 and dt3.day == 16

    with pytest.raises(ValueError):
        parse_timestamp_safe("invalid-date-string")


def test_duration_parsing():
    assert parse_duration_seconds(120) == 120
    assert parse_duration_seconds("45") == 45
    assert parse_duration_seconds("02:30") == 150
    assert parse_duration_seconds("01:02:30") == 3750
    assert parse_duration_seconds("") == 0
    assert parse_duration_seconds(None) == 0

    with pytest.raises(ValueError):
        parse_duration_seconds(-5)  # negative duration rejected
    with pytest.raises(ValueError):
        parse_duration_seconds("-01:20")


def test_call_type_parsing():
    assert parse_call_type("INCOMING") == CallType.INCOMING
    assert parse_call_type("in") == CallType.INCOMING
    assert parse_call_type("OUTGOING") == CallType.OUTGOING
    assert parse_call_type("out") == CallType.OUTGOING
    assert parse_call_type("MISSED") == CallType.MISSED
    assert parse_call_type("rejected") == CallType.MISSED
    assert parse_call_type("OTHER") == CallType.UNKNOWN


# -----------------------------------------------------------------------------
# 3. CSV & JSON Parsing Tests
# -----------------------------------------------------------------------------

def test_csv_parsing():
    csv_text = """call_id,caller,receiver,timestamp,duration_seconds,call_type,cell_tower,location
CALL-01,9811223344,9876500112,2024-07-16 10:00:00,120,OUTGOING,TOWER-1,Okhla
CALL-02,9876500112,9811223344,2024-07-16 10:30:00,0,MISSED,TOWER-1,Okhla
"""
    records, rejected, warnings, errors = CDRParser.parse_csv(csv_text)
    assert len(records) == 2
    assert rejected == 0
    assert len(errors) == 0
    assert records[0].caller == "+919811223344"
    assert records[0].receiver == "+919876500112"
    assert records[0].duration_seconds == 120
    assert records[1].call_type == CallType.MISSED


def test_json_parsing():
    json_data = [
        {
            "call_id": "CALL-J1",
            "caller": "+919811223344",
            "receiver": "+919876500112",
            "timestamp": "2024-07-16T12:00:00",
            "duration_seconds": 95,
            "call_type": "OUTGOING",
            "cell_tower": "TOWER-NOIDA"
        }
    ]
    records, rejected, warnings, errors = CDRParser.parse_json(json_data)
    assert len(records) == 1
    assert rejected == 0
    assert records[0].call_id == "CALL-J1"
    assert records[0].duration_seconds == 95


def test_malformed_records_handling():
    csv_text = """caller,receiver,timestamp,duration_seconds
,9876500112,2024-07-16 10:00:00,60
9811223344,,2024-07-16 10:00:00,60
9811223344,9876500112,invalid_date,60
9811223344,9876500112,2024-07-16 10:00:00,-50
9811223344,9876500112,2024-07-16 10:00:00,90
"""
    records, rejected, warnings, errors = CDRParser.parse_csv(csv_text)
    # 4 invalid rows, 1 valid row
    assert len(records) == 1
    assert rejected == 4
    assert len(errors) == 4
    assert records[0].duration_seconds == 90


# -----------------------------------------------------------------------------
# 4. Ingestion API & Graph Integration Tests
# -----------------------------------------------------------------------------

def test_cdr_ingest_json_api():
    payload = [
        {
            "call_id": "SYN-API-01",
            "caller": "+919811223344",
            "receiver": "+919876500112",
            "timestamp": "2024-07-16T10:00:00",
            "duration_seconds": 150,
            "call_type": "OUTGOING",
            "cell_tower": "OKHLA-01"
        },
        {
            "call_id": "SYN-API-02",
            "caller": "+919876500112",
            "receiver": "+919811223344",
            "timestamp": "2024-07-16T11:00:00",
            "duration_seconds": 210,
            "call_type": "INCOMING",
            "cell_tower": "OKHLA-01"
        }
    ]
    resp = client.post("/api/cdr/ingest", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["records_processed"] == 2
    assert data["records_rejected"] == 0
    assert data["relationships_created"] == 2

    # Verify graph edges were created
    g = get_graph_engine()
    call_edges = [
        e for e in g.get_all_edges()
        if (e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)) == "CALLS"
    ]
    assert len(call_edges) >= 2


def test_cdr_ingest_csv_file():
    csv_content = (
        "call_id,caller,receiver,timestamp,duration_seconds,call_type,cell_tower\n"
        "CSV-TEST-1,+919811223344,+919988776655,2024-07-16 15:00:00,300,OUTGOING,CHANDNI-1\n"
    )
    resp = client.post(
        "/api/cdr/ingest",
        files={"file": ("test_batch.csv", csv_content.encode("utf-8"), "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["records_processed"] == 1
    assert data["records_rejected"] == 0


def test_duplicate_prevention_and_node_reuse():
    # Tariq Ahmad owns PHONE_9811223344 in syndicate_network.json
    g = get_graph_engine()
    node_count_before = len(g.get_all_nodes())

    payload = [
        {
            "call_id": "DUP-01",
            "caller": "+919811223344",
            "receiver": "+919876500112",
            "timestamp": "2024-07-16T08:00:00",
            "duration_seconds": 60,
            "call_type": "OUTGOING"
        }
    ]
    resp1 = client.post("/api/cdr/ingest", json=payload)
    assert resp1.status_code == 200
    # Should reuse existing PHONE_9811223344 and PHONE_9876500112 nodes
    assert resp1.json()["entities_created"] == 0

    # Ingest same call again (duplicate call_id)
    resp2 = client.post("/api/cdr/ingest", json=payload)
    assert resp2.status_code == 200
    # Duplicate call_id skipped or handled safely without node bloat
    assert len(g.get_all_nodes()) == node_count_before


# -----------------------------------------------------------------------------
# 5. Call Statistics & Contact Breakdown Tests
# -----------------------------------------------------------------------------

def test_call_statistics_calculation():
    # Ingest sample demo CDR
    sample_csv_path = Path(__file__).resolve().parent / "sample_data" / "demo_cdr.csv"
    assert sample_csv_path.exists()

    with sample_csv_path.open("rb") as f:
        resp = client.post("/api/cdr/ingest", files={"file": ("demo_cdr.csv", f.read(), "text/csv")})
    assert resp.status_code == 200

    # Analyze Tariq Ahmad by Person ID
    res = client.get("/api/cdr/analyze/PERSON_TARIQ_AHMAD")
    assert res.status_code == 200
    data = res.json()

    assert data["entity_id"] == "PERSON_TARIQ_AHMAD"
    assert data["entity_name"] == "Tariq Ahmad"
    assert len(data["phone_numbers"]) > 0
    assert "+919811223344" in data["phone_numbers"]

    stats = data["statistics"]
    assert stats["total_calls"] > 0
    assert stats["total_duration_seconds"] > 0
    assert stats["unique_contacts"] >= 1
    assert stats["first_call_timestamp"] is not None
    assert stats["latest_call_timestamp"] is not None


def _ensure_demo_cdr_ingested():
    sample_csv_path = Path(__file__).resolve().parent / "sample_data" / "demo_cdr.csv"
    with sample_csv_path.open("rb") as f:
        resp = client.post("/api/cdr/ingest", files={"file": ("demo_cdr.csv", f.read(), "text/csv")})
    assert resp.status_code == 200


def test_contact_breakdown():
    _ensure_demo_cdr_ingested()
    # Contacts for Kabir Mirza
    res = client.get("/api/cdr/contacts/PERSON_KABIR_MIRZA")
    assert res.status_code == 200
    contacts = res.json()
    assert isinstance(contacts, list)
    assert len(contacts) > 0
    top = contacts[0]
    assert "phone_number" in top
    assert "call_count" in top
    assert top["call_count"] >= 1
    assert "total_duration_seconds" in top


# -----------------------------------------------------------------------------
# 6. Communication Burst Detection Tests
# -----------------------------------------------------------------------------

def test_burst_detection():
    _ensure_demo_cdr_ingested()
    res = client.get("/api/cdr/bursts/PERSON_KABIR_MIRZA?window_hours=6&min_calls=5")
    assert res.status_code == 200
    bursts = res.json()
    assert isinstance(bursts, list)
    # The demo dataset has a concentrated cluster on morning of 2024-07-16
    assert len(bursts) >= 1
    b = bursts[0]
    assert b["call_count"] >= 5
    assert "start_time" in b
    assert "end_time" in b
    assert b["indicator"] == "Communication Burst"
    assert "Requires investigator verification" in b["description"]


# -----------------------------------------------------------------------------
# 7. Edge Cases & Error Handling Tests
# -----------------------------------------------------------------------------

def test_empty_cdr_error():
    resp = client.post("/api/cdr/ingest", files={"file": ("empty.csv", b"", "text/csv")})
    assert resp.status_code == 400


def test_unknown_entity_analyze():
    # Unknown entity should return zero statistics without crashing
    res = client.get("/api/cdr/analyze/NON_EXISTENT_ENTITY_9999")
    assert res.status_code == 200
    data = res.json()
    assert data["statistics"]["total_calls"] == 0
    assert len(data["top_contacts"]) == 0
    assert len(data["bursts"]) == 0


def test_chronological_timeline():
    _ensure_demo_cdr_ingested()
    res = client.get("/api/cdr/timeline/PERSON_TARIQ_AHMAD")
    assert res.status_code == 200
    timeline = res.json()
    assert isinstance(timeline, list)
    assert len(timeline) >= 2
    # Should be sorted newest first
    ts1 = datetime.fromisoformat(timeline[0]["timestamp"])
    ts2 = datetime.fromisoformat(timeline[1]["timestamp"])
    assert ts1 >= ts2


# -----------------------------------------------------------------------------
# 8. Integration: Universal Search & Graph Analytics Correlation
# -----------------------------------------------------------------------------

def test_universal_search_and_cdr_integration():
    # Perform universal search for Tariq Ahmad
    s_res = client.get("/api/search?q=Tariq+Ahmad")
    assert s_res.status_code == 200
    s_data = s_res.json()
    assert s_data["total_results"] >= 1

    person_item = s_data["results"][0]
    entity_id = person_item["entity_id"]

    # Verify that this entity ID can be directly analyzed by CDR service
    cdr_res = client.get(f"/api/cdr/analyze/{entity_id}")
    assert cdr_res.status_code == 200
    cdr_data = cdr_res.json()
    assert cdr_data["entity_id"] == entity_id
    # Check graph centrality correlation
    assert "graph_centrality" in cdr_data
    if cdr_data["graph_centrality"]:
        assert "degree_centrality" in cdr_data["graph_centrality"]
        assert "betweenness_centrality" in cdr_data["graph_centrality"]
