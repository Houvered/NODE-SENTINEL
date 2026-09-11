import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.core.graph_engine import BaseGraphEngine, get_graph_engine
from app.core.nlp_extractor import NLPExtractor
from app.models.graph_models import Edge, EdgeType, Node, NodeType
from app.models.schemas import IngestTextRequest

router = APIRouter(prefix="/ingest", tags=["ingestion"])


def _label(value: str) -> NodeType:
    try:
        return NodeType(value)
    except ValueError:
        return NodeType.PERSON


def ingest_sample_batch_data(graph: BaseGraphEngine):
    """Load the version-controlled demonstration graph dataset."""
    dataset_path = Path(__file__).resolve().parents[2] / "sample_data" / "demo_graph.json"
    with dataset_path.open(encoding="utf-8") as dataset_file:
        dataset = json.load(dataset_file)
    if graph.get_all_nodes():
        return {"status": "already_loaded", "total_graph_nodes": len(graph.get_all_nodes()), "total_graph_edges": len(graph.get_all_edges())}
    for item in dataset["nodes"]:
        graph.add_node(Node(item["id"], _label(item["label"]), item["name"], item.get("properties", {})))
    for item in dataset["edges"]:
        graph.add_edge(Edge(item["source"], item["target"], EdgeType(item["relationship"]), item.get("properties", {})))
    return {"status": "success", "total_graph_nodes": len(graph.get_all_nodes()), "total_graph_edges": len(graph.get_all_edges())}


@router.post("/batch")
def ingest_batch():
    return ingest_sample_batch_data(get_graph_engine())


@router.post("/demo/reset")
def reset_demo_data():
    """Restore the predictable demo graph after a presentation or experiment."""
    graph = get_graph_engine()
    graph.clear()
    return ingest_sample_batch_data(graph)


@router.post("/text")
def ingest_text(request: IngestTextRequest):
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="Text cannot be empty")
    extractor = NLPExtractor()
    entities = extractor.extract_entities(request.text)
    relationships = extractor.extract_triplets(request.text, entities)
    graph = get_graph_engine()
    for item in entities:
        graph.add_node(Node(item["id"], _label(item["label"]), item["name"], item.get("properties", {})))
    for item in relationships:
        if graph.get_node(item["source"]) and graph.get_node(item["target"]):
            graph.add_edge(Edge(item["source"], item["target"], EdgeType(item["relationship"]), item.get("properties", {})))
    return {"status": "success", "extracted_entities_count": len(entities), "extracted_relations_count": len(relationships), "entities": entities, "relationships": relationships}
