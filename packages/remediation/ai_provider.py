"""AI triage providers.

Azure OpenAI and Anthropic are optional live providers. Azure OpenAI has a
bounded, keyless-capable path that is exercised only by an explicit synthetic
demo; Anthropic remains implemented but not live-validated. Every automated
test and CI run continues to use an offline deterministic provider.

The LLM never touches SQL, the database, or any system directly. It receives
only a ``BreakFacts`` document and a list of candidate citations, and returns
only a JSON document that is validated against ``AIRecommendation`` before
anything downstream ever sees it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from .models import (
    AIRecommendation,
    ApprovalRole,
    BreakFacts,
    Citation,
    Priority,
    RecommendedAction,
    RemediationModel,
    RiskTier,
)
from .retrieval import RetrievedSection

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_AZURE_OPENAI_DEPLOYMENT = "gpt-5.4-mini"
DEFAULT_AZURE_OPENAI_API_VERSION = "2024-10-21"
AZURE_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high"})

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

AZURE_SYSTEM_PROMPT = """You are a post-trade reconciliation triage assistant \
for a synthetic FX trade-break platform. You analyse structured break facts \
only. You have no database access, no tool use, and no ability to execute \
anything. The response schema is enforced by Azure Structured Outputs.

Rules you must follow:
- recommended_action and abstain_reason are mutually exclusive: set exactly
  one of them.
- You may cite ONLY document_id/section pairs given in candidate_citations.
- The only action you may propose is CORRECT_LEGACY_BOOKING_FIELD, and only
  when the observed source is MOCK_LEGACY_BOOKING and the expected source is
  FIX_EXECUTION. Anything else must abstain.
- proposed_fields is an array, not an object. When recommending a correction,
  it must contain exactly one item whose field_path is the break's own
  field_path and whose value is the break's expected_value, verbatim.
