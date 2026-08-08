"""Fresh-PostgreSQL persistence boundary tests.

The CI job supplies ``TRADEOPS_TEST_DATABASE_URL``.  Local unit-only runs skip
this module when no disposable PostgreSQL endpoint is explicitly configured.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("TRADEOPS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="TRADEOPS_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)

ROOT = Path(__file__).resolve().parents[2]
DDL_DIR = ROOT / "packages/persistence/ddl"
MIGRATIONS = sorted(DDL_DIR.glob("*.sql"))


def _connect() -> psycopg.Connection[dict[str, object]]:
    assert DATABASE_URL is not None
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def _apply_migrations(
    connection: psycopg.Connection[dict[str, object]], migrations: list[Path]
) -> None:
    for migration in migrations:
        connection.execute(migration.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fresh_schema() -> None:
    with _connect() as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        _apply_migrations(connection, MIGRATIONS)


def _insert_source_row(connection: psycopg.Connection[dict[str, object]]) -> int:
    row = connection.execute(
        """
        INSERT INTO source_event_inbox (
            schema_version, observation_id, observation_kind, entity_version,
            tenant_id, portfolio_id, correlation_id, source_system,
            source_event_id, source_business_key, source_version, content_hash,
            event_time, effective_time, ingest_time, source_sequence,
            lineage_group_id, actor, payload
        ) VALUES (
            '1.0.0', 'obs_postgres_append_only_001', 'EXECUTION', 1,
            'tenant_demo', 'portfolio_london', 'corr_postgres_001', 'FIX_EXECUTION',
            'evt_postgres_append_only_001', 'trade_postgres_001', '1',
            'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z',
            '2026-08-03T00:00:01Z', 1, 'lineage_postgres_001',
            '{"identity_type":"SOURCE","actor_id":"fix_execution"}'::jsonb,
            '{"marker":"original"}'::jsonb
        )
        RETURNING inbox_id
        """
    ).fetchone()
    assert row is not None
    return int(row["inbox_id"])


@pytest.mark.parametrize(
    ("operation", "statement"),
    [
        (
            "UPDATE",
            'UPDATE source_event_inbox SET payload = \'{"marker":"changed"}\'::jsonb '
            "WHERE inbox_id = %s",
        ),
        ("DELETE", "DELETE FROM source_event_inbox WHERE inbox_id = %s"),
    ],
)
def test_source_event_inbox_rejects_destructive_mutation(
    operation: str,
    statement: str,
) -> None:
    with _connect() as connection:
        inbox_id = _insert_source_row(connection)
        connection.commit()

        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            connection.execute(statement, (inbox_id,))
        connection.rollback()

        stored = connection.execute(
            "SELECT payload FROM source_event_inbox WHERE inbox_id = %s", (inbox_id,)
        ).fetchone()
        assert stored is not None
        assert stored["payload"] == {"marker": "original"}
        assert operation in {"UPDATE", "DELETE"}


def _insert_canonical_row(connection: psycopg.Connection[dict[str, object]]) -> None:
    connection.execute(
        """
        INSERT INTO canonical_trade_state_versions (
            schema_version, trade_id, entity_version, canonical_state_version,
            tenant_id, portfolio_id, correlation_id, content_hash, as_of_time,
            source_watermark, source_version_set, actor, state, field_provenance
        ) VALUES (
            '1.0.0', 'trade_postgres_001', 1, 1,
            'tenant_demo', 'portfolio_london', 'corr_postgres_001',
            'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
            '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z',
            '[]'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb
        )
        """
    )


# TRUNCATE does not fire row-level BEFORE UPDATE/DELETE triggers, so an
# append-only table guarded only by row triggers can still be emptied in one
# statement. These two tests pin the statement-level BEFORE TRUNCATE guards
# added in 0002 and assert the rows actually survive the attempt.
def test_source_event_inbox_rejects_truncate() -> None:
    with _connect() as connection:
        _insert_source_row(connection)
        connection.commit()

        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            connection.execute("TRUNCATE source_event_inbox")
        connection.rollback()

        surviving = connection.execute(
            "SELECT count(*) AS total FROM source_event_inbox"
        ).fetchone()
        assert surviving is not None
        assert surviving["total"] == 1


def test_canonical_trade_state_versions_rejects_truncate() -> None:
    with _connect() as connection:
        _insert_canonical_row(connection)
        connection.commit()

        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            connection.execute("TRUNCATE canonical_trade_state_versions")
        connection.rollback()

        surviving = connection.execute(
            "SELECT count(*) AS total FROM canonical_trade_state_versions"
        ).fetchone()
        assert surviving is not None
        assert surviving["total"] == 1


def _trigger_names(connection: psycopg.Connection[dict[str, object]], table: str) -> list[str]:
    rows = connection.execute(
        """
        SELECT tgname
        FROM pg_trigger
        WHERE tgrelid = %s::regclass
          AND NOT tgisinternal
        ORDER BY tgname
        """,
        (table,),
    ).fetchall()
    return [str(row["tgname"]) for row in rows]


def _canonical_key_constraint_def(
    connection: psycopg.Connection[dict[str, object]],
) -> str | None:
    row = connection.execute(
        """
        SELECT pg_get_constraintdef(oid) AS definition
        FROM pg_constraint
        WHERE conrelid = 'canonical_trade_state_versions'::regclass
          AND conname = 'canonical_trade_state_versions_trade_version_key'
        """
    ).fetchone()
    return None if row is None else str(row["definition"])


def _canonical_current_index_def(
    connection: psycopg.Connection[dict[str, object]],
) -> str | None:
    row = connection.execute(
        sql.SQL(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = 'canonical_trade_state_versions' "
            "AND indexname = 'canonical_trade_state_versions_current_idx'"
        )
    ).fetchone()
    return None if row is None else str(row["indexdef"])


def test_migration_is_reapplicable_without_weakening_append_only_triggers() -> None:
    with _connect() as connection:
        _apply_migrations(connection, MIGRATIONS)
        assert _trigger_names(connection, "source_event_inbox") == [
            "source_event_inbox_no_delete",
            "source_event_inbox_no_truncate",
            "source_event_inbox_no_update",
        ]
        assert _trigger_names(connection, "remediation_priority_assessments") == [
            "remediation_priority_assessments_no_delete",
            "remediation_priority_assessments_no_truncate",
            "remediation_priority_assessments_no_update",
        ]
        assert _trigger_names(connection, "canonical_trade_state_versions") == [
            "canonical_trade_state_versions_no_delete",
            "canonical_trade_state_versions_no_truncate",
            "canonical_trade_state_versions_no_update",
        ]


def test_canonical_scope_index_includes_portfolio() -> None:
    with _connect() as connection:
        indexes = connection.execute(
            sql.SQL(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'canonical_trade_state_versions'"
            )
        ).fetchall()
    assert any(
        "(tenant_id, portfolio_id, trade_id, canonical_state_version" in str(row["indexdef"])
        for row in indexes
    )


def test_fresh_install_applies_all_migrations_and_creates_expected_objects() -> None:
    # The autouse fresh_schema fixture already ran every migration in
    # packages/persistence/ddl against an empty schema; this asserts that a
    # single from-scratch install lands directly on the fully-migrated
    # (0002) shape, not the legacy 0001-only shape.
    with _connect() as connection:
        assert _trigger_names(connection, "source_event_inbox") == [
            "source_event_inbox_no_delete",
            "source_event_inbox_no_truncate",
            "source_event_inbox_no_update",
        ]
        assert _canonical_key_constraint_def(connection) == (
            "UNIQUE (tenant_id, portfolio_id, trade_id, canonical_state_version)"
        )
        index_definition = _canonical_current_index_def(connection)
        assert index_definition is not None
        assert "(tenant_id, portfolio_id, trade_id, canonical_state_version" in index_definition


def test_upgrade_from_0001_only_to_0002_migrates_legacy_schema() -> None:
    migration_0001 = DDL_DIR / "0001_canonical_persistence.sql"
    migration_0002 = DDL_DIR / "0002_p1_production_boundaries.sql"
    with _connect() as connection:
        # Roll back to a pre-P1 (0001-only) database to prove 0002 upgrades
        # it in place, rather than only working on a fresh install.
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        connection.execute(migration_0001.read_text(encoding="utf-8"))

        assert _trigger_names(connection, "source_event_inbox") == []
        assert _canonical_key_constraint_def(connection) == (
            "UNIQUE (tenant_id, trade_id, canonical_state_version)"
        )
        legacy_index_definition = _canonical_current_index_def(connection)
        assert legacy_index_definition is not None
        assert "(tenant_id, portfolio_id," not in legacy_index_definition

        connection.execute(migration_0002.read_text(encoding="utf-8"))

        assert _trigger_names(connection, "source_event_inbox") == [
            "source_event_inbox_no_delete",
            "source_event_inbox_no_truncate",
            "source_event_inbox_no_update",
        ]
        assert _canonical_key_constraint_def(connection) == (
            "UNIQUE (tenant_id, portfolio_id, trade_id, canonical_state_version)"
        )
        upgraded_index_definition = _canonical_current_index_def(connection)
        assert upgraded_index_definition is not None
        assert "(tenant_id, portfolio_id, trade_id, canonical_state_version" in (
            upgraded_index_definition
        )


def test_all_migrations_reapply_idempotently_over_already_migrated_schema() -> None:
    with _connect() as connection:
        # fresh_schema already applied every migration once; apply the full
        # sequence a second time on top and confirm state is unchanged, not
        # duplicated (extra triggers, weakened constraints, stale indexes).
        _apply_migrations(connection, MIGRATIONS)
        _apply_migrations(connection, MIGRATIONS)

        assert _trigger_names(connection, "source_event_inbox") == [
            "source_event_inbox_no_delete",
            "source_event_inbox_no_truncate",
            "source_event_inbox_no_update",
        ]
        assert _trigger_names(connection, "canonical_trade_state_versions") == [
            "canonical_trade_state_versions_no_delete",
            "canonical_trade_state_versions_no_truncate",
            "canonical_trade_state_versions_no_update",
        ]
        assert _canonical_key_constraint_def(connection) == (
            "UNIQUE (tenant_id, portfolio_id, trade_id, canonical_state_version)"
        )
        index_definition = _canonical_current_index_def(connection)
        assert index_definition is not None
        assert "(tenant_id, portfolio_id, trade_id, canonical_state_version" in index_definition
