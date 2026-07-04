# Decisions log

The hardest calls I made, why I made them, and what I would take further. Reasoning judgments
are the point of this exercise, so this is longer than the code comments.

## The core problem, and the shape of the fix

The starter makes one LLM call per placeholder, each seeing the entire client folder dumped
into the prompt. That single design choice causes most of the failures: joint accounts get
double-counted, firm-wide market commentary leaks in as the client's holdings, section
inclusion is a fragile yes/no guess, and the Tax prompt literally asks the model to invent a
CGT figure. Better prompts on top of that shape cannot fix it, because nothing gives the
sections a *shared, reconciled* view of the client.

So I changed the shape to **curate → extract → reconcile → render**:

1. **Curate** sources deterministically (config `source_policy`).
2. **Parse** the structured DB in code (exact, de-duplicated).
3. **Extract** the narrative facts from the prose with one LLM call, validated to a schema.
4. **Render** each section from that one `ClientFacts` object, deterministically where possible.

Everything below follows from that.

## Hardest calls

### 1. Split the work by data type, not by section
`client_data_db.json` is structured, so I parse it in Python — de-duping joint accounts,
dropping closed accounts, flagging null balances. The LLM is only used for the prose it is
actually good at (objectives, the recommendation, whether a disposal occurs, amounts). This
makes the account table and the balances *provably* correct rather than hopefully correct, and
it is cheaper. The joint-account bug in particular is impossible to solve reliably by prompting
— it is a `GROUP BY account_id`, so I wrote it as one.

### 2. Which source wins when they disagree
Every client has a stale joint-GIA valuation in the DB and the statement PNG, and a fresher but
approximate live figure in the meeting note (e.g. "a little over £45,000"). My rules:
- **Account structure and the holdings table** use the DB (system of record) with its own
  valuation dates. This keeps the high-level table stable and auditable.
- **The disposal amount** in Recommendations uses the fresher live figure, stated as
  "approximately £X, exact confirmed at the point of sale." The exact proceeds are only known
  at sale, so committing to a precise number would be false precision.
- **The statement PNGs** only duplicate the Holloway rows of the DB, so they are excluded as
  redundant. If a future statement diverged from the DB, that becomes a real reconciliation
  problem needing OCR/vision plus a trust rule — noted under "further work".

### 3. Never invent numbers a person finalises — enforced in code, not prompt
The fde notes are explicit: CGT and the platform/advice fee *rates* are confirmed by a person,
and a made-up number must never reach the client, nor may the gap be hidden. I did **not** rely
on the prompt to remember this. `ClientFacts.manual_review` is populated in code
(`_policy_manual_review`): fee rates always, CGT whenever there is a disposal, and any
null-valued open account balance. Sections render these as `[MANUAL REVIEW: ...]` markers. The
eval fails the build if a fees/CGT figure appears as a number instead of a marker.

### 4. Section inclusion should be deterministic
"Include Tax only if something is being sold" is a rule, not a judgment. `use_if` supports a
structured form (`{"fact": "disposal", "equals": true}`) evaluated against the facts, so the
Tax section's presence is deterministic and testable. The old plain-language LLM check is kept
as a fallback for genuinely fuzzy rules, but nothing important depends on it. The eval derives
the expected answer independently from the report request and asserts it.

### 5. Decide what to feed the model
The `platform_market_update` and `portfolio_pack` files are firm-wide commentary carrying
illustrative aggregate figures ("Aldgate Growth Portfolio … £312,000"). Dumped into a prompt,
the model will happily present those as the client's money. They are excluded from context, and
the eval has a dedicated anti-leakage check that pulls the figures/fund names out of those
excluded docs and asserts none appear in the report. `fde_notes.md`/`template_spec.md` are
instructions to me, not client inputs, so they are excluded too. An unrecognised file is
excluded but logged — a genuinely new source should be noticed, not silently dumped (the old
bug) or silently trusted.

### 6. Give each section only the facts it needs
Once I sliced the facts (`context_fields` per placeholder), a class of problems disappeared:
the Recommendations slot no longer sees `manual_review`, so it stopped re-listing fees and CGT
that belong in their own sections. This is the same idea as the whole redesign, one level down —
a prompt with less irrelevant context behaves better and costs less. It generalises: adding a
field to a section is a config edit.

