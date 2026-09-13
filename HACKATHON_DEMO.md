# NODE SENTINEL — 5 to 7 Minute Hackathon Demonstration Guide

> **Target Audience**: Hackathon Judges, Law Enforcement Officials, Technical Evaluators  
> **Presenter Persona**: Lead Intelligence Systems Architect  
> **Synthetic Target Case**: Operation Hawala Falcon (Primary Target: *Tariq Ahmad*, Case: *FIR-2024-311*)

---

## ⏱️ Live Demonstration Sequence

| Step | Action | Duration | Primary Focus |
|---|---|---|---|
| **1** | Open NODE SENTINEL | 0:00 - 0:30 | Sleek intelligence dashboard, startup state & data integrity |
| **2** | Search for a Person | 0:30 - 1:00 | Universal multi-modal indexed search |
| **3** | Show Knowledge Graph | 1:00 - 1:30 | Graph topology, node hierarchy & broker identification |
| **4** | Expand Connections | 1:30 - 2:00 | 1-hop and 2-hop neighbor expansion & pathfinder |
| **5** | Show Risk Intelligence | 2:00 - 2:30 | Explainable additive risk breakdown (no black boxes) |
| **6** | Analyze CDR | 2:30 - 3:00 | Telecom call burst detection & frequency analysis |
| **7** | Analyze Financial Activity | 3:00 - 3:30 | Rapid transaction sequences & hawala money routing |
| **8** | Open Investigation Timeline | 3:30 - 4:00 | Multi-source chronological event fusion |
| **9** | Ask AI Investigation Assistant | 4:00 - 4:30 | Zero-hallucination grounded natural language queries |
| **10** | Generate Investigation Report | 4:30 - 5:00 | 13-section publication-grade PDF/HTML dossier generation |
| **11** | Show Audit Trail | 5:00 - 5:30 | Immutable audit logging & automatic credential redaction |

---

## 🎬 Step-by-Step Live Presentation Script

### Step 1: Open NODE SENTINEL

- **WHAT TO CLICK**:
  - Open your browser to `http://127.0.0.1:8000`.
  - Point to the top navigation header showing the active investigation workflow pipeline (`Search → Graph → CDR → Finance → Timeline → AI Copilot → Report`).
- **WHAT THE JUDGE SHOULD NOTICE**:
  - A responsive dark intelligence-dashboard interface with high contrast, clear visual hierarchy, and immediate load of the 28-node / 47-edge synthetic syndicate network.
  - The left sidebar organized into 5 structured categories: *Investigation, Analytics, Intelligence, Administration, Data Ingestion*.
  - The live status banner: `CONNECTED · ENGINE READY`.
- **WHY IT MATTERS**:
  - Law enforcement analysts operate under high stress. A clean, purpose-built interface avoids cognitive fatigue while keeping every critical investigative tool one click away.

---

### Step 2: Search for a Person

- **WHAT TO CLICK**:
  - Click the **Universal Search Bar** at the top or in the left sidebar.
  - Type `Tariq` (or exact phone `+919811223344` or vehicle plate `DL01AB9988`).
  - Press Enter or click the resolved search card: `Tariq Ahmad (PERSON_TARIQ_AHMAD)`.
- **WHAT THE JUDGE SHOULD NOTICE**:
  - The search engine instantly indexes across people, phone numbers, vehicle registrations, bank accounts, and FIR case IDs.
  - The search card shows resolved identity attributes, phone numbers, active case links (`FIR-2024-311`, `FIR-2024-405`), and match confidence.
  - The right-hand **Intelligence Dossier** opens immediately with Tariq Ahmad's full profile.
- **WHY IT MATTERS**:
  - Investigators frequently start with fragmented clues (e.g., just a phone number or partial plate). Universal multi-modal search eliminates siloed database searches.

---

### Step 3: Show Knowledge Graph

