import logging
import numpy as np
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.core.graph_engine import BaseGraphEngine
from app.core.graph_analytics import GraphAnalytics
from app.models.graph_models import NodeType, EdgeType
from app.models.schemas import AlertItem, RiskScoreBreakdown

logger = logging.getLogger(__name__)

def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    try:
        cleaned = str(val).replace("₹", "").replace(",", "").replace("$", "").strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return default


class AnomalyDetector:
    """Statistical anomaly detector & Investigative Risk Score Engine."""

    def __init__(self, graph_engine: BaseGraphEngine):
        self.graph_engine = graph_engine
        self.analytics = GraphAnalytics(graph_engine)

    def detect_call_bursts(self) -> List[AlertItem]:
        """Flag CDR relationships with communication frequency > 3x mean."""
        edges = self.graph_engine.get_all_edges()
        call_edges = [
            e for e in edges
            if (e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)) == EdgeType.CALLS.value
        ]

        if not call_edges or len(call_edges) < 2:
            return []

        frequencies = []
        for e in call_edges:
            raw_freq = e.properties.get("frequency", e.properties.get("count", 1))
            frequencies.append(_safe_float(raw_freq, 1.0))

        if not frequencies:
            return []

        mean_freq = float(np.mean(frequencies))
        burst_threshold = max(3.0, mean_freq * 3.0)

        alerts = []
        for idx, e in enumerate(call_edges):
            raw_freq = e.properties.get("frequency", e.properties.get("count", 1))
            freq = _safe_float(raw_freq, 1.0)
            if freq >= burst_threshold:
                source_node = self.graph_engine.get_node(e.source)
                target_node = self.graph_engine.get_node(e.target)
                src_name = source_node.name if source_node else e.source
                tgt_name = target_node.name if target_node else e.target

                alerts.append(AlertItem(
                    id=f"ALERT_CALL_BURST_{idx}_{e.id}",
                    alert_type="CALL_BURST",
                    severity="HIGH",
                    title="Suspicious Call Burst Detected",
                    description=f"Communication frequency between '{src_name}' and '{tgt_name}' ({int(freq)} calls) exceeds 3x mean frequency ({mean_freq:.1f}).",
                    entities=[e.source, e.target],
                    timestamp=e.properties.get("timestamp", datetime.now().isoformat()),
                    evidence={
                        "observed_frequency": freq,
                        "network_mean_frequency": round(mean_freq, 2),
                        "multiplier": round(freq / (mean_freq + 0.01), 2)
                    }
                ))

        return alerts

    def detect_financial_anomalies(self) -> List[AlertItem]:
        """Flag financial transactions > 2 sigma above user/network average."""
        edges = self.graph_engine.get_all_edges()
        money_edges = [
            e for e in edges
            if (e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)) == EdgeType.TRANSFERRED_MONEY.value
        ]

        if not money_edges:
            return []

        amounts = []
        for e in money_edges:
            amt = _safe_float(e.properties.get("amount", 0.0))
            if amt > 0:
                amounts.append(amt)

        if not amounts or len(amounts) < 2:
            return []

        mean_amt = float(np.mean(amounts))
        std_amt = float(np.std(amounts))

        # If zero or negligible variance, cannot statistically identify an outlier spike
        if std_amt < 1e-4:
            return []

        threshold = mean_amt + (2.0 * std_amt)

        alerts = []
        for idx, e in enumerate(money_edges):
            amt = _safe_float(e.properties.get("amount", 0.0))
            if amt >= threshold and amt > mean_amt and amt > 0:
                sender = self.graph_engine.get_node(e.source)
                receiver = self.graph_engine.get_node(e.target)
                src_name = sender.name if sender else e.source
                rec_name = receiver.name if receiver else e.target
                z_score = (amt - mean_amt) / (std_amt + 0.01)

                alerts.append(AlertItem(
                    id=f"ALERT_FINANCIAL_SPIKE_{idx}_{e.id}",
                    alert_type="FINANCIAL_ANOMALY",
                    severity="CRITICAL",
                    title="High-Value Financial Transaction Anomaly",
                    description=f"Transaction of ₹{amt:,.2f} from '{src_name}' to '{rec_name}' is {z_score:.1f}σ above average.",
                    entities=[e.source, e.target],
                    timestamp=e.properties.get("timestamp", datetime.now().isoformat()),
                    evidence={
                        "amount": amt,
                        "mean_amount": round(mean_amt, 2),
                        "z_score": round(z_score, 2),
                        "tx_id": e.properties.get("tx_id", "N/A")
                    }
                ))

        return alerts

    def detect_colocation_clusters(self) -> List[AlertItem]:
        """Flag co-location clusters where multiple individuals appear at same location in tight window."""
        edges = self.graph_engine.get_all_edges()

        loc_edges = [
            e for e in edges
            if (e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)) == EdgeType.LOCATED_AT.value
        ]

        # Group people by location ID, robust to edge orientation
        loc_to_people: Dict[str, List[str]] = {}
        for e in loc_edges:
            src_node = self.graph_engine.get_node(e.source)
            tgt_node = self.graph_engine.get_node(e.target)
            src_lbl = src_node.label.value if src_node and hasattr(src_node.label, "value") else (str(src_node.label) if src_node else "")
            tgt_lbl = tgt_node.label.value if tgt_node and hasattr(tgt_node.label, "value") else (str(tgt_node.label) if tgt_node else "")

            if tgt_lbl == NodeType.LOCATION.value:
                loc_id, person_id = e.target, e.source
            elif src_lbl == NodeType.LOCATION.value:
                loc_id, person_id = e.source, e.target
            else:
                loc_id, person_id = e.target, e.source
            loc_to_people.setdefault(loc_id, []).append(person_id)

        alerts = []
        for loc_id, person_ids in loc_to_people.items():
            unique_people = list(set(person_ids))
            if len(unique_people) >= 3:
                loc_node = self.graph_engine.get_node(loc_id)
                loc_name = loc_node.name if loc_node else loc_id

                person_names = []
                for pid in unique_people:
                    pn = self.graph_engine.get_node(pid)
                    person_names.append(pn.name if pn else pid)

                alerts.append(AlertItem(
                    id=f"ALERT_COLOCATION_{loc_id}",
                    alert_type="COLOCATION_CLUSTER",
                    severity="HIGH",
                    title="Co-Location Cluster Alert",
                    description=f"Cluster of {len(unique_people)} suspects ({', '.join(person_names[:3])}) co-located at '{loc_name}'.",
                    entities=[loc_id] + unique_people,
                    timestamp=datetime.now().isoformat(),
                    evidence={
                        "location_id": loc_id,
                        "location_name": loc_name,
                        "co_located_count": len(unique_people),
                        "suspects": person_names
                    }
                ))

        return alerts

    def get_all_alerts(self) -> List[AlertItem]:
        """Aggregate all graph anomaly alerts."""
        call_alerts = self.detect_call_bursts()
        fin_alerts = self.detect_financial_anomalies()
        loc_alerts = self.detect_colocation_clusters()
        return call_alerts + fin_alerts + loc_alerts

    def calculate_investigative_risk_score(self, entity_id: str) -> RiskScoreBreakdown:
        """Calculate composite 0-100 Investigative Risk Score with explainable evidence breakdown."""
        node = self.graph_engine.get_node(entity_id)
        if not node:
            return RiskScoreBreakdown(overall_score=0.0, severity_level="LOW", factors=[{"factor": "Unknown Node", "points": 0.0}])

        factors = []
        raw_score = 0.0
        node_lbl = node.label.value if hasattr(node.label, "value") else str(node.label)

        # 1. Base Tag Risk
        tagged_risk = node.properties.get("risk_tag", node.properties.get("risk_level", "LOW")).upper()
        if tagged_risk in ("HIGH", "CRITICAL"):
            raw_score += 35.0
            factors.append({"factor": "Tagged High-Risk Suspect in Police Registry", "points": 35.0})
        elif tagged_risk == "MEDIUM":
            raw_score += 15.0
            factors.append({"factor": "Tagged Medium-Risk Person of Interest", "points": 15.0})

        # 2. Centrality Factor (Betweenness/Degree)
        centrality_data = self.analytics.compute_centrality_metrics()
        b_score = centrality_data["betweenness"].get(entity_id, 0.0)
        d_score = centrality_data["degree"].get(entity_id, 0.0)

        if b_score > 0.1:
            points = min(30.0, b_score * 100.0)
            raw_score += points
            factors.append({"factor": f"High Betweenness Centrality Broker/Mule ({b_score:.2f})", "points": round(points, 1)})
        elif d_score > 0.15:
            points = min(20.0, d_score * 50.0)
            raw_score += points
            factors.append({"factor": f"High Direct Degree Centrality ({d_score:.2f})", "points": round(points, 1)})

        # 3. Active Anomaly Alerts Involvement
        all_alerts = self.get_all_alerts()
        entity_alerts = [a for a in all_alerts if entity_id in a.entities]

        for alert in entity_alerts:
            if alert.alert_type == "FINANCIAL_ANOMALY":
                raw_score += 25.0
                factors.append({"factor": f"Involved in Financial Spike Anomaly: {alert.title}", "points": 25.0})
            elif alert.alert_type == "CALL_BURST":
                raw_score += 15.0
                factors.append({"factor": f"Involved in Call Burst Anomaly: {alert.title}", "points": 15.0})
            elif alert.alert_type == "COLOCATION_CLUSTER":
                raw_score += 10.0
                if node_lbl == NodeType.LOCATION.value:
                    factors.append({"factor": "Identified High-Density Suspect Meeting Hotspot", "points": 10.0})
                else:
                    factors.append({"factor": "Co-located in High-Density Suspect Cluster", "points": 10.0})

        # 4. Case Linkage Factor (for non-case entities)
        if node_lbl != NodeType.CASE.value:
            neighbors_info = self.graph_engine.get_neighbors(entity_id, depth=1)
            linked_cases = [
                n for n in neighbors_info.get("nodes", [])
                if (n.label.value if hasattr(n.label, "value") else str(n.label)) == NodeType.CASE.value and n.id != entity_id
            ]
            if linked_cases:
                points = min(20.0, len(linked_cases) * 10.0)
                raw_score += points
                case_names = [c.name for c in linked_cases]
                factors.append({"factor": f"Directly Linked to {len(linked_cases)} Police Case(s) ({', '.join(case_names)})", "points": round(points, 1)})

        final_score = min(100.0, max(0.0, raw_score))

        if final_score >= 70.0:
            severity = "CRITICAL"
        elif final_score >= 45.0:
            severity = "HIGH"
        elif final_score >= 25.0:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        return RiskScoreBreakdown(
            overall_score=round(final_score, 1),
            severity_level=severity,
            factors=factors
        )
