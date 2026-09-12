from fastapi import APIRouter

from app.core.anomaly_detector import AnomalyDetector
from app.core.graph_engine import get_graph_engine

router = APIRouter(prefix="/network/alerts", tags=["alerts"])


@router.get("")
def alerts():
    results = AnomalyDetector(get_graph_engine()).get_all_alerts()
    dumped = [item.model_dump() if hasattr(item, "model_dump") else item.dict() for item in results]
    return {"alerts": dumped, "count": len(results)}