- **WHAT TO CLICK**:
  - Click **Knowledge Graph** in the sidebar or top navigation.
  - Click the **Betweenness Centrality** chip or node type filter buttons (`Person`, `Phone`, `Vehicle`, `Bank/Acct`, `Case`).
- **WHAT THE JUDGE SHOULD NOTICE**:
  - The central canvas displays an interactive force-directed graph.
  - Tariq Ahmad appears as a prominent central hub bridging the criminal street operatives with the hawala financial routing arm.
  - Clicking any node focuses it with subtle glow rings while gently dimming unrelated background nodes.
- **WHY IT MATTERS**:
  - Organized crime syndicates intentionally isolate their leadership behind tiers of intermediaries. Graph centrality algorithms uncover the hidden brokers and kingpins who rarely execute crimes directly.

---

### Step 4: Expand Connections

- **WHAT TO CLICK**:
  - With Tariq Ahmad selected, click the **[Expand Connections]** button in the graph toolbar.
  - Alternatively, click **[Show Direct Connections]** or **[Find Connection]** to find the shortest path between Tariq and suspect `Kabir Mirza`.
- **WHAT THE JUDGE SHOULD NOTICE**:
  - The graph dynamically retrieves 1-hop and 2-hop neighbors via REST API without reloading or stuttering.
  - Edge labels clearly identify relational semantics: `CALLS`, `TRANSFERRED_MONEY`, `INVOLVED_IN`, `OWNS`, `OPERATES`.
  - The shortest path finder highlights the exact multi-hop route between two suspects.
- **WHY IT MATTERS**:
  - Enables progressive disclosure: investigators start from a single suspect and systematically unravel the wider syndicate without visual clutter.

---

### Step 5: Show Risk Intelligence

- **WHAT TO CLICK**:
  - In the right-hand **Intelligence Dossier**, scroll to the **Risk Assessment** card (Score: `82 / 100 — ELEVATED`).
  - Click to expand the **Transparent Evidence Factor Breakdown**.
- **WHAT THE JUDGE SHOULD NOTICE**:
  - The score is not an opaque black-box number. An additive evidence breakdown explains every point:
    - `+35 pts` Known High-Risk Registry Indicator
    - `+20 pts` High Betweenness Centrality Broker
    - `+15 pts` Communication Burst Detected (>5 calls in 24h)
    - `+10 pts` Rapid Financial Flow Sequence
    - `+02 pts` Connected to Multiple Active FIR Cases
  - Clear responsible AI wording: `Decision Support Indicator · Requires Investigator Verification · Not proof of guilt`.
- **WHY IT MATTERS**:
  - Court admissibility requires explainable evidence. Judges and prosecutors cannot accept black-box scores; every risk point must trace back to verifiable facts.

---

### Step 6: Analyze CDR (Call Detail Records)

- **WHAT TO CLICK**:
  - Click **CDR Analysis** in the left sidebar or top navigation.
  - Select suspect Tariq Ahmad (`+919811223344`) or click **Analyze Calls**.
- **WHAT THE JUDGE SHOULD NOTICE**:
  - Interactive call summary metrics: total calls, total duration, top interlocutors, and incoming vs. outgoing ratio.
  - The **Communication Burst Detector** flags an anomalous spike: 12 calls exchanged between Tariq and co-conspirator Kabir Mirza in a 24-hour window directly preceding the incident date of `FIR-2024-311`.
- **WHY IT MATTERS**:
  - Sudden surges in call frequency between co-conspirators immediately precede operational criminal events, providing temporal corroboration for conspiracy charges.

---

### Step 7: Analyze Financial Activity

- **WHAT TO CLICK**:
  - Click **Financial Analysis** in the sidebar or top navigation.
  - Select Bank Account `ACC990188231` or click **Analyze Finances**.
- **WHAT THE JUDGE SHOULD NOTICE**:
  - Total credit vs. debit volume analysis with counterparty breakdown.
  - The **Rapid Velocity Sequence** detector flags 4 transactions totaling ₹18,50,000 transferred between shell accounts within 120 minutes.
  - Visual layering detection identifying structuring / smurfing patterns.
