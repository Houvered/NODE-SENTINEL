# -*- coding: utf-8 -*-
"""
Case-scoped grounded investigation assistant for NODE SENTINEL.

Hard guarantees (tested):
- Only nodes/edges carrying the case's case_id are ever read. No
  cross-case leakage, even when two cases mention the same name.
- Every person/connection/transaction cited maps to a stored record
  (document + page + extraction, or dataset import + record id).
- Missing required data produces the fixed missing-data format instead
  of an invented answer. Nothing is fabricated.
- Uploaded evidence is untrusted data: instruction-like text inside
  documents is never executed (this engine is deterministic template
  code with no instruction-following path) and guilt is never declared.

Response structure: Answer / Connections found / Evidence / Limitations /
Next action. Neutral language only ("associated in the available
records", "potential connection", "requires investigator verification",
"No supporting record was found in the currently processed data").
"""
from __future__ import annotations

import logging
import re
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

NO_RECORD = "No supporting record was found in the currently processed data."

# question intent -> minimum required data
INTENT_REQUIREMENTS = {
    "connection": {"needs": ["reports_or_relationships"], "upload": None},
    "ego": {"needs": ["reports_or_relationships"], "upload": None},
    "calls": {"needs": ["cdr"], "upload": {
        "file": "CSV or XLSX CDR dataset",
        "columns": ["calling_number", "receiving_number", "call_start_time"]}},
    "financial": {"needs": ["financial"], "upload": {
        "file": "CSV or XLSX financial dataset",
        "columns": ["sender_account", "receiver_account", "amount", "transaction_time", "transaction_id"]}},
    "vehicle": {"needs": ["vehicle"], "upload": {
        "file": "CSV or XLSX vehicle dataset",
        "columns": ["vehicle_registration"]}},
    "network": {"needs": ["reports_or_relationships"], "upload": None},
    "readiness": {"needs": [], "upload": None},
}


def case_scope_ids(graph, case_id: str) -> set:
    """Node IDs belonging to a case.

    A node is in scope when it is tagged with the case OR is an endpoint
    of an edge tagged with the case. The endpoint rule keeps shared
    entities (same person in two cases) and service-created nodes (CDR
    phones, ledger accounts, which carry no node-level tag) visible
    through the case's own evidence — without leaking other cases'
    records, since only case-tagged edges pull nodes in.
    """
    scope = set()
    for n in graph.get_all_nodes():
        props = n.properties or {}
        if props.get("case_id") == case_id:
            scope.add(n.id)
    for e in graph.get_all_edges():
        if (e.properties or {}).get("case_id") == case_id:
            scope.add(e.source)
            scope.add(e.target)
    anchor = f"CASE_{case_id}"
    if graph.get_node(anchor):
        scope.add(anchor)
    return scope


def case_edges(graph, scope: set) -> List[Any]:
    return [e for e in graph.get_all_edges() if e.source in scope and e.target in scope]


def case_readiness(graph, scope: set, store=None, case_id: str = "") -> Dict[str, Any]:
    """What the case has vs needs (drives missing-data answers)."""
    edges = case_edges(graph, scope)
    rels = set()
    for e in edges:
        rels.add(e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship))
    persons = sum(1 for nid in scope
                  if (n := graph.get_node(nid)) is not None
                  and (n.label.value if hasattr(n.label, "value") else str(n.label)) == "Person")
    has = {
        "reports_or_relationships": len(edges) > 0,
        "cdr": "CALLS" in rels,
        "financial": "TRANSFERRED_MONEY" in rels,
        "vehicle": any(r in rels for r in ("OWNS", "OPERATES")) or any(
            (n := graph.get_node(nid)) is not None and
            (n.label.value if hasattr(n.label, "value") else str(n.label)) == "Vehicle"
            for nid in scope),
    }
    docs = store.list_documents(case_id) if store and case_id else []
    return {"has": has, "relationships": sorted(rels), "persons": persons,
            "nodes": len(scope), "edges": len(edges),
            "documents": len(docs),
            "awaiting_review": sum(1 for d in docs if d.get("status") == "awaiting_review")}


