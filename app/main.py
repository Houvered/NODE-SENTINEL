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
from app.api.routes_search import router as search_router
from app.api.routes_cdr import router as cdr_router
from app.api.routes_financial import router as financial_router
from app.api.routes_timeline import router as timeline_router
from app.api.routes_risk import router as risk_router
from app.api.routes_assistant import router as assistant_router
from app.api.routes_dashboard import router as dashboard_router
from app.api.routes_reports import router as reports_router
from app.api.routes_auth import router as auth_router
from app.api.routes_users import router as users_router
from app.api.routes_audit import router as audit_router
from app.api.routes_cases import router as cases_router
from app.api.routes_case_documents import router as case_documents_router
from app.api.routes_case_datasets import router as case_datasets_router
from app.api.routes_case_review import router as case_review_router
from app.api.routes_case_assistant import router as case_assistant_router
from app.api.routes_case_reports import router as case_reports_router
from app.core.audit_logger import audit_logger
from app.models.audit_models import AuditAction
from app.core.graph_engine import get_graph_engine
from app.core.face_storage import get_face_storage
from app.core.demo_face_data import seed_demo_face_database
from app.core.production_seed import seed_real_data_if_available
from app.api.routes_ingest import load_dataset_by_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

from app.api.routes_ingest import load_dataset_by_name
from contextlib import asynccontextmanager

_seed_summary: dict = {"mode": "not-started"}


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """Auto-load synthetic dataset on server startup if graph engine is empty and record startup audit."""
    audit_logger.log(
        action=AuditAction.SYSTEM_STARTUP,
        status="SUCCESS",
        details={"version": "1.0.0", "project": settings.PROJECT_NAME},
    )
    graph = get_graph_engine()
    nodes = graph.get_all_nodes()
    if len(nodes) == 0:
        logger.info("Graph is empty on startup. Auto-ingesting synthetic sample dataset...")
        try:
            res = load_dataset_by_name(graph, "syndicate_network.json")
            logger.info(f"Auto-ingested sample data: {res['total_graph_nodes']} nodes, {res['total_graph_edges']} edges created.")
        except Exception as e:
            logger.error(f"Failed to auto-ingest sample dataset: {e}")
    else:
        logger.info(f"Graph loaded with {len(nodes)} pre-existing nodes.")

    # Initialize demo face storage
    try:
        f_storage = get_face_storage()
        if len(f_storage.list_identities()) == 0:
            seed_demo_face_database(f_storage)
            logger.info(f"Auto-seeded demo face database with {len(f_storage.list_identities())} identities.")
    except Exception as e:
        logger.warning(f"Could not auto-seed face database: {e}")

    # Wire locally-available real data (PaySim live seed, scored emails,
    # face watchlist manifest); falls back silently when artifacts are absent.
    try:
        seed_summary = seed_real_data_if_available(graph)
        _seed_summary.clear()
        _seed_summary.update(seed_summary)
        logger.info(f"Production seeding mode={seed_summary['mode']}: "
                    f"paysim={seed_summary['paysim'].get('records_added', 0)} "
                    f"email_cases={seed_summary['email'].get('cases_created', 0)} "
                    f"watchlist_persons={seed_summary['faces'].get('persons_created', 0)}")
    except Exception as e:
        logger.warning(f"Production seeding failed, using synthetic fallback: {e}")
        _seed_summary.clear()
        _seed_summary.update({"mode": "synthetic-fallback"})

    # Initialize demo financial storage (synthetic fallback only when no
    # real PaySim live seed was ingested above).
    try:
        from app.core.financial_analytics import get_financial_storage, get_financial_service
        from app.core.financial_parser import FinancialParser
        f_store = get_financial_storage()
        paysim_live = bool(_seed_summary.get("paysim", {}).get("records_added"))
        if not paysim_live and len(f_store.get_all_records()) == 0:
            demo_csv_path = os.path.join(settings.BASE_DIR, "sample_data", "demo_financial.csv")
            if os.path.exists(demo_csv_path):
                with open(demo_csv_path, "rb") as df:
                    records, _, _, _ = FinancialParser.parse_csv(df.read(), source_name="demo_financial.csv")
                    f_svc = get_financial_service()
                    f_svc.ingest_records_into_graph(records)
                    logger.info(f"Auto-ingested {len(records)} demo financial records.")
    except Exception as e:
        logger.warning(f"Could not auto-seed demo financial data: {e}")

    # Unify ALL sample sources into one connected graph: merge demo_graph
    # (Ring B), auto-ingest demo CDR bursts, bridge PaySim/hawala/shell
    # accounts, stand up Ring C operators, and link every scored email
    # fraud case to handlers across the three criminal rings.
    try:
        from app.core.unified_seed import run_unified_seed
        _seed_summary["unified"] = run_unified_seed(graph)
        rings = _seed_summary["unified"].get("rings", [])
        logger.info("Unified seed rings_ok=%s nodes=%s edges=%s :: %s",
                    _seed_summary["unified"].get("rings_ok"),
                    _seed_summary["unified"].get("nodes"),
                    _seed_summary["unified"].get("edges"),
                    "; ".join(f"{r['label']}:{r.get('person_count', 0)}p/{r.get('status')}" for r in rings))
    except Exception as e:
        logger.warning(f"Unified seeding failed, continuing with base graph: {e}")

    yield

    audit_logger.log(
        action=AuditAction.SYSTEM_SHUTDOWN,
        status="SUCCESS",
        details={"project": settings.PROJECT_NAME},
    )


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="AI-Assisted Criminal Network Analysis & Knowledge Graph System Engine",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS Setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS if settings.CORS_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(users_router, prefix=settings.API_V1_STR)
app.include_router(audit_router, prefix=settings.API_V1_STR)
app.include_router(ingest_router, prefix=settings.API_V1_STR)
app.include_router(graph_router, prefix=settings.API_V1_STR)
app.include_router(analytics_router, prefix=settings.API_V1_STR)
app.include_router(alerts_router, prefix=settings.API_V1_STR)
app.include_router(search_router, prefix=settings.API_V1_STR)
# Alias mount for bare /search + /face/* paths (hidden from schema to
# avoid duplicate operation IDs with the canonical /api mount above).
app.include_router(search_router, prefix="", include_in_schema=False)
app.include_router(cdr_router, prefix=settings.API_V1_STR)
app.include_router(financial_router, prefix=settings.API_V1_STR)
app.include_router(timeline_router, prefix=settings.API_V1_STR)
app.include_router(risk_router, prefix=settings.API_V1_STR)
app.include_router(assistant_router, prefix=settings.API_V1_STR)
app.include_router(dashboard_router, prefix=settings.API_V1_STR)
app.include_router(reports_router, prefix=settings.API_V1_STR)
app.include_router(cases_router, prefix=settings.API_V1_STR)
app.include_router(case_documents_router, prefix=settings.API_V1_STR)
app.include_router(case_datasets_router, prefix=settings.API_V1_STR)
app.include_router(case_review_router, prefix=settings.API_V1_STR)
app.include_router(case_assistant_router, prefix=settings.API_V1_STR)
app.include_router(case_reports_router, prefix=settings.API_V1_STR)


