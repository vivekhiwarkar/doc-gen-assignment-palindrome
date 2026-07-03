"""Deterministic checks over a generated report.

Each check is a pure function of the report text and the oracle's expectations, returning a
:class:`CheckResult`. Checks encode what "correct" means for these reports; none hard-code a
particular client's numbers, so the same suite runs on the held-out set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from evals.oracle import Expectations

FCA_LINE = "This firm is authorised and regulated by the Financial Conduct Authority."
RISK_WARNING = (
    "The value of investments can fall as well as rise and you may get back less than you "
    "invest. Past performance is not a guide to future returns."
)
REQUIRED_SECTIONS = [
    "Introduction",
    "Background & Objectives",
    "Recommendations",
    "Fees & Charges",
    "Conclusion",
]
TAX_SECTION = "Tax Implications"
MANUAL_MARKER = "[MANUAL REVIEW"
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%")
_MONEY_INT = re.compile(r"£\s?([\d,]+)")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def sections(report: str) -> dict[str, str]:
    """Split a report into {section title: body}."""
    out: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in report.splitlines():
        if line.startswith("## "):
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out


def run_all(report: str, exp: Expectations) -> list[CheckResult]:
    secs = sections(report)
    return [
        _required_sections(secs),
        _tax_section_matches_disposal(secs, exp),
        _holdings_table_rows(secs, exp),
        _fca_line_verbatim(report),
        _risk_warning_verbatim(report),
        _fees_flagged_not_invented(secs),
        _cgt_flagged_if_disposal(secs, exp),
        _unconfirmed_balance_flagged(report, exp),
        _no_platform_leakage(report, exp),
    ]


def _required_sections(secs: dict[str, str]) -> CheckResult:
    missing = [s for s in REQUIRED_SECTIONS if s not in secs]
    return CheckResult("required_sections_present", not missing, f"missing={missing}")


def _tax_section_matches_disposal(secs: dict[str, str], exp: Expectations) -> CheckResult:
    present = TAX_SECTION in secs
    ok = present == exp.disposal_expected
    return CheckResult(
        "tax_section_matches_disposal",
        ok,
        f"disposal_expected={exp.disposal_expected}, tax_section_present={present}",
    )


def _holdings_table_rows(secs: dict[str, str], exp: Expectations) -> CheckResult:
    body = secs.get("Background & Objectives", "")
    rows = [
        ln
        for ln in body.splitlines()
        if ln.strip().startswith("|")
        and "---" not in ln
        and not re.match(r"\|\s*Account\s*\|", ln)
    ]
    ok = len(rows) == exp.expected_account_count
    return CheckResult(
        "holdings_table_row_count",
        ok,
        f"expected={exp.expected_account_count}, found={len(rows)} (joint accounts must appear once)",
    )


def _fca_line_verbatim(report: str) -> CheckResult:
    return CheckResult("fca_line_verbatim", FCA_LINE in report, "exact FCA authorisation line")


def _risk_warning_verbatim(report: str) -> CheckResult:
    return CheckResult("risk_warning_verbatim", RISK_WARNING in report, "exact risk warning")


def _fees_flagged_not_invented(secs: dict[str, str]) -> CheckResult:
    body = secs.get("Fees & Charges", "")
    has_marker = MANUAL_MARKER in body
    invented = _PERCENT.search(body)
    ok = has_marker and not invented
    detail = f"has_manual_marker={has_marker}, invented_rate={invented.group(0) if invented else None}"
    return CheckResult("fees_flagged_not_invented", ok, detail)


def _cgt_flagged_if_disposal(secs: dict[str, str], exp: Expectations) -> CheckResult:
    if not exp.disposal_expected:
        return CheckResult("cgt_flagged_if_disposal", True, "n/a (no disposal)")
    body = secs.get(TAX_SECTION, "")
    ok = MANUAL_MARKER in body
    return CheckResult("cgt_flagged_if_disposal", ok, f"tax section has manual marker={ok}")


def _unconfirmed_balance_flagged(report: str, exp: Expectations) -> CheckResult:
    if not exp.has_unconfirmed_balance:
        return CheckResult("unconfirmed_balance_flagged", True, "n/a (no unconfirmed balances)")
    ok = MANUAL_MARKER in report
    return CheckResult("unconfirmed_balance_flagged", ok, f"manual marker present={ok}")


def _no_platform_leakage(report: str, exp: Expectations) -> CheckResult:
    report_figures = {int(m.replace(",", "")) for m in _MONEY_INT.findall(report)}
    leaked_figures = exp.leak_figures & report_figures
    leaked_names = {n for n in exp.leak_names if n in report}
    ok = not leaked_figures and not leaked_names
    return CheckResult(
        "no_platform_leakage",
        ok,
        f"leaked_figures={sorted(leaked_figures)}, leaked_names={sorted(leaked_names)}",
    )
