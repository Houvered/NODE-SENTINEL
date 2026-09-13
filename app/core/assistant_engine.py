# -*- coding: utf-8 -*-
"""
NODE SENTINEL - AI Investigation Assistant Engine (STEP 13)
Provides an investigator-facing conversational assistant strictly grounded in
the existing Knowledge Graph, CDR Telemetry, Financial Ledger, Timeline, and
Risk Intelligence. Never hallucinates unrecorded entities or criminal verdicts.
"""
from __future__ import annotations

import re
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.ai_provider import BaseAIProvider, get_ai_provider
from app.core.anomaly_detector import AnomalyDetector
from app.core.cdr_analytics import CDRService
from app.core.face_storage import FaceStorage, get_face_storage
from app.core.financial_analytics import FinancialService
from app.core.graph_analytics import GraphAnalytics
from app.core.graph_engine import BaseGraphEngine, get_graph_engine
from app.core.timeline_engine import TimelineEngine, get_timeline_engine
from app.core.universal_search import UniversalSearchEngine, get_universal_search
from app.models.assistant_models import (
    AssistantAction,
    AssistantActionType,
    AssistantMessage,
    AssistantRole,
    AssistantSource,
    InvestigationAssistantRequest,
    InvestigationAssistantResponse,
)
from app.models.graph_models import NodeType

logger = logging.getLogger(__name__)


