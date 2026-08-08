"""Transactional PostgreSQL adapter for the visible product.

``InboxStore`` (inbox.py) is the storage-agnostic semantic reference. This
module is the real psycopg 3 implementation of the same three ingest outcomes
against ``source_event_inbox``, plus canonical-state, run and break
persistence.

Ingest semantics mirror inbox.py exactly and are enforced inside one
transaction per observation batch:

* content hash is recomputed and validated before any trust decision, so a
  forged ``content_hash`` cannot suppress a material payload difference;
* an unseen identity/version INSERTs;
* the same identity/version with the *same* verified content is an
  ``IDEMPOTENT_REPLAY`` -- a no-op, not a second row;
* the same identity/version with *different* verified content is a
  ``DUPLICATE_SOURCE_CONFLICT`` and raises ``SourceConflictError``.

Any database error rolls the whole batch back; partial ingestion is never
committed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

import psycopg
from psycopg.rows import dict_row

from packages.contracts.hashing import validate_observation_content_hash
from packages.contracts.models import (
    CanonicalTradeState,
    DuplicateSourceConflict,
    ObservationEnvelope,
    TradeBreak,
)
from packages.persistence.inbox import SourceConflictError, identity_key

LOGGER = logging.getLogger("tradeops.persistence.adapter")

MIGRATION_FILENAMES: tuple[str, ...] = (
    "0001_canonical_persistence.sql",
    "0002_p1_production_boundaries.sql",
    "0003_product_runtime.sql",
    "0004_ai_remediation.sql",
    "0005_ml_priority_assessment.sql",
)

# Statement timeout applied to every session so a pathological query cannot
# pin a connection indefinitely behind the API's request timeout.
DEFAULT_STATEMENT_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class IngestSummary:
    """Outcome counts for one ingest batch."""

    inserted: int
    replayed: int
    conflicted: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.replayed + self.conflicted


class DatabaseUnavailableError(RuntimeError):
    """Raised when PostgreSQL cannot be reached or is not migrated."""


def migration_sql() -> list[tuple[str, str]]:
    """Return ``(filename, sql)`` for every migration, in application order."""

    ddl = files("packages.persistence").joinpath("ddl")
    return [(name, ddl.joinpath(name).read_text(encoding="utf-8")) for name in MIGRATION_FILENAMES]


class PostgresAdapter:
    """Small transactional adapter -- deliberately not a framework."""

    def __init__(self, dsn: str, *, statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS):
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        """Open one connection with an enforced statement timeout."""

        try:
            connection = psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=10)
        except psycopg.Error as error:  # pragma: no cover - exercised via readiness
            raise DatabaseUnavailableError(str(error)) from error
        try:
            connection.execute(f"SET statement_timeout = {self._statement_timeout_ms}")
            yield connection
        finally:
            connection.close()

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------
    def migrate(self) -> list[str]:
        """Apply every migration in order. Idempotent and safe to re-run."""

        applied: list[str] = []
        with self.connect() as connection:
            for name, sql in migration_sql():
                connection.execute(sql)
                applied.append(name)
            connection.commit()
        LOGGER.info("migrations_applied", extra={"migrations": applied})
        return applied

    def is_ready(self) -> tuple[bool, str]:
        """Cheap readiness probe: reachable *and* migrated."""

        try:
            with self.connect() as connection:
                row = connection.execute(
                    """
                    SELECT count(*) AS present
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name IN (
                        'source_event_inbox',
                        'canonical_trade_state_versions',
                        'reconciliation_runs',
                        'trade_breaks'
                      )
                    """
                ).fetchone()
        except DatabaseUnavailableError as error:
            return False, f"database unreachable: {type(error).__name__}"
        except psycopg.Error as error:
            return False, f"database error: {type(error).__name__}"
        present = int(row["present"]) if row else 0
        if present != 4:
            return False, f"schema incomplete: {present}/4 required tables present"
        return True, "ready"

    # ------------------------------------------------------------------
    # ingestion
    # ------------------------------------------------------------------
    def ingest_observations(
        self,
        observations: Iterable[ObservationEnvelope],
        *,
        on_conflict: str = "raise",
    ) -> IngestSummary:
        """Ingest a batch transactionally with replay/conflict semantics.

        The whole batch commits or rolls back together, so a contradictory
        source revision is never half-applied.

        ``on_conflict`` selects what a DUPLICATE_SOURCE_CONFLICT does:

        * ``"raise"`` (default) -- abort and roll the batch back. This is the
          strict C-09 contract used by callers ingesting a single trusted feed.
        * ``"quarantine"`` -- keep the authoritative row untouched and record
          the rejected delivery in ``source_event_conflicts`` as evidence, then
          continue. Used for bulk demo load, where the corpus deliberately
          contains conflicting revisions so the DUPLICATE_SOURCE_CONFLICT break
          family can be detected downstream.

        Either way the authoritative inbox row is never overwritten.
        """

        if on_conflict not in {"raise", "quarantine"}:
            raise ValueError("on_conflict must be 'raise' or 'quarantine'")
        inserted = 0
        replayed = 0
        conflicted = 0
        with self.connect() as connection:
            try:
                for observation in observations:
                    # Never trust the caller's fingerprint.
                    validate_observation_content_hash(observation)
                    key = identity_key(observation)
                    existing = connection.execute(
                        """
                        SELECT content_hash, observation_id
                        FROM source_event_inbox
                        WHERE tenant_id = %s AND portfolio_id = %s
                          AND source_system = %s AND observation_kind = %s
                          AND source_business_key = %s AND source_version = %s
                        FOR UPDATE
                        """,
                        (
                            key.tenant_id,
                            key.portfolio_id,
                            key.source_system,
                            key.observation_kind,
                            key.source_business_key,
                            key.source_version,
                        ),
                    ).fetchone()

                    if existing is None:
                        self._insert_observation(connection, observation)
                        inserted += 1
                        continue

                    if str(existing["content_hash"]) == observation.content_hash:
                        replayed += 1
                        continue

                    conflict = SourceConflictError(
                        DuplicateSourceConflict(
                            conflict_type="SAME_SOURCE_KEY_VERSION_CONTENT",
                            source_observation_ids=sorted(
                                {str(existing["observation_id"]), observation.observation_id}
                            ),
                            source_business_key=key.source_business_key,
                            source_version=key.source_version,
                        )
                    )
                    if on_conflict == "raise":
                        raise conflict
                    self._quarantine_conflict(
                        connection,
                        observation,
                        conflicting_observation_id=str(existing["observation_id"]),
                        conflicting_content_hash=str(existing["content_hash"]),
                    )
                    conflicted += 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        LOGGER.info(
            "observations_ingested",
            extra={"inserted": inserted, "replayed": replayed, "conflicted": conflicted},
        )
        return IngestSummary(inserted=inserted, replayed=replayed, conflicted=conflicted)

    @staticmethod
    def _quarantine_conflict(
        connection: psycopg.Connection[dict[str, Any]],
        observation: ObservationEnvelope,
        *,
        conflicting_observation_id: str,
        conflicting_content_hash: str,
    ) -> None:
        document = json.loads(observation.model_dump_json())
        connection.execute(
            """
            INSERT INTO source_event_conflicts (
                observation_id, tenant_id, portfolio_id, correlation_id,
                lineage_group_id, source_system, observation_kind,
                source_business_key, source_version, content_hash,
                conflicting_observation_id, conflicting_content_hash,
                observation_document
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT ON CONSTRAINT source_event_conflicts_identity_key DO NOTHING
            """,
            (
                observation.observation_id,
                observation.tenant_id,
                observation.portfolio_id,
                observation.correlation_id,
                observation.lineage_group_id,
                observation.source_system,
                observation.observation_kind,
                observation.source_business_key,
                observation.source_version,
                observation.content_hash,
                conflicting_observation_id,
                conflicting_content_hash,
                json.dumps(document),
            ),
        )

    def conflict_documents(
        self, *, tenant_id: str, portfolio_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Return quarantined conflicting deliveries for the scope."""

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT observation_document
                FROM source_event_conflicts
                WHERE tenant_id = %s AND portfolio_id = ANY(%s)
                ORDER BY lineage_group_id, observation_id
                """,
                (tenant_id, list(portfolio_ids)),
            ).fetchall()
        return [dict(row["observation_document"]) for row in rows]

    @staticmethod
    def _insert_observation(
        connection: psycopg.Connection[dict[str, Any]],
        observation: ObservationEnvelope,
    ) -> None:
        document = json.loads(observation.model_dump_json())
        connection.execute(
            """
            INSERT INTO source_event_inbox (
                schema_version, observation_id, observation_kind, entity_version,
                tenant_id, portfolio_id, correlation_id, source_system,
                source_event_id, source_business_key, source_version, content_hash,
                event_time, effective_time, ingest_time, source_sequence,
                lineage_group_id, actor, supersedes_observation_id,
                supersession_reason, payload
            ) VALUES (
                %(schema_version)s, %(observation_id)s, %(observation_kind)s,
                %(entity_version)s, %(tenant_id)s, %(portfolio_id)s,
                %(correlation_id)s, %(source_system)s, %(source_event_id)s,
                %(source_business_key)s, %(source_version)s, %(content_hash)s,
                %(event_time)s, %(effective_time)s, %(ingest_time)s,
                %(source_sequence)s, %(lineage_group_id)s, %(actor)s,
                %(supersedes_observation_id)s, %(supersession_reason)s, %(payload)s
            )
            """,
            {
                **{
                    field: document[field]
                    for field in (
                        "schema_version",
                        "observation_id",
                        "observation_kind",
                        "entity_version",
                        "tenant_id",
                        "portfolio_id",
                        "correlation_id",
                        "source_system",
                        "source_event_id",
                        "source_business_key",
                        "source_version",
                        "content_hash",
                        "event_time",
                        "effective_time",
                        "ingest_time",
                        "source_sequence",
                        "lineage_group_id",
                    )
                },
                "actor": json.dumps(document["actor"]),
                "supersedes_observation_id": document.get("supersedes_observation_id"),
                "supersession_reason": document.get("supersession_reason"),
                "payload": json.dumps(document["payload"]),
            },
        )

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def observation_documents(
        self, *, tenant_id: str, portfolio_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Return raw observation documents for one tenant/portfolio scope.

        Scope is always required: no call path can read across tenants.
        """

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT schema_version, observation_id, observation_kind, entity_version,
                       tenant_id, portfolio_id, correlation_id, source_system,
                       source_event_id, source_business_key, source_version, content_hash,
                       event_time, effective_time, ingest_time, source_sequence,
                       lineage_group_id, actor, supersedes_observation_id,
                       supersession_reason, payload
                FROM source_event_inbox
                WHERE tenant_id = %s AND portfolio_id = ANY(%s)
                ORDER BY lineage_group_id, source_sequence, observation_id
                """,
                (tenant_id, list(portfolio_ids)),
            ).fetchall()
        return [dict(row) for row in rows]

    def observation_count(self, *, tenant_id: str, portfolio_ids: Sequence[str]) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT count(*) AS total FROM source_event_inbox "
                "WHERE tenant_id = %s AND portfolio_id = ANY(%s)",
                (tenant_id, list(portfolio_ids)),
            ).fetchone()
        return int(row["total"]) if row else 0

    # ------------------------------------------------------------------
    # canonical state
    # ------------------------------------------------------------------
    def persist_canonical_states(self, states: Sequence[CanonicalTradeState]) -> int:
        """Append canonical states, skipping versions already stored."""

        written = 0
        with self.connect() as connection:
            try:
                for state in states:
                    document = json.loads(state.model_dump_json())
                    existing = connection.execute(
                        """
                        SELECT 1 FROM canonical_trade_state_versions
                        WHERE tenant_id = %s AND portfolio_id = %s
                          AND trade_id = %s AND canonical_state_version = %s
                        """,
                        (
                            state.tenant_id,
                            state.portfolio_id,
                            state.trade_id,
                            state.canonical_state_version,
                        ),
                    ).fetchone()
                    if existing is not None:
                        continue
                    connection.execute(
                        """
                        INSERT INTO canonical_trade_state_versions (
                            schema_version, trade_id, entity_version, canonical_state_version,
                            tenant_id, portfolio_id, correlation_id, content_hash,
                            as_of_time, source_watermark, source_version_set, actor,
                            state, field_provenance
                        ) VALUES (
                            %(schema_version)s, %(trade_id)s, %(entity_version)s,
                            %(canonical_state_version)s, %(tenant_id)s, %(portfolio_id)s,
                            %(correlation_id)s, %(content_hash)s, %(as_of_time)s,
                            %(source_watermark)s, %(source_version_set)s, %(actor)s,
                            %(state)s, %(field_provenance)s
                        )
                        """,
                        {
                            **{
                                field: document[field]
                                for field in (
                                    "schema_version",
                                    "trade_id",
                                    "entity_version",
                                    "canonical_state_version",
                                    "tenant_id",
                                    "portfolio_id",
                                    "correlation_id",
                                    "content_hash",
                                    "as_of_time",
                                    "source_watermark",
                                )
                            },
                            "source_version_set": json.dumps(document["source_version_set"]),
                            "actor": json.dumps(document["actor"]),
                            "state": json.dumps(document["state"]),
                            "field_provenance": json.dumps(document["field_provenance"]),
                        },
                    )
                    written += 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return written

    def canonical_state_document(
        self, *, tenant_id: str, portfolio_ids: Sequence[str], trade_id: str
    ) -> dict[str, Any] | None:
        """Return the highest-version canonical state for one trade."""

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT trade_id, canonical_state_version, tenant_id, portfolio_id,
                       correlation_id, content_hash, as_of_time, source_watermark,
                       source_version_set, state, field_provenance
                FROM canonical_trade_state_versions
                WHERE tenant_id = %s AND portfolio_id = ANY(%s) AND trade_id = %s
                ORDER BY canonical_state_version DESC
                LIMIT 1
                """,
                (tenant_id, list(portfolio_ids), trade_id),
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # runs and breaks
    # ------------------------------------------------------------------
    def latest_completed_run_id(
        self, *, tenant_id: str, portfolio_ids: Sequence[str]
    ) -> str | None:
        """Return the run_id of the most recently completed reconciliation run.

        A reconciliation run writes one ``reconciliation_runs`` row per
        portfolio under the same ``run_id``, so this identifies *which* run is
        latest -- callers then scope trades/breaks to that run_id rather than
        aggregating across every historical run ever persisted.

        Ordered by ``completed_at DESC`` with ``row_id DESC`` as a tiebreaker:
        ``row_id`` is a monotonically increasing identity column, so selection
        stays deterministic even when two runs complete within the same
        wall-clock second -- it never relies on timestamp resolution alone.
        """

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT run_id
                FROM reconciliation_runs
                WHERE tenant_id = %s AND portfolio_id = ANY(%s) AND status = 'COMPLETED'
                ORDER BY completed_at DESC, row_id DESC
                LIMIT 1
                """,
                (tenant_id, list(portfolio_ids)),
            ).fetchone()
        return str(row["run_id"]) if row else None

    def runs_by_run_id(
        self, *, tenant_id: str, portfolio_ids: Sequence[str], run_id: str
    ) -> list[dict[str, Any]]:
        """Return every per-portfolio run row sharing one run_id.

        One reconciliation invocation produces one row per portfolio; this
        lets a caller sum trades_evaluated/clean/broken/break_count across
        the portfolios of a single run without re-deriving them from
        canonical state or trade_breaks.
        """

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, run_version, portfolio_id, config_id, config_version,
                       config_hash, detection_rule_version, trades_evaluated,
                       observations_ingested, clean_trades, broken_trades,
                       break_count, status, started_at, completed_at
                FROM reconciliation_runs
                WHERE tenant_id = %s AND portfolio_id = ANY(%s) AND run_id = %s
                ORDER BY portfolio_id
                """,
                (tenant_id, list(portfolio_ids), run_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def persist_run(
        self,
        *,
        run_id: str,
        run_version: int,
        tenant_id: str,
        portfolio_id: str,
        config_id: str,
        config_version: str,
        config_hash: str,
        detection_rule_version: str,
        trades_evaluated: int,
        observations_ingested: int,
        clean_trades: int,
        broken_trades: int,
        breaks: Sequence[TradeBreak],
        status: str,
        started_at: datetime,
    ) -> None:
        """Persist one run and all of its breaks in a single transaction."""

        with self.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO reconciliation_runs (
                        run_id, run_version, tenant_id, portfolio_id, config_id,
                        config_version, config_hash, detection_rule_version,
                        trades_evaluated, observations_ingested, clean_trades,
                        broken_trades, break_count, status, started_at, completed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        run_id,
                        run_version,
                        tenant_id,
                        portfolio_id,
                        config_id,
                        config_version,
                        config_hash,
                        detection_rule_version,
                        trades_evaluated,
                        observations_ingested,
                        clean_trades,
                        broken_trades,
                        len(breaks),
                        status,
                        started_at,
                        datetime.now(UTC),
                    ),
                )
                for item in breaks:
                    document = json.loads(item.model_dump_json())
                    connection.execute(
                        """
                        INSERT INTO trade_breaks (
                            break_id, break_version, run_id, tenant_id, portfolio_id,
                            correlation_id, trade_id, canonical_state_version,
                            product_type, break_family, condition_code, severity,
                            state, detected_at, break_document, source_version_set,
                            config_hash
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s
                        )
                        """,
                        (
                            item.break_id,
                            item.break_version,
                            run_id,
                            item.tenant_id,
                            item.portfolio_id,
                            item.correlation_id,
                            item.trade_id,
                            item.canonical_state_version,
                            item.product_type,
                            item.family,
                            item.condition_code,
                            item.severity,
                            item.state,
                            item.detected_at,
                            json.dumps(document),
                            json.dumps(document["source_version_set"]),
                            config_hash,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def recent_runs(
        self, *, tenant_id: str, portfolio_ids: Sequence[str], limit: int = 20
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, run_version, portfolio_id, config_id, config_version, config_hash,
                       detection_rule_version, trades_evaluated, observations_ingested,
                       clean_trades, broken_trades, break_count, status,
                       started_at, completed_at
                FROM reconciliation_runs
                WHERE tenant_id = %s AND portfolio_id = ANY(%s)
                ORDER BY completed_at DESC, row_id DESC
                LIMIT %s
                """,
                (tenant_id, list(portfolio_ids), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_run(self, *, tenant_id: str, portfolio_ids: Sequence[str]) -> dict[str, Any] | None:
        runs = self.recent_runs(tenant_id=tenant_id, portfolio_ids=portfolio_ids, limit=1)
        return runs[0] if runs else None

    def query_breaks(
        self,
        *,
        tenant_id: str,
        portfolio_ids: Sequence[str],
        product_type: str | None = None,
        break_family: str | None = None,
        state: str | None = None,
        run_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = ["tenant_id = %s", "portfolio_id = ANY(%s)"]
        params: list[Any] = [tenant_id, list(portfolio_ids)]
        for column, value in (
            ("product_type", product_type),
            ("break_family", break_family),
            ("state", state),
            ("run_id", run_id),
        ):
            if value:
                clauses.append(f"{column} = %s")
                params.append(value)
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT break_id, break_version, run_id, trade_id, product_type,
                       break_family, condition_code, severity, state, detected_at,
                       canonical_state_version, config_hash
                FROM trade_breaks
                WHERE {" AND ".join(clauses)}
                ORDER BY detected_at DESC, break_id
                LIMIT %s
                """,  # noqa: S608 - clauses are built from a fixed column allowlist
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def break_detail(
        self, *, tenant_id: str, portfolio_ids: Sequence[str], break_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT break_id, break_version, run_id, trade_id, product_type,
                       break_family, condition_code, severity, state, detected_at,
                       canonical_state_version, config_hash, break_document,
                       source_version_set
                FROM trade_breaks
                WHERE tenant_id = %s AND portfolio_id = ANY(%s) AND break_id = %s
                ORDER BY break_version DESC
                LIMIT 1
                """,
                (tenant_id, list(portfolio_ids), break_id),
            ).fetchone()
        return dict(row) if row else None

    def break_family_counts(
        self, *, tenant_id: str, portfolio_ids: Sequence[str], run_id: str | None = None
    ) -> dict[str, int]:
        """Break counts by family, optionally scoped to one run_id.

        Without ``run_id`` this aggregates every historical break ever
        persisted for the scope -- callers displaying a single reconciliation
        result (e.g. the product summary) must pass the run_id they mean, or
        family counts silently accumulate across every past run.
        """

        clauses = ["tenant_id = %s", "portfolio_id = ANY(%s)"]
        params: list[Any] = [tenant_id, list(portfolio_ids)]
        if run_id:
            clauses.append("run_id = %s")
            params.append(run_id)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT break_family, count(*) AS total FROM trade_breaks "
                f"WHERE {' AND '.join(clauses)} GROUP BY break_family",  # noqa: S608
                params,
            ).fetchall()
        return {str(row["break_family"]): int(row["total"]) for row in rows}

    def product_counts(self, *, tenant_id: str, portfolio_ids: Sequence[str]) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT state->>'product_type' AS product_type, count(DISTINCT trade_id) AS total
                FROM canonical_trade_state_versions
                WHERE tenant_id = %s AND portfolio_id = ANY(%s)
                GROUP BY state->>'product_type'
                """,
                (tenant_id, list(portfolio_ids)),
            ).fetchall()
        return {str(row["product_type"]): int(row["total"]) for row in rows if row["product_type"]}
