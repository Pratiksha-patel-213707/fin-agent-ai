from decimal import Decimal
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.models import AdjustmentLine, AdjustmentType, ManualAdjustment, Severity
from core.validators import (
    check_accounts_exist_in_coa,
    check_circular_intercompany,
    check_debit_credit_balance,
    check_line_signs,
    check_no_zero_entry,
    run_all_deterministic_checks,
)


def make_entry(entry_id="TEST-001", lines=None, adj_type=AdjustmentType.ACCRUAL, ic_ref=None):
    if lines is None:
        lines = [
            AdjustmentLine("7090", "Prof Services", Decimal("1000"), Decimal("0"), "USD"),
            AdjustmentLine("2020", "Accrued Liab", Decimal("0"), Decimal("1000"), "USD"),
        ]

    return ManualAdjustment(
        entry_id=entry_id,
        description="Test entry",
        adj_type=adj_type,
        date="2024-12-31",
        period="2024-12",
        prepared_by="test@acme.com",
        lines=lines,
        intercompany_ref=ic_ref,
    )


VALID_CODES = {"7090", "2020", "5120", "9000", "9100", "2500", "1030"}


def test_balanced_entry_passes():
    entry = make_entry()
    issue = check_debit_credit_balance(entry)
    assert issue is None


def test_unbalanced_entry_fails():
    lines = [
        AdjustmentLine("7090", "Salaries", Decimal("22500"), Decimal("0"), "USD"),
        AdjustmentLine("2020", "Accrued", Decimal("0"), Decimal("22000"), "USD"),
    ]
    entry = make_entry(lines=lines)
    issue = check_debit_credit_balance(entry)
    assert issue is not None
    assert issue.code == "DEBIT_CREDIT_MISMATCH"
    assert issue.severity == Severity.ERROR
    assert issue.blocking is True


def test_within_tolerance_passes():
    lines = [
        AdjustmentLine("7090", "Salaries", Decimal("1000.004"), Decimal("0"), "USD"),
        AdjustmentLine("2020", "Accrued", Decimal("0"), Decimal("1000.00"), "USD"),
    ]
    entry = make_entry(lines=lines)
    issue = check_debit_credit_balance(entry)
    assert issue is None


def test_valid_accounts_pass():
    entry = make_entry()
    issues = check_accounts_exist_in_coa(entry, VALID_CODES)
    assert issues == []


def test_unknown_account_fails():
    lines = [
        AdjustmentLine("9999", "Mystery", Decimal("5000"), Decimal("0"), "USD"),
        AdjustmentLine("5120", "Misc Income", Decimal("0"), Decimal("5000"), "USD"),
    ]
    entry = make_entry(lines=lines)
    issues = check_accounts_exist_in_coa(entry, VALID_CODES)
    assert len(issues) == 1
    assert issues[0].code == "UNKNOWN_ACCOUNT_CODE"
    assert "9999" in issues[0].field_ref


def test_circular_ic_detected():
    entry_a = make_entry("ADJ-004", adj_type=AdjustmentType.INTERCOMPANY_ELIMINATION, ic_ref="ADJ-010")
    entry_b = make_entry("ADJ-010", adj_type=AdjustmentType.INTERCOMPANY_ELIMINATION, ic_ref="ADJ-004")
    all_entries = [entry_a, entry_b]

    issue_a = check_circular_intercompany(entry_a, all_entries)
    assert issue_a is not None
    assert issue_a.code == "CIRCULAR_IC_REFERENCE"

    issue_b = check_circular_intercompany(entry_b, all_entries)
    assert issue_b is not None


def test_non_circular_ic_passes():
    entry_a = make_entry("ADJ-004", adj_type=AdjustmentType.INTERCOMPANY_ELIMINATION, ic_ref="ADJ-010")
    entry_b = make_entry("ADJ-010", adj_type=AdjustmentType.INTERCOMPANY_ELIMINATION, ic_ref=None)
    all_entries = [entry_a, entry_b]

    issue = check_circular_intercompany(entry_a, all_entries)
    assert issue is None


def test_non_ic_entry_skipped():
    entry = make_entry(adj_type=AdjustmentType.ACCRUAL, ic_ref="ADJ-010")
    issue = check_circular_intercompany(entry, [entry])
    assert issue is None


def test_negative_amounts_fail():
    lines = [
        AdjustmentLine("7090", "Salaries", Decimal("-1000"), Decimal("0"), "USD"),
        AdjustmentLine("2020", "Accrued", Decimal("0"), Decimal("1000"), "USD"),
    ]
    entry = make_entry(lines=lines)
    issues = check_line_signs(entry)
    assert len(issues) == 1
    assert issues[0].code == "NEGATIVE_AMOUNT"


def test_all_zero_warning():
    lines = [
        AdjustmentLine("7090", "Salaries", Decimal("0"), Decimal("0"), "USD"),
        AdjustmentLine("2020", "Accrued", Decimal("0"), Decimal("0"), "USD"),
    ]
    entry = make_entry(lines=lines)
    issue = check_no_zero_entry(entry)
    assert issue is not None
    assert issue.severity == Severity.WARNING
    assert issue.blocking is False


def test_valid_entry_has_no_issues():
    entry = make_entry()
    issues = run_all_deterministic_checks(entry, VALID_CODES, [entry])
    assert issues == []


def test_adj008_error_is_detected():
    lines = [
        AdjustmentLine("7010", "Salaries", Decimal("22500"), Decimal("0"), "USD"),
        AdjustmentLine("2020", "Accrued", Decimal("0"), Decimal("22000"), "USD"),
    ]
    entry = make_entry("ADJ-008", lines=lines)
    valid_codes = {"7010", "2020"}
    issues = run_all_deterministic_checks(entry, valid_codes, [entry])
    assert "DEBIT_CREDIT_MISMATCH" in {issue.code for issue in issues}


def test_adj009_error_is_detected():
    lines = [
        AdjustmentLine("9999", "Mystery", Decimal("5000"), Decimal("0"), "USD"),
        AdjustmentLine("5130", "Misc Inc", Decimal("0"), Decimal("5000"), "USD"),
    ]
    entry = make_entry("ADJ-009", lines=lines)
    valid_codes = {"5130"}
    issues = run_all_deterministic_checks(entry, valid_codes, [entry])
    assert "UNKNOWN_ACCOUNT_CODE" in {issue.code for issue in issues}


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
