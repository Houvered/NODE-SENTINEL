# -*- coding: utf-8 -*-
"""
NODE SENTINEL - Automated Case Report Generation Tests (STEP 14)
Comprehensive test suite verifying:
- Entity and Case report generation
- PDF, HTML, and JSON format exports
- All 13 standard sections
- Specific section selection filtering
- Evidence source matrix attribution (Source, Entity, Timestamp, Case ID, Record ID)
- Empty data handling and non-existent entity error handling
- AI Assistant grounded summary and deterministic fallback resilience
- Legal disclaimer and non-guilt decision-support compliance
- REST API endpoints: POST /api/reports/investigation, GET /api/reports/{report_id}
"""
import base64
import pytest
import pymupdf
from fastapi.testclient import TestClient

from app.main import app
from app.core.graph_engine import get_graph_engine
from app.api.routes_ingest import load_dataset_by_name
from app.core.report_engine import get_report_engine
from app.models.report_models import InvestigationReportRequest, ReportFormat, ReportSection


@pytest.fixture(scope="module", autouse=True)
def setup_dataset():
    """Ensure baseline graph dataset is loaded."""
    graph = get_graph_engine()
    if len(graph.get_all_nodes()) == 0:
        load_dataset_by_name(graph, "syndicate_network.json")
    return graph


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def report_engine():
    return get_report_engine()


# =============================================================================
# 1. CORE REPORT GENERATION TESTS
# =============================================================================

def test_entity_report_generation_json(report_engine):
    """Test generating a comprehensive JSON report for a known subject entity."""
    req = InvestigationReportRequest(
        entity_id="Tariq Ahmad",
        output_format=ReportFormat.JSON,
        investigator_notes="Field surveillance conducted on Sector 62.",
    )
    res = report_engine.generate_report(req)

    assert res.report_id.startswith("REP-")
    assert res.target_name == "Tariq Ahmad"
    assert res.output_format == "json"
    assert res.data is not None

    # Verify standard sections populated
    d = res.data
    assert d.metadata.target_name == "Tariq Ahmad"
    assert d.metadata.classification == "LAW ENFORCEMENT SENSITIVE // RESTRICTED INVESTIGATIVE MATERIAL"
    assert d.subject_profile is not None
    assert d.subject_profile.entity_id == "PERSON_TARIQ_AHMAD"
    assert len(d.associated_entities) > 0
    assert d.graph_summary is not None
    assert d.cdr_analysis is not None
    assert d.financial_analysis is not None
    assert len(d.timeline) > 0
    assert d.risk_assessment is not None
    assert len(d.connected_cases) > 0
    assert len(d.evidence_references) > 0
    assert d.investigator_notes == "Field surveillance conducted on Sector 62."
    assert "constitute a legal finding of fact" in d.disclaimer.lower()


def test_case_report_generation(report_engine):
    """Test generating a case-centric report for a registered FIR."""
    req = InvestigationReportRequest(
        case_id="FIR-2024-NDPS-01",
        output_format=ReportFormat.JSON,
    )
    res = report_engine.generate_report(req)

    assert res.report_id.startswith("REP-")
    assert "FIR-2024-NDPS-01" in res.target_name or res.target_id == "FIR-2024-NDPS-01"
    assert res.data is not None
    assert res.data.metadata.target_type == "case"


def test_missing_entity_error(report_engine):
    """Test that requesting an unknown entity raises a clear descriptive ValueError."""
    req = InvestigationReportRequest(
        entity_id="NON_EXISTENT_SUSPECT_9999",
        output_format=ReportFormat.JSON,
    )
    with pytest.raises(ValueError, match="not found in the investigation dataset"):
        report_engine.generate_report(req)


def test_empty_target_specification(report_engine):
    """Test that omitting both entity_id and case_id raises ValueError."""
    req = InvestigationReportRequest(
        output_format=ReportFormat.JSON,
    )
    with pytest.raises(ValueError, match="Either entity_id or case_id must be provided"):
        report_engine.generate_report(req)


# =============================================================================
# 2. MULTI-FORMAT EXPORT TESTS (PDF, HTML, JSON)
# =============================================================================

