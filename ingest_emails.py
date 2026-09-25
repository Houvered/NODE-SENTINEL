"""NODE SENTINEL - Fraud-email batch adapter.
Streams C:/Users/HP/Downloads/fraud_email_.csv.zip (11,929 rows: Text, Class),
scores every email with the explainable screener, and writes:
  1. sample_data/email_fraud_scored.csv  (row, Text, Class, fraud_score, fraud_level, indicators)
  2. Optionally wires HIGH/CRITICAL hits into the live graph as Case nodes.

Usage:
    python ingest_emails.py                                   # score only -> CSV
    python ingest_emails.py --wire --max-cases 300            # also ingest top cases
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import zipfile
from pathlib import Path

from app.core.email_fraud_screener import score_email

DEFAULT_ZIP = r"C:\Users\HP\Downloads\fraud_email_.csv.zip"
DEFAULT_OUT = Path("sample_data") / "email_fraud_scored.csv"


def main() -> int:
    ap = argparse.ArgumentParser(description="Score fraud-email dataset for NODE SENTINEL")
    ap.add_argument("--zip", dest="zip_path", default=DEFAULT_ZIP)
    ap.add_argument("--out", dest="out_path", default=str(DEFAULT_OUT))
    ap.add_argument("--wire", action="store_true", help="Wire HIGH/CRITICAL hits into live graph")
    ap.add_argument("--max-cases", type=int, default=300)
    args = ap.parse_args()

    zp = Path(args.zip_path)
    if not zp.exists():
        print(f"ERROR: not found: {zp}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(zp) as z:
        inner = z.namelist()[0]
        txt = io.TextIOWrapper(z.open(inner), encoding="utf-8", errors="replace")
        reader = csv.DictReader(txt)
        rows = list(reader)
    print(f"Loaded {len(rows):,} emails from {inner}")

    scored = []
    for i, r in enumerate(rows, start=1):
        body = (r.get("Text") or "").strip()
        if not body:
            continue
        res = score_email(body)
        scored.append({"row": i, "body": body, "label": (r.get("Class") or "").strip(), **res})
    from collections import Counter
    print("Level distro:", dict(Counter(s["level"] for s in scored)))

    out = Path(args.out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        w.writerow(["row", "Text", "Class", "fraud_score", "fraud_level", "indicators"])
        for s in scored:
            w.writerow([s["row"], s["body"], s["label"], s["score"], s["level"],
                        "; ".join(f"{d['indicator']} (+{d['points']})" for d in s["indicators"])])
    print(f"Wrote {len(scored):,} scored rows -> {out}")

    if args.wire:
        from app.core.graph_engine import get_graph_engine
        from app.models.graph_models import Node, NodeType
        g = get_graph_engine()
        made = 0
        for s in sorted(scored, key=lambda x: -x["score"]):
            if s["score"] < 35 or made >= args.max_cases:
                break
            cid = f"CASE_EMAIL_FRAUD_{s['row']:05d}"
            if g.get_node(cid):
                continue
            g.add_node(Node(cid, NodeType.CASE, f"Email Fraud {s['row']} [{s['level']}]",
                            {"case_code": cid, "source": f"{zp.name}/{inner}",
                             "fraud_score": s["score"], "fraud_level": s["level"],
                             "excerpt": s["body"][:500],
                             "indicators": "; ".join(d["indicator"] for d in s["indicators"][:5]),
                             "dataset_label": s["label"]}))
            made += 1
        print(f"Wired {made} Case nodes (graph now {len(g.get_all_nodes())} nodes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
