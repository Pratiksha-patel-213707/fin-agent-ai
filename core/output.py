"""Report writers for validation results."""

import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from core.models import Severity, ValidationResult, ValidationStatus


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def result_to_dict(result: ValidationResult) -> dict:
    def convert(value):
        if isinstance(value, Decimal):
            return str(value)
        if hasattr(value, "value"):
            return value.value
        if hasattr(value, "__dataclass_fields__"):
            return {key: convert(item) for key, item in asdict(value).items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        return value

    return convert(result)


def write_json_report(result: ValidationResult, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(result_to_dict(result), file, indent=2, cls=DecimalEncoder)


def write_text_report(result: ValidationResult, path: str | Path) -> None:
    lines: list[str] = []
    divider = "=" * 72
    section_rule = "-" * 40
    row_rule = "-" * 72

    lines.append(divider)
    lines.append("  MANUAL ADJUSTMENTS VALIDATION REPORT")
    lines.append(f"  Period: {result.period}   |   Run ID: {result.run_id}")
    lines.append(divider)
    lines.append("")
    lines.append("EXECUTIVE SUMMARY")
    lines.append(section_rule)
    lines.append(result.summary_plain_english)
    lines.append("")
    lines.append("TOTALS")
    lines.append(f"  Total entries reviewed : {result.total_entries}")
    lines.append(f"  Valid         : {result.valid_count}")
    lines.append(f"  Needs review  : {result.needs_review_count}")
    lines.append(f"  Blocked errors: {result.invalid_count}")
    lines.append("")

    status_labels = {
        ValidationStatus.VALID: "VALID",
        ValidationStatus.INVALID: "INVALID",
        ValidationStatus.NEEDS_REVIEW: "NEEDS_REVIEW",
    }
    severity_labels = {
        Severity.ERROR: "ERROR",
        Severity.WARNING: "WARNING",
        Severity.INFO: "INFO",
    }

    for entry in result.entries:
        lines.append(row_rule)
        lines.append(f"{status_labels[entry.status]}  {entry.entry_id}  |  {entry.adj_type.value.upper()}")
        lines.append(f"  {entry.description}")
        lines.append(f"  Prepared by: {entry.prepared_by}  |  Date: {entry.date}")
        lines.append("")
        lines.append("  Lines:")

        for line in entry.lines:
            debit = f"Dr {line.debit:>12,.2f}" if line.debit else " " * 17
            credit = f"Cr {line.credit:>12,.2f}" if line.credit else " " * 17
            lines.append(f"    {line.account_code}  {line.account_name:<35} {debit}  {credit}  {line.currency}")

        lines.append(f"  {'-' * 50}")
        lines.append(
            f"  TOTAL                                          Dr {entry.total_debits:>12,.2f}  "
            f"Cr {entry.total_credits:>12,.2f}"
        )

        if entry.issues:
            lines.append("")
            lines.append("  ISSUES:")
            for issue in entry.issues:
                level = severity_labels[issue.severity]
                scope = "BLOCKING" if issue.blocking else "ADVISORY"
                lines.append(f"  {level} [{issue.code}] {scope}")
                lines.append(f"    {issue.plain_english or issue.description}")
                if issue.suggested_action:
                    lines.append(f"    Action: {issue.suggested_action}")

        lines.append("")

    lines.append(divider)
    lines.append("AUDIT TRAIL")
    lines.append(section_rule)
    for item in result.audit_trail:
        lines.append(f"  [{item.timestamp}] {item.source_ref}: {item.narrative}")

    lines.append("")
    lines.append("END OF REPORT")
    lines.append(divider)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines))
