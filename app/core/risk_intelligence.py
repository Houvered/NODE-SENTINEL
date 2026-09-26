# -*- coding: utf-8 -*-
"""
Risk Intelligence facade for NODE SENTINEL.

Canonical module documented in ARCHITECTURE.md (Layer 8). Thin, stable
facade over AnomalyDetector so both import paths work:

    from app.core.risk_intelligence import RiskIntelligenceEngine, evaluate_entity_risk
    from app.core.anomaly_detector import AnomalyDetector  # legacy, still valid
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.anomaly_detector import AnomalyDetector
from app.core.graph_engine import get_graph_engine
from app.models.risk_models import RiskIntelligenceResult


class RiskIntelligenceEngine(AnomalyDetector):
    """Explainable additive risk scoring engine (0-100, evidence breakdown)."""

    def evaluate(self, entity_id: str) -> RiskIntelligenceResult:
        return self.calculate_investigative_risk_score(entity_id)

    def summarize(self, top_k: int = 20) -> List[Dict[str, Any]]:
        """Triage ranking of riskiest Person entities.

        Performance: computes graph degree ONCE (O(V+E)) and scores with a
        lightweight heuristic — never calls the full per-entity scorer
        (which recomputes sampled betweenness each time and is O(n) slower).
        Full explainable breakdowns remain available via GET /risk/{id}.
        """
        graph = self.graph_engine or get_graph_engine()
        persons = []
        for node in graph.get_all_nodes():
            label = node.label.value if hasattr(node.label, "value") else str(node.label)
            if label.lower() == "person":
                persons.append(node)
        if not persons:
            return []
        # Single degree pass over edges (cheap even at 4k+ nodes).
        from collections import Counter
        deg = Counter()
        try:
            for e in graph.get_all_edges():
                deg[e.source] += 1
                deg[e.target] += 1
        except Exception:
            pass
        max_deg = max((deg.get(n.id, 0) for n in persons), default=1) or 1
        ranked = []
        for n in persons:
            props = n.properties or {}
            score = 0.0
            tag = str(props.get("risk_tag", props.get("risk_level", ""))).upper()
            if tag in ("CRITICAL", "HIGH"):
                score += 35
            elif tag in ("MEDIUM", "ELEVATED"):
                score += 15
            role = str(props.get("role", "")).lower()
            if role and any(r in role for r in ("kingpin", "coordinator", "mastermind", "boss", "head", "syndicate", "hawala", "broker", "facilitator")):
                score += 15
            score += round(20 * deg.get(n.id, 0) / max_deg, 1)  # hub proxy, max +20
            if props.get("fraud_level") and str(props["fraud_level"]).upper() in ("CRITICAL", "HIGH"):
                score += 10
            ranked.append({
                "entity_id": n.id,
                "entity_name": n.name,
                "entity_type": "Person",
                "risk_score": round(min(score, 100.0), 1),
                "risk_level": "HIGH" if score >= 75 else ("ELEVATED" if score >= 50 else ("MODERATE" if score >= 25 else "LOW")),
                "top_factor": "registry hit" if score >= 35 else ("network hub" if deg.get(n.id, 0) >= max_deg / 2 else "triage rank"),
            })
        ranked.sort(key=lambda r: r["risk_score"], reverse=True)
        k = max(1, min(top_k, 100))
        return ranked[:k]


def evaluate_entity_risk(entity_id: str, graph=None) -> RiskIntelligenceResult:
    return RiskIntelligenceEngine(graph_engine=graph or get_graph_engine()).evaluate(entity_id)


def risk_summary(top_k: int = 20, graph=None) -> Dict[str, Any]:
    engine = RiskIntelligenceEngine(graph_engine=graph or get_graph_engine())
    items = engine.summarize(top_k=top_k)
    return {"count": len(items), "items": items}
