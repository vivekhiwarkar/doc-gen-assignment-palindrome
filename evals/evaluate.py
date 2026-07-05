"""Run the evaluation suite over generated reports.

Deterministic checks always run and decide pass/fail. An optional LLM judge (`--judge`)
scores things rules struggle with — tone, sensitivity, and whether deferred aspirations leaked
in — and is skipped gracefully if no funded model is available.

Usage:
    python -m evals.evaluate --client client_01_clean
    python -m evals.evaluate --all --judge
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from evals.checks import CheckResult, run_all
from evals.judge import judge_report
from evals.oracle import build_expectations

CLIENTS = ["client_01_clean", "client_02_medium", "client_03_hard", "client_04_stretch"]


def evaluate_client(
    client: str, data_dir: Path, output_dir: Path, use_judge: bool
) -> tuple[list[CheckResult], list[CheckResult]]:
    """Return (gating deterministic results, advisory judge results)."""
    report_path = output_dir / f"{client}.md"
    if not report_path.exists():
        return [CheckResult("report_exists", False, f"missing {report_path}; run generation first")], []

    report = report_path.read_text(encoding="utf-8")
    exp = build_expectations(data_dir / client)
    gating = run_all(report, exp)
    advisory = judge_report(client, report) if use_judge else []
    return gating, advisory


def _print(client: str, gating: list[CheckResult], advisory: list[CheckResult]) -> bool:
    """Print results; only the deterministic (gating) results decide pass/fail."""
    print(f"\n=== {client} ===")
    all_ok = True
    for r in gating:
        mark = "PASS" if r.passed else "FAIL"
        all_ok &= r.passed
        print(f"  [{mark}] {r.name}: {r.detail}")
    for r in advisory:
        # The LLM judge is a qualitative signal, not a gate; it never flips the exit code.
        mark = "ok" if r.passed else "NOTE"
        print(f"  [{mark}] (advisory) {r.name}: {r.detail}")
    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate generated advice reports.")
    parser.add_argument("--client", help="a single client folder name")
    parser.add_argument("--all", action="store_true", help="evaluate all built-in clients")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--judge", action="store_true", help="also run the LLM judge")
    args = parser.parse_args()

    load_dotenv()

    if args.all:
        clients = CLIENTS
    elif args.client:
        clients = [args.client]
    else:
        parser.error("pass --client <name> or --all")

    overall_ok = True
    for client in clients:
        gating, advisory = evaluate_client(client, args.data_dir, args.output_dir, args.judge)
        overall_ok &= _print(client, gating, advisory)

    print("\n" + ("ALL CHECKS PASSED" if overall_ok else "SOME CHECKS FAILED"))
    raise SystemExit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
