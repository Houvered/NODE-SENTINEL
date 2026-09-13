# -*- coding: utf-8 -*-
"""
Verification and Test Suite for STEP 8: Upgrade the existing Knowledge Graph.

Tests:
1. Graph Search: Search by exact Node ID ('PERSON_TARIQ_AHMAD')
2. Graph Search: Search by partial name ('Tariq')
3. Node Expansion (1-hop): GET /api/network/node/{id}/expand?depth=1
4. Direct Connections: Verify 1-hop neighbor count and connectivity
5. Extended Network (2-hop): GET /api/network/node/{id}/expand?depth=2
6. Shortest Path Finder: Connected nodes (Tariq Ahmad -> DL01AB9988 / Vikram Malhotra)
7. Shortest Path Finder: Disconnected/Unknown node -> clean path_found=False
8. Shortest Path Finder: Same source and target handling
9. Graph Metadata & Filters: GET /api/network/meta returns node & edge types
10. Node Investigative Details: GET /api/network/node/{id}/details (cases, phones, vehicles)
11. Error Handling: Non-existent node details returns 404
12. Universal Search -> Knowledge Graph linkage
13. Face Search -> Knowledge Graph linkage
14. Core Network Analytics Preservation (/graph, /alerts, /centrality, /communities)
15. Frontend UI Compliance (required buttons, controls, and disclaimer wording)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

BASE_URL = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:8000")

# Setup TestClient as fallback for offline execution
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
except Exception as e:
    _test_client = None


def http_get(path: str) -> tuple[int, dict]:
    if _test_client is not None:
        resp = _test_client.get(path)
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {"error": resp.text}
    try:
        url = f"{BASE_URL}{path}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except Exception:
        raise


def http_post_multipart(path: str, file_path: Path) -> dict:
    if _test_client is not None:
        with open(file_path, "rb") as f:
            resp = _test_client.post(path, files={"file": (file_path.name, f.read(), "image/png")})
        return resp.json()
    try:
        url = f"{BASE_URL}{path}"
        boundary = "----WebKitFormBoundarySentinelGraphStep8"
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
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        raise


def run_all_tests():
    print("=" * 70)
    print("STEP 8 VERIFICATION: UPGRADED KNOWLEDGE GRAPH TEST SUITE")
    print("=" * 70)

    # TEST 1: Graph Search by exact Node ID
    print("\n[TEST 1] Testing Graph Search by exact node ID ('PERSON_TARIQ_AHMAD')...")
    status, res = http_get("/api/network/search?query=PERSON_TARIQ_AHMAD&depth=1")
    assert status == 200, f"Expected 200, got {status}"
    assert len(res["nodes"]) >= 1, "Expected at least 1 matching node"
    target_node = next((n for n in res["nodes"] if n["id"] == "PERSON_TARIQ_AHMAD"), None)
    assert target_node is not None, "PERSON_TARIQ_AHMAD not found in search results"
    print(f"  -> SUCCESS: Found node '{target_node['name']}' with {len(res['edges'])} connected edges.")

    # TEST 2: Graph Search by partial name
    print("\n[TEST 2] Testing Graph Search by partial name ('Tariq')...")
    status, res = http_get("/api/network/search?query=Tariq&depth=1")
    assert status == 200
    assert any("Tariq" in n.get("name", "") for n in res["nodes"])
    print(f"  -> SUCCESS: Partial search 'Tariq' returned {len(res['nodes'])} nodes.")

    # TEST 2b: Multi-modal Graph Search (Phone & Vehicle)
    print("\n[TEST 2b] Testing Multi-modal Graph Search by phone number & vehicle plate...")
    status, res_phone = http_get("/api/network/search?query=9811223344&depth=1")
    assert status == 200
    assert len(res_phone["nodes"]) >= 1, "Expected at least 1 node matching phone 9811223344"
    status, res_veh = http_get("/api/network/search?query=DL01AB9988&depth=1")
    assert status == 200
    assert len(res_veh["nodes"]) >= 1, "Expected at least 1 node matching vehicle DL01AB9988"
    print(f"  -> SUCCESS: Phone search returned {len(res_phone['nodes'])} nodes; vehicle search returned {len(res_veh['nodes'])} nodes.")

    # TEST 3: Node Expansion (1-hop)
    print("\n[TEST 3] Testing Node Expansion endpoint (/api/network/node/PERSON_TARIQ_AHMAD/expand?depth=1)...")
    status, res = http_get("/api/network/node/PERSON_TARIQ_AHMAD/expand?depth=1")
    assert status == 200
    assert res["center_node_id"] == "PERSON_TARIQ_AHMAD"
    assert res["depth"] == 1
    depth1_nodes_count = len(res["nodes"])
    depth1_edges_count = len(res["edges"])
    print(f"  -> SUCCESS: 1-hop expansion returned {depth1_nodes_count} nodes, {depth1_edges_count} edges.")

    # TEST 4: Direct Connections Verification
    print("\n[TEST 4] Testing Direct Connections calculation...")
    status, details = http_get("/api/network/node/PERSON_TARIQ_AHMAD/details")
    assert status == 200
    direct_count = details["direct_count"]
    # Depth 1 includes the center node itself, so nodes = direct_count + 1
    assert depth1_nodes_count == direct_count + 1, f"Mismatch: depth1 nodes {depth1_nodes_count} vs direct {direct_count} + 1"
    print(f"  -> SUCCESS: Direct connections = {direct_count} precisely matches 1-hop neighbor count.")

    # TEST 5: Extended Network (2-hop)
    print("\n[TEST 5] Testing Extended Network endpoint (/api/network/node/PERSON_TARIQ_AHMAD/expand?depth=2)...")
    status, res = http_get("/api/network/node/PERSON_TARIQ_AHMAD/expand?depth=2")
    assert status == 200
    assert res["depth"] == 2
    depth2_nodes_count = len(res["nodes"])
    assert depth2_nodes_count >= depth1_nodes_count, "2-hop network should be >= 1-hop network"
    print(f"  -> SUCCESS: 2-hop extended network has {depth2_nodes_count} nodes (>= 1-hop: {depth1_nodes_count}).")

    # TEST 6: Shortest Path Finder (Connected nodes)
    print("\n[TEST 6] Testing Shortest Path Finder between connected entities...")
    status, res = http_get("/api/network/shortest-path?source=PERSON_TARIQ_AHMAD&target=VEHICLE_DL01AB9988")
    assert status == 200
    assert res["path_found"] is True
    assert res["length"] >= 1
    assert len(res["path_ids"]) == res["length"] + 1
    assert len(res["steps"]) == res["length"]
    assert res["path_ids"][0] == "PERSON_TARIQ_AHMAD"
    assert res["path_ids"][-1] in ("VEHICLE_DL01AB9988", "VEH_DL01AB9988")
    step_str = " -> ".join([f"{s['from_name']} --[{s['relationship']}]--> {s['to_name']}" for s in res["steps"]])
    print(f"  -> SUCCESS: Path found (hops={res['length']}): {step_str}")

    # TEST 6b: /network/path alias endpoint
    status_p, res_p = http_get("/api/network/path?source=PERSON_TARIQ_AHMAD&target=VEHICLE_DL01AB9988")
    assert status_p == 200 and res_p["path_found"] is True, "Expected /network/path to return identical path result"
    print(f"  -> SUCCESS: Verified /network/path alias returns valid path.")

    # TEST 7: Shortest Path Finder (Non-existent target -> clean failure)
    print("\n[TEST 7] Testing Shortest Path with non-existent target entity...")
    status, res = http_get("/api/network/shortest-path?source=PERSON_TARIQ_AHMAD&target=NON_EXISTENT_ENTITY_XYZ")
    assert status == 200
    assert res["path_found"] is False
    assert "not found" in res["message"].lower()
    print(f"  -> SUCCESS: Clean response: '{res['message']}' without 500 error.")

    # TEST 8: Shortest Path Finder (Same source and target)
    print("\n[TEST 8] Testing Shortest Path with identical source and target...")
    status, res = http_get("/api/network/shortest-path?source=PERSON_TARIQ_AHMAD&target=PERSON_TARIQ_AHMAD")
    assert status == 200
    assert res["path_found"] is True
    assert res["length"] == 0
    print(f"  -> SUCCESS: Same entity path returned hops=0 cleanly.")

    # TEST 9: Graph Metadata & Filter Types
    print("\n[TEST 9] Testing Graph Metadata endpoint (/api/network/meta)...")
    status, meta = http_get("/api/network/meta")
    assert status == 200
    node_types = meta["node_types"]
    rel_types = meta["relationship_types"]
    assert "Person" in node_types
    assert "Phone" in node_types or "Vehicle" in node_types
    assert len(rel_types) > 0
    print(f"  -> SUCCESS: Metadata returns {len(node_types)} node types and {len(rel_types)} relationship types.")

    # TEST 10: Node Investigative Details
    print("\n[TEST 10] Testing Node Investigative Details (/api/network/node/{id}/details)...")
    status, nd = http_get("/api/network/node/PERSON_TARIQ_AHMAD/details")
    assert status == 200
    for key in ["node_id", "name", "node_type", "cases", "phones", "vehicles", "direct_count", "indirect_count"]:
        assert key in nd, f"Missing key '{key}' in node details"
    print(f"  -> SUCCESS: Node details for '{nd['name']}': direct={nd['direct_count']}, indirect={nd['indirect_count']}, cases={nd['cases']}, phones={nd['phones']}, vehicles={nd['vehicles']}")

    # TEST 11: Error Handling for Non-existent Node Details
    print("\n[TEST 11] Testing error handling for non-existent node details...")
    status, err_resp = http_get("/api/network/node/GHOST_ID_999/details")
    assert status == 404, f"Expected 404, got {status}"
    print(f"  -> SUCCESS: Correct 404 returned for unknown node: {err_resp}")

    # TEST 12: Universal Search -> Knowledge Graph Linkage
    print("\n[TEST 12] Testing Universal Search -> Knowledge Graph linkage...")
    status, u_search = http_get("/api/search?q=Tariq%20Ahmad")
    assert status == 200
    assert u_search["total_results"] >= 1
    person_entity = u_search["results"][0]
    person_id = person_entity["entity_id"]
    # Check that this person_id exists in the graph and can be expanded
    status, exp = http_get(f"/api/network/node/{person_id}/expand?depth=1")
    assert status == 200
    print(f"  -> SUCCESS: Universal search entity '{person_id}' seamlessly links to graph node with {len(exp['nodes'])} entities.")

    # TEST 13: Face Search -> Knowledge Graph Linkage & Wording
    print("\n[TEST 13] Testing Biometric Face Search -> Knowledge Graph linkage...")
    probe_img = Path(__file__).resolve().parent / "sample_data" / "face_database" / "images" / "probe_tariq.png"
    face_resp = http_post_multipart("/api/search/face", probe_img)
    assert face_resp["status"] == "ok"
    matched_id = face_resp["matched_person_id"]
    assert matched_id == "PERSON_TARIQ_AHMAD"
    # Verify matched_id expands in the graph
    status, face_exp = http_get(f"/api/network/node/{matched_id}/expand?depth=1")
    assert status == 200
    print(f"  -> SUCCESS: Face probe matched '{matched_id}', verified active in Knowledge Graph.")

    # TEST 14: Core Analytics Preservation
    print("\n[TEST 14] Testing preservation of existing analytics endpoints...")
    s_graph, graph_data = http_get("/api/network/graph")
    assert s_graph == 200 and len(graph_data["nodes"]) > 0
    s_alerts, alerts_data = http_get("/api/network/alerts")
    assert s_alerts == 200 and "alerts" in alerts_data
    s_cent, cent_data = http_get("/api/network/analytics/centrality?top_k=5")
    assert s_cent == 200 and "items" in cent_data
    s_comm, comm_data = http_get("/api/network/analytics/communities")
    assert s_comm == 200 and "communities" in comm_data
    print(f"  -> SUCCESS: All 4 core analytics endpoints (/graph, /alerts, /centrality, /communities) fully operational.")

    # TEST 15: Frontend Compliance Check
    print("\n[TEST 15] Checking frontend HTML compliance in static/index.html...")
    index_html = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")

    required_snippets = [
        "[Expand Connections]",
        "[Show Direct Connections]",
        "[Show Extended Network]",
        "[Focus Node]",
        "[Find Connection]",
        "[Reset Graph]",
        "pathSourceInput",
        "pathTargetInput",
        "pathFinderBar",
        "pathResultPanel",
        "node-details-panel",
        "Possible Match",
        "Verification Required",
    ]

    for snip in required_snippets:
        assert snip in index_html, f"Frontend compliance missing required snippet: '{snip}'"
        print(f"    [OK] Found '{snip}'")

    print(f"  -> SUCCESS: Frontend includes all required controls, panels, and disclaimer compliance wording.")

    print("\n" + "=" * 70)
    print("ALL 15 TESTS PASSED SUCCESSFULLY! STEP 8 IS COMPLETE.")
    print("=" * 70)


def test_graph_upgrade():
    """Pytest entrypoint."""
    run_all_tests()


if __name__ == "__main__":
    run_all_tests()
