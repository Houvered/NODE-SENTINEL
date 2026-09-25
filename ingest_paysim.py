"""NODE SENTINEL - PaySim ingestion adapter.
Streams C:/Users/HP/Downloads/archive.zip (Synthetic_Financial_datasets_log.csv,
6.36M rows, PaySim format) and builds a graph-safe NODE SENTINEL sample CSV.

Why sampling: the full 6.3M-row log cannot live in the in-memory
NetworkX graph + FinancialStorage. We keep ALL frauds (8213) plus a
stratified reservoir sample of legit rows (default total 20k).

Usage:
    python ingest_paysim.py --zip "C:/Users/HP/Downloads/archive.zip" --total 20000
    python ingest_paysim.py --zip ... --total 50000 --out sample_data/paysim_financial_50k.csv

Output columns (native NODE SENTINEL format):
    transaction_id,timestamp,sender,receiver,amount,currency,
    transaction_type,case_id,location,source_document,description
"""
from __future__ import annotations

import argparse
import csv
import io
import random
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

BASE_DATE = datetime(2024, 1, 1)
DEFAULT_ZIP = r"C:\Users\HP\Downloads\archive.zip"
DEFAULT_OUT = Path("sample_data") / "paysim_financial_sample.csv"

TYPE_MAP = {
    "TRANSFER": "TRANSFER",
    "PAYMENT": "PAYMENT",
    "CASH_OUT": "WITHDRAWAL",
    "CASH_IN": "DEPOSIT",
    "DEBIT": "PAYMENT",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build NODE SENTINEL sample from PaySim archive.zip")
    p.add_argument("--zip", dest="zip_path", default=DEFAULT_ZIP, help="Path to archive.zip")
    p.add_argument("--out", dest="out_path", default=str(DEFAULT_OUT), help="Output CSV path")
    p.add_argument("--total", type=int, default=20000, help="Total rows in sample (incl. all frauds)")
    p.add_argument("--seed", type=int, default=42, help="Reservoir sampling seed")
    p.add_argument("--fraud-case", default="CASE_PAYSIM_FRAUD", help="case_id tagged on isFraud=1 rows")
    return p.parse_args()


def to_node_row(idx: int, row: dict, fraud_case: str, source: str) -> dict:
    step = int(float(row["step"]))
    ts = BASE_DATE + timedelta(hours=step, minutes=idx % 60)
    is_fraud = str(row.get("isFraud", "0")).strip() == "1"
    raw_type = str(row.get("type", "TRANSFER")).strip().upper()
    return {
        "transaction_id": f"PAYSIM-{idx:07d}",
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "sender": str(row["nameOrig"]).strip(),
        "receiver": str(row["nameDest"]).strip(),
        "amount": str(row["amount"]).strip(),
        "currency": "INR",
        "transaction_type": TYPE_MAP.get(raw_type, raw_type),
        "case_id": fraud_case if is_fraud else "",
        "location": "",
        "source_document": source,
        "description": (
            f"PaySim[{raw_type} step={row['step']} isFraud={row.get('isFraud','0')} "
            f"isFlaggedFraud={row.get('isFlaggedFraud','0')} "
            f"oldbalanceOrg={row.get('oldbalanceOrg','')} newbalanceOrig={row.get('newbalanceOrig','')} "
            f"oldbalanceDest={row.get('oldbalanceDest','')} newbalanceDest={row.get('newbalanceDest','')}]"
        ),
    }


def main() -> int:
    args = parse_args()
    random.seed(args.seed)
    zip_path = Path(args.zip_path)
    if not zip_path.exists():
        print(f"ERROR: zip not found: {zip_path}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(zip_path) as z:
        inner = z.namelist()[0]
        print(f"Reading {inner} from {zip_path} ...")
        f = z.open(inner)
        txt = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
        reader = csv.DictReader(txt)

        frauds: list[dict] = []
        reservoir: list[dict] = []
        legit_budget = max(0, args.total - 8213)  # fraud count known; adjusted below
        # Unknown exact fraud count up front in general case, so first collect
        # frauds fully and reservoir-sample legit with dynamic capacity.
        # We know frauds ~= 8213, so pre-size reservoir to args.total - 8213.
        legit_seen = 0
        total_seen = 0
        # Dynamic: if frauds exceed estimate, shrink reservoir at the end.
        for raw in reader:
            total_seen += 1
            if str(raw.get("isFraud", "0")).strip() == "1":
                frauds.append(raw)
            else:
                legit_seen += 1
                cap = max(0, args.total - 8213)
                if len(reservoir) < cap:
                    reservoir.append(raw)
                elif cap > 0:
                    j = random.randint(0, legit_seen - 1)
                    if j < cap:
                        reservoir[j] = raw
            if total_seen % 1_000_000 == 0:
                print(f"  ... {total_seen:,} rows (frauds={len(frauds)})")

        # If actual fraud count differs from estimate, trim reservoir to fit total.
        legit_keep = max(0, args.total - len(frauds))
        reservoir = reservoir[:legit_keep]
        print(f"Total={total_seen:,} frauds={len(frauds):,} legit_sample={len(reservoir):,}")

        # Index rows for deterministic tx ids / timestamps, frauds first then legit.
        combined = [("F", r) for r in frauds] + [("L", r) for r in reservoir]
        random.shuffle(combined)  # mix so timeline isn't fraud-block then legit-block
        combined.sort(key=lambda t: float(t[1]["step"]))  # chronological

        out_path = Path(args.out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["transaction_id", "timestamp", "sender", "receiver", "amount",
                      "currency", "transaction_type", "case_id", "location",
                      "source_document", "description"]
        with open(out_path, "w", newline="", encoding="utf-8") as fo:
            w = csv.DictWriter(fo, fieldnames=fieldnames)
            w.writeheader()
            for i, (_, raw) in enumerate(combined, start=1):
                w.writerow(to_node_row(i, raw, args.fraud_case, f"{zip_path.name}/{inner}"))

        print(f"Wrote {len(combined):,} rows -> {out_path}")
        print("Next: POST to /api/financial/ingest as multipart file, or:")
        print(f"  python -c \"from app.core.financial_parser import FinancialParser; "
              f"from app.core.financial_analytics import get_financial_service; "
              f"recs,rej,_,err=FinancialParser.parse_csv(open(r'{out_path}',encoding='utf-8').read()); "
              f"print(get_financial_service().ingest_records_into_graph(recs))\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