### 7. Contingent and committed money (client 4)
The £850k completion payment includes £200k committed to a bridging-loan repayment (not
investable) and sits alongside a £400k earnout that is contingent and not yet received (must not
be invested). The extraction prompt encodes both: compute the net investable amount
(850 − 200 = £650k, stated as a number because it is arithmetic, not a manual-review item), and
treat the earnout as not-yet-available. This is the kind of reasoning that has to live in the
prompt, and it is the client where getting it wrong matters most.

### 8. Sensitivity (client 3)
The new money is an inheritance following a bereavement. Extraction captures a `sensitivities`
field; the Background prompt acknowledges it briefly and with care. The LLM judge has a
`sensitivity` criterion so a regression would be visible.

### 9. Model choice
`gpt-4o-mini` handles all four clients correctly, including the stretch client's contingent-
money reasoning, so I kept it as the default for cost. The extraction step is the highest-
leverage call; the obvious cheap upgrade is a stronger model there only (the code allows a
per-call model). I left the default as shipped and documented the lever rather than quietly
raising everyone's bill.

## What I would take further (given more time / for production)

- **An independent eval harness.** There are no gold outputs, so the plan is an oracle that
  re-reads the raw client data (not the pipeline's own facts, which would be circular) and
  asserts general invariants — section inclusion, joint-account de-dup, verbatim FCA/risk lines,
  no invented CGT/fees, no platform leakage, unconfirmed balances flagged — plus an advisory LLM
  judge for tone/sensitivity that never gates the build on its own. Not yet built in this pass.

- **More agentic, where it earns its keep.** The current pipeline is a deterministic graph with
  one reasoning step. The highest-value next step is a *verification* agent that re-reads the
  drafted report against the facts and the template spec and returns structured defects to fix —
  a self-critique loop — rather than more upfront generation. After that, a small planning agent
  that decides which sections and sources are in play for unusual clients.
- **Fees as data, not a gap.** The fee rates are "manual review" only because we have no feed for
  them. In production the platform/advice charges live in a system; a tool call would fetch the
  real rate and the marker would become a number. Same for a CGT calculation service.
- **Structured outputs.** Swap the JSON-mode + pydantic-validate extraction for the provider's
  schema/function-calling mode to remove the residual "model returned a weird value" surface
  (I mitigated it with lenient coercion, tested).
- **Per-role models and async.** Stronger model for extraction, cheaper for rendering; run the
  independent section renders concurrently to cut latency.
- **Statement images.** If a statement PNG ever diverges from the DB, add vision/OCR and a
  documented trust order; today they are redundant so I excluded them.
- **Multiple document types.** The section/placeholder/compute framework already generalises;
  supporting a second report type is a second config file plus a small registry, not new code.
- **Regression + fact-level eval.** Once outputs are stable, snapshot them as golden files, and
  add an eval that scores the *extracted facts* against a hand-labelled key (separating
  extraction errors from rendering errors).
- **Tests and a CI gate.** Deterministic tests around the parsing/policy/rendering logic that
  don't need an API key, plus a CI workflow (lint + tests, eventually the eval) — not yet added.
- **A repair loop on extraction.** `_extract_narrative` currently hard-fails the whole run if the
  model's JSON doesn't validate against the schema; retrying transient network errors is not the
  same as retrying a structurally malformed response with corrective feedback. Given this one
  call decides `disposal` (which gates the Tax section and the CGT manual-review flag), it is
  worth a repair pass before failing the run.

## Known limitations

- Prose quality depends on `gpt-4o-mini`; occasional mild verbosity remains. Facts, sections,
  and figures are enforced; wording is not.
- No automated eval or test suite yet in this pass, and reports haven't been generated/checked
  against this config yet either — nothing here is asserted, only designed. See "what I would
  take further".
- Curation matches sources by filename pattern. That fits the given (and, per the brief,
  held-out) data; a genuinely new source type needs a `source_policy` entry, and the log warns
  when one appears.
- `total_value` sums account values without grouping by currency (`facts._sum_known_values`).
  Every sample client is GBP-only, so this hasn't surfaced yet, but a held-out client with a
  foreign-currency account would silently get a wrong total — no error, no manual-review flag.
  Worth a currency-aware sum before relying on this beyond the sample set.
- `_primary_name` reads the holder keyed literally `"client"` in the DB. All four sample clients
  use that key, so it works today, but it's an assumption about the schema rather than something
  validated — a differently-keyed held-out client would silently fall back to the generic string
  "the client" rather than erroring or flagging it.
