"""TS-10: canonical assembler + source_event_inbox persistence.

Scope (issue #10): persistence/inbox only — no reconciliation logic
(TS-11), no generator changes, no contract changes. See
``packages/persistence/README.md`` and ``ddl/0001_canonical_persistence.sql``.
"""

from __future__ import annotations

from .assembler import (
    CanonicalAssemblyError,
    SourceObservationSetError,
    SourceOfTruthPolicyContentError,
    SourceOfTruthPolicyVersionError,
    SourceOfTruthSelectionError,
    assemble_canonical_state,
    load_mvp_source_of_truth_policy,
    resolve_field_selection,
)
from .inbox import (
    IdentityKey,
    InboxRecord,
    InboxStore,
    IngestOutcome,
    IngestResult,
    SourceConflictError,
    identity_key,
)

__all__ = [
    "IdentityKey",
    "CanonicalAssemblyError",
    "InboxRecord",
    "InboxStore",
    "IngestOutcome",
    "IngestResult",
    "SourceConflictError",
    "SourceObservationSetError",
    "SourceOfTruthPolicyContentError",
    "SourceOfTruthPolicyVersionError",
    "SourceOfTruthSelectionError",
    "assemble_canonical_state",
    "identity_key",
    "load_mvp_source_of_truth_policy",
    "resolve_field_selection",
]