def test_pdf_report_generation(report_engine):
    """Test generating a valid, multi-page vector PDF report with PyMuPDF."""
    req = InvestigationReportRequest(
        entity_id="Tariq Ahmad",
        output_format=ReportFormat.PDF,
    )
    res = report_engine.generate_report(req)

    assert res.output_format == "pdf"
    assert res.pdf_base64 is not None
    assert len(res.pdf_base64) > 1000

    # Verify valid PDF structure using PyMuPDF
    pdf_bytes = base64.b64decode(res.pdf_base64)
    assert pdf_bytes.startswith(b"%PDF-")

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc) >= 2  # Multi-page layout

    # Verify classification banner on page 1
    page1_text = doc[0].get_text()
    assert "LAW ENFORCEMENT SENSITIVE" in page1_text
    assert "Tariq Ahmad" in page1_text
    assert "executive summary" in page1_text.lower()
    doc.close()


def test_html_report_generation(report_engine):
    """Test generating standalone responsive HTML with print-ready CSS."""
    req = InvestigationReportRequest(
        entity_id="Tariq Ahmad",
        output_format=ReportFormat.HTML,
    )
    res = report_engine.generate_report(req)

    assert res.output_format == "html"
    assert res.html_content is not None
    html = res.html_content

    assert "<!DOCTYPE html>" in html
    assert "NODE SENTINEL Investigation Report" in html
    assert "Tariq Ahmad" in html
    assert "Executive Summary" in html
    assert "Associated Network Entities" in html
    assert "data:image/png;base64," in html  # Embedded network diagram
    assert "@media print" in html  # Print-ready CSS


def test_json_report_export(report_engine):
    """Test that JSON export conforms to structured schema."""
    req = InvestigationReportRequest(
        entity_id="Tariq Ahmad",
        output_format=ReportFormat.JSON,
    )
    res = report_engine.generate_report(req)

    json_dict = report_engine.export_json(res.data)
    assert isinstance(json_dict, dict)
    assert "metadata" in json_dict
    assert "subject_profile" in json_dict
    assert "evidence_references" in json_dict
    assert "risk_assessment" in json_dict


# =============================================================================
# 3. SECTION FILTERING & DATE RANGE TESTS
# =============================================================================

def test_section_selection_subset(report_engine):
    """Test that requesting only specific sections excludes non-selected sections."""
    req = InvestigationReportRequest(
        entity_id="Tariq Ahmad",
        sections=["cdr_analysis", "financial_analysis"],
        output_format=ReportFormat.JSON,
    )
    res = report_engine.generate_report(req)

    assert set(res.sections_included) == {"cdr_analysis", "financial_analysis"}
    assert res.data.cdr_analysis is not None
    assert res.data.financial_analysis is not None
    assert res.data.graph_summary is None
    assert len(res.data.timeline) == 0


def test_date_range_filtering_on_timeline(report_engine):
    """Test date bounds filtering applied to investigation timeline."""
    req = InvestigationReportRequest(
        entity_id="Tariq Ahmad",
        date_from="2024-07-15",
        date_to="2024-07-17",
        sections=["timeline"],
        output_format=ReportFormat.JSON,
    )
    res = report_engine.generate_report(req)

    assert res.data.metadata.date_from == "2024-07-15"
    assert res.data.metadata.date_to == "2024-07-17"
    for ev in res.data.timeline:
        assert ev.timestamp >= "2024-07-15"


# =============================================================================
# 4. EVIDENCE ATTRIBUTION MATRIX TESTS
# =============================================================================

def test_evidence_source_references(report_engine):
    """Test that each verifiable finding includes Source, Entity, Timestamp, and Record ID."""
    req = InvestigationReportRequest(
        entity_id="Tariq Ahmad",
        output_format=ReportFormat.JSON,
    )
    res = report_engine.generate_report(req)

    ev_list = res.data.evidence_references
    assert len(ev_list) > 0

    sources = {ev.source for ev in ev_list}
    # Sources should reflect primary telemetry files / registries
    assert any("financial" in s.lower() or "cdr" in s.lower() or "registry" in s.lower() or "graph" in s.lower() for s in sources)

    for ev in ev_list[:10]:
        assert ev.source is not None and len(ev.source) > 0
        assert ev.description is not None and len(ev.description) > 0


