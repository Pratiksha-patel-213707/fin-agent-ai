"""Helpers for loading CSV and JSON input files."""

import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from core.models import AdjustmentLine, AdjustmentType, ManualAdjustment


def load_coa(path: str | Path) -> tuple[dict[str, dict], list[str]]:
    coa: dict[str, dict] = {}
    ambiguous_accounts: list[str] = []

    with open(path, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            code = row["account_code"].strip()
            notes = row.get("notes", "")
            cash_flow_category = row.get("cash_flow_category", "")
            is_ambiguous = "AMBIGUOUS" in (notes + cash_flow_category).upper()

            coa[code] = {
                "account_code": code,
                "account_name": row["account_name"].strip(),
                "parent_code": row.get("parent_code", "").strip() or None,
                "statement": row.get("statement", "").strip(),
                "category": row.get("category", "").strip(),
                "subcategory": row.get("subcategory", "").strip(),
                "cash_flow_category": cash_flow_category.strip() or None,
                "notes": notes.strip(),
                "is_ambiguous": is_ambiguous,
            }

            if is_ambiguous:
                ambiguous_accounts.append(code)

    return coa, ambiguous_accounts


def _parse_decimal(value: str) -> Decimal:
    value = str(value).strip()
    if not value:
        return Decimal("0")

    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse '{value}' as decimal") from exc


def load_adjustments(path: str | Path) -> list[ManualAdjustment]:
    with open(path, encoding="utf-8-sig") as file:
        raw = json.load(file)

    entries_data = raw if isinstance(raw, list) else raw.get("entries", [])
    default_period = "" if isinstance(raw, list) else str(raw.get("period", "")).strip()

    entries: list[ManualAdjustment] = []
    for item in entries_data:
        lines: list[AdjustmentLine] = []
        for line in item.get("lines", []):
            account_code = line.get("account_code") or line.get("account", "")
            account_name = line.get("account_name") or line.get("memo", "")
            lines.append(
                AdjustmentLine(
                    account_code=str(account_code).strip(),
                    account_name=str(account_name).strip(),
                    debit=_parse_decimal(line.get("debit", "0")),
                    credit=_parse_decimal(line.get("credit", "0")),
                    currency=str(line.get("currency", "USD")).strip(),
                    counterparty=line.get("counterparty"),
                )
            )

        entry_id = item.get("entry_id") or item.get("id", "")
        raw_type = str(item.get("type", "other")).lower()
        try:
            adjustment_type = AdjustmentType(raw_type)
        except ValueError:
            adjustment_type = AdjustmentType.OTHER

        entries.append(
            ManualAdjustment(
                entry_id=entry_id,
                description=item.get("description", ""),
                adj_type=adjustment_type,
                date=item.get("date", ""),
                period=str(item.get("period", "")).strip() or default_period,
                prepared_by=str(item.get("prepared_by") or item.get("source", "")).strip(),
                lines=lines,
                intercompany_ref=item.get("intercompany_ref"),
            )
        )

    return entries
