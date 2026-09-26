# SentinelName — Case Evidence Platform

Case-scoped investigation workspace on top of the NODE SENTINEL graph engine:
cases → secure evidence upload → extraction → human review → evidence-linked
graph → readiness-gated grounded chat → downloadable reports. All data is
synthetic demo data. Decision-support only — requires investigator verification.

## 1. Local setup

```bash
cd criminal_network_engine
python -m pip install -r requirements.txt   # adds openpyxl, python-docx
python -m uvicorn app.main:app --reload
# UI: http://127.0.0.1:8000   API docs: http://127.0.0.1:8000/docs
python -m pytest -q
```

## 2. Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | insecure dev key | **Must be set in production** (boots refuse prod without it) |
| `AUTH_TOKEN_EXPIRE_MINUTES` | 480 | Bearer token lifetime |
| `DATA_DIR` | `./data` | Private evidence vault (never served statically) |
| `CASES_DB` | `./data/cases.db` | SQLite case store (WAL mode) |
| `MAX_UPLOAD_MB` | 25 | Upload size limit |
| `MAX_DATASET_ROWS` | 20000 | Rows read per dataset (rest ignored + reported) |
| `ALLOW_PUBLIC_REGISTER` | false | Gate for POST /api/auth/register (read-only VIEWER) |
| `RETENTION_DAYS` | 365 | Case retention window for guarded delete |
| `MALWARE_SCAN_CMD` | empty | Optional scanner, `{path}` placeholder, fail-closed |
| `CORS_ORIGINS` | `*` | Pin to frontend origin in production |

`.env.example` documents all of the above (no real secrets).

## 3. Data-processing architecture (text diagram)

```
Investigator UI (static/index.html: My Cases)
  create case -> POST /api/cases (creator = lead, audit CASE_CREATED)
  upload      -> POST /api/cases/:id/documents
                   validate (ext allowlist + magic bytes + size)
                   sha256 -> duplicate? -> private vault data/cases/:id/originals/
                   optional MALWARE_SCAN_CMD -> DOC_UPLOADED / REJECTED / DUPLICATE
  process     -> POST /api/documents/:id/process  (processing_jobs row)
                   unstructured: validating -> extracting_text (page-aware:
                     pdf per-page, docx per-paragraph, txt direct, images OCR)
                     -> extracting_entities (NLPExtractor + provider/confidence)
                     -> extracting_relationships (triplets) -> awaiting_review
                   structured: sniff type -> auto-map columns -> quality report
                     (required/found/missing/invalid/duplicates/ignored+preview)
                     -> awaiting_mapping
  review      -> GET extractions (counts by status) -> approve/verify/reject/
                 uncertain/edit (REVIEW_EXTRACTIONS permission)
              -> GET duplicates -> POST merge (confirmed; edges re-pointed,
                 originals preserved in audit ENTITY_MERGED)
  datasets    -> GET imports/:id (mapping screen) -> POST confirm
                 (minimum requirements enforced) -> evidence graph edges with
                 case_id + import + record provenance, review_status set
  build       -> POST /api/cases/:id/graph/build (approved|verified only,
                 deterministic IDs, idempotent, doc -> graph_updated)
  readiness   -> GET /api/cases/:id/readiness (has vs gaps per question type)
  chat        -> POST /api/cases/:id/assistant/query (membership-checked,
                 case subgraph only, citations, missing-data format,
                 sessions persisted, AI_QUERY audited)
  report      -> POST /api/cases/:id/reports (scope-checked subject,
                 readiness+limitations injected into notes, vault PDF/HTML/JSON,
                 authorized download, GENERATE_REPORT/REPORT_DOWNLOADED audits)
```

SQLite tables: cases, case_members, documents, extractions (+external_id),
dataset_imports, data_quality_issues, processing_jobs, chat_sessions,
chat_messages, generated_reports. Graph stays NetworkX (same schema; service
boundary preserved for a future engine swap).

