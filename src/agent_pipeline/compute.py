"""Deterministic placeholder builders.

Some slots should never be left to the model. The holdings table, in particular, must
de-duplicate joint accounts and show exact values; a computed builder guarantees that where a
prompt could not. Config selects one of these with ``{"kind": "computed", "compute": "<name>"}``.
"""

from __future__ import annotations

from collections.abc import Callable

from agent_pipeline.facts import Account, ClientFacts

MANUAL_BALANCE = "[MANUAL REVIEW: balance to be confirmed]"


def _format_value(account: Account) -> str:
    if account.value is None:
        return MANUAL_BALANCE
    if account.currency == "GBP":
        return f"£{account.value:,.0f}"
    return f"{account.currency} {account.value:,.0f}"


def holdings_table(facts: ClientFacts) -> str:
    """A markdown table of the covered accounts: Account | Owner | Type | Value.

    Accounts are already de-duplicated in the facts, so each joint account appears once.
    """
    header = "| Account | Owner | Type | Value |\n| --- | --- | --- | --- |"
    rows = [
        f"| {a.platform} | {a.owner} | {a.type} | {_format_value(a)} |"
        for a in facts.accounts
    ]
    return "\n".join([header, *rows])


# Registry of available computed builders. Adding one is a function plus an entry here.
COMPUTES: dict[str, Callable[[ClientFacts], str]] = {
    "holdings_table": holdings_table,
}


def compute_placeholder(name: str, facts: ClientFacts) -> str:
    try:
        builder = COMPUTES[name]
    except KeyError as exc:
        raise KeyError(f"unknown computed placeholder {name!r}; known: {sorted(COMPUTES)}") from exc
    return builder(facts)
