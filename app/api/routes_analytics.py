from fastapi import APIRouter, Query

from app.core.anomaly_detector import AnomalyDetector
from app.core.graph_analytics import GraphAnalytics
from app.core.graph_engine import get_graph_engine

router = APIRouter(prefix="/network/analytics", tags=["analytics"])


@router.get("/centrality")
def centrality(top_k: int = Query(20, ge=1, le=100)):
    graph = get_graph_engine()
    detector = AnomalyDetector(graph)
    results = GraphAnalytics(graph).get_ranked_influencers(top_k)
    for result in results:
        result["risk_score"] = detector.calculate_investigative_risk_score(result["node_id"]).overall_score
    return {"items": results}


@router.get("/communities")
def communities():
    graph = get_graph_engine()
    memberships = GraphAnalytics(graph).detect_communities()
    groups = {}
    for node in graph.get_all_nodes():
        cid = memberships.get(node.id, 0)
        groups.setdefault(str(cid), []).append(node.to_dict())
    return {"communities": groups, "count": len(groups)}
