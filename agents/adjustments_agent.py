"""Validation flow for manual journal adjustments."""

import json
import os
from datetime import datetime, timezone

from google import genai
from google.genai import types

from core.models import (
    AuditTrailEntry,
    ManualAdjustment,
    Severity,
    ValidationIssue,
    ValidationResult,
    ValidationStatus,
)
from core.validators import run_all_deterministic_checks


class ManualAdjustmentsAgent:
    def __init__(self, coa: dict[str, dict], verbose: bool = True):
        self.coa = coa
        self.valid_codes = set(coa.keys())
        self.verbose = verbose
        self.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        preferred_model = os.environ.get("GEMINI_MODEL", "").strip()
        default_models = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
        ]
        self.model_candidates = []
        if preferred_model:
            self.model_candidates.append(preferred_model)
        self.model_candidates.extend(
            model for model in default_models if model not in self.model_candidates
        )
        self.model = self.model_candidates[0]
        self.llm_available = bool(self.client)
        self._last_llm_failure = ""

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def _build_local_issue_text(
        self,
        entry: ManualAdjustment,
        issue: ValidationIssue,
    ) -> tuple[str, str]:
        amounts = []
        currencies = sorted({line.currency for line in entry.lines})

        for line in entry.lines:
            if line.debit:
                amounts.append(f"debit {line.account_code} for {line.debit:,.2f}")
            if line.credit:
                amounts.append(f"credit {line.account_code} for {line.credit:,.2f}")

        if issue.code == "DEBIT_CREDIT_MISMATCH":
            difference = abs(entry.total_debits - entry.total_credits)
            return (
                f"This entry does not balance. Total debits are {entry.total_debits:,.2f} "
                f"and total credits are {entry.total_credits:,.2f}, leaving a difference of "
                f"{difference:,.2f}.",
                (
                    f"Update the lines so the total debit equals the total credit for {entry.entry_id}. "
                    f"Current amounts: {', '.join(amounts)}."
                ),
            )

        if issue.code == "UNKNOWN_ACCOUNT_CODE":
            account_code = issue.field_ref.replace("account_code=", "") if issue.field_ref else "the referenced account"
            return (
                f"This entry uses account {account_code}, but that code is not in the chart of accounts.",
                f"Replace {account_code} with a valid account code, or add it to the chart of accounts before posting.",
            )

        if issue.code == "NEGATIVE_AMOUNT":
            return (
                "One line uses a negative debit or credit, which is not allowed in this journal format.",
                "Move the amount to the opposite column so every debit and credit is entered as a positive value.",
            )

        if issue.code == "BOTH_DEBIT_AND_CREDIT_ON_LINE":
            return (
                "One line has both a debit and a credit amount, so the posting is ambiguous.",
                "Split that line into separate debit and credit lines.",
            )

        if issue.code == "CIRCULAR_IC_REFERENCE":
            return (
                "This intercompany entry points back to itself through another reference, so the relationship is circular.",
                "Keep a single elimination entry for the relationship and remove the circular reference.",
            )

        if issue.code == "ALL_ZERO_LINES":
            return (
                "Every line in this journal is zero, which usually means the entry is incomplete or accidental.",
                "Confirm the entry is intentional or update the amounts before posting.",
            )

        if issue.code == "MIXED_CURRENCY_ENTRY":
            return (
                f"This entry mixes currencies ({', '.join(currencies)}), so it needs review before posting.",
                "Confirm the FX treatment and make sure the journal is recorded in the right currency.",
            )

        return (
            issue.description,
            issue.suggested_action or f"Review {entry.entry_id} and correct the issue before posting.",
        )

    def _apply_local_issue_text(
        self,
        entry: ManualAdjustment,
        issues: list[ValidationIssue],
    ) -> list[ValidationIssue]:
        for issue in issues:
            plain_english, suggested_action = self._build_local_issue_text(entry, issue)
            issue.plain_english = plain_english
            if not issue.suggested_action or issue.suggested_action == issue.description:
                issue.suggested_action = suggested_action
        return issues

    def _is_non_retryable_error(self, exc: Exception) -> bool:
        message = str(exc).upper()
        markers = [
            "RESOURCE_EXHAUSTED",
            "QUOTA",
            "API KEY EXPIRED",
            "INVALID_ARGUMENT",
            "PERMISSION_DENIED",
            "UNAUTHENTICATED",
            "API_KEY",
        ]
        return any(marker in message for marker in markers)

    def _generate_text(self, prompt: str, max_output_tokens: int) -> str:
        if not self.llm_available or not self.client:
            raise RuntimeError("Gemini client unavailable")

        failures = []
        for model in self.model_candidates:
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(max_output_tokens=max_output_tokens),
                )
                self.model = model
                return response.text.strip()
            except Exception as exc:
                failures.append(f"{model}: {exc}")
                if self._is_non_retryable_error(exc):
                    continue

        self.llm_available = False
        self._last_llm_failure = "; ".join(failures)
        raise RuntimeError(self._last_llm_failure)

    def _validate_entry(
        self,
        entry: ManualAdjustment,
        all_entries: list[ManualAdjustment],
    ) -> list[ValidationIssue]:
        return run_all_deterministic_checks(entry, self.valid_codes, all_entries)

    def _assign_status(self, issues: list[ValidationIssue]) -> ValidationStatus:
        if any(issue.severity == Severity.ERROR for issue in issues):
            return ValidationStatus.INVALID
        if any(issue.severity == Severity.WARNING for issue in issues):
            return ValidationStatus.NEEDS_REVIEW
        return ValidationStatus.VALID

    def _strip_code_fence(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned.startswith("```"):
            return cleaned

        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        return cleaned.strip()

    def _enrich_issues(
        self,
        entry: ManualAdjustment,
        issues: list[ValidationIssue],
    ) -> list[ValidationIssue]:
        if not issues:
            return issues

        if not self.llm_available:
            return self._apply_local_issue_text(entry, issues)

        payload = [
            {
                "issue_code": issue.code,
                "severity": issue.severity.value,
                "technical_description": issue.description,
                "suggested_action": issue.suggested_action,
                "blocking": issue.blocking,
            }
            for issue in issues
        ]

        prompt = f"""You are reviewing a journal entry for a finance team.

Entry:
- ID: {entry.entry_id}
- Description: {entry.description}
- Type: {entry.adj_type.value}
- Date: {entry.date}
- Prepared by: {entry.prepared_by}
- Lines:
{json.dumps([{"account": line.account_code, "name": line.account_name, "debit": str(line.debit), "credit": str(line.credit), "currency": line.currency} for line in entry.lines], indent=2)}

Issues:
{json.dumps(payload, indent=2)}

Return a JSON array in the same order as the input.
Each item must contain:
- "issue_code"
- "plain_english"
- "suggested_action"
"""

        try:
            raw = self._generate_text(prompt, max_output_tokens=1000)
            enriched = json.loads(self._strip_code_fence(raw))
            enriched_by_code = {item["issue_code"]: item for item in enriched}

            for issue in issues:
                if issue.code not in enriched_by_code:
                    continue
                item = enriched_by_code[issue.code]
                issue.plain_english = item.get("plain_english", "")
                issue.suggested_action = item.get("suggested_action", issue.suggested_action)
        except Exception as exc:
            self._log(f"  Warning: explanation generation failed for {entry.entry_id}: {exc}. Using local text.")
            return self._apply_local_issue_text(entry, issues)

        return issues

    def _summary_looks_incomplete(self, summary: str) -> bool:
        summary = summary.strip()
        if len(summary.split()) < 12:
            return True
        return summary[-1] not in ".!?"

    def _build_local_summary(self, result: ValidationResult) -> str:
        invalid_entries = [entry for entry in result.entries if entry.status == ValidationStatus.INVALID]
        review_entries = [entry for entry in result.entries if entry.status == ValidationStatus.NEEDS_REVIEW]

        parts = [
            (
                f"Validation complete for {result.total_entries} entries: "
                f"{result.valid_count} valid, {result.invalid_count} blocked, and "
                f"{result.needs_review_count} needing review."
            )
        ]

        if invalid_entries:
            blocked = ", ".join(
                f"{entry.entry_id} ({'; '.join(issue.code for issue in entry.issues if issue.blocking)})"
                for entry in invalid_entries
            )
            parts.append(f"Blocked entries: {blocked}.")

        if review_entries:
            pending = ", ".join(entry.entry_id for entry in review_entries)
            parts.append(f"Review is still required for: {pending}.")

        parts.append("Resolve blocked items before close and clear any review items with the controller.")
        return " ".join(parts)

    def _generate_summary(self, result: ValidationResult) -> str:
        invalid_entries = [entry for entry in result.entries if entry.status == ValidationStatus.INVALID]
        review_entries = [entry for entry in result.entries if entry.status == ValidationStatus.NEEDS_REVIEW]

        summary_data = {
            "total_entries": result.total_entries,
            "valid": result.valid_count,
            "needs_review": result.needs_review_count,
            "invalid": result.invalid_count,
            "invalid_entries": [
                {
                    "id": entry.entry_id,
                    "description": entry.description,
                    "issues": [issue.code for issue in entry.issues if issue.severity == Severity.ERROR],
                }
                for entry in invalid_entries
            ],
            "review_entries": [
                {
                    "id": entry.entry_id,
                    "description": entry.description,
                    "warnings": [issue.code for issue in entry.issues if issue.severity == Severity.WARNING],
                }
                for entry in review_entries
            ],
        }

        prompt = f"""Write a short finance-facing summary of this validation run.

{json.dumps(summary_data, indent=2)}

Use 3 to 5 sentences. Mention counts, blocked entries, and what needs to happen next.
"""

        if not self.llm_available:
            return self._build_local_summary(result)

        try:
            summary = self._generate_text(prompt, max_output_tokens=500)
            if self._summary_looks_incomplete(summary):
                raise ValueError(f"Incomplete summary returned by model: {summary!r}")
            return summary
        except Exception as exc:
            self._log(f"  Warning: summary generation failed: {exc}. Using local summary.")
            return self._build_local_summary(result)

    def _make_audit_entry(
        self,
        entry: ManualAdjustment,
        status: ValidationStatus,
        issues: list[ValidationIssue],
    ) -> AuditTrailEntry:
        return AuditTrailEntry(
            source_type="manual_adjustment_validation",
            source_ref=entry.entry_id,
            account_code=",".join(line.account_code for line in entry.lines),
            field_affected="status",
            value_before=None,
            value_after=None,
            narrative=(
                f"Entry {entry.entry_id} ('{entry.description}') validated. "
                f"Status: {status.value}. "
                f"Issues found: {len(issues)}. "
                f"Blocking errors: {sum(1 for issue in issues if issue.blocking)}."
            ),
            agent="ManualAdjustmentsAgent",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def validate(self, entries: list[ManualAdjustment]) -> ValidationResult:
        self._log(f"Starting validation of {len(entries)} entries...")
        result = ValidationResult(
            period=entries[0].period if entries else "",
            total_entries=len(entries),
        )

        status_labels = {
            ValidationStatus.VALID: "VALID",
            ValidationStatus.INVALID: "INVALID",
            ValidationStatus.NEEDS_REVIEW: "NEEDS_REVIEW",
        }

        for entry in entries:
            self._log(f"  Checking {entry.entry_id}: {entry.description[:50]}...")

            issues = self._validate_entry(entry, entries)
            status = self._assign_status(issues)

            if issues:
                self._log(f"    Found {len(issues)} issue(s). Generating explanations...")
                issues = self._enrich_issues(entry, issues)

            entry.status = status
            entry.issues = issues
            entry.audit_trail.append(self._make_audit_entry(entry, status, issues))

            result.entries.append(entry)
            result.audit_trail.extend(entry.audit_trail)

            self._log(f"    {status_labels[status]}")

        result.valid_count = sum(1 for entry in result.entries if entry.status == ValidationStatus.VALID)
        result.invalid_count = sum(1 for entry in result.entries if entry.status == ValidationStatus.INVALID)
        result.needs_review_count = sum(
            1 for entry in result.entries if entry.status == ValidationStatus.NEEDS_REVIEW
        )

        self._log("Generating summary...")
        result.summary_plain_english = self._generate_summary(result)
        self._log(
            f"Done. {result.valid_count} valid, "
            f"{result.invalid_count} invalid, "
            f"{result.needs_review_count} for review."
        )
        return result