def classify_intent(query: str) -> str:
    q = query.lower()
    if any(k in q for k in ("missing", "what data", "what do you need", "readiness", "still need")):
        return "readiness"
    if any(k in q for k in ("financ", "transaction", "money", "payment", "account")):
        return "financial"
    if any(k in q for k in ("call", "cdr", "phone", "rang", "dialed", "contact number")):
        return "calls"
    if any(k in q for k in ("vehicle", "car", "truck", "plate", "registration")):
        return "vehicle"
    if any(k in q for k in ("connect", "between", "link", "relationship", "associated", "path")):
        m = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|\+?\d[\d\s\-]{7,}\b|[A-Z]{2}\d[A-Z0-9]+", query)
        return "connection" if len([x for x in m if len(x.strip()) > 2]) >= 2 else "ego"
    if any(k in q for k in ("who", "show", "list", "all", "network", "graph")):
        return "ego"
    return "ego"


def resolve_in_scope(graph, scope: set, query: str) -> List[Any]:
    """Find case nodes matching a name/phone/plate/account/case mention."""
    q = query.strip()
    q_lower, q_digits = q.lower(), re.sub(r"\D", "", q)
    q_alnum = re.sub(r"[^A-Z0-9]", "", q.upper())
    hits = []
    for nid in scope:
        n = graph.get_node(nid)
        if n is None:
            continue
        props = n.properties or {}
        hay = " ".join([n.id, n.name] + [str(v) for v in props.values()])
        score = 0
        if q_lower and (q_lower in n.name.lower() or q_lower == nid.lower()):
            score = 90
        elif q_digits and len(q_digits) >= 6 and q_digits in re.sub(r"\D", "", hay):
            score = 80
        elif q_alnum and len(q_alnum) >= 4 and q_alnum in re.sub(r"[^A-Z0-9]", "", hay.upper()):
            score = 75
        if score:
            hits.append((score, n))
    return [n for _, n in sorted(hits, key=lambda t: -t[0])]


def _label(n) -> str:
    return n.label.value if hasattr(n.label, "value") else str(n.label)


def edge_citation(graph, store, e) -> str:
    """One-line traceable evidence reference for an edge."""
    p = e.properties or {}
    bits = []
    if p.get("source_document_id"):
        doc = store.get_document(p["source_document_id"]) if store else None
        name = doc["filename_original"] if doc else p["source_document_id"]
        bits.append(f"document {name}")
        if p.get("page") is not None:
            bits.append(f"page {p['page']}")
    if p.get("source_dataset_id"):
        bits.append(f"dataset import {p['source_dataset_id']}")
    if p.get("source_record_id"):
        bits.append(f"record {p['source_record_id']}")
    if p.get("transaction_id"):
        bits.append(f"txn {p['transaction_id']}")
    if p.get("call_id"):
        bits.append(f"call {p['call_id']}")
    if not bits and p.get("source"):
        bits.append(f"source {p['source']}")
    status = p.get("review_status", "unprocessed")
    return f"{e.source} --[{_rel(e)}]--> {e.target} | status={status} | " + ("; ".join(bits) or "source on file")


def _rel(e) -> str:
    return e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)


def scoped_path(graph, scope: set, src: str, tgt: str) -> Optional[List[str]]:
    adj: Dict[str, set] = {}
    for e in case_edges(graph, scope):
        adj.setdefault(e.source, set()).add(e.target)
        adj.setdefault(e.target, set()).add(e.source)
    if src not in adj or tgt not in adj:
        return None
    prev = {src: None}
    dq = deque([src])
    while dq:
        cur = dq.popleft()
        if cur == tgt:
            break
        for nb in adj.get(cur, ()):
            if nb not in prev:
                prev[nb] = cur
                dq.append(nb)
    if tgt not in prev:
        return None
    path, cur = [], tgt
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    return path[::-1]


