"""Shared data models used by the validation flow."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional
import uuid


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class ValidationStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class AdjustmentType(str, Enum):
    ACCRUAL = "accrual"
    RECLASS = "reclass"
    FX_REVALUATION = "fx_revaluation"
    INTERCOMPANY_ELIMINATION = "intercompany_elimination"
    PROVISION = "provision"
    AMORTIZATION = "amortization"
    REVENUE_RECOGNITION = "revenue_recognition"
    OTHER = "other"


@dataclass
class AuditTrailEntry:
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_type: str = ""
    source_ref: str = ""
    account_code: str = ""
    field_affected: str = ""
    value_before: Optional[Decimal] = None
    value_after: Optional[Decimal] = None
    delta: Optional[Decimal] = None
    narrative: str = ""
    agent: str = ""
    timestamp: str = ""


@dataclass
class ValidationIssue:
    issue_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    severity: Severity = Severity.ERROR
    code: str = ""
    entry_ref: str = ""
    field_ref: str = ""
    description: str = ""
    plain_english: str = ""
    suggested_action: str = ""
    blocking: bool = True
    requires_human: bool = False


@dataclass
class AdjustmentLine:
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal
    currency: str
    counterparty: Optional[str] = None


@dataclass
class ManualAdjustment:
    entry_id: str
    description: str
    adj_type: AdjustmentType
    date: str
    period: str
    prepared_by: str
    lines: list[AdjustmentLine] = field(default_factory=list)
    intercompany_ref: Optional[str] = None
    status: ValidationStatus = ValidationStatus.VALID
    issues: list[ValidationIssue] = field(default_factory=list)
    audit_trail: list[AuditTrailEntry] = field(default_factory=list)

    @property
    def total_debits(self) -> Decimal:
        return sum(line.debit for line in self.lines)

    @property
    def total_credits(self) -> Decimal:
        return sum(line.credit for line in self.lines)

    @property
    def is_balanced(self) -> bool:
        return abs(self.total_debits - self.total_credits) < Decimal("0.005")


@dataclass
class ValidationResult:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    period: str = ""
    total_entries: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    needs_review_count: int = 0
    entries: list[ManualAdjustment] = field(default_factory=list)
    audit_trail: list[AuditTrailEntry] = field(default_factory=list)
    summary_plain_english: str = ""
