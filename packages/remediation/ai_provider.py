"""AI triage providers.

One live provider (Anthropic) and two offline, fully deterministic providers
used by every test in this repository and by CI. No live Anthropic
credential was available while building this slice -- ``AnthropicProvider``
is implemented and its request/response handling is unit-tested against
crafted payloads, but it has **not** been exercised against a real API call.
Nothing in this codebase or its documentation claims otherwise; see
``docs/AI_REMEDIATION.md``.

The LLM never touches SQL, the database, or any system directly. It receives
only a ``BreakFacts`` document and a list of candidate citations, and returns
only a JSON document that is validated against ``AIRecommendation`` before
anything downstream ever sees it.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

from .models import AIRecommendation, BreakFacts, Citation
from .retrieval import RetrievedSection

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are a post-trade reconciliation triage assistant for a \
synthetic FX trade-break platform. You analyse structured break facts only \
-- you have no database access, no tool use, and no ability to execute \
anything. You must respond with a single JSON object and nothing else, \
matching exactly this schema:

{
  "predicted_root_cause": string,
  "confidence": number between 0.0 and 1.0,
  "priority": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
  "recommended_action": "CORRECT_LEGACY_BOOKING_FIELD" | "MANUAL_INVESTIGATION" | null,
  "proposed_fields": object mapping a field path to its proposed value
    (empty if recommended_action is null),
  "risk_tier": "LOW" | "MEDIUM" | "HIGH",
  "required_approvals": array of "MAKER" and/or "CHECKER",
  "citations": array of {"document_id": string, "section": string},
  "abstain_reason": string or null
}

Rules you must follow:
- recommended_action and abstain_reason are mutually exclusive: set exactly
  one of them.
- You may cite ONLY document_id/section pairs given to you in the candidate
  citations below -- never invent a citation.
- The only recommended_action you may ever propose is
  CORRECT_LEGACY_BOOKING_FIELD, and only when the observed source is
  MOCK_LEGACY_BOOKING and the expected source is FIX_EXECUTION. Any other
  situation -- or anything resembling an order, a price decision, or a
  trading action -- must result in recommended_action: null with an
  abstain_reason.
- If you do recommend CORRECT_LEGACY_BOOKING_FIELD, proposed_fields must
  contain exactly one entry: the break's own field_path mapped to its own
  expected_value, verbatim. Never propose a different field or a different
  value.
- Output the JSON object only. No prose, no markdown fences."""


class AIProviderError(RuntimeError):
    """Raised when a provider cannot produce a schema-valid recommendation."""


class AIProvider(Protocol):
    name: str

    def recommend(
        self, *, facts: BreakFacts, candidate_citations: list[RetrievedSection]
    ) -> AIRecommendation: ...


def _build_user_prompt(facts: BreakFacts, candidate_citations: list[RetrievedSection]) -> str:
    payload = {
        "break_facts": facts.model_dump(mode="json"),
        "candidate_citations": [
            {"document_id": c.document_id, "section": c.section, "title": c.title}
            for c in candidate_citations
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def parse_recommendation(raw_text: str) -> AIRecommendation:
    """Parse and schema-validate a provider's raw JSON response.

    Raises ``AIProviderError`` for anything that isn't valid JSON matching
    ``AIRecommendation`` exactly -- malformed provider output must never
    reach the policy engine.
    """

    try:
        document = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise AIProviderError(f"provider response is not valid JSON: {error}") from error
    try:
        return AIRecommendation.model_validate(document)
    except Exception as error:  # noqa: BLE001 - re-raised as a typed provider error
        raise AIProviderError(f"provider response failed schema validation: {error}") from error


class AnthropicProvider:
    """Live provider. Never validated against a real API call in this repo."""

    name = "anthropic-live"

    def __init__(self, *, api_key: str | None = None, model: str = DEFAULT_ANTHROPIC_MODEL) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "")
        self._model = model

    def recommend(
        self, *, facts: BreakFacts, candidate_citations: list[RetrievedSection]
    ) -> AIRecommendation:
        if not self._api_key:
            raise AIProviderError(
                "ANTHROPIC_API_KEY is not configured; the live provider cannot run"
            )
        try:
            import anthropic
        except ImportError as error:
            raise AIProviderError("the 'anthropic' package is not installed") from error

        client = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(facts, candidate_citations)}],
        )
        text_parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        raw_text = "".join(text_parts)
        return parse_recommendation(raw_text)


