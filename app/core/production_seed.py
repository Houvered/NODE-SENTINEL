# -*- coding: utf-8 -*-
"""
Production seeding for NODE SENTINEL (incremental cutover).

Wires locally-available REAL data artifacts into the live in-memory graph at
startup, with the synthetic demo dataset as fallback when artifacts are absent.

Local real-data artifacts (gitignored, built by ingest_*.py scripts):
  sample_data/paysim_live_seed.csv    PaySim transactions, fraud-priority, <=2000 rows
  sample_data/email_fraud_scored.csv  Scored fraud emails (full 11.9k local run)

Committed reproducibility metadata:
  sample_data/face_enrollment_manifest.json  31 watchlist persons (images pending)

Operational limits (never exceed):
  Live graph ingestion <= 2,000 rows per source.
  Never load the full 6.3M-row PaySim log into the graph.

All entry points are total (never raise) so startup can never break.
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger(__name__)

SAMPLE_DIR = Path(settings.BASE_DIR) / "sample_data"
PAYSIM_LIVE_SEED = SAMPLE_DIR / "paysim_live_seed.csv"
EMAIL_SCORED = SAMPLE_DIR / "email_fraud_scored.csv"
FACE_MANIFEST = SAMPLE_DIR / "face_enrollment_manifest.json"

MAX_PAYSIM_ROWS = 2000
MAX_EMAIL_CASES = 300
EMAIL_CASE_THRESHOLD = 35


def seed_paysim_live(
    graph=None,
    storage=None,
    path: Path = PAYSIM_LIVE_SEED,
    cap: int = MAX_PAYSIM_ROWS,
) -> Dict[str, Any]:
    """Ingest the local PaySim live-seed CSV (fraud-priority, capped)."""
    result: Dict[str, Any] = {"attempted": False, "records_added": 0, "capped": False}
    path = Path(path)
    if not path.exists():
        return result
    try:
        from app.core.financial_parser import FinancialParser
        from app.core.financial_analytics import FinancialService, get_financial_storage
        from app.core.graph_engine import get_graph_engine

        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = [r for r in reader if any((v or "").strip() for v in r.values())]
        result["attempted"] = True
        result["rows_found"] = len(rows)
        if len(rows) > cap:
            rows = rows[:cap]
            result["capped"] = True
        import io as _io
        buf = _io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        records, rejected, _, errors = FinancialParser.parse_csv(buf.getvalue(), source_name=path.name)
        if errors and not records:
            result["error"] = "; ".join(errors[:3])
            return result
        svc = FinancialService(
            graph_engine=graph or get_graph_engine(),
            storage=storage or get_financial_storage(),
        )
        added, entities, rels = svc.ingest_records_into_graph(records)
        result.update({"records_added": added, "entities_created": entities,
                       "relationships_created": rels, "rejected": rejected})
    except Exception as ex:  # never break startup
        logger.warning(f"PaySim live seeding skipped: {ex}")
        result["error"] = str(ex)[:200]
    return result


def seed_email_cases(
    graph=None,
    path: Path = EMAIL_SCORED,
    cap: int = MAX_EMAIL_CASES,
    threshold: int = EMAIL_CASE_THRESHOLD,
) -> Dict[str, Any]:
    """Wire top-scoring fraud emails as Case nodes (score desc, capped)."""
    result: Dict[str, Any] = {"attempted": False, "cases_created": 0}
    path = Path(path)
    if not path.exists():
        return result
    try:
        from app.core.graph_engine import get_graph_engine
        from app.models.graph_models import Node, NodeType

        g = graph or get_graph_engine()
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        result["attempted"] = True
        result["emails_found"] = len(rows)
        scored = []
        for r in rows:
            try:
                score = int(float((r.get("fraud_score") or "0").strip()))
            except Exception:
                continue
            if score >= threshold:
                scored.append((score, r))
        scored.sort(key=lambda t: -t[0])
        made = 0
        for score, r in scored:
            if made >= cap:
                break
            try:
                row_id = int(float(str(r.get("row") or "0")))
            except Exception:
                row_id = 0
            case_id = f"CASE_EMAIL_FRAUD_{row_id:05d}"
            if g.get_node(case_id):
                continue
            body = r.get("Text") or ""
            g.add_node(Node(
                case_id, NodeType.CASE, f"Email Fraud {row_id} [{r.get('fraud_level', '')}]",
                {"case_code": case_id, "source": path.name,
                 "fraud_score": score, "fraud_level": (r.get("fraud_level") or "").strip(),
                 "excerpt": body[:500], "indicators": (r.get("indicators") or "")[:500],
                 "dataset_label": (r.get("Class") or "").strip()},
            ))
            made += 1
        result["cases_created"] = made
        result["candidates"] = len(scored)
    except Exception as ex:
        logger.warning(f"Email case seeding skipped: {ex}")
        result["error"] = str(ex)[:200]
    return result


def seed_face_watchlist(graph=None, path: Path = FACE_MANIFEST) -> Dict[str, Any]:
    """Create Person nodes for the 31 watchlist identities (no images fabricated)."""
    result: Dict[str, Any] = {"attempted": False, "persons_created": 0}
    path = Path(path)
    if not path.exists():
        return result
    try:
        from app.core.graph_engine import get_graph_engine
        from app.models.graph_models import Node, NodeType

        g = graph or get_graph_engine()
        manifest = json.loads(path.read_text(encoding="utf-8"))
        persons = manifest.get("persons", {})
        result["attempted"] = True
        result["persons_found"] = len(persons)
        made = 0
        for pid, info in persons.items():
            if g.get_node(pid):
                continue
            g.add_node(Node(
                pid, NodeType.PERSON, info.get("name", pid),
                {"source": "face_watchlist:Dataset.csv",
                 "watchlist_images": info.get("image_count", 0),
                 "face_enrollment": "pending_images", "role": "watchlist"},
            ))
            made += 1
        result["persons_created"] = made
    except Exception as ex:
        logger.warning(f"Face watchlist seeding skipped: {ex}")
        result["error"] = str(ex)[:200]
    return result


def seed_real_data_if_available(
    graph=None,
    fin_storage=None,
    paysim_path: Path = PAYSIM_LIVE_SEED,
    email_path: Path = EMAIL_SCORED,
    face_path: Path = FACE_MANIFEST,
) -> Dict[str, Any]:
    """Wire all available real-data artifacts. Never raises. Returns a summary."""
    paysim = seed_paysim_live(graph=graph, storage=fin_storage, path=paysim_path)
    email = seed_email_cases(graph=graph, path=email_path)
    faces = seed_face_watchlist(graph=graph, path=face_path)
    real_sources = sum(1 for r in (paysim, email, faces)
                       if r.get("attempted") and (r.get("records_added", 0) or r.get("cases_created", 0) or r.get("persons_created", 0)))
    mode = "real" if real_sources == 3 else ("partial" if real_sources else "synthetic-fallback")
    return {"mode": mode, "paysim": paysim, "email": email, "faces": faces}
