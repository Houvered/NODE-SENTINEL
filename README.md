# NODE SENTINEL — AI-Assisted Criminal Network Intelligence Engine

> **Autonomous Multi-Source Telemetry Fusion & Decision-Support System for Complex Criminal and Financial Investigations.**

---

## 1. Problem
Modern law enforcement and financial intelligence units face critical operational hurdles when investigating organized criminal syndicates:
- **Fragmented Data Silos**: Information is trapped in unstructured police incident reports (FIRs), raw telephony Call Detail Records (CDRs), banking ledgers, and surveillance footage.
- **Hidden Network Topology**: Syndicate leaders and money laundering controllers shield themselves behind multi-tier proxy networks, burner SIMs, and shell accounts.
- **Investigation Latency & Cognitive Overload**: Analysts spend days manually matching telephone numbers, transaction hashes, and suspect names across disconnected spreadsheets.
- **Opaque Decision-Making**: Legacy scoring engines generate arbitrary "black-box" risk ratings that fail court scrutiny and violate civil liberty standards.

---

## 2. Solution
**NODE SENTINEL** unifies multi-source investigative data into a living, explainable **Knowledge Graph Intelligence Platform**. It provides automated entity extraction, graph analytics, biometric face recognition, telephony burst detection, financial velocity analysis, unified chronological timelines, zero-hallucination AI inquiry, and one-touch court-admissible case reports—all protected by role-based access control and tamper-evident audit trails.

---

## 3. Key Features
- **Interactive Multi-Relational Knowledge Graph**: 7 entity types, 6 relationship types, 1-hop/2-hop neighborhood expansion, and shortest-path calculation.
- **Universal Multi-Modal Search**: Instant indexing across suspect names, normalized phone numbers (`+91`), vehicle registration plates, bank accounts, and FIR case IDs.
- **Biometric Face Recognition**: Cosine similarity face matching against enrolled photo registries with strictly bounded confidence and disclaimer compliance.
- **CDR Telemetry Analytics**: Sliding-window burst detection (>5 calls/24h), call frequency distributions, and interlocutor ranking.
- **Financial Flow & Velocity Analysis**: Rapid transaction sequence detection (>3 transfers/120 min), counterparty flow aggregation, and high-value alerts.
- **Unified Chronological Timeline**: Multi-source event stream synthesizing FIR incident dates, phone calls, and bank transfers with severity tags.
- **Explainable Risk Intelligence**: Transparent additive scoring model (0–100) breaking down exact evidentiary contributions.
- **Grounded AI Investigation Assistant**: Zero-hallucination conversational query engine providing evidence citations and next-step actions.
- **Automated Case Dossier Reports**: One-click publication-grade dossiers generated in **PDF**, **HTML**, and **JSON** with 13 standard sections.
- **Role-Based Access Control (RBAC)**: Secure user authorization (`ADMIN`, `INVESTIGATOR`, `ANALYST`, `VIEWER`) with PBKDF2 password hashing.
- **Tamper-Evident Audit Trail**: Immutable JSON logging with automatic scrubbing and redaction of passwords, tokens, and sensitive headers.

---

