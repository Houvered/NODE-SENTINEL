# -*- coding: utf-8 -*-
"""
Financial Analytics & Storage Service for NODE SENTINEL.
Aggregates account statistics, calculates counterparty interaction metrics,
detects transaction velocity bursts, evaluates money flow, and correlates financial patterns
with the live Knowledge Graph. Strictly adheres to neutral investigator decision-support terminology.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from app.core.financial_parser import normalize_account_identifier
from app.core.graph_analytics import GraphAnalytics
from app.core.graph_engine import BaseGraphEngine, get_graph_engine
from app.models.financial_models import (
    CounterpartySummary,
    FinancialAnalysisResult,
    FinancialBurst,
    FinancialFlowSummary,
    FinancialIndicator,
    FinancialRecord,
    FlowBreakdownItem,
    TransactionItem,
    TransactionStatistics,
    TransactionType,
)
from app.models.graph_models import Edge, EdgeType, Node, NodeType

logger = logging.getLogger(__name__)

# Configurable investigation threshold for high-value transactions (default ₹500,000)
DEFAULT_HIGH_VALUE_THRESHOLD = 500000.0


class FinancialStorage:
    """In-memory indexed storage for Financial records."""

    def __init__(self) -> None:
        self._records: List[FinancialRecord] = []
        self._by_account: Dict[str, List[FinancialRecord]] = defaultdict(list)
        self._by_tx_id: Dict[str, FinancialRecord] = {}

    def add_records(self, records: List[FinancialRecord]) -> int:
        added = 0
        for r in records:
            if r.transaction_id and r.transaction_id in self._by_tx_id:
                # Deduplicate by tx_id
                continue
            self._records.append(r)
            if r.transaction_id:
                self._by_tx_id[r.transaction_id] = r
            self._by_account[r.sender].append(r)
            self._by_account[r.receiver].append(r)
            added += 1
        return added

    def get_records_for_account(self, account_id: str) -> List[FinancialRecord]:
        return self._by_account.get(account_id, [])

    def get_all_records(self) -> List[FinancialRecord]:
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()
        self._by_account.clear()
        self._by_tx_id.clear()


_financial_storage_instance: Optional[FinancialStorage] = None


def get_financial_storage() -> FinancialStorage:
    global _financial_storage_instance
    if _financial_storage_instance is None:
        _financial_storage_instance = FinancialStorage()
    return _financial_storage_instance


class FinancialService:
    """Investigator decision-support service for financial transaction analysis."""

    def __init__(
        self,
        graph_engine: Optional[BaseGraphEngine] = None,
        storage: Optional[FinancialStorage] = None,
    ) -> None:
        self.graph = graph_engine or get_graph_engine()
        self.storage = storage or get_financial_storage()
        self.analytics = GraphAnalytics(self.graph)

    # -------------------------------------------------------------------------
    # Graph Wiring & Entity Resolution
    # -------------------------------------------------------------------------

    def find_or_create_account_node(self, account_id: str) -> Tuple[Node, bool]:
        """
        Find an existing BankAccount node in the graph matching this account identifier,
        or create a new BankAccount node without duplication.
        Returns (Node, created_boolean).
        """
        # 1. Direct ID check
        existing = self.graph.get_node(account_id)
        if existing:
            return existing, False

        # 2. Check all nodes in graph for account number property, name, or label match
        for node in self.graph.get_all_nodes():
            lbl = node.label.value if hasattr(node.label, "value") else str(node.label)
            if lbl == NodeType.BANK_ACCOUNT.value or node.id.startswith("ACC_"):
                acc_prop = node.properties.get("account_number") or node.properties.get("account_id") or node.name or ""
                # Compare cleaned
                if acc_prop == account_id or node.id == account_id:
                    return node, False
                if str(acc_prop).replace("ACC", "").replace("_", "") == account_id.replace("ACC", "").replace("_", ""):
                    return node, False

        # Also check if account_id corresponds to a Person node directly
        p_node = self.graph.get_node(account_id)
        if p_node:
            return p_node, False

        # 3. Create new BankAccount node
        canonical_id = account_id if account_id.startswith("ACC_") else f"ACC_{account_id.replace('ACC', '')}"
        # Check if canonical_id exists
        existing_canon = self.graph.get_node(canonical_id)
        if existing_canon:
            return existing_canon, False

        new_node = Node(
            id=canonical_id,
            label=NodeType.BANK_ACCOUNT,
            name=account_id,
            properties={"account_number": account_id, "source": "financial_ingest"},
        )
        self.graph.add_node(new_node)
        return new_node, True

    def find_or_create_case_node(self, case_id: str) -> Tuple[Node, bool]:
        """
        Find an existing Case node or create one if absent.
        """
        existing = self.graph.get_node(case_id)
        if existing:
            return existing, False

        for node in self.graph.get_all_nodes():
            lbl = node.label.value if hasattr(node.label, "value") else str(node.label)
            if lbl == NodeType.CASE.value or node.id.startswith("CASE_"):
                case_code = node.properties.get("case_code") or node.name or node.id
                if case_code == case_id or node.id == f"CASE_{case_id}":
                    return node, False

        canonical_id = case_id if case_id.startswith("CASE_") else f"CASE_{case_id}"
        new_case = Node(
            id=canonical_id,
            label=NodeType.CASE,
            name=case_id,
            properties={"case_code": case_id, "source": "financial_ingest"},
        )
        self.graph.add_node(new_case)
        return new_case, True

    def ingest_records_into_graph(self, records: List[FinancialRecord]) -> Tuple[int, int, int]:
        """
        Integrate parsed financial transaction records into the Knowledge Graph and storage.
        - Preserves existing accounts and person relationships.
        - Wires Sender Account -> Receiver Account with TRANSFERRED_MONEY edges.
        - Links Case nodes where case_id is provided.
        - Attaches transaction metadata to edges.
        - Prevents duplicate nodes and edges.
        Returns (records_added, entities_created, relationships_created).
        """
        entities_created = 0
        relationships_created = 0

        # Store in indexed memory
        records_added = self.storage.add_records(records)

        # Fast resolution indexes: the per-record graph scans in
        # find_or_create_* are O(nodes)/O(edges) each, i.e. O(n^2) for
        # production-size seeds (70s+ for 2k rows). Snapshot once here.
        id_index: Dict[str, Node] = {}
        prop_index: Dict[str, Node] = {}
        stripped_index: Dict[str, Node] = {}

        def _register_account_node(node: Node) -> None:
            id_index[node.id] = node
            lbl = node.label.value if hasattr(node.label, "value") else str(node.label)
            if lbl == NodeType.BANK_ACCOUNT.value or node.id.startswith("ACC_"):
                raw_acc = (node.properties.get("account_number")
                           or node.properties.get("account_id") or node.name or node.id)
                prop_index[str(raw_acc)] = node
                stripped_index[str(raw_acc).replace("ACC", "").replace("_", "")] = node
                stripped_index[node.id.replace("ACC", "").replace("_", "")] = node

        for _n in self.graph.get_all_nodes():
            _register_account_node(_n)

        def _resolve_account_fast(account_id: str) -> Tuple[Node, bool]:
            hit = id_index.get(account_id)
            if hit is not None:
                return hit, False
            hit = prop_index.get(account_id)
            if hit is not None:
                return hit, False
            hit = stripped_index.get(account_id.replace("ACC", "").replace("_", ""))
            if hit is not None:
                return hit, False
            canonical_id = (account_id if account_id.startswith("ACC_")
                            else f"ACC_{account_id.replace('ACC', '')}")
            hit = id_index.get(canonical_id)
            if hit is not None:
                return hit, False
            new_node = Node(
                id=canonical_id,
                label=NodeType.BANK_ACCOUNT,
                name=account_id,
                properties={"account_number": account_id, "source": "financial_ingest"},
            )
            self.graph.add_node(new_node)
            _register_account_node(new_node)
            return new_node, True

        case_cache: Dict[str, Node] = {}
        involved_pairs = {
            (e.source, e.target) for e in self.graph.get_all_edges()
            if (e.relationship.value if hasattr(e.relationship, "value")
                else str(e.relationship)) == EdgeType.INVOLVED_IN.value
        }

        for rec in records:
            # Resolve or create sender node
            sender_node, created_s = _resolve_account_fast(rec.sender)
            if created_s:
                entities_created += 1

            # Resolve or create receiver node
            receiver_node, created_r = _resolve_account_fast(rec.receiver)
            if created_r:
                entities_created += 1

            # Build TRANSFERRED_MONEY edge
            edge_id = f"TX_{rec.transaction_id}" if rec.transaction_id else None
            edge_props = {
                "amount": rec.amount,
                "currency": rec.currency,
                "timestamp": rec.timestamp.isoformat(),
                "tx_id": rec.transaction_id,
                "transaction_id": rec.transaction_id,
                "transaction_type": rec.transaction_type.value,
            }
            if rec.location:
                edge_props["location"] = rec.location
            if rec.case_id:
                edge_props["case_id"] = rec.case_id
            if rec.source_document:
                edge_props["source"] = rec.source_document
            if rec.description:
                edge_props["description"] = rec.description

            tx_edge = Edge(
                source=sender_node.id,
                target=receiver_node.id,
                relationship=EdgeType.TRANSFERRED_MONEY,
                properties=edge_props,
                id=edge_id,
            )
            self.graph.add_edge(tx_edge)
            relationships_created += 1

            # If case_id is present, link sender and receiver to case if not already linked
            if rec.case_id:
                case_node = case_cache.get(rec.case_id)
                if case_node is None:
                    case_node, created_c = self.find_or_create_case_node(rec.case_id)
                    case_cache[rec.case_id] = case_node
                    if created_c:
                        entities_created += 1

                # Check if edge already exists from sender to case
                if (sender_node.id, case_node.id) not in involved_pairs:
                    self.graph.add_edge(
                        Edge(
                            source=sender_node.id,
                            target=case_node.id,
                            relationship=EdgeType.INVOLVED_IN,
                            properties={"role": "Financial Party", "case_id": rec.case_id},
                        )
                    )
                    involved_pairs.add((sender_node.id, case_node.id))
                    relationships_created += 1

        return records_added, entities_created, relationships_created

    def resolve_entity_accounts(self, entity_id: str) -> Tuple[Optional[Node], List[str]]:
        """
        Resolve an entity ID (which could be a Person, BankAccount, or raw account string)
        to the underlying Node and associated normalized account identifiers.
        """
        node = self.graph.get_node(entity_id)

        try:
            norm_direct = normalize_account_identifier(entity_id)
        except Exception:
            norm_direct = None

        accounts: Set[str] = set()

        if node:
            lbl = node.label.value if hasattr(node.label, "value") else str(node.label)

            if lbl == NodeType.BANK_ACCOUNT.value or node.id.startswith("ACC_"):
                raw_acc = node.properties.get("account_number") or node.properties.get("account_id") or node.name or node.id
                accounts.add(node.id)
                try:
                    accounts.add(normalize_account_identifier(raw_acc))
                except Exception:
                    accounts.add(str(raw_acc))
                return node, list(accounts)

            elif lbl == NodeType.PERSON.value or node.id.startswith("PERSON_"):
                # Also consider the Person ID itself as an endpoint if transactions were wired directly to person
                accounts.add(node.id)

                # Check for bank accounts linked to this person via OWNS or TRANSFERRED_MONEY
                for edge in self.graph.get_all_edges():
                    rel = edge.relationship.value if hasattr(edge.relationship, "value") else str(edge.relationship)
                    if edge.source == node.id:
                        target_node = self.graph.get_node(edge.target)
                        if target_node:
                            t_lbl = target_node.label.value if hasattr(target_node.label, "value") else str(target_node.label)
                            if t_lbl == NodeType.BANK_ACCOUNT.value or target_node.id.startswith("ACC_"):
                                accounts.add(target_node.id)
                                raw_acc = target_node.properties.get("account_number") or target_node.name
                                if raw_acc:
                                    accounts.add(str(raw_acc))
                    elif edge.target == node.id:
                        source_node = self.graph.get_node(edge.source)
                        if source_node:
                            s_lbl = source_node.label.value if hasattr(source_node.label, "value") else str(source_node.label)
                            if s_lbl == NodeType.BANK_ACCOUNT.value or source_node.id.startswith("ACC_"):
                                accounts.add(source_node.id)
                                raw_acc = source_node.properties.get("account_number") or source_node.name
                                if raw_acc:
                                    accounts.add(str(raw_acc))

                # Check properties on person
                for key in ("account", "account_number", "bank_account", "account_id"):
                    if key in node.properties:
                        try:
                            accounts.add(normalize_account_identifier(node.properties[key]))
                        except Exception:
                            accounts.add(str(node.properties[key]))

                return node, list(accounts)

            else:
                # Other node type with account properties
                accounts.add(node.id)
                if "account_number" in node.properties:
                    try:
                        accounts.add(normalize_account_identifier(node.properties["account_number"]))
                    except Exception:
                        pass
                return node, list(accounts)

        # If no node found directly by ID, search by account string
        if norm_direct:
            for n in self.graph.get_all_nodes():
                lbl = n.label.value if hasattr(n.label, "value") else str(n.label)
                if lbl == NodeType.BANK_ACCOUNT.value or n.id.startswith("ACC_"):
                    raw_acc = n.properties.get("account_number") or n.name or n.id
                    try:
                        if normalize_account_identifier(raw_acc) == norm_direct or n.id == norm_direct:
                            return n, [n.id, norm_direct]
                    except Exception:
                        pass
            return None, [norm_direct]

        return None, []

    # -------------------------------------------------------------------------
    # Transaction Aggregations & Statistics
    # -------------------------------------------------------------------------

    def get_all_records_for_accounts(self, target_accounts: List[str]) -> List[FinancialRecord]:
        """
        Pull records involving target_accounts from both indexed FinancialStorage
        and existing live Knowledge Graph TRANSFERRED_MONEY edges.
        """
        if not target_accounts:
            return []

        target_set = set(target_accounts)
        seen_txs: Dict[str, FinancialRecord] = {}
        all_matches: List[FinancialRecord] = []

        # 1. Fetch from indexed memory
        for acc in target_accounts:
            for rec in self.storage.get_records_for_account(acc):
                key = rec.transaction_id or f"{rec.sender}_{rec.receiver}_{rec.timestamp.isoformat()}_{rec.amount}"
                if key not in seen_txs:
                    seen_txs[key] = rec
                    all_matches.append(rec)

        # 2. Pull TRANSFERRED_MONEY edges from the live graph
        for edge in self.graph.get_all_edges():
            rel = edge.relationship.value if hasattr(edge.relationship, "value") else str(edge.relationship)
            if rel != EdgeType.TRANSFERRED_MONEY.value:
                continue

            src_match = edge.source in target_set
            tgt_match = edge.target in target_set

            # Also check if account numbers match
            if not src_match:
                s_node = self.graph.get_node(edge.source)
                if s_node:
                    s_acc = s_node.properties.get("account_number") or s_node.name
                    if s_acc in target_set:
                        src_match = True
            if not tgt_match:
                t_node = self.graph.get_node(edge.target)
                if t_node:
                    t_acc = t_node.properties.get("account_number") or t_node.name
                    if t_acc in target_set:
                        tgt_match = True

            if src_match or tgt_match:
                tx_id = edge.properties.get("tx_id") or edge.properties.get("transaction_id") or edge.id
                key = str(tx_id)
                if key not in seen_txs:
                    # Parse timestamp
                    raw_ts = edge.properties.get("timestamp") or datetime.now().isoformat()
                    try:
                        dt = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                    except Exception:
                        dt = datetime.now()

                    amt = float(edge.properties.get("amount", 0.0))
                    curr = str(edge.properties.get("currency", "INR"))
                    tt_raw = edge.properties.get("transaction_type", "TRANSFER")
                    from app.core.financial_parser import parse_transaction_type
                    tt = parse_transaction_type(tt_raw)

                    rec = FinancialRecord(
                        transaction_id=str(tx_id),
                        timestamp=dt,
                        sender=edge.source,
                        receiver=edge.target,
                        amount=amt,
                        currency=curr,
                        transaction_type=tt,
                        case_id=edge.properties.get("case_id"),
                        location=edge.properties.get("location"),
                        source_document=edge.properties.get("source"),
                        description=edge.properties.get("description"),
                    )
                    seen_txs[key] = rec
                    all_matches.append(rec)

        # Sort chronological newest first
        all_matches.sort(key=lambda r: r.timestamp, reverse=True)
        return all_matches

    def calculate_transaction_statistics(
        self, target_accounts: List[str], records: List[FinancialRecord]
    ) -> TransactionStatistics:
        """
        Calculate summary transaction metrics for an entity across matching records.
        """
        if not records or not target_accounts:
            return TransactionStatistics()

        target_set = set(target_accounts)
        outgoing_amt = 0.0
        incoming_amt = 0.0
        amounts = []
        counterparties = set()
        timestamps = []

        for r in records:
            amt = r.amount
            amounts.append(amt)
            timestamps.append(r.timestamp)

            is_sender = r.sender in target_set
            is_receiver = r.receiver in target_set

            if is_sender:
                outgoing_amt += amt
                counterparties.add(r.receiver)
            if is_receiver:
                incoming_amt += amt
                counterparties.add(r.sender)

        counterparties.difference_update(target_set)

        net_flow = incoming_amt - outgoing_amt
        total_tx = len(records)
        avg_amt = sum(amounts) / total_tx if total_tx > 0 else 0.0
        largest_tx = max(amounts) if amounts else 0.0
        smallest_tx = min(amounts) if amounts else 0.0

        timestamps.sort()
        first_tx = timestamps[0].isoformat() if timestamps else None
        latest_tx = timestamps[-1].isoformat() if timestamps else None

        # Frequency: transactions per day over the active span
        days_span = max(1.0, (timestamps[-1] - timestamps[0]).total_seconds() / 86400.0) if len(timestamps) > 1 else 1.0
        frequency = round(total_tx / days_span, 2)

        return TransactionStatistics(
            total_transactions=total_tx,
            total_outgoing_amount=round(outgoing_amt, 2),
            total_incoming_amount=round(incoming_amt, 2),
            net_flow=round(net_flow, 2),
            average_transaction_amount=round(avg_amt, 2),
            largest_transaction=round(largest_tx, 2),
            smallest_transaction=round(smallest_tx, 2),
            unique_counterparties=len(counterparties),
            transaction_frequency=frequency,
            first_transaction=first_tx,
            latest_transaction=latest_tx,
        )

    def calculate_counterparty_summaries(
        self, target_accounts: List[str], records: List[FinancialRecord], limit: int = 20
    ) -> List[CounterpartySummary]:
        """
        Group interactions by counterparty and compute totals, averages, incoming/outgoing flow.
        """
        if not records or not target_accounts:
            return []

        target_set = set(target_accounts)
        cp_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "count": 0,
            "total_amount": 0.0,
            "incoming_amount": 0.0,
            "outgoing_amount": 0.0,
            "timestamps": [],
        })

        for r in records:
            is_sender = r.sender in target_set
            is_receiver = r.receiver in target_set

            if is_sender and not is_receiver:
                cp = r.receiver
                cp_map[cp]["count"] += 1
                cp_map[cp]["total_amount"] += r.amount
                cp_map[cp]["outgoing_amount"] += r.amount
                cp_map[cp]["timestamps"].append(r.timestamp)
            elif is_receiver and not is_sender:
                cp = r.sender
                cp_map[cp]["count"] += 1
                cp_map[cp]["total_amount"] += r.amount
                cp_map[cp]["incoming_amount"] += r.amount
                cp_map[cp]["timestamps"].append(r.timestamp)
            elif is_sender and is_receiver:
                # Self transfer
                cp = r.receiver
                cp_map[cp]["count"] += 1
                cp_map[cp]["total_amount"] += r.amount
                cp_map[cp]["timestamps"].append(r.timestamp)

        summaries = []
        for cp_id, data in cp_map.items():
            if not cp_id or cp_id in target_set:
                continue

            node = self.graph.get_node(cp_id)
            cp_name = node.name if node else cp_id
            cp_type = node.label.value if (node and hasattr(node.label, "value")) else (str(node.label) if node else "BankAccount")

            c_count = data["count"]
            tot = data["total_amount"]
            avg_amt = tot / c_count if c_count > 0 else 0.0

            ts_list = sorted(data["timestamps"])
            first_int = ts_list[0].isoformat() if ts_list else None
            latest_int = ts_list[-1].isoformat() if ts_list else None

            summaries.append(CounterpartySummary(
                counterparty_id=cp_id,
                counterparty_name=cp_name,
                counterparty_type=cp_type,
                transaction_count=c_count,
                total_amount=round(tot, 2),
                average_amount=round(avg_amt, 2),
                incoming_amount=round(data["incoming_amount"], 2),
                outgoing_amount=round(data["outgoing_amount"], 2),
                first_interaction=first_int,
                latest_interaction=latest_int,
            ))

        # Sort primarily by total_amount descending, then count
        summaries.sort(key=lambda s: (s.total_amount, s.transaction_count), reverse=True)
        return summaries[:limit]

    def calculate_flow_summary(
        self, target_accounts: List[str], records: List[FinancialRecord], top_k: int = 5
    ) -> FinancialFlowSummary:
        """
        Money-flow summary: exact totals, top sources, and top destinations.
        """
        if not records or not target_accounts:
            return FinancialFlowSummary()

        target_set = set(target_accounts)
        sources_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"amount": 0.0, "count": 0})
        destinations_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"amount": 0.0, "count": 0})

        in_count = 0
        in_total = 0.0
        out_count = 0
        out_total = 0.0

        for r in records:
            is_sender = r.sender in target_set
            is_receiver = r.receiver in target_set

            if is_receiver and not is_sender:
                in_count += 1
                in_total += r.amount
                sources_map[r.sender]["amount"] += r.amount
                sources_map[r.sender]["count"] += 1
            if is_sender and not is_receiver:
                out_count += 1
                out_total += r.amount
                destinations_map[r.receiver]["amount"] += r.amount
                destinations_map[r.receiver]["count"] += 1

        top_sources = []
        for s_id, s_data in sorted(sources_map.items(), key=lambda x: x[1]["amount"], reverse=True)[:top_k]:
            n = self.graph.get_node(s_id)
            name = n.name if n else s_id
            top_sources.append(FlowBreakdownItem(
                counterparty_id=s_id,
                counterparty_name=name,
                amount=round(s_data["amount"], 2),
                transaction_count=s_data["count"],
            ))

        top_destinations = []
        for d_id, d_data in sorted(destinations_map.items(), key=lambda x: x[1]["amount"], reverse=True)[:top_k]:
            n = self.graph.get_node(d_id)
            name = n.name if n else d_id
            top_destinations.append(FlowBreakdownItem(
                counterparty_id=d_id,
                counterparty_name=name,
                amount=round(d_data["amount"], 2),
                transaction_count=d_data["count"],
            ))

        return FinancialFlowSummary(
            incoming_count=in_count,
            incoming_total=round(in_total, 2),
            top_sources=top_sources,
            outgoing_count=out_count,
            outgoing_total=round(out_total, 2),
            top_destinations=top_destinations,
            net_flow=round(in_total - out_total, 2),
        )

    # -------------------------------------------------------------------------
    # Velocity / Burst Detection
    # -------------------------------------------------------------------------

    def detect_transaction_bursts(
        self,
        records: List[FinancialRecord],
        window_minutes: int = 120,
        min_transactions: int = 3,
    ) -> List[FinancialBurst]:
        """
        Sliding-window burst detection: identifies rapid clusters of transactions within a short time window.
        """
        if not records or len(records) < min_transactions:
            return []

        # Sort chronological ascending
        chrono = sorted(records, key=lambda r: r.timestamp)
        window_delta = timedelta(minutes=window_minutes)
        bursts: List[FinancialBurst] = []
        seen_ranges: List[Tuple[datetime, datetime]] = []

        n = len(chrono)
        for i in range(n):
            window_end = chrono[i].timestamp + window_delta
            window_recs = [r for r in chrono[i:] if r.timestamp <= window_end]

            if len(window_recs) >= min_transactions:
                start_dt = window_recs[0].timestamp
                end_dt = window_recs[-1].timestamp

                # Avoid duplicate / heavily overlapping reporting
                is_overlap = any(
                    abs((start_dt - s).total_seconds()) < 600 and abs((end_dt - e).total_seconds()) < 600
                    for s, e in seen_ranges
                )
                if is_overlap:
                    continue

                seen_ranges.append((start_dt, end_dt))
                count = len(window_recs)
                total_amt = sum(r.amount for r in window_recs)
                avg_amt = total_amt / count
                cps = {r.sender for r in window_recs} | {r.receiver for r in window_recs}

                # Calculate duration in minutes
                span_minutes = max(1, int((end_dt - start_dt).total_seconds() / 60))

                severity = "HIGH" if count >= 6 or total_amt >= 1000000 else "ELEVATED"

                bursts.append(FinancialBurst(
                    start_time=start_dt.isoformat(),
                    end_time=end_dt.isoformat(),
                    transaction_count=count,
                    total_amount=round(total_amt, 2),
                    unique_counterparties=len(cps),
                    average_amount=round(avg_amt, 2),
                    severity=severity,
                    indicator="Rapid Transaction Sequence",
                    description=f"Rapid transaction activity: {count} transactions (₹{total_amt:,.2f}) within {span_minutes} minutes.",
                ))

        return bursts[:10]

    # -------------------------------------------------------------------------
    # Explainable Financial Anomaly Indicators
    # -------------------------------------------------------------------------

    def evaluate_financial_indicators(
        self,
        target_accounts: List[str],
        records: List[FinancialRecord],
        bursts: List[FinancialBurst],
        high_value_threshold: float = DEFAULT_HIGH_VALUE_THRESHOLD,
    ) -> List[FinancialIndicator]:
        """
        Evaluate objective, explainable indicators using neutral investigator decision-support language.
        Never declares a transaction to be criminal.
        """
        indicators: List[FinancialIndicator] = []
        if not records:
            return indicators

        target_set = set(target_accounts)
        amounts = [r.amount for r in records]
        mean_amt = float(np.mean(amounts)) if amounts else 0.0
        std_amt = float(np.std(amounts)) if len(amounts) > 1 else 0.0

        # 1. High-Value Transaction Indicator
        high_value_txs = [r for r in records if r.amount >= high_value_threshold]
        if high_value_txs:
            max_tx = max(high_value_txs, key=lambda r: r.amount)
            indicators.append(FinancialIndicator(
                name="High-Value Transaction",
                severity="HIGH",
                explanation=f"Transaction amount ₹{max_tx.amount:,.2f} exceeds configured investigation threshold ₹{high_value_threshold:,.2f}.",
                supporting_values={
                    "threshold_used": high_value_threshold,
                    "observed_amount": max_tx.amount,
                    "transaction_id": max_tx.transaction_id,
                    "currency": max_tx.currency,
                    "sender": max_tx.sender,
                    "receiver": max_tx.receiver,
                },
                timestamp=max_tx.timestamp.isoformat(),
            ))

        # 2. Rapid Transaction Sequence (Burst)
        if bursts:
            top_burst = max(bursts, key=lambda b: b.transaction_count)
            indicators.append(FinancialIndicator(
                name="Rapid Transaction Sequence",
                severity=top_burst.severity,
                explanation=f"{top_burst.description} Requires investigator verification.",
                supporting_values={
                    "transaction_count": top_burst.transaction_count,
                    "total_amount": top_burst.total_amount,
                    "start_time": top_burst.start_time,
                    "end_time": top_burst.end_time,
                },
                time_window=f"{top_burst.start_time} - {top_burst.end_time}",
            ))

        # 3. High Transaction Frequency
        if len(records) >= 8:
            indicators.append(FinancialIndicator(
                name="High Transaction Activity",
                severity="ELEVATED",
                explanation=f"Elevated interaction volume: {len(records)} transactions logged across active monitoring window.",
                supporting_values={"total_transactions": len(records)},
            ))

        # 4. Repeated Counterparty Transfers
        cp_counts = defaultdict(int)
        cp_amounts = defaultdict(float)
        for r in records:
            cp = r.receiver if r.sender in target_set else r.sender
            cp_counts[cp] += 1
            cp_amounts[cp] += r.amount

        repeated_cps = [cp for cp, count in cp_counts.items() if count >= 3]
        if repeated_cps:
            top_cp = max(repeated_cps, key=lambda c: cp_counts[c])
            cp_node = self.graph.get_node(top_cp)
            cp_display = cp_node.name if cp_node else top_cp
            indicators.append(FinancialIndicator(
                name="Repeated Counterparty Interaction",
                severity="NOTICE",
                explanation=f"Repeated transactions ({cp_counts[top_cp]} transfers, total ₹{cp_amounts[top_cp]:,.2f}) with counterparty '{cp_display}'.",
                supporting_values={
                    "counterparty": top_cp,
                    "counterparty_name": cp_display,
                    "transfer_count": cp_counts[top_cp],
                    "total_amount": round(cp_amounts[top_cp], 2),
                },
            ))

        # 5. Multiple Counterparties in Short Period (Fan-out / Fan-in)
        if len(records) >= 4:
            recent_cps = set()
            recent_recs = records[:5]
            for r in recent_recs:
                recent_cps.add(r.receiver if r.sender in target_set else r.sender)
            if len(recent_cps) >= 3:
                indicators.append(FinancialIndicator(
                    name="Multiple Counterparties Pattern",
                    severity="NOTICE",
                    explanation=f"Transfers involving {len(recent_cps)} distinct counterparties recorded in quick succession.",
                    supporting_values={"unique_counterparties_count": len(recent_cps)},
                ))

        # 6. Unusual Transaction Amount (Statistical Outlier Baseline)
        if std_amt > 1e-4 and len(amounts) >= 3:
            z_thresh = mean_amt + (2.0 * std_amt)
            outliers = [r for r in records if r.amount > z_thresh]
            if outliers:
                top_outlier = max(outliers, key=lambda r: r.amount)
                z_score = (top_outlier.amount - mean_amt) / std_amt
                indicators.append(FinancialIndicator(
                    name="Unusual Transaction Pattern",
                    severity="ELEVATED",
                    explanation=f"Transaction of ₹{top_outlier.amount:,.2f} deviates {z_score:.1f}σ from entity baseline average (₹{mean_amt:,.2f}). Requires investigator verification.",
                    supporting_values={
                        "observed_amount": top_outlier.amount,
                        "baseline_mean": round(mean_amt, 2),
                        "baseline_std": round(std_amt, 2),
                        "z_score": round(z_score, 2),
                    },
                    timestamp=top_outlier.timestamp.isoformat(),
                ))

        # 7. High Inflow/Outflow Imbalance
        out_tot = sum(r.amount for r in records if r.sender in target_set)
        in_tot = sum(r.amount for r in records if r.receiver in target_set)
        if (out_tot > 500000 or in_tot > 500000) and (out_tot > 0 and in_tot > 0):
            ratio = max(out_tot, in_tot) / max(1.0, min(out_tot, in_tot))
            if ratio >= 5.0:
                direction = "outgoing" if out_tot > in_tot else "incoming"
                indicators.append(FinancialIndicator(
                    name="Financial Flow Disparity",
                    severity="NOTICE",
                    explanation=f"Significant {direction} flow disparity observed ({ratio:.1f}x flow imbalance: incoming ₹{in_tot:,.2f} vs outgoing ₹{out_tot:,.2f}).",
                    supporting_values={
                        "incoming_total": in_tot,
                        "outgoing_total": out_tot,
                        "flow_ratio": round(ratio, 2),
                    },
                ))

        return indicators

    # -------------------------------------------------------------------------
    # Comprehensive Entity Financial Analysis
    # -------------------------------------------------------------------------

    def analyze_entity_finances(
        self,
        entity_id: str,
        high_value_threshold: float = DEFAULT_HIGH_VALUE_THRESHOLD,
        burst_window_minutes: int = 120,
        timeline_limit: int = 50,
    ) -> FinancialAnalysisResult:
        """
        Comprehensive financial analysis for a Person or BankAccount entity.
        Integrates statistics, top counterparties, flow breakdown, indicators, velocity bursts,
        chronological transaction table preview, and live graph centrality.
        """
        node, accounts = self.resolve_entity_accounts(entity_id)

        # Entity metadata
        if node:
            ent_type = node.label.value if hasattr(node.label, "value") else str(node.label)
            ent_name = node.name or entity_id
        else:
            ent_type = "BankAccount" if "ACC" in entity_id.upper() else "Unknown"
            ent_name = entity_id

        # Query all records
        records = self.get_all_records_for_accounts(accounts)

        # 1. Statistics
        stats = self.calculate_transaction_statistics(accounts, records)

        # 2. Counterparties
        top_cps = self.calculate_counterparty_summaries(accounts, records, limit=20)

        # 3. Bursts
        bursts = self.detect_transaction_bursts(records, window_minutes=burst_window_minutes)

        # 4. Indicators
        indicators = self.evaluate_financial_indicators(
            accounts, records, bursts, high_value_threshold=high_value_threshold
        )

        # 5. Flow summary
        flow = self.calculate_flow_summary(accounts, records, top_k=5)

        # 6. Graph centrality metrics
        centrality_info = {}
        try:
            metrics = self.analytics.compute_centrality_metrics()
            node_id_to_check = node.id if node else entity_id
            betweenness = metrics["betweenness"].get(node_id_to_check, 0.0)
            degree = metrics["degree"].get(node_id_to_check, 0.0)
            pagerank = metrics["pagerank"].get(node_id_to_check, 0.0)
            centrality_info = {
                "betweenness_centrality": round(betweenness, 4),
                "degree_centrality": round(degree, 4),
                "pagerank": round(pagerank, 4),
            }
        except Exception as e:
            logger.warning(f"Failed to compute centrality for {entity_id}: {e}")

        # 7. Timeline preview
        preview_items = []
        for r in records[:timeline_limit]:
            s_node = self.graph.get_node(r.sender)
            r_node = self.graph.get_node(r.receiver)
            s_name = s_node.name if s_node else r.sender
            r_name = r_node.name if r_node else r.receiver

            preview_items.append(TransactionItem(
                transaction_id=r.transaction_id,
                timestamp=r.timestamp.isoformat(),
                sender=r.sender,
                sender_name=s_name,
                receiver=r.receiver,
                receiver_name=r_name,
                amount=r.amount,
                currency=r.currency,
                transaction_type=r.transaction_type.value,
                account_id=r.account_id,
                case_id=r.case_id,
                location=r.location,
                description=r.description,
            ))

        return FinancialAnalysisResult(
            entity_id=node.id if node else entity_id,
            entity_type=ent_type,
            entity_name=ent_name,
            accounts=accounts,
            statistics=stats,
            top_counterparties=top_cps,
            indicators=indicators,
            bursts=bursts,
            flow=flow,
            graph_centrality=centrality_info,
            transactions_preview=preview_items,
        )


_financial_service_instance: Optional[FinancialService] = None


def get_financial_service() -> FinancialService:
    global _financial_service_instance
    if _financial_service_instance is None:
        _financial_service_instance = FinancialService()
    return _financial_service_instance

