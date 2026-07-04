"""The single reconciled view of a client that every section is rendered from.

The pipeline's core idea: reconcile the sources *once* into a validated ``ClientFacts``
object, then render every section from that object instead of re-reading raw documents per
placeholder. This is what keeps figures consistent across sections and stops joint accounts
being double-counted.

Two inputs are merged:

- **Structured** account data, parsed deterministically in :mod:`sources` (exact, de-duped).
- **Narrative** facts (objectives, the recommendation, whether a disposal occurs, amounts,
  out-of-scope items), extracted from the prose sources by one LLM call.

Firm policy — CGT and fee *rates* are finalised by a person, unconfirmed balances must be
flagged — is applied in code (:func:`_policy_manual_review`) so it never depends on the model
remembering to do it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError, field_validator

from agent_pipeline.llm import LLMClient, LLMError
from agent_pipeline.sources import CuratedSources, ParsedAccount


class Account(BaseModel):
    account_id: str
    platform: str = ""
    type: str = ""
    owner: str = ""
    status: str = ""
    value: float | None = None
    currency: str = "GBP"
    valuation_date: str | None = None


class Amount(BaseModel):
    label: str
    value: float | None = None
    currency: str = "GBP"
    note: str | None = None  # source, date, or contingency (e.g. "not yet received")

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v: object) -> float | None:
        """Be forgiving: the model occasionally returns a string or a mangled list.

        A single unparseable amount should become 'unknown' (None), not crash the whole
        report. The accompanying note preserves whatever the model was trying to say.
        """
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            cleaned = v.replace(",", "").replace("£", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None


class ManualReviewItem(BaseModel):
    item: str
    reason: str


class ExtractedNarrative(BaseModel):
    """Schema the extraction LLM call must return. Kept flat and forgiving."""

    objectives: str = ""
    risk_profile: str | None = None
    report_scope: str = ""
    disposal: bool = False
    disposal_detail: str | None = None
    recommendation: str = ""
    amounts: list[Amount] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    sensitivities: list[str] = Field(default_factory=list)


class ClientFacts(BaseModel):
    """Everything a section needs, reconciled and validated."""

    client_name: str
    holders: list[str]
    snapshot_date: str | None
    accounts: list[Account]
    total_value: float | None
    objectives: str
    risk_profile: str | None
    report_scope: str
    disposal: bool
    disposal_detail: str | None
    recommendation: str
    amounts: list[Amount]
    manual_review: list[ManualReviewItem]
    out_of_scope: list[str]
    sensitivities: list[str]


def build_facts(curated: CuratedSources, config: dict, llm: LLMClient) -> ClientFacts:
    """Merge structured account data with LLM-extracted narrative into one validated object."""
    narrative = _extract_narrative(curated, config, llm)

    accounts = [Account(**vars(a)) for a in curated.accounts]
    total_value = _sum_known_values(curated.accounts)
    holders = _holder_names(curated.raw_db)

    manual_review = _policy_manual_review(curated, narrative)

    return ClientFacts(
        client_name=_primary_name(curated.raw_db),
        holders=holders,
        snapshot_date=curated.snapshot_date,
        accounts=accounts,
        total_value=total_value,
        objectives=narrative.objectives,
        risk_profile=narrative.risk_profile,
        report_scope=narrative.report_scope,
        disposal=narrative.disposal,
        disposal_detail=narrative.disposal_detail,
        recommendation=narrative.recommendation,
        amounts=narrative.amounts,
        manual_review=manual_review,
        out_of_scope=narrative.out_of_scope,
        sensitivities=narrative.sensitivities,
    )


def _extract_narrative(curated: CuratedSources, config: dict, llm: LLMClient) -> ExtractedNarrative:
    system = config.get("global_instructions", "")
    prompt = config["extraction"]["prompt"]
    accounts_summary = _accounts_summary(curated.accounts)

    user = (
        f"{prompt}\n\n"
        f"=== Parsed account data (system of record, already de-duplicated) ===\n"
        f"{accounts_summary}\n\n"
        f"=== Client-specific documents ===\n"
        f"{curated.context_text}"
    )
    payload = llm.complete_json(system, user)
    try:
        return ExtractedNarrative.model_validate(payload)
    except ValidationError as exc:  # pragma: no cover - defensive
        raise LLMError(f"extraction returned an object that failed validation: {exc}") from exc


def _policy_manual_review(
    curated: CuratedSources, narrative: ExtractedNarrative
) -> list[ManualReviewItem]:
    """Apply firm policy for what a person must finalise, independent of the model."""
    items: list[ManualReviewItem] = [
        ManualReviewItem(
            item="Platform charge (rate)",
            reason="Fee rates are confirmed by a person before issue; not to be estimated.",
        ),
        ManualReviewItem(
            item="Ongoing advice charge (rate)",
            reason="Fee rates are confirmed by a person before issue; not to be estimated.",
        ),
    ]
    if narrative.disposal:
        items.append(
            ManualReviewItem(
                item="Capital gains tax on the disposal",
                reason="CGT is finalised by a person; the draft must not estimate it.",
            )
        )
    for account in curated.unconfirmed_accounts:
        items.append(
            ManualReviewItem(
                item=f"Balance of {account.type} ({account.account_id})",
                reason="Value not captured at snapshot; confirm before the report is issued.",
            )
        )
    return items


def _sum_known_values(accounts: list[ParsedAccount]) -> float | None:
    known = [a.value for a in accounts if a.value is not None]
    return sum(known) if known else None


def _holder_names(raw_db: dict) -> list[str]:
    names = []
    for holder in raw_db.get("holders", {}).values():
        name = holder.get("name")
        if name:
            names.append(name)
    return names


def _primary_name(raw_db: dict) -> str:
    client = raw_db.get("holders", {}).get("client", {})
    return client.get("name", "the client")


def _accounts_summary(accounts: list[ParsedAccount]) -> str:
    if not accounts:
        return "(no accounts found)"
    lines = []
    for a in accounts:
        value = "unconfirmed" if a.value is None else f"{a.currency} {a.value:,.0f}"
        lines.append(
            f"- {a.account_id}: {a.platform} {a.type}, owner {a.owner}, "
            f"{value} (valued {a.valuation_date or 'n/a'})"
        )
    return "\n".join(lines)