## 4. Key routes added

- `POST /api/auth/register` (flag-gated), `GET /api/me` (exists as `/api/auth/me`)
- `POST/GET /api/cases`, `GET/PATCH/DELETE /api/cases/:id`,
  `GET/POST/DELETE /api/cases/:id/members`, `GET /api/cases/:id/audit-logs`
- `POST/GET /api/cases/:id/documents`, `GET /api/documents/:id`,
  `GET /api/documents/:id/download`, `POST /api/documents/:id/process`,
  `DELETE /api/documents/:id`, `GET /api/cases/:id/jobs/:jobId`
- `GET /api/cases/:id/imports`, `GET .../imports/:importId`,
  `POST .../imports/:importId/confirm`
- `GET /api/cases/:id/extractions`, `POST .../extractions/:extId/review`,
  `GET /api/cases/:id/duplicates`, `POST .../extractions/merge`,
  `POST /api/cases/:id/graph/build`
- `POST /api/cases/:id/assistant/query`, `GET .../assistant/sessions[/:sid]`,
  `GET /api/cases/:id/readiness`
- `POST/GET /api/cases/:id/reports`, `GET .../reports/:reportId/download`
- `GET /api/network/operations` (ring census + telemetry readiness)

## 5. What the investigator must upload per analysis

| Question | Minimum data |
|---|---|
| Person connections / network | 1 processed report (approved extractions, graph built) |
| Call relationships | CDR csv/xlsx: calling_number, receiving_number, call_start_time |
| Financial links | Financial csv/xlsx: sender_account, receiver_account, amount, transaction_time, transaction_id |
| Vehicle connections | Vehicle csv/xlsx: vehicle_registration (+owner/user) |
| Timeline | Any of the above with timestamps |
| Full report | Built graph + subject; gaps are listed inside the report |

Templates: `sample_data/templates/{cdr,financial,vehicle,location_event}.csv`.
Without the data, the assistant returns the missing-data checklist — never fiction.

## 6. Security checklist (implemented)

- [x] PBKDF2 hashing, bearer tokens, RBAC (ADMIN/INVESTIGATOR/ANALYST/REVIEWER/VIEWER) + case roles (lead/investigator/reviewer/viewer)
- [x] Object-level auth on every case route (membership; writers; managers; admin delete)
- [x] Upload allowlist + magic bytes + size cap + sha256 dedup + private vault + authorized downloads only
- [x] Optional malware hook (fail-closed), safe uuid filenames, traversal guard
- [x] Evidence immutable (corrections = new audit events; merge preserves originals in audit)
- [x] Prompt-injection test (malicious doc text cannot alter answers or leak secrets)
- [x] Cross-case isolation test (shared names stay scoped)
- [x] PII: audit redaction kept; downloads permission-gated
- [ ] Rate limiting, CSRF tokens, at-rest encryption, backups — NOT implemented (see limits)

## 7. Known limitations (honest)

1. **Ephemeral disk on Render**: `data/` and SQLite vanish on redeploy/restart unless a persistent disk is attached. Attach one and set DATA_DIR/CASES_DB accordingly.
2. **No async worker**: pipeline runs inside the request via job rows (same state machine; plug a queue in later).
3. **Rule-based NLP**: no spaCy model installed — bare names without titles/patterns are missed; reviewers see everything first.
4. **Global demo graph + case graphs share one engine**: case scope is enforced by `case_id` tagging + endpoint rule; `/demo/reset` still resets global demo data only.
5. **No rate limiting / CSRF / encryption at rest**; audit log is a capped JSON file (10k entries).
6. **Watchlist faces are real celebrity names** → biometric registry only, never linked as criminals.
7. All sample data is synthetic/fictitious; resemblance is coincidental. Real FIR/CDR/financial/PII data requires legal authorization, institutional review, and the controls in §6 plus a proper deployment review.
