# -*- coding: utf-8 -*-
"""
Comprehensive Test Suite for STEP 11: Investigation Timeline for NODE SENTINEL.
Tests:
- Timeline generation from unified multi-source data
- Chronological ordering (descending newest-first, ascending oldest-first)
- Entity filtering (Person, Bank Account, Phone, Case)
- Event type filtering (FINANCIAL, CALL, CASE, LOCATION, VEHICLE, FACE)
- Severity filtering (INFO, NOTICE, ELEVATED, HIGH)
- Date filtering (start, end, range)
- Keyword search across events
- Financial event extraction & metadata integrity
- CDR event extraction & telecommunications context
- Case / FIR event extraction & section linkages
- Unknown entity handling (graceful empty timeline, no crash)
- Empty timeline handling
- Malformed dates & parameter tolerance
- API response via TestClient (GET /api/timeline/{entity_id})
- Integration with Universal Search
- Integration with Knowledge Graph
- Preservation of existing CDR, Financial, Graph, and Universal Search functionality
"""
from __future__ import annotations

from datetime import datetime
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.graph_engine import get_graph_engine
from app.api.routes_ingest import load_dataset_by_name
from app.core.timeline_engine import TimelineEngine, get_timeline_engine
from app.models.timeline_models import TimelineEventType, TimelineSeverity, TimelineResponse


@pytest.fixture(autouse=True)
def setup_dataset():
    """Ensure standard demo graph is loaded prior to timeline testing."""
    g = get_graph_engine()
    g.clear()
    load_dataset_by_name(g, "syndicate_network.json")
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def engine():
    return get_timeline_engine()


# ===================================================================
# 1. Timeline Generation Tests
# ===================================================================

def test_timeline_generation_person(engine: TimelineEngine):
    """Test timeline generation for a known person entity (Tariq Ahmad)."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD")
    assert isinstance(res, TimelineResponse)
    assert res.entity_id == "PERSON_TARIQ_AHMAD"
    assert res.entity_name == "Tariq Ahmad"
    assert res.total_events > 0
    assert len(res.events) == res.total_events
    assert res.date_range.get("earliest") is not None
    assert res.date_range.get("latest") is not None


def test_timeline_generation_account(engine: TimelineEngine):
    """Test timeline generation for a bank account entity."""
    res = engine.get_entity_timeline("ACC990188231")
    assert isinstance(res, TimelineResponse)
    assert res.entity_id in ("ACC990188231", "ACC_HAWALA_9901")
    assert res.total_events > 0
    # Bank account should have financial events
    fin_events = [e for e in res.events if e.event_type == TimelineEventType.FINANCIAL]
    assert len(fin_events) > 0


def test_timeline_generation_phone(engine: TimelineEngine):
    """Test timeline generation for a phone entity."""
    res = engine.get_entity_timeline("+919811223344")
    assert isinstance(res, TimelineResponse)
    assert res.total_events > 0
    call_events = [e for e in res.events if e.event_type == TimelineEventType.CALL]
    assert len(call_events) > 0


def test_timeline_generation_case(engine: TimelineEngine):
    """Test timeline generation for a case entity."""
    res = engine.get_entity_timeline("CASE_FIR_2024_405")
    assert isinstance(res, TimelineResponse)
    assert res.total_events > 0
    case_events = [e for e in res.events if e.event_type == TimelineEventType.CASE]
    assert len(case_events) > 0


# ===================================================================
# 2. Chronological Ordering Tests
# ===================================================================

def test_chronological_ordering_desc(engine: TimelineEngine):
    """Test default descending order (newest first)."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", sort_order="desc")
    assert len(res.events) >= 2
    timestamps = [e.timestamp for e in res.events]
    for i in range(len(timestamps) - 1):
        assert timestamps[i] >= timestamps[i + 1], f"Event at index {i} is older than event at {i+1}"


def test_chronological_ordering_asc(engine: TimelineEngine):
    """Test ascending order (oldest first)."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", sort_order="asc")
    assert len(res.events) >= 2
    timestamps = [e.timestamp for e in res.events]
    for i in range(len(timestamps) - 1):
        assert timestamps[i] <= timestamps[i + 1], f"Event at index {i} is newer than event at {i+1}"


# ===================================================================
# 3. Event Type Filtering Tests
# ===================================================================

def test_event_type_filtering_call(engine: TimelineEngine):
    """Filter specifically for CALL events."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", event_type="CALL")
    for ev in res.events:
        assert ev.event_type == TimelineEventType.CALL


def test_event_type_filtering_financial(engine: TimelineEngine):
    """Filter specifically for FINANCIAL events."""
    res = engine.get_entity_timeline("PERSON_POOJA_RATHI", event_type="FINANCIAL")
    for ev in res.events:
        assert ev.event_type == TimelineEventType.FINANCIAL


