# -*- coding: utf-8 -*-
"""
Universal Search Engine for NODE SENTINEL.

Provides multi-modal entity resolution and retrieval across the Knowledge Graph
and Face Recognition storage.
Searches across:
1. Person ID
2. Person Name (exact & partial matching)
3. Phone Number (with normalization for Indian & international formats)
4. Vehicle Number (with alphanumeric plate normalization)
5. Case ID / Case Code
6. Bank / Account Identifier

Guarantees:
- Exact matches prioritized first
- Partial name matching supported
- Ambiguous matches flagged
- Zero results handled cleanly
- Never invents an entity (only existing graph nodes are returned)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.face_storage import FaceStorage, get_face_storage
from app.core.graph_engine import BaseGraphEngine, get_graph_engine
from app.models.graph_models import NodeType
from app.models.schemas import (
    UniversalSearchEntityResult,
    UniversalSearchResponse,
)

logger = logging.getLogger(__name__)


class UniversalSearchEngine:
    """Universal Search Engine indexing the Knowledge Graph and Biometric Face Registry."""

    def __init__(
        self,
        graph_engine: Optional[BaseGraphEngine] = None,
        face_storage: Optional[FaceStorage] = None,
    ) -> None:
        self.graph_engine = graph_engine or get_graph_engine()
        self.face_storage = face_storage or get_face_storage()

    # -----------------------------------------------------------------------
    # Normalization Utilities
    # -----------------------------------------------------------------------

    @staticmethod
    def normalize_text(text: str) -> str:
        """Strip leading/trailing whitespace and reduce internal multiple spaces."""
        return " ".join(text.strip().split())

    @staticmethod
    def normalize_phone(query: str) -> Set[str]:
        """
        Normalize phone query into candidate formats (+91XXXXXXXXXX, XXXXXXXXXX).
        Handles queries with spaces, dashes, parentheses, dots.
        """
        cleaned = re.sub(r"[\s\-\(\)\.\+]", "", query)
        variants = {query.strip(), cleaned}

        # If 10 digits starting with [6-9]
        if re.fullmatch(r"[6-9]\d{9}", cleaned):
            variants.add(f"+91{cleaned}")
            variants.add(f"91{cleaned}")
            variants.add(cleaned)
        # If 12 digits starting with 91
        elif len(cleaned) == 12 and cleaned.startswith("91"):
            raw_10 = cleaned[2:]
            variants.add(f"+91{raw_10}")
            variants.add(cleaned)
            variants.add(raw_10)

        # Also add with + if not already
        if query.startswith("+"):
            variants.add("+" + cleaned)

        return {v for v in variants if v}

    @staticmethod
    def normalize_vehicle(query: str) -> Set[str]:
        """
        Normalize Indian vehicle registration number (e.g. 'UP 14 AB 1234' -> 'UP14AB1234').
        """
        cleaned = re.sub(r"[\s\-\.]", "", query).upper()
        return {query.strip().upper(), cleaned}

    @staticmethod
    def normalize_case(query: str) -> Set[str]:
        """
        Normalize case codes (e.g. 'FIR-2024-311', 'CASE_FIR_2024_311', 'crim 2024 089').
        """
        upper = query.strip().upper()
        variants = {upper}
        # Replace spaces/hyphens with underscore
        underscored = re.sub(r"[\s\-]", "_", upper)
        variants.add(underscored)
        # Replace spaces/underscores with hyphen
        hyphenated = re.sub(r"[\s_]", "-", upper)
        variants.add(hyphenated)
        # With CASE_ prefix or without
        if not upper.startswith("CASE_"):
            variants.add(f"CASE_{underscored}")
        else:
            variants.add(upper.replace("CASE_", ""))
        return variants

    @staticmethod
    def normalize_account(query: str) -> Set[str]:
        """
        Normalize bank account query (e.g. 'ACC990188231', '990188231', 'A/C 990188231').
        """
        upper = query.strip().upper()
        cleaned = re.sub(r"[\s\-\.\/]", "", upper)
        variants = {upper, cleaned}
        # Strip ACC prefix
        without_acc = re.sub(r"^(?:ACC|A\/C|AC)", "", cleaned)
        if without_acc:
            variants.add(without_acc)
            variants.add(f"ACC{without_acc}")
            variants.add(f"ACC_{without_acc}")
        return variants

    # -----------------------------------------------------------------------
    # Graph Enrichment Helpers
    # -----------------------------------------------------------------------

    def _get_entity_cases_and_connections(
        self, node_id: str
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        """
        Traverse 1-hop neighborhood of a node to collect:
        1. Directly associated case codes/IDs
        2. Connected target entities with relationship types
        """
        cases: Set[str] = set()
        connections: List[Dict[str, Any]] = []

        node = self.graph_engine.get_node(node_id)
        if not node:
            return [], []

        # Check node's own properties for case metadata
        for case_key in ("case_id", "case_code", "case", "fir_number"):
            c_val = node.properties.get(case_key)
            if c_val:
                cases.add(str(c_val))

        # Check Face Registry for case linkage
        face_rec = self.face_storage.get_identity(node_id)
        if face_rec and face_rec.case_id:
            cases.add(face_rec.case_id)

        # Traverse neighbors
        neighborhood = self.graph_engine.get_neighbors(node_id, depth=1)
        nodes_map = {n.id: n for n in neighborhood.get("nodes", [])}
        edges = neighborhood.get("edges", [])

        # Build edge lookup: neighbor_id -> relationship string
        edge_rel_map: Dict[str, str] = {}
        for e in edges:
            rel_str = e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)
            if e.source == node_id and e.target != node_id:
                edge_rel_map[e.target] = rel_str
            elif e.target == node_id and e.source != node_id:
                edge_rel_map[e.source] = rel_str

        for nbr_id, nbr in nodes_map.items():
            if nbr_id == node_id:
                continue

            nbr_type = nbr.label.value if hasattr(nbr.label, "value") else str(nbr.label)

            # If neighbor is a CASE entity, track in cases list
            if nbr_type.upper() in ("CASE", NodeType.CASE.value.upper()):
                cases.add(nbr.name or nbr.id)

            connections.append({
                "entity_id": nbr.id,
                "name": nbr.name,
                "entity_type": nbr_type,
                "relationship": edge_rel_map.get(nbr_id, "CONNECTED_TO"),
                "properties": nbr.properties,
            })

        return sorted(list(cases)), connections

    # -----------------------------------------------------------------------
    # Main Search Execution
    # -----------------------------------------------------------------------

    def search(self, query: str, limit: int = 25) -> UniversalSearchResponse:
        """
        Execute universal search across Person ID, Name, Phone, Vehicle, Case ID, Account.

        Returns:
            UniversalSearchResponse: Structured, ranked results grouped by entity type.
        """
        raw_query = query or ""
        clean_q = self.normalize_text(raw_query)

        if not clean_q:
            return UniversalSearchResponse(
                query=raw_query,
                total_results=0,
                is_exact_match=False,
                is_ambiguous=False,
                message="Search query cannot be empty.",
                results=[],
                grouped_results={},
            )

        q_lower = clean_q.lower()
        q_upper = clean_q.upper()

        phone_variants = self.normalize_phone(clean_q)
        vehicle_variants = self.normalize_vehicle(clean_q)
        case_variants = self.normalize_case(clean_q)
        account_variants = self.normalize_account(clean_q)

        all_nodes = self.graph_engine.get_all_nodes()

        scored_matches: List[Tuple[int, str, Any]] = []

        for node in all_nodes:
            n_id = node.id
            n_id_lower = n_id.lower()
            n_id_upper = n_id.upper()
            n_name = node.name or ""
            n_name_lower = n_name.lower()
            n_type = node.label.value if hasattr(node.label, "value") else str(node.label)
            n_type_upper = n_type.upper()
            props = node.properties or {}

            score = 0
            match_type = "none"

            # ---------------------------------------------------------------
            # 1. EXACT MATCHES (Score 100 - 110)
            # ---------------------------------------------------------------
            # 1.1 Exact ID match (case-insensitive)
            if q_lower == n_id_lower:
                score = 110
                match_type = "exact_id"

            # 1.2 Exact Name match (case-insensitive)
            elif q_lower == n_name_lower:
                score = 105
                match_type = "exact_name"

            # 1.3 Phone Number exact match
            elif n_type_upper in ("PHONE", "PERSON"):
                node_phone = str(props.get("phone_number", n_name)).strip()
                node_phone_variants = self.normalize_phone(node_phone)
                if any(v in node_phone_variants for v in phone_variants):
                    score = 100
                    match_type = "exact_phone"

            # 1.4 Vehicle Registration exact match
            if score < 100 and n_type_upper in ("VEHICLE", "PERSON"):
                node_plate = str(props.get("registration", props.get("plate", n_name))).strip()
                node_veh_variants = self.normalize_vehicle(node_plate)
                if any(v in node_veh_variants for v in vehicle_variants):
                    score = 100
                    match_type = "exact_vehicle"

            # 1.5 Case ID / Code exact match
            if score < 100 and n_type_upper in ("CASE", "PERSON"):
                node_case = str(props.get("case_code", props.get("case_id", n_name))).strip()
                node_case_variants = self.normalize_case(node_case) | self.normalize_case(n_id)
                if any(v in node_case_variants for v in case_variants):
                    score = 100
                    match_type = "exact_case"

            # 1.6 Bank Account exact match
            if score < 100 and n_type_upper in ("BANKACCOUNT", "BANK_ACCOUNT", "PERSON"):
                node_acc = str(props.get("account_number", n_name)).strip()
                node_acc_variants = self.normalize_account(node_acc) | self.normalize_account(n_id)
                if any(v in node_acc_variants for v in account_variants):
                    score = 100
                    match_type = "exact_account"

            # ---------------------------------------------------------------
            # 2. PREFIX & UNPREFIXED ID MATCHES (Score 80 - 95)
            # ---------------------------------------------------------------
            if score == 0:
                # Strip domain prefixes like PERSON_, CASE_, VEH_, PHONE_, ACC_
                unprefixed_id = re.sub(r"^(?:PERSON|CASE|VEH|PHONE|ACC|LOC|ORG)_", "", n_id_upper)
                if q_upper == unprefixed_id:
                    score = 95
                    match_type = "unprefixed_id_match"
                elif n_name_lower.startswith(q_lower):
                    score = 90
                    match_type = "name_prefix"
                elif n_id_lower.startswith(q_lower):
                    score = 85
                    match_type = "id_prefix"

            # ---------------------------------------------------------------
            # 3. PARTIAL & SUBSTRING MATCHES (Score 40 - 75)
            # ---------------------------------------------------------------
            if score == 0:
                # Token matching: all words in query present in name
                q_words = [w for w in q_lower.split() if len(w) > 1]
                if q_words and all(w in n_name_lower for w in q_words):
                    score = 75
                    match_type = "partial_name_tokens"
                # Substring in name
                elif len(q_lower) >= 3 and q_lower in n_name_lower:
                    score = 65
                    match_type = "partial_name_substring"
                # Substring in ID
                elif len(q_lower) >= 3 and q_lower in n_id_lower:
                    score = 55
                    match_type = "partial_id_substring"
                # Property alias / role substring
                else:
                    alias = str(props.get("alias", "")).lower()
                    role = str(props.get("role", "")).lower()
                    if (len(q_lower) >= 3) and (q_lower in alias or q_lower in role):
                        score = 45
                        match_type = "property_match"

            if score > 0:
                scored_matches.append((score, match_type, node))

        # Sort matches by score descending, then alphabetically by name
        scored_matches.sort(key=lambda item: (-item[0], item[2].name.lower()))

        # Slice limit
        selected = scored_matches[:limit]

        # Build response entities
        entity_results: List[UniversalSearchEntityResult] = []
        grouped_results: Dict[str, List[UniversalSearchEntityResult]] = {}

        has_exact = any(item[0] >= 100 for item in selected)
        is_ambiguous = len(selected) > 1 and (
            selected[0][0] == selected[1][0] or (len(selected) > 2 and selected[0][0] < 100)
        )

        for score, m_type, node in selected:
            n_type = node.label.value if hasattr(node.label, "value") else str(node.label)
            # Normalize group key (e.g. BankAccount -> BANK_ACCOUNT, Person -> PERSON)
            norm_type = re.sub(r"(?<!^)(?=[A-Z])", "_", n_type).upper()

            cases, connections = self._get_entity_cases_and_connections(node.id)

            # Check Face Registration
            face_rec = self.face_storage.get_identity(node.id)
            face_registered = face_rec is not None

            # Risk details
            risk_tag = node.properties.get("risk_tag", node.properties.get("risk_level", "LOW"))
            risk_score = None
            if risk_tag:
                risk_tag = str(risk_tag).upper()

            res_item = UniversalSearchEntityResult(
                entity_id=node.id,
                entity_type=norm_type,
                name=node.name,
                cases=cases,
                connections=connections,
                properties=node.properties,
                risk_level=risk_tag,
                risk_score=risk_score,
                face_registered=face_registered,
                match_type=m_type,
                match_score=score,
            )

            entity_results.append(res_item)
            grouped_results.setdefault(norm_type, []).append(res_item)

        # Message crafting
        if not entity_results:
            msg = f"No entities found matching '{raw_query}'. The engine never invents entities."
        elif is_ambiguous:
            msg = f"Found {len(entity_results)} matching entities across {len(grouped_results)} category/categories."
        elif has_exact:
            msg = f"Found exact match: '{entity_results[0].name}' ({entity_results[0].entity_type})."
        else:
            msg = f"Found {len(entity_results)} potential matching candidate(s)."

        return UniversalSearchResponse(
            query=raw_query,
            total_results=len(entity_results),
            is_exact_match=has_exact,
            is_ambiguous=is_ambiguous,
            message=msg,
            results=entity_results,
            grouped_results=grouped_results,
        )


# Singleton instance
_universal_search_instance: Optional[UniversalSearchEngine] = None


def get_universal_search() -> UniversalSearchEngine:
    """Retrieve or initialize UniversalSearchEngine singleton."""
    global _universal_search_instance
    if _universal_search_instance is None:
        _universal_search_instance = UniversalSearchEngine()
    return _universal_search_instance