## 4. System Architecture
```
Data Sources (FIRs, CDRs, Bank Ledgers, Facial Imagery)
      │
      ▼
Ingestion Layer (Multipart Uploads, CSV/JSON Parsers, FIR Ingestion)
      │
      ▼
NLP & Parsing (E.164 Normalization, Regex Extraction, Tokenization)
      │
      ▼
Entity & Relationship Extraction (7 Entity Types, 6 Relationship Types)
      │
      ▼
Knowledge Graph Engine (NetworkX MultiDiGraph / Dual Neo4j Ready)
      │
      ▼
Graph Analytics (Degree & Betweenness Centrality, PageRank, Louvain Modularity)
      │
      ▼
Domain Telemetry Analysis (CDR 24h Bursts, Financial Flow Sequences, Timelines)
      │
      ▼
Risk Intelligence (Explainable Additive Scoring Engine, Point Breakdown)
      │
      ▼
AI Investigation Assistant (Grounded Zero-Hallucination Querying & Action Chips)
      │
      ▼
Investigation Report Engine (13-Section Formal Dossiers in PDF/HTML/JSON)
      │
      ▼
Audit Trail & RBAC (Immutable Action Logs, Credential Redaction, PBKDF2 Auth)
```
*(For detailed layer documentation, refer to [ARCHITECTURE.md](file:///c:/Users/laksh_suhwwir/Downloads/criminal_network_engine/criminal_network_engine/ARCHITECTURE.md)).*

---

## 5. Technology Stack
- **Backend Framework**: Python 3.10+ / FastAPI / Uvicorn (ASGI)
- **Graph Processing**: NetworkX `MultiDiGraph` (with optional Neo4j dual driver)
- **Data Validation & Schemas**: Pydantic v2
- **Vector & Biometrics**: NumPy, Pillow, synthetic unit-normalized embeddings
- **Report Generation**: PyMuPDF (`fitz`), pure HTML5/CSS printing engine
- **Security & Authentication**: PBKDF2-HMAC-SHA256 password hashing, cryptographic Bearer tokens
- **Frontend Dashboard**: Single-Page Application (HTML5, Vanilla CSS Design System, Vis.js Network Canvas)
- **Testing Suite**: Pytest, FastAPI TestClient, HTTPX

---

## 6. Investigation Workflow
NODE SENTINEL guides officers through a 7-step investigation lifecycle:
1. **Search**: Enter suspect name, phone, plate, or case ID to locate active intelligence.
2. **Graph**: Inspect the multi-relational network topology and identify key brokers.
3. **CDR**: Uncover anomalous telephony bursts and high-frequency communication links.
4. **Finance**: Track rapid money flows, shell account routing, and high-value transactions.
5. **Timeline**: Review chronological sequence of events across FIRs, calls, and transfers.
6. **AI Copilot**: Query the knowledge base in natural language for instant evidence synthesis.
7. **Report**: Export a standardized, court-admissible 13-section dossier in PDF or HTML.

---

## 7. AI/ML Components
- **Biometric Face Search Engine**: Computes facial feature embeddings and evaluates cosine similarity against enrolled mugshot registries.
- **Topological Centrality & Clustering**: Executes Betweenness Centrality and Louvain Community Detection to uncover criminal hierarchies.
- **Grounded Assistant Engine**: Retrieves graph facts and telemetry records to synthesize zero-hallucination responses with verified source citations.
- **Anomaly Detection Heuristics**: Sliding-window algorithms for communication burst detection and financial velocity spikes.

---

## 8. Knowledge Graph
- **Graph Representation**: Multi-relational directed graph managing 7 node types (`Person`, `Phone`, `Vehicle`, `Case`, `BankAccount`, `Location`, `Organization`) and 6 edge types (`CALLS`, `TRANSFERRED_MONEY`, `INVOLVED_IN`, `LOCATED_AT`, `OWNS`, `OPERATES`).
- **Interactive Capabilities**: Dynamic 1-hop and 2-hop neighborhood expansion, shortest path route finding, node filtering, and centrality overlay.

---

## 9. Face Recognition
- **Enrollment & Probing**: Enrolls biometric identity profiles and accepts probe image uploads.
- **Responsible Output**: Returns match candidates accompanied by similarity confidence percentages and mandatory human verification notices:
  `Possible Match: 'Tariq Ahmad' (100.0% confidence). Requires Investigator Verification. Decision-support indicator only, not proof of guilt.`

---

## 10. CDR Analysis
- **Call Detail Record Telemetry**: Ingests telecom logs, normalizes international numbers (`+91`), and aggregates call frequencies and total duration.
- **Sliding-Window Burst Detector**: Identifies acute communication spikes (>5 calls exchanged within 24 hours), highlighting pre-operational coordination.

---

## 11. Financial Analysis
- **Ledger Ingestion & Processing**: Audits debit/credit volume flows across suspect bank accounts.
- **Velocity & Layering Alerts**: Flags rapid sequences (>3 transfers within 120 minutes) and high-value transfers exceeding investigative thresholds.

---

## 12. Timeline
- **Multi-Source Event Stream**: Interleaves timestamps from police FIR filings, telephony call logs, and banking ledgers on a single chronological axis.
- **Filtering & Badging**: Filters by date ranges and highlights events with severity tags (`CRITICAL`, `ELEVATED`, `NOTICE`).

---

## 13. Risk Intelligence
- **Explainable Additive Formula**: Composite risk score (0–100) calculated transparently from verifiable evidence factors:
  - `+35 pts`: Known High-Risk Registry Indicator
  - `+20 pts`: High Betweenness Centrality Broker
  - `+15 pts`: Telecommunication Burst Detected
  - `+10 pts`: Rapid Financial Flow Sequence
  - `+02 pts`: Multiple FIR Case Involvements

---

## 14. AI Investigation Assistant
- **Read-Only Grounded Inquiry**: Answers natural language questions using live knowledge graph facts without hallucinations.
- **Actionable Guidance**: Embeds one-touch navigation chips (`[View Network]`, `[Analyze Finances]`, `[Open Timeline]`) directly into responses.

---

## 15. Automated Reports
- **13-Section Standard Dossier**: Compiles complete case reports covering executive summary, subject profiles, graph topology, CDR analysis, financial ledger, and chronological timeline.
- **Export Formats**: Instant download in publication-grade **PDF**, styled **HTML**, or raw **JSON**.

---

## 16. Authentication and Audit Trail
- **Role-Based Access Control**: Strict access boundaries across `ADMIN`, `INVESTIGATOR`, `ANALYST`, and `VIEWER`.
- **Tamper-Evident Audit Logging**: Logs every search, report generation, and data query. Automatically scrubs passwords, bearer tokens, and credentials.

---

## 17. Running the Project

### Prerequisites
- Python 3.10 or higher
- Standard pip package manager

### Installation
```bash
# Clone or navigate to the repository
cd criminal_network_engine

# Install dependencies
python -m pip install -r requirements.txt
```

### Start Server
```bash
python -m uvicorn app.main:app --reload
```
- Web Application: `http://127.0.0.1:8000`
- Interactive API Documentation: `http://127.0.0.1:8000/docs`

---

## 18. Demo Credentials

| Username | Password | Role | Permissions |
|---|---|---|---|
| `admin` | `AdminPassword123!` | `ADMIN` | Full access, user management, audit logs |
| `investigator1` | `InvestigatorPassword123!` | `INVESTIGATOR` | Search, face search, graph, CDR, finance, reports |
| `analyst1` | `AnalystPassword123!` | `ANALYST` | Graph analytics, AI assistant queries, timelines |
| `viewer1` | `ViewerPassword123!` | `VIEWER` | Read-only observation |

---

## 19. Testing
Run the complete automated test suite:
```bash
python -m pytest -q
```
**Test Results**: `139 passed, 0 failed, 3 warnings in ~7s`.

---

## 20. Responsible AI / Limitations

> [!IMPORTANT]
> **LEGAL & ETHICAL NOTICE FOR LAW ENFORCEMENT INVESTIGATORS:**
> 1. **Decision Support Only**: NODE SENTINEL is an analytical decision-support system. Risk scores, graph centrality, anomaly indicators, and facial similarity matches **DO NOT constitute proof of guilt, criminal status, or conclusive identity**.
> 2. **Mandatory Human Verification**: All outputs, match suggestions, and automated reports **Require Investigator Verification** prior to any executive action, warrant application, or legal submission.
> 3. **Synthetic Demonstration Data**: All suspect names, telephone numbers, vehicle plates, bank accounts, and FIR case IDs in the sample database are **purely synthetic and fictitious**. Any resemblance to real individuals or cases is strictly coincidental.
> 4. **Scalability Notice**: The prototype includes an in-memory NetworkX graph driver for immediate portability; for multi-million-node deployments, enable the dual Neo4j driver via `NEO4J_URI`.
