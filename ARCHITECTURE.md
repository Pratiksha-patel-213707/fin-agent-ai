# Architecture: AI-Native Financial Statement Generation System

**Candidate Submission — AI Agentic Engineer Take-Home**
**Period:** December 2024 | **Entity:** ACME-US | **Functional Currency:** USD

---

## 1. Problem Decomposition

"Generate financial statements from a trial balance" is not one problem. It is at least six distinct sub-problems, each with different reliability requirements and different answers to the question *should an LLM be involved here?*

| # | Sub-problem | Reliability requirement | LLM role |
|---|-------------|------------------------|----------|
| 1 | **TB ingestion & dedup** | Exact — duplicates corrupt totals | None — deterministic |
| 2 | **COA mapping & disambiguation** | High — wrong mapping = misclassified line item | Yes — disambiguation only |
| 3 | **Manual adjustment validation** | Exact — debit ≠ credit is a hard error | None for math; Yes for explanation |
| 4 | **FX translation** | Exact — formula-driven, gap = blocked | None — deterministic; LLM flags gaps |
| 5 | **Statement arithmetic** | Exact — arithmetic is not a prompt | None — deterministic |
| 6 | **Traceability & audit output** | Exact — every cell must trace to source | None — deterministic |

The reason to decompose this way is not architectural elegance — it is failure isolation. If COA mapping is wrong, you want that failure to be visible and contained. If it lives inside a single "generate financials" prompt, you can't tell where the error came from.

---

## 2. Agent Topology

### Decision: Orchestrator + Specialized Sub-agents

```
                         ┌─────────────────────────────┐
                         │      Orchestrator Agent      │
                         │  (coordinator, not executor)  │
                         └──────────┬──────────────────┘
                                    │
          ┌─────────────┬───────────┼───────────┬─────────────┐
          ▼             ▼           ▼           ▼             ▼
   ┌────────────┐ ┌──────────┐ ┌────────┐ ┌─────────┐ ┌──────────┐
   │  Ingestion │ │  Mapper  │ │ Adjus- │ │  State- │ │Validator │
   │   Agent   │ │  Agent   │ │  tment │ │  ment   │ │  Agent   │
   │           │ │          │ │  Agent │ │ Builder │ │          │
   └────────────┘ └──────────┘ └────────┘ └─────────┘ └──────────┘
   Deterministic  LLM-assisted Hybrid     Deterministic Deterministic
```

**Why not a single agent with tools?**

A single agent with tools works fine for simple pipelines. It fails here because:

1. **Different failure modes need different retry strategies.** A COA mapping failure (ambiguous account) is a human-in-the-loop pause. An arithmetic error is an immediate block. An FX gap is a configurable fallback. A monolithic agent can't express this cleanly.
2. **Auditability requires a clear chain of custody.** When an auditor asks "why is this revenue number $25,000 higher than last period?", the answer needs to trace through Adjustment Agent → Statement Builder → specific journal entry. One big agent blurs that chain.
3. **LLM calls must be surgically targeted.** In a monolithic agent, it's tempting to "just send the whole TB to Claude." That approach hallucinates account mappings, invents corrections for rounding errors, and produces untraceable output. Sub-agents enforce the contract: the LLM touches only the inputs it's qualified to reason about.

**Why not 12 agents?**

Because statement arithmetic, FX arithmetic, and audit trail generation are all deterministic functions, not reasoning tasks. Wrapping them in LLM agents adds latency, cost, nondeterminism, and hallucination risk with zero benefit.

---

## 3. Agent Responsibilities

### 3.1 Ingestion Agent *(deterministic)*

Parses the raw TB CSV and enforces structural integrity before any other agent runs.

**Checks it runs:**
- Detect duplicate account codes (ADJ: last-one-wins with WARNING, or error — configurable)
- Detect orphan accounts (in TB but not in COA) — quarantines them, flags for Mapper Agent
- Verify TB balances: `Σ debits_fc = Σ credits_fc` (within FX rounding tolerance)
- Detect FX rounding suspense — the $47.32 in account 1999 in the mock data is a classic NetSuite artifact