- **WHY IT MATTERS**:
  - Financial trails are the most objective evidence in organized fraud and hawala cases. Rapid velocity detection uncovers automated money laundering rings in seconds.

---

### Step 8: Open Investigation Timeline

- **WHAT TO CLICK**:
  - Click **Timeline** in the sidebar or top navigation.
  - Select severity filter chips (`All`, `Critical`, `Elevated`).
- **WHAT THE JUDGE SHOULD NOTICE**:
  - A unified chronological stream interleaving FIR incident dates, telephony calls, and bank wire transfers on a single axis.
  - High-severity events are color-coded with badges and linked directly back to the underlying evidence records.
- **WHY IT MATTERS**:
  - Establishing a tight sequence of events (e.g., money transfer at 14:00 → phone call at 14:15 → physical incident at 15:00) provides the chronological backbone of prosecutorial filings.

---

### Step 9: Ask AI Investigation Assistant

- **WHAT TO CLICK**:
  - Click **AI Copilot** in the sidebar or top navigation.
  - Type or click a suggested prompt: `Who is connected to Tariq Ahmad and what are their phone numbers?`
- **WHAT THE JUDGE SHOULD NOTICE**:
  - The AI Assistant generates a structured, grounded analytical response citing specific graph facts and case numbers.
  - Zero hallucination: it only answers from verified records in the knowledge base.
  - Includes interactive next-step action chips (e.g., `[View Network]`, `[Analyze Finances]`, `[Open Timeline]`).
- **WHY IT MATTERS**:
  - Serves as an autonomous junior analyst, synthesizing complex multi-source data and assisting officers in formulating investigative hypotheses without risking factual fabrications.

---

### Step 10: Generate Investigation Report

- **WHAT TO CLICK**:
  - Click **Reports** in the sidebar or top navigation.
  - Click **[Generate Case Report]** for `FIR-2024-311` (or select format **PDF** / **HTML**).
- **WHAT THE JUDGE SHOULD NOTICE**:
  - The system compiles a publication-grade, 13-section formal investigation dossier within milliseconds.
  - The report includes executive summary, subject identity profiles, graph metrics, CDR communication analysis, financial ledger summaries, chronological timeline, and legal disclaimers.
- **WHY IT MATTERS**:
  - Manual report compilation takes investigators 8–16 hours per case. Automated, standardized report generation allows immediate dissemination to supervisory leadership and court prosecutors.

---

### Step 11: Show Audit Trail

- **WHAT TO CLICK**:
  - Click **Audit Trail** in the sidebar (or log in with `admin` / `AdminPassword123!`).
  - Inspect the live audit ledger.
- **WHAT THE JUDGE SHOULD NOTICE**:
  - Every investigator action (searches, face queries, graph expansions, report downloads) is immutably logged with ISO timestamps, user ID, role, and action type.
  - Passwords, cryptographic tokens, and sensitive credentials are automatically intercepted and replaced with `[REDACTED]`.
- **WHY IT MATTERS**:
  - Preserves evidentiary chain-of-custody, satisfies regulatory oversight, and prevents internal data leaks or unauthorized surveillance.

---

## 🎯 Quick Reference Factsheet for Q&A

- **Primary Suspect**: Tariq Ahmad (`PERSON_TARIQ_AHMAD`) — Kingpin / Broker
- **Associate**: Kabir Mirza (`PERSON_KABIR_MIRZA`) — Operational Facilitator
- **Key Bank Account**: `ACC990188231` — Hawala Clearing Account
- **Key Telephone**: `+919811223344` (Tariq Ahmad)
- **Active Case**: `FIR-2024-311` (Cyber Financial Fraud Syndicate)
- **Test Suite**: `python -m pytest -q` (139 passed)
- **Backend**: FastAPI + NetworkX + PyMuPDF + Pydantic v2
