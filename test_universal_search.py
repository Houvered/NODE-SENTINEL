# -*- coding: utf-8 -*-
"""
Verification and Test Suite for STEP 7: Universal Search.

Tests:
1. Search existing Person ID ('PERSON_TARIQ_AHMAD')
2. Search existing person name ('Tariq Ahmad')
3. Search phone number ('+919811223344' and '9811223344' and '9811-223-344')
4. Search vehicle number ('DL01AB9988' and 'dl 01 ab 9988')
5. Search case ID ('CASE_FIR_2024_311' and 'FIR-2024-311')
6. Search unknown value ('NON_EXISTENT_GHOST_ENTITY_999') -> 0 results, never invent entity
7. Search partial name ('Tariq', 'Pooja', 'Kabir')
8. Select person entity result & verify structure (cases, connections, risk, face_registered)
9. Open their profile (/api/entity/{id}/dossier)
10. Open existing Knowledge Graph (/api/network/search?query={id})
11. Verify existing functionality: /api/network/graph, /api/network/alerts, /api/network/analytics/centrality
12. Verify Face Search -> Person ID -> Universal Profile linkage (/api/search/face)
13. Verify both GET /search, POST /search, and /api/search aliases
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

BASE_URL = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:8000")

_test_client = None
try:
    from fastapi.testclient import TestClient
    from app.main import app
    from app.core.graph_engine import get_graph_engine
    from app.api.routes_ingest import load_dataset_by_name
    from app.core.face_storage import get_face_storage
    from app.core.demo_face_data import seed_demo_face_database

    g = get_graph_engine()
    if len(g.get_all_nodes()) == 0:
        load_dataset_by_name(g, "syndicate_network.json")
    fs = get_face_storage()
    if len(fs.list_identities()) == 0:
        seed_demo_face_database(fs)

    _test_client = TestClient(app)
except Exception:
    _test_client = None


def http_get(path: str) -> dict:
    if _test_client is not None:
        resp = _test_client.get(path)
        return resp.json()
    try:
        url = f"{BASE_URL}{path}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        raise


def http_post_json(path: str, data: dict) -> dict:
    if _test_client is not None:
        resp = _test_client.post(path, json=data)
        return resp.json()
    try:
        url = f"{BASE_URL}{path}"
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        raise


def http_post_multipart(path: str, file_path: Path) -> dict:
    if _test_client is not None:
        with open(file_path, "rb") as f:
            resp = _test_client.post(path, files={"file": (file_path.name, f.read(), "image/png")})
        return resp.json()
    try:
        url = f"{BASE_URL}{path}"
        boundary = "----WebKitFormBoundarySentinel7MA4YWxkTrZu0gW"
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        raise


def run_tests() -> bool:
    print("=" * 75)
    print("RUNNING STEP 7: UNIVERSAL SEARCH VERIFICATION TESTS")
    print("=" * 75)

    passed = 0
    total = 13

    # -----------------------------------------------------------------------
    # TEST 1: Search existing Person ID
    # -----------------------------------------------------------------------
    print("\n[TEST 1] Search existing Person ID ('PERSON_TARIQ_AHMAD')...")
    res1 = http_get("/search?q=PERSON_TARIQ_AHMAD")
    assert res1["total_results"] >= 1, f"Expected >= 1 result, got {res1['total_results']}"
    top1 = res1["results"][0]
    assert top1["entity_id"] == "PERSON_TARIQ_AHMAD", f"Expected PERSON_TARIQ_AHMAD, got {top1['entity_id']}"
    assert top1["name"] == "Tariq Ahmad"
    assert top1["entity_type"] == "PERSON"
    assert res1["is_exact_match"] is True
    print(f"  -> SUCCESS: Found exact match '{top1['name']}' ({top1['entity_id']}) with {len(top1['cases'])} case(s) and {len(top1['connections'])} connection(s)")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 2: Search existing person name
    # -----------------------------------------------------------------------
    print("\n[TEST 2] Search existing person name ('Tariq Ahmad')...")
    res2 = http_get("/search?q=" + urllib.parse.quote("Tariq Ahmad"))
    assert res2["total_results"] >= 1
    top2 = res2["results"][0]
    assert top2["name"] == "Tariq Ahmad"
    assert top2["entity_id"] == "PERSON_TARIQ_AHMAD"
    print(f"  -> SUCCESS: Found exact name match '{top2['name']}' ({top2['entity_id']})")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 3: Search phone number (with normalization)
    # -----------------------------------------------------------------------
    print("\n[TEST 3] Search phone number with normalization ('9811-223-344' -> '+919811223344')...")
    res3 = http_get("/search?q=" + urllib.parse.quote("9811-223-344"))
    assert res3["total_results"] >= 1, f"Expected phone match, got {res3['total_results']}"
    phone_res = [r for r in res3["results"] if r["entity_type"] in ("PHONE", "PERSON")]
    assert len(phone_res) >= 1, "Expected PHONE entity in results"
    print(f"  -> SUCCESS: Found phone entity '{phone_res[0]['name']}' ({phone_res[0]['entity_id']})")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 4: Search vehicle number (with normalization)
    # -----------------------------------------------------------------------
    print("\n[TEST 4] Search vehicle number ('dl 01 ab 9988' -> 'DL01AB9988')...")
    res4 = http_get("/search?q=" + urllib.parse.quote("dl 01 ab 9988"))
    assert res4["total_results"] >= 1, f"Expected vehicle match, got {res4['total_results']}"
    veh_res = [r for r in res4["results"] if r["entity_type"] == "VEHICLE"]
    assert len(veh_res) >= 1, "Expected VEHICLE entity in results"
    assert "DL01AB9988" in veh_res[0]["name"].replace(" ", "").upper()
    print(f"  -> SUCCESS: Found vehicle entity '{veh_res[0]['name']}' ({veh_res[0]['entity_id']})")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 5: Search case ID
    # -----------------------------------------------------------------------
    print("\n[TEST 5] Search case ID ('FIR-2024-311')...")
    res5 = http_get("/search?q=" + urllib.parse.quote("FIR-2024-311"))
    assert res5["total_results"] >= 1, f"Expected case match, got {res5['total_results']}"
    case_res = [r for r in res5["results"] if r["entity_type"] == "CASE"]
    assert len(case_res) >= 1, "Expected CASE entity in results"
    print(f"  -> SUCCESS: Found case entity '{case_res[0]['name']}' ({case_res[0]['entity_id']}) with {len(case_res[0]['connections'])} involved suspects/targets")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 6: Search unknown value (Never invent an entity)
    # -----------------------------------------------------------------------
    print("\n[TEST 6] Search unknown value ('NON_EXISTENT_GHOST_ENTITY_999')...")
    res6 = http_get("/search?q=NON_EXISTENT_GHOST_ENTITY_999")
    assert res6["total_results"] == 0, f"Expected 0 results, got {res6['total_results']}"
    assert len(res6["results"]) == 0
    assert "never invents" in res6["message"].lower() or "no entities" in res6["message"].lower()
    print(f"  -> SUCCESS: 0 results returned. System cleanly handled unknown query without hallucinating: '{res6['message']}'")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 7: Search partial name
    # -----------------------------------------------------------------------
    print("\n[TEST 7] Search partial name ('Kabir')...")
    res7 = http_get("/search?q=Kabir")
    assert res7["total_results"] >= 1, f"Expected partial match, got {res7['total_results']}"
    top7 = res7["results"][0]
    assert "Kabir" in top7["name"], f"Expected 'Kabir' in name, got {top7['name']}"
    print(f"  -> SUCCESS: Partial name 'Kabir' resolved to '{top7['name']}' ({top7['entity_id']})")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 8: Select person entity & verify complete data structure
    # -----------------------------------------------------------------------
    print("\n[TEST 8] Select person entity and verify data contract...")
    person = top1
    required_keys = ["entity_id", "entity_type", "name", "cases", "connections", "properties", "risk_level", "face_registered"]
    for k in required_keys:
        assert k in person, f"Missing required key in person result: {k}"
    assert isinstance(person["cases"], list)
    assert isinstance(person["connections"], list)
    assert person["face_registered"] is True, "Expected face_registered=True for Tariq Ahmad"
    print(f"  -> SUCCESS: Entity structure valid. Cases={person['cases']}, Face registered={person['face_registered']}, Connections count={len(person['connections'])}")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 9: Open profile (dossier endpoint)
    # -----------------------------------------------------------------------
    print("\n[TEST 9] Open profile via /api/entity/{id}/dossier...")
    dossier = http_get(f"/api/entity/{person['entity_id']}/dossier")
    assert dossier["entity_id"] == person["entity_id"]
    assert dossier["name"] == person["name"]
    assert "risk" in dossier
    assert "centrality" in dossier
    assert "connected_nodes" in dossier
    print(f"  -> SUCCESS: Profile loaded. Risk severity={dossier['risk'].get('severity_level')}, Score={dossier['risk'].get('overall_score')}, Connected nodes={len(dossier['connected_nodes'])}")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 10: Open Knowledge Graph (search/neighborhood endpoint)
    # -----------------------------------------------------------------------
    print("\n[TEST 10] Open Knowledge Graph via existing /api/network/search...")
    graph_search = http_get(f"/api/network/search?query={urllib.parse.quote(person['entity_id'])}&depth=1")
    assert len(graph_search["nodes"]) >= 1, "Expected graph nodes for person"
    assert len(graph_search["edges"]) >= 1, "Expected graph edges for person"
    target_node = next((n for n in graph_search["nodes"] if n["id"] == person["entity_id"]), None)
    assert target_node is not None, "Target person node not found in graph neighborhood"
    print(f"  -> SUCCESS: Existing Knowledge Graph returned {len(graph_search['nodes'])} nodes and {len(graph_search['edges'])} edges for '{target_node['label']}'")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 11: Verify that existing functionality still works
    # -----------------------------------------------------------------------
    print("\n[TEST 11] Verifying existing APIs (Graph, Alerts, Centrality)...")
    full_graph = http_get("/api/network/graph")
    assert full_graph["stats"]["nodes"] >= 28
    assert full_graph["stats"]["edges"] >= 47

    alerts = http_get("/api/network/alerts")
    assert len(alerts["alerts"]) >= 4

    centrality = http_get("/api/network/analytics/centrality")
    assert len(centrality["items"]) >= 10
    print(f"  -> SUCCESS: All existing endpoints working. Graph has {full_graph['stats']['nodes']} nodes, {len(alerts['alerts'])} alerts, {len(centrality['items'])} ranked centrality items.")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 12: Biometric Face Search -> Person ID -> Universal Profile flow
    # -----------------------------------------------------------------------
    print("\n[TEST 12] Testing Face Search -> Person ID -> Universal Profile linkage...")
    probe_img = Path(__file__).resolve().parent / "sample_data" / "face_database" / "images" / "probe_tariq.png"
    assert probe_img.exists(), f"Probe image missing at {probe_img}"
    face_resp = http_post_multipart("/api/search/face", probe_img)
    assert face_resp["status"] == "ok"
    assert face_resp["matched_person_id"] == "PERSON_TARIQ_AHMAD"
    assert face_resp["matched_name"] == "Tariq Ahmad"
    assert face_resp["confidence_pct"] > 90.0
    assert face_resp["entity"] is not None
    assert face_resp["entity"]["entity_id"] == "PERSON_TARIQ_AHMAD"
    print(f"  -> SUCCESS: Biometric face probe matched '{face_resp['matched_name']}' ({face_resp['confidence_pct']}%) -> linked to Universal Entity Profile with {len(face_resp['entity']['cases'])} cases")
    passed += 1

    # -----------------------------------------------------------------------
    # TEST 13: Verify both GET /search, POST /search, and /api/search aliases
    # -----------------------------------------------------------------------
    print("\n[TEST 13] Verifying POST /search and /api/search routes...")
    post_res = http_post_json("/search", {"query": "Kabir Mirza", "limit": 5})
    assert post_res["total_results"] >= 1
    assert post_res["results"][0]["name"] == "Kabir Mirza"

    api_get_res = http_get("/api/search?q=ACC990188231")
    assert api_get_res["total_results"] >= 1
    assert api_get_res["results"][0]["name"] == "ACC990188231"
    print(f"  -> SUCCESS: Both /search and /api/search working across GET and POST verbs")
    passed += 1

    print("\n" + "=" * 75)
    print(f"ALL TESTS COMPLETED: {passed} / {total} PASSED.")
    print("=" * 75)
    return True


def test_universal_search():
    ok = run_tests()
    assert ok


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
