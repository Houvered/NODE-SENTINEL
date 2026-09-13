# -*- coding: utf-8 -*-
"""
Timeline Engine for NODE SENTINEL.
Aggregates chronological investigation events across all available modules:
- Financial Transactions (transfers, deposits, withdrawals)
- CDR Telephony (calls, cell tower logs)
- Case / FIR Involvements
- Location Observations (co-location, surveillance sightings)
- Vehicle Operations & Sightings
- Biometric Face Enrollments
Strictly adheres to neutral investigator decision-support terminology.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.cdr_analytics import get_cdr_storage
from app.core.face_storage import get_face_storage
from app.core.financial_analytics import get_financial_storage
from app.core.graph_engine import BaseGraphEngine, get_graph_engine
from app.models.graph_models import EdgeType, NodeType
from app.models.timeline_models import (
    TimelineEvent,
    TimelineEventType,
    TimelineResponse,
    TimelineSeverity,
)

logger = logging.getLogger(__name__)


def _parse_dt_safe(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo is not None else val
    s = str(val).strip()
    if not s:
        return None
    # ISO clean
    s_iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s_iso)
        return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
        except ValueError:
            continue
    return None


class TimelineEngine:
    """Multi-source chronological investigation timeline builder."""

    def __init__(self, graph_engine: Optional[BaseGraphEngine] = None) -> None:
        self.graph = graph_engine or get_graph_engine()
        self.cdr_storage = get_cdr_storage()
        self.fin_storage = get_financial_storage()
        self.face_storage = get_face_storage()
        self._ensure_seed_data()

    def _ensure_seed_data(self) -> None:
        """Auto-seed sample financial and CDR data if storages are currently empty."""
        try:
            if len(self.fin_storage.get_all_records()) == 0:
                import os
                from app.core.config import settings
                from app.core.financial_parser import FinancialParser
                fin_path = os.path.join(settings.BASE_DIR, "sample_data", "demo_financial.csv")
                if os.path.exists(fin_path):
                    with open(fin_path, "rb") as f:
                        records, _, _, _ = FinancialParser.parse_csv(f.read(), source_name="demo_financial.csv")
                        self.fin_storage.add_records(records)
        except Exception as e:
            logger.debug(f"Could not auto-seed timeline financial records: {e}")

        try:
            if len(self.cdr_storage.get_all_records()) == 0:
                import os
                from app.core.config import settings
                from app.core.cdr_parser import CDRParser
                cdr_path = os.path.join(settings.BASE_DIR, "sample_data", "demo_cdr.csv")
                if os.path.exists(cdr_path):
                    with open(cdr_path, "rb") as f:
                        records, _, _, _ = CDRParser.parse_csv(f.read(), source_name="demo_cdr.csv")
                        self.cdr_storage.add_records(records)
        except Exception as e:
            logger.debug(f"Could not auto-seed timeline CDR records: {e}")

    # -------------------------------------------------------------------------
    # Entity Resolution
    # -------------------------------------------------------------------------

    def resolve_subject(self, entity_id: str) -> Dict[str, Any]:
        """
        Identify entity and all its directly linked sub-entities:
        - target_node
        - person_ids
        - phone_ids & phone_numbers
        - account_ids & account_numbers
        - case_ids & case_codes
        - vehicle_ids
        - location_ids
        """
        clean_id = entity_id.strip()
        node = self.graph.get_node(clean_id)

        # Also search by phone or account number if node not found directly
        if not node:
            for n in self.graph.get_all_nodes():
                if n.name == clean_id:
                    node = n
                    break
                p_no = n.properties.get("phone_number") or n.properties.get("phone")
                if p_no and str(p_no).replace(" ", "").endswith(clean_id.replace(" ", "")[-10:]):
                    node = n
                    break
                acc_no = n.properties.get("account_number") or n.properties.get("account_id")
                if acc_no and str(acc_no).replace("ACC", "").replace("_", "") == clean_id.replace("ACC", "").replace("_", ""):
                    node = n
                    break

        ent_name = node.name if node else clean_id
        ent_type = node.label.value if (node and hasattr(node.label, "value")) else (str(node.label) if node else "Unknown")

        person_ids: Set[str] = set()
        phone_ids: Set[str] = set()
        account_ids: Set[str] = set()
        case_ids: Set[str] = set()
        vehicle_ids: Set[str] = set()
        location_ids: Set[str] = set()
        all_related_ids: Set[str] = {clean_id}

        if node:
            all_related_ids.add(node.id)
            lbl = ent_type.lower()

            if "person" in lbl:
                person_ids.add(node.id)
                # Check linked entities
                for edge in self.graph.get_all_edges():
                    if edge.source == node.id or edge.target == node.id:
                        other_id = edge.target if edge.source == node.id else edge.source
                        other_node = self.graph.get_node(other_id)
                        if not other_node:
                            continue
                        o_lbl = other_node.label.value if hasattr(other_node.label, "value") else str(other_node.label)
                        o_lbl_lower = o_lbl.lower()
                        all_related_ids.add(other_id)

                        if "phone" in o_lbl_lower:
                            phone_ids.add(other_id)
                            raw_p = other_node.properties.get("phone_number") or other_node.name
                            if raw_p:
                                phone_ids.add(str(raw_p))
                        elif "bank" in o_lbl_lower or "account" in o_lbl_lower:
                            account_ids.add(other_id)
                            raw_a = other_node.properties.get("account_number") or other_node.name
                            if raw_a:
                                account_ids.add(str(raw_a))
                        elif "case" in o_lbl_lower:
                            case_ids.add(other_id)
                            raw_c = other_node.properties.get("case_code") or other_node.name
                            if raw_c:
                                case_ids.add(str(raw_c))
                        elif "vehicle" in o_lbl_lower:
                            vehicle_ids.add(other_id)
                        elif "location" in o_lbl_lower:
                            location_ids.add(other_id)

                # Direct properties
                if "phone_number" in node.properties:
                    phone_ids.add(str(node.properties["phone_number"]))
                if "account_number" in node.properties:
                    account_ids.add(str(node.properties["account_number"]))

            elif "bank" in lbl or "account" in lbl:
                account_ids.add(node.id)
                raw_a = node.properties.get("account_number") or node.name
                if raw_a:
                    account_ids.add(str(raw_a))
                # Check owners
                for edge in self.graph.get_all_edges():
                    if (edge.target == node.id or edge.source == node.id) and edge.relationship == EdgeType.OWNS:
                        person_ids.add(edge.source)
                        all_related_ids.add(edge.source)

            elif "phone" in lbl:
                phone_ids.add(node.id)
                raw_p = node.properties.get("phone_number") or node.name
                if raw_p:
                    phone_ids.add(str(raw_p))
                for edge in self.graph.get_all_edges():
                    if (edge.target == node.id or edge.source == node.id) and edge.relationship == EdgeType.OWNS:
                        person_ids.add(edge.source)
                        all_related_ids.add(edge.source)

            elif "case" in lbl:
                case_ids.add(node.id)
                raw_c = node.properties.get("case_code") or node.name
                if raw_c:
                    case_ids.add(str(raw_c))
                # All entities involved in case
                for edge in self.graph.get_all_edges():
                    if (edge.target == node.id or edge.source == node.id) and edge.relationship == EdgeType.INVOLVED_IN:
                        other_id = edge.source if edge.target == node.id else edge.target
                        all_related_ids.add(other_id)
                        person_ids.add(other_id)

            elif "vehicle" in lbl:
                vehicle_ids.add(node.id)
                for edge in self.graph.get_all_edges():
                    if edge.target == node.id and edge.relationship == EdgeType.OPERATES:
                        person_ids.add(edge.source)
                        all_related_ids.add(edge.source)

            elif "location" in lbl:
                location_ids.add(node.id)
                for edge in self.graph.get_all_edges():
                    if edge.target == node.id and edge.relationship == EdgeType.LOCATED_AT:
                        person_ids.add(edge.source)
                        all_related_ids.add(edge.source)

        else:
            # Check if clean_id is an account format
            if clean_id.startswith("ACC") or clean_id.isdigit():
                account_ids.add(clean_id)
                ent_type = "BankAccount"
            elif clean_id.startswith("+") or clean_id.startswith("91") or (clean_id.isdigit() and len(clean_id) >= 10):
                phone_ids.add(clean_id)
                ent_type = "Phone"
            elif "FIR" in clean_id.upper() or clean_id.startswith("CASE"):
                case_ids.add(clean_id)
                ent_type = "Case"

        return {
            "entity_id": node.id if node else clean_id,
            "entity_name": ent_name,
            "entity_type": ent_type,
            "all_related_ids": all_related_ids,
            "person_ids": person_ids,
            "phone_ids": phone_ids,
            "account_ids": account_ids,
            "case_ids": case_ids,
            "vehicle_ids": vehicle_ids,
            "location_ids": location_ids,
        }

    # -------------------------------------------------------------------------
    # Event Collection
    # -------------------------------------------------------------------------

    def collect_financial_events(self, subject: Dict[str, Any]) -> List[TimelineEvent]:
        """Collect financial transfer events involving the subject or linked accounts."""
        events: List[TimelineEvent] = []
        target_accounts = subject["account_ids"] | subject["person_ids"] | subject["all_related_ids"]
        target_cases = subject["case_ids"]

        seen_txs = set()

        # 1. From FinancialStorage
        for r in self.fin_storage.get_all_records():
            is_party = (
                r.sender in target_accounts
                or r.receiver in target_accounts
                or (r.case_id and r.case_id in target_cases)
            )
            if not is_party:
                continue

            tx_key = r.transaction_id or f"{r.sender}_{r.receiver}_{r.timestamp.isoformat()}_{r.amount}"
            if tx_key in seen_txs:
                continue
            seen_txs.add(tx_key)

            s_node = self.graph.get_node(r.sender)
            r_node = self.graph.get_node(r.receiver)
            s_name = s_node.name if s_node else r.sender
            r_name = r_node.name if r_node else r.receiver

            # Severity determination
            if r.amount >= 500000.0:
                sev = TimelineSeverity.HIGH
            elif r.amount >= 100000.0:
                sev = TimelineSeverity.ELEVATED
            elif r.amount >= 25000.0:
                sev = TimelineSeverity.NOTICE
            else:
                sev = TimelineSeverity.INFO

            amt_formatted = f"₹{r.amount:,.2f}" if r.currency == "INR" else f"{r.amount:,.2f} {r.currency}"
            title = f"{amt_formatted} {r.transaction_type.value}: {s_name} → {r_name}"

            desc_parts = [f"Transaction of {amt_formatted} ({r.transaction_type.value}) executed from {s_name} ({r.sender}) to {r_name} ({r.receiver})."]
            if r.description:
                desc_parts.append(f"Reference: {r.description}")
            if r.case_id:
                desc_parts.append(f"Case: {r.case_id}")

            events.append(TimelineEvent(
                event_id=f"EVT_FIN_{r.transaction_id or len(events) + 1}",
                timestamp=_parse_dt_safe(r.timestamp) or r.timestamp,
                event_type=TimelineEventType.FINANCIAL,
                title=title,
                description=" ".join(desc_parts),
                source=r.source_document or "financial_ledger",
                entity_ids=[r.sender, r.receiver],
                related_case_ids=[r.case_id] if r.case_id else [],
                location=r.location,
                severity=sev,
                metadata={
                    "amount": r.amount,
                    "currency": r.currency,
                    "transaction_id": r.transaction_id,
                    "transaction_type": r.transaction_type.value,
                    "sender": r.sender,
                    "receiver": r.receiver,
                },
            ))

        # 2. From Live Graph TRANSFERRED_MONEY edges
        for edge in self.graph.get_all_edges():
            rel = edge.relationship.value if hasattr(edge.relationship, "value") else str(edge.relationship)
            if rel != EdgeType.TRANSFERRED_MONEY.value:
                continue

            c_id = edge.properties.get("case_id")
            is_party = (
                edge.source in target_accounts
                or edge.target in target_accounts
                or (c_id and c_id in target_cases)
            )
            if not is_party:
                continue

            tx_id = edge.properties.get("tx_id") or edge.properties.get("transaction_id") or edge.id
            if tx_id in seen_txs:
                continue
            seen_txs.add(tx_id)

            raw_ts = edge.properties.get("timestamp") or "2024-07-15T10:00:00"
            dt = _parse_dt_safe(raw_ts) or datetime(2024, 7, 15, 10, 0)
            amt = float(edge.properties.get("amount", 0.0))
            curr = str(edge.properties.get("currency", "INR"))
            t_type = str(edge.properties.get("transaction_type", "TRANSFER"))

            s_node = self.graph.get_node(edge.source)
            r_node = self.graph.get_node(edge.target)
            s_name = s_node.name if s_node else edge.source
            r_name = r_node.name if r_node else edge.target

            if amt >= 500000.0:
                sev = TimelineSeverity.HIGH
            elif amt >= 100000.0:
                sev = TimelineSeverity.ELEVATED
            elif amt >= 25000.0:
                sev = TimelineSeverity.NOTICE
            else:
                sev = TimelineSeverity.INFO

            amt_formatted = f"₹{amt:,.2f}" if curr == "INR" else f"{amt:,.2f} {curr}"
            title = f"{amt_formatted} {t_type}: {s_name} → {r_name}"
            desc = f"Transferred money edge in Knowledge Graph: {amt_formatted} from {s_name} to {r_name}."

            events.append(TimelineEvent(
                event_id=f"EVT_FIN_{tx_id}",
                timestamp=dt,
                event_type=TimelineEventType.FINANCIAL,
                title=title,
                description=desc,
                source=edge.properties.get("source") or "knowledge_graph",
                entity_ids=[edge.source, edge.target],
                related_case_ids=[c_id] if c_id else [],
                location=edge.properties.get("location"),
                severity=sev,
                metadata={"amount": amt, "currency": curr, "tx_id": tx_id, "sender": edge.source, "receiver": edge.target},
            ))

        return events

    def collect_cdr_events(self, subject: Dict[str, Any]) -> List[TimelineEvent]:
        """Collect CDR call events involving the subject or linked phone numbers."""
        events: List[TimelineEvent] = []
        target_phones = subject["phone_ids"] | subject["all_related_ids"]
        target_cases = subject["case_ids"]

        seen_calls = set()

        # 1. From CDRStorage
        for r in self.cdr_storage.get_all_records():
            is_party = (
                r.caller in target_phones
                or r.receiver in target_phones
                or (r.case_id and r.case_id in target_cases)
            )
            if not is_party:
                continue

            call_key = r.call_id or f"{r.caller}_{r.receiver}_{r.timestamp.isoformat()}"
            if call_key in seen_calls:
                continue
            seen_calls.add(call_key)

            c_node = self.graph.get_node(r.caller)
            rc_node = self.graph.get_node(r.receiver)
            c_name = c_node.name if c_node else r.caller
            rc_name = rc_node.name if rc_node else r.receiver

            # Severity
            if r.duration_seconds >= 600:
                sev = TimelineSeverity.ELEVATED
            elif r.duration_seconds >= 240:
                sev = TimelineSeverity.NOTICE
            else:
                sev = TimelineSeverity.INFO

            mins = r.duration_seconds // 60
            secs = r.duration_seconds % 60
            dur_str = f"{mins}m {secs}s" if r.duration_seconds > 0 else "0s (unanswered/missed)"

            title = f"Call ({r.call_type.value}): {c_name} → {rc_name}"
            desc = f"Telephony call between {c_name} and {rc_name}. Duration: {dur_str}."
            if r.cell_tower:
                desc += f" Tower: {r.cell_tower}."
            if r.location:
                desc += f" Area: {r.location}."

            events.append(TimelineEvent(
                event_id=f"EVT_CDR_{r.call_id or len(events) + 1}",
                timestamp=_parse_dt_safe(r.timestamp) or r.timestamp,
                event_type=TimelineEventType.CALL,
                title=title,
                description=desc,
                source=r.source_document or "cdr_records",
                entity_ids=[r.caller, r.receiver],
                related_case_ids=[r.case_id] if r.case_id else [],
                location=r.location or r.cell_tower,
                severity=sev,
                metadata={
                    "duration_seconds": r.duration_seconds,
                    "call_type": r.call_type.value,
                    "cell_tower": r.cell_tower,
                    "caller": r.caller,
                    "receiver": r.receiver,
                },
            ))

        # 2. From Live Graph CALLS edges
        for edge in self.graph.get_all_edges():
            rel = edge.relationship.value if hasattr(edge.relationship, "value") else str(edge.relationship)
            if rel != EdgeType.CALLS.value:
                continue

            c_id = edge.properties.get("case_id")
            is_party = (
                edge.source in target_phones
                or edge.target in target_phones
                or (c_id and c_id in target_cases)
            )
            if not is_party:
                continue

            call_id = edge.properties.get("call_id") or edge.id
            if call_id in seen_calls:
                continue
            seen_calls.add(call_id)

            raw_ts = edge.properties.get("timestamp") or "2024-07-15T10:00:00"
            dt = _parse_dt_safe(raw_ts) or datetime(2024, 7, 15, 10, 0)
            dur = int(edge.properties.get("duration", edge.properties.get("duration_seconds", 60)))
            freq = edge.properties.get("frequency", 1)

            c_node = self.graph.get_node(edge.source)
            rc_node = self.graph.get_node(edge.target)
            c_name = c_node.name if c_node else edge.source
            rc_name = rc_node.name if rc_node else edge.target

            sev = TimelineSeverity.NOTICE if (dur >= 240 or int(freq) >= 20) else TimelineSeverity.INFO
            title = f"Telephony Link: {c_name} ↔ {rc_name}"
            desc = f"Call link between {c_name} and {rc_name} (frequency: {freq}, duration: {dur}s)."

            events.append(TimelineEvent(
                event_id=f"EVT_CDR_{call_id}",
                timestamp=dt,
                event_type=TimelineEventType.CALL,
                title=title,
                description=desc,
                source=edge.properties.get("source") or "knowledge_graph",
                entity_ids=[edge.source, edge.target],
                related_case_ids=[c_id] if c_id else [],
                location=edge.properties.get("location") or edge.properties.get("cell_tower"),
                severity=sev,
                metadata={"duration_seconds": dur, "frequency": freq, "caller": edge.source, "receiver": edge.target},
            ))

        return events

    def collect_case_events(self, subject: Dict[str, Any]) -> List[TimelineEvent]:
        """Collect case involvement and FIR registration events."""
        events: List[TimelineEvent] = []
        target_ids = subject["all_related_ids"] | subject["person_ids"]
        target_cases = subject["case_ids"]
        is_case_subject = "case" in subject["entity_type"].lower()

        seen_cases = set()

        for edge in self.graph.get_all_edges():
            rel = edge.relationship.value if hasattr(edge.relationship, "value") else str(edge.relationship)
            if rel != EdgeType.INVOLVED_IN.value:
                continue

            if is_case_subject:
                is_party = (edge.source in target_cases or edge.target in target_cases)
            else:
                is_party = (
                    edge.source in target_ids
                    or edge.target in target_ids
                    or edge.target in target_cases
                    or edge.source in target_cases
                )
            if not is_party:
                continue

            case_node = self.graph.get_node(edge.target)
            person_node = self.graph.get_node(edge.source)
            if not case_node or not person_node:
                continue

            c_lbl = case_node.label.value if hasattr(case_node.label, "value") else str(case_node.label)
            if "case" not in c_lbl.lower() and not case_node.id.startswith("CASE_"):
                # swap if orientation reversed
                case_node, person_node = person_node, case_node

            pair_key = f"{person_node.id}_{case_node.id}"
            if pair_key in seen_cases:
                continue
            seen_cases.add(pair_key)

            case_code = case_node.properties.get("case_code") or case_node.name or case_node.id
            role = edge.properties.get("role") or person_node.properties.get("role") or "Suspect"
            section = case_node.properties.get("section") or case_node.properties.get("crime_type") or "Police FIR Registry"

            # Parse year/timestamp from case code (e.g. FIR-2024-311 -> 2024-01-15)
            year_match = re.search(r"20\d{2}", case_code)
            year = int(year_match.group(0)) if year_match else 2024
            dt = datetime(year, 1, 15, 9, 0)

            # High severity for major narcotics/extortion cases
            sev = TimelineSeverity.ELEVATED if any(k in section.lower() for k in ("ndps", "pmla", "arms", "extortion", "kingpin")) else TimelineSeverity.NOTICE

            title = f"Case Filing: {case_code} · {case_node.name}"
            desc = f"Subject '{person_node.name}' identified with role '{role}' under investigation case {case_code} ({section})."

            events.append(TimelineEvent(
                event_id=f"EVT_CASE_{case_node.id}_{person_node.id}",
                timestamp=dt,
                event_type=TimelineEventType.CASE,
                title=title,
                description=desc,
                source="police_fir_registry",
                entity_ids=[person_node.id, case_node.id],
                related_case_ids=[case_code],
                location=case_node.properties.get("city") or "State Police Station",
                severity=sev,
                metadata={"case_code": case_code, "role": role, "section": section},
            ))

        return events

    def collect_location_events(self, subject: Dict[str, Any]) -> List[TimelineEvent]:
        """Collect physical location observation events from LOCATED_AT edges."""
        events: List[TimelineEvent] = []
        target_ids = subject["all_related_ids"] | subject["person_ids"] | subject["location_ids"]

        seen_locs = set()

        for edge in self.graph.get_all_edges():
            rel = edge.relationship.value if hasattr(edge.relationship, "value") else str(edge.relationship)
            if rel != EdgeType.LOCATED_AT.value:
                continue

            is_party = edge.source in target_ids or edge.target in target_ids
            if not is_party:
                continue

            p_node = self.graph.get_node(edge.source)
            l_node = self.graph.get_node(edge.target)
            if not p_node or not l_node:
                continue

            # Ensure proper orientation
            l_lbl = l_node.label.value if hasattr(l_node.label, "value") else str(l_node.label)
            if "location" not in l_lbl.lower() and not l_node.id.startswith("LOC_"):
                p_node, l_node = l_node, p_node

            pair_key = f"{p_node.id}_{l_node.id}"
            if pair_key in seen_locs:
                continue
            seen_locs.add(pair_key)

            obs_raw = edge.properties.get("observed") or edge.properties.get("timestamp") or "2024-07-16"
            dt = _parse_dt_safe(obs_raw) or datetime(2024, 7, 16, 12, 0)

            loc_name = l_node.name or l_node.id
            facility = l_node.properties.get("facility") or l_node.properties.get("city") or "Surveillance Zone"

            title = f"Location Sighting: {p_node.name} at {loc_name}"
            desc = f"Physical sighting of subject '{p_node.name}' documented at '{loc_name}' ({facility})."

            events.append(TimelineEvent(
                event_id=f"EVT_LOC_{l_node.id}_{p_node.id}",
                timestamp=dt,
                event_type=TimelineEventType.LOCATION,
                title=title,
                description=desc,
                source=edge.properties.get("source") or "field_surveillance_log",
                entity_ids=[p_node.id, l_node.id],
                related_case_ids=[],
                location=loc_name,
                severity=TimelineSeverity.INFO,
                metadata={"location_id": l_node.id, "facility": facility},
            ))

        return events

    def collect_vehicle_events(self, subject: Dict[str, Any]) -> List[TimelineEvent]:
        """Collect vehicle operation and observation events from OPERATES edges."""
        events: List[TimelineEvent] = []
        target_ids = subject["all_related_ids"] | subject["person_ids"] | subject["vehicle_ids"]

        seen_vehs = set()

        for edge in self.graph.get_all_edges():
            rel = edge.relationship.value if hasattr(edge.relationship, "value") else str(edge.relationship)
            if rel != EdgeType.OPERATES.value:
                continue

            is_party = edge.source in target_ids or edge.target in target_ids
            if not is_party:
                continue

            p_node = self.graph.get_node(edge.source)
            v_node = self.graph.get_node(edge.target)
            if not p_node or not v_node:
                continue

            pair_key = f"{p_node.id}_{v_node.id}"
            if pair_key in seen_vehs:
                continue
            seen_vehs.add(pair_key)

            dt = datetime(2024, 7, 15, 8, 30)
            veh_name = v_node.name or v_node.id
            make = v_node.properties.get("make") or v_node.properties.get("model") or "Motor Vehicle"
            color = v_node.properties.get("color") or ""
            veh_desc = f"{color} {make}".strip()

            title = f"Vehicle Link: {p_node.name} operates {veh_name}"
            desc = f"Subject '{p_node.name}' documented operating registered vehicle {veh_name} ({veh_desc})."

            events.append(TimelineEvent(
                event_id=f"EVT_VEH_{v_node.id}_{p_node.id}",
                timestamp=dt,
                event_type=TimelineEventType.VEHICLE,
                title=title,
                description=desc,
                source="transport_authority_registry",
                entity_ids=[p_node.id, v_node.id],
                related_case_ids=[],
                location=None,
                severity=TimelineSeverity.INFO,
                metadata={"vehicle_id": v_node.id, "registration": veh_name, "make": make},
            ))

        return events

    def collect_face_events(self, subject: Dict[str, Any]) -> List[TimelineEvent]:
        """Collect biometric face registration events from FaceStorage."""
        events: List[TimelineEvent] = []
        target_ids = subject["all_related_ids"] | subject["person_ids"]

        for idn in self.face_storage.list_identities():
            p_id = idn.person_id or idn.identity_id
            if p_id in target_ids:
                raw_ts = idn.registration_timestamp or "2024-07-14T08:00:00"
                dt = _parse_dt_safe(raw_ts) or datetime(2024, 7, 14, 8, 0)

                events.append(TimelineEvent(
                    event_id=f"EVT_FACE_{p_id}",
                    timestamp=dt,
                    event_type=TimelineEventType.FACE,
                    title=f"Biometric Face Profile Enrolled: {idn.name}",
                    description=f"Facial biometric reference vector enrolled in registry for subject '{idn.name}'. Decision-support reference only.",
                    source="biometric_face_database",
                    entity_ids=[p_id],
                    related_case_ids=[idn.case_id] if idn.case_id else [],
                    location=None,
                    severity=TimelineSeverity.INFO,
                    metadata={"person_id": p_id, "case_id": idn.case_id},
                ))

        return events

    # -------------------------------------------------------------------------
    # Unified Timeline Aggregation & Filtering
    # -------------------------------------------------------------------------

    def get_entity_timeline(
        self,
        entity_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 100,
        sort_order: str = "desc",
        search: Optional[str] = None,
    ) -> TimelineResponse:
        """
        Build and filter the complete investigation timeline for an entity.
        Safe against unknown entities, malformed dates, and missing filters.
        """
        clean_id = (entity_id or "").strip()
        if not clean_id:
            return TimelineResponse(
                entity_id="",
                entity_name="Unknown",
                entity_type="Unknown",
                events=[],
                total_events=0,
                date_range={"start": None, "end": None},
                event_type_counts={},
                severity_counts={},
                high_priority_count=0,
            )

        subject = self.resolve_subject(clean_id)

        # Collect events from all data streams
        all_events: List[TimelineEvent] = []
        try:
            all_events.extend(self.collect_financial_events(subject))
        except Exception as e:
            logger.warning(f"Error collecting financial events for {clean_id}: {e}")

        try:
            all_events.extend(self.collect_cdr_events(subject))
        except Exception as e:
            logger.warning(f"Error collecting CDR events for {clean_id}: {e}")

        try:
            all_events.extend(self.collect_case_events(subject))
        except Exception as e:
            logger.warning(f"Error collecting case events for {clean_id}: {e}")

        try:
            all_events.extend(self.collect_location_events(subject))
        except Exception as e:
            logger.warning(f"Error collecting location events for {clean_id}: {e}")

        try:
            all_events.extend(self.collect_vehicle_events(subject))
        except Exception as e:
            logger.warning(f"Error collecting vehicle events for {clean_id}: {e}")

        try:
            all_events.extend(self.collect_face_events(subject))
        except Exception as e:
            logger.warning(f"Error collecting face events for {clean_id}: {e}")

        # Filter by date range
        start_dt = _parse_dt_safe(start) if start else None
        end_dt = None
        if end:
            end_dt = _parse_dt_safe(end)
            if end_dt and len(str(end).strip()) <= 10:
                end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

        filtered: List[TimelineEvent] = []
        for ev in all_events:
            if start_dt and ev.timestamp < start_dt:
                continue
            if end_dt and ev.timestamp > end_dt:
                continue

            # Filter by event_type (supports comma-separated e.g. "FINANCIAL,CALL")
            if event_type and event_type.upper() != "ALL":
                allowed_types = {t.strip().upper() for t in event_type.split(",")}
                if ev.event_type.value.upper() not in allowed_types:
                    continue

            # Filter by severity
            if severity and severity.upper() != "ALL":
                allowed_sevs = {s.strip().upper() for s in severity.split(",")}
                if ev.severity.value.upper() not in allowed_sevs:
                    continue

            # Keyword search
            if search:
                q = search.lower().strip()
                match = (
                    q in ev.title.lower()
                    or q in ev.description.lower()
                    or (ev.location and q in ev.location.lower())
                    or (ev.source and q in ev.source.lower())
                    or any(q in c.lower() for c in ev.related_case_ids)
                )
                if not match:
                    continue

            filtered.append(ev)

        # Sort order
        reverse = sort_order.lower() != "asc"
        filtered.sort(key=lambda ev: ev.timestamp, reverse=reverse)

        # Compute metric aggregates across filtered stream
        type_counts: Dict[str, int] = {}
        sev_counts: Dict[str, int] = {}
        high_prio_count = 0

        for ev in filtered:
            type_counts[ev.event_type.value] = type_counts.get(ev.event_type.value, 0) + 1
            sev_counts[ev.severity.value] = sev_counts.get(ev.severity.value, 0) + 1
            if ev.severity in (TimelineSeverity.HIGH, TimelineSeverity.ELEVATED):
                high_prio_count += 1

        # Determine date range
        if filtered:
            all_ts = [ev.timestamp for ev in filtered]
            min_ts = min(all_ts).isoformat()
            max_ts = max(all_ts).isoformat()
        else:
            min_ts = None
            max_ts = None

        # Slice limit
        safe_limit = max(1, min(1000, limit))
        paged_events = filtered[:safe_limit]

        return TimelineResponse(
            entity_id=subject["entity_id"],
            entity_name=subject["entity_name"],
            entity_type=subject["entity_type"],
            events=paged_events,
            total_events=len(filtered),
            date_range={"start": min_ts, "end": max_ts, "earliest": min_ts, "latest": max_ts},
            event_type_counts=type_counts,
            severity_counts=sev_counts,
            high_priority_count=high_prio_count,
        )


_timeline_engine_instance: Optional[TimelineEngine] = None


def get_timeline_engine() -> TimelineEngine:
    global _timeline_engine_instance
    if _timeline_engine_instance is None:
        _timeline_engine_instance = TimelineEngine()
    return _timeline_engine_instance