**Key design choice:** The Ingestion Agent never silently fixes data. It either passes clean data downstream, or it halts with a structured error report. Silent fixes destroy traceability.

### 3.2 Mapper Agent *(LLM-assisted)*

Maps TB accounts to COA nodes. This is where the LLM genuinely earns its keep.

**What the LLM does:**
- Given an unmatched or ambiguous account (e.g., "FX Suspense" which is in the TB but not the COA), it reasons about which COA node is the correct home
- Assigns a confidence score (0–1) to each mapping decision
- High confidence (>0.85): auto-accept, log decision
- Medium confidence (0.5–0.85): flag for human review with explanation
- Low confidence (<0.5): hard escalation — block processing for that account

**What the LLM does NOT do:**
- It does not modify account balances
- It does not decide whether an account is an asset or liability based on its balance sign — that's a COA property
- It does not invent new COA nodes

**Prompt design — why structure matters:**

```
You are a financial classification assistant. You will be given an unmatched 
account from a trial balance and the full chart of accounts hierarchy. 
Your job is to identify the single most likely COA mapping.

Account to classify:
  Code: 1999
  Name: FX Suspense Account
  Balance: $47.32 debit
  Source system: NetSuite

Chart of accounts candidates (filtered to top 5 by name similarity):
  [... structured COA nodes ...]

Respond ONLY with JSON:
{
  "recommended_code": "...",
  "confidence": 0.0-1.0,
  "reasoning": "...",
  "requires_human": true/false
}
```

By filtering to top-5 candidates first (using deterministic string similarity), we reduce the LLM's search space and the risk of it inventing a mapping from thin air.

### 3.3 Manual Adjustments Agent *(hybrid — see prototype)*

This is the slice we built. See Section 6 for full implementation detail.

**Validation pipeline:**
1. `check_debit_credit_balance` — arithmetic, deterministic
2. `check_accounts_exist_in_coa` — set membership, deterministic  
3. `check_circular_intercompany` — graph traversal, deterministic
4. `check_line_signs`, `check_single_sided_line` — structural, deterministic
5. LLM: translate machine errors → plain English for finance user
6. LLM: generate run summary for finance manager

The LLM never makes a correctness decision. It only explains what the deterministic rules already decided.

### 3.4 Statement Builder *(deterministic)*

Pure arithmetic. Given a clean, mapped, adjusted TB:

```python
# P&L
revenue     = sum(net_balance for accounts where statement='PL' and category='Income')
cogs        = sum(net_balance for accounts where subcategory='COGS')
gross_profit = revenue - cogs
opex        = sum(net_balance for accounts where subcategory='OpEx')
ebit        = gross_profit - opex
net_income  = ebit - interest_expense - tax_expense

# Balance Sheet — assets must equal liabilities + equity
total_assets = sum(net_balance for accounts where statement='BS' and category='Asset')
total_liabilities_equity = (
    sum(net_balance for accounts where statement='BS' and category='Liability') +
    sum(net_balance for accounts where statement='BS' and category='Equity')
)
assert abs(total_assets - total_liabilities_equity) < TOLERANCE, "BS does not balance"
```

**This is not a prompt. It is code.** The balance sheet check at the end is a hard assertion, not a plausibility check.

### 3.5 Validator Agent *(deterministic with LLM explanation)*

Runs after Statement Builder. Checks output integrity before release.

**Checks:**
- Balance sheet balances (`assets = liabilities + equity`)
- P&L net income reconciles to retained earnings movement in SOCIE
- Cash flow statement reconciles to cash movement on BS
- No line item on any statement is untraced (every number has a source)
- Prior-period comparatives reconcile to prior period TB (with renamed-account resolution)

If any check fails, the Validator Agent:
1. Blocks output
2. Routes back to the specific agent that produced the failing number
3. Logs the failure with full provenance

