"""Pure deterministic evaluation for the eight ADR-002 break families."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from packages.contracts.models import (
    Actor,
    BreakComparison,
    BreakConditionCode,
    BreakEvidence,
    BreakEvidenceRole,
    BreakFamily,
    BreakPriority,
    BreakProductContext,
    BreakSeverity,
    BreakSourceReference,
    BreakTolerance,
    DeadlineStatus,
    DuplicateSourceConflict,
    MissingSourceExpectation,
    ObservationModel,
    TradeBreak,
)

from .config import ArrivalWindowRule, MissingSourceKind, ReconciliationConfig
from .models import (
    BreakFact,
    ChangedField,
    ReconciliationContext,
    ReconciliationRun,
    stable_content_hash,
)

_OBSERVATION_RANK: dict[str, int] = {
    "EXECUTION": 1,
    "TRADE_CAPTURE": 2,
    "CONFIRMATION": 3,
    "BOOKING": 4,
}
_SEVERITY_RANK: dict[BreakSeverity, int] = {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3}
_DEADLINE_RANK: dict[DeadlineStatus, int] = {
    "OVERDUE": 1,
    "DUE": 2,
    "NO_CONFIGURED_DEADLINE": 3,
}


class ReconciliationEngine:
    """Evaluate a validated source set without model, LLM, or write access."""

    def __init__(self, config: ReconciliationConfig) -> None:
        self.config = config

    def run(
        self,
        context: ReconciliationContext,
        *,
        actor: Actor | None = None,
    ) -> ReconciliationRun:
        """Return an append-only run envelope for the supplied exact source set."""

        if context.effective_evaluated_at < self.config.effective_from:
            raise ValueError("reconciliation config is not effective at evaluated_at")
        if (
            self.config.effective_to is not None
            and context.effective_evaluated_at >= self.config.effective_to
        ):
            raise ValueError("reconciliation config is outside its effective interval")
        observations = self._ordered_observations(context.source_observations)
        source_refs = [self._source_reference(observation) for observation in observations]
        breaks: list[TradeBreak] = []

        breaks.extend(self._missing_source_breaks(context, observations, source_refs))
        linkage_break = self._linkage_break(context, observations, source_refs)
        if linkage_break is not None:
            breaks.append(linkage_break)
        breaks.extend(self._duplicate_breaks(context, observations, source_refs))
        breaks.extend(self._currency_breaks(context, observations, source_refs))
        breaks.extend(self._economic_breaks(context, observations, source_refs))
        date_break = self._date_break(context, observations, source_refs)
        if date_break is not None:
            breaks.append(date_break)
        lifecycle_break = self._lifecycle_break(context, observations, source_refs)
        if lifecycle_break is not None:
            breaks.append(lifecycle_break)
        post_action_break = self._post_action_break(context, observations, source_refs)
        if post_action_break is not None:
            breaks.append(post_action_break)

        breaks.sort(key=lambda break_item: (break_item.family, break_item.break_id))
        break_facts = tuple(
            BreakFact(
                family=break_item.family,
                condition_code=break_item.condition_code,
                field_path=comparison.field_path,
                value_type=comparison.value_type,
                expected_value=comparison.expected_value,
                observed_value=comparison.observed_value,
                tolerance=comparison.tolerance,
                severity=break_item.severity,
                expected_source_observation_id=comparison.expected_source_observation_id,
                observed_source_observation_id=comparison.observed_source_observation_id,
            )
            for break_item in breaks
            for comparison in break_item.comparisons
        )
        active_actor = actor or Actor(identity_type="SYSTEM", actor_id="reconciliation_engine")
        result = "BREAKS_DETECTED" if breaks else "PASS"
        run_fields: dict[str, Any] = {
            "schema_version": "1.0.0",
            "run_id": context.reconciliation_run_id,
            "run_version": context.run_version,
            "tenant_id": context.canonical_state.tenant_id,
            "portfolio_id": context.canonical_state.portfolio_id,
            "correlation_id": context.canonical_state.correlation_id,
            "trade_id": context.canonical_state.trade_id,
            "canonical_state_version": context.canonical_state.canonical_state_version,
            "source_watermark": context.canonical_state.source_watermark,
            "evaluated_at": context.effective_evaluated_at,
            "config_id": self.config.config_id,
            "config_version": self.config.config_version,
            "config_hash": self.config.content_hash,
            "detection_rule_version": self.config.detection_rule_version,
            "result": result,
            "break_ids": tuple(break_item.break_id for break_item in breaks),
            "breaks": tuple(breaks),
            "break_facts": break_facts,
            "source_version_set": tuple(source_refs),
            "actor": active_actor,
        }
        draft = ReconciliationRun.model_construct(
            **run_fields,
            content_hash="sha256:" + "0" * 64,
        )
        content_hash = stable_content_hash(draft.model_dump(mode="json", exclude={"content_hash"}))
        run = ReconciliationRun(
            **run_fields,
            content_hash=cast(Any, content_hash),
        )
        return run

    reconcile = run
    evaluate = run

    @staticmethod
    def _ordered_observations(
        observations: Iterable[ObservationModel],
    ) -> list[ObservationModel]:
        return sorted(
            observations,
            key=lambda observation: (
                _OBSERVATION_RANK[observation.observation_kind],
                observation.source_sequence,
                observation.observation_id,
                observation.source_version,
                observation.content_hash,
            ),
        )

    @staticmethod
    def _source_reference(observation: ObservationModel) -> BreakSourceReference:
        return BreakSourceReference(
            source_observation_id=observation.observation_id,
            observation_kind=observation.observation_kind,
            source_system=observation.source_system,
            source_business_key=observation.source_business_key,
            source_tenant_id=observation.tenant_id,
            source_portfolio_id=observation.portfolio_id,
            source_version=observation.source_version,
            content_hash=observation.content_hash,
        )

    def _missing_source_breaks(
        self,
        context: ReconciliationContext,
        observations: list[ObservationModel],
        source_refs: list[BreakSourceReference],
    ) -> list[TradeBreak]:
        available_kinds = {observation.observation_kind for observation in observations}
        product_type = context.canonical_state.state.product_type
        breaks: list[TradeBreak] = []
        for kind in ("EXECUTION", "CONFIRMATION", "BOOKING"):
            if kind in available_kinds:
                continue
            rule = self.config.arrival_rule(product_type, kind)
            expected_by = context.canonical_state.source_watermark + timedelta(
                seconds=rule.window_seconds
            )
            if context.effective_evaluated_at < expected_by:
                continue
            observed = observations[0]
            field_path = rule.field_path
            evidence = [
                self._evidence(
                    context,
                    "INGESTION_WATERMARK",
                    field_path,
                    captured_at=context.canonical_state.source_watermark,
                    salt=f"watermark:{kind}",
                ),
                self._evidence(
                    context,
                    "EXPECTED_SOURCE",
                    field_path,
                    captured_at=context.effective_evaluated_at,
                    salt=f"expected:{kind}:{rule.rule_version}",
                ),
            ]
            comparison = self._comparison(
                field_path=field_path,
                value_type="ABSENCE",
                expected_value="present",
                observed_value="absent_after_watermark",
                tolerance=BreakTolerance(mode="NONE"),
                expected=None,
                observed=observed,
                evidence_ids=[evidence[1].evidence_id],
            )
            breaks.append(
                self._break(
                    context,
                    family="MISSING_REQUIRED_SOURCE",
                    source_refs=source_refs,
                    comparisons=[comparison],
                    evidence=evidence,
                    severity_context=kind,
                    missing=(kind, rule, expected_by),
                    detected_at=context.effective_evaluated_at,
                )
            )
        return breaks

    def _linkage_break(
        self,
        context: ReconciliationContext,
        observations: list[ObservationModel],
        source_refs: list[BreakSourceReference],
    ) -> TradeBreak | None:
        decision = context.linkage_decision
        candidate_count = 0 if decision is None else len(decision.candidate_links)
        invalid = decision is None
        if decision is not None:
            candidate_scope_invalid = any(
                (candidate.tenant_id, candidate.portfolio_id)
                != (context.canonical_state.tenant_id, context.canonical_state.portfolio_id)
                for candidate in decision.candidate_links
            )
            invalid = (
                candidate_scope_invalid
                or decision.decision
                in {"REJECTED", "UNMATCHED", "AMBIGUOUS", "CROSS_SCOPE_REJECTED"}
                or decision.chosen_trade_id != context.canonical_state.trade_id
                or len(decision.candidate_links) != 1
            )
        if not invalid:
            return None

        observed = observations[0]
        expected_value = "1"
        observed_value = str(candidate_count)
        if expected_value == observed_value:
            observed_value = "0"
        evidence = [
            self._evidence(
                context,
                "CANDIDATE_LINK",
                "/linkage/trade_id",
                source=observed,
                salt=f"candidates:{candidate_count}",
            ),
            self._evidence(
                context,
                "LINKAGE_DECISION",
                "/linkage/trade_id",
                source=observed,
                salt="decision:none" if decision is None else decision.decision,
            ),
        ]
        comparison = self._comparison(
            field_path="/linkage/trade_id",
            value_type="COUNT",
            expected_value=expected_value,
            observed_value=observed_value,
            tolerance=BreakTolerance(mode="NONE"),
            expected=observed,
            observed=observed,
            evidence_ids=[evidence[0].evidence_id, evidence[1].evidence_id],
        )
        return self._break(
            context,
            family="AMBIGUOUS_OR_UNMATCHED_LINKAGE",
            source_refs=source_refs,
            comparisons=[comparison],
            evidence=evidence,
        )

    def _duplicate_breaks(
        self,
        context: ReconciliationContext,
        observations: list[ObservationModel],
        source_refs: list[BreakSourceReference],
    ) -> list[TradeBreak]:
        groups: dict[tuple[str, str, str], list[ObservationModel]] = {}
        for observation in observations:
            key = (
                observation.observation_kind,
                observation.source_business_key,
                observation.source_version,
            )
            groups.setdefault(key, []).append(observation)
        breaks: list[TradeBreak] = []
        for (
            observation_kind,
            source_business_key,
            source_version,
        ), group in sorted(groups.items()):
            hashes = {observation.content_hash for observation in group}
            if len(hashes) < 2:
                continue
            ordered_group = self._ordered_observations(group)
            expected, observed = ordered_group[0], ordered_group[1]
            field_path = "/source/content_hash"
            evidence = [
                self._evidence(
                    context,
                    "SOURCE_PAYLOAD_PAIR",
                    field_path,
                    source=expected,
                    salt=f"duplicate:{observation_kind}:payload",
                ),
                self._evidence(
                    context,
                    "SOURCE_METADATA",
                    field_path,
                    source=observed,
                    salt=f"duplicate:{observation_kind}:metadata:{source_version}",
                ),
            ]
            comparison = self._comparison(
                field_path=field_path,
                value_type="CONTENT_HASH",
                expected_value=expected.content_hash,
                observed_value=observed.content_hash,
                tolerance=BreakTolerance(mode="NONE"),
                expected=expected,
                observed=observed,
                evidence_ids=[evidence[0].evidence_id, evidence[1].evidence_id],
            )
            breaks.append(
                self._break(
                    context,
                    family="DUPLICATE_SOURCE_CONFLICT",
                    source_refs=[self._source_reference(item) for item in ordered_group],
                    comparisons=[comparison],
                    evidence=evidence,
                    duplicate=(ordered_group, source_business_key, source_version),
                )
            )
        return breaks

    def _currency_breaks(
        self,
        context: ReconciliationContext,
        observations: list[ObservationModel],
        source_refs: list[BreakSourceReference],
    ) -> list[TradeBreak]:
        comparisons: list[BreakComparison] = []
        evidence: list[BreakEvidence] = []
        for field_path, value_type in (
            ("/payload/base_currency", "CURRENCY"),
            ("/payload/terms_currency", "CURRENCY"),
            ("/payload/side", "SIDE"),
        ):
            baseline = self._authoritative_observation(context, observations, field_path)
            expected_value = self._field_value(baseline, field_path)
            for observed in observations:
                if observed.observation_id == baseline.observation_id:
                    continue
                observed_value = self._field_value(observed, field_path)
                if observed_value == expected_value:
                    continue
                pair_evidence = self._comparison_evidence(
                    context,
                    field_path,
                    ("FIELD_COMPARISON", "NORMALISATION_RULE"),
                    baseline,
                    observed,
                )
                evidence.extend(pair_evidence)
                comparisons.append(
                    self._comparison(
                        field_path=field_path,
                        value_type=cast(Any, value_type),
                        expected_value=expected_value,
                        observed_value=observed_value,
                        tolerance=BreakTolerance(mode="NONE"),
                        expected=baseline,
                        observed=observed,
                        evidence_ids=[item.evidence_id for item in pair_evidence],
                    )
                )
                break
        if not comparisons:
            return []
        return [
            self._break(
                context,
                family="CURRENCY_PAIR_OR_SIDE_MISMATCH",
                source_refs=source_refs,
                comparisons=comparisons,
                evidence=evidence,
            )
        ]

    def _economic_breaks(
        self,
        context: ReconciliationContext,
        observations: list[ObservationModel],
        source_refs: list[BreakSourceReference],
    ) -> list[TradeBreak]:
        comparisons: list[BreakComparison] = []
        evidence: list[BreakEvidence] = []
        product_type = context.canonical_state.state.product_type
        for field_path in (
            "/payload/base_amount",
            "/payload/terms_amount",
            "/payload/quoted_rate",
        ):
            baseline = self._authoritative_observation(context, observations, field_path)
            expected_value = self._field_value(baseline, field_path)
            expected_decimal = self._decimal(expected_value)
            rule = self.config.decimal_rule(product_type, field_path)
            for observed in observations:
                if observed.observation_id == baseline.observation_id:
                    continue
                observed_value = self._field_value(observed, field_path)
                observed_decimal = self._decimal(observed_value)
                if self._within_tolerance(expected_decimal, observed_decimal, rule.tolerance):
                    continue
                pair_evidence = self._comparison_evidence(
                    context,
                    field_path,
                    ("DECIMAL_COMPARISON", "NORMALISATION_RULE"),
                    baseline,
                    observed,
                )
                evidence.extend(pair_evidence)
                comparisons.append(
                    self._comparison(
                        field_path=field_path,
                        value_type="DECIMAL",
                        expected_value=expected_value,
                        observed_value=observed_value,
                        tolerance=rule.tolerance,
                        expected=baseline,
                        observed=observed,
                        evidence_ids=[item.evidence_id for item in pair_evidence],
                    )
                )
                break
        if not comparisons:
            return []
        return [
            self._break(
                context,
                family="ECONOMIC_VALUE_MISMATCH",
                source_refs=source_refs,
                comparisons=comparisons,
                evidence=evidence,
            )
        ]

    def _date_break(
        self,
        context: ReconciliationContext,
        observations: list[ObservationModel],
        source_refs: list[BreakSourceReference],
    ) -> TradeBreak | None:
        comparisons: list[BreakComparison] = []
        evidence: list[BreakEvidence] = []
        for field_path in ("/payload/trade_date", "/payload/value_date"):
            baseline = self._authoritative_observation(context, observations, field_path)
            expected_value = self._field_value(baseline, field_path)
            for observed in observations:
                if observed.observation_id == baseline.observation_id:
                    continue
                observed_value = self._field_value(observed, field_path)
                if observed_value == expected_value:
                    continue
                pair_evidence = self._comparison_evidence(
                    context,
                    field_path,
                    ("DATE_COMPARISON", "NORMALISATION_RULE"),
                    baseline,
                    observed,
                )
                evidence.extend(pair_evidence)
                comparisons.append(
                    self._comparison(
                        field_path=field_path,
                        value_type="DATE",
                        expected_value=expected_value,
                        observed_value=observed_value,
                        tolerance=BreakTolerance(mode="NONE"),
                        expected=baseline,
                        observed=observed,
                        evidence_ids=[item.evidence_id for item in pair_evidence],
                    )
                )
                break
        if not comparisons:
            return None
        return self._break(
            context,
            family="TRADE_OR_VALUE_DATE_MISMATCH",
            source_refs=source_refs,
            comparisons=comparisons,
            evidence=evidence,
        )

    def _lifecycle_break(
        self,
        context: ReconciliationContext,
        observations: list[ObservationModel],
        source_refs: list[BreakSourceReference],
    ) -> TradeBreak | None:
        statuses = [
            self._field_value(observation, "/payload/lifecycle_status")
            for observation in observations
        ]
        if len(set(statuses)) == 1 and statuses[0] in {"AMENDED", "CANCELLED"}:
            return None
        invalid = [
            observation
            for observation in observations
            if self._field_value(observation, "/payload/lifecycle_status")
            != self.config.lifecycle_rule(observation.observation_kind).expected_status
        ]
        if not invalid:
            return None
        observed = invalid[0]
        expected = next(
            (
                item
                for item in observations
                if item is not observed
                and self._field_value(item, "/payload/lifecycle_status")
                == self.config.lifecycle_rule(item.observation_kind).expected_status
            ),
            None,
        )
        if expected is None:
            return None
        field_path = "/payload/lifecycle_status"
        evidence_pair = self._comparison_evidence(
            context,
            field_path,
            ("LIFECYCLE_RELATION", "NORMALISATION_RULE"),
            expected,
            observed,
        )
        comparison = self._comparison(
            field_path=field_path,
            value_type="LIFECYCLE_STATUS",
            expected_value=self.config.lifecycle_rule(expected.observation_kind).expected_status,
            observed_value=self._field_value(observed, field_path),
            tolerance=BreakTolerance(mode="NONE"),
            expected=expected,
            observed=observed,
            evidence_ids=[item.evidence_id for item in evidence_pair],
        )
        return self._break(
            context,
            family="LIFECYCLE_STATUS_MISMATCH",
            source_refs=source_refs,
            comparisons=[comparison],
            evidence=evidence_pair,
        )

    def _post_action_break(
        self,
        context: ReconciliationContext,
        observations: list[ObservationModel],
        source_refs: list[BreakSourceReference],
    ) -> TradeBreak | None:
        verification = context.post_action_verification
        if verification is None:
            return None
        pre_action = verification.pre_action
        post_action = verification.post_action
        changed = list(verification.changed_fields)
        if post_action is not None:
            for field_path in ("/payload/book_id", "/payload/lifecycle_status"):
                expected_value = self._field_value(pre_action, field_path)
                observed_value = self._field_value(post_action, field_path)
                if expected_value != observed_value and not any(
                    field.field_path == field_path for field in changed
                ):
                    changed.append(
                        ChangedField(
                            field_path=cast(Any, field_path),
                            expected_value=expected_value,
                            observed_value=observed_value,
                        )
                    )
        if not verification.readback_available and not changed:
            changed.append(
                ChangedField(
                    field_path="/payload/book_id",
                    expected_value=self._field_value(pre_action, "/payload/book_id"),
                    observed_value="readback_unavailable",
                )
            )
        if not changed and not verification.original_break_remaining:
            return None
        if not changed:
            changed.append(
                ChangedField(
                    field_path="/payload/book_id",
                    expected_value=self._field_value(pre_action, "/payload/book_id"),
                    observed_value="readback_unavailable",
                )
            )
        comparisons: list[BreakComparison] = []
        evidence: list[BreakEvidence] = []
        for changed_field in changed:
            expected_source = pre_action
            observed_source = post_action or pre_action
            pair_evidence = self._comparison_evidence(
                context,
                changed_field.field_path,
                ("PRE_ACTION_READ", "POST_ACTION_READ"),
                expected_source,
                observed_source,
                salt=f"action:{verification.action_instruction_hash}:{changed_field.field_path}",
            )
            action_evidence = self._evidence(
                context,
                "ACTION_INSTRUCTION",
                changed_field.field_path,
                source=expected_source,
                salt=f"instruction:{verification.action_instruction_hash}:{changed_field.field_path}",
            )
            diff_evidence = self._evidence(
                context,
                "CHANGED_FIELD_DIFF",
                changed_field.field_path,
                source=observed_source,
                salt=f"diff:{changed_field.expected_value}:{changed_field.observed_value}",
            )
            comparison_evidence = [action_evidence, *pair_evidence, diff_evidence]
            evidence.extend(comparison_evidence)
            comparisons.append(
                self._comparison(
                    field_path=changed_field.field_path,
                    value_type=(
                        "IDENTIFIER"
                        if changed_field.field_path == "/payload/book_id"
                        else "LIFECYCLE_STATUS"
                    ),
                    expected_value=changed_field.expected_value,
                    observed_value=changed_field.observed_value,
                    tolerance=BreakTolerance(mode="NONE"),
                    expected=expected_source,
                    observed=observed_source,
                    evidence_ids=[item.evidence_id for item in comparison_evidence],
                )
            )
        evidence.append(
            self._evidence(
                context,
                "RECONCILIATION_RESULT",
                None,
                source=post_action or pre_action,
                salt=f"result:{verification.original_break_remaining}:{len(changed)}",
            )
        )
        return self._break(
            context,
            family="POST_ACTION_VERIFICATION_FAILURE",
            source_refs=source_refs,
            comparisons=comparisons,
            evidence=evidence,
        )

    @staticmethod
    def _authoritative_observation(
        context: ReconciliationContext,
        observations: list[ObservationModel],
        field_path: str,
    ) -> ObservationModel:
        """Resolve the expected operand from canonical field provenance."""

        field_name = field_path.rsplit("/", 1)[1]
        provenance = getattr(context.canonical_state.field_provenance, field_name)
        for observation in observations:
            if observation.observation_id == provenance.source_observation_id:
                return observation
        raise ValueError(
            f"canonical provenance for {field_path} references an unavailable source observation"
        )

    @staticmethod
    def _field_value(observation: ObservationModel, field_path: str) -> str:
        payload: Any = observation.payload
        leaf = field_path.rsplit("/", 1)[1]
        value: Any = getattr(payload, leaf)
        if leaf in {"base_amount", "terms_amount", "quoted_rate"}:
            return str(value.value)
        if isinstance(value, (date,)):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _decimal(value: str) -> Decimal:
        try:
            return Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"economic value is not a valid Decimal: {value}") from exc

    @staticmethod
    def _within_tolerance(
        expected: Decimal,
        observed: Decimal,
        tolerance: BreakTolerance,
    ) -> bool:
        difference = abs(observed - expected)
        if tolerance.mode == "NONE":
            return difference == 0
        assert tolerance.value is not None
        allowed = Decimal(tolerance.value)
        if tolerance.mode == "ABSOLUTE_DECIMAL":
            return difference <= allowed
        return difference <= abs(expected) * allowed

    def _comparison_evidence(
        self,
        context: ReconciliationContext,
        field_path: str,
        roles: tuple[BreakEvidenceRole, BreakEvidenceRole],
        expected: ObservationModel,
        observed: ObservationModel,
        *,
        salt: str = "",
    ) -> list[BreakEvidence]:
        return [
            self._evidence(
                context,
                roles[0],
                field_path,
                source=expected,
                salt=f"{salt}:expected:{expected.observation_id}",
            ),
            self._evidence(
                context,
                roles[1],
                field_path,
                source=observed,
                salt=f"{salt}:observed:{observed.observation_id}",
            ),
        ]

    def _evidence(
        self,
        context: ReconciliationContext,
        role: BreakEvidenceRole,
        field_path: str | None,
        *,
        source: ObservationModel | None = None,
        captured_at: Any | None = None,
        salt: str = "",
    ) -> BreakEvidence:
        source_id = None if source is None else source.observation_id
        source_version = None if source is None else source.source_version
        source_hash = None if source is None else source.content_hash
        effective_captured_at = captured_at or context.effective_evaluated_at
        payload = {
            "run_id": context.reconciliation_run_id,
            "config_hash": self.config.content_hash,
            "role": role,
            "field_path": field_path,
            "source_observation_id": source_id,
            "source_version": source_version,
            "source_content_hash": source_hash,
            "captured_at": effective_captured_at.isoformat(),
            "salt": salt,
        }
        digest = stable_content_hash(payload)
        evidence_id = f"evidence_{role.lower()}_{digest.removeprefix('sha256:')[:24]}"
        return BreakEvidence(
            evidence_id=evidence_id,
            role=role,
            content_hash=cast(Any, digest),
            captured_at=effective_captured_at,
            source_observation_id=source_id,
            source_version=source_version,
            field_path=cast(Any, field_path) if field_path is not None else None,
        )

    def _comparison(
        self,
        *,
        field_path: str,
        value_type: Any,
        expected_value: str,
        observed_value: str,
        tolerance: BreakTolerance,
        expected: ObservationModel | None,
        observed: ObservationModel | None,
        evidence_ids: list[str],
    ) -> BreakComparison:
        return BreakComparison(
            field_path=cast(Any, field_path),
            value_type=value_type,
            expected_value=expected_value,
            observed_value=observed_value,
            tolerance=tolerance,
            normalisation_rule_version=self.config.normalisation_rule_version,
            evidence_ids=evidence_ids,
            expected_source_observation_id=None if expected is None else expected.observation_id,
            expected_source_version=None if expected is None else expected.source_version,
            observed_source_observation_id=None if observed is None else observed.observation_id,
            observed_source_version=None if observed is None else observed.source_version,
        )

    def _break(
        self,
        context: ReconciliationContext,
        *,
        family: BreakFamily,
        source_refs: list[BreakSourceReference],
        comparisons: list[BreakComparison],
        evidence: list[BreakEvidence],
        severity_context: MissingSourceKind | None = None,
        missing: tuple[MissingSourceKind, ArrivalWindowRule, Any] | None = None,
        duplicate: tuple[list[ObservationModel], str, str] | None = None,
        detected_at: Any | None = None,
    ) -> TradeBreak:
        canonical = context.canonical_state
        product_context = BreakProductContext(
            product_type=canonical.state.product_type,
            settlement_rule_version=canonical.state.settlement_rule_version,
            trade_date=canonical.state.trade_date,
            value_date=canonical.state.value_date,
        )
        assert family in {
            "MISSING_REQUIRED_SOURCE",
            "AMBIGUOUS_OR_UNMATCHED_LINKAGE",
            "DUPLICATE_SOURCE_CONFLICT",
            "CURRENCY_PAIR_OR_SIDE_MISMATCH",
            "ECONOMIC_VALUE_MISMATCH",
            "TRADE_OR_VALUE_DATE_MISMATCH",
            "LIFECYCLE_STATUS_MISMATCH",
            "POST_ACTION_VERIFICATION_FAILURE",
        }
        condition_by_family: dict[BreakFamily, BreakConditionCode] = {
            "MISSING_REQUIRED_SOURCE": "MISSING_SOURCE_AFTER_WATERMARK",
            "AMBIGUOUS_OR_UNMATCHED_LINKAGE": "LINKAGE_CANDIDATE_SCOPE_INVARIANT",
            "DUPLICATE_SOURCE_CONFLICT": "DUPLICATE_SOURCE_IDENTITY_CONTENT",
            "CURRENCY_PAIR_OR_SIDE_MISMATCH": "EXACT_CURRENCY_PAIR_SIDE",
            "ECONOMIC_VALUE_MISMATCH": "DECIMAL_OUTSIDE_TOLERANCE",
            "TRADE_OR_VALUE_DATE_MISMATCH": "EXACT_TRADE_VALUE_DATE",
            "LIFECYCLE_STATUS_MISMATCH": "ALLOWED_LIFECYCLE_RELATION",
            "POST_ACTION_VERIFICATION_FAILURE": "POST_ACTION_READBACK_RECONCILIATION",
        }
        missing_severity: dict[MissingSourceKind, BreakSeverity] = {
            "EXECUTION": "HIGH",
            "CONFIRMATION": "MEDIUM",
            "BOOKING": "HIGH",
        }
        fixed_severity: dict[BreakFamily, BreakSeverity] = {
            "AMBIGUOUS_OR_UNMATCHED_LINKAGE": "HIGH",
            "DUPLICATE_SOURCE_CONFLICT": "HIGH",
            "CURRENCY_PAIR_OR_SIDE_MISMATCH": "CRITICAL",
            "ECONOMIC_VALUE_MISMATCH": "CRITICAL",
            "TRADE_OR_VALUE_DATE_MISMATCH": "HIGH",
            "LIFECYCLE_STATUS_MISMATCH": "HIGH",
            "POST_ACTION_VERIFICATION_FAILURE": "CRITICAL",
        }
        if family == "MISSING_REQUIRED_SOURCE":
            if severity_context is None:
                raise ValueError("missing-source break requires severity context")
            severity = missing_severity[severity_context]
        else:
            severity = fixed_severity[family]
        if family == "MISSING_REQUIRED_SOURCE":
            if missing is None:
                raise ValueError("missing-source break requires an arrival-window expectation")
            expected_by = missing[2]
            deadline: DeadlineStatus = (
                "OVERDUE" if context.effective_evaluated_at > expected_by else "DUE"
            )
        else:
            deadline = "NO_CONFIGURED_DEADLINE"
        ordering_key = (2, _SEVERITY_RANK[severity], _DEADLINE_RANK[deadline], 0)
        detected = detected_at or context.effective_evaluated_at
        hash_payload = {
            "run_id": context.reconciliation_run_id,
            "family": family,
            "source_ids": [source.source_observation_id for source in source_refs],
            "comparisons": [comparison.model_dump(mode="json") for comparison in comparisons],
        }
        break_digest = stable_content_hash(hash_payload).removeprefix("sha256:")[:40]
        missing_expectation = None
        if missing is not None:
            kind, rule, expected_by = missing
            missing_expectation = MissingSourceExpectation(
                expected_observation_kind=kind,
                expected_source_system=rule.source_system,
                field_path=rule.field_path,
                arrival_window_rule_version=rule.rule_version,
                watermark_at=canonical.source_watermark,
                expected_by=expected_by,
            )
        duplicate_conflict = None
        if duplicate is not None:
            group, source_business_key, source_version = duplicate
            duplicate_conflict = DuplicateSourceConflict(
                conflict_type="SAME_SOURCE_KEY_VERSION_CONTENT",
                source_observation_ids=[item.observation_id for item in group],
                source_business_key=source_business_key,
                source_version=source_version,
            )
        return TradeBreak(
            taxonomy_version="1.0.0",
            detection_rule_version=self.config.detection_rule_version,
            priority_rule_version=self.config.priority_rule_version,
            lifecycle_rule_version=self.config.lifecycle_rule_version,
            break_id=f"break_{family.lower()}_{break_digest}",
            break_version=1,
            tenant_id=canonical.tenant_id,
            portfolio_id=canonical.portfolio_id,
            correlation_id=canonical.correlation_id,
            trade_id=canonical.trade_id,
            canonical_state_version=canonical.canonical_state_version,
            reconciliation_run_id=context.reconciliation_run_id,
            product_type=canonical.state.product_type,
            product_context=product_context,
            family=family,
            condition_code=condition_by_family[family],
            severity=severity,
            severity_context=severity_context,
            priority=BreakPriority(
                materiality_band="UNASSESSED",
                deadline_status=deadline,
                case_age_seconds=0,
                ordering_key=ordering_key,
            ),
            source_version_set=source_refs,
            evaluated_field_paths=[comparison.field_path for comparison in comparisons],
            comparisons=comparisons,
            evidence=evidence,
            missing_source_expectation=missing_expectation,
            duplicate_source_conflict=duplicate_conflict,
            state="OPEN",
            previous_state=None,
            transition_reason="DETECTED",
            detected_at=detected,
            state_changed_at=detected,
        )


def reconcile(
    context: ReconciliationContext,
    *,
    config: ReconciliationConfig,
    actor: Actor | None = None,
) -> ReconciliationRun:
    """Convenience entry point for one pure reconciliation evaluation."""

    return ReconciliationEngine(config).run(context, actor=actor)
