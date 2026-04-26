# Reflection

## What I Would Build Differently with 3 Months

The prototype validates adjustments well but stops before the hardest part: the Statement Builder and its reconciliation chain. With three months I would:

**Build the full Statement Builder with cell-level lineage.** Every output cell carries a `lineage` object: a list of (source_type, source_ref, account_code, amount, rate) tuples. This is the foundation that makes the product genuinely useful in an audit — not just "the number is right" but "here is the exact path from raw TB row to this cell."

**Replace the COA mapper stub with a real fine-tuned classifier.** The architecture describes LLM-based mapping with confidence scores, but the real production version would start with a labeled dataset of (account_name, account_description) → COA_node pairs from real ERP exports, fine-tune a small model on it, and use the frontier model only for genuinely novel cases. This cuts latency and cost by ~80% on the happy path.

**Add a proper consolidation layer.** Single-entity is a toy. The IC elimination problem — detecting that two entities have mirror entries that net to zero — requires a proper graph-based IC ledger, not just checking `intercompany_ref` fields that humans may or may not populate.

**Make the human-in-the-loop queue a real interface.** Right now, "escalate to human" means "print a warning." In production it means a review UI where a finance controller can see the proposed mapping, the LLM's reasoning, and click approve/reject. The architecture supports this; it wasn't worth building for a prototype.

---

## Where the Prototype Would Break at Scale

**1,000+ accounts, multi-entity consolidation:**

- The circular IC detection uses a naive O(n²) graph check. At 50-entity consolidation with thousands of IC entries, this needs to be replaced with a proper DFS cycle detector.
- The LLM enrichment loop calls the API once per rejected entry. At 100 entries with 40% rejection rate, that's 40 sequential API calls. At scale, batch these with a single multi-entry prompt.
- The COA ambiguity flags for accounts 1400 and 1800 become a real problem when you have 20 entities each with slightly different COA interpretations. The system needs a canonical COA layer with per-entity override mappings, not just flags.
- Statement Builder currently loads the full TB into memory. At multi-entity consolidation with 10,000+ accounts, this needs to be a database-backed query, not a pandas DataFrame.

---

## How I Used AI Tools

**Claude (this conversation):** Used heavily for scaffolding the Python dataclass structure, validator functions, and the architecture document. The workflow was: write the design in my head, use Claude to generate the boilerplate fast, then review every line and correct the parts that were wrong or didn't reflect the actual design intent.

**Where it helped:** Dataclass definitions, test case generation (it correctly anticipated the edge cases for balance tolerance and sign checks), and getting a first draft of the architecture document that I then restructured significantly.

**Where it led me wrong:** Claude's first attempt at the circular IC detection was too clever — it tried to build a full graph traversal when the mock data only needed a 2-node check. The over-engineered version would have passed the tests but obscured the limitation I needed to document honestly. I replaced it with the simpler version and noted the gap explicitly.

**One consistent issue:** Claude tends to put optimistic language in validation failures ("this might need review") where the finance context requires unambiguous language ("this is blocked"). Had to edit all user-facing strings to remove hedging.

---

## One Thing About This Problem That I Think Is Being Underestimated

**The COA is never stable.** The assignment treats the chart of accounts as a fixed input. In reality, COAs change mid-period: accounts get renamed, reclassified, split, merged, deprecated. The prior-period TB in the mock data already demonstrates this with account 1615 → 1620.

The hard problem isn't mapping a static COA — it's maintaining a versioned COA where every change is recorded with an effective date, and historical statements can be restated using the COA that was current at the time, or restated using the new COA for comparability. This is what makes year-over-year comparison non-trivial: you're not just comparing numbers, you're comparing numbers computed under potentially different classification rules.

A system that doesn't version the COA will produce correct statements for the current period and silently produce wrong comparatives the moment any account is reclassified. That's not a validation failure — it's invisible. It's the kind of bug that lives in production for two years until an auditor spots a line item that doubled in one period because it absorbed a renamed account from prior year.

The fix is: treat the COA as an append-only log of classification rules, each with an effective date, and make the Statement Builder explicitly aware of which COA version it's using for each period. This is a week of work to design correctly and a red flag if it's not in the product roadmap.
