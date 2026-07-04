"""Deciding what to feed the model, and parsing the parts that are structured.

Two jobs live here:

1. **Curation.** The starter dumped every file in the folder into every prompt. That leaks
   firm-wide market commentary (with illustrative aggregate figures) in as if it were the
   client's holdings. :func:`curate_sources` applies the config's ``source_policy`` to keep
   only client-specific prose in the model's context.

2. **Structured parsing.** ``client_data_db.json`` is the system of record for account
   structure. We parse it deterministically rather than asking the model to read it, which
   guarantees joint accounts are de-duplicated and values/dates are exact.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from document_formatter.loading import read_file

logger = logging.getLogger(__name__)


@dataclass
class ParsedAccount:
    """One account after de-duplication. Mirrors the DB fields we rely on."""

    account_id: str
    platform: str
    type: str
    owner: str
    status: str
    value: float | None
    currency: str
    valuation_date: str | None


@dataclass
class CuratedSources:
    """The result of applying the source policy to a client folder."""

    context_text: str  # client-specific prose, ready for the LLM
    raw_db: dict
    snapshot_date: str | None
    accounts: list[ParsedAccount]
    unconfirmed_accounts: list[ParsedAccount] = field(default_factory=list)
    included: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)


def _matches(name: str, patterns: list[str]) -> bool:
    lower = name.lower()
    return any(p.lower() in lower for p in patterns)


def curate_sources(client_dir: Path, policy: dict) -> CuratedSources:
    """Read a client folder and split it into structured data, LLM context, and excluded noise."""
    db_match = policy.get("structured_db_match", "client_data_db.json")
    include_patterns = policy.get("context_include_patterns", [])
    exclude_patterns = policy.get("exclude_patterns", [])

    files = sorted(p for p in client_dir.iterdir() if p.is_file())

    raw_db: dict = {}
    context_parts: list[str] = []
    included: list[str] = []
    excluded: list[str] = []

    for path in files:
        name = path.name
        if db_match in name:
            raw_db = json.loads(path.read_text(encoding="utf-8"))
            continue  # structured data is parsed, not concatenated into prose
        if _matches(name, exclude_patterns):
            excluded.append(name)
            continue
        if _matches(name, include_patterns):
            context_parts.append(f"=== {name} ===\n{read_file(path)}")
            included.append(name)
            continue
        # Unknown source: exclude from context but shout about it, so a genuinely new
        # relevant source gets noticed rather than silently dumped (the old bug) or trusted.
        excluded.append(name)
        logger.warning(
            "Unclassified source %r excluded from context. Add it to source_policy if relevant.",
            name,
        )

    accounts, unconfirmed = _parse_accounts(raw_db)

    return CuratedSources(
        context_text="\n\n".join(context_parts),
        raw_db=raw_db,
        snapshot_date=raw_db.get("snapshot_date"),
        accounts=accounts,
        unconfirmed_accounts=unconfirmed,
        included=included,
        excluded=excluded,
    )


def _parse_accounts(raw_db: dict) -> tuple[list[ParsedAccount], list[ParsedAccount]]:
    """De-duplicate joint accounts and separate out those needing manual confirmation.

    A jointly-held account is stored under each holder with the same ``account_id``; we keep
    the first occurrence only. Closed accounts are dropped (not current holdings). Open
    accounts with a missing value are kept but returned separately so the caller can flag
    them for manual review rather than silently omit them.
    """
    seen: set[str] = set()
    accounts: list[ParsedAccount] = []
    unconfirmed: list[ParsedAccount] = []

    for holder in raw_db.get("holders", {}).values():
        for entry in holder.get("accounts", []):
            account_id = entry.get("account_id")
            if account_id in seen:
                continue  # joint account already recorded under another holder
            seen.add(account_id)

            account = ParsedAccount(
                account_id=account_id,
                platform=entry.get("platform", ""),
                type=entry.get("type", ""),
                owner=entry.get("owner", ""),
                status=entry.get("status", ""),
                value=entry.get("value"),
                currency=entry.get("currency", "GBP"),
                valuation_date=entry.get("valuation_date"),
            )

            if account.status == "closed":
                continue  # e.g. an old account explicitly disregarded; not a holding

            accounts.append(account)
            if account.value is None:
                unconfirmed.append(account)

    return accounts, unconfirmed
