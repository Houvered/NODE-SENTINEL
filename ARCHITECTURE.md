# NODE SENTINEL — System Architecture

This document details the end-to-end technical architecture, analytical pipelines, and component mapping of **NODE SENTINEL**.

---

## 1. End-to-End Pipeline Overview

```
                     ┌─────────────────────────────────────────┐
                     │              DATA SOURCES               │
                     │  (FIR Text, CDR Logs, Bank Ledgers,     │
                     │   CCTV Facial Imagery, Case Files)      │
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │                INGESTION                │
                     │  (Multipart Upload, CSV/JSON Parsers,   │
                     │   Image Processing, FIR Ingestion)      │
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │              NLP / PARSING              │
                     │  (Regex Normalization, Regex Extraction,│
                     │   Tokenization, Document Parsing)       │
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │    ENTITY & RELATIONSHIP EXTRACTION     │
                     │  (Triplets: Subject-Predicate-Object,   │
                     │   Phone/Vehicle/Account Canonicalizers) │
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │             KNOWLEDGE GRAPH             │
                     │  (NetworkX MultiDiGraph Engine, 7 Node  │
                     │   Types, 6 Rel Types, Dual Neo4j Ready) │
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │             GRAPH ANALYTICS             │
                     │  (Degree/Betweenness Centrality,        │
                     │   PageRank, Louvain Community Clusters) │
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │    CDR / FINANCIAL / TIMELINE ANALYSIS  │
                     │  (24h Call Burst Sliding Windows,       │
                     │   Rapid Transfer Sequences, Timelines)  │
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │            RISK INTELLIGENCE            │
                     │  (Explainable Additive Scoring Engine,  │
                     │   Transparent Evidence Breakdown)       │
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │        AI INVESTIGATION ASSISTANT       │
                     │  (Grounded Zero-Hallucination Querying, │
                     │   Evidence Retrieval, Next-Step Actions)│
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │          INVESTIGATION REPORT           │
                     │  (13-Section Formal Dossiers, PDF/HTML/ │
                     │   JSON Output, PyMuPDF Engine)          │
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │               AUDIT TRAIL               │
                     │  (Immutable JSON Ledger, Automatic      │
                     │   Credential Redaction, RBAC Enforcement│
                     └─────────────────────────────────────────┘
```

---

## 2. Layer-by-Layer Architectural Specifications

### Layer 1: Data Sources
- **Description**: Ingests multi-source investigation artifacts including unstructured police incident reports (FIRs), raw telephony Call Detail Records (CDRs), banking ledgers/spreadsheets, CCTV surveillance stills, and investigator seed metadata.
- **Source Files & Formats**:
  - Unstructured incident narratives (`.txt`, `.pdf`, direct form inputs)
  - Telecommunication logs (`sample_data/demo_cdr.csv`, `sample_data/demo_cdr.json`)
  - Financial transaction ledgers (`sample_data/demo_financial.csv`, `sample_data/demo_financial.json`)
  - Facial biometric imagery (`sample_data/face_database/images/*.png`)
  - Seed graph database (`sample_data/syndicate_network.json`)

---

### Layer 2: Ingestion
- **Description**: Handles secure HTTP multipart file uploads, raw payload stream reading, character decoding, and validation against strict Pydantic schemas.
- **Project Modules**:
  - `app/api/routes_ingest.py` — Endpoints for FIR text ingestion, CSV/JSON uploads, and network resets.
  - `app/api/routes_search.py` — Multipart endpoint for biometric image probe submissions (`/api/search/face`).
  - `app/api/routes_cdr.py` — Ingestion routes for telecommunication logs (`/api/cdr/upload`).
  - `app/api/routes_financial.py` — Ingestion routes for banking statements (`/api/financial/upload`).

---

