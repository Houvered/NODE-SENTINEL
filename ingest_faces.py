"""NODE SENTINEL - Face watchlist adapter for Dataset.csv.
Dataset.csv maps face image filenames -> person names (2,562 rows, 31 persons)
but ships WITHOUT the .jpg files, so biometric enrollment cannot run yet.

This script integrates what's available:
  1. Creates one Person node per unique name (PERSON_<Name_With_Underscores>)
     with properties: watchlist source, expected image count, enrollment status.
  2. Writes sample_data/face_enrollment_manifest.json mapping each person_id
     to expected filenames — when the images arrive, batch-enroll via
     FaceStorage.register_face() using this manifest.

Usage:
    python ingest_faces.py [--csv "C:/Users/HP/Downloads/Dataset.csv"] [--wire]
    --wire  also inserts the 31 Person nodes into the live graph.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_CSV = r"C:\Users\HP\Downloads\Dataset.csv"
DEFAULT_MANIFEST = Path("sample_data") / "face_enrollment_manifest.json"


def person_id_for(name: str) -> str:
    clean = "".join(c if (c.isalnum() or c == " ") else "" for c in name).strip()
    return "PERSON_" + "_".join(clean.split())


def main() -> int:
    ap = argparse.ArgumentParser(description="Integrate face watchlist Dataset.csv")
    ap.add_argument("--csv", dest="csv_path", default=DEFAULT_CSV)
    ap.add_argument("--wire", action="store_true", help="Insert Person nodes into live graph")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    args = ap.parse_args()

    cp = Path(args.csv_path)
    if not cp.exists():
        print(f"ERROR: not found: {cp}", file=sys.stderr)
        return 1
    with open(cp, encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows):,} label rows")

    by_person: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        name = (r.get("label") or "").strip()
        fid = (r.get("id") or "").strip()
        if name and fid:
            by_person[name].append(fid)
    print(f"Unique persons: {len(by_person)}")

    manifest = {}
    for name in sorted(by_person):
        pid = person_id_for(name)
        manifest[pid] = {"name": name, "expected_images": sorted(by_person[name]),
                         "image_count": len(by_person[name]),
                         "enrollment_status": "pending_images",
                         "source": cp.name}
    mp = Path(args.manifest)
    mp.parent.mkdir(parents=True, exist_ok=True)
    with open(mp, "w", encoding="utf-8") as f:
        json.dump({"person_count": len(manifest), "persons": manifest}, f, indent=2)
    print(f"Wrote manifest ({len(manifest)} persons) -> {mp}")

    if args.wire:
        from app.core.graph_engine import get_graph_engine
        from app.models.graph_models import Node, NodeType
        g = get_graph_engine()
        made = 0
        for pid, info in manifest.items():
            if g.get_node(pid):
                continue
            g.add_node(Node(pid, NodeType.PERSON, info["name"],
                            {"source": f"face_watchlist:{cp.name}",
                             "watchlist_images": info["image_count"],
                             "face_enrollment": "pending_images",
                             "role": "watchlist"}))
            made += 1
        print(f"Wired {made} Person nodes (graph now {len(g.get_all_nodes())} nodes)")
    else:
        print("Dry run: pass --wire to insert Person nodes into the live graph.")
    print("NOTE: face biometric enrollment needs the actual .jpg files (not in Dataset.csv).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
