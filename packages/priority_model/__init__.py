"""Explainable, advisory priority scoring for remediation cases.

The package deliberately keeps its public models importable without the
optional LightGBM runtime.  The concrete provider is loaded by the API only
when the ``ml`` extra is installed.
"""

from .models import PriorityAssessment, ShapContribution

__all__ = ["PriorityAssessment", "ShapContribution"]
