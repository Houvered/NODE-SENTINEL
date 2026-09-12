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
    except (ValueError, TypeError):
        return NodeType.PERSON


def _edge_type(value: str) -> EdgeType:
    try:
        return EdgeType(value)
    except (ValueError, TypeError):
        return EdgeType.CALLS


def load_dataset_by_name(graph: BaseGraphEngine, dataset_name: str = "demo_graph"):
    """Load a named dataset from the sample_data directory."""
    clean_name = dataset_name if dataset_name.endswith(".json") else f"{dataset_name}.json"
    dataset_path = Path(__file__).resolve().parents[2] / "sample_data" / clean_name
    if not dataset_path.exists():
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_name}' not found")
    with dataset_path.open(encoding="utf-8") as dataset_file:
        dataset = json.load(dataset_file)
    for item in dataset["nodes"]:
        graph.add_node(Node(item["id"], _label(item["label"]), item["name"], item.get("properties", {})))
    for item in dataset["edges"]:
        graph.add_edge(Edge(item["source"], item["target"], _edge_type(item["relationship"]), item.get("properties", {})))
    return {
        "status": "success",
        "dataset": clean_name,
        "total_graph_nodes": len(graph.get_all_nodes()),
        "total_graph_edges": len(graph.get_all_edges())
    }


def ingest_sample_batch_data(graph: BaseGraphEngine):
    """Load default demonstration graph dataset."""
    if graph.get_all_nodes():
        return {"status": "already_loaded", "total_graph_nodes": len(graph.get_all_nodes()), "total_graph_edges": len(graph.get_all_edges())}
    return load_dataset_by_name(graph, "demo_graph.json")


@router.get("/datasets")
def list_available_datasets():
    """List all available graph datasets in sample_data directory."""
    data_dir = Path(__file__).resolve().parents[2] / "sample_data"
    files = [f.stem for f in sorted(data_dir.glob("*.json"))]
    return {"datasets": files}


@router.post("/dataset/{dataset_name}")
def switch_dataset(dataset_name: str):
    """Clear and load a specified dataset (e.g. demo_graph or syndicate_network)."""
    graph = get_graph_engine()
    graph.clear()
    return load_dataset_by_name(graph, dataset_name)


@router.post("/batch")
def ingest_batch():
    return ingest_sample_batch_data(get_graph_engine())


@router.post("/demo/reset")
def reset_demo_data():
    """Restore the predictable demo graph after a presentation or experiment."""
    graph = get_graph_engine()
    graph.clear()
    return load_dataset_by_name(graph, "demo_graph.json")


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
            graph.add_edge(Edge(item["source"], item["target"], _edge_type(item["relationship"]), item.get("properties", {})))
    return {"status": "success", "extracted_entities_count": len(entities), "extracted_relations_count": len(relationships), "entities": entities, "relationships": relationships}
