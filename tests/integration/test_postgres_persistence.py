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
MIGRATION = ROOT / "packages/persistence/ddl/0001_canonical_persistence.sql"


def _connect() -> psycopg.Connection[dict[str, object]]:
    assert DATABASE_URL is not None
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@pytest.fixture(autouse=True)
def fresh_schema() -> None:
    with _connect() as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        connection.execute(MIGRATION.read_text(encoding="utf-8"))


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


def test_migration_is_reapplicable_without_weakening_append_only_triggers() -> None:
    with _connect() as connection:
        connection.execute(MIGRATION.read_text(encoding="utf-8"))
        trigger_rows = connection.execute(
            """
            SELECT tgname
            FROM pg_trigger
            WHERE tgrelid = 'source_event_inbox'::regclass
              AND NOT tgisinternal
            ORDER BY tgname
            """
        ).fetchall()
        assert [row["tgname"] for row in trigger_rows] == [
            "source_event_inbox_no_delete",
            "source_event_inbox_no_update",
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
