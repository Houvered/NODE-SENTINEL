# -*- coding: utf-8 -*-
"""
Case-scoped evidence platform store for NODE SENTINEL (SQLite, stdlib only).

Tables: cases, case_members, documents, extractions, dataset_imports,
data_quality_issues, processing_jobs, chat_sessions, chat_messages,
generated_reports.

Conventions:
- All IDs are prefixed random hex (CASE_xxxxxxxx, DOC_xxxxxxxx, ...).
- Timestamps are UTC ISO strings.
- Review vocabulary (extractions.review_status): unprocessed, ai-extracted,
  analyst-reviewed, verified, rejected, uncertain.
- Document pipeline (documents.status): uploaded, validating,
  extracting_text, extracting_entities, extracting_relationships,
  awaiting_review, approved, graph_updated, failed.
- Case roles (case_members.case_role): lead, investigator, reviewer, viewer.
- Case statuses: open, under_review, closed, archived.
- Thread-safe via a single lock; WAL mode for concurrent readers.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

REVIEW_STATUSES = {"unprocessed", "ai-extracted", "analyst-reviewed", "verified", "rejected", "uncertain"}
DOC_STATUSES = {"uploaded", "validating", "extracting_text", "extracting_entities",
                "extracting_relationships", "awaiting_review", "approved",
                "graph_updated", "failed"}
CASE_STATUSES = {"open", "under_review", "closed", "archived"}
CASE_ROLES = {"lead", "investigator", "reviewer", "viewer"}
WRITE_ROLES = {"lead", "investigator", "reviewer"}  # may upload/process/review
MANAGE_ROLES = {"lead"}  # may manage members (ADMIN bypasses everywhere)

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  fir_no TEXT DEFAULT '',
  police_station TEXT DEFAULT '',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT,
  retention_until TEXT
);
CREATE TABLE IF NOT EXISTS case_members (
  case_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  case_role TEXT NOT NULL DEFAULT 'investigator',
  added_by TEXT,
  added_at TEXT NOT NULL,
  PRIMARY KEY (case_id, user_id)
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  filename_original TEXT NOT NULL,
  filename_stored TEXT NOT NULL,
  mime TEXT DEFAULT '',
  ext TEXT DEFAULT '',
  size_bytes INTEGER DEFAULT 0,
  sha256 TEXT NOT NULL,
  uploader_id TEXT NOT NULL,
  uploaded_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'uploaded',
  error TEXT DEFAULT '',
  page_count INTEGER DEFAULT 0,
  text_chars INTEGER DEFAULT 0,
  entities_count INTEGER DEFAULT 0,
  rels_count INTEGER DEFAULT 0,
  duplicate_of TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_case ON documents(case_id);
CREATE INDEX IF NOT EXISTS idx_documents_sha ON documents(sha256);
CREATE TABLE IF NOT EXISTS extractions (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  etype TEXT NOT NULL,
  value TEXT NOT NULL DEFAULT '',
  normalized TEXT NOT NULL DEFAULT '',
  subject TEXT DEFAULT '',
  predicate TEXT DEFAULT '',
  object TEXT DEFAULT '',
  evidence_text TEXT DEFAULT '',
  page INTEGER DEFAULT 0,
  char_start INTEGER DEFAULT 0,
  char_end INTEGER DEFAULT 0,
  confidence REAL DEFAULT 0.0,
  provider TEXT DEFAULT '',
  model_version TEXT DEFAULT '',
  review_status TEXT NOT NULL DEFAULT 'ai-extracted',
  reviewer_id TEXT,
  reviewed_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extractions_case ON extractions(case_id);
CREATE INDEX IF NOT EXISTS idx_extractions_doc ON extractions(document_id);
CREATE INDEX IF NOT EXISTS idx_extractions_review ON extractions(case_id, review_status);
CREATE TABLE IF NOT EXISTS dataset_imports (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  document_id TEXT,
  dataset_type TEXT NOT NULL,
  mapping_json TEXT DEFAULT '{}',
  total_rows INTEGER DEFAULT 0,
  imported_rows INTEGER DEFAULT 0,
  rejected_rows INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'awaiting_mapping',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS data_quality_issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  import_id TEXT NOT NULL,
  row_no INTEGER DEFAULT 0,
  column_name TEXT DEFAULT '',
  issue TEXT NOT NULL,
  detail TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dqi_import ON data_quality_issues(import_id);
CREATE TABLE IF NOT EXISTS processing_jobs (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  document_id TEXT,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  progress INTEGER DEFAULT 0,
  result_json TEXT DEFAULT '{}',
  error TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_sessions (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  citations_json TEXT DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id);
CREATE TABLE IF NOT EXISTS generated_reports (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  format TEXT NOT NULL DEFAULT 'pdf',
  file_name TEXT DEFAULT '',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  meta_json TEXT DEFAULT '{}'
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class CaseStore:
    """SQLite-backed case platform store."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.CASES_DB
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        return con

    def _init_schema(self) -> None:
        with self._lock, self._connect() as con:
            con.executescript(SCHEMA)
            # Lightweight migrations (idempotent).
            for stmt in (
                "ALTER TABLE extractions ADD COLUMN external_id TEXT DEFAULT ''",
            ):
                try:
                    con.execute(stmt)
                except Exception:
                    pass

    # -- generic helpers -------------------------------------------------
    def _one(self, sql: str, args=()) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as con:
            row = con.execute(sql, args).fetchone()
            return dict(row) if row else None

    def _all(self, sql: str, args=()) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as con:
            return [dict(r) for r in con.execute(sql, args).fetchall()]

    def _exec(self, sql: str, args=()) -> int:
        with self._lock, self._connect() as con:
            cur = con.execute(sql, args)
            con.commit()
            return cur.rowcount

    # -- cases -----------------------------------------------------------
    def create_case(self, title: str, description: str, created_by: str,
                    fir_no: str = "", police_station: str = "",
                    retention_days: Optional[int] = None) -> Dict[str, Any]:
        from datetime import timedelta
        cid, now = _nid("CASE"), _now()
        retention_until = (datetime.now(timezone.utc) + timedelta(
            days=retention_days if retention_days is not None else settings.RETENTION_DAYS)).isoformat()
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT INTO cases (id,title,description,status,fir_no,police_station,created_by,created_at,updated_at,retention_until)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (cid, title.strip(), description or "", "open", fir_no or "", police_station or "",
                 created_by, now, now, retention_until))
            con.execute(
                "INSERT INTO case_members (case_id,user_id,case_role,added_by,added_at) VALUES (?,?,?,?,?)",
                (cid, created_by, "lead", created_by, now))
            con.commit()
        return self.get_case(cid)

    def get_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM cases WHERE id=?", (case_id,))

    def list_cases_for_user(self, user_id: str, is_admin: bool) -> List[Dict[str, Any]]:
        if is_admin:
            return self._all("SELECT * FROM cases ORDER BY updated_at DESC")
        return self._all(
            "SELECT c.* FROM cases c JOIN case_members m ON m.case_id=c.id"
            " WHERE m.user_id=? ORDER BY c.updated_at DESC", (user_id,))

    def update_case(self, case_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        allowed = {"title", "description", "status", "fir_no", "police_station"}
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                if k == "status" and v not in CASE_STATUSES:
                    raise ValueError(f"Invalid status '{v}'")
                sets.append(f"{k}=?")
                args.append(v)
        if not sets:
            return self.get_case(case_id)
        if "status" in fields and fields["status"] == "archived":
            sets.append("archived_at=?")
            args.append(_now())
        sets.append("updated_at=?")
        args.append(_now())
        args.append(case_id)
        self._exec(f"UPDATE cases SET {', '.join(sets)} WHERE id=?", args)
        return self.get_case(case_id)

    def delete_case(self, case_id: str) -> None:
        # Remove DB rows; file dir removal is handled by the route layer.
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM chat_messages WHERE session_id IN (SELECT id FROM chat_sessions WHERE case_id=?)", (case_id,))
            con.execute("DELETE FROM chat_sessions WHERE case_id=?", (case_id,))
            con.execute("DELETE FROM generated_reports WHERE case_id=?", (case_id,))
            con.execute("DELETE FROM processing_jobs WHERE case_id=?", (case_id,))
            con.execute("DELETE FROM data_quality_issues WHERE import_id IN (SELECT id FROM dataset_imports WHERE case_id=?)", (case_id,))
            con.execute("DELETE FROM dataset_imports WHERE case_id=?", (case_id,))
            con.execute("DELETE FROM extractions WHERE case_id=?", (case_id,))
            con.execute("DELETE FROM documents WHERE case_id=?", (case_id,))
            con.execute("DELETE FROM case_members WHERE case_id=?", (case_id,))
            con.execute("DELETE FROM cases WHERE id=?", (case_id,))
            con.commit()

    def touch_case(self, case_id: str) -> None:
        self._exec("UPDATE cases SET updated_at=? WHERE id=?", (_now(), case_id))

    # -- members ---------------------------------------------------------
    def get_membership(self, case_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM case_members WHERE case_id=? AND user_id=?", (case_id, user_id))

    def list_members(self, case_id: str) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM case_members WHERE case_id=? ORDER BY added_at", (case_id,))

    def add_member(self, case_id: str, user_id: str, case_role: str, added_by: str) -> Dict[str, Any]:
        if case_role not in CASE_ROLES:
            raise ValueError(f"Invalid case role '{case_role}'")
        self._exec(
            "INSERT INTO case_members (case_id,user_id,case_role,added_by,added_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(case_id,user_id) DO UPDATE SET case_role=excluded.case_role",
            (case_id, user_id, case_role, added_by, _now()))
        self.touch_case(case_id)
        return self.get_membership(case_id, user_id)

    def remove_member(self, case_id: str, user_id: str) -> None:
        self._exec("DELETE FROM case_members WHERE case_id=? AND user_id=?", (case_id, user_id))

    # -- documents -------------------------------------------------------
    def add_document(self, **kw) -> Dict[str, Any]:
        kw.setdefault("id", _nid("DOC"))
        kw.setdefault("uploaded_at", _now())
        kw.setdefault("status", "uploaded")
        cols = ("id,case_id,filename_original,filename_stored,mime,ext,size_bytes,"
                "sha256,uploader_id,uploaded_at,status,error,page_count,text_chars,"
                "entities_count,rels_count,duplicate_of")
        vals = [kw.get(c, "" if c not in ("size_bytes", "page_count", "text_chars",
                                          "entities_count", "rels_count") else 0) for c in cols.split(",")]
        self._exec(f"INSERT INTO documents ({cols}) VALUES ({','.join('?' * len(vals))})", vals)
        self.touch_case(kw["case_id"])
        return self.get_document(kw["id"])

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM documents WHERE id=?", (doc_id,))

    def list_documents(self, case_id: str) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM documents WHERE case_id=? ORDER BY uploaded_at DESC", (case_id,))

    def find_by_sha(self, case_id: str, sha256: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM documents WHERE case_id=? AND sha256=? ORDER BY uploaded_at DESC LIMIT 1",
                         (case_id, sha256))

    def set_doc_status(self, doc_id: str, status: str, error: str = "", **counts) -> None:
        if status not in DOC_STATUSES:
            raise ValueError(f"Invalid document status '{status}'")
        sets, args = ["status=?", "error=?"], [status, error]
        for k in ("page_count", "text_chars", "entities_count", "rels_count"):
            if k in counts:
                sets.append(f"{k}=?")
                args.append(counts[k])
        args.append(doc_id)
        self._exec(f"UPDATE documents SET {', '.join(sets)} WHERE id=?", args)

    def delete_document(self, doc_id: str) -> None:
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM extractions WHERE document_id=?", (doc_id,))
            con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            con.commit()

    # -- extractions -----------------------------------------------------
    def add_extraction(self, **kw) -> Dict[str, Any]:
        kw.setdefault("id", _nid("EXT"))
        kw.setdefault("created_at", _now())
        kw.setdefault("review_status", "ai-extracted")
        cols = ("id,case_id,document_id,kind,etype,value,normalized,external_id,subject,predicate,"
                "object,evidence_text,page,char_start,char_end,confidence,provider,"
                "model_version,review_status,reviewer_id,reviewed_at,created_at")
        vals = [kw.get(c, 0 if c in ("page", "char_start", "char_end", "confidence") else "") for c in cols.split(",")]
        self._exec(f"INSERT INTO extractions ({cols}) VALUES ({','.join('?' * len(vals))})", vals)
        return self.get_extraction(kw["id"])

    def get_extraction(self, ext_id: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM extractions WHERE id=?", (ext_id,))

    def list_extractions(self, case_id: str, review_status: Optional[str] = None,
                         kind: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM extractions WHERE case_id=?", [case_id]
        if review_status:
            sql += " AND review_status=?"
            args.append(review_status)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))
        return self._all(sql, args)

    def set_review(self, ext_id: str, review_status: str, reviewer_id: str,
                   normalized: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if review_status not in REVIEW_STATUSES:
            raise ValueError(f"Invalid review status '{review_status}'")
        if normalized is not None:
            self._exec("UPDATE extractions SET review_status=?, reviewer_id=?, reviewed_at=?, normalized=? WHERE id=?",
                       (review_status, reviewer_id, _now(), normalized, ext_id))
        else:
            self._exec("UPDATE extractions SET review_status=?, reviewer_id=?, reviewed_at=? WHERE id=?",
                       (review_status, reviewer_id, _now(), ext_id))
        return self.get_extraction(ext_id)

    def duplicate_suggestions(self, case_id: str) -> List[Dict[str, Any]]:
        """Groups of extractions sharing a normalized value (needs confirmation)."""
        rows = self._all(
            "SELECT normalized, etype, GROUP_CONCAT(id) AS ids, COUNT(*) AS n FROM extractions"
            " WHERE case_id=? AND kind='entity' AND normalized<>''"
            " GROUP BY normalized, etype HAVING n > 1 ORDER BY n DESC LIMIT 50", (case_id,))
        out = []
        for r in rows:
            ids = (r["ids"] or "").split(",")
            items = [self.get_extraction(i) for i in ids]
            out.append({"normalized": r["normalized"], "etype": r["etype"],
                        "count": r["n"], "extractions": [i for i in items if i]})
        return out

    # -- dataset imports -------------------------------------------------
    def add_import(self, **kw) -> Dict[str, Any]:
        kw.setdefault("id", _nid("IMP"))
        kw.setdefault("created_at", _now())
        kw.setdefault("status", "awaiting_mapping")
        cols = "id,case_id,document_id,dataset_type,mapping_json,total_rows,imported_rows,rejected_rows,status,created_by,created_at"
        vals = [kw.get(c, 0 if c in ("total_rows", "imported_rows", "rejected_rows") else "") for c in cols.split(",")]
        self._exec(f"INSERT INTO dataset_imports ({cols}) VALUES ({','.join('?' * len(vals))})", vals)
        return self.get_import(kw["id"])

    def get_import(self, import_id: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM dataset_imports WHERE id=?", (import_id,))

    def list_imports(self, case_id: str) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM dataset_imports WHERE case_id=? ORDER BY created_at DESC", (case_id,))

    def update_import(self, import_id: str, **kw) -> Optional[Dict[str, Any]]:
        allowed = {"mapping_json", "total_rows", "imported_rows", "rejected_rows", "status", "document_id"}
        sets, args = [], []
        for k, v in kw.items():
            if k in allowed:
                sets.append(f"{k}=?")
                args.append(v)
        if sets:
            args.append(import_id)
            self._exec(f"UPDATE dataset_imports SET {', '.join(sets)} WHERE id=?", args)
        return self.get_import(import_id)

    def add_quality_issue(self, import_id: str, row_no: int, column_name: str, issue: str, detail: str = "") -> None:
        self._exec("INSERT INTO data_quality_issues (import_id,row_no,column_name,issue,detail) VALUES (?,?,?,?,?)",
                   (import_id, row_no, column_name or "", issue, detail or ""))

    def list_quality_issues(self, import_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM data_quality_issues WHERE import_id=? ORDER BY id LIMIT ?",
                         (import_id, max(1, min(limit, 1000))))

    # -- jobs ------------------------------------------------------------
    def add_job(self, case_id: str, job_type: str, document_id: str = "") -> Dict[str, Any]:
        jid, now = _nid("JOB"), _now()
        self._exec("INSERT INTO processing_jobs (id,case_id,document_id,job_type,status,progress,result_json,error,created_at,updated_at)"
                   " VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (jid, case_id, document_id, job_type, "queued", 0, "{}", "", now, now))
        return self.get_job(jid)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM processing_jobs WHERE id=?", (job_id,))

    def update_job(self, job_id: str, status: Optional[str] = None, progress: Optional[int] = None,
                   result_json: Optional[str] = None, error: Optional[str] = None) -> Optional[Dict[str, Any]]:
        sets, args = ["updated_at=?"], [_now()]
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if progress is not None:
            sets.append("progress=?")
            args.append(progress)
        if result_json is not None:
            sets.append("result_json=?")
            args.append(result_json)
        if error is not None:
            sets.append("error=?")
            args.append(error)
        args.append(job_id)
        self._exec(f"UPDATE processing_jobs SET {', '.join(sets)} WHERE id=?", args)
        return self.get_job(job_id)

    # -- chat ------------------------------------------------------------
    def add_session(self, case_id: str, user_id: str) -> Dict[str, Any]:
        sid = _nid("CHAT")
        self._exec("INSERT INTO chat_sessions (id,case_id,user_id,created_at) VALUES (?,?,?,?)",
                   (sid, case_id, user_id, _now()))
        return {"id": sid, "case_id": case_id, "user_id": user_id}

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM chat_sessions WHERE id=?", (session_id,))

    def list_sessions(self, case_id: str, user_id: str) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM chat_sessions WHERE case_id=? AND user_id=? ORDER BY created_at DESC",
                         (case_id, user_id))

    def add_message(self, session_id: str, role: str, content: str, citations: str = "[]") -> None:
        self._exec("INSERT INTO chat_messages (session_id,role,content,citations_json,created_at) VALUES (?,?,?,?,?)",
                   (session_id, role, content, citations, _now()))

    def list_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                         (session_id, max(1, min(limit, 500))))[::-1]

    # -- reports ---------------------------------------------------------
    def add_report(self, **kw) -> Dict[str, Any]:
        kw.setdefault("id", _nid("REP"))
        kw.setdefault("created_at", _now())
        cols = "id,case_id,subject,format,file_name,created_by,created_at,meta_json"
        vals = [kw.get(c, "") for c in cols.split(",")]
        self._exec(f"INSERT INTO generated_reports ({cols}) VALUES ({','.join('?' * len(vals))})", vals)
        return self._one("SELECT * FROM generated_reports WHERE id=?", (kw["id"],))

    def list_reports(self, case_id: str) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM generated_reports WHERE case_id=? ORDER BY created_at DESC", (case_id,))

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        return self._one("SELECT * FROM generated_reports WHERE id=?", (report_id,))


_store: Optional[CaseStore] = None
_store_lock = threading.Lock()


def get_case_store() -> CaseStore:
    """Process-wide singleton (tests may override CASES_DB via env before import)."""
    global _store
    with _store_lock:
        if _store is None:
            _store = CaseStore()
        return _store