# Static Files Setup
static_path = os.path.join(settings.BASE_DIR, "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# NOTE: sample_data/ is intentionally NOT mounted. It contains users.json
# (password hashes) and audit_log.json (operational trail) which must never
# be served over HTTP. Use authenticated /api/* endpoints instead.


@app.get("/health", tags=["system"])
def health_check():
    """System health check endpoint for monitoring probes."""
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": "1.0.0",
        "database": "active",
    }


@app.get("/api/health", tags=["system"], include_in_schema=False)
def health_check_alias():
    """Alias for reverse proxies / Render health checks under /api."""
    return health_check()


@app.get("/ready", tags=["system"])
def readiness_check():
    """Readiness probe: graph counts + seeding mode (no secrets)."""
    try:
        graph = get_graph_engine()
        node_count = len(graph.get_all_nodes())
        edge_count = len(graph.get_all_edges())
    except Exception:
        node_count, edge_count = 0, 0
    return {
        "status": "ready" if node_count else "empty",
        "nodes": node_count,
        "edges": edge_count,
        "seed_mode": _seed_summary.get("mode", "unknown"),
        "service": settings.PROJECT_NAME,
    }


@app.get("/api/graph", tags=["network"], include_in_schema=False)
def graph_compat_alias():
    """Legacy alias: old dashboard called GET /api/graph; canonical is /api/network/graph."""
    graph = get_graph_engine()
    from app.api.routes_graph import graph_payload

    return graph_payload(graph.get_all_nodes(), graph.get_all_edges())


@app.get("/", include_in_schema=False)
def serve_dashboard():
    """Serve single-page investigator dashboard frontend."""
    index_file = os.path.join(static_path, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "AI Criminal Network Engine API running. Dashboard file not found."}