---

## 4. The LLM Earns Its Keep — And Only There

The hardest design decision in this system is the boundary between deterministic code and LLM reasoning. Here is the explicit contract:

### Use LLM for:

| Task | Why LLM | Guard rail |
|------|---------|------------|
| COA disambiguation for unmatched accounts | Natural language reasoning about accounting semantics | Confidence threshold + human escalation; LLM cannot modify balances |
| Plain-English rejection explanations | Finance users are not engineers | LLM only generates text; machine-readable status set deterministically |
| Renamed account reconciliation across periods | "Computer Software" → "Software Licenses" is a semantic match, not a string match | LLM proposes; human confirms if confidence < 0.85 |
| Summarizing validation runs for finance managers | Communication task | LLM generates summary from structured data; summary is informational, not authoritative |

### Never use LLM for:

| Task | Why not | What to use instead |
|------|---------|-------------------|
| Verifying debit = credit | This is addition | `assert abs(total_dr - total_cr) < 0.01` |
| Generating balance sheet numbers | Arithmetic is not a prompt | Pandas / plain Python |
| Deciding if the balance sheet balances | This is subtraction | Hard assertion |
| FX translation | Formula: `balance_lc × rate` | Lookup + multiply |
| Detecting duplicate account codes | Set membership | `df.duplicated()` |
| Circular reference detection | Graph traversal | DFS / cycle detection |

The failure mode of using an LLM where deterministic code belongs is subtle but catastrophic: the model will produce plausible-looking numbers that are wrong, and those numbers will propagate through the statements with no way to detect the error until an auditor catches it.

---

## 5. Failure Mode Handling

### 5.1 Hallucinated Account Mappings

**Problem:** LLM maps "Prepaid Marketing" to Revenue instead of Current Assets.

**Mitigation:**
- LLM only sees pre-filtered COA candidates (top-5 by similarity score), never the full COA
- Confidence threshold: anything below 0.85 escalates to human
- Audit trail records every LLM mapping decision with reasoning
- Post-mapping validation: verify mapped accounts produce a balancing TB
- If net effect of all LLM mappings on net income exceeds a materiality threshold, require human sign-off

### 5.2 Debit ≠ Credit After Adjustments

**Problem:** ADJ-008 in the mock data has a $500 imbalance.

**Mitigation:**
- Hard block at the Adjustment Agent — entry never reaches Statement Builder
- Imbalanced entries are quarantined with a structured error
- Re-check TB balance after all valid adjustments are applied — the TB must still balance
- Validator Agent checks the post-adjustment TB before Statement Builder runs

### 5.3 Account Fits No COA Node

**Problem:** Account 1999 (FX Suspense) in the mock data has no COA entry.

**Mitigation:**
- Ingestion Agent quarantines orphan accounts immediately
- Mapper Agent attempts to classify with LLM; if confidence < 0.5, hard escalation
- Quarantined accounts appear as a named exception on output: "3 accounts unclassified — see Appendix A"
- Statement Builder never silently drops orphan balances — they appear explicitly so materiality can be assessed

### 5.4 FX Rate Gaps

**Problem:** GBP period-end rate is missing in the mock data.

**Mitigation:**
- Ingestion Agent detects missing rates at load time (before any computation)
- Three configurable fallback strategies (set per-deployment, not per-run):
  1. `BLOCK` — hard error, halt processing (default for audit-grade output)
  2. `USE_PRIOR_DATE` — use most recent available rate, flag with WARNING
  3. `USE_AVERAGE` — use period-average rate as proxy, flag with WARNING
- All fallbacks produce a visible flag on the affected line item in output
- Auditor can see exactly which cells used an estimated rate

### 5.5 Circular Intercompany Entries

**Problem:** ADJ-010 in the mock data creates a circular IC elimination.

**Mitigation:**
- Adjustment Agent runs a directed graph traversal over all IC entries
- Cycle detected → all entries in the cycle are quarantined as a group
- Plain-English explanation generated for the finance team
- Note: the current prototype detects direct 2-node cycles; a full Kahn's algorithm implementation handles n-node chains (see Reflection)

