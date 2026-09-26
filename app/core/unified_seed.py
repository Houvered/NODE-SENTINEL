# -*- coding: utf-8 -*-
"""
Unified connected seed for NODE SENTINEL.

Merges EVERY committed sample-data source into one connected investigative
graph and guarantees at least three big criminal rings with 3+ criminals:

  Ring A "Operation Hawala Falcon"  — syndicate_network.json (10 persons:
  Tariq Ahmad kingpin + 9 associates) + demo CDR + demo ledger.
  Ring B "Noida Cell"               — demo_graph.json (4 persons: Rahul
  Sharma + 3 associates) + shared shell bridge + email-fraud cases.
  Ring C "PaySim Mule Ring"         — 4 synthetic operators anchored on the
  live PaySim fraud cluster (CASE_PAYSIM_FRAUD + top mule accounts) +
  email-fraud cases.

Bridges (all tagged source="unified_seed", deterministic edge IDs so
re-runs overwrite instead of duplicating):
  - Hawala pool ACC_HAWALA_9901 <-> top-6 PaySim mule accounts
    (suspected layering hops between hawala pool and formal rails).
  - Shell ACC_SHELL_4420 <-> Noida pool ACC_987654321 (shell layering).
  - Email-fraud Case nodes (fraud_score ranked) round-robin INVOLVED_IN
    to six money-handler persons across the three rings (phishing
    proceeds layered through each ring).
  - Ring C operators share LOC_CHANDNI_CHOWK + CASE_PAYSIM_FRAUD.

Deliberately NOT linked: the 31 face-watchlist identities are REAL
celebrity names from a public face dataset — they stay a biometric-only
registry (face search). Branding real people as criminals would be
defamation; the census below excludes them for the same reason.

All entry points are total (never raise) and idempotent.
"""
from __future__ import annotations

import logging
from collections import Counter, deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

SAMPLE_DIR = Path(settings.BASE_DIR) / "sample_data"
SOURCE_TAG = "unified_seed"

# Ring C — synthetic operators (all sample data in this repo is synthetic /
# fictitious per README; names follow the existing demo_graph convention).
RING_C = [
    ("PERSON_ARJUN_MEHTA", "Arjun Mehta", "Mule Ring Coordinator", "HIGH"),
    ("PERSON_KARAN_JOSHI", "Karan Joshi", "Cash-Out Rider", "MEDIUM"),
    ("PERSON_NEHA_KULKARNI", "Neha Kulkarni", "Mule Account Recruiter", "MEDIUM"),
    ("PERSON_MANOJ_TIWARI", "Manoj Tiwari", "Digital Wallet Handler", "LOW"),
]

# Money handlers receiving email-fraud case links (2 per ring).
EMAIL_HANDLERS = [
    "PERSON_POOJA_RATHI",     # Ring A hawala conduit
    "PERSON_DEEPAK_AGARWAL",  # Ring A bullion/hawala
    "PERSON_RAHUL_SHARMA",    # Ring B head
    "PERSON_AMIT_VERMA",      # Ring B
    "PERSON_ARJUN_MEHTA",     # Ring C coordinator
    "PERSON_KARAN_JOSHI",     # Ring C rider
]

RING_DEFS = [
    {"key": "hawala_falcon", "label": "Operation Hawala Falcon",
     "anchor": "PERSON_TARIQ_AHMAD", "head": "PERSON_TARIQ_AHMAD",
     "persons": ["PERSON_TARIQ_AHMAD", "PERSON_KABIR_MIRZA", "PERSON_IMRAN_QURESHI",
                 "PERSON_POOJA_RATHI", "PERSON_ROHIT_SAXENA", "PERSON_VIKRAM_MALHOTRA",
                 "PERSON_SURESH_RAWAT", "PERSON_ANITA_DESHMUKH", "PERSON_DEEPAK_AGARWAL",
                 "PERSON_SAMEER_KHAN"]},
    {"key": "noida_cell", "label": "Noida Extortion Cell",
     "anchor": "PERSON_RAHUL_SHARMA", "head": "PERSON_RAHUL_SHARMA",
     "persons": ["PERSON_RAHUL_SHARMA", "PERSON_AMIT_VERMA", "PERSON_SUNIL_KUMAR",
                 "PERSON_VIKAS_SINGH"]},
    {"key": "mule_ring", "label": "PaySim Mule Ring",
     "anchor": "CASE_PAYSIM_FRAUD", "head": "PERSON_ARJUN_MEHTA",
     "persons": [p[0] for p in RING_C]},
]


def _label_of(node) -> str:
    return node.label.value if hasattr(node.label, "value") else str(node.label)


def _is_person(node) -> bool:
    return _label_of(node).lower() == "person"