### Layer 3: NLP / Parsing
- **Description**: Performs textual tokenization, regular expression parsing, international telephone number canonicalization (`+91` E.164 normalization), vehicle plate pattern matching, and date-time standardization.
- **Project Modules**:
  - `app/core/document_parser.py` — Text extraction and entity normalization routines.
  - `app/core/cdr_parser.py` — Parses telecom CSV/JSON records, normalizes phone numbers, validates ISO timestamps and call duration integers.
  - `app/core/financial_parser.py` — Parses financial ledger rows, normalizes account identifiers, cleans currency amounts, and extracts timestamps.
  - `app/core/face_engine.py` — Image tensor decoding, resizing, facial feature embedding calculation, and cosine similarity metric evaluation.

---

### Layer 4: Entity & Relationship Extraction
- **Description**: Extracts structured entity nodes and directional relationship edges using heuristic rule extractors and Subject-Predicate-Object (SPO) triplet patterns.
- **Extracted Entity Types**: `Person`, `Phone`, `Vehicle`, `Case`, `BankAccount`, `Location`, `Organization`.
- **Extracted Relationship Types**: `CALLS`, `TRANSFERRED_MONEY`, `INVOLVED_IN`, `LOCATED_AT`, `OWNS`, `OPERATES`.
- **Project Modules**:
  - `app/core/graph_engine.py` — Canonical node creation, edge linking, and entity deduplication.
  - `app/models/schemas.py` — Pydantic models enforcing typing for nodes, edges, and graph payloads.

---

### Layer 5: Knowledge Graph
- **Description**: The core topology engine maintaining a unified multi-relational graph. Supports fast neighbor traversals, multi-hop subgraphs, node degree queries, and shortest path calculations.
- **Project Modules**:
  - `app/core/graph_engine.py` — `GraphEngine` class managing an in-memory `networkx.MultiDiGraph` with dual-driver support for Neo4j.
  - `app/api/routes_graph.py` — Traversal APIs: `/api/network/graph`, `/api/network/node/{id}/expand`, `/api/network/path`, `/api/network/meta`.
  - `app/core/universal_search.py` — Inverted index mapping entity identifiers, names, phone numbers, and case references directly to graph node IDs.

---

### Layer 6: Graph Analytics
- **Description**: Quantitative topological graph analytics to identify key facilitators, bridge nodes, and operational syndicates.
- **Algorithms**:
  - **Degree Centrality**: Immediate connectivity volume.
  - **Betweenness Centrality**: Key information/money brokers bridging disparate network clusters.
  - **PageRank**: Structural influence within the criminal hierarchy.
  - **Louvain Modularity**: Unsupervised community and cell detection.
- **Project Modules**:
  - `app/core/graph_analytics.py` — Algorithms utilizing NetworkX analytics routines.
  - `app/api/routes_analytics.py` — Endpoints: `/api/network/analytics/centrality`, `/api/network/analytics/communities`.
  - `app/core/anomaly_detector.py` — Heuristic detection of high-risk topological patterns.

---

### Layer 7: CDR / Financial / Timeline Analysis
- **Description**: Specialized domain engines for temporal telemetry and behavioral pattern recognition.
- **Analytical Capabilities**:
  - **CDR Engine**: Sliding-window burst detector (flags >5 calls within 24 hours), call frequency histograms, interlocutor ranking.
  - **Financial Engine**: Rapid transaction sequence detector (>3 transfers in 120 minutes), high-value velocity alerts, counterparty net flow calculation.
  - **Unified Timeline**: Multi-source chronological interleaving of FIR incidents, CDR calls, and financial transfers with severity tags (`CRITICAL`, `ELEVATED`, `NOTICE`).
- **Project Modules**:
  - `app/core/cdr_analytics.py` & `app/api/routes_cdr.py`
  - `app/core/financial_analytics.py` & `app/api/routes_financial.py`
  - `app/core/timeline_engine.py` & `app/api/routes_timeline.py`

---

