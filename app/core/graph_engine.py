"""Small, dependency-light graph storage used when Neo4j is not configured."""
from abc import ABC, abstractmethod
from collections import deque
from typing import Dict, List, Optional

import networkx as nx

from app.models.graph_models import Edge, Node, NodeType


class BaseGraphEngine(ABC):
    @abstractmethod
    def add_node(self, node: Node) -> Node: ...
    @abstractmethod
    def add_edge(self, edge: Edge) -> Edge: ...
    @abstractmethod
    def get_node(self, node_id: str) -> Optional[Node]: ...
    @abstractmethod
    def get_all_nodes(self) -> List[Node]: ...
    @abstractmethod
    def get_all_edges(self) -> List[Edge]: ...
    @abstractmethod
    def get_neighbors(self, node_id: str, depth: int = 1) -> Dict[str, List]: ...
    @abstractmethod
    def clear(self) -> None: ...


class NetworkXGraphEngine(BaseGraphEngine):
    def __init__(self) -> None:
        self.graph = nx.MultiDiGraph()
        self._nodes: Dict[str, Node] = {}
        self._edges: Dict[str, Edge] = {}

    def add_node(self, node: Node) -> Node:
        existing = self._nodes.get(node.id)
        if existing:
            existing.name = node.name or existing.name
            existing.label = node.label or existing.label
            existing.properties.update(node.properties or {})
            return existing
        self._nodes[node.id] = node
        self.graph.add_node(node.id)
        return node

    def add_edge(self, edge: Edge) -> Edge:
        if edge.source not in self._nodes or edge.target not in self._nodes:
            raise ValueError("Both edge endpoints must exist before adding an edge")
        self._edges[edge.id] = edge
        self.graph.add_edge(edge.source, edge.target, key=edge.id)
        return edge

    def get_node(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    def get_all_nodes(self) -> List[Node]:
        return list(self._nodes.values())

    def get_all_edges(self) -> List[Edge]:
        return list(self._edges.values())

    def get_neighbors(self, node_id: str, depth: int = 1) -> Dict[str, List]:
        if node_id not in self.graph or depth < 0:
            return {"nodes": [], "edges": []}
        seen = {node_id}
        queue = deque([(node_id, 0)])
        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for neighbor in set(self.graph.successors(current)) | set(self.graph.predecessors(current)):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, level + 1))
        return {
            "nodes": [self._nodes[n] for n in seen],
            "edges": [e for e in self._edges.values() if e.source in seen and e.target in seen],
        }

    def clear(self) -> None:
        self.graph.clear()
        self._nodes.clear()
        self._edges.clear()


_engine: Optional[NetworkXGraphEngine] = None


def get_graph_engine() -> NetworkXGraphEngine:
    global _engine
    if _engine is None:
        _engine = NetworkXGraphEngine()
    return _engine
