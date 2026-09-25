# -*- coding: utf-8 -*-
"""Tests for the dashboard summary endpoint (ops-console home aggregates)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.core.graph_engine import get_graph_engine
from app.api.routes_ingest import load_dataset_by_name
from app.core.financial_analytics import get_financial_storage

client = TestClient(app)


def _seed():
    g = get_graph_engine()
    g.clear()
    load_dataset_by_name(g, "syndicate_network.json")
    get_financial_storage().clear()


def test_dashboard_summary_shape_and_counts():
    _seed()
    res = client.get("/api/dashboard/summary")
    assert res.status_code == 200
    d = res.json()
    for key in ("entities", "links", "by_type", "cases", "persons",
                "high_risk", "fraud_signals", "financial", "activity"):
        assert key in d, f"missing {key}"
    assert d["entities"] == len(get_graph_engine().get_all_nodes())
    assert d["links"] == len(get_graph_engine().get_all_edges())
    assert d["entities"] > 0
    assert isinstance(d["financial"]["per_day"], list)
    assert isinstance(d["activity"], list)


def test_dashboard_summary_empty_graph():
    get_graph_engine().clear()
    get_financial_storage().clear()
    res = client.get("/api/dashboard/summary")
    assert res.status_code == 200
    d = res.json()
    assert d["entities"] == 0
    assert d["financial"]["transactions"] == 0
    _seed()
