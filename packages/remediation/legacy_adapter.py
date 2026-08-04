"""Mock legacy booking system adapter.

UiPath-ready controlled action contract demonstrated through a mock
legacy-booking adapter: this class is the exact boundary a UiPath robot, or
any other legacy-system integration, would sit behind -- read the current
record, apply exactly one verified field change under a signed envelope,
report what happened. No real UiPath environment is installed, configured,
or connected anywhere in this repository. This is a mock of that boundary,
not an integration with it.
"""

from __future__ import annotations

from typing import Any

from .store import BookingApplyOutcome, RemediationStore


class MockLegacyBookingAdapter:
    """The only component permitted to write to ``legacy_booking_records``."""

    def __init__(self, store: RemediationStore) -> None:
        self._store = store

    def read(self, *, tenant_id: str, portfolio_id: str, trade_id: str) -> dict[str, Any] | None:
        """Re-read the booking record -- the post-action verification read-back."""

        return self._store.read_legacy_booking_record(
            tenant_id=tenant_id, portfolio_id=portfolio_id, trade_id=trade_id
        )

    def apply(
        self,
        *,
        tenant_id: str,
        portfolio_id: str,
        trade_id: str,
        approved_value: str,
        expected_old_value: str,
        idempotency_key: str,
    ) -> tuple[BookingApplyOutcome, str | None]:
        """Apply the single approved field change, or safely no-op on replay."""

        return self._store.apply_legacy_booking_correction(
            tenant_id=tenant_id,
            portfolio_id=portfolio_id,
            trade_id=trade_id,
            approved_value=approved_value,
            expected_old_value=expected_old_value,
            idempotency_key=idempotency_key,
        )


__all__ = ["MockLegacyBookingAdapter"]
