from fastapi import APIRouter

from app.core.anomaly_detector import AnomalyDetector
from app.core.graph_engine import get_graph_engine

router = APIRouter(prefix="/network/alerts", tags=["alerts"])


@router.get("")
def alerts():
    results = AnomalyDetector(get_graph_engine()).get_all_alerts()
    return {"alerts": [item.dict() for item in results], "count": len(results)}