---

## 6. Prototype: Manual Adjustments Agent

We chose this slice because it has the clearest contract between deterministic validation and LLM explanation, and because the mock data contains exactly the right seeded errors to demonstrate the failure handling.

### What was built

```
fin-agent/
├── core/
│   ├── models.py         # Typed dataclasses: ManualAdjustment, ValidationIssue,
│   │                     #   AuditTrailEntry, ValidationResult
│   ├── validators.py     # Deterministic rule engine (no LLM, fully tested)
│   ├── loaders.py        # CSV/JSON parsers → typed models
│   └── output.py         # JSON + text report formatters
├── agents/
│   └── adjustments_agent.py  # Orchestrates validation + LLM enrichment
├── data/                 # Mock data (all seeded errors present)
├── tests/
│   └── test_validators.py    # 13 unit tests, 0 LLM calls
└── main.py               # CLI entrypoint
```

### Seeded errors detected

| Entry | Error type | Caught by | Action |
|-------|-----------|-----------|--------|
| ADJ-008 | Debit ($22,500) ≠ Credit ($22,000) | `check_debit_credit_balance` | BLOCKED |
| ADJ-009 | Account 9999 not in COA | `check_accounts_exist_in_coa` | BLOCKED |
| ADJ-010 | Circular IC (structural, via account reuse) | Flagged for human review | NEEDS_REVIEW |

### Audit trail design

Every validation decision produces an `AuditTrailEntry`:

```python
AuditTrailEntry(
    source_type = "manual_adjustment_validation",
    source_ref  = "ADJ-008",
    narrative   = "Entry ADJ-008 validated. Status: INVALID. "
                  "Blocking errors: 1. Delta: $500.00 debit > credit.",
    agent       = "ManualAdjustmentsAgent",
    timestamp   = "2024-12-31T18:42:01+00:00",
)
```

An auditor can reconstruct every decision made by reading the audit trail in order. No black-box logic.

---

## 7. Traceability: How an Auditor Traces Any Cell

The question: *"How do I know where the $445,000 Service Revenue figure comes from?"*

Every number on every statement carries a lineage chain:

```
Statement cell: Service Revenue = $445,000
    ↓
Statement Builder: sum of COA subcategory 'Revenue' accounts tagged PL
    ↓  
Accounts contributing: 5020 (Service Revenue)
    ↓
5020 post-adjustment balance: $445,000
    = TB balance $420,000
    + ADJ-007 (Deferred Revenue recognition): +$25,000
    ↓
ADJ-007 audit trail:
    Entry ID:    ADJ-007
    Type:        revenue_recognition
    Date:        2024-12-31
    Prepared by: finance@acme.com
    Lines:       Dr 2300 Deferred Revenue $25,000
                 Cr 5020 Service Revenue  $25,000
    Status:      VALID (no issues detected)
    Validated:   2024-12-31T18:42:01Z by ManualAdjustmentsAgent
    ↓
TB source row for 5020:
    account_code: 5020, debit_fc: 0, credit_fc: 420000
    period: 2024-12, entity: ACME-US
    source_system: NetSuite export 2024-12-31
```

The design principle: **every number in every output is a function of source rows, not a product of inference.** The LLM never produces a number that goes into a financial statement.

---

## 8. Validation and Self-Correction Loop

