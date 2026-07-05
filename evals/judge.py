"""An optional LLM-as-judge for the qualities rules struggle with.

Deterministic checks own the hard pass/fail (facts, sections, verbatim text). The judge covers
tone, sensitivity, and whether deferred aspirations leaked in — things that need reading, not
regex. It returns normal :class:`CheckResult`s and degrades gracefully when no funded model is
available, so `--judge` never breaks a run.
"""

from __future__ import annotations

from evals.checks import CheckResult
from agent_pipeline.llm import LLMClient, LLMError

_RUBRIC = """You are auditing a UK financial advice report for QUALITY, not facts.
Return a JSON object exactly like:
{ "criteria": [ { "name": string, "passed": boolean, "detail": string } ] }

Judge exactly these criteria:
- "british_english_first_person": written in British English, first person plural ('we'), addressing the client as 'you'.
- "professional_tone": clear, professional, no filler or invented reassurance.
- "sensitivity": if the report touches a bereavement or an inheritance from a late relative, it is handled with appropriate care; otherwise pass.
- "no_out_of_scope": the report does not advise on, or recommend actioning, DEFERRED PERSONAL ASPIRATIONS the client asked to leave for later — specifically gifting to family/grandchildren, school fees, charitable donations, holidays, or buying property. IMPORTANT: an inheritance, business-sale proceeds, a loan repayment, and an earnout are the SUBJECT of the advice and are IN scope; never flag those. Pass unless a deferred aspiration above is actually advised on.
- "manual_review_not_hidden": figures a person must finalise (capital gains tax, fee rates, unconfirmed balances) appear as clear '[MANUAL REVIEW: ...]' markers rather than invented numbers, and no such gap is hidden. Pass as long as the markers are present and unambiguous; they need no further explanation."""


def judge_report(client: str, report: str) -> list[CheckResult]:
    try:
        llm = LLMClient()
        payload = llm.complete_json(_RUBRIC, f"Report to audit:\n\n{report}")
    except LLMError as exc:
        return [CheckResult("llm_judge", True, f"skipped ({exc})")]

    results = []
    for c in payload.get("criteria", []):
        results.append(
            CheckResult(f"judge:{c.get('name', '?')}", bool(c.get("passed")), c.get("detail", ""))
        )
    if not results:
        return [CheckResult("llm_judge", True, "judge returned no criteria")]
    return results
