"""Minimum runbook retrieval: a local keyword index over three synthetic docs.

Deliberately not a vector database or RAG platform -- the runbook corpus is
three short markdown files, parsed once into sections, and searched by simple
keyword overlap. Every citation returned names a real, checkable
(document_id, section) pair; ``citation_supports_action`` is the deterministic
gate the policy engine uses to reject a citation that doesn't actually back
the recommended action, closing the "cites something irrelevant" loophole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

_SECTION_HEADING = re.compile(r"^##\s+Section\s+(\d+)\s+—\s+(.+)$", re.MULTILINE)
_WORD = re.compile(r"[a-z0-9]+")

# Which runbook sections are acceptable support for each recommended_action.
# Deliberately explicit and closed: an action not listed here can never be
# cited into eligibility, regardless of what the retrieval search returns.
_SUPPORTING_SECTIONS: dict[str, frozenset[str]] = {
    "CORRECT_LEGACY_BOOKING_FIELD": frozenset(
        {"RB-001#3", "RB-001#1", "RB-001#2", "RB-002#1", "RB-002#7"}
    ),
    "MANUAL_INVESTIGATION": frozenset({"RB-001#5", "RB-002#4"}),
}


@dataclass(frozen=True)
class RunbookSection:
    document_id: str
    section_number: int
    title: str
    content: str

    @property
    def citation_key(self) -> str:
        return f"{self.document_id}#{self.section_number}"


@dataclass(frozen=True)
class RetrievedSection:
    """One search result: a runbook section plus why it matched.

    Distinct from ``packages.remediation.models.Citation`` -- that is the
    strict, minimal (document_id, section) pair the AI output schema and the
    policy engine work with. This carries the extra title/snippet a caller
    needs to build a prompt or render a search result, and is never itself
    accepted anywhere a ``Citation`` is required.
    """

    document_id: str
    section: str
    title: str
    snippet: str

    @property
    def citation_key(self) -> str:
        return f"{self.document_id}#{self.section}"


def _document_id(filename: str) -> str:
    # "RB-001-fx-economic-value-mismatch.md" -> "RB-001"
    return filename.split("-", 2)[0] + "-" + filename.split("-", 2)[1]


@lru_cache(maxsize=1)
def _index() -> tuple[RunbookSection, ...]:
    runbook_dir = files("packages.remediation").joinpath("runbooks")
    sections: list[RunbookSection] = []
    for entry in sorted(runbook_dir.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".md"):
            continue
        text = entry.read_text(encoding="utf-8")
        document_id = _document_id(entry.name)
        matches = list(_SECTION_HEADING.finditer(text))
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections.append(
                RunbookSection(
                    document_id=document_id,
                    section_number=int(match.group(1)),
                    title=match.group(2).strip(),
                    content=text[start:end].strip(),
                )
            )
    if not sections:
        raise RuntimeError("runbook index is empty -- no sections parsed")
    return tuple(sections)


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def search(query: str, *, top_k: int = 3) -> list[RetrievedSection]:
    """Keyword-overlap search over the runbook section index.

    Not semantic search -- token-set overlap between the query and each
    section's title+content, ranked by overlap size. Sufficient for a
    three-document, closed corpus; the policy engine never trusts search
    relevance alone -- see ``citation_supports_action``.
    """

    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    scored = []
    for section in _index():
        section_tokens = _tokens(f"{section.title} {section.content}")
        overlap = len(query_tokens & section_tokens)
        if overlap > 0:
            scored.append((overlap, section))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        RetrievedSection(
            document_id=section.document_id,
            section=str(section.section_number),
            title=section.title,
            snippet=section.content.splitlines()[0][:200],
        )
        for _, section in scored[:top_k]
    ]


def citation_exists(citation_key: str) -> bool:
    return any(section.citation_key == citation_key for section in _index())


def citation_supports_action(citation_key: str, recommended_action: str) -> bool:
    """Deterministic fail-closed gate: does this citation actually back this action?

    A citation that resolves to a real document/section but was not on the
    closed support-list for the recommended action does not count -- this is
    what makes "the citation does not support the action" a real, checkable
    failure mode rather than a formality.
    """

    if not citation_exists(citation_key):
        return False
    allowed = _SUPPORTING_SECTIONS.get(recommended_action)
    return allowed is not None and citation_key in allowed


__all__ = [
    "RetrievedSection",
    "RunbookSection",
    "citation_exists",
    "citation_supports_action",
    "search",
]