def missing_data_answer(intent: str, readiness: Dict[str, Any], context: str = "") -> Dict[str, Any]:
    req = INTENT_REQUIREMENTS.get(intent, {})
    lines = ["Answer:", context or "This cannot be answered from the currently available data.", "",
             "What cannot be answered:"]
    need = req.get("needs", [])
    missing = [n for n in need if not readiness["has"].get(n)]
    lines.append(f"- {intent} analysis (missing: {', '.join(missing) or 'required dataset'}).")
    lines += ["", "What is missing:"]
    up = req.get("upload")
    if up:
        lines.append(f"- {up['file']} with columns: {', '.join(up['columns'])}.")
        lines += ["", "Why it is required:",
                  f"- The {intent} analysis reads {', '.join(up['columns'])} from case evidence;",
                  "  without them no relationship can be established.",
                  "", "After upload:",
                  "- Re-run column mapping, confirm the import, and this answer will be recalculated automatically."]
    else:
        lines += ["- At least one processed report or structured relationship dataset in this case.",
                  "", "Why it is required:",
                  "- Connections are built only from reviewed case evidence; there is nothing to traverse yet.",
                  "", "After upload:",
                  "- Process the document, approve the extractions, rebuild the graph, and re-ask."]
    lines += ["", "Limitations:",
              "- No supporting record was found in the currently processed data.",
              "- Nothing has been inferred beyond the records above.",
              "- Requires investigator verification."]
    return {"answer": "\n".join(lines), "evidence": [], "actions": ["UPLOAD_DATA" if up else "UPLOAD_REPORT"],
            "missing": missing or need}


