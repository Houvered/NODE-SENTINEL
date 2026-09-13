# -*- coding: utf-8 -*-
"""
Financial Transaction Parser for NODE SENTINEL.
Validates, normalizes, and extracts structured financial transaction records from CSV and JSON streams.
Supports row-level error reporting, prevents silent data loss, and validates numeric amounts.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from app.models.financial_models import FinancialRecord, TransactionType


def normalize_account_identifier(raw_val: Any) -> str:
    """
    Standardize bank account identifiers or entity IDs.
    - Strips whitespace, hyphens, slashes, periods.
    - Preserves established prefixes (e.g. ACC_, PERSON_, ORG_).
    - Rejects strings with insufficient alphanumeric characters (< 3).
    """
    if raw_val is None:
        raise ValueError("Account/entity identifier is missing or null")

    val_str = str(raw_val).strip()
    if not val_str:
        raise ValueError("Account/entity identifier is empty")

    # If it's already a well-formed graph node ID (e.g. PERSON_TARIQ_AHMAD, ACC_HAWALA_9901)
    if re.match(r"^[A-Z0-9]+_[A-Z0-9_]+$", val_str):
        return val_str

    # Clean characters: keep alphanumeric and underscores
    cleaned = re.sub(r"[\s\-\.\/\(\)]", "", val_str).upper()
    if len(cleaned) < 3:
        raise ValueError(f"Invalid account identifier '{val_str}': must be at least 3 alphanumeric characters")

    # If user provided something like "A/C 990188231" or "ACC990188231", keep clean standard form
    if cleaned.startswith("ACC_"):
        return cleaned
    elif cleaned.startswith("ACC"):
        return cleaned
    elif cleaned.isdigit():
        return f"ACC_{cleaned}"

    return cleaned


def parse_timestamp_safe(ts_val: Any) -> datetime:
    """
    Safely parse datetime from strings, ISO timestamps, or unix epochs.
    """
    if ts_val is None:
        raise ValueError("Transaction timestamp is missing")

    if isinstance(ts_val, datetime):
        return ts_val

    if isinstance(ts_val, (int, float)):
        try:
            if ts_val > 1e11:
                return datetime.fromtimestamp(ts_val / 1000.0)
            return datetime.fromtimestamp(float(ts_val))
        except Exception as e:
            raise ValueError(f"Invalid epoch timestamp '{ts_val}': {e}")

    s = str(ts_val).strip()
    if not s:
        raise ValueError("Transaction timestamp string is empty")

    iso_clean = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_clean)
    except Exception:
        pass

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


def parse_transaction_type(type_val: Any) -> TransactionType:
    """Normalize transaction type string to TransactionType Enum."""
    if not type_val:
        return TransactionType.TRANSFER

    raw = str(type_val).strip().upper()
    if raw in {"TRANSFER", "NEFT", "RTGS", "IMPS", "WIRE", "UPI", "INTERNAL_TRANSFER"}:
        return TransactionType.TRANSFER
    elif raw in {"DEPOSIT", "CASH_DEPOSIT", "CREDIT"}:
        return TransactionType.DEPOSIT
    elif raw in {"WITHDRAWAL", "ATM", "CASH_WITHDRAWAL", "DEBIT"}:
        return TransactionType.WITHDRAWAL
    elif raw in {"PAYMENT", "POS", "BILL", "PURCHASE"}:
        return TransactionType.PAYMENT
    elif raw in {"REFUND", "REVERSAL", "CHARGEBACK"}:
        return TransactionType.REFUND
    return TransactionType.UNKNOWN


def validate_amount(amount_val: Any, tx_type: TransactionType) -> float:
    """
    Validate numeric amount. Reject non-numeric and negative amounts
    unless the transaction type explicitly supports negative amounts (e.g. REFUND).
    """
    if amount_val is None or amount_val == "":
        raise ValueError("Transaction amount is missing")

    if isinstance(amount_val, (int, float)):
        amt = float(amount_val)
    else:
        s = str(amount_val).replace("₹", "").replace(",", "").replace("$", "").strip()
        try:
            amt = float(s)
        except ValueError:
            raise ValueError(f"Invalid monetary amount format: '{amount_val}'")

    if amt < 0 and tx_type != TransactionType.REFUND:
        raise ValueError(f"Transaction amount cannot be negative ({amt}) for transaction type '{tx_type.value}'")

    if amt == 0:
        raise ValueError("Transaction amount cannot be zero")

    return amt


class FinancialParser:
    """Modular parser for financial transaction data in CSV and JSON formats."""

    @staticmethod
    def parse_record_dict(data: Dict[str, Any], row_id: Optional[Union[int, str]] = None) -> FinancialRecord:
        """
        Validate and normalize a single dictionary into a FinancialRecord.
        Raises ValueError with context if required fields are missing or invalid.
        """
        row_desc = f"Record #{row_id}" if row_id is not None else "Record"

        # Sender aliases
        sender_raw = (
            data.get("sender")
            or data.get("sender_account")
            or data.get("from_account")
            or data.get("from")
            or data.get("source_account")
            or data.get("payer")
            or data.get("origin")
        )
        # Receiver aliases
        receiver_raw = (
            data.get("receiver")
            or data.get("receiver_account")
            or data.get("to_account")
            or data.get("to")
            or data.get("destination_account")
            or data.get("payee")
            or data.get("beneficiary")
        )

        if not sender_raw:
            raise ValueError(f"{row_desc}: Missing required sender account or entity identifier")
        if not receiver_raw:
            raise ValueError(f"{row_desc}: Missing required receiver account or entity identifier")

        sender_norm = normalize_account_identifier(sender_raw)
        receiver_norm = normalize_account_identifier(receiver_raw)

        # Transaction type
        type_raw = (
            data.get("transaction_type")
            or data.get("type")
            or data.get("tx_type")
            or data.get("transfer_type")
        )
        tx_type = parse_transaction_type(type_raw)

        # Amount
        amount_raw = (
            data.get("amount")
            or data.get("amt")
            or data.get("transaction_amount")
            or data.get("value")
        )
        amount = validate_amount(amount_raw, tx_type)

        # Timestamp
        ts_raw = (
            data.get("timestamp")
            or data.get("tx_time")
            or data.get("date_time")
            or data.get("date")
            or data.get("time")
            or data.get("tx_date")
        )
        if not ts_raw:
            raise ValueError(f"{row_desc}: Missing required transaction timestamp")
        timestamp = parse_timestamp_safe(ts_raw)

        # Optional fields
        currency = str(data.get("currency") or data.get("curr") or "INR").strip().upper()
        if len(currency) != 3:
            raise ValueError(f"{row_desc}: Unsupported currency code '{currency}', expected 3-letter ISO code (e.g. INR, USD)")

        tx_id = (
            data.get("transaction_id")
            or data.get("tx_id")
            or data.get("id")
            or data.get("reference_number")
            or data.get("ref_id")
        )
        tx_id_str = str(tx_id).strip() if tx_id else None

        account_id = data.get("account_id") or data.get("account_number") or data.get("acct_no")
        account_id_str = str(account_id).strip() if account_id else None

        case_id = data.get("case_id") or data.get("case_code") or data.get("fir_id")
        case_id_str = str(case_id).strip() if case_id else None

        location = data.get("location") or data.get("branch") or data.get("city")
        location_str = str(location).strip() if location else None

        source_doc = data.get("source_document") or data.get("source_file") or data.get("source")
        source_doc_str = str(source_doc).strip() if source_doc else None

        description = data.get("description") or data.get("reference") or data.get("narrative") or data.get("remarks")
        description_str = str(description).strip() if description else None

        return FinancialRecord(
            transaction_id=tx_id_str,
            timestamp=timestamp,
            sender=sender_norm,
            receiver=receiver_norm,
            amount=amount,
            currency=currency,
            transaction_type=tx_type,
            account_id=account_id_str,
            case_id=case_id_str,
            location=location_str,
            source_document=source_doc_str,
            description=description_str,
        )

    @classmethod
    def parse_csv(
        cls, csv_content: Union[str, bytes], source_name: Optional[str] = None
    ) -> Tuple[List[FinancialRecord], int, List[str], List[str]]:
        """
        Parse CSV formatted financial transaction data.
        Returns (valid_records, rejected_count, warnings, errors).
        """
        if isinstance(csv_content, bytes):
            try:
                text = csv_content.decode("utf-8")
            except UnicodeDecodeError:
                text = csv_content.decode("latin-1")
        else:
            text = str(csv_content)

        text = text.strip()
        if not text:
            return [], 0, [], ["CSV data is completely empty"]

        valid_records: List[FinancialRecord] = []
        errors: List[str] = []
        warnings: List[str] = []
        seen_tx_ids = set()

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return [], 0, [], ["CSV has no header row or columns defined"]

        clean_fieldnames = [f.strip().lstrip("\ufeff") for f in reader.fieldnames if f]
        reader.fieldnames = clean_fieldnames

        for row_idx, row in enumerate(reader, start=1):
            if not any(v.strip() for v in row.values() if v):
                continue

            cleaned_row = {
                k.strip().lower(): v.strip() for k, v in row.items() if k is not None and v is not None
            }
            if source_name and "source_document" not in cleaned_row:
                cleaned_row["source_document"] = source_name

            try:
                record = cls.parse_record_dict(cleaned_row, row_id=row_idx)

                if record.transaction_id:
                    if record.transaction_id in seen_tx_ids:
                        warnings.append(f"Row #{row_idx}: Duplicate transaction_id '{record.transaction_id}' encountered; record preserved")
                    seen_tx_ids.add(record.transaction_id)

                valid_records.append(record)
            except Exception as ex:
                errors.append(f"Row #{row_idx}: {str(ex)}")

        return valid_records, len(errors), warnings, errors

    @classmethod
    def parse_json(
        cls, json_content: Union[str, bytes, List[Dict[str, Any]]], source_name: Optional[str] = None
    ) -> Tuple[List[FinancialRecord], int, List[str], List[str]]:
        """
        Parse JSON formatted financial transaction data (array of objects or envelope with 'records'/'transactions').
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

        if isinstance(raw_data, dict):
            if "records" in raw_data and isinstance(raw_data["records"], list):
                items = raw_data["records"]
            elif "transactions" in raw_data and isinstance(raw_data["transactions"], list):
                items = raw_data["transactions"]
            elif "transfers" in raw_data and isinstance(raw_data["transfers"], list):
                items = raw_data["transfers"]
            else:
                items = [raw_data]
        elif isinstance(raw_data, list):
            items = raw_data
        else:
            return [], 0, [], ["Expected a JSON list of transaction objects or an envelope containing a 'records' list"]

        if not items:
            return [], 0, [], ["JSON contains 0 records"]

        valid_records: List[FinancialRecord] = []
        errors: List[str] = []
        warnings: List[str] = []
        seen_tx_ids = set()

        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                errors.append(f"Item #{idx}: Expected JSON object, found {type(item).__name__}")
                continue

            cleaned_item = {k.strip().lower(): v for k, v in item.items() if k is not None}
            if source_name and "source_document" not in cleaned_item:
                cleaned_item["source_document"] = source_name

            try:
                record = cls.parse_record_dict(cleaned_item, row_id=idx)

                if record.transaction_id:
                    if record.transaction_id in seen_tx_ids:
                        warnings.append(f"Item #{idx}: Duplicate transaction_id '{record.transaction_id}' encountered; record preserved")
                    seen_tx_ids.add(record.transaction_id)

                valid_records.append(record)
            except Exception as ex:
                errors.append(f"Item #{idx}: {str(ex)}")

        return valid_records, len(errors), warnings, errors
