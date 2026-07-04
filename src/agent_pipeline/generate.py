"""Generate an advice report for a client from the template config.

Pipeline: curate the client's sources, reconcile them into one validated ``ClientFacts``
object with a single LLM call, then render each section from those facts.

Usage:
    python -m agent_pipeline.generate --client client_01_clean
    python -m agent_pipeline.generate --client client_01_clean --dump-facts
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from agent_pipeline.facts import ClientFacts, build_facts
from agent_pipeline.llm import LLMClient
from agent_pipeline.rendering import SectionRenderer
from agent_pipeline.sources import curate_sources
from document_formatter.formatting import format_document

logger = logging.getLogger(__name__)


def generate_report(
    client_dir: Path, config: dict, llm: LLMClient
) -> tuple[str, ClientFacts]:
    """Run the full pipeline for one client and return the report and the facts it was built from."""
    curated = curate_sources(client_dir, config.get("source_policy", {}))
    logger.info("sources included=%s excluded=%s", curated.included, curated.excluded)

    facts = build_facts(curated, config, llm)

    renderer = SectionRenderer(llm, config.get("global_instructions", ""))
    sections = renderer.render(config, facts)

    return format_document(config, sections), facts


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an advice report for a client.")
    parser.add_argument("--client", required=True, help="folder name under data/")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--config", type=Path, default=Path("config/template_config.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument(
        "--dump-facts",
        action="store_true",
        help="also write the reconciled facts JSON next to the report (for inspection/eval)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    client_dir = args.data_dir / args.client
    llm = LLMClient()

    report, facts = generate_report(client_dir, config, llm)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / f"{args.client}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"Wrote {out_path}")

    if args.dump_facts:
        facts_path = args.output_dir / f"{args.client}.facts.json"
        facts_path.write_text(facts.model_dump_json(indent=2), encoding="utf-8")
        print(f"Wrote {facts_path}")


if __name__ == "__main__":
    main()