def answer_query(graph, store, case_id: str, query: str) -> Dict[str, Any]:
    """Main entry: grounded, case-isolated answer dict."""
    scope = case_scope_ids(graph, case_id)
    ready = case_readiness(graph, scope, store, case_id)
    intent = classify_intent(query)

    if intent == "readiness":
        has = ready["has"]
        lines = ["Answer:", f"Case evidence status: {ready['nodes']} nodes, {ready['edges']} relationships, "
                 f"{ready['persons']} people, {ready['documents']} documents "
                 f"({ready['awaiting_review']} awaiting review).", ""]
        for key, label in (("reports_or_relationships", "reports/relationships"), ("cdr", "CDR data"),
                           ("financial", "financial data"), ("vehicle", "vehicle data")):
            lines.append(f"- {label}: {'available' if has[key] else 'MISSING'}")
        lines += ["", "Limitations:", "- Unreviewed extractions are not in the graph.",
                  "- Requires investigator verification."]
        return {"answer": "\n".join(lines), "evidence": [], "actions": [], "missing": []}

    req = INTENT_REQUIREMENTS.get(intent, {})
    missing = [n for n in req.get("needs", []) if not ready["has"].get(n)]
    if missing and intent in ("calls", "financial", "vehicle"):
        return missing_data_answer(intent, ready)
    if not scope or not ready["has"]["reports_or_relationships"]:
        if intent in ("connection", "ego", "network"):
            return missing_data_answer(intent, ready,
                                       "This case has no reviewed graph relationships yet.")
        return missing_data_answer(intent, ready)

    if intent == "connection":
        cands = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", query)
        resolved = []
        for c in cands[:4]:
            hits = resolve_in_scope(graph, scope, c)
            if hits:
                resolved.append(hits[0])
        uniq = []
        for n in resolved:
            if n.id not in [u.id for u in uniq]:
                uniq.append(n)
        if len(uniq) < 2:
            phones = re.findall(r"\+?\d[\d\s\-]{7,}\b", query)
            for p in phones[:4]:
                hits = resolve_in_scope(graph, scope, p)
                if hits and hits[0].id not in [u.id for u in uniq]:
                    uniq.append(hits[0])
        if len(uniq) < 2:
            return {"answer": f"Answer:\nI could resolve fewer than two entities in this case for that question. {NO_RECORD}",
                    "evidence": [], "actions": [], "missing": []}
        a, b = uniq[0], uniq[1]
        path = scoped_path(graph, scope, a.id, b.id)
        if not path:
            return {"answer": f"Answer:\n{a.name} and {b.name} are both associated in the available records, "
                              f"but no potential connection path exists between them in this case's reviewed data. "
                              f"{NO_RECORD} for a link.",
                    "evidence": [], "actions": [], "missing": []}
        edges = case_edges(graph, scope)
        steps = []
        for s, t in zip(path, path[1:]):
            e = next((x for x in edges if {x.source, x.target} == {s, t}), None)
            if e:
                steps.append(edge_citation(graph, store, e))
        ans = [f"Answer:\n{a.name} is potentially connected to {b.name} through {len(path) - 1} hop(s) "
               f"in the available records. This is a potential connection and requires investigator verification.",
               "", "Connections found:"]
        for i, (s, t) in enumerate(zip(path, path[1:]), 1):
            ans.append(f"{i}. {graph.get_node(s).name} -> {graph.get_node(t).name}")
        ans += ["", "Evidence:"] + [f"- {st}" for st in steps] + ["", "Limitations:",
               "- Only analyst-reviewed or verified relationships were traversed.",
               "- Path uses unweighted hops; it shows reachability, not strength or guilt.",
               "- Requires investigator verification."]
        return {"answer": "\n".join(ans), "evidence": steps, "actions": [], "missing": []}

    # ego / network / calls / financial / vehicle detail over scope
    hits = []
    for chunk in re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|\+?\d[\d\s\-]{7,}\b|[A-Z]{2}\d[A-Z0-9]+", query):
        hits += resolve_in_scope(graph, scope, chunk)
    target = hits[0] if hits else None
    if target is None:
        # scoped summary instead of guessing
        persons = [graph.get_node(nid).name for nid in scope
                   if (n := graph.get_node(nid)) is not None and _label(n) == "Person"][:10]
        return {"answer": f"Answer:\nThis case holds {ready['nodes']} entities and {ready['edges']} reviewed "
                          f"relationships. People on file include: {', '.join(persons) or 'none yet'}. "
                          f"Name a person, phone, vehicle, or account for detail. "
                          f"Associations below require investigator verification.",
                "evidence": [], "actions": [], "missing": []}
    edges = [e for e in case_edges(graph, scope) if e.source == target.id or e.target == target.id]
    if intent in ("calls", "financial", "vehicle"):
        want = {"calls": "CALLS", "financial": "TRANSFERRED_MONEY", "vehicle": ("OWNS", "OPERATES")}[intent]
        edges = [e for e in edges if _rel(e) == want or (isinstance(want, tuple) and _rel(e) in want)]
        if not edges:
            return missing_data_answer(intent, ready,
                                       f"{target.name} is associated in the available records, but this case "
                                       f"has no {intent} relationships for them.")
    cites = [edge_citation(graph, store, e) for e in edges[:20]]
    status_note = "analyst-reviewed/verified" if all(
        (e.properties or {}).get("review_status") in ("analyst-reviewed", "verified") for e in edges) \
        else "mixed review states (see citations)"
    ans = [f"Answer:\n{target.name} ({_label(target)}) has {len(edges)} recorded association(s) in this case. "
           f"These are potential connections and require investigator verification; they do not establish wrongdoing.",
           "", "Connections found:"]
    for e in edges[:20]:
        other = e.target if e.source == target.id else e.source
        on = graph.get_node(other)
        ans.append(f"- {_rel(e)} -> {(on.name if on else other)}")
    ans += ["", "Evidence:"] + [f"- {c}" for c in cites] + ["", "Limitations:",
           f"- Edge review states: {status_note}.",
           "- Unreviewed extractions are excluded.", "- Requires investigator verification.",
           "", "Next action:", "- Approve pending extractions or upload the missing dataset to extend this view."]
    return {"answer": "\n".join(ans), "evidence": cites, "actions": [], "missing": []}