- When abstaining, proposed_fields and citations must be empty arrays.
- Never propose an order, price decision, trading action, different field, or
  different value."""


class AIProviderError(RuntimeError):
    """Raised when a provider cannot produce a schema-valid recommendation."""


class AIProvider(Protocol):
    name: str

    def recommend(
        self, *, facts: BreakFacts, candidate_citations: list[RetrievedSection]
    ) -> AIRecommendation: ...


class AzureProposedField(RemediationModel):
    """Strict list item used because Structured Outputs forbids dynamic maps."""

    field_path: str
    value: str


class AzureRecommendationOutput(RemediationModel):
    """Azure transport schema, converted immediately to ``AIRecommendation``."""

    predicted_root_cause: str
    confidence: float
    priority: Priority
    recommended_action: RecommendedAction | None
    proposed_fields: list[AzureProposedField]
    risk_tier: RiskTier
    required_approvals: list[ApprovalRole]
    citations: list[Citation]
    abstain_reason: str | None

    def to_recommendation(self) -> AIRecommendation:
        proposed_fields = {item.field_path: item.value for item in self.proposed_fields}
        if len(proposed_fields) != len(self.proposed_fields):
            raise AIProviderError("Azure OpenAI returned duplicate proposed field paths")
        return AIRecommendation(
            predicted_root_cause=self.predicted_root_cause,
            confidence=self.confidence,
            priority=self.priority,
            recommended_action=self.recommended_action,
            proposed_fields=proposed_fields,
            risk_tier=self.risk_tier,
            required_approvals=self.required_approvals,
            citations=self.citations,
            abstain_reason=self.abstain_reason,
        )


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


@dataclass(frozen=True)
class AzureOpenAISettings:
    """Non-secret settings for one deliberately bounded Azure deployment."""

    endpoint: str
    deployment: str = DEFAULT_AZURE_OPENAI_DEPLOYMENT
    api_version: str = DEFAULT_AZURE_OPENAI_API_VERSION
    max_completion_tokens: int = 400
    reasoning_effort: str | None = "minimal"

    @classmethod
    def from_env(cls) -> AzureOpenAISettings:
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
        if not endpoint:
            raise AIProviderError(
                "AZURE_OPENAI_ENDPOINT is not configured; the Azure provider cannot run"
            )
        max_tokens = int(os.getenv("AZURE_OPENAI_MAX_COMPLETION_TOKENS", "400"))
        if not 1 <= max_tokens <= 400:
            raise AIProviderError("AZURE_OPENAI_MAX_COMPLETION_TOKENS must be between 1 and 400")
        reasoning_effort = os.getenv("AZURE_OPENAI_REASONING_EFFORT", "minimal").strip() or None
        if reasoning_effort is not None and reasoning_effort not in AZURE_REASONING_EFFORTS:
            allowed = ", ".join(sorted(AZURE_REASONING_EFFORTS))
            raise AIProviderError(f"AZURE_OPENAI_REASONING_EFFORT must be one of: {allowed}")
        return cls(
            endpoint=endpoint.rstrip("/"),
            deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", DEFAULT_AZURE_OPENAI_DEPLOYMENT).strip()
            or DEFAULT_AZURE_OPENAI_DEPLOYMENT,
            api_version=os.getenv(
                "AZURE_OPENAI_API_VERSION", DEFAULT_AZURE_OPENAI_API_VERSION
            ).strip()
            or DEFAULT_AZURE_OPENAI_API_VERSION,
            max_completion_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )


def _build_azure_openai_client(settings: AzureOpenAISettings) -> Any:
    """Build an Azure client without ever logging or persisting a credential."""

    try:
        from openai import AzureOpenAI
    except ImportError as error:  # pragma: no cover - packaging/runtime boundary
        raise AIProviderError("install the optional 'azure' dependencies first") from error

    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
    if api_key:
        return AzureOpenAI(
            azure_endpoint=settings.endpoint,
            api_key=api_key,
            api_version=settings.api_version,
        )

    try:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    except ImportError as error:  # pragma: no cover - packaging/runtime boundary
        raise AIProviderError("install azure-identity or provide AZURE_OPENAI_API_KEY") from error

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )
    return AzureOpenAI(
        azure_endpoint=settings.endpoint,
        azure_ad_token_provider=token_provider,
        api_version=settings.api_version,
    )


class AzureOpenAIProvider:
    """Bounded Azure OpenAI provider for the existing remediation contract."""

    def __init__(
        self,
        *,
        settings: AzureOpenAISettings | None = None,
        client: Any | None = None,
    ) -> None:
        self._settings = settings or AzureOpenAISettings.from_env()
        self._client = client or _build_azure_openai_client(self._settings)
        self.name = f"azure-openai:{self._settings.deployment}"

    def recommend(
        self, *, facts: BreakFacts, candidate_citations: list[RetrievedSection]
    ) -> AIRecommendation:
        kwargs: dict[str, Any] = {
            "model": self._settings.deployment,
            "messages": [
                {"role": "system", "content": AZURE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_user_prompt(facts, candidate_citations),
                },
            ],
            "response_format": AzureRecommendationOutput,
            "max_completion_tokens": self._settings.max_completion_tokens,
        }
        if self._settings.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self._settings.reasoning_effort

        try:
            response = self._client.beta.chat.completions.parse(**kwargs)
            message = response.choices[0].message
        except Exception as error:  # noqa: BLE001 - typed provider boundary
            raise AIProviderError("Azure OpenAI recommendation request failed") from error

        parsed = getattr(message, "parsed", None)
        if parsed is None:
            refusal = getattr(message, "refusal", None)
            suffix = f": {refusal}" if refusal else ""
            raise AIProviderError(f"Azure OpenAI returned no structured recommendation{suffix}")
        try:
            payload = parsed.model_dump(mode="json") if hasattr(parsed, "model_dump") else parsed
            return AzureRecommendationOutput.model_validate(payload).to_recommendation()
        except Exception as error:  # noqa: BLE001 - typed provider boundary
            raise AIProviderError(
                "Azure OpenAI returned an invalid structured recommendation"
            ) from error


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

    Defaults to the deterministic provider. Live providers must be selected
    explicitly; an unknown name fails closed instead of silently falling back.
    """

    configured: str = (
        name if name is not None else os.getenv("TRADEOPS_AI_PROVIDER", "deterministic")
    )
    selected = configured.strip().lower()
    if selected in {"deterministic", "deterministic_test", "deterministic-test"}:
        return DeterministicTestProvider()
    if selected == "anthropic":
        return AnthropicProvider()
    if selected in {"azure", "azure_openai", "azure-openai"}:
        return AzureOpenAIProvider()
    raise AIProviderError(f"unknown TRADEOPS_AI_PROVIDER value: {configured!r}")


__all__ = [
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_AZURE_OPENAI_API_VERSION",
    "DEFAULT_AZURE_OPENAI_DEPLOYMENT",
    "SYSTEM_PROMPT",
    "AIProvider",
    "AIProviderError",
    "AZURE_REASONING_EFFORTS",
    "AzureRecommendationOutput",
    "AnthropicProvider",
    "AzureOpenAIProvider",
    "AzureOpenAISettings",
    "AzureProposedField",
    "DeterministicTestProvider",
    "FixedRecommendationProvider",
    "get_provider",
    "parse_recommendation",
]
