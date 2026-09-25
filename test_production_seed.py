# -*- coding: utf-8 -*-
"""Regression tests for production seeding (incremental cutover).

Covers: missing-file fallback (never raises), PaySim cap enforcement,
email top-N selection, face manifest wiring. Small deterministic fixtures only.
"""
from __future__ import annotations

import csv
from pathlib import Path

from app.core import production_seed as ps
from app.core.graph_engine import get_graph_engine
from app.core.financial_analytics import get_financial_storage
from app.models.graph_models import NodeType


def _clean():
    get_graph_engine().clear()
    get_financial_storage().clear()


def test_missing_files_fall_back_silently(tmp_path):
    missing = tmp_path / "nope.csv"
    assert ps.seed_paysim_live(path=missing)["attempted"] is False
    assert ps.seed_email_cases(path=missing)["attempted"] is False
    assert ps.seed_face_watchlist(path=missing)["attempted"] is False
    summary = ps.seed_real_data_if_available(
        paysim_path=missing, email_path=missing, face_path=missing)
    assert summary["mode"] == "synthetic-fallback"


def test_paysim_cap_enforced(tmp_path):
    _clean()
    p = tmp_path / "seed.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["transaction_id", "timestamp", "sender", "receiver",
                    "amount", "currency", "transaction_type"])
        for i in range(10):
            w.writerow([f"T-{i}", "2024-01-02 10:00:00",
                        f"ACC900{i:03d}", f"ACC800{i:03d}", 100 + i, "INR", "TRANSFER"])
    res = ps.seed_paysim_live(path=p, cap=5)
    assert res["attempted"] is True
    assert res["capped"] is True
    assert res["records_added"] == 5
    _clean()


def test_email_top_n_selection(tmp_path):
    _clean()
    p = tmp_path / "scored.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["row", "Text", "Class", "fraud_score", "fraud_level", "indicators"])
        w.writerow([1, "hello there friend", "0", 5, "LOW", ""])
        w.writerow([2, "Dear Friend inheritance funds urgent", "1", 70, "CRITICAL", "Advance-fee"])
        w.writerow([3, "lottery winner claim prize now", "1", 45, "HIGH", "Lottery"])
        w.writerow([4, "meeting at noon tomorrow", "0", 0, "LOW", ""])
    res = ps.seed_email_cases(path=p, cap=2, threshold=35)
    assert res["cases_created"] == 2
    g = get_graph_engine()
    assert g.get_node("CASE_EMAIL_FRAUD_00002") is not None
    assert g.get_node("CASE_EMAIL_FRAUD_00003") is not None
    assert g.get_node("CASE_EMAIL_FRAUD_00001") is None
    _clean()


def test_face_manifest_wires_persons():
    _clean()
    manifest = Path("sample_data/face_enrollment_manifest.json")
    assert manifest.exists()
    res = ps.seed_face_watchlist(path=manifest)
    assert res["attempted"] is True
    assert res["persons_found"] == 31
    assert res["persons_created"] == 31
    g = get_graph_engine()
    persons = [n for n in g.get_all_nodes()
               if (n.label.value if hasattr(n.label, "value") else str(n.label)) == NodeType.PERSON.value]
    assert len(persons) == 31
    assert all(n.properties.get("face_enrollment") == "pending_images" for n in persons)
    _clean()