def test_event_type_filtering_case(engine: TimelineEngine):
    """Filter specifically for CASE events."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", event_type="CASE")
    for ev in res.events:
        assert ev.event_type == TimelineEventType.CASE


def test_event_type_filtering_multiple(engine: TimelineEngine):
    """Filter by comma-separated event types (CALL,FINANCIAL)."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", event_type="CALL,FINANCIAL")
    for ev in res.events:
        assert ev.event_type in (TimelineEventType.CALL, TimelineEventType.FINANCIAL)


# ===================================================================
# 4. Severity Filtering Tests
# ===================================================================

def test_severity_filtering_high(engine: TimelineEngine):
    """Filter for HIGH severity events."""
    res = engine.get_entity_timeline("PERSON_POOJA_RATHI", severity="HIGH")
    for ev in res.events:
        assert ev.severity == TimelineSeverity.HIGH


def test_severity_filtering_elevated(engine: TimelineEngine):
    """Filter for ELEVATED severity events."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", severity="ELEVATED")
    for ev in res.events:
        assert ev.severity == TimelineSeverity.ELEVATED


def test_severity_counts_integrity(engine: TimelineEngine):
    """Verify severity count aggregates equal total events."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD")
    sev_counts = res.severity_counts
    total_from_counts = sum(sev_counts.values())
    assert total_from_counts == res.total_events
    assert res.high_priority_count == (sev_counts.get("HIGH", 0) + sev_counts.get("ELEVATED", 0))


# ===================================================================
# 5. Date Range Filtering Tests
# ===================================================================

def test_date_range_filtering_start_only(engine: TimelineEngine):
    """Filter with start date only."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", start="2024-07-16")
    start_dt = datetime.fromisoformat("2024-07-16T00:00:00")
    for ev in res.events:
        assert ev.timestamp >= start_dt


def test_date_range_filtering_end_only(engine: TimelineEngine):
    """Filter with end date only."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", end="2024-07-18")
    end_dt = datetime.fromisoformat("2024-07-18T23:59:59")
    for ev in res.events:
        assert ev.timestamp <= end_dt


def test_date_range_filtering_window(engine: TimelineEngine):
    """Filter with both start and end date."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", start="2024-07-16", end="2024-07-17")
    start_dt = datetime.fromisoformat("2024-07-16T00:00:00")
    end_dt = datetime.fromisoformat("2024-07-17T23:59:59")
    for ev in res.events:
        assert start_dt <= ev.timestamp <= end_dt


# ===================================================================
# 6. Search Filtering Tests
# ===================================================================

def test_keyword_search_filtering(engine: TimelineEngine):
    """Filter events matching keyword."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", search="transfer")
    for ev in res.events:
        text = f"{ev.title} {ev.description} {ev.location or ''} {ev.source or ''}".lower()
        assert "transfer" in text


# ===================================================================
# 7. Multi-Source Event Content Verification
# ===================================================================

def test_financial_event_content(engine: TimelineEngine):
    """Verify structure and metadata of financial events."""
    res = engine.get_entity_timeline("ACC990188231", event_type="FINANCIAL")
    assert len(res.events) > 0
    ev = res.events[0]
    assert ev.event_type == TimelineEventType.FINANCIAL
    assert "amount" in ev.metadata
    assert ev.metadata["amount"] > 0
    assert "sender" in ev.metadata
    assert "receiver" in ev.metadata
    assert ev.source is not None


def test_cdr_event_content(engine: TimelineEngine):
    """Verify structure and metadata of CDR events."""
    res = engine.get_entity_timeline("+919811223344", event_type="CALL")
    assert len(res.events) > 0
    ev = res.events[0]
    assert ev.event_type == TimelineEventType.CALL
    assert "caller" in ev.metadata
    assert "receiver" in ev.metadata
    assert "duration_seconds" in ev.metadata
    assert ev.source is not None


def test_case_event_content(engine: TimelineEngine):
    """Verify structure and metadata of case events."""
    res = engine.get_entity_timeline("CASE_FIR_2024_405")
    case_evs = [e for e in res.events if e.event_type == TimelineEventType.CASE]
    assert len(case_evs) > 0
    ev = case_evs[0]
    assert "CASE_FIR_2024_405" in ev.entity_ids or "CASE_FIR_2024_405" in ev.related_case_ids or "FIR-2024-405" in ev.title


