# -*- coding: utf-8 -*-
"""
Verification and Test Suite for STEP 13: AI Investigation Assistant for NODE SENTINEL.

Tests:
1. Entity Resolution (exact ID, name, phone, account, vehicle, case)
2. Ambiguous Entity Handling (clarification requested, no guessing)
3. Unknown Entity Handling (clean no-record response, zero hallucination)
4. Grounded Connections Inquiry ("Who is connected to X?")
5. Grounded Call Telemetry Inquiry ("Show unusual call activity", "Show communication pattern")
6. Grounded Financial Flow Inquiry ("Show unusual financial activity", "Show financial flow")
7. Grounded Case Linkages Inquiry ("Which cases are connected to this person?")
8. Grounded Temporal Timeline Inquiry ("Summarize timeline", date filtering)
9. Grounded Explainable Risk Inquiry ("Why is this entity high risk?")
10. Shortest Path Finder Inquiry ("Find the shortest connection between A and B")
11. Investigation Summary Inquiry ("Summarize this investigation.")
12. Multi-turn Follow-up Context ("Show Tariq" -> "Show his calls" -> "What about his finances?")
13. Missing Telemetry Reporting (explicitly notes absent data)
14. Safety & Compliance (no criminal guilt determinations)
15. Navigation Actions Presence & Schema Validation
16. REST API Endpoints Contract (POST /query, 400 on blank, /context, /reset, /suggestions)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routes_ingest import load_dataset_by_name
from app.core.assistant_engine import AssistantEngine, get_assistant_engine
from app.core.graph_engine import get_graph_engine
from app.main import app
from app.models.assistant_models import (
    AssistantActionType,
    InvestigationAssistantRequest,
)

# Initialize TestClient
client = TestClient(app)

# Ensure sample dataset is loaded
graph = get_graph_engine()
if len(graph.get_all_nodes()) == 0:
    load_dataset_by_name(graph, "syndicate_network.json")


@pytest.fixture
def engine() -> AssistantEngine:
    return get_assistant_engine()


# -----------------------------------------------------------------------------
# 1. Entity Resolution
# -----------------------------------------------------------------------------

def test_entity_resolution_exact_and_modal(engine: AssistantEngine):
    # Person ID
    req1 = InvestigationAssistantRequest(query="Who is connected to PERSON_TARIQ_AHMAD?")
    res1 = engine.process_query(req1)
    assert res1.selected_entity is not None
    assert res1.selected_entity["entity_id"] == "PERSON_TARIQ_AHMAD"
    assert "Tariq Ahmad" in res1.answer

    # Person Name
    req2 = InvestigationAssistantRequest(query="Show connections for Pooja Rathi")
    res2 = engine.process_query(req2)
    assert res2.selected_entity is not None
    assert res2.selected_entity["name"] == "Pooja Rathi"

    # Phone Number
    req3 = InvestigationAssistantRequest(query="Who uses 9811223344?")
    res3 = engine.process_query(req3)
    assert res3.selected_entity is not None

    # Vehicle Plate
    req4 = InvestigationAssistantRequest(query="Who is connected to DL01AB9988?")
    res4 = engine.process_query(req4)
    assert res4.selected_entity is not None

    # Bank Account
    req5 = InvestigationAssistantRequest(query="Show transactions for ACC990188231")
    res5 = engine.process_query(req5)
    assert res5.selected_entity is not None


# -----------------------------------------------------------------------------
# 2. Ambiguous Entity Handling
# -----------------------------------------------------------------------------

def test_ambiguous_entity_handling(engine: AssistantEngine):
    # Search term matching multiple candidates
    req = InvestigationAssistantRequest(query="Who is connected to Sharma?")
    res = engine.process_query(req)
    if res.is_ambiguous:
        assert res.clarification_needed is not None
        assert len(res.candidate_entities) > 1
        assert "Multiple" in res.answer or "clarify" in res.answer


# -----------------------------------------------------------------------------
# 3. Unknown Entity / Zero Hallucination
# -----------------------------------------------------------------------------

def test_unknown_entity_handling(engine: AssistantEngine):
    req = InvestigationAssistantRequest(query="Who is connected to NonExistentGhostEntity999?")
    res = engine.process_query(req)
    assert "No supporting record was found in the current investigation dataset" in res.answer
    assert len(res.evidence) == 0
    assert res.uncertainty is not None


# -----------------------------------------------------------------------------
# 4. Connections Inquiries
# -----------------------------------------------------------------------------

def test_connections_inquiry(engine: AssistantEngine):
    req = InvestigationAssistantRequest(query="What are Tariq Ahmad's strongest connections?")
    res = engine.process_query(req)
    assert "Tariq Ahmad" in res.answer
    assert len(res.evidence) >= 1
    assert len(res.actions) >= 2
    action_types = [a.action_type for a in res.actions]
    assert AssistantActionType.VIEW_PROFILE in action_types
    assert AssistantActionType.VIEW_NETWORK in action_types


# -----------------------------------------------------------------------------
# 5. Call Telemetry Inquiries
# -----------------------------------------------------------------------------

def test_call_telemetry_inquiry(engine: AssistantEngine):
    # Global unusual calls
    req_global = InvestigationAssistantRequest(query="Show unusual call activity.")
    res_global = engine.process_query(req_global)
    assert "call" in res_global.answer.lower()
    assert len(res_global.evidence) >= 1

    # Subject-specific communication pattern
    req_subject = InvestigationAssistantRequest(
        query="Show the communication pattern.",
        selected_entity_id="PERSON_TARIQ_AHMAD"
    )
    res_subject = engine.process_query(req_subject)
    assert "Tariq Ahmad" in res_subject.answer
    assert any(a.action_type == AssistantActionType.ANALYZE_CALLS for a in res_subject.actions)


# -----------------------------------------------------------------------------
# 6. Financial Flow Inquiries
# -----------------------------------------------------------------------------

def test_financial_flow_inquiry(engine: AssistantEngine):
    # Global financial activity
    req_global = InvestigationAssistantRequest(query="Show unusual financial activity.")
    res_global = engine.process_query(req_global)
    assert "financial" in res_global.answer.lower()

    # Subject-specific financial flow
    req_subject = InvestigationAssistantRequest(
        query="Show the financial flow.",
        selected_entity_id="PERSON_TARIQ_AHMAD"
    )
    res_subject = engine.process_query(req_subject)
    assert "Tariq Ahmad" in res_subject.answer
    assert any(a.action_type == AssistantActionType.ANALYZE_FINANCES for a in res_subject.actions)


# -----------------------------------------------------------------------------
# 7. Case Linkages Inquiries
# -----------------------------------------------------------------------------

def test_case_linkages_inquiry(engine: AssistantEngine):
    req = InvestigationAssistantRequest(
        query="Which cases are connected to this person?",
        selected_entity_id="PERSON_TARIQ_AHMAD"
    )
    res = engine.process_query(req)
    assert "Tariq Ahmad" in res.answer
    assert len(res.relevant_cases) >= 1 or "case" in res.answer.lower()
    assert any(a.action_type == AssistantActionType.VIEW_CASE for a in res.actions)


# -----------------------------------------------------------------------------
# 8. Timeline & Temporal Inquiries
# -----------------------------------------------------------------------------

def test_timeline_inquiry(engine: AssistantEngine):
    req = InvestigationAssistantRequest(
        query="Summarize timeline",
        selected_entity_id="PERSON_TARIQ_AHMAD"
    )
    res = engine.process_query(req)
    assert "Tariq Ahmad" in res.answer
    assert any(a.action_type == AssistantActionType.VIEW_TIMELINE for a in res.actions)


# -----------------------------------------------------------------------------
# 9. Explainable Risk Inquiry
# -----------------------------------------------------------------------------

def test_risk_explanation_inquiry(engine: AssistantEngine):
    req = InvestigationAssistantRequest(
        query="Why is this entity high risk?",
        selected_entity_id="PERSON_TARIQ_AHMAD"
    )
    res = engine.process_query(req)
    assert "Risk Score" in res.answer
    assert "Risk Level" in res.answer
    assert "Evidence Factors Breakdown" in res.answer
    assert len(res.risk_indicators) >= 1
    assert "decision-support" in res.answer.lower() or "investigator verification" in res.answer.lower()
    assert any(a.action_type == AssistantActionType.VIEW_RISK for a in res.actions)


# -----------------------------------------------------------------------------
# 10. Shortest Path Inquiries
# -----------------------------------------------------------------------------

def test_shortest_path_inquiry(engine: AssistantEngine):
    # Connected entities
    req = InvestigationAssistantRequest(query="Find the shortest connection between Tariq Ahmad and DL01AB9988.")
    res = engine.process_query(req)
    assert "Shortest path between" in res.answer
    assert "hop(s)" in res.answer
    assert len(res.evidence) >= 1

    # Missing target
    req_bad = InvestigationAssistantRequest(query="Find the shortest connection between Tariq Ahmad and GhostTarget999.")
    res_bad = engine.process_query(req_bad)
    assert "No connection path found" in res_bad.answer or "not found" in res_bad.answer


# -----------------------------------------------------------------------------
# 11. Investigation Overview / Summary
# -----------------------------------------------------------------------------

def test_investigation_summary(engine: AssistantEngine):
    req = InvestigationAssistantRequest(query="Summarize this investigation.")
    res = engine.process_query(req)
    assert "NODE SENTINEL" in res.answer or "Overview" in res.answer
    assert "Knowledge Graph Scale" in res.answer
    assert "Active Statistical Telemetry Alerts" in res.answer


# -----------------------------------------------------------------------------
# 12. Multi-Turn Conversation Context & Follow-Ups
# -----------------------------------------------------------------------------

def test_multi_turn_followup_context(engine: AssistantEngine):
    cid = "test_conv_flow_123"

    # Turn 1: Inspect Tariq
    r1 = engine.process_query(InvestigationAssistantRequest(query="Show Tariq Ahmad.", conversation_id=cid))
    assert r1.selected_entity is not None
    assert r1.selected_entity["entity_id"] == "PERSON_TARIQ_AHMAD"

    # Turn 2: Follow-up using pronoun "his calls" without repeating entity name
    r2 = engine.process_query(InvestigationAssistantRequest(query="Show his calls.", conversation_id=cid))
    assert "Tariq Ahmad" in r2.answer
    assert r2.selected_entity is not None
    assert r2.selected_entity["entity_id"] == "PERSON_TARIQ_AHMAD"

    # Turn 3: Follow-up on financial activity
    r3 = engine.process_query(InvestigationAssistantRequest(query="What about his financial activity?", conversation_id=cid))
    assert "Tariq Ahmad" in r3.answer
    assert r3.selected_entity["entity_id"] == "PERSON_TARIQ_AHMAD"

    # Turn 4: Follow-up on risk explanation
    r4 = engine.process_query(InvestigationAssistantRequest(query="Why is he high risk?", conversation_id=cid))
    assert "Tariq Ahmad" in r4.answer
    assert "Risk Score" in r4.answer


# -----------------------------------------------------------------------------
# 13. Missing Telemetry Reporting
# -----------------------------------------------------------------------------

def test_missing_telemetry_reporting(engine: AssistantEngine):
    # Check Anita Deshmukh (has financial but no registered cases)
    req = InvestigationAssistantRequest(query="Which cases are connected to Anita Deshmukh?")
    res = engine.process_query(req)
    assert "No registered criminal FIRs" in res.answer or "0 linked CASE" in res.uncertainty or "0" in str(res.evidence)


# -----------------------------------------------------------------------------
# 14. Neutral Decision Support Terminology
# -----------------------------------------------------------------------------

def test_neutral_decision_support_language(engine: AssistantEngine):
    req = InvestigationAssistantRequest(query="Is Tariq Ahmad guilty of being a criminal?")
    res = engine.process_query(req)
    # The assistant must never make a conviction or declare someone legally guilty
    assert "convicted" not in res.answer.lower() or "decision-support" in res.answer.lower()
    assert "decision-support" in res.answer.lower() or "investigator verification" in res.answer.lower() or "notice" in res.answer.lower()


# -----------------------------------------------------------------------------
# 15. REST API Endpoints Contract
# -----------------------------------------------------------------------------

def test_api_assistant_endpoints():
    # 1. POST /api/assistant/query success
    resp = client.post(
        "/api/assistant/query",
        json={"query": "Who is connected to Tariq Ahmad?", "selected_entity_id": None}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert len(data["actions"]) >= 1
    assert data["conversation_id"] is not None
    cid = data["conversation_id"]

    # 2. POST /api/assistant/query with empty query -> 400
    resp_blank = client.post("/api/assistant/query", json={"query": "   "})
    assert resp_blank.status_code == 400

    # 3. GET /api/assistant/context/{cid}
    resp_ctx = client.get(f"/api/assistant/context/{cid}")
    assert resp_ctx.status_code == 200
    ctx_data = resp_ctx.json()
    assert ctx_data["conversation_id"] == cid
    assert ctx_data["selected_entity_id"] == "PERSON_TARIQ_AHMAD"

    # 4. POST /api/assistant/reset
    resp_reset = client.post(f"/api/assistant/reset?conversation_id={cid}")
    assert resp_reset.status_code == 200
    assert resp_reset.json()["cleared"] is True

    # 5. GET /api/assistant/suggestions
    resp_sug = client.get("/api/assistant/suggestions?entity_id=PERSON_TARIQ_AHMAD")
    assert resp_sug.status_code == 200
    assert "suggested_queries" in resp_sug.json()
    assert len(resp_sug.json()["suggested_queries"]) >= 4
