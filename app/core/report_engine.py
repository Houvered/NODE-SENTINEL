# -*- coding: utf-8 -*-
"""
NODE SENTINEL - Automated Case Report Engine (STEP 14)
Aggregates verified telemetry across Universal Search, Knowledge Graph, CDR Analytics,
Financial Ledger, Timeline, and Risk Intelligence to produce publication-grade
investigation reports in PDF, HTML, and JSON formats.
"""
from __future__ import annotations

import base64
import io
import math
import re
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import pymupdf  # PyMuPDF for native vector PDF generation
from PIL import Image, ImageDraw, ImageFont

from app.core.anomaly_detector import AnomalyDetector
from app.core.cdr_analytics import CDRService
from app.core.financial_analytics import FinancialService
from app.core.graph_analytics import GraphAnalytics
from app.core.graph_engine import BaseGraphEngine, get_graph_engine
from app.core.timeline_engine import TimelineEngine, get_timeline_engine
from app.core.universal_search import UniversalSearchEngine, get_universal_search
from app.models.graph_models import EdgeType, NodeType
from app.models.report_models import (
    AssociatedEntityItem,
    CDRAnalysisSummary,
    ConnectedCaseItem,
    EvidenceReferenceItem,
    FinancialAnalysisSummary,
    GraphSummary,
    InvestigationReportData,
    InvestigationReportRequest,
    InvestigationReportResponse,
    ReportFormat,
    ReportMetadata,
    ReportSection,
    RiskAssessmentSummary,
    SubjectProfile,
    TimelineEventItem,
)

logger = logging.getLogger(__name__)


def _format_curr(val: float) -> str:
    """Format currency in Indian Rupee notation."""
    return f"₹{val:,.2f}"