def test_location_and_vehicle_events(engine: TimelineEngine):
    """Verify location or vehicle events are captured from graph edges."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD")
    types = {e.event_type for e in res.events}
    # Tariq Ahmad has LOCATED_AT and OPERATES edges in syndicate_network.json
    assert TimelineEventType.LOCATION in types or TimelineEventType.VEHICLE in types


# ===================================================================
# 8. Edge Cases & Error Tolerance
# ===================================================================

def test_unknown_entity_handling(engine: TimelineEngine):
    """Unknown entity returns a valid, empty timeline without raising an exception."""
    res = engine.get_entity_timeline("UNKNOWN_GHOST_ENTITY_99999")
    assert isinstance(res, TimelineResponse)
    assert res.total_events == 0
    assert len(res.events) == 0
    assert res.high_priority_count == 0


def test_malformed_date_tolerance(engine: TimelineEngine):
    """Malformed dates should be ignored gracefully rather than crashing."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", start="NOT-A-DATE", end="INVALID")
    assert isinstance(res, TimelineResponse)
    assert res.total_events > 0


def test_limit_parameter(engine: TimelineEngine):
    """Limit correctly caps the returned event count."""
    res = engine.get_entity_timeline("PERSON_TARIQ_AHMAD", limit=3)
    assert len(res.events) <= 3


# ===================================================================
# 9. API Route Tests (FastAPI TestClient)
# ===================================================================

def test_api_get_timeline_success(client: TestClient):
    """API endpoint returns 200 and matches response model."""
    resp = client.get("/api/timeline/PERSON_TARIQ_AHMAD")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_id"] == "PERSON_TARIQ_AHMAD"
    assert "events" in data
    assert "total_events" in data
    assert "date_range" in data
    assert "event_type_counts" in data
    assert "high_priority_count" in data


def test_api_get_timeline_with_filters(client: TestClient):
    """API endpoint respects query parameters."""
    resp = client.get(
        "/api/timeline/PERSON_TARIQ_AHMAD",
        params={"event_type": "CALL", "severity": "NOTICE", "limit": 5, "sort_order": "asc"}
    )
    assert resp.status_code == 200
    data = resp.json()
    for ev in data["events"]:
        assert ev["event_type"] == "CALL"
        assert ev["severity"] == "NOTICE"


def test_api_get_timeline_unknown_entity(client: TestClient):
    """API endpoint for unknown entity returns 200 with empty events."""
    resp = client.get("/api/timeline/DOES_NOT_EXIST_XYZ")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_events"] == 0
    assert data["events"] == []


def test_api_get_timeline_blank_id(client: TestClient):
    """API endpoint for whitespace entity ID returns 400 Bad Request."""
    resp = client.get("/api/timeline/%20%20")
    assert resp.status_code in (400, 404)


# ===================================================================
# 10. Integration with Universal Search & Knowledge Graph
# ===================================================================

def test_universal_search_integration(client: TestClient):
    """Entities returned by Universal Search can be queried for their timeline."""
    search_resp = client.get("/api/search", params={"q": "Tariq"})
    assert search_resp.status_code == 200
    results = search_resp.json().get("results", [])
    assert len(results) > 0

    first_entity_id = results[0]["entity_id"]
    tl_resp = client.get(f"/api/timeline/{first_entity_id}")
    assert tl_resp.status_code == 200
    assert tl_resp.json()["total_events"] > 0


def test_knowledge_graph_node_timeline_consistency(client: TestClient):
    """Graph nodes retrieved from /api/network/graph have timelines accessible via /api/timeline."""
    graph_resp = client.get("/api/network/graph")
    assert graph_resp.status_code == 200
    nodes = graph_resp.json().get("nodes", [])
    assert len(nodes) > 0

    # Pick a Person node
    person_node = next((n for n in nodes if n.get("node_type") == "Person" or n.get("group") == "Person"), None)
    assert person_node is not None

    tl_resp = client.get(f"/api/timeline/{person_node['id']}")
    assert tl_resp.status_code == 200
    assert tl_resp.json()["entity_id"] == person_node["id"]


# ===================================================================
# 11. Preservation of Existing Features
# ===================================================================

def test_existing_cdr_analysis_preserved(client: TestClient):
    """Ensure CDR analysis endpoint still functions normally."""
    resp = client.get("/api/cdr/analyze/PERSON_TARIQ_AHMAD")
    assert resp.status_code == 200
    data = resp.json()
    assert "statistics" in data
    assert "top_contacts" in data


def test_existing_financial_analysis_preserved(client: TestClient):
    """Ensure financial analysis endpoint still functions normally."""
    resp = client.get("/api/financial/analyze/PERSON_POOJA_RATHI")
    assert resp.status_code == 200
    data = resp.json()
    assert "statistics" in data
    assert "top_counterparties" in data
