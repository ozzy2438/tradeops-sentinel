"""RemediationExecutor: the only component permitted to act on a signed envelope.

Every reject rule required by this slice is checked here, in a fixed order,
so the same bad input always fails for the same first reason: expired ->
tampered -> approvals missing -> same-person Maker/Checker -> field not
allow-listed -> (delegated to the adapter, which holds the row lock)
value-mismatch / idempotent replay.
"""

from __future__ import annotations

from typing import Literal

from . import envelope as envelope_module
from .legacy_adapter import MockLegacyBookingAdapter
from .models import ALLOWED_PROPOSED_FIELDS, ActionEnvelope, Approval, ExecutionResult
from .store import RemediationStore

AttemptContext = Literal["NORMAL", "TIMEOUT_RECOVERY_ATTEMPT"]


class ApprovalsIncompleteError(RuntimeError):
    """Both a MAKER and a CHECKER APPROVE decision are required."""


class SameIdentityError(RuntimeError):
    """The Maker and Checker must be different identities."""


def _require_valid_approvals(envelope: ActionEnvelope, approvals: list[Approval]) -> None:
    approved_by_role = {a.role: a for a in approvals if a.decision == "APPROVE"}
    if "MAKER" not in approved_by_role or "CHECKER" not in approved_by_role:
        raise ApprovalsIncompleteError(
            "both a MAKER and a CHECKER APPROVE decision are required to execute"
        )
    maker = approved_by_role["MAKER"]
    checker = approved_by_role["CHECKER"]
    if maker.approver_identity == checker.approver_identity:
        raise SameIdentityError("maker and checker must be different identities")
    if (
        maker.approver_identity != envelope.maker_identity
        or checker.approver_identity != envelope.checker_identity
    ):
        raise SameIdentityError(
            "approval identities do not match the identities recorded on the envelope"
        )


class RemediationExecutor:
    def __init__(
        self,
        store: RemediationStore,
        adapter: MockLegacyBookingAdapter,
        *,
        secret: str | None = None,
    ) -> None:
        self._store = store
        self._adapter = adapter
        self._secret = secret

    def execute(
        self,
        envelope: ActionEnvelope,
        approvals: list[Approval],
        *,
        attempt_context: AttemptContext = "NORMAL",
    ) -> ExecutionResult:
        try:
            envelope_module.verify_envelope(envelope, secret=self._secret)
        except envelope_module.EnvelopeExpiredError as error:
            return self._record(envelope, "REJECTED_EXPIRED", str(error), None, False)
        except envelope_module.EnvelopeTamperedError as error:
            return self._record(envelope, "REJECTED_TAMPERED", str(error), None, False)

        try:
            _require_valid_approvals(envelope, approvals)
        except ApprovalsIncompleteError as error:
            return self._record(envelope, "REJECTED_APPROVALS_MISSING", str(error), None, False)
        except SameIdentityError as error:
            return self._record(envelope, "REJECTED_SAME_IDENTITY", str(error), None, False)

        if envelope.field_path not in ALLOWED_PROPOSED_FIELDS:
            return self._record(
                envelope,
                "REJECTED_FIELD_NOT_ALLOWED",
                f"{envelope.field_path!r} is not on the approved field allow-list",
                None,
                False,
            )

        outcome, read_back_value = self._adapter.apply(
            tenant_id=envelope.tenant_id,
            portfolio_id=envelope.portfolio_id,
            trade_id=envelope.trade_id,
            approved_value=envelope.approved_value,
            expected_old_value=envelope.expected_old_value,
            idempotency_key=envelope.idempotency_key,
        )

        if outcome == "APPLIED":
            return self._record(
                envelope,
                "SUCCESS",
                "legacy booking record corrected",
                read_back_value,
                True,
            )
        if outcome == "DUPLICATE_NOOP":
            # Same underlying idempotency-key check either way; the label
            # only distinguishes the narrative (plain replay vs a retry after
            # a simulated uncertain-outcome timeout) for evidence clarity.
            reported = (
                "TIMEOUT_RECOVERED"
                if attempt_context == "TIMEOUT_RECOVERY_ATTEMPT"
                else "DUPLICATE_NOOP"
            )
            return self._record(
                envelope,
                reported,
                "idempotency_key already applied; read-back confirms no second side effect",
                read_back_value,
                False,
            )
        if outcome == "VALUE_MISMATCH":
            return self._record(
                envelope,
                "REJECTED_VALUE_MISMATCH",
                (
                    f"current value {read_back_value!r} does not match "
                    f"expected_old_value {envelope.expected_old_value!r}"
                ),
                read_back_value,
                False,
            )
        # NOT_FOUND
        return self._record(
            envelope,
            "REJECTED_VALUE_MISMATCH",
            "no legacy booking record exists for this trade",
            None,
            False,
        )

    def _record(
        self,
        envelope: ActionEnvelope,
        outcome: str,
        detail: str,
        read_back_value: str | None,
        applied: bool,
    ) -> ExecutionResult:
        self._store.insert_execution(
            case_id=envelope.case_id,
            idempotency_key=envelope.idempotency_key,
            outcome=outcome,
            detail=detail,
            read_back_value=read_back_value,
            applied=applied,
        )
        return ExecutionResult(
            outcome=outcome,  # type: ignore[arg-type]
            detail=detail,
            read_back_value=read_back_value,
            applied=applied,
        )


__all__ = ["ApprovalsIncompleteError", "RemediationExecutor", "SameIdentityError"]
