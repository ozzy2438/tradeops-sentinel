"""Versioned canonical hashing for source observations.

The source ``content_hash`` is a semantic-content fingerprint, not a delivery
identifier.  Delivery-specific fields are deliberately excluded so an
at-least-once retransmission can carry a new observation/event identifier and
ingest timestamp while remaining an idempotent replay of the same source
revision.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any, Final, Protocol, runtime_checkable

OBSERVATION_HASH_STANDARD: Final = "tradeops-observation-canonical-json-v1"

_DELIVERY_FIELDS: Final = frozenset(
    {
        "content_hash",
        "correlation_id",
        "ingest_time",
        "observation_id",
        "source_event_id",
    }
)
_TIMESTAMP_FIELDS: Final = frozenset(
    {
        "event_time",
        "effective_time",
        "execution_time",
        "capture_time",
        "confirmation_time",
        "last_updated_time",
    }
)


@runtime_checkable
class _ModelDump(Protocol):
    def model_dump(self, *, mode: str = "python") -> dict[str, Any]: ...


class ObservationContentHashMismatchError(ValueError):
    """Raised when a declared source hash does not match canonical content."""

    def __init__(
        self,
        *,
        observation_id: str,
        declared_hash: str,
        calculated_hash: str,
    ) -> None:
        self.observation_id = observation_id
        self.declared_hash = declared_hash
        self.calculated_hash = calculated_hash
        super().__init__(
            "observation content_hash does not match canonical content: "
            f"observation_id={observation_id!r} declared={declared_hash!r} "
            f"calculated={calculated_hash!r} standard={OBSERVATION_HASH_STANDARD!r}"
        )


def _utc_timestamp(value: datetime | str) -> str:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("canonical observation timestamps must include a timezone offset")
    normalised = parsed.astimezone(UTC)
    return normalised.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalise(value: Any, *, field_name: str | None = None) -> Any:
    if isinstance(value, datetime):
        return _utc_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if field_name in _TIMESTAMP_FIELDS and isinstance(value, str):
        return _utc_timestamp(value)
    if isinstance(value, Mapping):
        return {
            str(key): _normalise(item, field_name=str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if item is not None
        }
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return value


def canonical_observation_document(observation: Mapping[str, Any] | _ModelDump) -> dict[str, Any]:
    """Return the domain-separated v1 semantic observation document."""

    if isinstance(observation, _ModelDump):
        raw = observation.model_dump(mode="python")
    else:
        raw = dict(observation)
    semantic = {key: value for key, value in raw.items() if key not in _DELIVERY_FIELDS}
    return {
        "hash_standard": OBSERVATION_HASH_STANDARD,
        "observation": _normalise(semantic),
    }


def canonical_observation_bytes(observation: Mapping[str, Any] | _ModelDump) -> bytes:
    """Encode semantic observation content using canonical JSON v1."""

    document = canonical_observation_document(observation)
    return json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def compute_observation_content_hash(observation: Mapping[str, Any] | _ModelDump) -> str:
    """Calculate the v1 SHA-256 semantic content fingerprint."""

    return f"sha256:{sha256(canonical_observation_bytes(observation)).hexdigest()}"


def validate_observation_content_hash(observation: Mapping[str, Any] | _ModelDump) -> str:
    """Fail closed unless the declared hash matches the canonical content."""

    if isinstance(observation, _ModelDump):
        raw = observation.model_dump(mode="python")
    else:
        raw = dict(observation)
    declared = raw.get("content_hash")
    observation_id = raw.get("observation_id")
    if not isinstance(declared, str) or not isinstance(observation_id, str):
        raise ValueError("observation_id and content_hash are required for hash validation")
    calculated = compute_observation_content_hash(raw)
    if declared != calculated:
        raise ObservationContentHashMismatchError(
            observation_id=observation_id,
            declared_hash=declared,
            calculated_hash=calculated,
        )
    return calculated


__all__ = [
    "OBSERVATION_HASH_STANDARD",
    "ObservationContentHashMismatchError",
    "canonical_observation_bytes",
    "canonical_observation_document",
    "compute_observation_content_hash",
    "validate_observation_content_hash",
]
