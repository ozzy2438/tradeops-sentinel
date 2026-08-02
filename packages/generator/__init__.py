"""Deterministic synthetic FX lifecycle fixtures for the E3 generator."""

from .core import (
    BREAK_FAMILIES,
    PRODUCTS,
    GeneratedCorpus,
    GeneratorConfig,
    generate_corpus,
)

__all__ = [
    "BREAK_FAMILIES",
    "PRODUCTS",
    "GeneratedCorpus",
    "GeneratorConfig",
    "generate_corpus",
]
