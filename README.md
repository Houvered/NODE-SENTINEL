# AI-Assisted Criminal Network Analysis & Knowledge Graph System (NODE SENTINEL)

**NODE SENTINEL** is an evolutionary prototype for an AI-Assisted Criminal Network Analysis & Knowledge Graph System built with **Python (FastAPI)**, **Neo4j** (with an in-memory **NetworkX** dual fallback), **spaCy/Regex NLP**, statistical anomaly detection, and an interactive **Vis.js + Tailwind CSS** investigator dashboard.

---

## 🌟 Key Architecture & Features

1. **Dual Graph Engine (`app/core/graph_engine.py`)**:
   - Out-of-the-box in-memory **NetworkX** `MultiDiGraph` fallback allowing full graph operation without requiring a running Neo4j instance.
   - Automatic connection detection for **Neo4j** database when configured in environment (`NEO4J_URI`).

2. **NLP Entity & Triplet Extraction (`app/core/nlp_extractor.py`)**:
   - Regex domain extractors for Indian Vehicle Registration Plates (e.g. `UP14AB1234`), Phone Numbers (`+919876543210`), Bank Accounts (`ACC987654321`), Monetary Values, and Case Codes.
   - spaCy / Rule-based Subject-Predicate-Object (SPO) dependency triplet parser for relationship extraction from raw Police First Information Reports (FIRs).

3. **Entity Resolution & Deduplication (`app/core/entity_resolution.py`)**:
   - Normalization of phone numbers, vehicle plates, and bank accounts.
   - Name similarity matching (e.g. linking "Rahul S." to "Rahul Sharma") and canonical node merging with automatic re-wiring of edge relationships.

4. **Graph Analytics & Anomaly Detection (`app/core/graph_analytics.py` & `app/core/anomaly_detector.py`)**:
   - Metric computation: **Degree Centrality**, **Betweenness Centrality** (identifying money mules & key brokers), **PageRank**, and **Louvain Community Detection**.
   - Statistical anomaly flags:
     - **Call Burst Detection**: > 3x mean communication frequency.
     - **Financial Anomaly**: Transactions > $2\sigma$ above network average.
     - **Co-Location Clusters**: Multiple suspects appearing at the same location within short time windows.
   - **Investigative Risk Score (0-100)**: Composite risk engine providing clear, natural-language evidence explanations.

5. **Interactive Investigator Dashboard (`static/index.html`)**:
   - Vis.js interactive network graph canvas with color-coded nodes and physics layout.
   - Real-time suspect/vehicle search & 1-hop neighborhood expansion.
   - Entity Intelligence Dossier sidebar with risk score gauges and connected target trees.
   - Live Anomaly Alert Feed & FIR Raw Text Ingestion Modal.
s
t
---

## 📂 Project Directory Structure

```
criminal_network_engine/
├── app/
│   ├── __init__.py
│   ├── config.py              # System configuration & environment settings
│   ├── main.py                # FastAPI entrypoint & static dashboard server
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes_ingest.py    # Text parsing & synthetic batch loading endpoints
│   │   ├── routes_graph.py     # Graph exploration, neighborhood search & dossier
│   │   ├── routes_analytics.py # Centrality rankings & community clusters
│   │   └── routes_alerts.py    # Anomaly alerts feed
│   ├── core/
│   │   ├── nlp_extractor.py    # Regex & spaCy entity & triplet extraction
│   │   ├── entity_resolution.py# Deduplication & canonical entity linking
│   │   ├── graph_engine.py     # Neo4j & NetworkX dual graph engine driver
│   │   ├── graph_analytics.py  # Centrality & Louvain community detection
│   │   └── anomaly_detector.py # Anomaly rules & composite risk score engine
│   └── models/
│       ├── schemas.py          # Pydantic request/response schemas
│       └── graph_models.py     # Entity & Edge dataclasses
├── sample_data/
│   ├── fir_reports.json        # Synthetic police FIR reports
│   ├── cdr_records.csv         # Call Detail Records (CDR)
│   ├── transactions.csv      # Financial transaction logs
│   └── criminal_records.csv    # Suspect registry & past cases
├── static/
│   └── index.html              # Vis.js + Tailwind CSS Investigator Dashboard
├── requirements.txt
├── docker-compose.yml          # Docker service setup (Neo4j + App)
└── README.md
```

---

## 🚀 Quick Start Guide

### Option 1: Running Locally with Python (NetworkX Fallback)

1. **Navigate to project directory**:
   ```bash
   cd criminal_network_engine
   ```

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Start the FastAPI server**:
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

4. **Access the Application**:
   - **Investigator Dashboard**: Open `http://localhost:8000` in your web browser.
   - **Swagger API Docs**: Open `http://localhost:8000/docs`.

---

### Option 2: Running with Docker Compose & Neo4j Database

To run the system connected to a live Neo4j Graph Database container:

```bash
docker-compose up --build
```
- **FastAPI Web App & Dashboard**: `http://localhost:8000`
- **Neo4j Browser Console**: `http://localhost:7474` (User: `neo4j`, Password: `password123`)

---

## 🔌 API Endpoints Summary

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/ingest/text` | Parse raw unstructured FIR text $\rightarrow$ extract entities/triplets $\rightarrow$ insert to graph |
| `POST` | `/api/ingest/batch` | Load pre-populated synthetic sample dataset from CSVs/JSON |
| `GET` | `/api/network/graph` | Fetch full graph payload formatted for Vis.js canvas |
| `GET` | `/api/network/search?query=Rahul` | Search graph nodes and return N-hop expanded subgraph |
| `GET` | `/api/entity/{id}/dossier` | Generate suspect profile, risk score breakdown & connected targets |
| `GET` | `/api/network/analytics/centrality` | Ranked table of key influencers (Degree, Betweenness, PageRank) |
| `GET` | `/api/network/analytics/communities` | Louvain community clusters partition breakdown |
| `GET` | `/api/network/alerts` | Active anomaly alerts (call bursts, transaction spikes, co-locations) |

---

## 📊 Sample Verification Workflow

1. Open `http://localhost:8000` in browser.
2. Click on the node **Rahul Sharma** or **Amit Verma** on the visual graph canvas.
3. Observe the right sidebar update with their **Investigative Risk Score**, risk factor breakdown (e.g. "+35.0 High Risk Suspect", "+25.0 Financial Spike Anomaly"), and centrality measures.
4. Click on **Active Alerts** (warning icon in left navigation) to inspect financial anomalies (e.g. transaction spike of ₹450,000) and call bursts.
5. Click **Ingest FIR Text**, paste a new report narrative, and click **Run NLP Extraction & Merge** to watch new entities automatically link to the live graph canvas!
