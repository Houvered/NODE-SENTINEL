import logging
from typing import Dict, List, Any, Optional
import networkx as nx
from app.core.graph_engine import BaseGraphEngine, NetworkXGraphEngine
from app.models.graph_models import NodeType

logger = logging.getLogger(__name__)

def _safe_float(val: Any, default: float = 1.0) -> float:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    try:
        cleaned = str(val).replace("₹", "").replace(",", "").replace("$", "").strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return default


class GraphAnalytics:
    """Network Analytics Suite for computing Centrality metrics and Community Detection."""

    def __init__(self, graph_engine: BaseGraphEngine):
        self.graph_engine = graph_engine

    def _get_networkx_graph(self) -> nx.Graph:
        """Converts internal graph model to NetworkX Graph for analytics algorithms."""
        all_nodes = self.graph_engine.get_all_nodes()
        all_edges = self.graph_engine.get_all_edges()

        # Undirected simple graph for centrality/community computation
        G = nx.Graph()

        for n in all_nodes:
            lbl = n.label.value if hasattr(n.label, "value") else str(n.label)
            G.add_node(n.id, name=n.name, label=lbl, properties=n.properties)

        for e in all_edges:
            # Aggregate weights for parallel edges
            weight = _safe_float(e.properties.get("weight"), 1.0)
            if e.properties.get("amount") is not None:
                weight = max(0.01, _safe_float(e.properties["amount"], 1.0) / 1000.0)  # Normalized financial weight
            elif e.properties.get("frequency") is not None:
                weight = max(0.01, _safe_float(e.properties["frequency"], 1.0))

            if G.has_edge(e.source, e.target):
                G[e.source][e.target]["weight"] += weight
                G[e.source][e.target]["distance"] = 1.0 / max(G[e.source][e.target]["weight"], 0.001)
            else:
                dist = 1.0 / max(weight, 0.001)
                G.add_edge(e.source, e.target, weight=weight, distance=dist)

        return G

    def compute_centrality_metrics(
        self,
        max_exact_nodes: int = 800,
        approx_samples: int = 200,
    ) -> Dict[str, Dict[str, float]]:
        """Compute Degree, Betweenness Centrality, and PageRank.

        Exact betweenness is O(n*m) — unusable on production-size graphs
        (4k+ nodes took minutes). Above max_exact_nodes, betweenness falls
        back to a deterministic sampled approximation (seed=42); degree and
        PageRank stay exact. Small graphs are unaffected (exact path).
        """
        G = self._get_networkx_graph()
        if len(G) == 0:
            return {"degree": {}, "betweenness": {}, "pagerank": {}}

        degree_cent = nx.degree_centrality(G)
        if len(G) <= max_exact_nodes:
            betweenness_cent = nx.betweenness_centrality(G, weight="distance")
        else:
            logger.info(
                f"Graph has {len(G)} nodes; using sampled betweenness "
                f"(k={approx_samples}) for bounded latency.")
            betweenness_cent = nx.betweenness_centrality(
                G, k=min(approx_samples, len(G)), weight="distance", seed=42)

        try:
            pagerank_val = nx.pagerank(G, weight="weight")
        except Exception:
            pagerank_val = {n: 1.0 / len(G) for n in G.nodes()}

        return {
            "degree": degree_cent,
            "betweenness": betweenness_cent,
            "pagerank": pagerank_val
        }

    def detect_communities(self) -> Dict[str, int]:
        """Community detection using Louvain algorithm or Greedy Modularity fallback."""
        G = self._get_networkx_graph()
        if len(G) == 0:
            return {}

        communities = {}
        try:
            # Try Louvain community detection
            import networkx.algorithms.community as nx_comm
            community_sets = nx_comm.louvain_communities(G, weight="weight", seed=42)
            for cid, c_set in enumerate(community_sets):
                for node_id in c_set:
                    communities[node_id] = cid
        except Exception as e:
            logger.info(f"Louvain community detection fallback to greedy modularity ({e}).")
            try:
                import networkx.algorithms.community as nx_comm
                community_sets = nx_comm.greedy_modularity_communities(G, weight="weight")
                for cid, c_set in enumerate(community_sets):
                    for node_id in c_set:
                        communities[node_id] = cid
            except Exception:
                communities = {n: 0 for n in G.nodes()}

        return communities

    def get_ranked_influencers(self, top_k: int = 20) -> List[Dict[str, Any]]:
        """Get ranked list of key influencers and brokers with centrality scores."""
        metrics = self.compute_centrality_metrics()
        communities = self.detect_communities()
        nodes_dict = {n.id: n for n in self.graph_engine.get_all_nodes()}

        degree_dict = metrics["degree"]
        betweenness_dict = metrics["betweenness"]
        pagerank_dict = metrics["pagerank"]

        ranked_list = []
        for node_id, d_val in degree_dict.items():
            node = nodes_dict.get(node_id)
            if not node:
                continue

            lbl = node.label.value if isinstance(node.label, NodeType) else str(node.label)
            b_val = betweenness_dict.get(node_id, 0.0)
            pr_val = pagerank_dict.get(node_id, 0.0)
            comm_id = communities.get(node_id, 0)

            ranked_list.append({
                "node_id": node_id,
                "name": node.name or node_id,
                "node_type": lbl,
                "degree_centrality": round(d_val, 4),
                "betweenness_centrality": round(b_val, 4),
                "pagerank": round(pr_val, 4),
                "community_id": comm_id
            })

        # Sort primarily by betweenness centrality (brokers/mules) then degree
        ranked_list.sort(key=lambda x: (x["betweenness_centrality"], x["degree_centrality"]), reverse=True)
        return ranked_list[:top_k]
