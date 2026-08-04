"""PostgreSQL access for the remediation slice's own tables.

Deliberately a separate, small module rather than more methods bolted onto
``packages.persistence.adapter.PostgresAdapter`` -- it reuses that adapter's
connection/timeout handling via composition, but keeps every remediation
table's SQL in one place, matching the project's package-per-concern layout.

``legacy_booking_records`` is the only mutable table this module writes.
Every other table (cases, approvals, envelopes, executions, evidence) is
insert-only, matching the append-only guards on migration 0004.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

import psycopg

from packages.persistence.adapter import PostgresAdapter

BookingApplyOutcome = Literal["APPLIED", "DUPLICATE_NOOP", "VALUE_MISMATCH", "NOT_FOUND"]


class DuplicateApprovalRoleError(RuntimeError):
    """A Maker or Checker decision already exists for this case and role."""


class RemediationStore:
    def __init__(self, adapter: PostgresAdapter) -> None:
        self._adapter = adapter

    # ------------------------------------------------------------------
    # mock legacy booking record (the one mutable table)
    # ------------------------------------------------------------------
    def read_legacy_booking_record(
        self, *, tenant_id: str, portfolio_id: str, trade_id: str
    ) -> dict[str, Any] | None:
        with self._adapter.connect() as connection:
            row = connection.execute(
                """
                SELECT tenant_id, portfolio_id, trade_id, base_amount_value,
                       base_amount_currency, base_amount_scale, source_observation_id,
                       booking_version, last_applied_idempotency_key, updated_at
                FROM legacy_booking_records
                WHERE tenant_id = %s AND portfolio_id = %s AND trade_id = %s
                """,
                (tenant_id, portfolio_id, trade_id),
            ).fetchone()
        return dict(row) if row else None

    def seed_legacy_booking_record(
        self,
        *,
        tenant_id: str,
        portfolio_id: str,
        trade_id: str,
        base_amount_value: str,
        base_amount_currency: str,
        base_amount_scale: int,
        source_observation_id: str,
    ) -> None:
        """Seed the mock legacy record from its originating BOOKING observation.

        Idempotent: does nothing if a record already exists for this trade,
        so seeding can be called every time a case is created without
        clobbering a record a prior remediation may have already corrected.
        """

        with self._adapter.connect() as connection:
            connection.execute(
                """
                INSERT INTO legacy_booking_records (
                    tenant_id, portfolio_id, trade_id, base_amount_value,
                    base_amount_currency, base_amount_scale, source_observation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, portfolio_id, trade_id) DO NOTHING
                """,
                (
                    tenant_id,
                    portfolio_id,
                    trade_id,
                    base_amount_value,
                    base_amount_currency,
                    base_amount_scale,
                    source_observation_id,
                ),
            )
            connection.commit()

    def apply_legacy_booking_correction(
        self,
        *,
        tenant_id: str,
        portfolio_id: str,
        trade_id: str,
        approved_value: str,
        expected_old_value: str,
        idempotency_key: str,
    ) -> tuple[BookingApplyOutcome, str | None]:
        """Apply (or safely no-op) a base_amount correction.

        Locks the row for the duration of the check-then-write so a second,
        concurrent attempt with the same idempotency_key cannot race past the
        duplicate check -- this, not application-level caching, is what
        actually prevents a second side effect on replay (RB-003).
        """

        with self._adapter.connect() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT base_amount_value, last_applied_idempotency_key
                    FROM legacy_booking_records
                    WHERE tenant_id = %s AND portfolio_id = %s AND trade_id = %s
                    FOR UPDATE
                    """,
                    (tenant_id, portfolio_id, trade_id),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return "NOT_FOUND", None

                current_value = str(row["base_amount_value"])
                if row["last_applied_idempotency_key"] == idempotency_key:
                    # Replay of an already-applied action: no write, no second
                    # side effect. This is also the timeout-recovery path --
                    # read-back after an uncertain outcome lands here.
                    connection.rollback()
                    return "DUPLICATE_NOOP", current_value

                if current_value != expected_old_value:
                    connection.rollback()
                    return "VALUE_MISMATCH", current_value

                connection.execute(
                    """
                    UPDATE legacy_booking_records
                    SET base_amount_value = %s,
                        booking_version = booking_version + 1,
                        last_applied_idempotency_key = %s,
                        updated_at = now()
                    WHERE tenant_id = %s AND portfolio_id = %s AND trade_id = %s
                    """,
                    (approved_value, idempotency_key, tenant_id, portfolio_id, trade_id),
                )
                connection.commit()
                return "APPLIED", approved_value
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # cases (insert-only)
    # ------------------------------------------------------------------
    def insert_case(
        self,
        *,
        case_id: str,
        break_id: str,
        run_id: str,
        trade_id: str,
        tenant_id: str,
        portfolio_id: str,
        product_type: str,
        ai_provider: str,
        break_facts: dict[str, Any],
        ai_recommendation: dict[str, Any],
        policy_decision: dict[str, Any],
    ) -> None:
        with self._adapter.connect() as connection:
            connection.execute(
                """
                INSERT INTO remediation_cases (
                    case_id, break_id, run_id, trade_id, tenant_id, portfolio_id,
                    product_type, ai_provider, break_facts, ai_recommendation, policy_decision
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    case_id,
                    break_id,
                    run_id,
                    trade_id,
                    tenant_id,
                    portfolio_id,
                    product_type,
                    ai_provider,
                    json.dumps(break_facts),
                    json.dumps(ai_recommendation),
                    json.dumps(policy_decision),
                ),
            )
            connection.commit()

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self._adapter.connect() as connection:
            row = connection.execute(
                """
                SELECT case_id, break_id, run_id, trade_id, tenant_id, portfolio_id,
                       product_type, ai_provider, break_facts, ai_recommendation,
                       policy_decision, created_at
                FROM remediation_cases WHERE case_id = %s
                """,
                (case_id,),
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # approvals (insert-only, one per role per case)
    # ------------------------------------------------------------------
    def insert_approval(
        self,
        *,
        case_id: str,
        role: str,
        approver_identity: str,
        decision: str,
        approved_recommendation_hash: str,
    ) -> None:
        with self._adapter.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO remediation_approvals (
                        case_id, role, approver_identity, decision, approved_recommendation_hash
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (case_id, role, approver_identity, decision, approved_recommendation_hash),
                )
                connection.commit()
            except psycopg.errors.UniqueViolation as error:
                connection.rollback()
                raise DuplicateApprovalRoleError(
                    f"a {role} decision already exists for case {case_id}"
                ) from error

    def get_approvals(self, case_id: str) -> list[dict[str, Any]]:
        with self._adapter.connect() as connection:
            rows = connection.execute(
                """
                SELECT role, approver_identity, decision, approved_recommendation_hash, decided_at
                FROM remediation_approvals WHERE case_id = %s ORDER BY role
                """,
                (case_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # envelopes (insert-only, one per case)
    # ------------------------------------------------------------------
    def insert_envelope_if_absent(
        self,
        *,
        case_id: str,
        idempotency_key: str,
        envelope_document: dict[str, Any],
        content_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> dict[str, Any]:
        """Issue the envelope once; every later call returns the same row.

        ``ON CONFLICT (case_id) DO NOTHING`` plus a follow-up SELECT means
        the first call wins and every subsequent call -- including a retried
        "execute" request -- gets back the identical, already-signed
        envelope rather than a fresh one with a different idempotency_key.
        """

        with self._adapter.connect() as connection:
            connection.execute(
                """
                INSERT INTO remediation_envelopes (
                    case_id, idempotency_key, envelope_document, content_hash,
                    issued_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (case_id) DO NOTHING
                """,
                (
                    case_id,
                    idempotency_key,
                    json.dumps(envelope_document),
                    content_hash,
                    issued_at,
                    expires_at,
                ),
            )
            connection.commit()
        existing = self.get_envelope(case_id)
        assert existing is not None
        return existing

    def get_envelope(self, case_id: str) -> dict[str, Any] | None:
        with self._adapter.connect() as connection:
            row = connection.execute(
                """
                SELECT case_id, idempotency_key, envelope_document, content_hash,
                       issued_at, expires_at, created_at
                FROM remediation_envelopes WHERE case_id = %s
                """,
                (case_id,),
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # executions (insert-only, one row per attempt)
    # ------------------------------------------------------------------
    def insert_execution(
        self,
        *,
        case_id: str,
        idempotency_key: str,
        outcome: str,
        detail: str,
        read_back_value: str | None,
        applied: bool,
    ) -> None:
        with self._adapter.connect() as connection:
            connection.execute(
                """
                INSERT INTO remediation_executions (
                    case_id, idempotency_key, outcome, detail, read_back_value, applied
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (case_id, idempotency_key, outcome, detail, read_back_value, applied),
            )
            connection.commit()

    def get_executions(self, case_id: str) -> list[dict[str, Any]]:
        with self._adapter.connect() as connection:
            rows = connection.execute(
                """
                SELECT case_id, idempotency_key, outcome, detail, read_back_value,
                       applied, attempted_at
                FROM remediation_executions WHERE case_id = %s ORDER BY attempted_at, row_id
                """,
                (case_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # evidence (insert-only, one frozen snapshot per case)
    # ------------------------------------------------------------------
    def insert_evidence_if_absent(
        self,
        *,
        evidence_id: str,
        case_id: str,
        evidence_document: dict[str, Any],
        content_hash: str,
    ) -> dict[str, Any]:
        with self._adapter.connect() as connection:
            connection.execute(
                """
                INSERT INTO remediation_evidence (
                    evidence_id, case_id, evidence_document, content_hash
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (case_id) DO NOTHING
                """,
                (evidence_id, case_id, json.dumps(evidence_document, default=str), content_hash),
            )
            connection.commit()
        existing = self.get_evidence(case_id)
        assert existing is not None
        return existing

    def get_evidence(self, case_id: str) -> dict[str, Any] | None:
        with self._adapter.connect() as connection:
            row = connection.execute(
                """
                SELECT evidence_id, case_id, evidence_document, content_hash, created_at
                FROM remediation_evidence WHERE case_id = %s
                """,
                (case_id,),
            ).fetchone()
        return dict(row) if row else None


__all__ = ["BookingApplyOutcome", "DuplicateApprovalRoleError", "RemediationStore"]
