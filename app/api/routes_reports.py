# -*- coding: utf-8 -*-
"""
NODE SENTINEL - Report Generation API Routes (STEP 14)
Provides endpoints for automated case and entity investigation report generation
in PDF, HTML, and JSON formats.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.audit_logger import audit_logger
from app.core.report_engine import get_report_engine
from app.models.audit_models import AuditAction
from app.models.report_models import (
    InvestigationReportData,
    InvestigationReportRequest,
    InvestigationReportResponse,
    ReportFormat,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post(
    "/investigation",
    response_model=InvestigationReportResponse,
    summary="Generate an automated case/entity investigation report",
)
def generate_investigation_report(
    request: InvestigationReportRequest,
    download: bool = Query(False, description="If true, directly streams the file attachment (PDF/HTML)"),
) -> Any:
    """
    Generates a publication-grade, decision-support investigation report covering
    all 13 standard sections (or a selected subset) for an entity or criminal case.
    Supports PDF, HTML, and JSON export.
    """
    engine = get_report_engine()
    try:
        response = engine.generate_report(request)
        audit_logger.log(
            action=AuditAction.GENERATE_REPORT,
            resource_type="REPORT",
            resource_id=response.report_id,
            status="SUCCESS",
            details={
                "report_id": response.report_id,
                "target_id": response.target_id,
                "target_name": response.target_name,
                "output_format": response.output_format,
            },
        )
    except ValueError as e:
        logger.warning(f"Report generation validation error: {e}")
        audit_logger.log(
            action=AuditAction.GENERATE_REPORT,
            resource_type="REPORT",
            resource_id=request.entity_id or request.case_id,
            status="FAILED",
            details={"error": str(e)},
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(f"Report generation internal error: {e}", exc_info=True)
        audit_logger.log(
            action=AuditAction.GENERATE_REPORT,
            resource_type="REPORT",
            resource_id=request.entity_id or request.case_id,
            status="ERROR",
            details={"error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate investigation report: {str(e)}",
        )

    # Direct attachment download handling
    if download:
        cached = engine.get_report(response.report_id)
        if not cached:
            raise HTTPException(status_code=404, detail="Generated report not found in cache")

        report_data: InvestigationReportData = cached["data"]
        target_slug = (response.target_name or response.target_id).replace(" ", "_")

        if response.output_format == ReportFormat.PDF.value:
            pdf_bytes = engine.export_pdf(report_data, graph_image_bytes=cached.get("graph_png_bytes"))
            filename = f"report_{target_slug}_{response.report_id}.pdf"
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        elif response.output_format == ReportFormat.HTML.value:
            html_str = engine.export_html(report_data)
            filename = f"report_{target_slug}_{response.report_id}.html"
            return Response(
                content=html_str,
                media_type="text/html",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        else:
            filename = f"report_{target_slug}_{response.report_id}.json"
            return Response(
                content=report_data.model_dump_json(indent=2),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

    return response


@router.get(
    "/{report_id}",
    summary="Retrieve previously generated report by ID",
)
def get_report_by_id(
    report_id: str,
    format: str = Query("json", pattern="^(pdf|html|json)$", description="Format to retrieve: pdf, html, or json"),
    download: bool = Query(False, description="If true, forces attachment download"),
) -> Any:
    """
    Retrieves a cached investigation report by its unique report ID.
    Allows re-exporting the report into PDF, HTML, or JSON.
    """
    engine = get_report_engine()
    cached = engine.get_report(report_id)
    if not cached:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report with ID '{report_id}' was not found.",
        )

    report_data: InvestigationReportData = cached["data"]
    target_slug = (report_data.metadata.target_name or report_data.metadata.target_id).replace(" ", "_")

    req_fmt = format.lower()

    if req_fmt == ReportFormat.PDF.value:
        pdf_bytes = engine.export_pdf(report_data, graph_image_bytes=cached.get("graph_png_bytes"))
        disposition = "attachment" if download else "inline"
        filename = f"report_{target_slug}_{report_id}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
        )

    elif req_fmt == ReportFormat.HTML.value:
        html_str = engine.export_html(report_data)
        if download:
            filename = f"report_{target_slug}_{report_id}.html"
            return Response(
                content=html_str,
                media_type="text/html",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        return HTMLResponse(content=html_str)

    else:
        # JSON format
        if download:
            filename = f"report_{target_slug}_{report_id}.json"
            return Response(
                content=report_data.model_dump_json(indent=2),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        return report_data.model_dump()


@router.get(
    "",
    summary="List recent generated reports",
)
def list_reports(
    limit: int = Query(20, ge=1, le=100, description="Max reports to list"),
) -> List[Dict[str, Any]]:
    """Lists metadata of recently generated reports during this session."""
    engine = get_report_engine()
    return engine.list_recent_reports(limit=limit)