def _is_watchlist(node) -> bool:
    props = node.properties or {}
    return props.get("role") == "watchlist" or props.get("source") == "face_watchlist:Dataset.csv"


def ensure_demo_graph_merged(graph=None) -> Dict[str, Any]:
    """Merge demo_graph.json (Ring B) alongside syndicate_network.json."""
    res: Dict[str, Any] = {"merged": False}
    try:
        from app.core.graph_engine import get_graph_engine
        from app.api.routes_ingest import load_dataset_by_name

        g = graph or get_graph_engine()
        if g.get_node("PERSON_RAHUL_SHARMA"):
            res["merged"] = False
            res["reason"] = "already-present"
            return res
        out = load_dataset_by_name(g, "demo_graph.json")
        res.update({"merged": True, "nodes": out.get("total_graph_nodes"),
                    "edges": out.get("total_graph_edges")})
    except Exception as ex:
        logger.warning(f"Unified seed: demo_graph merge skipped: {ex}")
        res["error"] = str(ex)[:200]
    return res


def ensure_demo_cdr(graph=None, storage=None) -> Dict[str, Any]:
    """Auto-ingest demo_cdr.csv when CDR storage is empty (burst demo data)."""
    res: Dict[str, Any] = {"ingested": False}
    try:
        from app.core.cdr_analytics import CDRService, get_cdr_storage
        from app.core.cdr_parser import CDRParser
        from app.core.graph_engine import get_graph_engine

        store = storage or get_cdr_storage()
        if len(store.get_all_records()) > 0:
            res["reason"] = "already-present"
            return res
        path = SAMPLE_DIR / "demo_cdr.csv"
        if not path.exists():
            res["reason"] = "missing-file"
            return res
        records, rejected, _, errors = CDRParser.parse_csv(path.read_bytes(), source_name=path.name)
        if errors and not records:
            res["error"] = "; ".join(errors[:3])
            return res
        svc = CDRService(graph_engine=graph or get_graph_engine())
        added, entities, rels = svc.ingest_records_into_graph(records)
        res.update({"ingested": True, "records_added": added,
                    "entities_created": entities, "relationships_created": rels,
                    "rejected": rejected})
    except Exception as ex:
        logger.warning(f"Unified seed: demo CDR ingest skipped: {ex}")
        res["error"] = str(ex)[:200]
    return res


def _has_edge(graph, source: str, target: str, relationship: str) -> bool:
    for e in graph.get_all_edges():
        rel = e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)
        if e.source == source and e.target == target and rel == relationship:
            return True
    return False


def _add_bridge(graph, edge_id: str, source: str, target: str, relationship,
                narrative: str) -> bool:
    """Add a deterministic bridge edge; overwrites on re-run, never dupes."""
    from app.models.graph_models import Edge

    if not graph.get_node(source) or not graph.get_node(target):
        return False
    try:
        graph.add_edge(Edge(source, target, relationship,
                            {"source": SOURCE_TAG, "narrative": narrative},
                            id=edge_id))
        return True
    except Exception as ex:
        logger.warning(f"Unified seed: bridge {edge_id} skipped: {ex}")
        return False


def _top_paysim_accounts(fin_storage, top_k: int = 6) -> List[str]:
    counts: Counter = Counter()
    try:
        for r in fin_storage.get_all_records():
            if r.sender and (r.sender.startswith("C") or r.sender.startswith("M") or r.sender.startswith("ACC_C")):
                counts[r.sender] += 1
            if r.receiver and (r.receiver.startswith("C") or r.receiver.startswith("M") or r.receiver.startswith("ACC_C")):
                counts[r.receiver] += 1
    except Exception:
        pass
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [acc for acc, _ in ranked[:top_k]]


def bridge_paysim_hawala(graph=None, fin_storage=None) -> Dict[str, Any]:
    """Tie the PaySim fraud cluster to the hawala pool (layering hops)."""
    res: Dict[str, Any] = {"bridges": 0, "accounts": []}
    try:
        from app.core.financial_analytics import get_financial_storage
        from app.core.graph_engine import get_graph_engine
        from app.models.graph_models import EdgeType

        g = graph or get_graph_engine()
        store = fin_storage or get_financial_storage()
        pool = "ACC_HAWALA_9901"
        if not g.get_node(pool):
            res["reason"] = "no-hawala-pool"
            return res
        accounts = _top_paysim_accounts(store)
        res["accounts"] = accounts
        made = 0
        for i, acc in enumerate(accounts):
            node = g.get_node(acc) or g.get_node(f"ACC_{acc}")
            if not node:
                continue
            acc_id = node.id
            if i % 2 == 0:
                if _add_bridge(g, f"USEED_HAWALA_OUT_{acc_id}", pool, acc_id,
                               EdgeType.TRANSFERRED_MONEY,
                               "Suspected layering hop: hawala pool dispersing into high-volume mule account"):
                    made += 1
            else:
                if _add_bridge(g, f"USEED_HAWALA_IN_{acc_id}", acc_id, pool,
                               EdgeType.TRANSFERRED_MONEY,
                               "Suspected layering hop: mule account consolidating back into hawala pool"):
                    made += 1
        res["bridges"] = made
    except Exception as ex:
        logger.warning(f"Unified seed: paysim bridge skipped: {ex}")
        res["error"] = str(ex)[:200]
    return res


