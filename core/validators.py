"""Deterministic validation rules for manual journal entries."""

from decimal import Decimal
from typing import Optional

from core.models import AdjustmentType, ManualAdjustment, Severity, ValidationIssue


BALANCE_TOLERANCE = Decimal("0.01")


def check_debit_credit_balance(entry: ManualAdjustment) -> Optional[ValidationIssue]:
    delta = abs(entry.total_debits - entry.total_credits)
    if delta <= BALANCE_TOLERANCE:
        return None

    return ValidationIssue(
        severity=Severity.ERROR,
        code="DEBIT_CREDIT_MISMATCH",
        entry_ref=entry.entry_id,
        description=(
            f"Entry {entry.entry_id}: debits={entry.total_debits:.2f}, "
            f"credits={entry.total_credits:.2f}, difference={delta:.2f}"
        ),
        suggested_action="Review entry lines and correct the imbalance before posting.",
        blocking=True,
        requires_human=True,
    )


def check_accounts_exist_in_coa(
    entry: ManualAdjustment,
    valid_account_codes: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for line in entry.lines:
        if line.account_code in valid_account_codes:
            continue

        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                code="UNKNOWN_ACCOUNT_CODE",
                entry_ref=entry.entry_id,
                field_ref=f"account_code={line.account_code}",
                description=(
                    f"Entry {entry.entry_id}, line account {line.account_code} "
                    f"('{line.account_name}') does not exist in chart of accounts."
                ),
                suggested_action=(
                    "Verify account code. If it is a new account, add it to the chart of accounts first. "
                    "If it is a typo, correct the account code."
                ),
                blocking=True,
                requires_human=True,
            )
        )
    return issues


def check_no_zero_entry(entry: ManualAdjustment) -> Optional[ValidationIssue]:
    all_zero = all(line.debit == Decimal("0") and line.credit == Decimal("0") for line in entry.lines)
    if not all_zero:
        return None

    return ValidationIssue(
        severity=Severity.WARNING,
        code="ALL_ZERO_LINES",
        entry_ref=entry.entry_id,
        description=f"Entry {entry.entry_id}: all lines have zero debit and zero credit.",
        suggested_action="Confirm this is intentional. Zero entries are usually mistakes.",
        blocking=False,
        requires_human=True,
    )


def check_line_signs(entry: ManualAdjustment) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for index, line in enumerate(entry.lines):
        if line.debit >= Decimal("0") and line.credit >= Decimal("0"):
            continue

        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                code="NEGATIVE_AMOUNT",
                entry_ref=entry.entry_id,
                field_ref=f"line_{index}_account_{line.account_code}",
                description=(
                    f"Entry {entry.entry_id}, account {line.account_code}: "
                    f"debit={line.debit}, credit={line.credit}. "
                    f"Amounts must not be negative; use the opposite column instead."
                ),
                suggested_action="Move the amount to the correct debit or credit column.",
                blocking=True,
                requires_human=False,
            )
        )
    return issues


def check_single_sided_line(entry: ManualAdjustment) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for index, line in enumerate(entry.lines):
        if not (line.debit > Decimal("0") and line.credit > Decimal("0")):
            continue

        issues.append(
            ValidationIssue(
                severity=Severity.ERROR,
                code="BOTH_DEBIT_AND_CREDIT_ON_LINE",
                entry_ref=entry.entry_id,
                field_ref=f"line_{index}_account_{line.account_code}",
                description=(
                    f"Entry {entry.entry_id}, account {line.account_code}: "
                    f"has both debit ({line.debit}) and credit ({line.credit}) on the same line."
                ),
                suggested_action="Split this into separate debit and credit lines.",
                blocking=True,
                requires_human=False,
            )
        )
    return issues


def check_circular_intercompany(
    entry: ManualAdjustment,
    all_entries: list[ManualAdjustment],
) -> Optional[ValidationIssue]:
    if entry.adj_type != AdjustmentType.INTERCOMPANY_ELIMINATION:
        return None
    if not entry.intercompany_ref:
        return None

    referenced_entry = next(
        (candidate for candidate in all_entries if candidate.entry_id == entry.intercompany_ref),
        None,
    )
    if referenced_entry is None:
        return None

    if referenced_entry.intercompany_ref != entry.entry_id:
        return None

    return ValidationIssue(
        severity=Severity.ERROR,
        code="CIRCULAR_IC_REFERENCE",
        entry_ref=entry.entry_id,
        field_ref="intercompany_ref",
        description=(
            f"Entry {entry.entry_id} references IC entry {entry.intercompany_ref}, "
            f"which in turn references {referenced_entry.intercompany_ref}. "
            "This creates a circular elimination loop."
        ),
        suggested_action=(
            "Review the intercompany relationship. There should only be one elimination entry "
            "for the transaction."
        ),
        blocking=True,
        requires_human=True,
    )


def check_currency_consistency(entry: ManualAdjustment) -> Optional[ValidationIssue]:
    if entry.adj_type == AdjustmentType.FX_REVALUATION:
        return None

    currencies = {line.currency for line in entry.lines}
    if len(currencies) <= 1:
        return None

    return ValidationIssue(
        severity=Severity.WARNING,
        code="MIXED_CURRENCY_ENTRY",
        entry_ref=entry.entry_id,
        description=(
            f"Entry {entry.entry_id} has lines in multiple currencies: "
            f"{', '.join(sorted(currencies))}. Verify FX conversion is correct."
        ),
        suggested_action="Confirm the FX rates and make sure the posting currency is correct.",
        blocking=False,
        requires_human=True,
    )


def run_all_deterministic_checks(
    entry: ManualAdjustment,
    valid_account_codes: set[str],
    all_entries: list[ManualAdjustment],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    balance_issue = check_debit_credit_balance(entry)
    if balance_issue:
        issues.append(balance_issue)

    issues.extend(check_accounts_exist_in_coa(entry, valid_account_codes))
    issues.extend(check_line_signs(entry))
    issues.extend(check_single_sided_line(entry))

    circular_issue = check_circular_intercompany(entry, all_entries)
    if circular_issue:
        issues.append(circular_issue)

    zero_issue = check_no_zero_entry(entry)
    if zero_issue:
        issues.append(zero_issue)

    currency_issue = check_currency_consistency(entry)
    if currency_issue:
        issues.append(currency_issue)

    return issues
