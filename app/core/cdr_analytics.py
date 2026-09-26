# -*- coding: utf-8 -*-
"""
CDR Analytics & Storage Service for NODE SENTINEL.
Aggregates call statistics, calculates contact-level interaction metrics,
detects communication bursts, and correlates CDR patterns with the live Knowledge Graph.
Strictly adheres to neutral investigator decision-support terminology.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.cdr_parser import normalize_phone_number
from app.core.graph_analytics import GraphAnalytics
from app.core.graph_engine import BaseGraphEngine, get_graph_engine
from app.models.cdr_models import (
    CallStatistics,
    CallType,
    CDRAnalysisResult,
    CDRRecord,
    CDRTimelineItem,
    CommunicationBurst,
    CommunicationIndicator,
    ContactSummary,
)
from app.models.graph_models import Edge, EdgeType, Node, NodeType

logger = logging.getLogger(__name__)


def _format_seconds(sec: int) -> str:
    """Format seconds into readable 'Xh Ym Zs' or 'Xm Ys' string."""
    if sec <= 0:
        return "0s"
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    seconds = sec % 60
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class CDRStorage:
    """In-memory indexed storage for CDR records."""

    def __init__(self) -> None:
        self._records: List[CDRRecord] = []
        self._by_phone: Dict[str, List[CDRRecord]] = defaultdict(list)
        self._by_call_id: Dict[str, CDRRecord] = {}

    def add_records(self, records: List[CDRRecord]) -> int:
        added = 0
        for r in records:
            if r.call_id and r.call_id in self._by_call_id:
                # Update or skip duplicate
                continue
            self._records.append(r)
            if r.call_id:
                self._by_call_id[r.call_id] = r
            self._by_phone[r.caller].append(r)
            self._by_phone[r.receiver].append(r)
            added += 1
        return added

    def get_records_for_phone(self, phone: str) -> List[CDRRecord]:
        return self._by_phone.get(phone, [])

    def get_all_records(self) -> List[CDRRecord]:
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()
        self._by_phone.clear()
        self._by_call_id.clear()


_storage_instance: Optional[CDRStorage] = None


def get_cdr_storage() -> CDRStorage:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = CDRStorage()
    return _storage_instance


class CDRService:
    """Investigator decision-support service for CDR analysis and graph correlation."""

    def __init__(
        self,
        graph_engine: Optional[BaseGraphEngine] = None,
        storage: Optional[CDRStorage] = None,
    ) -> None:
        self.graph = graph_engine or get_graph_engine()
        self.storage = storage or get_cdr_storage()

    # -------------------------------------------------------------------------
    # Graph Wiring & Entity Resolution
    # -------------------------------------------------------------------------

    def find_or_create_phone_node(self, normalized_phone: str) -> Tuple[Node, bool]:
        """
        Find an existing Phone node in the graph matching this phone number,
        or create a new Phone node without duplication.
        Returns (Node, created_boolean).
        """
        digits = "".join(c for c in normalized_phone if c.isdigit())
        canonical_id = f"PHONE_{digits[-10:] if len(digits) >= 10 else digits}"

        # 1. Direct ID check
        existing = self.graph.get_node(canonical_id)
        if existing:
            return existing, False

        # 2. Check all nodes in graph for phone number property or label match
        for node in self.graph.get_all_nodes():
            lbl = node.label.value if hasattr(node.label, "value") else str(node.label)
            if lbl == NodeType.PHONE.value or node.id.startswith("PHONE_"):
                phone_prop = node.properties.get("phone_number") or node.name or ""
                p_digits = "".join(c for c in str(phone_prop) if c.isdigit())
                if p_digits and (p_digits == digits or p_digits.endswith(digits[-10:])):
                    return node, False

        # 3. Create new Phone node
        new_node = Node(
            id=canonical_id,
            label=NodeType.PHONE,
            name=normalized_phone,
            properties={"phone_number": normalized_phone, "source": "cdr_ingest"},
        )
        self.graph.add_node(new_node)
        return new_node, True

    def ingest_records_into_graph(self, records: List[CDRRecord]) -> Tuple[int, int, int]:
        """
        Integrate parsed CDR records into the Knowledge Graph and CDR storage.
        - Preserves existing person relationships.
        - Wires Caller Phone -> Receiver Phone with CALLS edges.
        - Attaches call metadata to edges.
        - Prevents duplicate nodes.
        Returns (records_added, entities_created, relationships_created).
        """
        entities_created = 0
        relationships_created = 0

        # Store records in index
        records_added = self.storage.add_records(records)

        for rec in records:
            # Resolve or create caller phone node
            caller_node, created_c = self.find_or_create_phone_node(rec.caller)
            if created_c:
                entities_created += 1

            # Resolve or create receiver phone node
            receiver_node, created_r = self.find_or_create_phone_node(rec.receiver)
            if created_r:
                entities_created += 1

            # Build CALLS edge
            edge_id = f"CALL_{rec.call_id}" if rec.call_id else None
            edge_props = {
                "timestamp": rec.timestamp.isoformat(),
                "duration": rec.duration_seconds,
                "duration_seconds": rec.duration_seconds,
                "call_type": rec.call_type.value,
                "frequency": 1,
            }
            if rec.cell_tower:
                edge_props["cell_tower"] = rec.cell_tower
                edge_props["tower"] = rec.cell_tower
            if rec.location:
                edge_props["location"] = rec.location
            if rec.case_id:
                edge_props["case_id"] = rec.case_id
            if rec.source_document:
                edge_props["source"] = rec.source_document

            call_edge = Edge(
                source=caller_node.id,
                target=receiver_node.id,
                relationship=EdgeType.CALLS,
                properties=edge_props,
                id=edge_id,
            )
            self.graph.add_edge(call_edge)
            relationships_created += 1

        return records_added, entities_created, relationships_created

    def resolve_entity_phones(self, entity_id: str) -> Tuple[Optional[Node], List[str]]:
        """
        Resolve an entity ID (which could be a Person, Phone, or raw phone string)
        to the underlying Node and associated normalized phone numbers.
        """
        # Try direct graph lookup
        node = self.graph.get_node(entity_id)

        # Check if entity_id itself is a phone number string
        try:
            norm_direct = normalize_phone_number(entity_id)
        except Exception:
            norm_direct = None

        phones: Set[str] = set()

        if node:
            lbl = node.label.value if hasattr(node.label, "value") else str(node.label)

            if lbl == NodeType.PHONE.value or node.id.startswith("PHONE_"):
                # Node is already a phone
                raw_p = node.properties.get("phone_number") or node.name or node.id
                try:
                    phones.add(normalize_phone_number(raw_p))
                except Exception:
                    phones.add(str(raw_p))
                return node, list(phones)

            elif lbl == NodeType.PERSON.value or node.id.startswith("PERSON_"):
                # Check for phones owned or linked to this person
                for edge in self.graph.get_all_edges():
                    rel = edge.relationship.value if hasattr(edge.relationship, "value") else str(edge.relationship)
                    if rel == EdgeType.OWNS.value and edge.source == node.id:
                        target_node = self.graph.get_node(edge.target)
                        if target_node:
                            t_lbl = target_node.label.value if hasattr(target_node.label, "value") else str(target_node.label)
                            if t_lbl == NodeType.PHONE.value or target_node.id.startswith("PHONE_"):
                                raw_p = target_node.properties.get("phone_number") or target_node.name
                                if raw_p:
                                    try:
                                        phones.add(normalize_phone_number(raw_p))
                                    except Exception:
                                        phones.add(str(raw_p))

                # Check properties on person
                for key in ("phone", "phone_number", "mobile", "contact"):
                    if key in node.properties:
                        try:
                            phones.add(normalize_phone_number(node.properties[key]))
                        except Exception:
                            phones.add(str(node.properties[key]))

                return node, list(phones)

            else:
                # Other node type with phone property
                if "phone_number" in node.properties:
                    try:
                        phones.add(normalize_phone_number(node.properties["phone_number"]))
                    except Exception:
                        pass
                return node, list(phones)

        # If no node found directly by ID, search by phone digits
        if norm_direct:
            for n in self.graph.get_all_nodes():
                lbl = n.label.value if hasattr(n.label, "value") else str(n.label)
                if lbl == NodeType.PHONE.value or n.id.startswith("PHONE_"):
                    raw_p = n.properties.get("phone_number") or n.name
                    if raw_p:
                        try:
                            if normalize_phone_number(raw_p) == norm_direct:
                                return n, [norm_direct]
                        except Exception:
                            pass
            return None, [norm_direct]

        return None, []

    # -------------------------------------------------------------------------
    # Call Statistics & Aggregations
    # -------------------------------------------------------------------------

    def calculate_call_statistics(self, target_phones: List[str]) -> Tuple[CallStatistics, List[CDRRecord]]:
        """
        Calculate comprehensive call statistics across all records involving target_phones.
        Returns (CallStatistics, matching_records).
        """
        if not target_phones:
            return CallStatistics(), []

        target_set = set(target_phones)
        seen_calls: Dict[str, CDRRecord] = {}
        all_matches: List[CDRRecord] = []

        # Fetch from in-memory storage
        for p in target_phones:
            for rec in self.storage.get_records_for_phone(p):
                key = rec.call_id if rec.call_id else f"{rec.caller}_{rec.receiver}_{rec.timestamp.isoformat()}"
                if key not in seen_calls:
                    seen_calls[key] = rec
                    all_matches.append(rec)

        # Also pull any CALLS edges in the live graph involving these phones
        all_edges = self.graph.get_all_edges()
        for e in all_edges:
            rel = e.relationship.value if hasattr(e.relationship, "value") else str(e.relationship)
            if rel != EdgeType.CALLS.value:
                continue

            src_node = self.graph.get_node(e.source)
            tgt_node = self.graph.get_node(e.target)
            src_phone = (src_node.properties.get("phone_number") or src_node.name) if src_node else e.source
            tgt_phone = (tgt_node.properties.get("phone_number") or tgt_node.name) if tgt_node else e.target

            try:
                s_norm = normalize_phone_number(src_phone)
            except Exception:
                s_norm = str(src_phone)
            try:
                t_norm = normalize_phone_number(tgt_phone)
            except Exception:
                t_norm = str(tgt_phone)

            if s_norm in target_set or t_norm in target_set:
                key = e.id or f"EDGE_{e.source}_{e.target}_{e.properties.get('timestamp')}"
                if key not in seen_calls:
                    try:
                        ts = datetime.fromisoformat(str(e.properties.get("timestamp", datetime.now().isoformat())))
                    except Exception:
                        ts = datetime.now()
                    dur = int(e.properties.get("duration", e.properties.get("duration_seconds", 0)))
                    c_type = (
                        CallType.OUTGOING
                        if s_norm in target_set
                        else (CallType.INCOMING if t_norm in target_set else CallType.UNKNOWN)
                    )
                    synth_rec = CDRRecord(
                        call_id=e.id,
                        caller=s_norm,
                        receiver=t_norm,
                        timestamp=ts,
                        duration_seconds=dur,
                        call_type=c_type,
                        cell_tower=e.properties.get("tower") or e.properties.get("cell_tower"),
                        location=e.properties.get("location"),
                        case_id=e.properties.get("case_id"),
                        source_document=e.properties.get("source"),
                    )
                    seen_calls[key] = synth_rec
                    all_matches.append(synth_rec)

        if not all_matches:
            return CallStatistics(), []

        # Sort chronologically
        all_matches.sort(key=lambda r: r.timestamp)

        total_calls = len(all_matches)
        incoming_calls = 0
        outgoing_calls = 0
        missed_calls = 0
        total_duration = 0
        longest_call = 0
        calls_per_contact: Dict[str, int] = defaultdict(int)

        for r in all_matches:
            # Determine direction relative to target
            is_caller = r.caller in target_set
            is_receiver = r.receiver in target_set
            contact_number = r.receiver if is_caller else r.caller

            calls_per_contact[contact_number] += 1
            total_duration += r.duration_seconds
            if r.duration_seconds > longest_call:
                longest_call = r.duration_seconds

            if r.call_type == CallType.MISSED:
                missed_calls += 1
            elif is_caller and not is_receiver:
                outgoing_calls += 1
            elif is_receiver and not is_caller:
                incoming_calls += 1
            else:
                # Both or unknown
                if r.call_type == CallType.INCOMING:
                    incoming_calls += 1
                elif r.call_type == CallType.OUTGOING:
                    outgoing_calls += 1
                else:
                    outgoing_calls += 1

        avg_dur = round(total_duration / total_calls, 1) if total_calls > 0 else 0.0

        stats = CallStatistics(
            total_calls=total_calls,
            incoming_calls=incoming_calls,
            outgoing_calls=outgoing_calls,
            missed_calls=missed_calls,
            total_duration_seconds=total_duration,
            formatted_total_duration=_format_seconds(total_duration),
            avg_duration_seconds=avg_dur,
            longest_call_seconds=longest_call,
            unique_contacts=len(calls_per_contact),
            calls_per_contact=dict(calls_per_contact),
            first_call_timestamp=all_matches[0].timestamp.isoformat(),
            latest_call_timestamp=all_matches[-1].timestamp.isoformat(),
        )

        return stats, all_matches

    # -------------------------------------------------------------------------
    # Contact Level Statistics
    # -------------------------------------------------------------------------

    def calculate_contact_summaries(
        self, target_phones: List[str], records: List[CDRRecord], limit: int = 15
    ) -> List[ContactSummary]:
        """Compute contact-level aggregated interaction metrics."""
        if not target_phones or not records:
            return []

        target_set = set(target_phones)
        contact_map: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "call_count": 0,
                "incoming": 0,
                "outgoing": 0,
                "missed": 0,
                "total_duration": 0,
                "first_time": None,
                "latest_time": None,
            }
        )

        for r in records:
            is_caller = r.caller in target_set
            contact_number = r.receiver if is_caller else r.caller

            entry = contact_map[contact_number]
            entry["call_count"] += 1
            entry["total_duration"] += r.duration_seconds

            if r.call_type == CallType.MISSED:
                entry["missed"] += 1
            elif is_caller:
                entry["outgoing"] += 1
            else:
                entry["incoming"] += 1

            if entry["first_time"] is None or r.timestamp < entry["first_time"]:
                entry["first_time"] = r.timestamp
            if entry["latest_time"] is None or r.timestamp > entry["latest_time"]:
                entry["latest_time"] = r.timestamp

        summaries: List[ContactSummary] = []
        for contact_phone, data in contact_map.items():
            # Resolve contact name from graph if available
            contact_node, _ = self.resolve_entity_phones(contact_phone)
            contact_name = contact_node.name if contact_node else None

            # Check if this contact phone is owned by a person in the graph
            if not contact_name:
                for edge in self.graph.get_all_edges():
                    rel = edge.relationship.value if hasattr(edge.relationship, "value") else str(edge.relationship)
                    if rel == EdgeType.OWNS.value:
                        tgt_node = self.graph.get_node(edge.target)
                        if tgt_node and (tgt_node.id == contact_phone or tgt_node.properties.get("phone_number") == contact_phone):
                            person = self.graph.get_node(edge.source)
                            if person:
                                contact_name = f"{person.name} ({contact_phone})"
                                break

            avg_dur = round(data["total_duration"] / data["call_count"], 1) if data["call_count"] > 0 else 0.0

            digits = "".join(c for c in contact_phone if c.isdigit())
            contact_id = f"PHONE_{digits[-10:] if len(digits) >= 10 else digits}"

            summaries.append(
                ContactSummary(
                    contact_id=contact_id,
                    phone_number=contact_phone,
                    contact_name=contact_name,
                    call_count=data["call_count"],
                    incoming_count=data["incoming"],
                    outgoing_count=data["outgoing"],
                    missed_count=data["missed"],
                    total_duration_seconds=data["total_duration"],
                    formatted_duration=_format_seconds(data["total_duration"]),
                    avg_duration_seconds=avg_dur,
                    first_interaction=data["first_time"].isoformat() if data["first_time"] else None,
                    latest_interaction=data["latest_time"].isoformat() if data["latest_time"] else None,
                )
            )

        # Sort primarily by call count descending, then total duration
        summaries.sort(key=lambda x: (x.call_count, x.total_duration_seconds), reverse=True)
        return summaries[:limit]

    # -------------------------------------------------------------------------
    # Burst Detection Engine
    # -------------------------------------------------------------------------

    def detect_communication_bursts(
        self,
        records: List[CDRRecord],
        window_hours: int = 24,
        min_calls: int = 5,
        density_multiplier: float = 2.5,
    ) -> List[CommunicationBurst]:
        """
        Explainable sliding window burst detection.
        Identifies concentrated clusters of calls in a configurable window
        that significantly exceed the subject's baseline communication density.
        """
        if not records or len(records) < min_calls:
            return []

        sorted_records = sorted(records, key=lambda r: r.timestamp)
        total_span = (sorted_records[-1].timestamp - sorted_records[0].timestamp).total_seconds()
        total_hours = max(window_hours, total_span / 3600.0)
        baseline_rate_per_window = (len(sorted_records) / total_hours) * window_hours
        # Documented investigative rule: flag any window with >= min_calls
        # calls (>5 calls/24h by default). The baseline density ratio is
        # retained for severity grading, never for suppressing the flag.
        threshold_calls = min_calls

        bursts: List[CommunicationBurst] = []
        window_delta = timedelta(hours=window_hours)
        n = len(sorted_records)
        i = 0
        seen_windows: Set[Tuple[str, str]] = set()

        while i < n:
            window_start = sorted_records[i].timestamp
            window_end = window_start + window_delta

            # Find all records within this window
            j = i
            window_calls: List[CDRRecord] = []
            while j < n and sorted_records[j].timestamp <= window_end:
                window_calls.append(sorted_records[j])
                j += 1

            if len(window_calls) >= threshold_calls:
                contacts = {r.caller for r in window_calls} | {r.receiver for r in window_calls}
                contacts_count = len(contacts) - 1  # exclude target itself
                tot_dur = sum(r.duration_seconds for r in window_calls)

                # Determine indicator severity
                ratio = len(window_calls) / max(baseline_rate_per_window, 1.0)
                if ratio >= 4.0 or len(window_calls) >= 25:
                    sev = "HIGH"
                elif ratio >= 2.5 or len(window_calls) >= 12:
                    sev = "MEDIUM"
                else:
                    sev = "LOW"

                start_str = window_calls[0].timestamp.isoformat()
                end_str = window_calls[-1].timestamp.isoformat()
                window_key = (start_str[:13], end_str[:13])  # Deduplicate overlapping hour windows

                if window_key not in seen_windows:
                    seen_windows.add(window_key)
                    bursts.append(
                        CommunicationBurst(
                            start_time=start_str,
                            end_time=end_str,
                            call_count=len(window_calls),
                            unique_contacts=max(1, contacts_count),
                            total_duration_seconds=tot_dur,
                            severity=sev,
                            indicator="Communication Burst",
                            description=(
                                f"Observed {len(window_calls)} calls within a {window_hours}-hour period "
                                f"({len(window_calls)} vs {baseline_rate_per_window:.1f} baseline window density, "
                                f"{ratio:.1f}x normal frequency). Requires investigator verification."
                            ),
                        )
                    )
                i = j  # advance past this cluster
            else:
                i += 1

        return bursts

    # -------------------------------------------------------------------------
    # Communication Indicators (Neutral Decision Support)
    # -------------------------------------------------------------------------

    def compute_communication_indicators(
        self,
        stats: CallStatistics,
        top_contacts: List[ContactSummary],
        bursts: List[CommunicationBurst],
        centrality_score: float = 0.0,
    ) -> List[CommunicationIndicator]:
        """
        Generate explainable, neutral communication indicators.
        Labels strictly follow decision support guidelines without bias.
        """
        indicators: List[CommunicationIndicator] = []

        # 1. High-frequency contact
        if top_contacts:
            top_contact = top_contacts[0]
            if top_contact.call_count >= 15 or (
                stats.total_calls >= 10 and (top_contact.call_count / stats.total_calls) >= 0.45
            ):
                indicators.append(
                    CommunicationIndicator(
                        name="Frequent Contact",
                        level="NOTICE",
                        description=(
                            f"High communication activity with contact {top_contact.contact_name or top_contact.phone_number}: "
                            f"{top_contact.call_count} interactions ({round(top_contact.call_count / max(stats.total_calls, 1) * 100)}% of total calls). "
                            f"Requires investigator verification."
                        ),
                        metric_value={
                            "contact": top_contact.phone_number,
                            "calls": top_contact.call_count,
                            "percentage": round(top_contact.call_count / max(stats.total_calls, 1) * 100),
                        },
                    )
                )

        # 2. Long-duration communication
        long_duration_contacts = [c for c in top_contacts if c.total_duration_seconds >= 1800 or c.avg_duration_seconds >= 300]
        if long_duration_contacts:
            lead = long_duration_contacts[0]
            indicators.append(
                CommunicationIndicator(
                    name="Long-Duration Communication",
                    level="NOTICE",
                    description=(
                        f"Prolonged interaction observed with {lead.contact_name or lead.phone_number}: "
                        f"{_format_seconds(lead.total_duration_seconds)} cumulative conversation time "
                        f"(average {int(lead.avg_duration_seconds)}s/call). Requires investigator verification."
                    ),
                    metric_value={
                        "contact": lead.phone_number,
                        "duration_seconds": lead.total_duration_seconds,
                        "formatted_duration": lead.formatted_duration,
                    },
                )
            )

        # 3. Communication burst
        if bursts:
            most_severe = max(bursts, key=lambda b: b.call_count)
            indicators.append(
                CommunicationIndicator(
                    name="Communication Burst",
                    level="ELEVATED" if most_severe.severity == "HIGH" else "NOTICE",
                    description=most_severe.description,
                    metric_value={
                        "call_count": most_severe.call_count,
                        "start_time": most_severe.start_time,
                        "end_time": most_severe.end_time,
                        "severity": most_severe.severity,
                    },
                )
            )

        # 4. Central communication node (betweenness / degree)
        if centrality_score >= 0.15:
            indicators.append(
                CommunicationIndicator(
                    name="Central Communication Node",
                    level="NOTICE",
                    description=(
                        f"Entity exhibits elevated network centrality ({centrality_score:.3f} broker score), "
                        f"linking {stats.unique_contacts} unique communication endpoints. Requires investigator verification."
                    ),
                    metric_value={"centrality_score": centrality_score, "unique_contacts": stats.unique_contacts},
                )
            )

        return indicators

    # -------------------------------------------------------------------------
    # Comprehensive Analysis Orchestration
    # -------------------------------------------------------------------------

    def analyze_entity_cdr(
        self,
        entity_id: str,
        burst_window_hours: int = 24,
        burst_min_calls: int = 5,
        timeline_limit: int = 25,
    ) -> CDRAnalysisResult:
        """
        Orchestrate complete CDR analysis for a Person or Phone entity.
        Resolves phones, computes stats, extracts top contacts, detects bursts,
        correlates with GraphAnalytics, and compiles chronological timeline.
        """
        node, phone_numbers = self.resolve_entity_phones(entity_id)

        # Entity metadata
        if node:
            ent_id = node.id
            ent_type = node.label.value if hasattr(node.label, "value") else str(node.label)
            ent_name = node.name or node.id
        else:
            ent_id = entity_id
            ent_type = "Phone" if any(c.isdigit() for c in entity_id) else "Unknown"
            ent_name = entity_id

        # Calculate statistics
        stats, records = self.calculate_call_statistics(phone_numbers)

        # Contact breakdown
        top_contacts = self.calculate_contact_summaries(phone_numbers, records, limit=15)

        # Communication bursts
        bursts = self.detect_communication_bursts(
            records, window_hours=burst_window_hours, min_calls=burst_min_calls
        )

        # Graph centrality metrics correlation
        graph_centrality = {}
        centrality_val = 0.0
        try:
            analytics = GraphAnalytics(self.graph)
            metrics = analytics.compute_centrality_metrics()
            communities = analytics.detect_communities()

            # Find matching graph node ID for centrality
            match_id = node.id if node else None
            if not match_id and phone_numbers:
                match_id = f"PHONE_{phone_numbers[0][-10:]}"

            if match_id:
                d_val = metrics["degree"].get(match_id, 0.0)
                b_val = metrics["betweenness"].get(match_id, 0.0)
                pr_val = metrics["pagerank"].get(match_id, 0.0)
                comm_id = communities.get(match_id, 0)
                centrality_val = b_val
                graph_centrality = {
                    "node_id": match_id,
                    "degree_centrality": round(d_val, 4),
                    "betweenness_centrality": round(b_val, 4),
                    "pagerank": round(pr_val, 4),
                    "community_id": comm_id,
                }
        except Exception as e:
            logger.warning(f"Could not compute graph analytics for {entity_id}: {e}")

        # Indicators
        indicators = self.compute_communication_indicators(
            stats=stats,
            top_contacts=top_contacts,
            bursts=bursts,
            centrality_score=centrality_val,
        )

        # Chronological timeline preview (sorted newest first)
        timeline_items: List[CDRTimelineItem] = []
        newest_first = sorted(records, key=lambda r: r.timestamp, reverse=True)

        for r in newest_first[:timeline_limit]:
            # Resolve caller and receiver names
            c_node, _ = self.resolve_entity_phones(r.caller)
            r_node, _ = self.resolve_entity_phones(r.receiver)
            c_name = c_node.name if c_node else None
            r_name = r_node.name if r_node else None

            timeline_items.append(
                CDRTimelineItem(
                    call_id=r.call_id,
                    timestamp=r.timestamp.isoformat(),
                    caller=r.caller,
                    caller_name=c_name,
                    receiver=r.receiver,
                    receiver_name=r_name,
                    duration_seconds=r.duration_seconds,
                    formatted_duration=_format_seconds(r.duration_seconds),
                    call_type=r.call_type.value,
                    location=r.location,
                    cell_tower=r.cell_tower,
                    case_id=r.case_id,
                )
            )

        return CDRAnalysisResult(
            entity_id=ent_id,
            entity_type=ent_type,
            entity_name=ent_name,
            phone_numbers=phone_numbers,
            statistics=stats,
            top_contacts=top_contacts,
            indicators=indicators,
            bursts=bursts,
            graph_centrality=graph_centrality,
            call_timeline_preview=timeline_items,
        )

    def get_chronological_timeline(
        self, entity_id: str, limit: int = 100, reverse: bool = True
    ) -> List[CDRTimelineItem]:
        """Fetch full chronological call timeline for an entity."""
        _, phone_numbers = self.resolve_entity_phones(entity_id)
        if not phone_numbers:
            return []

        _, records = self.calculate_call_statistics(phone_numbers)
        records_sorted = sorted(records, key=lambda r: r.timestamp, reverse=reverse)

        items: List[CDRTimelineItem] = []
        for r in records_sorted[:limit]:
            c_node, _ = self.resolve_entity_phones(r.caller)
            r_node, _ = self.resolve_entity_phones(r.receiver)
            items.append(
                CDRTimelineItem(
                    call_id=r.call_id,
                    timestamp=r.timestamp.isoformat(),
                    caller=r.caller,
                    caller_name=c_node.name if c_node else None,
                    receiver=r.receiver,
                    receiver_name=r_node.name if r_node else None,
                    duration_seconds=r.duration_seconds,
                    formatted_duration=_format_seconds(r.duration_seconds),
                    call_type=r.call_type.value,
                    location=r.location,
                    cell_tower=r.cell_tower,
                    case_id=r.case_id,
                )
            )
        return items