def bridge_noida_shell(graph=None) -> Dict[str, Any]:
    """Tie the Noida pool account to the syndicate shell account."""
    res: Dict[str, Any] = {"bridges": 0}
    try:
        from app.core.graph_engine import get_graph_engine
        from app.models.graph_models import EdgeType

        g = graph or get_graph_engine()
        made = 0
        if _add_bridge(g, "USEED_SHELL_NOIDA_OUT", "ACC_SHELL_4420", "ACC_987654321",
                       EdgeType.TRANSFERRED_MONEY,
                       "Suspected shell layering: corporate shell funding Noida cell pool"):
            made += 1
        if _add_bridge(g, "USEED_SHELL_NOIDA_IN", "ACC_987654321", "ACC_SHELL_4420",
                       EdgeType.TRANSFERRED_MONEY,
                       "Suspected round-trip: Noida pool kicking back to corporate shell"):
            made += 1
        res["bridges"] = made
    except Exception as ex:
        logger.warning(f"Unified seed: noida bridge skipped: {ex}")
        res["error"] = str(ex)[:200]
    return res


def _find_paysim_case(graph) -> Optional[str]:
    if graph.get_node("CASE_PAYSIM_FRAUD"):
        return "CASE_PAYSIM_FRAUD"
    for n in graph.get_all_nodes():
        if _label_of(n).lower() != "case":
            continue
        props = n.properties or {}
        code = str(props.get("case_code", "") or "")
        if "PAYSIM" in code.upper() or (props.get("source") or "") == "financial_ingest" and "paysim" in str(props.get("source_document", "")).lower():
            return n.id
    return None


def ensure_ring_c(graph=None, fin_storage=None) -> Dict[str, Any]:
    """Create the 4-operator PaySim mule ring anchored on the fraud cluster."""
    res: Dict[str, Any] = {"persons": 0, "edges": 0}
    try:
        from app.core.financial_analytics import get_financial_storage
        from app.core.graph_engine import get_graph_engine
        from app.models.graph_models import EdgeType, Node, NodeType

        g = graph or get_graph_engine()
        store = fin_storage or get_financial_storage()
        made_p = 0
        for pid, name, role, risk in RING_C:
            if g.get_node(pid):
                continue
            g.add_node(Node(pid, NodeType.PERSON, name,
                            {"source": SOURCE_TAG, "role": role, "risk_tag": risk,
                             "synthetic": True,
                             "note": "Synthetic demo operator for the PaySim mule ring"}))
            made_p += 1
        res["persons"] = made_p

        made_e = 0
        ids = [p[0] for p in RING_C]
        # CALLS ring
        for a, b in zip(ids, ids[1:] + ids[:1]):
            if _add_bridge(g, f"USEED_RINGC_CALL_{a}_{b}", a, b, EdgeType.CALLS,
                           "Mule-ring coordination calls"):
                made_e += 1
        # Operate top mule accounts + join paysim case + share location
        accounts = _top_paysim_accounts(store, top_k=4)
        for pid, acc in zip(ids, accounts * 2):
            node = g.get_node(acc)
            if node and _add_bridge(g, f"USEED_RINGC_OP_{pid}_{node.id}", pid, node.id,
                                   EdgeType.TRANSFERRED_MONEY,
                                   "Operator moving funds through controlled mule account"):
                made_e += 1
        case_id = _find_paysim_case(g)
        if case_id:
            for pid in ids:
                if _add_bridge(g, f"USEED_RINGC_CASE_{pid}", pid, case_id,
                               EdgeType.INVOLVED_IN,
                               "Operator implicated in PaySim fraud cluster case"):
                    made_e += 1
        if g.get_node("LOC_CHANDNI_CHOWK"):
            for pid in ids:
                if _add_bridge(g, f"USEED_RINGC_LOC_{pid}", pid, "LOC_CHANDNI_CHOWK",
                               EdgeType.LOCATED_AT,
                               "Operator sighted at shared hawala bullion hub"):
                    made_e += 1
        res["edges"] = made_e
        res["mule_accounts"] = accounts
    except Exception as ex:
        logger.warning(f"Unified seed: ring C skipped: {ex}")
        res["error"] = str(ex)[:200]
    return res