```
┌─────────────────────────────────────────────────────────┐
│                    Orchestrator                          │
│                                                         │
│  1. Ingest → check structural integrity                 │
│     └─ FAIL → halt, report, do not proceed             │
│                                                         │
│  2. Map accounts → confidence scores                    │
│     └─ LOW CONF → human escalation queue               │
│     └─ HIGH CONF → proceed                             │
│                                                         │
│  3. Validate adjustments → deterministic rules          │
│     └─ ERROR → quarantine entry, continue with rest    │
│     └─ WARNING → flag, continue with rest              │
│                                                         │
│  4. Apply valid adjustments → re-check TB balance      │
│     └─ FAIL → something wrong with adjustment logic    │
│                                                         │
│  5. Build statements → arithmetic                       │
│     └─ BS doesn't balance → block, trace to source    │
│                                                         │
│  6. Validate output                                     │
│     └─ BS check                                        │
│     └─ P&L → RE reconciliation                        │
│     └─ CF → cash movement reconciliation              │
│     └─ All pass → release                             │
│     └─ Any fail → route back to responsible agent     │
│                                                         │
│  7. Generate audit output — attach lineage to every    │
│     cell before releasing statements                    │
└─────────────────────────────────────────────────────────┘
```

The self-correction loop runs at step 6. If the Balance Sheet doesn't balance after Statement Builder runs, the Orchestrator does not retry the LLM — it traces which accounts are responsible for the imbalance deterministically and routes the specific error to the finance team.

The reason not to use an LLM for self-correction here: a BS imbalance is not an ambiguity — it is a definite error with a definite cause. An LLM might suggest a plausible-sounding fix that is arithmetically wrong. The correct response is always to surface the exact delta and let a human resolve it.

---

## 9. Additional Issues in the Mock Data (Beyond the Spec)

The assignment said "we deliberately did not list every defect." Here are additional issues found:

| File | Issue | Severity | Handling |
|------|-------|----------|---------|
| `trial_balance.csv` | Account 1020 (Bank - USD) appears twice — duplicate rows | ERROR | Ingestion Agent detects via `df.duplicated('account_code')` |
| `trial_balance.csv` | Account 1999 (FX Suspense) not in COA | WARNING | Ingestion Agent quarantines; Mapper Agent attempts classification |
| `trial_balance.csv` | After deduplication and FX rounding, TB likely does not balance exactly | WARNING | Ingestion Agent computes debit/credit totals, reports delta |
| `chart_of_accounts.csv` | Accounts 1400 and 1800 have ambiguous cash flow category | WARNING | Mapper Agent flags; human must classify before CF statement builds |
| `chart_of_accounts.csv` | Account 5130 (Miscellaneous Income) has no parent explicitly mapped from TB | INFO | Validate hierarchy completeness at load time |
| `prior_period_tb.csv` | Account 1615 (Computer Software) renamed to 1620 (Software Licenses) | WARNING | Mapper Agent: LLM semantic match; require human confirmation |
| `fx_rates.csv` | GBP missing period-end rate | ERROR | Blocks BS FX translation for GBP unless fallback strategy configured |
| `manual_adjustments.json` | ADJ-010 re-eliminates same IC balance as ADJ-004, effectively double-eliminating | ERROR | Detected by circular IC check; both entries quarantined pending review |

---

## 10. Production Considerations

**Idempotency:** Every run is identified by `(entity, period, run_id)`. Running the same inputs twice produces identical outputs. The run_id is deterministic (hash of inputs) so duplicate submissions are detected.

**Human-in-the-loop:** The system has three escalation levels:
- `AUTO_ACCEPT` — high-confidence decisions, logged but not surfaced
- `REVIEW_QUEUE` — low-confidence or warning-level items, finance team reviews before close
- `HARD_BLOCK` — errors, nothing proceeds until resolved

**Concurrency:** Each agent is stateless and can run in parallel where dependencies allow (Ingestion → Mapper, Adjuster in parallel → Statement Builder).

**Multi-entity consolidation:** The current design handles a single entity. Multi-entity requires a Consolidation Agent above the Orchestrator that runs entity-level pipelines first, then eliminates IC balances across entities before building consolidated statements.

**Scale:** At 1,000+ accounts, the main bottleneck is the Mapper Agent's LLM calls for ambiguous accounts. Mitigation: cache mapping decisions (same account code + same name = same decision), batch similar accounts into a single prompt, and use a fine-tuned smaller model for well-defined mapping tasks.
