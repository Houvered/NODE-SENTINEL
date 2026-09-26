# -*- coding: utf-8 -*-
"""
Evidence processing pipeline for NODE SENTINEL.

Document states: uploaded → validating → extracting_text →
extracting_entities → extracting_relationships → awaiting_review
(→ approved → graph_updated, or → failed at any point).

- Unstructured (pdf/docx/txt/md/log/images): page-aware text extraction,
  rule-based NLP entities + triplets, each stored with evidence sentence,
  page, character offsets, confidence, provider/model version.
- Structured (csv/xlsx/json): staged as a dataset import awaiting column
  mapping (see dataset_schemas / routes_case_datasets).
- Runs synchronously inside a processing_jobs record (same state machine
  an async worker would use; drop-in point for a task queue later).
- Extracted rows are NEVER auto-merged into the graph: a reviewer must
  approve them (review queue), preserving the human-verification rule.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

PROVIDER_RULE = "rule-regex-v1"
PROVIDER_SPACY = "spacy:en_core_web_sm"
PROVIDER_HEUR = "keyword-heuristic-v1"
MODEL_VERSION = "nlp-extractor-v1"
CONF_RULE = 0.8
CONF_SPACY = 0.9
CONF_HEUR = 0.6


def _provider_for(entity: Dict[str, Any]) -> Tuple[str, float]:
    src = (entity.get("properties") or {}).get("source", "")
    if src == "spacy_ner":
        return PROVIDER_SPACY, CONF_SPACY
    if src == "location_heuristic":
        return PROVIDER_HEUR, CONF_HEUR
    return PROVIDER_RULE, CONF_RULE


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?\n])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _evidence_for(page_text: str, needle: str) -> Tuple[str, int, int]:
    """First sentence containing needle + char offsets within the page."""
    for sent in _sentences(page_text):
        idx = sent.find(needle)
        if idx >= 0:
            start = page_text.find(sent) + idx
            return sent[:500], start, start + len(needle)
    idx = page_text.find(needle)
    if idx >= 0:
        return page_text[max(0, idx - 120):idx + 380][:500], idx, idx + len(needle)
    return page_text[:500], 0, 0


def extract_pages_text(stored_path: Path, ext: str, original: bytes) -> Tuple[List[Tuple[int, str]], int]:
    """Returns ([(page_no, text)], page_count) for unstructured evidence."""
    from app.core.document_parser import DocumentParser
    import asyncio

    if ext == ".pdf":
        try:
            import pymupdf
            doc = pymupdf.open(stream=original, filetype="pdf")
            pages = []
            for i, page in enumerate(doc):
                t = (page.get_text("text") or "").strip()
                if t:
                    pages.append((i + 1, t))
            n = len(doc)
            doc.close()
            if pages:
                return pages, n
        except Exception as ex:
            logger.warning(f"PDF page split failed, falling back to whole-text: {ex}")
        parser = DocumentParser()
        text = asyncio.run(parser.extract_text_from_file(original, stored_path.name))
        return ([(0, text)] if text.strip() else []), 0
    if ext == ".docx":
        from app.core.evidence_files import read_docx_text
        text, paras = read_docx_text(original)
        # Paragraph-granular evidence: page field carries paragraph number.
        out = []
        for i, para in enumerate([p for p in text.split("\n") if p.strip()], start=1):
            out.append((i, para))
        return out, paras
    if ext in {".txt", ".md", ".log"}:
        for enc in ("utf-8", "latin-1"):
            try:
                return [(0, original.decode(enc).strip())], 1
            except (UnicodeDecodeError, ValueError):
                continue
        raise ValueError("Could not decode text file.")
    # images → OCR single unit
    parser = DocumentParser()
    text = asyncio.run(parser.extract_text_from_file(original, stored_path.name))
    return ([(0, text)] if text.strip() else []), 1


def process_unstructured(store, doc: Dict[str, Any]) -> Dict[str, Any]:
    """Run NLP over stored file; persist extractions; return counts."""
    from app.core.nlp_extractor import NLPExtractor

    case_id, doc_id = doc["case_id"], doc["id"]
    stored = Path(settings.DATA_DIR) / "cases" / case_id / "originals" / doc["filename_stored"]
    original = stored.read_bytes()

    store.set_doc_status(doc_id, "extracting_text")
    pages, page_count = extract_pages_text(stored, doc["ext"], original)
    full_chars = sum(len(t) for _, t in pages)
    if not pages:
        raise ValueError("No readable text could be extracted from the uploaded document.")
    store.set_doc_status(doc_id, "extracting_text", page_count=page_count, text_chars=full_chars)

    extractor = NLPExtractor()
    store.set_doc_status(doc_id, "extracting_entities")
    n_ent = 0
    for page_no, page_text in pages:
        for ent in extractor.extract_entities(page_text):
            name = ent.get("name", "")
            provider, conf = _provider_for(ent)
            ev, cs, ce = _evidence_for(page_text, name)
            store.add_extraction(
                case_id=case_id, document_id=doc_id, kind="entity",
                etype=str(ent.get("label", "Person")), value=name,
                normalized=name.strip().upper(),
                evidence_text=ev, page=page_no, char_start=cs, char_end=ce,
                confidence=conf, provider=provider, model_version=MODEL_VERSION)
            n_ent += 1

    store.set_doc_status(doc_id, "extracting_relationships")
    n_rel = 0
    for page_no, page_text in pages:
        page_ents = extractor.extract_entities(page_text)
        for rel in extractor.extract_triplets(page_text, page_ents):
            subj = str(rel.get("source", ""))
            obj = str(rel.get("target", ""))
            pred = str(rel.get("relationship", "ASSOCIATED_WITH"))
            ev, cs, ce = _evidence_for(page_text, subj)
            store.add_extraction(
                case_id=case_id, document_id=doc_id, kind="relationship",
                etype=pred, value=f"{subj} {pred} {obj}", normalized="",
                subject=subj, predicate=pred, object=obj,
                evidence_text=ev, page=page_no, char_start=cs, char_end=ce,
                confidence=CONF_RULE, provider=PROVIDER_RULE, model_version=MODEL_VERSION)
            n_rel += 1

    store.set_doc_status(doc_id, "awaiting_review",
                         page_count=page_count, text_chars=full_chars,
                         entities_count=n_ent, rels_count=n_rel)
    return {"pages": page_count, "text_chars": full_chars,
            "entities": n_ent, "relationships": n_rel}


def run_document_job(store, doc: Dict[str, Any]) -> Dict[str, Any]:
    """Create a job record, run the right pipeline branch, close the job."""
    job = store.add_job(doc["case_id"], "process_document", doc["id"])
    try:
        store.update_job(job["id"], status="running", progress=10)
        store.set_doc_status(doc["id"], "validating")
        from app.core.evidence_files import STRUCTURED_EXTS
        if doc["ext"] in STRUCTURED_EXTS:
            from app.core import dataset_schemas
            result = dataset_schemas.stage_structured_upload(store, doc)
            store.set_doc_status(doc["id"], "awaiting_review")
        else:
            result = process_unstructured(store, doc)
        store.update_job(job["id"], status="succeeded", progress=100,
                         result_json=json.dumps(result, default=str))
        return {"job_id": job["id"], "status": "succeeded", **result}
    except Exception as ex:
        logger.warning(f"Document processing failed for {doc['id']}: {ex}")
        store.set_doc_status(doc["id"], "failed", error=str(ex)[:500])
        store.update_job(job["id"], status="failed", error=str(ex)[:500])
        return {"job_id": job["id"], "status": "failed", "error": str(ex)[:500]}
