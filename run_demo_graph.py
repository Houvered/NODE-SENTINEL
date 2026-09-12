"""NODE SENTINEL - Demo Graph Runner & Analyzer
Loads sample_data/demo_graph.json, performs network analytics, runs anomaly detection,
calculates investigative risk scores, and displays an investigation summary report.
Can also launch the live FastAPI dashboard with `--serve`.
"""
import sys
import json
import argparse
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.core.graph_engine import get_graph_engine
from app.api.routes_ingest import load_dataset_by_name
from app.core.graph_analytics import GraphAnalytics
from app.core.anomaly_detector import AnomalyDetector
from app.models.graph_models import NodeType


def run_demo_graph_analysis(dataset_name: str = "syndicate_network"):
    clean_name = dataset_name if dataset_name.endswith(".json") else f"{dataset_name}.json"
    print("=" * 70)
    print("  NODE SENTINEL · CRIMINAL NETWORK ANALYSIS ENGINE")
    print(f"  Running Graph Analysis (sample_data/{clean_name})")
    print("=" * 70)

    # 1. Ingestion
    engine = get_graph_engine()
    engine.clear()
    res = load_dataset_by_name(engine, clean_name)
    nodes = engine.get_all_nodes()
    edges = engine.get_all_edges()

    print(f"\n[+] Ingestion Status: {res['status'].upper()} ({res.get('dataset', clean_name)})")
    print(f"    Total Entities (Nodes):      {len(nodes)}")
    print(f"    Total Relationships (Edges): {len(edges)}")

    # Node types breakdown
    by_type = {}
    for n in nodes:
        lbl = n.label.value if hasattr(n.label, "value") else str(n.label)
        by_type[lbl] = by_type.get(lbl, 0) + 1
    types_str = ", ".join(f"{k}: {v}" for k, v in sorted(by_type.items()))
    print(f"    Entity Types:                {types_str}")

    # Edge types breakdown
    by_rel = {}
    for e in edges:
        r_val = e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)
        by_rel[r_val] = by_rel.get(r_val, 0) + 1
    rels_str = ", ".join(f"{k}: {v}" for k, v in sorted(by_rel.items()))
    print(f"    Relationship Types:          {rels_str}")

    # 2. Graph Centrality Analytics
    print("\n" + "-" * 70)
    print("  CENTRALITY RANKINGS & KEY BROKERS")
    print("-" * 70)
    analytics = GraphAnalytics(engine)
    ranked = analytics.get_ranked_influencers(top_k=12)
    detector = AnomalyDetector(engine)

    print(f"  {'Entity Name':<24} {'Type':<12} {'Degree':<8} {'Betweenness':<12} {'PageRank':<10} {'Risk'}")
    print("  " + "-" * 68)
    for item in ranked:
        risk_obj = detector.calculate_investigative_risk_score(item["node_id"])
        score = risk_obj.overall_score
        print(f"  {item['name']:<24} {item['node_type']:<12} {item['degree_centrality']:<8.3f} {item['betweenness_centrality']:<12.3f} {item['pagerank']:<10.4f} {score:>5.1f} ({risk_obj.severity_level})")

    # 3. Community Detection (Louvain)
    print("\n" + "-" * 70)
    print("  COMMUNITY DETECTION (LOUVAIN)")
    print("-" * 70)
    communities = analytics.detect_communities()
    comm_groups = {}
    for nid, cid in communities.items():
        node = engine.get_node(nid)
        name = node.name if node else nid
        comm_groups.setdefault(cid, []).append(name)

    for cid, members in sorted(comm_groups.items()):
        print(f"  [Cluster #{cid}] ({len(members)} entities):")
        print(f"    {', '.join(members)}")

    # 4. Statistical Anomaly Alerts
    print("\n" + "-" * 70)
    print("  STATISTICAL ANOMALY DETECTION ALERTS")
    print("-" * 70)
    alerts = detector.get_all_alerts()
    if not alerts:
        print("  No anomalies detected.")
    else:
        for idx, alert in enumerate(alerts, 1):
            sev_badge = f"[{alert.severity}]"
            print(f"\n  {idx}. {sev_badge} {alert.title} ({alert.alert_type})")
            print(f"     Details: {alert.description}")
            if alert.evidence:
                ev_str = ", ".join(f"{k}={v}" for k, v in alert.evidence.items())
                print(f"     Evidence: {ev_str}")

    # 5. Suspect Risk Dossiers
    print("\n" + "-" * 70)
    print("  PERSONS OF INTEREST - RISK DOSSIER BREAKDOWN")
    print("-" * 70)
    for n in nodes:
        if (n.label.value if hasattr(n.label, "value") else str(n.label)) == NodeType.PERSON.value:
            risk = detector.calculate_investigative_risk_score(n.id)
            print(f"\n  * {n.name} [{risk.severity_level} RISK - Score: {risk.overall_score}/100]")
            for f in risk.factors:
                factor_text = f["factor"] if isinstance(f, dict) else getattr(f, "factor", str(f))
                factor_pts = f["points"] if isinstance(f, dict) else getattr(f, "points", 0)
                print(f"      + {factor_text} (+{factor_pts} pts)")

    print("\n" + "=" * 70)
    print("  Analysis complete. Interactive Dashboard available at http://localhost:8000")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run NODE SENTINEL Graph Analysis")
    parser.add_argument("--dataset", type=str, default="syndicate_network", help="Dataset name in sample_data/ (default: syndicate_network)")
    parser.add_argument("--serve", action="store_true", help="Start the FastAPI web server after analysis")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind server (default: 8000)")
    args = parser.parse_args()

    run_demo_graph_analysis(args.dataset)

    if args.serve:
        import uvicorn
        print(f"Starting FastAPI server on http://127.0.0.1:{args.port} ...")
        uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, reload=False)