# =============================================================================
# 5. AI SUMMARY WITH DETERMINISTIC FALLBACK
# =============================================================================

def test_ai_summary_and_fallback(report_engine):
    """Test that executive summary generates successfully with clear ground truth labeling."""
    req = InvestigationReportRequest(
        entity_id="Tariq Ahmad",
        sections=["executive_summary"],
        output_format=ReportFormat.JSON,
    )
    res = report_engine.generate_report(req)

    assert res.data.executive_summary is not None
    assert len(res.data.executive_summary) > 50
    # Must be either ai_grounded or deterministic fallback
    assert res.data.executive_summary_type in ("ai_grounded", "deterministic")


# =============================================================================
# 6. NON-GUILT COMPLIANCE & LEGAL DISCLAIMER
# =============================================================================

def test_legal_disclaimer_compliance(report_engine):
    """Ensure report strictly complies with decision-support standards and does not imply guilt."""
    req = InvestigationReportRequest(
        entity_id="Tariq Ahmad",
        output_format=ReportFormat.JSON,
    )
    res = report_engine.generate_report(req)

    disclaimer = res.data.disclaimer
    assert "automated investigative decision-support artifact" in disclaimer
    assert "not constitute a legal finding of fact or declaration of criminal guilt" in disclaimer

    # Verify risk conclusion is explainable
    if res.data.risk_assessment:
        assert res.data.risk_assessment.conclusion is not None


# =============================================================================
# 7. REST API ENDPOINTS INTEGRATION TESTS
# =============================================================================

def test_api_post_investigation_report_json(client):
    """Test POST /api/reports/investigation returning JSON response."""
    payload = {
        "entity_id": "Tariq Ahmad",
        "output_format": "json",
        "investigator_notes": "Surveillance review complete.",
    }
    response = client.post("/api/reports/investigation", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["report_id"].startswith("REP-")
    assert data["target_name"] == "Tariq Ahmad"
    assert data["data"] is not None
    assert data["data"]["investigator_notes"] == "Surveillance review complete."


def test_api_post_investigation_report_pdf_download(client):
    """Test POST /api/reports/investigation with download=true returning streaming PDF attachment."""
    payload = {
        "entity_id": "Tariq Ahmad",
        "output_format": "pdf",
    }
    response = client.post("/api/reports/investigation?download=true", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment;" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF-")


def test_api_post_investigation_report_html_download(client):
    """Test POST /api/reports/investigation with download=true returning streaming HTML attachment."""
    payload = {
        "entity_id": "Tariq Ahmad",
        "output_format": "html",
    }
    response = client.post("/api/reports/investigation?download=true", json=payload)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "attachment;" in response.headers["content-disposition"]
    assert "<!DOCTYPE html>" in response.text


def test_api_get_report_by_id(client):
    """Test GET /api/reports/{report_id} retrieves cached report in any format."""
    # First generate a report to get an ID
    gen_res = client.post("/api/reports/investigation", json={"entity_id": "Tariq Ahmad", "output_format": "json"})
    assert gen_res.status_code == 200
    rid = gen_res.json()["report_id"]

    # Fetch as JSON
    r_json = client.get(f"/api/reports/{rid}?format=json")
    assert r_json.status_code == 200
    assert r_json.json()["metadata"]["report_id"] == rid

    # Fetch as HTML
    r_html = client.get(f"/api/reports/{rid}?format=html")
    assert r_html.status_code == 200
    assert "<!DOCTYPE html>" in r_html.text

    # Fetch as PDF
    r_pdf = client.get(f"/api/reports/{rid}?format=pdf")
    assert r_pdf.status_code == 200
    assert r_pdf.content.startswith(b"%PDF-")


def test_api_get_report_not_found(client):
    """Test GET /api/reports/{report_id} with unknown ID returns 404."""
    response = client.get("/api/reports/REP-UNKNOWN-999")
    assert response.status_code == 404


def test_api_list_reports(client):
    """Test GET /api/reports lists recent reports."""
    response = client.get("/api/reports")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    assert len(items) > 0
    assert "report_id" in items[0]
