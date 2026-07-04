"""Turning reconciled facts into section content.

Inclusion is decided from the facts, deterministically wherever the config allows it (the Tax
section keys off `disposal`), which removes a fragile per-run yes/no call. Each placeholder is
either computed (deterministic), generated (one focused LLM call over the facts), or left as
static template text.
"""

from __future__ import annotations

import json
import logging

from agent_pipeline.compute import compute_placeholder
from agent_pipeline.facts import ClientFacts
from agent_pipeline.llm import LLMClient

logger = logging.getLogger(__name__)


class SectionRenderer:
    def __init__(self, llm: LLMClient, global_instructions: str) -> None:
        self._llm = llm
        self._global = global_instructions

    def render(self, config: dict, facts: ClientFacts) -> list[dict]:
        sections = []
        for section in config["sections"]:
            if not self._applies(section, facts):
                logger.warning("section %r excluded by use_if", section.get("id"))
                continue
            sections.append(
                {
                    "title": section.get("title", ""),
                    "content": self._fill(section, facts),
                }
            )
        return sections

    def _applies(self, section: dict, facts: ClientFacts) -> bool:
        rule = section.get("use_if", "always")
        if rule == "always":
            return True
        if isinstance(rule, dict):
            field = rule["fact"]
            return getattr(facts, field) == rule.get("equals", True)
        # Plain-language fallback: ask the model. Kept for flexibility; deterministic rules
        # (the dict form) are preferred and used for the sections that matter.
        verdict = self._llm.complete(
            self._global,
            f"Facts:\n{facts.model_dump_json(indent=2)}\n\n"
            f"Does this section apply? Rule: {rule}\nReply with only 'yes' or 'no'.",
        )
        return verdict.strip().lower().startswith("y")

    def _fill(self, section: dict, facts: ClientFacts) -> str:
        content = section["template"]
        for name, spec in section.get("placeholders", {}).items():
            kind = spec.get("kind", "generated")
            if kind == "computed":
                value = compute_placeholder(spec["compute"], facts)
            elif kind == "generated":
                value = self._generate(spec["prompt"], facts, spec.get("context_fields"))
            else:
                raise ValueError(f"unknown placeholder kind {kind!r} for {name!r}")
            content = content.replace(f"<<{name}>>", value)
        return content

    def _generate(
        self, prompt: str, facts: ClientFacts, fields: list[str] | None
    ) -> str:
        """Render one slot from the facts.

        ``context_fields`` restricts what the model sees to only the facts the slot needs.
        This is what stops, e.g., the Recommendations slot listing fees it should not see,
        and it keeps each call small.
        """
        data = facts.model_dump()
        if fields:
            data = {k: data[k] for k in fields if k in data}
        user = (
            f"=== Client facts (the only source you may use) ===\n"
            f"{json.dumps(data, indent=2, default=str)}\n\n"
            f"=== Task ===\n{prompt}"
        )
        return self._llm.complete(self._global, user)