### Layer 8: Risk Intelligence
- **Description**: Transparent, additive risk scoring engine eliminating black-box opacity. Decomposes composite risk scores (0–100) into explainable evidence factors with explicit non-guilt decision-support language.
- **Scoring Contributions**:
  - Known High-Risk Registry Indicator (`+35 pts`)
  - High Betweenness Centrality Broker (`+20 pts`)
  - Telecommunication Burst Detected (`+15 pts`)
  - Rapid Financial Flow Sequence (`+10 pts`)
  - Multiple FIR Case Involvements (`+2 pts/case`)
- **Project Modules**:
  - `app/core/risk_intelligence.py` — Additive score computation and evidence breakdown generator.
  - `app/api/routes_risk.py` — Endpoints: `/api/risk/evaluate/{entity_id}`, `/api/risk/summary`.
  - `app/models/risk_models.py` — Data transfer models for risk factors and assessment summaries.

---

### Layer 9: AI Investigation Assistant
- **Description**: Grounded natural language query engine providing zero-hallucination investigative insights. Retrieves indexed graph facts, CDR records, and financial ledgers to construct evidence-backed answers with clickable investigation action chips.
- **Project Modules**:
  - `app/core/assistant_engine.py` — Intent classifier, context retriever, grounded prompt synthesizer, and response generator.
  - `app/core/ai_provider.py` — Provider abstraction supporting local deterministic grounding or external LLM backends.
  - `app/api/routes_assistant.py` — Endpoints: `/api/assistant/query`, `/api/assistant/suggestions`.
  - `app/models/assistant_models.py` — Chat message models and action intent schemas.

---

### Layer 10: Investigation Report
- **Description**: Automated case report compiler generating formal, publication-grade dossiers in PDF, HTML, and JSON formats. Incorporates 13 standard law enforcement sections, network metrics, ledger summaries, and statutory disclaimers.
- **Project Modules**:
  - `app/core/report_engine.py` — Report generation engine utilizing PyMuPDF (`fitz`) for PDF layout and HTML5 templating for web rendering.
  - `app/api/routes_reports.py` — Endpoints: `/api/reports/generate`, `/api/reports/templates`.
  - `app/models/report_models.py` — Schema definitions for structured report requests and metadata.

---

### Layer 11: Audit Trail & Security
- **Description**: Immutable operational audit logging and Role-Based Access Control (RBAC). Automatically sanitizes and redacts passwords, cryptographic hashes, bearer tokens, and sensitive headers before disk persistence.
- **RBAC Roles**: `ADMIN`, `INVESTIGATOR`, `ANALYST`, `VIEWER`.
- **Project Modules**:
  - `app/core/audit_logger.py` — Audit logger with thread-safe JSON persistence and credential redactor.
  - `app/core/auth_service.py` — PBKDF2-HMAC-SHA256 password hasher and Bearer token authenticator.
  - `app/api/routes_auth.py` — Endpoints: `/api/auth/login`, `/api/auth/me`.
  - `app/api/routes_audit.py` — Endpoints: `/api/audit/logs`, `/api/audit/stats`.
  - `app/api/routes_users.py` — User management endpoints (Admin only).
  - `app/models/audit_models.py` & `app/models/auth_models.py` — RBAC and audit data models.

---

## 3. Frontend Architecture

- **Single-Page Application**: Self-contained `static/index.html` built with Vanilla HTML5, CSS tokens, and JavaScript (ES6+).
- **Visualization Canvas**: Vis.js Network for interactive force-directed graph rendering with physics stabilization and dynamic node hierarchy.
- **Modular Panels**:
  - Top Navigation Bar (Investigation workflow status & search)
  - Left Investigation Sidebar (5 categorized sections)
  - Central Workspace (Knowledge Graph, CDR, Finance, Timeline, Reports, Audit)
  - Right Intelligence Dossier (Identity, Actions, Risk Assessment, Network, Anomalies)
