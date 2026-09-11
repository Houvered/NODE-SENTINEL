from fastapi import APIRouter, HTTPException, Query

from app.core.anomaly_detector import AnomalyDetector
from app.core.graph_analytics import GraphAnalytics
from app.core.graph_engine import get_graph_engine
from app.models.graph_models import NodeType

router = APIRouter(tags=["network"])


def graph_payload(nodes, edges):
    return {"nodes": [{"id": n.id, "label": n.name, "group": n.label.value, "title": f"{n.label.value}: {n.name}", "properties": n.properties} for n in nodes], "edges": [{"id": e.id, "from": e.source, "to": e.target, "label": e.relationship.value, "arrows": "to", "properties": e.properties} for e in edges], "stats": {"nodes": len(nodes), "edges": len(edges)}}


@router.get("/network/graph")
def get_graph():
    graph = get_graph_engine()
    return graph_payload(graph.get_all_nodes(), graph.get_all_edges())


@router.get("/network/overview")
def network_overview():
    """Presentation-friendly summary derived from the current live graph."""
    graph = get_graph_engine()
    nodes = graph.get_all_nodes()
    by_type = {node_type.value: 0 for node_type in NodeType}
    for node in nodes:
        by_type[node.label.value] = by_type.get(node.label.value, 0) + 1
    alerts = AnomalyDetector(graph).get_all_alerts()
    high_risk = [node for node in nodes if str(node.properties.get("risk_tag", "")).upper() in {"HIGH", "CRITICAL"}]
    return {"entity_count": len(nodes), "relationship_count": len(graph.get_all_edges()), "high_risk_count": len(high_risk), "alert_count": len(alerts), "by_type": by_type}


@router.get("/network/search")
def search_network(query: str = Query(min_length=1), depth: int = Query(1, ge=0, le=4)):
    graph = get_graph_engine()
    match_ids = [n.id for n in graph.get_all_nodes() if query.lower() in n.name.lower() or query.lower() in n.id.lower()]
    if not match_ids:
        return graph_payload([], [])
    selected_nodes, selected_edges = {}, {}
    for node_id in match_ids:
        neighborhood = graph.get_neighbors(node_id, depth)
        selected_nodes.update({n.id: n for n in neighborhood["nodes"]})
        selected_edges.update({e.id: e for e in neighborhood["edges"]})
    return graph_payload(list(selected_nodes.values()), list(selected_edges.values()))


@router.get("/entity/{entity_id}/dossier")
def dossier(entity_id: str):
    graph = get_graph_engine()
    node = graph.get_node(entity_id)
    if not node:
        raise HTTPException(status_code=404, detail="Entity not found")
    analytics = GraphAnalytics(graph)
    metrics = analytics.compute_centrality_metrics()
    communities = analytics.detect_communities()
    detector = AnomalyDetector(graph)
    neighborhood = graph.get_neighbors(entity_id)
    return {"entity_id": node.id, "name": node.name, "type": node.label.value, "properties": node.properties, "risk": detector.calculate_investigative_risk_score(entity_id).dict(), "centrality": {key: round(values.get(entity_id, 0.0), 4) for key, values in metrics.items()}, "community_id": communities.get(entity_id), "connected_nodes": [n.to_dict() for n in neighborhood["nodes"] if n.id != entity_id], "active_alerts": [a.dict() for a in detector.get_all_alerts() if entity_id in a.entities]}
