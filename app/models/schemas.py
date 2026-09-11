from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from app.models.graph_models import NodeType, EdgeType

class VisNode(BaseModel):
    id: str
    label: str
    group: str
    shape: str = "dot"
    color: Optional[Dict[str, Any]] = None
    title: Optional[str] = None
    risk_score: Optional[float] = 0.0
    properties: Dict[str, Any] = Field(default_factory=dict)

class VisEdge(BaseModel):
    id: str
    from_node: str = Field(..., alias="from")
    to_node: str = Field(..., alias="to")
    label: str
    title: Optional[str] = None
    arrows: str = "to"
    properties: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        populate_by_name = True

class VisGraphResponse(BaseModel):
    nodes: List[VisNode]
    edges: List[VisEdge]
    stats: Dict[str, int] = Field(default_factory=dict)

class IngestTextRequest(BaseModel):
    text: str = Field(..., description="Raw unstructured FIR or investigation text report")
    source_case_id: Optional[str] = Field(None, description="Optional Case ID to associate with the text")

class IngestTextResponse(BaseModel):
    status: str
    extracted_entities_count: int
    extracted_relations_count: int
    entities: List[Dict[str, Any]]
    relationships: List[Dict[str, Any]]

class SearchQuery(BaseModel):
    query: str
    node_type: Optional[str] = None
    max_results: int = 50

class CentralityRank(BaseModel):
    node_id: str
    name: str
    node_type: str
    degree_centrality: float
    betweenness_centrality: float
    pagerank: float
    risk_score: float
    community_id: Optional[int] = None

class AlertItem(BaseModel):
    id: str
    alert_type: str
    severity: str
    title: str
    description: str
    entities: List[str]
    timestamp: str
    evidence: Dict[str, Any] = Field(default_factory=dict)

class RiskScoreBreakdown(BaseModel):
    overall_score: float
    severity_level: str
    factors: List[Dict[str, Any]]

class EntityDossier(BaseModel):
    entity_id: str
    name: str
    type: str
    properties: Dict[str, Any]
    risk: RiskScoreBreakdown
    centrality: Dict[str, float]
    community_id: Optional[int]
    connected_nodes: List[Dict[str, Any]]
    active_alerts: List[AlertItem]