def link_email_cases(graph=None) -> Dict[str, Any]:
    """Link every scored email-fraud Case to handlers across the 3 rings."""
    res: Dict[str, Any] = {"linked": 0, "skipped_no_handler": 0}
    try:
        from app.core.graph_engine import get_graph_engine
        from app.models.graph_models import EdgeType

        g = graph or get_graph_engine()
        handlers = [h for h in EMAIL_HANDLERS if g.get_node(h)]
        if not handlers:
            res["reason"] = "no-handlers"
            return res
        linked_cases = set()
        for e in g.get_all_edges():
            rel = e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)
            if rel != EdgeType.INVOLVED_IN.value:
                continue
            src = g.get_node(e.source)
            if src is not None and _is_person(src):
                linked_cases.add(e.target)
        scored = []
        for n in g.get_all_nodes():
            if _label_of(n).lower() != "case":
                continue
            score = (n.properties or {}).get("fraud_score")
            if score is None:
                continue
            try:
                scored.append((float(score), n.id))
            except (TypeError, ValueError):
                continue
        scored.sort(key=lambda t: (-t[0], t[1]))
        made = 0
        hi = 0
        for _, case_id in scored:
            if case_id in linked_cases:
                continue
            handler = handlers[hi % len(handlers)]
            hi += 1
            if _add_bridge(g, f"USEED_EMAIL_{case_id}", handler, case_id,
                           EdgeType.INVOLVED_IN,
                           "Phishing proceeds suspected layered through handler network"):
                made += 1
        res["linked"] = made
        res["scored_cases"] = len(scored)
    except Exception as ex:
        logger.warning(f"Unified seed: email linking skipped: {ex}")
        res["error"] = str(ex)[:200]
    return res


def _reachable_persons(graph, anchor: str) -> List[str]:
    """Undirected BFS from anchor; returns reachable non-watchlist person IDs."""
    adj: Dict[str, set] = {}
    try:
        for e in graph.get_all_edges():
            adj.setdefault(e.source, set()).add(e.target)
            adj.setdefault(e.target, set()).add(e.source)
    except Exception:
        return []
    seen = {anchor}
    queue = deque([anchor])
    while queue:
        cur = queue.popleft()
        for nb in adj.get(cur, ()):  # noqa: B007 - BFS expansion
            if nb not in seen:
                seen.add(nb)
                queue.append(nb)
    out = []
    for nid in seen:
        n = graph.get_node(nid)
        if n is not None and _is_person(n) and not _is_watchlist(n):
            out.append(nid)
    return sorted(out)


def census_rings(graph=None) -> List[Dict[str, Any]]:
    """Verify each ring: anchor reachability + criminal headcount."""
    from app.core.graph_engine import get_graph_engine

    g = graph or get_graph_engine()
    census = []
    for rd in RING_DEFS:
        try:
            if not g.get_node(rd["anchor"]):
                census.append({"key": rd["key"], "label": rd["label"],
                               "status": "missing-anchor", "persons": []})
                continue
            reached = _reachable_persons(g, rd["anchor"])
            expected = [p for p in rd["persons"] if g.get_node(p)]
            missing = [p for p in expected if p not in reached]
            census.append({
                "key": rd["key"], "label": rd["label"],
                "head": rd["head"], "anchor": rd["anchor"],
                "status": "ok" if not missing and len(reached) >= 3 else "degraded",
                "person_count": len(reached),
                "persons": reached,
                "missing": missing,
            })
        except Exception as ex:
            census.append({"key": rd["key"], "label": rd["label"],
                           "status": f"error: {ex}"[:200], "persons": []})
    return census


def run_unified_seed(graph=None, fin_storage=None, cdr_storage=None) -> Dict[str, Any]:
    """Run every unification step. Never raises. Returns a summary."""
    summary: Dict[str, Any] = {"steps": {}}
    try:
        from app.core.graph_engine import get_graph_engine

        g = graph or get_graph_engine()
        summary["steps"]["demo_graph"] = ensure_demo_graph_merged(g)
        summary["steps"]["demo_cdr"] = ensure_demo_cdr(g, cdr_storage)
        summary["steps"]["paysim_bridge"] = bridge_paysim_hawala(g, fin_storage)
        summary["steps"]["noida_bridge"] = bridge_noida_shell(g)
        summary["steps"]["ring_c"] = ensure_ring_c(g, fin_storage)
        summary["steps"]["email_links"] = link_email_cases(g)
        summary["rings"] = census_rings(g)
        try:
            summary["nodes"] = len(g.get_all_nodes())
            summary["edges"] = len(g.get_all_edges())
        except Exception:
            pass
        ok = sum(1 for r in summary["rings"] if r.get("status") == "ok")
        summary["rings_ok"] = ok
    except Exception as ex:  # last-resort guard: startup must never break
        logger.warning(f"Unified seed failed: {ex}")
        summary["error"] = str(ex)[:200]
    return summary