class DeterministicTestProvider:
    """Offline, rule-based provider. Used by CI and every test in this repo.

    Supports exactly the one scenario this slice targets: an
    ECONOMIC_VALUE_MISMATCH on base_amount where the legacy booking system
    deviates from the authoritative FIX_EXECUTION value. Everything else
    abstains -- this provider does not attempt to generalise, matching the
    "one scenario only" scope of this PR.
    """

    name = "deterministic-test"

    def recommend(
        self, *, facts: BreakFacts, candidate_citations: list[RetrievedSection]
    ) -> AIRecommendation:
        supported = (
            facts.break_family == "ECONOMIC_VALUE_MISMATCH"
            and facts.field_path == "/payload/base_amount"
            and facts.expected_source_system == "FIX_EXECUTION"
            and facts.observed_source_system == "MOCK_LEGACY_BOOKING"
        )
        if not supported:
            return AIRecommendation(
                predicted_root_cause=(
                    "Break facts do not match the single automated-remediation "
                    "pattern this slice supports."
                ),
                confidence=0.2,
                priority="MEDIUM",
                recommended_action=None,
                proposed_fields={},
                risk_tier="LOW",
                required_approvals=[],
                citations=[],
                abstain_reason="unsupported_break_pattern",
            )

        preferred_keys = {"RB-001#3", "RB-002#1"}
        chosen_sections = [c for c in candidate_citations if c.citation_key in preferred_keys]
        if not chosen_sections:
            chosen_sections = candidate_citations[:2]
        chosen = [
            Citation(document_id=section.document_id, section=section.section)
            for section in chosen_sections
        ]

        return AIRecommendation(
            predicted_root_cause=(
                f"Legacy booking {facts.field_path} ({facts.observed_value}) diverges from "
                f"the authoritative {facts.expected_source_system} value "
                f"({facts.expected_value}) with no evidenced corporate action or rate "
                "revision, consistent with a booking data-entry error."
            ),
            confidence=0.93,
            priority="HIGH",
            recommended_action="CORRECT_LEGACY_BOOKING_FIELD",
            proposed_fields={facts.field_path: facts.expected_value},
            risk_tier="MEDIUM",
            required_approvals=["MAKER", "CHECKER"],
            citations=chosen,
            abstain_reason=None,
        )


class FixedRecommendationProvider:
    """Test-only provider that returns a pre-built recommendation verbatim.

    Lets tests exercise policy-rejection paths (invalid citation, low
    confidence, disallowed field) deterministically, without needing
    conditional branches inside DeterministicTestProvider for scenarios this
    product slice doesn't otherwise generate.
    """

    name = "fixed-test"

    def __init__(self, recommendation: AIRecommendation) -> None:
        self._recommendation = recommendation

    def recommend(
        self, *, facts: BreakFacts, candidate_citations: list[RetrievedSection]
    ) -> AIRecommendation:
        return self._recommendation


def get_provider(name: str | None = None) -> AIProvider:
    """Select the configured provider.

    Defaults to the deterministic provider. Only ``TRADEOPS_AI_PROVIDER=anthropic``
    selects the live provider -- CI and every test leave this unset.
    """

    configured: str = (
        name if name is not None else os.getenv("TRADEOPS_AI_PROVIDER", "deterministic")
    )
    selected = configured.strip().lower()
    if selected == "anthropic":
        return AnthropicProvider()
    return DeterministicTestProvider()


__all__ = [
    "DEFAULT_ANTHROPIC_MODEL",
    "SYSTEM_PROMPT",
    "AIProvider",
    "AIProviderError",
    "AnthropicProvider",
    "DeterministicTestProvider",
    "FixedRecommendationProvider",
    "get_provider",
    "parse_recommendation",
]
