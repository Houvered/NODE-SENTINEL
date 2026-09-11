import os
import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.routes_ingest import router as ingest_router
from app.api.routes_graph import router as graph_router
from app.api.routes_analytics import router as analytics_router
from app.api.routes_alerts import router as alerts_router
from app.core.graph_engine import get_graph_engine
from app.api.routes_ingest import ingest_sample_batch_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="AI-Assisted Criminal Network Analysis & Knowledge Graph System Engine",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(ingest_router, prefix=settings.API_V1_STR)
app.include_router(graph_router, prefix=settings.API_V1_STR)
app.include_router(analytics_router, prefix=settings.API_V1_STR)
app.include_router(alerts_router, prefix=settings.API_V1_STR)

# Static Files Setup
static_path = os.path.join(settings.BASE_DIR, "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

@app.on_event("startup")
def on_startup_auto_ingest():
    """Auto-load synthetic dataset on server startup if graph engine is empty."""
    graph = get_graph_engine()
    nodes = graph.get_all_nodes()
    if len(nodes) == 0:
        logger.info("Graph is empty on startup. Auto-ingesting synthetic sample dataset...")
        try:
            res = ingest_sample_batch_data(graph)
            logger.info(f"Auto-ingested sample data: {res['total_graph_nodes']} nodes, {res['total_graph_edges']} edges created.")
        except Exception as e:
            logger.error(f"Failed to auto-ingest sample dataset: {e}")
    else:
        logger.info(f"Graph loaded with {len(nodes)} pre-existing nodes.")

@app.get("/", include_in_schema=False)
def serve_dashboard():
    """Serve single-page investigator dashboard frontend."""
    index_file = os.path.join(static_path, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "AI Criminal Network Engine API running. Dashboard file not found."}
