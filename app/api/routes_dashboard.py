# -*- coding: utf-8 -*-
"""Dashboard summary endpoint for NODE SENTINEL.

Single real-data aggregate powering the ops-console home view:
entity/case/risk counts, financial volume + per-day series (for sparklines),
email-fraud signal counts, and the recent audit activity feed.

Intentionally NOT audit-logged: it fires on every dashboard load and would
spam the immutable trail.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter

from app.core.audit_logger import audit_logger
from app.core.financial_analytics import get_financial_storage
from app.core.graph_engine import get_graph_engine

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _label_of(node) -> str:
    return node.label.value if hasattr(node.label, "value") else str(node.label)


@router.get("/summary")
def dashboard_summary() -> Dict[str, Any]:
    graph = get_graph_engine()
    nodes = graph.get_all_nodes()
    edges = graph.get_all_edges()

    by_type: Counter = Counter()
    high_risk = 0
    fraud_signals = 0
    for n in nodes:
        lbl = _label_of(n)
        by_type[lbl] += 1
        tag = str((n.properties or {}).get("risk_tag", "")).upper()
        if tag in ("CRITICAL", "HIGH"):
            high_risk += 1
        lvl = str((n.properties or {}).get("fraud_level", "")).upper()
        if lvl in ("CRITICAL", "HIGH"):
            fraud_signals += 1

    fin = get_financial_storage().get_all_records()
    per_day: Counter = Counter()
    volume = 0.0
    for r in fin:
        volume += r.amount or 0.0
        try:
            per_day[r.timestamp.date().isoformat()] += 1
        except Exception:
            continue
    series = [{"day": d, "count": c} for d, c in sorted(per_day.items())][-14:]

    activity: List[Dict[str, Any]] = []
    for entry in audit_logger.get_recent_logs(limit=8):
        activity.append({
            "timestamp": entry.timestamp,
            "action": entry.action,
            "actor": entry.username or "anonymous",
            "resource": entry.resource_id or entry.resource_type or "",
            "status": entry.status,
        })

    return {
        "entities": len(nodes),
        "links": len(edges),
        "by_type": dict(by_type),
        "cases": int(by_type.get("Case", 0) + by_type.get("CASE", 0)),
        "persons": int(by_type.get("Person", 0) + by_type.get("PERSON", 0)),
        "high_risk": high_risk,
        "fraud_signals": fraud_signals,
        "financial": {
            "transactions": len(fin),
            "volume": round(volume, 2),
            "per_day": series,
        },
        "activity": activity,
        "generated_at": datetime.now().isoformat(),
    }