class AssistantEngine:
    """
    Investigator Decision-Support Assistant Engine.
    Coordinates retrieval across existing analytics, handles multi-turn conversation context,
    resolves query entities, and synthesizes grounded, explainable answers.
    """

    def __init__(
        self,
        graph_engine: Optional[BaseGraphEngine] = None,
        search_engine: Optional[UniversalSearchEngine] = None,
        timeline_engine: Optional[TimelineEngine] = None,
        provider: Optional[BaseAIProvider] = None,
    ) -> None:
        self.graph = graph_engine or get_graph_engine()
        self.search = search_engine or get_universal_search()
        self.timeline = timeline_engine or get_timeline_engine()
        self.provider = provider or get_ai_provider()
        self.analytics = GraphAnalytics(self.graph)
        self.anomaly_detector = AnomalyDetector(self.graph)
        self.cdr_service = CDRService(self.graph)
        self.financial_service = FinancialService(self.graph)
        self.face_storage = get_face_storage()

        # In-memory conversation state: conversation_id -> {selected_entity_id, history, updated_at}
        self._conversations: Dict[str, Dict[str, Any]] = {}

    # -------------------------------------------------------------------------
    # Conversation State Management
    # -------------------------------------------------------------------------

    def get_or_create_conversation(self, conversation_id: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        cid = conversation_id.strip() if conversation_id and conversation_id.strip() else f"conv_{uuid.uuid4().hex[:12]}"
        if cid not in self._conversations:
            self._conversations[cid] = {
                "conversation_id": cid,
                "selected_entity_id": None,
                "selected_entity_name": None,
                "history": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        return cid, self._conversations[cid]

    def reset_conversation(self, conversation_id: str) -> bool:
        if conversation_id in self._conversations:
            self._conversations[conversation_id]["selected_entity_id"] = None
            self._conversations[conversation_id]["selected_entity_name"] = None
            self._conversations[conversation_id]["history"].clear()
            self._conversations[conversation_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            return True
        return False

    # -------------------------------------------------------------------------
    # Entity Resolution & Pronoun Handling
    # -------------------------------------------------------------------------

    def _resolve_entity(
        self,
        query: str,
        explicit_entity_id: Optional[str],
        session: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], bool, Optional[str], List[Dict[str, Any]]]:
        """
        Resolves the focal entity for the query.
        Returns: (resolved_entity_dict, is_ambiguous, clarification_prompt, candidate_list)
        """
        # 1. Explicit ID provided in request
        if explicit_entity_id and explicit_entity_id.strip():
            node = self.graph.get_node(explicit_entity_id.strip())
            if node:
                ent = {
                    "entity_id": node.id,
                    "name": node.name,
                    "entity_type": node.label.value if hasattr(node.label, "value") else str(node.label),
                }
                session["selected_entity_id"] = node.id
                session["selected_entity_name"] = node.name
                return ent, False, None, []

        q_clean = query.strip()

        # Check for shortest-path query first (A and B pattern handled separately)
        if re.search(r"\bbetween\b.+\band\b", q_clean, re.IGNORECASE):
            return None, False, None, []

        # Check known node exact ID matches in graph
        for node in self.graph.get_all_nodes():
            if node.id.lower() in q_clean.lower():
                ent = {
                    "entity_id": node.id,
                    "name": node.name,
                    "entity_type": node.label.value if hasattr(node.label, "value") else str(node.label),
                }
                session["selected_entity_id"] = node.id
                session["selected_entity_name"] = node.name
                return ent, False, None, []

        # Check known node exact name matches in graph (case-insensitive boundary)
        for node in self.graph.get_all_nodes():
            if len(node.name) >= 3 and re.search(rf"\b{re.escape(node.name)}\b", q_clean, re.IGNORECASE):
                ent = {
                    "entity_id": node.id,
                    "name": node.name,
                    "entity_type": node.label.value if hasattr(node.label, "value") else str(node.label),
                }
                session["selected_entity_id"] = node.id
                session["selected_entity_name"] = node.name
                return ent, False, None, []

        # Check for entity extraction via search terms (names, phone, account, vehicle, case)
        extracted_terms = self._extract_entity_terms(q_clean)
        for term in extracted_terms:
            search_res = self.search.search(term, limit=5)
            if search_res.total_results == 1:
                top = search_res.results[0]
                ent = {"entity_id": top.entity_id, "name": top.name, "entity_type": top.entity_type}
                session["selected_entity_id"] = top.entity_id
                session["selected_entity_name"] = top.name
                return ent, False, None, []
            elif search_res.total_results > 1:
                # Check for exact match first
                exact = next((r for r in search_res.results if r.name.lower() == term.lower() or r.entity_id.lower() == term.lower()), None)
                if exact:
                    ent = {"entity_id": exact.entity_id, "name": exact.name, "entity_type": exact.entity_type}
                    session["selected_entity_id"] = exact.entity_id
                    session["selected_entity_name"] = exact.name
                    return ent, False, None, []
                # Ambiguous match detected!
                cands = [{"entity_id": r.entity_id, "name": r.name, "entity_type": r.entity_type} for r in search_res.results[:4]]
                clarification = f"Multiple entities match '{term}': {', '.join(c['name'] + ' (' + c['entity_id'] + ')' for c in cands)}. Please specify which entity you want to inspect."
                return None, True, clarification, cands

        # 3. Follow-up pronoun or relative intent resolution
        has_pronoun = bool(re.search(r"\b(his|her|their|this person|this entity|him|he|she|them|the suspect|selected entity)\b", q_clean, re.IGNORECASE))
        has_relative_intent = bool(re.search(r"\b(calls|finances|connections|timeline|risk|cases|activity)\b", q_clean, re.IGNORECASE))

        if (has_pronoun or has_relative_intent) and session.get("selected_entity_id"):
            node = self.graph.get_node(session["selected_entity_id"])
            if node:
                ent = {
                    "entity_id": node.id,
                    "name": node.name,
                    "entity_type": node.label.value if hasattr(node.label, "value") else str(node.label),
                }
                return ent, False, None, []

        return None, False, None, []

    def _extract_entity_terms(self, query: str) -> List[str]:
        terms: List[str] = []
        # Case codes
        cases = re.findall(r"\b(?:FIR|CASE)[-_\w]+\b", query, re.IGNORECASE)
        terms.extend(cases)
        # Bank accounts
        accounts = re.findall(r"\b(?:ACC[_\w]+|\d{9,16})\b", query, re.IGNORECASE)
        terms.extend(accounts)
        # Phone numbers
        phones = re.findall(r"(?:\+91[\s-]?)?[6-9]\d{9}", query)
        terms.extend(phones)
        # Vehicle plates
        plates = re.findall(r"\b[A-Z]{2}[-\s]?\d{1,2}[-\s]?[A-Z]{1,3}[-\s]?\d{1,4}\b", query)
        terms.extend(plates)
        # Specific named entities target pattern: "connected to X", "for X", "is X", "show X"
        target_patterns = [
            r"(?:connected to|strongest connections for|connections for|who is|show|about|for)\s+([A-Za-z0-9_\-+]+(?:\s+[A-Za-z0-9_\-+]+)?)",
        ]
        stop_words = {
            "his", "her", "their", "this person", "this entity", "him", "he", "she", "them", "a", "the",
            "unusual", "call", "calls", "financial", "finances", "activity", "connections", "timeline",
            "cases", "case", "investigation", "pattern", "flow", "key connections", "communication pattern", "financial flow"
        }
        for pat in target_patterns:
            matches = re.findall(pat, query, re.IGNORECASE)
            for m in matches:
                clean_m = m.strip().strip("?.!,")
                if clean_m.lower() not in stop_words and len(clean_m) >= 2:
                    terms.append(clean_m)
        # Proper names (two capitalized words)
        names = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", query)
        terms.extend(names)
        return list(dict.fromkeys(terms))

    # -------------------------------------------------------------------------
    # Main Query Dispatcher
    # -------------------------------------------------------------------------

    def process_query(self, req: InvestigationAssistantRequest) -> InvestigationAssistantResponse:
        cid, session = self.get_or_create_conversation(req.conversation_id)
        query = req.query.strip()

        # Update session history with user message
        user_msg = AssistantMessage(role=AssistantRole.USER, content=query)
        session["history"].append(user_msg.model_dump())

        # Check entity resolution
        ent, is_ambiguous, clarification, candidates = self._resolve_entity(query, req.selected_entity_id, session)

        if is_ambiguous:
            res = InvestigationAssistantResponse(
                answer=f"Multiple entities matched your inquiry: {', '.join(c['name'] + ' (' + c['entity_id'] + ')' for c in candidates)}. Please clarify which specific entity you wish to investigate.",
                is_ambiguous=True,
                clarification_needed=clarification,
                candidate_entities=candidates,
                evidence=["Query matches multiple registered records without exact distinction."],
                uncertainty="Entity ambiguity prevents single-subject telemetry analysis. Investigator clarification required.",
                conversation_id=cid,
            )
            session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
            return res

        q_lower = query.lower()

        # Intent 1: Shortest Path Finder
        if "shortest" in q_lower or ("connected to" in q_lower and "between" in q_lower) or ("how is" in q_lower and "connected to" in q_lower) or ("between" in q_lower and "and" in q_lower):
            return self._handle_shortest_path(query, cid, session)

        # Intent 2: Investigation Overview / Summary
        if ("summarize this investigation" in q_lower or "investigation summary" in q_lower or
            "overview of this investigation" in q_lower or "overall investigation" in q_lower):
            return self._handle_investigation_summary(cid, session)

        # Intent 3: Risk Explanation
        if ("why" in q_lower and "risk" in q_lower) or "explain risk" in q_lower or "risk elevated" in q_lower:
            return self._handle_risk_explanation(ent, query, cid, session)

        # Intent 4: Call / Communication Telemetry
        if "call" in q_lower or "cdr" in q_lower or "communication pattern" in q_lower:
            return self._handle_call_telemetry(ent, query, cid, session)

        # Intent 5: Financial Flow / Activity
        if "financial" in q_lower or "transaction" in q_lower or "money flow" in q_lower or "hawala" in q_lower or "account" in q_lower:
            return self._handle_financial_telemetry(ent, query, cid, session)

        # Intent 6: Cases / FIR Involvements
        if "case" in q_lower or "fir" in q_lower or "charge" in q_lower or "offense" in q_lower:
            return self._handle_case_linkages(ent, query, cid, session)

        # Intent 7: Timeline / Date Queries
        if "timeline" in q_lower or "happened around" in q_lower or "what happened" in q_lower or "chronology" in q_lower or re.search(r"\b\d{4}-\d{2}-\d{2}\b", query):
            return self._handle_timeline_query(ent, query, cid, session)

        # Intent 8: Connections / Network Structure
        if "connected to" in q_lower or "connection" in q_lower or "network" in q_lower or "associates" in q_lower or "who is" in q_lower:
            return self._handle_connections(ent, query, cid, session)

        # Fallback: General Entity Profile or Not Found
        if ent:
            return self._handle_entity_profile(ent, query, cid, session)

        # Check if the query is referring to an unknown entity
        extracted_terms = self._extract_entity_terms(query)
        target_name = extracted_terms[0] if extracted_terms else query
        ans = f"No supporting record was found in the current investigation dataset for '{target_name}'."
        res = InvestigationAssistantResponse(
            answer=ans,
            evidence=[],
            uncertainty="No corresponding node, phone, bank account, vehicle, or case exists in active telemetry.",
            conversation_id=cid,
        )
        session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=ans).model_dump())
        return res

    # -------------------------------------------------------------------------
    # Intent Handlers
    # -------------------------------------------------------------------------

    def _handle_connections(self, ent: Optional[Dict[str, Any]], query: str, cid: str, session: Dict[str, Any]) -> InvestigationAssistantResponse:
        if not ent:
            return self._entity_missing_response("connections", cid, query)

        node_id = ent["entity_id"]
        node_name = ent["name"]
        neighbor_data = self.graph.get_neighbors(node_id, depth=1)
        neighbor_nodes = [n for n in neighbor_data.get("nodes", []) if n.id != node_id]
        edges = neighbor_data.get("edges", [])

        if not neighbor_nodes:
            ans = f"ANSWER\n\nNo active direct connections are currently recorded for '{node_name}' ({node_id}) in the Knowledge Graph."
            return InvestigationAssistantResponse(
                answer=ans,
                selected_entity=ent,
                evidence=[f"Node '{node_name}' has 0 active edge relationships in the graph."],
                uncertainty="Entity appears isolated or unconnected in the ingested dataset.",
                actions=[AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=node_id)],
                conversation_id=cid,
            )

        # Categorize connections
        persons = [n for n in neighbor_nodes if (n.label.value if hasattr(n.label, "value") else str(n.label)) == NodeType.PERSON.value]
        phones = [n for n in neighbor_nodes if (n.label.value if hasattr(n.label, "value") else str(n.label)) == NodeType.PHONE.value]
        vehicles = [n for n in neighbor_nodes if (n.label.value if hasattr(n.label, "value") else str(n.label)) == NodeType.VEHICLE.value]
        accounts = [n for n in neighbor_nodes if (n.label.value if hasattr(n.label, "value") else str(n.label)) == NodeType.BANK_ACCOUNT.value]
        cases = [n for n in neighbor_nodes if (n.label.value if hasattr(n.label, "value") else str(n.label)) == NodeType.CASE.value]

        # Strongest connections by edge weight / frequency
        edge_summary = []
        for e in edges:
            other_id = e.target if e.source == node_id else e.source
            other_node = self.graph.get_node(other_id)
            other_name = other_node.name if other_node else other_id
            rel_str = e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)
            wt = e.properties.get("weight") or e.properties.get("count") or 1.0
            edge_summary.append((other_name, other_id, rel_str, float(wt)))

        edge_summary.sort(key=lambda x: x[3], reverse=True)
        top_conn_strings = [f"{name} ({rel} - weight {wt:.0f})" for name, oid, rel, wt in edge_summary[:5]]

        evidence_list = [
            f"{len(neighbor_nodes)} direct 1-hop connection(s) verified in Knowledge Graph.",
            f"Strongest observed interactions: {', '.join(top_conn_strings)}." if top_conn_strings else "Standard association links.",
        ]

        # Formatted Answer
        ans_lines = [
            f"ANSWER",
            f"",
            f"'{node_name}' ({ent.get('entity_type', 'Entity')}) has {len(neighbor_nodes)} direct connection(s) in the Knowledge Graph.",
        ]
        if persons:
            ans_lines.append(f"• Connected Individuals: {', '.join(p.name for p in persons)}")
        if phones:
            ans_lines.append(f"• Associated Phones: {', '.join(p.name for p in phones)}")
        if vehicles:
            ans_lines.append(f"• Associated Vehicles: {', '.join(v.name for v in vehicles)}")
        if accounts:
            ans_lines.append(f"• Associated Bank Accounts: {', '.join(a.name for a in accounts)}")
        if cases:
            ans_lines.append(f"• Linked Criminal Cases: {', '.join(c.name for c in cases)}")

        ans_lines.extend([
            f"",
            f"Strongest Interactions:",
            *[f"- {line}" for line in top_conn_strings],
        ])

        actions = [
            AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=node_id),
            AssistantAction(action_type=AssistantActionType.VIEW_NETWORK, label="View Network", entity_id=node_id),
            AssistantAction(action_type=AssistantActionType.VIEW_TIMELINE, label="View Timeline", entity_id=node_id),
        ]
        if phones or "calls" in query.lower():
            actions.append(AssistantAction(action_type=AssistantActionType.ANALYZE_CALLS, label="Analyze Calls", entity_id=node_id))
        if accounts or "finances" in query.lower():
            actions.append(AssistantAction(action_type=AssistantActionType.ANALYZE_FINANCES, label="Analyze Finances", entity_id=node_id))

        sources = [
            AssistantSource(source_type="GRAPH", reference_id=node_id, title=f"Knowledge Graph Edge Adjacency for {node_name}"),
        ]

        rel_entities = [{"entity_id": n.id, "name": n.name, "entity_type": n.label.value if hasattr(n.label, "value") else str(n.label)} for n in neighbor_nodes]
        rel_cases = [c.name for c in cases]

        res = InvestigationAssistantResponse(
            answer="\n".join(ans_lines),
            selected_entity=ent,
            evidence=evidence_list,
            relevant_entities=rel_entities,
            relevant_cases=rel_cases,
            sources=sources,
            actions=actions,
            conversation_id=cid,
        )
        session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
        return res

    def _handle_call_telemetry(self, ent: Optional[Dict[str, Any]], query: str, cid: str, session: Dict[str, Any]) -> InvestigationAssistantResponse:
        if ent:
            node_id = ent["entity_id"]
            node_name = ent["name"]
            cdr_data = self.cdr_service.analyze_entity_cdr(node_id)
            stats = cdr_data.statistics

            if stats.total_calls == 0 and not cdr_data.bursts:
                ans = (
                    f"ANSWER\n\n"
                    f"No telecommunications (CDR) records were found for '{node_name}' ({node_id}) in the active dataset.\n\n"
                    f"Evidence:\n- 0 incoming or outgoing calls logged under this entity or its registered phone lines."
                )
                return InvestigationAssistantResponse(
                    answer=ans,
                    selected_entity=ent,
                    evidence=[f"0 CDR records available for {node_name}."],
                    uncertainty=f"No call records currently logged for '{node_name}'. Entity may use unregistered communication channels or CDR has not been ingested.",
                    actions=[
                        AssistantAction(action_type=AssistantActionType.ANALYZE_CALLS, label="Analyze Calls", entity_id=node_id),
                        AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=node_id),
                    ],
                    conversation_id=cid,
                )

            ev_list = [
                f"Total calls logged: {stats.total_calls} (incoming: {stats.incoming_calls}, outgoing: {stats.outgoing_calls}).",
                f"Unique contacts contacted: {stats.unique_contacts}.",
                f"Suspicious call bursts: {len(cdr_data.bursts)} identified window(s).",
            ]
            if cdr_data.bursts:
                b = cdr_data.bursts[0]
                ev_list.append(f"Primary burst: {b.call_count} calls within {b.window_hours:.1f}h window (spike ratio: {b.spike_ratio:.2f}x).")

            ans_lines = [
                f"ANSWER",
                f"",
                f"Communication telemetry for '{node_name}':",
                f"• Total Logged Calls: {stats.total_calls} ({stats.incoming_calls} incoming, {stats.outgoing_calls} outgoing)",
                f"• Total Duration: {stats.total_duration_seconds}s (mean call length: {stats.avg_duration_seconds:.1f}s)",
                f"• Monitored Phone Identifiers: {', '.join(cdr_data.phone_numbers) if cdr_data.phone_numbers else 'None'}",
                f"• Burst Activity: {len(cdr_data.bursts)} high-velocity burst window(s) detected.",
            ]
            if cdr_data.top_contacts:
                ans_lines.extend([
                    f"",
                    f"Top Communication Counterparties:",
                    *[f"- {c.contact_name or c.phone_number} ({c.call_count} calls, {c.total_duration_seconds}s total)" for c in cdr_data.top_contacts[:4]],
                ])

            actions = [
                AssistantAction(action_type=AssistantActionType.ANALYZE_CALLS, label="Analyze Calls", entity_id=node_id),
                AssistantAction(action_type=AssistantActionType.VIEW_TIMELINE, label="View Timeline", entity_id=node_id),
                AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=node_id),
            ]
            sources = [AssistantSource(source_type="CDR", reference_id=node_id, title=f"CDR Telecommunications Ledger for {node_name}")]
            calls_list = [{"phone": c.phone_number, "name": c.contact_name or c.name, "call_count": c.call_count} for c in cdr_data.top_contacts[:5]]

            res = InvestigationAssistantResponse(
                answer="\n".join(ans_lines),
                selected_entity=ent,
                evidence=ev_list,
                relevant_calls=calls_list,
                sources=sources,
                actions=actions,
                conversation_id=cid,
            )
            session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
            return res

        # Global call anomalies inquiry:
        burst_alerts = self.anomaly_detector.detect_call_bursts()
        if not burst_alerts:
            ans = "ANSWER\n\nNo unusual call bursts or communication spikes are currently detected across the network."
            return InvestigationAssistantResponse(answer=ans, evidence=["All monitored call frequencies are within statistical bounds."], conversation_id=cid)

        ans_lines = [
            f"ANSWER",
            f"",
            f"Observed {len(burst_alerts)} statistical communication anomaly alert(s) across monitored telecommunications channels:",
            *[f"• [{a.severity}] {a.title}: {a.description}" for a in burst_alerts],
        ]
        ev_list = [f"{a.title} ({a.evidence.get('multiplier', 'N/A')}x mean frequency)" for a in burst_alerts]
        actions = [AssistantAction(action_type=AssistantActionType.VIEW_NETWORK, label="View Network", entity_id="GLOBAL")]

        res = InvestigationAssistantResponse(
            answer="\n".join(ans_lines),
            evidence=ev_list,
            actions=actions,
            conversation_id=cid,
        )
        session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
        return res

    def _handle_financial_telemetry(self, ent: Optional[Dict[str, Any]], query: str, cid: str, session: Dict[str, Any]) -> InvestigationAssistantResponse:
        if ent:
            node_id = ent["entity_id"]
            node_name = ent["name"]
            fin_data = self.financial_service.analyze_entity_finances(node_id)
            stats = fin_data.statistics

            if stats.total_transactions == 0:
                ans = (
                    f"ANSWER\n\n"
                    f"No financial transactions or bank ledger records were found for '{node_name}' ({node_id}) in the active dataset.\n\n"
                    f"Evidence:\n- 0 financial transfers logged."
                )
                return InvestigationAssistantResponse(
                    answer=ans,
                    selected_entity=ent,
                    evidence=[f"0 financial transaction records for {node_name}."],
                    uncertainty=f"No financial transactions currently logged for '{node_name}'. Entity may operate cash-only or bank accounts are unindexed.",
                    actions=[AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=node_id)],
                    conversation_id=cid,
                )

            total_vol = stats.total_incoming_amount + stats.total_outgoing_amount
            ev_list = [
                f"Total volume: ₹{total_vol:,.2f} across {stats.total_transactions} transaction(s).",
                f"Net money flow: ₹{fin_data.flow.net_flow:,.2f} (inflow: ₹{fin_data.flow.incoming_total:,.2f}, outflow: ₹{fin_data.flow.outgoing_total:,.2f}).",
            ]
            for ind in fin_data.indicators:
                ind_name = getattr(ind, 'name', getattr(ind, 'title', 'Indicator'))
                ind_desc = getattr(ind, 'explanation', getattr(ind, 'description', ''))
                ev_list.append(f"Indicator [{ind.severity}]: {ind_name} — {ind_desc}")

            ans_lines = [
                f"ANSWER",
                f"",
                f"Financial activity summary for '{node_name}':",
                f"• Total Logged Volume: ₹{total_vol:,.2f} ({stats.total_transactions} transactions)",
                f"• Inflow: ₹{fin_data.flow.incoming_total:,.2f} | Outflow: ₹{fin_data.flow.outgoing_total:,.2f} (Net: ₹{fin_data.flow.net_flow:,.2f})",
                f"• Associated Bank Accounts: {', '.join(fin_data.accounts) if fin_data.accounts else 'None indexed'}",
                f"• Financial Anomaly Indicators: {len(fin_data.indicators)} observed indicator(s).",
            ]
            if fin_data.top_counterparties:
                ans_lines.extend([
                    f"",
                    f"Key Financial Counterparties:",
                    *[f"- {cp.counterparty_name} (₹{cp.total_amount:,.2f} across {cp.transaction_count} tx)" for cp in fin_data.top_counterparties[:4]],
                ])

            actions = [
                AssistantAction(action_type=AssistantActionType.ANALYZE_FINANCES, label="Analyze Finances", entity_id=node_id),
                AssistantAction(action_type=AssistantActionType.VIEW_TIMELINE, label="View Timeline", entity_id=node_id),
                AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=node_id),
            ]
            sources = [AssistantSource(source_type="FINANCIAL", reference_id=node_id, title=f"Financial Ledger for {node_name}")]
            fin_list = [{"counterparty": cp.counterparty_name, "amount": cp.total_amount, "count": cp.transaction_count} for cp in fin_data.top_counterparties[:5]]

            res = InvestigationAssistantResponse(
                answer="\n".join(ans_lines),
                selected_entity=ent,
                evidence=ev_list,
                relevant_financial=fin_list,
                sources=sources,
                actions=actions,
                conversation_id=cid,
            )
            session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
            return res

        # Global financial anomalies inquiry:
        fin_alerts = self.anomaly_detector.detect_financial_anomalies()
        if not fin_alerts:
            ans = "ANSWER\n\nNo unusual financial anomalies or high-z-score transactions are currently detected across the ledger."
            return InvestigationAssistantResponse(answer=ans, evidence=["All financial amounts within standard deviation bounds."], conversation_id=cid)

        ans_lines = [
            f"ANSWER",
            f"",
            f"Observed {len(fin_alerts)} statistical financial anomaly alert(s):",
            *[f"• [{a.severity}] {a.title}: {a.description}" for a in fin_alerts],
        ]
        ev_list = [f"{a.title} ({a.description})" for a in fin_alerts]
        actions = [AssistantAction(action_type=AssistantActionType.VIEW_NETWORK, label="View Network", entity_id="GLOBAL")]

        res = InvestigationAssistantResponse(
            answer="\n".join(ans_lines),
            evidence=ev_list,
            actions=actions,
            conversation_id=cid,
        )
        session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
        return res

    def _handle_case_linkages(self, ent: Optional[Dict[str, Any]], query: str, cid: str, session: Dict[str, Any]) -> InvestigationAssistantResponse:
        if not ent:
            return self._entity_missing_response("cases", cid, query)

        node_id = ent["entity_id"]
        node_name = ent["name"]
        neighbor_data = self.graph.get_neighbors(node_id, depth=1)
        neighbor_nodes = [n for n in neighbor_data.get("nodes", []) if n.id != node_id]
        case_nodes = [n for n in neighbor_nodes if (n.label.value if hasattr(n.label, "value") else str(n.label)) == NodeType.CASE.value]

        # Also check properties of current node for cases
        node = self.graph.get_node(node_id)
        prop_cases = node.properties.get("cases", []) if node else []
        if isinstance(prop_cases, str):
            prop_cases = [prop_cases]

        if not case_nodes and not prop_cases:
            ans = f"ANSWER\n\nNo registered criminal FIRs or formal case records are currently linked to '{node_name}' ({node_id})."
            return InvestigationAssistantResponse(
                answer=ans,
                selected_entity=ent,
                evidence=[f"0 linked CASE nodes in graph for {node_name}."],
                uncertainty="No FIR linkages exist in the indexed registry.",
                actions=[AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=node_id)],
                conversation_id=cid,
            )

        case_details = []
        for cn in case_nodes:
            sections = cn.properties.get("sections") or cn.properties.get("statutes") or "Under investigation"
            case_details.append(f"{cn.name} (Sections: {sections})")
        for pc in prop_cases:
            if pc not in [cn.name for cn in case_nodes]:
                case_details.append(f"{pc}")

        ev_list = [f"Linked to {len(case_details)} registered criminal case(s): {', '.join(case_details)}"]

        ans_lines = [
            f"ANSWER",
            f"",
            f"'{node_name}' is connected to {len(case_details)} case record(s):",
            *[f"• {c}" for c in case_details],
            f"",
            f"Notice: Case linkages represent formal investigative registry records and do not constitute legal findings of guilt.",
        ]

        actions = [
            AssistantAction(action_type=AssistantActionType.VIEW_CASE, label="View Case", entity_id=case_nodes[0].id if case_nodes else node_id),
            AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=node_id),
            AssistantAction(action_type=AssistantActionType.VIEW_TIMELINE, label="View Timeline", entity_id=node_id),
        ]
        sources = [AssistantSource(source_type="CASE", reference_id=node_id, title=f"Police FIR & Case Registry for {node_name}")]

        res = InvestigationAssistantResponse(
            answer="\n".join(ans_lines),
            selected_entity=ent,
            evidence=ev_list,
            relevant_cases=case_details,
            sources=sources,
            actions=actions,
            conversation_id=cid,
        )
        session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
        return res

    def _handle_timeline_query(self, ent: Optional[Dict[str, Any]], query: str, cid: str, session: Dict[str, Any]) -> InvestigationAssistantResponse:
        target_id = ent["entity_id"] if ent else None

        # Check for specific date in query
        date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", query)
        date_str = date_match.group(1) if date_match else None

        # Build timeline
        if target_id:
            tl = self.timeline.get_entity_timeline(entity_id=target_id, start=date_str, limit=10)
        else:
            tl = self.timeline.get_entity_timeline(entity_id="PERSON_TARIQ_AHMAD", start=date_str, limit=10)

        if not tl.events:
            ans = f"ANSWER\n\nNo chronological events were found around the specified parameters."
            return InvestigationAssistantResponse(answer=ans, evidence=["Timeline query returned 0 chronological events."], conversation_id=cid)

        ans_lines = [
            f"ANSWER",
            f"",
            f"Chronological event summary for '{tl.entity_name}':",
            *[f"• [{str(e.timestamp)[:16]}] [{e.event_type.value}] {e.title} — {e.description}" for e in tl.events[:6]],
        ]
        ev_list = [f"{str(e.timestamp)[:10]} | {e.event_type.value}: {e.title}" for e in tl.events[:6]]

        actions = [
            AssistantAction(action_type=AssistantActionType.VIEW_TIMELINE, label="View Timeline", entity_id=tl.entity_id),
            AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=tl.entity_id),
        ]
        sources = [AssistantSource(source_type="TIMELINE", reference_id=tl.entity_id, title=f"Chronological Event Stream for {tl.entity_name}")]

        res = InvestigationAssistantResponse(
            answer="\n".join(ans_lines),
            selected_entity=ent,
            evidence=ev_list,
            sources=sources,
            actions=actions,
            conversation_id=cid,
        )
        session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
        return res

    def _handle_risk_explanation(self, ent: Optional[Dict[str, Any]], query: str, cid: str, session: Dict[str, Any]) -> InvestigationAssistantResponse:
        if not ent:
            return self._entity_missing_response("risk explanation", cid, query)

        node_id = ent["entity_id"]
        node_name = ent["name"]
        risk_result = self.anomaly_detector.calculate_investigative_risk_score(node_id)

        factors = risk_result.factors
        score = risk_result.overall_score
        level = risk_result.severity_level

        ans_lines = [
            f"ANSWER",
            f"",
            f"Investigative Risk Assessment for '{node_name}':",
            f"Risk Score: {score:.1f} / 100",
            f"Risk Level: {level}",
            f"",
            f"Evidence Factors Breakdown:",
        ]
        ev_list = []
        for idx, f in enumerate(factors, 1):
            pts = f.score_contribution
            ans_lines.append(f"{idx}. [+ {pts:.1f} pts] {f.title} ({f.category.value if hasattr(f.category, 'value') else str(f.category)})")
            ans_lines.append(f"   Explanation: {f.explanation}")
            ans_lines.append(f"   Evidence: {f.evidence}")
            ev_list.append(f"[+{pts:.1f}] {f.title}: {f.evidence}")

        ans_lines.extend([
            f"",
            f"Conclusion:",
            f"\"{risk_result.conclusion}\"",
            f"",
            f"Notice: Risk scores are investigative decision-support indicators only and do not establish criminal culpability.",
        ])

        actions = [
            AssistantAction(action_type=AssistantActionType.VIEW_RISK, label="View Risk Evidence", entity_id=node_id),
            AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=node_id),
            AssistantAction(action_type=AssistantActionType.VIEW_TIMELINE, label="View Timeline", entity_id=node_id),
        ]
        sources = [
            AssistantSource(source_type="RISK", reference_id=node_id, title=f"Explainable Risk Score Breakdown for {node_name}"),
            AssistantSource(source_type="GRAPH", reference_id=node_id, title="Graph Centrality Metrics"),
        ]

        res = InvestigationAssistantResponse(
            answer="\n".join(ans_lines),
            selected_entity=ent,
            evidence=ev_list,
            risk_indicators=[f"{f.title} (+{f.score_contribution:.1f})" for f in factors],
            sources=sources,
            actions=actions,
            conversation_id=cid,
        )
        session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
        return res

    def _handle_shortest_path(self, query: str, cid: str, session: Dict[str, Any]) -> InvestigationAssistantResponse:
        # Extract endpoints A and B
        match = re.search(r"between\s+([^\s,]+(?:\s+[^\s,]+)?)\s+and\s+([^\s,?.!]+(?:\s+[^\s,?.!]+)?)", query, re.IGNORECASE)
        if not match:
            # Try alternate phrasing: "from X to Y" or "how is X connected to Y"
            match = re.search(r"(?:from|how is)\s+([^\s,]+(?:\s+[^\s,]+)?)\s+(?:to|connected to)\s+([^\s,?.!]+(?:\s+[^\s,?.!]+)?)", query, re.IGNORECASE)

        if not match:
            ans = "ANSWER\n\nPlease specify both start and destination entities. For example: 'Find the shortest connection between Tariq Ahmad and DL01AB9988'."
            return InvestigationAssistantResponse(answer=ans, uncertainty="Missing source or target entities for path finding.", conversation_id=cid)

        raw_a = match.group(1).strip()
        raw_b = match.group(2).strip()

        # Resolve A and B
        res_a = self.search.search(raw_a, limit=1)
        res_b = self.search.search(raw_b, limit=1)

        id_a = res_a.results[0].entity_id if res_a.results else raw_a
        id_b = res_b.results[0].entity_id if res_b.results else raw_b

        path_result = self.graph.find_shortest_path(id_a, id_b)

        if not path_result.get("path_found"):
            ans = f"ANSWER\n\nNo connection path found between '{raw_a}' and '{raw_b}' in the current network.\n\nDetails: {path_result.get('message', 'Nodes are in disconnected network components.')}"
            return InvestigationAssistantResponse(
                answer=ans,
                evidence=[f"Network traversal between {id_a} and {id_b} yielded 0 paths."],
                uncertainty="Entities belong to separate components or one entity is unrecorded.",
                conversation_id=cid,
            )

        steps = path_result.get("steps", [])
        length = path_result.get("length", 0)

        step_strings = [f"{s['from_name']} --[{s['relationship']}]--> {s['to_name']}" for s in steps]

        ans_lines = [
            f"ANSWER",
            f"",
            f"Shortest path between '{raw_a}' and '{raw_b}':",
            f"• Path Length: {length} hop(s)",
            f"• Traversal Sequence:",
            *[f"  {idx}. {step}" for idx, step in enumerate(step_strings, 1)],
        ]

        actions = [
            AssistantAction(action_type=AssistantActionType.VIEW_NETWORK, label="View Network", entity_id=id_a),
            AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label=f"Profile: {raw_a}", entity_id=id_a),
            AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label=f"Profile: {raw_b}", entity_id=id_b),
        ]
        sources = [AssistantSource(source_type="GRAPH", reference_id=f"{id_a}_{id_b}", title="Graph Traversal Path")]

        res = InvestigationAssistantResponse(
            answer="\n".join(ans_lines),
            evidence=step_strings,
            sources=sources,
            actions=actions,
            conversation_id=cid,
        )
        session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
        return res

    def _handle_investigation_summary(self, cid: str, session: Dict[str, Any]) -> InvestigationAssistantResponse:
        nodes = self.graph.get_all_nodes()
        edges = self.graph.get_all_edges()

        # Count by type
        by_type = {}
        for n in nodes:
            lbl = n.label.value if hasattr(n.label, "value") else str(n.label)
            by_type[lbl] = by_type.get(lbl, 0) + 1

        top_brokers = self.analytics.get_ranked_influencers(top_k=4)
        call_bursts = self.anomaly_detector.detect_call_bursts()
        fin_alerts = self.anomaly_detector.detect_financial_anomalies()
        colocations = self.anomaly_detector.detect_colocation_clusters()

        broker_lines = [f"{b['name']} ({b['node_type']} · Betweenness: {b['betweenness_centrality']:.2f})" for b in top_brokers]

        ans_lines = [
            f"ANSWER",
            f"",
            f"NODE SENTINEL Criminal Network Investigation Overview:",
            f"• Knowledge Graph Scale: {len(nodes)} total entities across {len(edges)} verified relationships.",
            f"• Entity Breakdown: {', '.join(f'{k}s: {v}' for k, v in by_type.items())}",
            f"",
            f"Key Network Brokers / Hubs:",
            *[f"- {line}" for line in broker_lines],
            f"",
            f"Active Statistical Telemetry Alerts:",
            f"• Suspicious Call Bursts: {len(call_bursts)}",
            f"• High-Value / Outlier Financial Anomalies: {len(fin_alerts)}",
            f"• Physical Co-location Clusters: {len(colocations)}",
            f"",
            f"Notice: Summary reflects current database ingest. Investigator verification required for all leads.",
        ]

        ev_list = [
            f"{len(nodes)} graph nodes, {len(edges)} graph edges.",
            f"{len(call_bursts)} call bursts, {len(fin_alerts)} financial anomalies, {len(colocations)} co-location clusters.",
        ]
        actions = [
            AssistantAction(action_type=AssistantActionType.VIEW_NETWORK, label="View Network", entity_id="GLOBAL"),
        ]

        res = InvestigationAssistantResponse(
            answer="\n".join(ans_lines),
            evidence=ev_list,
            actions=actions,
            conversation_id=cid,
        )
        session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
        return res

    def _handle_entity_profile(self, ent: Dict[str, Any], query: str, cid: str, session: Dict[str, Any]) -> InvestigationAssistantResponse:
        node_id = ent["entity_id"]
        node_name = ent["name"]
        node = self.graph.get_node(node_id)
        ent_type = ent.get("entity_type", "Entity")

        neighbor_data = self.graph.get_neighbors(node_id, depth=1)
        neighbor_nodes = [n for n in neighbor_data.get("nodes", []) if n.id != node_id]
        risk = self.anomaly_detector.calculate_investigative_risk_score(node_id)

        ans_lines = [
            f"ANSWER",
            f"",
            f"Entity Profile: '{node_name}' [{ent_type}]",
            f"• Entity ID: {node_id}",
            f"• Direct Graph Connections: {len(neighbor_nodes)}",
            f"• Investigative Risk Score: {risk.overall_score:.1f}/100 ({risk.severity_level})",
        ]
        if node and node.properties:
            clean_props = {k: v for k, v in node.properties.items() if k not in ("name", "id", "label") and v}
            if clean_props:
                ans_lines.append(f"• Indexed Properties: {', '.join(f'{k}: {v}' for k, v in clean_props.items())}")

        ans_lines.extend([
            f"",
            f"Notice: NODE SENTINEL provides objective investigative decision support based on available telemetry and does not make legal determinations of guilt or criminal convictions.",
        ])

        actions = [
            AssistantAction(action_type=AssistantActionType.VIEW_PROFILE, label="View Profile", entity_id=node_id),
            AssistantAction(action_type=AssistantActionType.VIEW_NETWORK, label="View Network", entity_id=node_id),
            AssistantAction(action_type=AssistantActionType.VIEW_TIMELINE, label="View Timeline", entity_id=node_id),
            AssistantAction(action_type=AssistantActionType.VIEW_RISK, label="View Risk Evidence", entity_id=node_id),
        ]

        res = InvestigationAssistantResponse(
            answer="\n".join(ans_lines),
            selected_entity=ent,
            evidence=[f"Direct connections: {len(neighbor_nodes)}, Risk score: {risk.overall_score:.1f}"],
            actions=actions,
            conversation_id=cid,
        )
        session["history"].append(AssistantMessage(role=AssistantRole.ASSISTANT, content=res.answer).model_dump())
        return res

    def _entity_missing_response(self, intent_label: str, cid: str, query: str = "") -> InvestigationAssistantResponse:
        extracted = self._extract_entity_terms(query)
        stop_words = {
            "his", "her", "their", "this person", "this entity", "him", "he", "she", "them", "a", "the",
            "unusual", "call", "calls", "financial", "finances", "activity", "connections", "timeline",
            "cases", "case", "investigation", "pattern", "flow", "key connections", "communication pattern", "financial flow"
        }
        filtered = [t for t in extracted if t.lower() not in stop_words]
        if filtered:
            ans = f"No supporting record was found in the current investigation dataset for '{filtered[0]}'."
            return InvestigationAssistantResponse(
                answer=ans,
                evidence=[],
                uncertainty=f"No corresponding record exists in Knowledge Graph, CDR telemetry, or ledger for '{filtered[0]}'.",
                conversation_id=cid,
            )
        ans = f"ANSWER\n\nPlease select or specify an entity to analyze {intent_label} (e.g. 'Show Tariq Ahmad's {intent_label}')."
        return InvestigationAssistantResponse(
            answer=ans,
            evidence=[],
            uncertainty=f"No entity currently selected for {intent_label} analysis.",
            conversation_id=cid,
        )


# Singleton instance
_assistant_engine_instance: Optional[AssistantEngine] = None


def get_assistant_engine() -> AssistantEngine:
    global _assistant_engine_instance
    if _assistant_engine_instance is None:
        _assistant_engine_instance = AssistantEngine()
    return _assistant_engine_instance
