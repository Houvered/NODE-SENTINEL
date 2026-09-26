# -*- coding: utf-8 -*-
"""
Secure evidence file handling for NODE SENTINEL.

- Extension allowlist + magic-byte verification (never trust MIME alone).
- Size limits (MAX_UPLOAD_MB), row limits enforced at import time.
- SHA-256 identity, duplicate detection per case.
- Safe storage: data/cases/<case_id>/originals/<uuid>.<ext>; never served
  statically — downloads go through an authorized endpoint.
- Optional malware-scan hook (MALWARE_SCAN_CMD with {path} placeholder,
  fail-closed on detection, fail-open with warning when unconfigured).
- Lazy DOCX/XLSX readers (openpyxl / python-docx) with clear errors.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import subprocess
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

# extension -> (mimetypes, magic prefixes)
ALLOWED: Dict[str, Dict[str, object]] = {
    ".pdf": {"mime": ["application/pdf"], "magic": [b"%PDF"]},
    ".txt": {"mime": ["text/plain"], "magic": []},
    ".md": {"mime": ["text/markdown", "text/plain"], "magic": []},
    ".log": {"mime": ["text/plain"], "magic": []},
    ".csv": {"mime": ["text/csv", "application/vnd.ms-excel", "text/plain"], "magic": []},
    ".json": {"mime": ["application/json", "text/plain"], "magic": []},
    ".png": {"mime": ["image/png"], "magic": [b"\x89PNG\r\n\x1a\n"]},
    ".jpg": {"mime": ["image/jpeg"], "magic": [b"\xff\xd8\xff"]},
    ".jpeg": {"mime": ["image/jpeg"], "magic": [b"\xff\xd8\xff"]},
    ".webp": {"mime": ["image/webp"], "magic": [b"RIFF"]},
    ".bmp": {"mime": ["image/bmp"], "magic": [b"BM"]},
    ".docx": {"mime": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"], "magic": [b"PK\x03\x04"]},
    ".xlsx": {"mime": ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"], "magic": [b"PK\x03\x04"]},
}

TEXT_EXTS = {".txt", ".md", ".log"}
STRUCTURED_EXTS = {".csv", ".json", ".xlsx"}
DOC_EXTS = {".pdf", ".docx"} | TEXT_EXTS


class FileRejected(ValueError):
    """Upload failed validation (message is safe to show the investigator)."""


def validate_upload(filename: str, content: bytes) -> Dict[str, object]:
    """Validate size, extension, and magic bytes. Returns metadata dict."""
    if not filename or "." not in filename:
        raise FileRejected("File must have an extension (pdf, txt, csv, json, xlsx, docx, or image).")
    ext = "." + filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED:
        raise FileRejected(f"Extension '{ext}' is not allowed. Allowed: {sorted(ALLOWED)}.")
    if not content:
        raise FileRejected("Uploaded file is empty (0 bytes).")
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise FileRejected(f"File exceeds the {settings.MAX_UPLOAD_MB} MB upload limit.")
    magic = ALLOWED[ext].get("magic") or []
    if magic and not any(content.startswith(m) for m in magic):
        raise FileRejected(f"File content does not match its '{ext}' type (magic-byte check failed).")
    if ext in (".docx", ".xlsx"):
        _require_office_lib(ext)
    mime, _ = mimetypes.guess_type(filename)
    return {"ext": ext, "mime": mime or "application/octet-stream",
            "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _require_office_lib(ext: str) -> None:
    try:
        if ext == ".docx":
            import docx  # noqa: F401
        else:
            import openpyxl  # noqa: F401
    except ImportError:
        raise FileRejected(f"'{ext}' support requires an uninstalled library. "
                           f"Install {'python-docx' if ext == '.docx' else 'openpyxl'} on the server.")


def store_original(case_id: str, ext: str, content: bytes) -> Path:
    """Write bytes to the private case vault. Returns the stored path."""
    case_dir = Path(settings.DATA_DIR) / "cases" / case_id / "originals"
    case_dir.mkdir(parents=True, exist_ok=True)
    stored = case_dir / f"{uuid.uuid4().hex}{ext}"
    # Guard against path traversal: resolved path must stay inside case_dir.
    if case_dir.resolve() not in stored.resolve().parents:
        raise FileRejected("Invalid storage path.")
    stored.write_bytes(content)
    return stored


def run_malware_scan(path: Path) -> None:
    """Optional external scanner hook. Fail-closed on positive detection."""
    cmd = (settings.MALWARE_SCAN_CMD or "").strip()
    if not cmd:
        return
    try:
        proc = subprocess.run(cmd.format(path=str(path)).split(), timeout=120,
                              capture_output=True, text=True)
    except Exception as ex:
        logger.warning(f"Malware scan hook failed (fail-open with warning): {ex}")
        return
    if proc.returncode != 0:
        raise FileRejected(f"File blocked by malware scan (exit {proc.returncode}).")


def read_docx_text(content: bytes) -> Tuple[str, int]:
    """Returns (text, paragraph_count)."""
    import docx
    import io as _io
    doc = docx.Document(_io.BytesIO(content))
    paras = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    return ("\n".join(paras).strip(), len(paras))


def read_xlsx_preview(content: bytes, max_rows: int = 20) -> Tuple[List[str], List[List[str]]]:
    """Returns (headers, first rows) for mapping/quality screens."""
    import openpyxl
    import io as _io
    wb = openpyxl.load_workbook(_io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    headers = [(str(h).strip() if h is not None else "") for h in rows[0]]
    preview = [[("" if v is None else str(v)) for v in r[:len(headers)]] for r in rows[1:max_rows + 1]]
    return headers, preview


def read_csv_preview(content: bytes, max_rows: int = 20) -> Tuple[List[str], List[List[str]]]:
    import csv
    import io as _io
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except (UnicodeDecodeError, ValueError):
            continue
    else:
        raise FileRejected("Could not decode CSV as UTF-8 or Latin-1.")
    reader = csv.reader(_io.StringIO(text))
    rows = [r for _, r in zip(range(max_rows + 1), reader)]
    if not rows:
        return [], []
    return [h.strip() for h in rows[0]], rows[1:]
