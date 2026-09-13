# -*- coding: utf-8 -*-
"""
CDR (Call Detail Record) Parser for NODE SENTINEL.
Validates, normalizes, and extracts structured CDR records from CSV and JSON streams.
Reports row-level errors and prevents silent data loss.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from app.models.cdr_models import CallType, CDRRecord


def normalize_phone_number(raw_val: Any) -> str:
    """
    Standardize phone numbers into clean canonical E.164-compatible strings.
    - Strips whitespace, hyphens, periods, parentheses.
    - Preserves or normalizes country codes (+91, etc.).
    - Rejects strings with insufficient digits (< 7).
    """
    if raw_val is None:
        raise ValueError("Phone number is missing or null")

    val_str = str(raw_val).strip()
    if not val_str:
        raise ValueError("Phone number is empty")

    has_plus = val_str.startswith("+")
    digits = "".join(c for c in val_str if c.isdigit())

    if len(digits) < 7:
        raise ValueError(f"Invalid phone number '{val_str}': contains fewer than 7 digits")

    # Standardize 10-digit Indian numbers to +91 or preserve standard format
    if len(digits) == 10:
        return f"+91{digits}"
    elif len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    elif len(digits) == 11 and digits.startswith("0"):
        return f"+91{digits[1:]}"
    elif has_plus:
        return f"+{digits}"
    else:
        # Default prefix + if 11+ digits, or +91 fallback
        if len(digits) > 10:
            return f"+{digits}"
        return f"+91{digits}"


def parse_timestamp_safe(ts_val: Any) -> datetime:
    """
    Safely parse datetime from strings or timestamps.
    Supports ISO formats, standard date strings, and unix epochs.
    """
    if ts_val is None:
        raise ValueError("Call timestamp is missing")

    if isinstance(ts_val, datetime):
        return ts_val

    if isinstance(ts_val, (int, float)):
        # Epoch timestamp
        try:
            # Check if millisecond timestamp
            if ts_val > 1e11:
                return datetime.fromtimestamp(ts_val / 1000.0)
            return datetime.fromtimestamp(float(ts_val))
        except Exception as e:
            raise ValueError(f"Invalid epoch timestamp '{ts_val}': {e}")

    s = str(ts_val).strip()
    if not s:
        raise ValueError("Call timestamp string is empty")

    # Try ISO formats first
    iso_clean = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_clean)
    except Exception:
        pass

    # Common format variations
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%Y%m%d%H%M%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    raise ValueError(f"Unable to parse timestamp '{s}' using standard datetime formats")


def parse_duration_seconds(dur_val: Any) -> int:
    """
    Convert raw duration into integer seconds.
    Supports integers, floats, 'MM:SS' and 'HH:MM:SS' strings.
    Rejects negative durations.
    """
    if dur_val is None or dur_val == "":
        return 0

    if isinstance(dur_val, (int, float)):
        sec = int(dur_val)
        if sec < 0:
            raise ValueError(f"Call duration cannot be negative: {dur_val}")
        return sec

    s = str(dur_val).strip()
    if not s:
        return 0

    # Check for HH:MM:SS or MM:SS
    if ":" in s:
        parts = s.split(":")
        try:
            if len(parts) == 2:
                mins, secs = int(parts[0]), int(float(parts[1]))
                total = mins * 60 + secs
            elif len(parts) == 3:
                hrs, mins, secs = int(parts[0]), int(parts[1]), int(float(parts[2]))
                total = hrs * 3600 + mins * 60 + secs
            else:
                raise ValueError("Invalid time colon format")
            if total < 0:
                raise ValueError("Duration cannot be negative")
            return total
        except Exception as e:
            raise ValueError(f"Failed to parse time string '{s}': {e}")

    try:
        val = int(float(s))
        if val < 0:
            raise ValueError(f"Call duration cannot be negative: {val}")
        return val
    except ValueError as ve:
        raise ValueError(f"Invalid duration value '{s}': {ve}")


def parse_call_type(type_val: Any) -> CallType:
    """Normalize call type string to CallType Enum."""
    if not type_val:
        return CallType.UNKNOWN

    raw = str(type_val).strip().upper()
    if raw in {"INCOMING", "IN", "INBOUND", "RCV", "RECEIVED"}:
        return CallType.INCOMING
    elif raw in {"OUTGOING", "OUT", "OUTBOUND", "DIALED", "CALL"}:
        return CallType.OUTGOING
    elif raw in {"MISSED", "UNANSWERED", "REJECTED", "NO_ANSWER"}:
        return CallType.MISSED
    return CallType.UNKNOWN


class CDRParser:
    """Modular parser for CDR data in CSV and JSON formats."""

    @staticmethod
    def parse_record_dict(data: Dict[str, Any], row_id: Optional[Union[int, str]] = None) -> CDRRecord:
        """
        Validate and normalize a single dictionary into a CDRRecord.
        Raises ValueError with context if required fields are missing or invalid.
        """
        # Map common field name aliases
        caller_raw = (
            data.get("caller")
            or data.get("calling_number")
            or data.get("caller_number")
            or data.get("source_number")
            or data.get("from_number")
            or data.get("from")
            or data.get("originating_number")
        )
        receiver_raw = (
            data.get("receiver")
            or data.get("called_number")
            or data.get("recipient_number")
            or data.get("target_number")
            or data.get("to_number")
            or data.get("to")
            or data.get("destination_number")
        )

        row_desc = f"Record #{row_id}" if row_id is not None else "Record"

        if not caller_raw:
            raise ValueError(f"{row_desc}: Missing required caller phone number")
        if not receiver_raw:
            raise ValueError(f"{row_desc}: Missing required receiver phone number")

        caller_norm = normalize_phone_number(caller_raw)
        receiver_norm = normalize_phone_number(receiver_raw)

        if caller_norm == receiver_norm:
            # Self-calls allowed as records, but validate
            pass

        # Timestamp
        ts_raw = (
            data.get("timestamp")
            or data.get("call_time")
            or data.get("date_time")
            or data.get("call_date")
            or data.get("time")
        )
        if not ts_raw:
            raise ValueError(f"{row_desc}: Missing required call timestamp")
        timestamp = parse_timestamp_safe(ts_raw)

        # Duration
        dur_raw = (
            data.get("duration_seconds")
            or data.get("duration")
            or data.get("call_duration")
            or data.get("duration_sec")
        )
        duration_seconds = parse_duration_seconds(dur_raw)

        # Call Type
        type_raw = (
            data.get("call_type")
            or data.get("direction")
            or data.get("type")
            or data.get("call_direction")
        )
        call_type = parse_call_type(type_raw)

        # Missed calls should have 0 duration if not explicitly stated
        if call_type == CallType.MISSED and duration_seconds > 0:
            # Warning or preserve
            pass

        # Optional fields
        call_id = data.get("call_id") or data.get("id") or data.get("record_id")
        if call_id is not None:
            call_id = str(call_id).strip()

        cell_tower = (
            data.get("cell_tower")
            or data.get("tower_id")
            or data.get("tower")
            or data.get("bts_id")
            or data.get("site_id")
        )
        location = (
            data.get("location")
            or data.get("tower_location")
            or data.get("cell_location")
            or data.get("address")
            or data.get("area")
        )
        case_id = data.get("case_id") or data.get("case_code") or data.get("fir_id")
        source_doc = data.get("source_document") or data.get("source_file") or data.get("source")

        return CDRRecord(
            call_id=call_id if call_id else None,
            caller=caller_norm,
            receiver=receiver_norm,
            timestamp=timestamp,
            duration_seconds=duration_seconds,
            call_type=call_type,
            cell_tower=str(cell_tower).strip() if cell_tower else None,
            location=str(location).strip() if location else None,
            case_id=str(case_id).strip() if case_id else None,
            source_document=str(source_doc).strip() if source_doc else None,
        )

    @classmethod
    def parse_csv(
        cls, csv_content: Union[str, bytes], source_name: Optional[str] = None
    ) -> Tuple[List[CDRRecord], int, List[str], List[str]]:
        """
        Parse CSV formatted CDR data.
        Returns (valid_records, rejected_count, warnings, errors).
        """
        if isinstance(csv_content, bytes):
            # Try utf-8 decode with fallback
            try:
                text = csv_content.decode("utf-8")
            except UnicodeDecodeError:
                text = csv_content.decode("latin-1")
        else:
            text = str(csv_content)

        text = text.strip()
        if not text:
            return [], 0, [], ["CSV data is completely empty"]

        valid_records: List[CDRRecord] = []
        errors: List[str] = []
        warnings: List[str] = []
        seen_call_ids = set()

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return [], 0, [], ["CSV has no header row or columns defined"]

        # Clean fieldnames (lowercase, strip whitespace and BOM)
        clean_fieldnames = [f.strip().lstrip("\ufeff") for f in reader.fieldnames if f]
        reader.fieldnames = clean_fieldnames

        for row_idx, row in enumerate(reader, start=1):
            if not any(v.strip() for v in row.values() if v):
                # Empty row
                continue

            # Clean row keys and values
            cleaned_row = {
                k.strip().lower(): v.strip() for k, v in row.items() if k is not None and v is not None
            }
            if source_name and "source_document" not in cleaned_row:
                cleaned_row["source_document"] = source_name

            try:
                record = cls.parse_record_dict(cleaned_row, row_id=row_idx)

                # Check duplicate call ID
                if record.call_id:
                    if record.call_id in seen_call_ids:
                        warnings.append(f"Row #{row_idx}: Duplicate call_id '{record.call_id}' encountered; record preserved")
                    seen_call_ids.add(record.call_id)

                valid_records.append(record)
            except Exception as ex:
                errors.append(f"Row #{row_idx}: {str(ex)}")

        return valid_records, len(errors), warnings, errors

    @classmethod
    def parse_json(
        cls, json_content: Union[str, bytes, List[Dict[str, Any]]], source_name: Optional[str] = None
    ) -> Tuple[List[CDRRecord], int, List[str], List[str]]:
        """
        Parse JSON formatted CDR data (array of objects or envelope with 'records').
        Returns (valid_records, rejected_count, warnings, errors).
        """
        if isinstance(json_content, (str, bytes)):
            text = json_content.decode("utf-8") if isinstance(json_content, bytes) else json_content
            text = text.strip()
            if not text:
                return [], 0, [], ["JSON data is empty"]
            try:
                raw_data = json.loads(text)
            except json.JSONDecodeError as jde:
                return [], 0, [], [f"Malformed JSON syntax: {jde}"]
        else:
            raw_data = json_content

        # Allow envelope like {"records": [...]} or direct list
        if isinstance(raw_data, dict):
            if "records" in raw_data and isinstance(raw_data["records"], list):
                items = raw_data["records"]
            elif "cdrs" in raw_data and isinstance(raw_data["cdrs"], list):
                items = raw_data["cdrs"]
            elif "calls" in raw_data and isinstance(raw_data["calls"], list):
                items = raw_data["calls"]
            else:
                items = [raw_data]
        elif isinstance(raw_data, list):
            items = raw_data
        else:
            return [], 0, [], ["Expected a JSON list of CDR objects or an object containing a 'records' list"]

        if not items:
            return [], 0, [], ["JSON contains 0 records"]

        valid_records: List[CDRRecord] = []
        errors: List[str] = []
        warnings: List[str] = []
        seen_call_ids = set()

        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                errors.append(f"Item #{idx}: Expected JSON object, found {type(item).__name__}")
                continue

            cleaned_item = {k.strip().lower(): v for k, v in item.items() if k is not None}
            if source_name and "source_document" not in cleaned_item:
                cleaned_item["source_document"] = source_name

            try:
                record = cls.parse_record_dict(cleaned_item, row_id=idx)

                if record.call_id:
                    if record.call_id in seen_call_ids:
                        warnings.append(f"Item #{idx}: Duplicate call_id '{record.call_id}' encountered; record preserved")
                    seen_call_ids.add(record.call_id)

                valid_records.append(record)
            except Exception as ex:
                errors.append(f"Item #{idx}: {str(ex)}")

        return valid_records, len(errors), warnings, errors
