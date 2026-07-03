"""Derive expectations about a report from the RAW client data.

The eval must be independent of the generator, or it only checks that the pipeline agrees
with itself. So the oracle re-reads the source files directly (not the pipeline's facts) and
works out what a correct report must and must not contain. Every rule is general — it reads
whatever the data says — so it holds on the held-out client set.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from document_formatter.loading import read_file

# Firm-wide documents whose figures/names must never appear as the client's own.
_LEAK_SOURCES = ("platform_market_update", "portfolio_pack")
_MONEY = re.compile(r"£\s?([\d,]+(?:\.\d+)?)")


@dataclass
class Expectations:
    client: str
    expected_account_count: int          # de-duped, excluding closed
    has_unconfirmed_balance: bool         # any open account with a null value
    disposal_expected: bool               # from the report request
    leak_figures: set[int] = field(default_factory=set)   # aggregate £ figures from excluded docs
    leak_names: set[str] = field(default_factory=set)      # model-portfolio / fund proper names
    db_figures: set[int] = field(default_factory=set)      # legitimate client figures


def build_expectations(client_dir: Path) -> Expectations:
    raw_db = json.loads((client_dir / "client_data_db.json").read_text(encoding="utf-8"))

    accounts, unconfirmed, db_figures = _dedupe_accounts(raw_db)
    disposal = _disposal_from_request(client_dir)
    leak_figures, leak_names = _leak_signals(client_dir)

    return Expectations(
        client=client_dir.name,
        expected_account_count=len(accounts),
        has_unconfirmed_balance=unconfirmed,
        disposal_expected=disposal,
        leak_figures=leak_figures - db_figures,  # a coincidental overlap is not a leak
        leak_names=leak_names,
        db_figures=db_figures,
    )


def _dedupe_accounts(raw_db: dict) -> tuple[list[str], bool, set[int]]:
    seen: set[str] = set()
    kept: list[str] = []
    unconfirmed = False
    figures: set[int] = set()
    for holder in raw_db.get("holders", {}).values():
        for entry in holder.get("accounts", []):
            aid = entry.get("account_id")
            if aid in seen:
                continue
            seen.add(aid)
            if entry.get("status") == "closed":
                continue
            kept.append(aid)
            value = entry.get("value")
            if value is None:
                unconfirmed = True
            elif value:
                figures.add(round(value))
    return kept, unconfirmed, figures


def _disposal_from_request(client_dir: Path) -> bool:
    request = read_file(client_dir / "report_request.docx")
    match = re.search(r"Selling existing investments\?\s*\|\s*(\w+)", request, re.IGNORECASE)
    return bool(match) and match.group(1).strip().lower().startswith("y")


def _leak_signals(client_dir: Path) -> tuple[set[int], set[str]]:
    figures: set[int] = set()
    names: set[str] = set()
    for path in client_dir.iterdir():
        if not any(tag in path.name.lower() for tag in _LEAK_SOURCES):
            continue
        text = read_file(path)
        figures.update(round(float(m.replace(",", ""))) for m in _MONEY.findall(text))
        # Proper names of model portfolios / funds, e.g. "Aldgate Growth Portfolio".
        names.update(
            m.group(1)
            for m in re.finditer(
                r"\b([A-Z][a-z]+(?: [A-Z][a-z-]+)* (?:Portfolio|Fund))\b", text
            )
        )
    return figures, names