class ReportEngine:
    """
    Modular report engine strictly compiling verified data from the existing singletons.
    Does not build a secondary database or graph.
    """

    def __init__(
        self,
        graph_engine: Optional[BaseGraphEngine] = None,
        search_engine: Optional[UniversalSearchEngine] = None,
        timeline_engine: Optional[TimelineEngine] = None,
        anomaly_detector: Optional[AnomalyDetector] = None,
        cdr_service: Optional[CDRService] = None,
        financial_service: Optional[FinancialService] = None,
        assistant_engine: Optional[Any] = None,
    ) -> None:
        self.graph = graph_engine or get_graph_engine()
        self.search = search_engine or get_universal_search()
        self.timeline_engine = timeline_engine or get_timeline_engine()
        self.anomaly_detector = anomaly_detector or AnomalyDetector(self.graph)
        self.cdr_service = cdr_service or CDRService(self.graph)
        self.financial_service = financial_service or FinancialService(self.graph)
        self.graph_analytics = GraphAnalytics(self.graph)

        # Lazy load assistant engine if available
        self._assistant_engine = assistant_engine

        # In-memory storage for generated reports: report_id -> dict
        self._reports_cache: Dict[str, Dict[str, Any]] = {}
        self._ensure_seed_data()

    def _ensure_seed_data(self) -> None:
        """Auto-seed sample data if graph engine is currently empty."""
        try:
            if len(self.graph.get_all_nodes()) == 0:
                from app.api.routes_ingest import load_dataset_by_name
                load_dataset_by_name(self.graph, "syndicate_network.json")
        except Exception as e:
            logger.debug(f"Could not auto-seed report engine graph data: {e}")

    @property
    def assistant_engine(self) -> Any:
        if self._assistant_engine is None:
            try:
                from app.core.assistant_engine import get_assistant_engine
                self._assistant_engine = get_assistant_engine()
            except Exception as e:
                logger.debug(f"AssistantEngine not loaded: {e}")
                self._assistant_engine = None
        return self._assistant_engine

    # -------------------------------------------------------------------------
    # Target Resolution
    # -------------------------------------------------------------------------

    def resolve_target(self, entity_id: Optional[str], case_id: Optional[str]) -> Tuple[str, str, str, Dict[str, Any]]:
        """
        Resolves the primary subject (entity or case).
        Returns (target_id, target_name, target_type, target_properties).
        """
        if entity_id and entity_id.strip():
            clean_id = entity_id.strip()
            node = self.graph.get_node(clean_id)
            if not node:
                # Try finding by name or alias
                for n in self.graph.get_all_nodes():
                    if n.name.lower() == clean_id.lower() or n.id.lower() == clean_id.lower():
                        node = n
                        break
            if not node:
                # Search via universal search
                search_res = self.search.search(clean_id, limit=5)
                for item in getattr(search_res, "results", []):
                    candidate = self.graph.get_node(item.entity_id)
                    if candidate:
                        node = candidate
                        break
            if not node:
                raise ValueError(f"Target entity '{clean_id}' was not found in the investigation dataset.")
            
            lbl = node.label.value if hasattr(node.label, "value") else str(node.label)
            return node.id, node.name, lbl, node.properties

        elif case_id and case_id.strip():
            clean_case = case_id.strip()
            node = self.graph.get_node(clean_case)
            if not node:
                for n in self.graph.get_all_nodes():
                    if n.id.lower() == clean_case.lower() or n.name.lower() == clean_case.lower():
                        node = n
                        break
            if node:
                return node.id, node.name, "Case", node.properties
            return clean_case, f"Case {clean_case}", "Case", {"case_id": clean_case}

        else:
            raise ValueError("Either entity_id or case_id must be provided to generate an investigation report.")

    # -------------------------------------------------------------------------
    # Network Graph Visualization (PIL)
    # -------------------------------------------------------------------------

    def render_ego_network_image(
        self,
        center_id: str,
        center_name: str,
        center_type: str,
        neighbors: List[AssociatedEntityItem],
    ) -> bytes:
        """
        Dynamically renders an ego-network sub-graph topology diagram using PIL.
        Returns PNG bytes.
        """
        width, height = 800, 420
        bg_color = (15, 23, 42)      # #0f172a slate-900
        grid_color = (30, 41, 59)    # #1e293b slate-800
        text_color = (248, 250, 252) # #f8fafc slate-50
        dim_text = (148, 163, 184)   # #94a3b8 slate-400
        edge_color = (71, 85, 105)   # #475569 slate-600

        img = Image.new("RGB", (width, height), color=bg_color)
        draw = ImageDraw.Draw(img)

        # Subtle background grid
        for x in range(0, width, 40):
            draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
        for y in range(0, height, 40):
            draw.line([(0, y), (width, y)], fill=grid_color, width=1)

        # Header banner text
        draw.text((20, 15), "NODE SENTINEL — EGO NETWORK TOPOLOGY", fill=(239, 68, 68), anchor="lt")
        draw.text((20, 32), f"Target Subject: {center_name} ({center_id}) | Direct Degree: {len(neighbors)}", fill=dim_text, anchor="lt")

        center_x, center_y = 400, 220
        max_display = min(len(neighbors), 14)
        active_neighbors = neighbors[:max_display]

        # Calculate neighbor node positions on an ellipse
        rx, ry = 280, 130
        coords = []
        for i, neighbor in enumerate(active_neighbors):
            angle = (2 * math.pi * i) / max(1, len(active_neighbors)) - (math.pi / 2)
            nx = int(center_x + rx * math.cos(angle))
            ny = int(center_y + ry * math.sin(angle))
            coords.append((nx, ny, neighbor))

        # Draw edge links
        for nx, ny, nb in coords:
            # Highlight edge if high weight or co-accused
            c = (220, 38, 38) if "CO_ACCUSED" in nb.relationship.upper() else edge_color
            draw.line([(center_x, center_y), (nx, ny)], fill=c, width=2)
            
            # Midpoint label
            mx = (center_x + nx) // 2
            my = (center_y + ny) // 2
            rel_label = nb.relationship[:12]
            draw.text((mx, my), rel_label, fill=(100, 116, 139), anchor="mm")

        # Color mapping for node types
        type_colors = {
            "PERSON": (2, 132, 199),       # Cyan/Blue
            "VEHICLE": (217, 119, 6),      # Amber
            "ACCOUNT": (5, 150, 105),      # Emerald
            "BANK_ACCOUNT": (5, 150, 105), # Emerald
            "CASE": (147, 51, 234),        # Purple
            "CRIME": (147, 51, 234),       # Purple
            "LOCATION": (100, 116, 139),   # Slate
            "PHONE": (16, 185, 129),       # Green
        }

        # Draw neighbor nodes
        for nx, ny, nb in coords:
            ntype = nb.node_type.upper()
            fill_c = type_colors.get(ntype, (59, 130, 246))
            r = 14
            draw.ellipse([nx - r, ny - r, nx + r, ny + r], fill=fill_c, outline=(255, 255, 255), width=1)
            # Label
            short_lbl = nb.label[:14]
            draw.text((nx, ny + r + 3), short_lbl, fill=text_color, anchor="mt")

        # Draw Center Node
        cr = 24
        draw.ellipse([center_x - cr, center_y - cr, center_x + cr, center_y + cr], fill=(220, 38, 38), outline=(255, 255, 255), width=3)
        draw.text((center_x, center_y), "★", fill=(255, 255, 255), anchor="mm")
        draw.text((center_x, center_y + cr + 5), center_name[:20], fill=(254, 240, 138), anchor="mt")

        # Legend at bottom
        legend_y = height - 20
        legend_items = [
            ("Subject", (220, 38, 38)),
            ("Person", (2, 132, 199)),
            ("Account", (5, 150, 105)),
            ("Vehicle", (217, 119, 6)),
            ("Case", (147, 51, 234)),
        ]
        start_x = 100
        for name, col in legend_items:
            draw.rectangle([start_x, legend_y - 5, start_x + 10, legend_y + 5], fill=col)
            draw.text((start_x + 14, legend_y), name, fill=dim_text, anchor="lm")
            start_x += 120

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # -------------------------------------------------------------------------
    # Core Aggregator: generate_report_data
    # -------------------------------------------------------------------------

    def generate_report_data(self, request: InvestigationReportRequest) -> InvestigationReportData:
        """
        Collects, correlates, and structures all 13 sections strictly from existing services.
        """
        target_id, target_name, target_type, target_props = self.resolve_target(
            request.entity_id, request.case_id
        )

        rep_id = f"REP-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        gen_time = datetime.now(timezone.utc).isoformat()

        # Normalize selected sections
        all_section_keys = {s.value for s in ReportSection}
        if request.sections:
            selected_sections = set(s.strip().lower() for s in request.sections if s.strip())
        else:
            selected_sections = all_section_keys

        # Section 1: Metadata
        metadata = ReportMetadata(
            report_id=rep_id,
            generated_at=gen_time,
            target_type=target_type.lower(),
            target_id=target_id,
            target_name=target_name,
            date_from=request.date_from,
            date_to=request.date_to,
            sections_included=list(selected_sections),
        )

        evidence_references: List[EvidenceReferenceItem] = []

        # Section 3: Subject Profile
        subject_profile = None
        if ReportSection.SUBJECT_PROFILE.value in selected_sections:
            # Extract demographics & attributes
            aliases = target_props.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            phone = target_props.get("phone") or target_props.get("phone_number")
            phones = [phone] if phone else []
            email = target_props.get("email")
            emails = [email] if email else []
            address = target_props.get("address")
            addresses = [address] if address else []
            acc = target_props.get("bank_account") or target_props.get("account_number")
            accs = [acc] if acc else []
            plate = target_props.get("vehicle_plate") or target_props.get("plate_number")
            plates = [plate] if plate else []

            # Check neighbors for connected phone/account/vehicle nodes
            for edge in self.graph.get_all_edges():
                if edge.source == target_id or edge.target == target_id:
                    other_id = edge.target if edge.source == target_id else edge.source
                    other_node = self.graph.get_node(other_id)
                    if other_node:
                        lbl = (other_node.label.value if hasattr(other_node.label, "value") else str(other_node.label)).upper()
                        if lbl == "PHONE" and other_node.name not in phones:
                            phones.append(other_node.name)
                        elif lbl in ("ACCOUNT", "BANK_ACCOUNT") and other_node.name not in accs:
                            accs.append(other_node.name)
                        elif lbl == "VEHICLE" and other_node.name not in plates:
                            plates.append(other_node.name)

            risk_val = float(target_props.get("risk_score") or 0.0)
            level = "HIGH" if risk_val >= 70 else ("ELEVATED" if risk_val >= 40 else "LOW")

            subject_profile = SubjectProfile(
                entity_id=target_id,
                name=target_name,
                node_type=target_type,
                risk_score=risk_val,
                risk_level=level,
                aliases=aliases,
                phone_numbers=phones,
                email_addresses=emails,
                registered_addresses=addresses,
                national_id=target_props.get("national_id") or target_props.get("aadhaar"),
                vehicle_plates=plates,
                bank_accounts=accs,
                status=target_props.get("status") or "Active Investigation",
                tags=target_props.get("tags") or [],
                attributes={k: v for k, v in target_props.items() if k not in ("aliases", "tags")},
            )

        # Section 4: Associated Entities
        associated_entities: List[AssociatedEntityItem] = []
        neighbor_nodes = []
        if ReportSection.ASSOCIATED_ENTITIES.value in selected_sections or ReportSection.GRAPH_SUMMARY.value in selected_sections:
            try:
                nb_data = self.graph.get_neighbors(target_id, depth=1)
                neighbor_nodes = [n for n in nb_data.get("nodes", []) if n.id != target_id]
                edge_map = {}
                for e in nb_data.get("edges", []):
                    edge_map[(e.source, e.target)] = e
                    edge_map[(e.target, e.source)] = e
            except Exception:
                neighbor_nodes = []
                edge_map = {}

            for n in neighbor_nodes:
                edge = edge_map.get((target_id, n.id))
                rel_name = "CONNECTED"
                weight = 1.0
                freq = None
                amt = None
                if edge:
                    rel_name = edge.relationship.value if hasattr(edge.relationship, "value") else str(edge.relationship)
                    weight = float(edge.properties.get("weight", 1.0))
                    freq = edge.properties.get("frequency") or edge.properties.get("call_count")
                    amt = edge.properties.get("amount") or edge.properties.get("total_amount")

                nlbl = n.label.value if hasattr(n.label, "value") else str(n.label)
                associated_entities.append(AssociatedEntityItem(
                    node_id=n.id,
                    label=n.name,
                    node_type=nlbl,
                    relationship=rel_name,
                    weight=weight,
                    frequency=freq,
                    amount=float(amt) if amt is not None else None,
                    shared_cases=n.properties.get("cases") or [],
                ))

        # Section 5: Knowledge Graph Summary
        graph_summary = None
        graph_png_bytes = None
        if ReportSection.GRAPH_SUMMARY.value in selected_sections:
            all_nodes = self.graph.get_all_nodes()
            all_edges = self.graph.get_all_edges()
            try:
                centrality = self.graph_analytics.compute_centrality_metrics()
            except Exception:
                centrality = {}

            deg_cent = centrality.get("degree", {}).get(target_id, 0.0)
            btw_cent = centrality.get("betweenness", {}).get(target_id, 0.0)

            # Generate visual diagram
            graph_png_bytes = self.render_ego_network_image(
                center_id=target_id,
                center_name=target_name,
                center_type=target_type,
                neighbors=associated_entities,
            )
            b64_img = base64.b64encode(graph_png_bytes).decode("utf-8")

            n_count = len(all_nodes)
            e_count = len(all_edges)
            density = (2.0 * e_count) / (n_count * (n_count - 1)) if n_count > 1 else 0.0

            graph_summary = GraphSummary(
                node_count=n_count,
                edge_count=e_count,
                density=round(density, 4),
                degree_centrality=round(deg_cent, 4),
                betweenness_centrality=round(btw_cent, 4),
                community_id=target_props.get("community_id") or "Cluster-Alpha",
                ego_nodes_count=len(associated_entities) + 1,
                ego_edges_count=len(associated_entities),
                graph_image_base64=b64_img,
            )

        # Section 6: CDR / Communication Analysis
        cdr_summary = None
        # Section 6: CDR / Communication Analysis
        cdr_summary = None
        if ReportSection.CDR_ANALYSIS.value in selected_sections:
            try:
                cdr_res = self.cdr_service.analyze_entity_cdr(target_id)
                stats = cdr_res.statistics
                
                # Top contacts formatting
                top_c = []
                for c in getattr(cdr_res, "top_contacts", []):
                    top_c.append({
                        "contact": c.contact_name or c.contact_id,
                        "call_count": c.call_count,
                        "duration_sec": getattr(c, "total_duration_seconds", 0),
                        "in_count": getattr(c, "incoming_count", 0),
                        "out_count": getattr(c, "outgoing_count", 0),
                    })

                bursts = []
                for b in getattr(cdr_res, "bursts", []):
                    w_start = getattr(b, "start_time", getattr(b, "window_start", ""))
                    w_end = getattr(b, "end_time", getattr(b, "window_end", ""))
                    bursts.append({
                        "window_start": str(w_start),
                        "window_end": str(w_end),
                        "call_count": getattr(b, "call_count", 0),
                        "severity": getattr(b, "severity", "MEDIUM"),
                    })
                    # Add evidence item for burst
                    evidence_references.append(EvidenceReferenceItem(
                        source="demo_cdr.csv",
                        entity_id=target_id,
                        timestamp=str(w_start),
                        record_id=f"BURST-{target_id[:6]}",
                        description=f"Telecommunications communication burst: {getattr(b, 'call_count', 0)} calls ({getattr(b, 'severity', 'BURST')}).",
                    ))

                phone_target = subject_profile.phone_numbers[0] if (subject_profile and subject_profile.phone_numbers) else target_id

                dur_formatted = getattr(stats, "formatted_total_duration", getattr(stats, "total_duration_formatted", "0s"))

                cdr_summary = CDRAnalysisSummary(
                    target_phone=phone_target,
                    total_calls=stats.total_calls,
                    total_duration_sec=stats.total_duration_seconds,
                    formatted_duration=dur_formatted,
                    incoming_count=stats.incoming_calls,
                    outgoing_count=stats.outgoing_calls,
                    top_contacts=top_c,
                    burst_alerts=bursts,
                    off_hours_calls_count=getattr(stats, "off_hours_calls", 0),
                    source="demo_cdr.csv",
                )

                # Add top call evidence item
                if stats.total_calls > 0:
                    evidence_references.append(EvidenceReferenceItem(
                        source="demo_cdr.csv",
                        entity_id=target_id,
                        timestamp=gen_time,
                        record_id=f"CDR-AGG-{target_id[:6]}",
                        description=f"Aggregated CDR profile: {stats.total_calls} calls logged, {stats.unique_contacts} distinct contacts.",
                    ))

            except Exception as e:
                logger.warning(f"Error compiling CDR analysis for {target_id}: {e}")
                cdr_summary = CDRAnalysisSummary(
                    target_phone=target_id,
                    total_calls=0,
                    source="demo_cdr.csv",
                )

        # Section 7: Financial Analysis
        financial_summary = None
        if ReportSection.FINANCIAL_ANALYSIS.value in selected_sections:
            try:
                fin_res = self.financial_service.analyze_entity_finances(target_id)
                stats = fin_res.statistics
                flow = fin_res.flow

                top_cp = []
                for cp in getattr(fin_res, "top_counterparties", []):
                    in_amt = getattr(cp, "incoming_amount", 0.0)
                    out_amt = getattr(cp, "outgoing_amount", 0.0)
                    flow_dir = "INCOMING" if in_amt >= out_amt else "OUTGOING"
                    top_cp.append({
                        "counterparty": cp.counterparty_name or cp.counterparty_id,
                        "transaction_count": cp.transaction_count,
                        "volume": cp.total_amount,
                        "direction": flow_dir,
                    })

                hi_val = []
                for h in getattr(fin_res, "high_value_transactions", []):
                    t_str = h.timestamp.isoformat() if hasattr(h.timestamp, "isoformat") else str(h.timestamp)
                    hi_val.append({
                        "txn_id": h.transaction_id,
                        "amount": h.amount,
                        "timestamp": t_str,
                        "sender": h.sender_name or h.sender_account,
                        "receiver": h.receiver_name or h.receiver_account,
                    })
                    evidence_references.append(EvidenceReferenceItem(
                        source="demo_financial.csv",
                        entity_id=target_id,
                        timestamp=t_str,
                        record_id=h.transaction_id,
                        description=f"High-value financial transfer: ₹{h.amount:,.2f} from {h.sender_account} to {h.receiver_account}.",
                    ))

                vel_alerts = []
                for ind in getattr(fin_res, "indicators", []):
                    vel_alerts.append({
                        "title": getattr(ind, "name", getattr(ind, "title", "Anomaly")),
                        "severity": ind.severity,
                        "description": getattr(ind, "explanation", getattr(ind, "description", "")),
                    })
                    evidence_references.append(EvidenceReferenceItem(
                        source="demo_financial.csv",
                        entity_id=target_id,
                        timestamp=gen_time,
                        record_id=f"IND-{getattr(ind, 'code', 'FIN')}",
                        description=f"Financial telemetry indicator [{ind.severity}]: {getattr(ind, 'name', 'Alert')}",
                    ))

                financial_summary = FinancialAnalysisSummary(
                    target_account=fin_res.accounts[0] if fin_res.accounts else target_id,
                    total_transactions=stats.total_transactions,
                    total_inflow=flow.incoming_total,
                    total_outflow=flow.outgoing_total,
                    net_flow=flow.net_flow,
                    top_counterparties=top_cp,
                    high_value_transactions=hi_val,
                    velocity_alerts=vel_alerts,
                    source="demo_financial.csv",
                )

            except Exception as e:
                logger.warning(f"Error compiling financial analysis for {target_id}: {e}")
                financial_summary = FinancialAnalysisSummary(
                    target_account=target_id,
                    total_transactions=0,
                    source="demo_financial.csv",
                )

        # Section 8: Investigation Timeline
        timeline_items: List[TimelineEventItem] = []
        if ReportSection.TIMELINE.value in selected_sections:
            try:
                tl_res = self.timeline_engine.get_entity_timeline(
                    entity_id=target_id,
                    start=request.date_from,
                    end=request.date_to,
                )
                for ev in getattr(tl_res, "events", [])[:25]:
                    t_str = ev.timestamp.isoformat() if hasattr(ev.timestamp, "isoformat") else str(ev.timestamp)
                    rec_id = getattr(ev, "record_id", None) or (ev.metadata.get("record_id") if hasattr(ev, "metadata") and ev.metadata else ev.event_id)
                    c_id = ev.related_case_ids[0] if (hasattr(ev, "related_case_ids") and ev.related_case_ids) else None
                    sev_val = ev.severity.value if hasattr(ev.severity, "value") else str(ev.severity)
                    ev_type_val = ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type)
                    timeline_items.append(TimelineEventItem(
                        event_id=ev.event_id,
                        timestamp=t_str,
                        event_type=ev_type_val,
                        title=ev.title,
                        description=ev.description,
                        severity=sev_val,
                        source=ev.source,
                        record_id=rec_id,
                        case_id=c_id,
                    ))
                    # Add key timeline events to evidence matrix
                    if sev_val.lower() in ("elevated", "high", "critical", "notice") or "FIR" in str(ev.event_id):
                        evidence_references.append(EvidenceReferenceItem(
                            source=ev.source or "timeline_engine",
                            entity_id=target_id,
                            timestamp=t_str,
                            case_id=c_id,
                            record_id=rec_id,
                            description=ev.title,
                        ))
            except Exception as e:
                logger.warning(f"Error compiling timeline for {target_id}: {e}")

        # Section 9: Risk & Anomaly Assessment
        risk_summary = None
        if ReportSection.RISK_ASSESSMENT.value in selected_sections:
            try:
                risk_data = self.anomaly_detector.calculate_investigative_risk_score(target_id)
                factors_list = []
                for f in getattr(risk_data, "factors", []):
                    factors_list.append({
                        "factor_id": f.factor_id,
                        "title": f.title,
                        "score_contribution": f.score_contribution,
                        "severity": f.severity,
                        "evidence": f.evidence,
                        "source": f.source,
                    })
                    evidence_references.append(EvidenceReferenceItem(
                        source=f.source,
                        entity_id=target_id,
                        record_id=f.factor_id,
                        description=f"Investigative Risk Factor (+{f.score_contribution:.0f} pts): {f.title} ({f.evidence})",
                    ))

                r_score = int(round(getattr(risk_data, "risk_score", getattr(risk_data, "score", 0.0))))
                r_level = getattr(risk_data, "risk_level", getattr(risk_data, "rating", "LOW"))
                r_conclusion = getattr(risk_data, "conclusion", "Requires Routine Monitoring")
                r_action = "Review multi-source telemetry and verify flagged indicators."

                risk_summary = RiskAssessmentSummary(
                    score=r_score,
                    rating=r_level,
                    conclusion=r_conclusion,
                    factors=factors_list,
                    indicators=[f.title for f in getattr(risk_data, "factors", [])],
                    evidence=getattr(risk_data, "evidence_summary", []),
                    recommended_action=r_action,
                )
            except Exception as e:
                logger.warning(f"Error compiling risk score for {target_id}: {e}")
                risk_summary = RiskAssessmentSummary(
                    score=0,
                    rating="LOW",
                    conclusion="No anomalies detected",
                )

        # Section 10: Connected Cases
        connected_cases: List[ConnectedCaseItem] = []
        if ReportSection.CONNECTED_CASES.value in selected_sections:
            # Case nodes from graph or property
            case_names = target_props.get("cases") or target_props.get("case_ids") or []
            if isinstance(case_names, str):
                case_names = [case_names]

            seen_cases = set()
            for cn in case_names:
                seen_cases.add(cn)
                connected_cases.append(ConnectedCaseItem(
                    case_id=cn,
                    title=f"Registered Case {cn}",
                    crime_category="Organized Crime Investigation",
                    statutes=["IPC 120B", "PMLA / NDPS"],
                    status="Active Investigation",
                    role="Subject of Interest",
                ))
                evidence_references.append(EvidenceReferenceItem(
                    source="fir_registry",
                    entity_id=target_id,
                    case_id=cn,
                    record_id=cn,
                    description=f"Subject recorded in police registry for case {cn}.",
                ))

            # Also inspect 1st degree neighbor case nodes
            for n in neighbor_nodes:
                lbl = (n.label.value if hasattr(n.label, "value") else str(n.label)).upper()
                if lbl in ("CASE", "CRIME") or n.id.startswith("CASE_") or n.id.startswith("FIR_"):
                    if n.id not in seen_cases:
                        seen_cases.add(n.id)
                        connected_cases.append(ConnectedCaseItem(
                            case_id=n.id,
                            title=n.name,
                            crime_category=n.properties.get("crime_type") or "Criminal Network Case",
                            statutes=n.properties.get("sections") or ["IPC 120B"],
                            status=n.properties.get("status") or "Active Investigation",
                            role="Co-accused / Linked Subject",
                        ))
                        evidence_references.append(EvidenceReferenceItem(
                            source="knowledge_graph",
                            entity_id=target_id,
                            case_id=n.id,
                            record_id=n.id,
                            description=f"Graph adjacency linkage to case {n.name}.",
                        ))

        # Section 12: Investigator Notes
        notes = request.investigator_notes.strip() if request.investigator_notes else None

        # Section 2: Executive Summary (AI grounded or deterministic fallback)
        exec_summary = None
        exec_type = "deterministic"
        if ReportSection.EXECUTIVE_SUMMARY.value in selected_sections:
            ai_attempted = False
            if self.assistant_engine:
                try:
                    from app.models.assistant_models import InvestigationAssistantRequest
                    ai_query = (
                        f"Summarize this investigation for {target_name} ({target_id}). "
                        f"Include key associates, cases, calls, and financial flows based strictly on recorded facts."
                    )
                    ai_req = InvestigationAssistantRequest(
                        query=ai_query,
                        selected_entity_id=target_id,
                    )
                    ai_res = self.assistant_engine.process_query(ai_req)
                    if ai_res and ai_res.answer and "No supporting record" not in ai_res.answer:
                        exec_summary = (
                            f"[AI-Assisted Investigation Summary - Grounded Telemetry]\n\n"
                            f"{ai_res.answer}"
                        )
                        exec_type = "ai_grounded"
                        ai_attempted = True
                except Exception as e:
                    logger.info(f"AI Assistant summary fallback triggered: {e}")

            if not ai_attempted or not exec_summary:
                # Deterministic fallback summary strictly grounded in data
                assoc_cnt = len(associated_entities)
                cases_cnt = len(connected_cases)
                risk_pt = risk_summary.score if risk_summary else 0
                risk_lvl = risk_summary.rating if risk_summary else "LOW"
                fin_vol = financial_summary.total_inflow + financial_summary.total_outflow if financial_summary else 0.0
                call_cnt = cdr_summary.total_calls if cdr_summary else 0
                dur_str = cdr_summary.formatted_duration if cdr_summary else "0s"

                exec_summary = (
                    f"[Deterministic Telemetry Summary]\n\n"
                    f"This automated investigation report correlates recorded telemetry for subject '{target_name}' "
                    f"(ID: {target_id}, Type: {target_type}).\n\n"
                    f"Key Analytical Indicators:\n"
                    f"• Network Connectivity: {assoc_cnt} direct 1st-degree associates across the Knowledge Graph.\n"
                    f"• Criminal Case Linkages: Linked to {cases_cnt} formal police investigation registry entries.\n"
                    f"• Explainable Risk Assessment: Composite score of {risk_pt}/100 ({risk_lvl} priority rating).\n"
                    f"• Telecommunications: {call_cnt} recorded calls totaling {dur_str} in logged duration.\n"
                    f"• Financial Telemetry: Cumulative transaction volume of {_format_curr(fin_vol)}.\n\n"
                    f"Investigative Conclusion:\n"
                    f"Data indicates active participation in coordinated cross-entity activities. "
                    f"All indicators represent analytical decision-support telemetry for human investigators "
                    f"and do not constitute an automatic determination of criminal guilt."
                )
                exec_type = "deterministic"

        # Deduplicate evidence references
        unique_ev = []
        seen_ev_keys = set()
        for ev in evidence_references:
            k = f"{ev.source}_{ev.record_id}_{ev.description[:30]}"
            if k not in seen_ev_keys:
                seen_ev_keys.add(k)
                unique_ev.append(ev)

        report_data = InvestigationReportData(
            metadata=metadata,
            executive_summary=exec_summary,
            executive_summary_type=exec_type,
            subject_profile=subject_profile,
            associated_entities=associated_entities,
            graph_summary=graph_summary,
            cdr_analysis=cdr_summary,
            financial_analysis=financial_summary,
            timeline=timeline_items,
            risk_assessment=risk_summary,
            connected_cases=connected_cases,
            evidence_references=unique_ev,
            investigator_notes=notes,
        )

        # Cache report
        self._reports_cache[rep_id] = {
            "report_id": rep_id,
            "generated_at": gen_time,
            "target_id": target_id,
            "target_name": target_name,
            "data": report_data,
            "graph_png_bytes": graph_png_bytes,
        }

        return report_data

    # -------------------------------------------------------------------------
    # Format Exporters: JSON, HTML, PDF
    # -------------------------------------------------------------------------

    def export_json(self, data: InvestigationReportData) -> Dict[str, Any]:
        """Serializes report data to clean structured dictionary."""
        return data.model_dump()

    def export_html(self, data: InvestigationReportData) -> str:
        """Generates a responsive, publication-ready, print-optimized HTML5 document."""
        m = data.metadata
        sp = data.subject_profile
        gs = data.graph_summary
        cdr = data.cdr_analysis
        fin = data.financial_analysis
        risk = data.risk_assessment

        # Risk badge color
        risk_color = "#ef4444" if (risk and risk.score >= 70) else ("#f59e0b" if (risk and risk.score >= 40) else "#10b981")

        html_parts = [
            "<!DOCTYPE html>",
            "<html lang='en'>",
            "<head>",
            "  <meta charset='UTF-8'>",
            "  <meta name='viewport' content='width=device-width, initial-scale=1.0'>",
            f"  <title>NODE SENTINEL Investigation Report - {m.target_name} ({m.report_id})</title>",
            "  <style>",
            "    :root {",
            "      --bg-base: #090d16; --bg-surface: #111827; --bg-card: #1f2937;",
            "      --text-main: #f9fafb; --text-dim: #9ca3af; --border-color: #374151;",
            "      --primary-red: #ef4444; --accent-cyan: #06b6d4; --accent-amber: #f59e0b; --accent-emerald: #10b981;",
            "    }",
            "    @media print {",
            "      body { background: #fff !important; color: #000 !important; font-size: 11pt; }",
            "      .no-print { display: none !important; }",
            "      .card { border: 1px solid #ccc !important; background: #fff !important; color: #000 !important; page-break-inside: avoid; }",
            "      .header-banner { background: #e5e7eb !important; color: #000 !important; }",
            "      table, th, td { border: 1px solid #999 !important; color: #000 !important; }",
            "    }",
            "    body {",
            "      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;",
            "      background-color: var(--bg-base); color: var(--text-main); margin: 0; padding: 24px; line-height: 1.5;",
            "    }",
            "    .container { max-width: 1080px; margin: 0 auto; }",
            "    .header-banner {",
            "      background: linear-gradient(135deg, #1e1b4b 0%, #1e293b 100%);",
            "      border: 1px solid var(--border-color); border-radius: 8px; padding: 24px; margin-bottom: 24px;",
            "    }",
            "    .classification {",
            "      color: var(--primary-red); font-weight: 800; letter-spacing: 0.15em; font-size: 0.85rem; text-transform: uppercase;",
            "    }",
            "    .title { font-size: 1.8rem; font-weight: 700; margin: 8px 0; color: #fff; }",
            "    .meta-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-top: 16px; }",
            "    .meta-item { font-size: 0.85rem; color: var(--text-dim); }",
            "    .meta-val { color: var(--text-main); font-weight: 600; }",
            "    .card {",
            "      background-color: var(--bg-surface); border: 1px solid var(--border-color); border-radius: 8px;",
            "      padding: 20px; margin-bottom: 24px;",
            "    }",
            "    .card-title {",
            "      font-size: 1.25rem; font-weight: 600; margin-bottom: 16px; border-bottom: 2px solid var(--border-color);",
            "      padding-bottom: 8px; display: flex; justify-content: space-between; align-items: center;",
            "    }",
            "    .badge {",
            "      display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 0.75rem; font-weight: 700; text-transform: uppercase;",
            "    }",
            "    .table-wrap { overflow-x: auto; margin-top: 12px; }",
            "    table { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.9rem; }",
            "    th { background-color: var(--bg-card); color: var(--text-dim); padding: 10px 12px; font-weight: 600; border-bottom: 1px solid var(--border-color); }",
            "    td { padding: 10px 12px; border-bottom: 1px solid var(--border-color); }",
            "    tr:hover { background-color: rgba(255, 255, 255, 0.02); }",
            "    .summary-box {",
            "      background-color: rgba(30, 41, 59, 0.5); border-left: 4px solid var(--accent-cyan); padding: 16px;",
            "      border-radius: 4px; white-space: pre-line; font-size: 0.95rem; margin-top: 8px;",
            "    }",
            "    .disclaimer-box {",
            "      background-color: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.3); padding: 16px;",
            "      border-radius: 6px; font-size: 0.85rem; color: #fca5a5; line-height: 1.4;",
            "    }",
            "    .print-btn {",
            "      background: #2563eb; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;",
            "    }",
            "    .graph-img { width: 100%; max-height: 440px; object-fit: contain; border-radius: 6px; border: 1px solid var(--border-color); }",
            "  </style>",
            "</head>",
            "<body>",
            "  <div class='container'>",
            "    <!-- Print Action Bar -->",
            "    <div class='no-print' style='text-align: right; margin-bottom: 12px;'>",
            "      <button class='print-btn' onclick='window.print()'>🖨️ Print / Save as PDF</button>",
            "    </div>",
            "",
            "    <!-- Header Banner -->",
            "    <div class='header-banner'>",
            f"      <div class='classification'>{m.classification}</div>",
            f"      <div class='title'>Investigation Dossier: {m.target_name}</div>",
            "      <div class='meta-grid'>",
            f"        <div class='meta-item'>Report ID: <span class='meta-val'>{m.report_id}</span></div>",
            f"        <div class='meta-item'>Generated: <span class='meta-val'>{m.generated_at[:19].replace('T', ' ')} UTC</span></div>",
            f"        <div class='meta-item'>Agency: <span class='meta-val'>{m.investigator_agency}</span></div>",
            f"        <div class='meta-item'>Target ID: <span class='meta-val'>{m.target_id}</span></div>",
            f"        <div class='meta-item'>Target Type: <span class='meta-val'>{m.target_type.upper()}</span></div>",
            f"        <div class='meta-item'>Date Window: <span class='meta-val'>{m.date_from or 'All'} → {m.date_to or 'Present'}</span></div>",
            "      </div>",
            "    </div>",
        ]

        # Executive Summary
        if data.executive_summary:
            html_parts.extend([
                "    <div class='card'>",
                f"      <div class='card-title'><span>Executive Summary</span> <span class='badge' style='background: #0284c7; color: #fff;'>{data.executive_summary_type.replace('_', ' ').upper()}</span></div>",
                f"      <div class='summary-box'>{data.executive_summary}</div>",
                "    </div>",
            ])

        # Subject Profile
        if sp:
            html_parts.extend([
                "    <div class='card'>",
                "      <div class='card-title'>Subject Profile</div>",
                "      <div class='meta-grid'>",
                f"        <div class='meta-item'>Subject Name: <span class='meta-val'>{sp.name}</span></div>",
                f"        <div class='meta-item'>Entity ID: <span class='meta-val'>{sp.entity_id}</span></div>",
                f"        <div class='meta-item'>Entity Type: <span class='meta-val'>{sp.node_type}</span></div>",
                f"        <div class='meta-item'>Risk Rating: <span class='meta-val' style='color: {risk_color};'>{sp.risk_level} ({sp.risk_score:.0f}/100)</span></div>",
                f"        <div class='meta-item'>Aliases: <span class='meta-val'>{', '.join(sp.aliases) if sp.aliases else 'None recorded'}</span></div>",
                f"        <div class='meta-item'>Phone Numbers: <span class='meta-val'>{', '.join(sp.phone_numbers) if sp.phone_numbers else 'None recorded'}</span></div>",
                f"        <div class='meta-item'>Bank Accounts: <span class='meta-val'>{', '.join(sp.bank_accounts) if sp.bank_accounts else 'None recorded'}</span></div>",
                f"        <div class='meta-item'>Vehicles: <span class='meta-val'>{', '.join(sp.vehicle_plates) if sp.vehicle_plates else 'None recorded'}</span></div>",
                f"        <div class='meta-item'>Status: <span class='meta-val'>{sp.status or 'Under Monitoring'}</span></div>",
                f"        <div class='meta-item'>National ID: <span class='meta-val'>{sp.national_id or 'Not Indexed'}</span></div>",
                "      </div>",
                "    </div>",
            ])

        # Knowledge Graph Summary
        if gs:
            html_parts.extend([
                "    <div class='card'>",
                "      <div class='card-title'>Knowledge Graph Summary & Topology</div>",
                "      <div class='meta-grid' style='margin-bottom: 16px;'>",
                f"        <div class='meta-item'>Direct Degree: <span class='meta-val'>{gs.ego_nodes_count - 1} 1st-degree links</span></div>",
                f"        <div class='meta-item'>Betweenness Centrality: <span class='meta-val'>{gs.betweenness_centrality:.4f}</span></div>",
                f"        <div class='meta-item'>Degree Centrality: <span class='meta-val'>{gs.degree_centrality:.4f}</span></div>",
                f"        <div class='meta-item'>Graph Density: <span class='meta-val'>{gs.density:.4f}</span></div>",
                f"        <div class='meta-item'>Assigned Cluster: <span class='meta-val'>{gs.community_id}</span></div>",
                "      </div>",
            ])
            if gs.graph_image_base64:
                html_parts.append(f"      <img src='data:image/png;base64,{gs.graph_image_base64}' alt='Network Topology' class='graph-img'/>")
            html_parts.append("    </div>")

        # Associated Entities
        if data.associated_entities:
            html_parts.extend([
                "    <div class='card'>",
                f"      <div class='card-title'>Associated Network Entities ({len(data.associated_entities)})</div>",
                "      <div class='table-wrap'>",
                "        <table>",
                "          <thead><tr><th>Entity</th><th>Type</th><th>Relationship</th><th>Weight</th><th>Shared Cases</th></tr></thead>",
                "          <tbody>",
            ])
            for ae in data.associated_entities:
                html_parts.append(
                    f"          <tr>"
                    f"            <td><strong>{ae.label}</strong><br><small style='color:var(--text-dim)'>{ae.node_id}</small></td>"
                    f"            <td><span class='badge' style='background:#374151'>{ae.node_type}</span></td>"
                    f"            <td>{ae.relationship}</td>"
                    f"            <td>{ae.weight:.1f}</td>"
                    f"            <td>{', '.join(ae.shared_cases) if ae.shared_cases else '-'}</td>"
                    f"          </tr>"
                )
            html_parts.extend(["        </tbody></table></div></div>"])

        # CDR / Communication Analysis
        if cdr:
            html_parts.extend([
                "    <div class='card'>",
                "      <div class='card-title'>CDR / Telecommunications Analysis</div>",
                "      <div class='meta-grid' style='margin-bottom: 16px;'>",
                f"        <div class='meta-item'>Monitored Line: <span class='meta-val'>{cdr.target_phone or 'N/A'}</span></div>",
                f"        <div class='meta-item'>Total Calls: <span class='meta-val'>{cdr.total_calls}</span></div>",
                f"        <div class='meta-item'>Total Duration: <span class='meta-val'>{cdr.formatted_duration}</span></div>",
                f"        <div class='meta-item'>Inbound / Outbound: <span class='meta-val'>{cdr.incoming_count} / {cdr.outgoing_count}</span></div>",
                f"        <div class='meta-item'>Night Calls: <span class='meta-val'>{cdr.off_hours_calls_count}</span></div>",
                f"        <div class='meta-item'>Burst Alerts: <span class='meta-val'>{len(cdr.burst_alerts)}</span></div>",
                "      </div>",
            ])
            if cdr.top_contacts:
                html_parts.extend([
                    "      <h4 style='margin:12px 0 6px 0; color:var(--text-dim)'>Top Communication Contacts</h4>",
                    "      <div class='table-wrap'><table>",
                    "        <thead><tr><th>Contact</th><th>Calls</th><th>Duration</th><th>Inbound</th><th>Outbound</th></tr></thead><tbody>",
                ])
                for tc in cdr.top_contacts:
                    html_parts.append(
                        f"        <tr><td><strong>{tc['contact']}</strong></td><td>{tc['call_count']}</td>"
                        f"<td>{tc['duration_sec']}s</td><td>{tc['in_count']}</td><td>{tc['out_count']}</td></tr>"
                    )
                html_parts.extend(["      </tbody></table></div>"])
            html_parts.append("    </div>")

        # Financial Analysis
        if fin:
            html_parts.extend([
                "    <div class='card'>",
                "      <div class='card-title'>Financial Transaction Analysis</div>",
                "      <div class='meta-grid' style='margin-bottom: 16px;'>",
                f"        <div class='meta-item'>Monitored Account: <span class='meta-val'>{fin.target_account or 'N/A'}</span></div>",
                f"        <div class='meta-item'>Transactions Logged: <span class='meta-val'>{fin.total_transactions}</span></div>",
                f"        <div class='meta-item'>Total Inflow: <span class='meta-val' style='color:#10b981'>{_format_curr(fin.total_inflow)}</span></div>",
                f"        <div class='meta-item'>Total Outflow: <span class='meta-val' style='color:#ef4444'>{_format_curr(fin.total_outflow)}</span></div>",
                f"        <div class='meta-item'>Net Flow: <span class='meta-val'>{_format_curr(fin.net_flow)}</span></div>",
                "      </div>",
            ])
            if fin.high_value_transactions:
                html_parts.extend([
                    "      <h4 style='margin:12px 0 6px 0; color:var(--text-dim)'>High-Value Transactions</h4>",
                    "      <div class='table-wrap'><table>",
                    "        <thead><tr><th>Txn ID</th><th>Timestamp</th><th>Amount</th><th>Sender</th><th>Receiver</th></tr></thead><tbody>",
                ])
                for ht in fin.high_value_transactions:
                    html_parts.append(
                        f"        <tr><td><code>{ht['txn_id']}</code></td><td>{ht['timestamp'][:16].replace('T', ' ')}</td>"
                        f"<td style='color:#f59e0b;font-weight:700'>{_format_curr(ht['amount'])}</td>"
                        f"<td>{ht['sender']}</td><td>{ht['receiver']}</td></tr>"
                    )
                html_parts.extend(["      </tbody></table></div>"])
            html_parts.append("    </div>")

        # Risk Assessment
        if risk:
            html_parts.extend([
                "    <div class='card'>",
                f"      <div class='card-title'><span>Explainable Risk & Anomaly Assessment</span> <span class='badge' style='background:{risk_color}; color:#fff;'>{risk.rating} ({risk.score}/100)</span></div>",
                f"      <p><strong>Conclusion:</strong> {risk.conclusion}</p>",
                f"      <p><strong>Recommended Investigative Action:</strong> {risk.recommended_action}</p>",
            ])
            if risk.factors:
                html_parts.extend([
                    "      <div class='table-wrap'><table>",
                    "        <thead><tr><th>Factor ID</th><th>Title</th><th>Contribution</th><th>Severity</th><th>Evidence</th><th>Source</th></tr></thead><tbody>",
                ])
                for f in risk.factors:
                    html_parts.append(
                        f"        <tr><td><code>{f['factor_id']}</code></td><td><strong>{f['title']}</strong></td>"
                        f"<td style='color:#ef4444;font-weight:700'>+{f['score_contribution']:.0f} pts</td>"
                        f"<td><span class='badge' style='background:#374151'>{f['severity']}</span></td>"
                        f"<td>{f['evidence']}</td><td>{f['source']}</td></tr>"
                    )
                html_parts.extend(["      </tbody></table></div>"])
            html_parts.append("    </div>")

        # Investigation Timeline
        if data.timeline:
            html_parts.extend([
                "    <div class='card'>",
                f"      <div class='card-title'>Chronological Investigation Timeline ({len(data.timeline)} Events)</div>",
                "      <div class='table-wrap'><table>",
                "        <thead><tr><th>Timestamp</th><th>Type</th><th>Title</th><th>Description</th><th>Severity</th><th>Source</th></tr></thead><tbody>",
            ])
            for ev in data.timeline:
                html_parts.append(
                    f"        <tr>"
                    f"          <td style='white-space:nowrap'><code>{ev.timestamp[:19].replace('T', ' ')}</code></td>"
                    f"          <td><span class='badge' style='background:#1e3a8a'>{ev.event_type}</span></td>"
                    f"          <td><strong>{ev.title}</strong></td>"
                    f"          <td>{ev.description}</td>"
                    f"          <td>{ev.severity.upper()}</td>"
                    f"          <td>{ev.source or '-'}</td>"
                    f"        </tr>"
                )
            html_parts.extend(["      </tbody></table></div></div>"])

        # Connected Cases
        if data.connected_cases:
            html_parts.extend([
                "    <div class='card'>",
                f"      <div class='card-title'>Connected Criminal Cases ({len(data.connected_cases)})</div>",
                "      <div class='table-wrap'><table>",
                "        <thead><tr><th>Case ID</th><th>Title</th><th>Crime Category</th><th>Statutes</th><th>Status</th><th>Role</th></tr></thead><tbody>",
            ])
            for cc in data.connected_cases:
                html_parts.append(
                    f"        <tr><td><code>{cc.case_id}</code></td><td><strong>{cc.title}</strong></td>"
                    f"<td>{cc.crime_category or '-'}</td><td>{', '.join(cc.statutes) if cc.statutes else '-'}</td>"
                    f"<td>{cc.status or 'Active'}</td><td>{cc.role or 'Subject'}</td></tr>"
                )
            html_parts.extend(["      </tbody></table></div></div>"])

        # Evidence References Matrix
        if data.evidence_references:
            html_parts.extend([
                "    <div class='card'>",
                f"      <div class='card-title'>Evidence / Source References ({len(data.evidence_references)})</div>",
                "      <div class='table-wrap'><table>",
                "        <thead><tr><th>Source</th><th>Record ID</th><th>Timestamp</th><th>Case ID</th><th>Finding / Description</th></tr></thead><tbody>",
            ])
            for er in data.evidence_references:
                html_parts.append(
                    f"        <tr>"
                    f"          <td><code>{er.source}</code></td>"
                    f"          <td><code>{er.record_id or '-'}</code></td>"
                    f"          <td>{er.timestamp[:19].replace('T', ' ') if er.timestamp else '-'}</td>"
                    f"          <td>{er.case_id or '-'}</td>"
                    f"          <td>{er.description}</td>"
                    f"        </tr>"
                )
            html_parts.extend(["      </tbody></table></div></div>"])

        # Investigator Notes
        if data.investigator_notes:
            html_parts.extend([
                "    <div class='card'>",
                "      <div class='card-title'>Investigator Notes & Remarks</div>",
                f"      <div class='summary-box' style='border-left-color: var(--accent-amber);'>{data.investigator_notes}</div>",
                "    </div>",
            ])

        # Disclaimer
        html_parts.extend([
            "    <div class='disclaimer-box'>",
            f"      <strong>LEGAL & ANALYTICAL DISCLAIMER:</strong> {data.disclaimer}",
            "    </div>",
            "  </div>",
            "</body>",
            "</html>",
        ])

        return "\n".join(html_parts)

    # -------------------------------------------------------------------------
    # PDF Generation (PyMuPDF / fitz)
    # -------------------------------------------------------------------------

    def export_pdf(self, data: InvestigationReportData, graph_image_bytes: Optional[bytes] = None) -> bytes:
        """
        Generates a clean, vector/text multi-page PDF document using PyMuPDF.
        Includes pagination, header banners, tables, diagrams, and legal disclaimers.
        """
        doc = pymupdf.open()
        m = data.metadata
        sp = data.subject_profile
        gs = data.graph_summary
        cdr = data.cdr_analysis
        fin = data.financial_analysis
        risk = data.risk_assessment

        page_width, page_height = 595, 842  # Standard A4
        margin_left = 40
        margin_right = 555
        content_width = margin_right - margin_left  # 515

        current_page = doc.new_page(width=page_width, height=page_height)
        y = 40.0

        def check_page_break(needed_height: float) -> pymupdf.Page:
            nonlocal current_page, y
            if y + needed_height > 780.0:
                current_page = doc.new_page(width=page_width, height=page_height)
                # Header on continuation pages
                current_page.draw_rect(pymupdf.Rect(margin_left, 30, margin_right, 50), color=(0.1, 0.15, 0.25), fill=(0.1, 0.15, 0.25))
                current_page.insert_textbox(
                    pymupdf.Rect(margin_left + 10, 32, margin_right - 10, 48),
                    f"NODE SENTINEL INVESTIGATION REPORT — {m.target_name} ({m.report_id})",
                    fontsize=8,
                    color=(1, 1, 1),
                )
                y = 65.0
            return current_page

        # Header Banner on First Page
        current_page.draw_rect(pymupdf.Rect(margin_left, y, margin_right, y + 80), color=(0.08, 0.12, 0.22), fill=(0.08, 0.12, 0.22))
        current_page.insert_textbox(
            pymupdf.Rect(margin_left + 15, y + 8, margin_right - 15, y + 24),
            m.classification,
            fontsize=9,
            color=(0.95, 0.25, 0.25),
        )
        current_page.insert_textbox(
            pymupdf.Rect(margin_left + 15, y + 25, margin_right - 15, y + 50),
            f"AUTOMATED CASE INVESTIGATION DOSSIER: {m.target_name}",
            fontsize=13,
            color=(1, 1, 1),
        )
        current_page.insert_textbox(
            pymupdf.Rect(margin_left + 15, y + 52, margin_right - 15, y + 75),
            f"Report ID: {m.report_id}  |  Generated: {m.generated_at[:19].replace('T', ' ')} UTC  |  Target ID: {m.target_id} ({m.target_type.upper()})",
            fontsize=8,
            color=(0.7, 0.75, 0.85),
        )
        y += 95.0

        # Helper: Section Header
        def draw_section_header(title: str):
            nonlocal y
            check_page_break(40.0)
            current_page.draw_rect(pymupdf.Rect(margin_left, y, margin_left + 4, y + 16), color=(0.8, 0.2, 0.2), fill=(0.8, 0.2, 0.2))
            current_page.insert_textbox(
                pymupdf.Rect(margin_left + 10, y - 2, margin_right, y + 18),
                title.upper(),
                fontsize=11,
                color=(0.1, 0.15, 0.25),
            )
            current_page.draw_line((margin_left, y + 18), (margin_right, y + 18), color=(0.85, 0.85, 0.9), width=0.5)
            y += 26.0

        # Section 2: Executive Summary
        if data.executive_summary:
            draw_section_header(f"Executive Summary ({data.executive_summary_type.replace('_', ' ').title()})")
            check_page_break(80.0)
            summary_clean = data.executive_summary.replace("[AI-Assisted Investigation Summary - Grounded Telemetry]", "").replace("[Deterministic Telemetry Summary]", "").strip()
            # Background box
            box_height = min(120.0, max(50.0, len(summary_clean) * 0.15 + 20))
            current_page.draw_rect(pymupdf.Rect(margin_left, y, margin_right, y + box_height), color=(0.93, 0.95, 0.98), fill=(0.93, 0.95, 0.98))
            current_page.insert_textbox(
                pymupdf.Rect(margin_left + 10, y + 6, margin_right - 10, y + box_height - 6),
                summary_clean,
                fontsize=8.5,
                color=(0.1, 0.15, 0.25),
            )
            y += box_height + 15.0

        # Section 3: Subject Profile
        if sp:
            draw_section_header("Subject Profile")
            check_page_break(70.0)
            prof_lines = [
                f"Subject Name: {sp.name}",
                f"Entity ID: {sp.entity_id}  |  Type: {sp.node_type}",
                f"Risk Rating: {sp.risk_level} ({sp.risk_score:.0f}/100)",
                f"Aliases: {', '.join(sp.aliases) if sp.aliases else 'None recorded'}",
                f"Phone Numbers: {', '.join(sp.phone_numbers) if sp.phone_numbers else 'None'}",
                f"Bank Accounts: {', '.join(sp.bank_accounts) if sp.bank_accounts else 'None'}",
                f"Vehicles: {', '.join(sp.vehicle_plates) if sp.vehicle_plates else 'None'}",
                f"Status: {sp.status or 'Active Investigation'}",
            ]
            box_h = 75.0
            current_page.draw_rect(pymupdf.Rect(margin_left, y, margin_right, y + box_h), color=(0.95, 0.95, 0.96), fill=(0.95, 0.95, 0.96))
            current_page.insert_textbox(
                pymupdf.Rect(margin_left + 10, y + 6, margin_right - 10, y + box_h - 6),
                "\n".join(prof_lines),
                fontsize=8,
                color=(0.15, 0.2, 0.3),
            )
            y += box_h + 15.0

        # Section 5: Knowledge Graph & Topology Image
        if gs:
            draw_section_header("Knowledge Graph & Ego Topology")
            check_page_break(210.0)
            stats_str = (
                f"Direct Degree Links: {gs.ego_nodes_count - 1}  |  "
                f"Betweenness Centrality: {gs.betweenness_centrality:.4f}  |  "
                f"Degree Centrality: {gs.degree_centrality:.4f}  |  "
                f"Assigned Cluster: {gs.community_id}"
            )
            current_page.insert_textbox(
                pymupdf.Rect(margin_left, y, margin_right, y + 15),
                stats_str,
                fontsize=8,
                color=(0.3, 0.35, 0.45),
            )
            y += 18.0

            # Insert diagram if available
            img_bytes = graph_image_bytes
            if not img_bytes and gs.graph_image_base64:
                try:
                    img_bytes = base64.b64decode(gs.graph_image_base64)
                except Exception:
                    img_bytes = None

            if img_bytes:
                img_rect = pymupdf.Rect(margin_left, y, margin_right, y + 175)
                current_page.insert_image(img_rect, stream=img_bytes)
                y += 185.0

        # Section 4: Associated Entities Table
        if data.associated_entities:
            draw_section_header(f"Associated Network Entities ({len(data.associated_entities)})")
            check_page_break(40.0)
            # Table Header
            current_page.draw_rect(pymupdf.Rect(margin_left, y, margin_right, y + 16), color=(0.15, 0.2, 0.3), fill=(0.15, 0.2, 0.3))
            current_page.insert_textbox(pymupdf.Rect(margin_left + 5, y + 2, margin_left + 180, y + 14), "ENTITY / ID", fontsize=7.5, color=(1, 1, 1))
            current_page.insert_textbox(pymupdf.Rect(margin_left + 185, y + 2, margin_left + 260, y + 14), "TYPE", fontsize=7.5, color=(1, 1, 1))
            current_page.insert_textbox(pymupdf.Rect(margin_left + 265, y + 2, margin_left + 380, y + 14), "RELATIONSHIP", fontsize=7.5, color=(1, 1, 1))
            current_page.insert_textbox(pymupdf.Rect(margin_left + 385, y + 2, margin_right - 5, y + 14), "SHARED CASES", fontsize=7.5, color=(1, 1, 1))
            y += 16.0

            for ae in data.associated_entities[:10]:
                check_page_break(18.0)
                current_page.draw_rect(pymupdf.Rect(margin_left, y, margin_right, y + 16), color=(0.95, 0.95, 0.97), fill=(0.95, 0.95, 0.97) if len(data.associated_entities) % 2 == 0 else (1, 1, 1))
                current_page.insert_textbox(pymupdf.Rect(margin_left + 5, y + 2, margin_left + 180, y + 14), f"{ae.label} ({ae.node_id})", fontsize=7.5, color=(0.1, 0.1, 0.1))
                current_page.insert_textbox(pymupdf.Rect(margin_left + 185, y + 2, margin_left + 260, y + 14), ae.node_type, fontsize=7.5, color=(0.2, 0.2, 0.2))
                current_page.insert_textbox(pymupdf.Rect(margin_left + 265, y + 2, margin_left + 380, y + 14), f"{ae.relationship} ({ae.weight:.1f})", fontsize=7.5, color=(0.2, 0.2, 0.2))
                cases_str = ", ".join(ae.shared_cases) if ae.shared_cases else "-"
                current_page.insert_textbox(pymupdf.Rect(margin_left + 385, y + 2, margin_right - 5, y + 14), cases_str[:25], fontsize=7.5, color=(0.2, 0.2, 0.2))
                y += 16.0
            y += 10.0

        # Section 6: CDR Analysis
        if cdr:
            draw_section_header("CDR / Telecommunications Analysis")
            check_page_break(50.0)
            cdr_text = (
                f"Monitored Line: {cdr.target_phone or 'N/A'}  |  Total Calls: {cdr.total_calls}  |  "
                f"Duration: {cdr.formatted_duration}  |  In/Out: {cdr.incoming_count}/{cdr.outgoing_count}  |  "
                f"Night Calls: {cdr.off_hours_calls_count}  |  Burst Alerts: {len(cdr.burst_alerts)}"
            )
            current_page.insert_textbox(pymupdf.Rect(margin_left, y, margin_right, y + 16), cdr_text, fontsize=8, color=(0.15, 0.2, 0.3))
            y += 20.0

            if cdr.top_contacts:
                for tc in cdr.top_contacts[:4]:
                    check_page_break(14.0)
                    line = f"• Contact: {tc['contact']}  —  {tc['call_count']} calls ({tc['duration_sec']}s), {tc['in_count']} in / {tc['out_count']} out"
                    current_page.insert_textbox(pymupdf.Rect(margin_left + 10, y, margin_right, y + 14), line, fontsize=7.5, color=(0.25, 0.3, 0.4))
                    y += 14.0
                y += 8.0

        # Section 7: Financial Analysis
        if fin:
            draw_section_header("Financial Ledger Analysis")
            check_page_break(50.0)
            fin_text = (
                f"Target Account: {fin.target_account or 'N/A'}  |  Transactions: {fin.total_transactions}  |  "
                f"Inflow: {_format_curr(fin.total_inflow)}  |  Outflow: {_format_curr(fin.total_outflow)}  |  "
                f"Net: {_format_curr(fin.net_flow)}"
            )
            current_page.insert_textbox(pymupdf.Rect(margin_left, y, margin_right, y + 16), fin_text, fontsize=8, color=(0.15, 0.2, 0.3))
            y += 20.0

            if fin.high_value_transactions:
                for ht in fin.high_value_transactions[:4]:
                    check_page_break(14.0)
                    line = f"• Txn {ht['txn_id']} ({ht['timestamp'][:10]}): {_format_curr(ht['amount'])} from {ht['sender']} to {ht['receiver']}"
                    current_page.insert_textbox(pymupdf.Rect(margin_left + 10, y, margin_right, y + 14), line, fontsize=7.5, color=(0.25, 0.3, 0.4))
                    y += 14.0
                y += 8.0

        # Section 9: Risk & Anomaly Assessment
        if risk:
            draw_section_header(f"Explainable Risk Assessment: {risk.rating} ({risk.score}/100)")
            check_page_break(40.0)
            current_page.insert_textbox(
                pymupdf.Rect(margin_left, y, margin_right, y + 28),
                f"Conclusion: {risk.conclusion}\nRecommended Action: {risk.recommended_action}",
                fontsize=8,
                color=(0.15, 0.2, 0.3),
            )
            y += 32.0

            if risk.factors:
                for f in risk.factors[:5]:
                    check_page_break(15.0)
                    f_line = f"+{f['score_contribution']:.0f} pts [{f['severity']}] {f['title']}: {f['evidence']}"
                    current_page.insert_textbox(pymupdf.Rect(margin_left + 10, y, margin_right, y + 14), f_line, fontsize=7.5, color=(0.7, 0.15, 0.15))
                    y += 14.0
                y += 8.0

        # Section 8: Timeline Events
        if data.timeline:
            draw_section_header(f"Chronological Timeline Highlights ({len(data.timeline)} Events)")
            for ev in data.timeline[:8]:
                check_page_break(16.0)
                ev_line = f"[{ev.timestamp[:10]}] ({ev.event_type}) {ev.title} — {ev.description[:60]}"
                current_page.insert_textbox(pymupdf.Rect(margin_left + 5, y, margin_right, y + 14), ev_line, fontsize=7.5, color=(0.2, 0.25, 0.35))
                y += 15.0
            y += 8.0

        # Section 10: Connected Cases
        if data.connected_cases:
            draw_section_header(f"Connected Cases ({len(data.connected_cases)})")
            for cc in data.connected_cases[:5]:
                check_page_break(15.0)
                c_line = f"• {cc.case_id}: {cc.title} | Statutes: {', '.join(cc.statutes)} | Status: {cc.status or 'Active'}"
                current_page.insert_textbox(pymupdf.Rect(margin_left + 5, y, margin_right, y + 14), c_line, fontsize=7.5, color=(0.2, 0.25, 0.35))
                y += 15.0
            y += 8.0

        # Section 11: Evidence References
        if data.evidence_references:
            draw_section_header(f"Evidence / Source References ({len(data.evidence_references)})")
            for er in data.evidence_references[:8]:
                check_page_break(15.0)
                ref_line = f"Source: {er.source} | ID: {er.record_id or '-'} | {er.description[:70]}"
                current_page.insert_textbox(pymupdf.Rect(margin_left + 5, y, margin_right, y + 14), ref_line, fontsize=7.5, color=(0.25, 0.3, 0.4))
                y += 15.0
            y += 8.0

        # Section 12: Investigator Notes
        if data.investigator_notes:
            draw_section_header("Investigator Notes")
            check_page_break(35.0)
            current_page.insert_textbox(
                pymupdf.Rect(margin_left, y, margin_right, y + 30),
                data.investigator_notes,
                fontsize=8,
                color=(0.2, 0.2, 0.3),
            )
            y += 35.0

        # Section 13: Disclaimer Box
        check_page_break(50.0)
        current_page.draw_rect(pymupdf.Rect(margin_left, y, margin_right, y + 40), color=(0.98, 0.9, 0.9), fill=(0.98, 0.9, 0.9))
        current_page.insert_textbox(
            pymupdf.Rect(margin_left + 8, y + 4, margin_right - 8, y + 36),
            f"LEGAL NOTICE: {data.disclaimer}",
            fontsize=6.8,
            color=(0.6, 0.1, 0.1),
        )
        y += 45.0

        # Global Footers on all pages
        total_pages = len(doc)
        for i, page in enumerate(doc):
            page.draw_line((margin_left, 815), (margin_right, 815), color=(0.8, 0.8, 0.85), width=0.5)
            page.insert_textbox(
                pymupdf.Rect(margin_left, 818, margin_right, 832),
                f"CONFIDENTIAL // LAW ENFORCEMENT SENSITIVE  |  NODE SENTINEL Intelligence  |  Page {i + 1} of {total_pages}",
                fontsize=7,
                color=(0.4, 0.45, 0.55),
            )

        pdf_bytes = doc.tobytes()
        doc.close()
        return pdf_bytes

    # -------------------------------------------------------------------------
    # Public Unified Generator: generate_report
    # -------------------------------------------------------------------------

    def generate_report(self, request: InvestigationReportRequest) -> InvestigationReportResponse:
        """Main entry point to generate report in requested format."""
        data = self.generate_report_data(request)
        rep_id = data.metadata.report_id
        fmt = request.output_format.value.lower()

        cached_bytes = self._reports_cache.get(rep_id, {}).get("graph_png_bytes")

        html_out = None
        pdf_b64 = None

        if fmt == ReportFormat.HTML.value:
            html_out = self.export_html(data)
        elif fmt == ReportFormat.PDF.value:
            pdf_bytes = self.export_pdf(data, graph_image_bytes=cached_bytes)
            pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        else:
            # JSON format
            pass

        return InvestigationReportResponse(
            report_id=rep_id,
            generated_at=data.metadata.generated_at,
            target_id=data.metadata.target_id,
            target_name=data.metadata.target_name,
            target_type=data.metadata.target_type,
            output_format=fmt,
            sections_included=data.metadata.sections_included,
            download_url=f"/api/reports/{rep_id}?format={fmt}",
            data=data if fmt == ReportFormat.JSON.value else None,
            html_content=html_out,
            pdf_base64=pdf_b64,
        )

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached report by report ID."""
        return self._reports_cache.get(report_id)

    def list_recent_reports(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Returns metadata of recently generated reports."""
        results = []
        for rid, item in list(self._reports_cache.items())[-limit:]:
            results.append({
                "report_id": rid,
                "generated_at": item["generated_at"],
                "target_id": item["target_id"],
                "target_name": item["target_name"],
            })
        return list(reversed(results))


# Singleton Instance
_report_engine: Optional[ReportEngine] = None


def get_report_engine() -> ReportEngine:
    global _report_engine
    if _report_engine is None:
        _report_engine = ReportEngine()
    return _report_engine
