"""Independent, read-only oracle for the TS-11 reconciliation fixtures."""

from .evaluator import BREAK_FAMILIES, OracleFact, OracleResult, evaluate
from .import_graph import (
    ImportIsolationError,
    IsolationReport,
    enforce_isolation,
    scan_repository,
)
from .models import (
    OracleChangedField,
    OracleContext,
    OraclePostAction,
)

__all__ = [
    "BREAK_FAMILIES",
    "ImportIsolationError",
    "IsolationReport",
    "OracleChangedField",
    "OracleContext",
    "OracleFact",
    "OraclePostAction",
    "OracleResult",
    "enforce_isolation",
    "evaluate",
    "scan_repository",
]
