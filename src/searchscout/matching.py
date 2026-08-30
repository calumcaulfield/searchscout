"""How a search term is matched against product text.

The original had one match rule and used it inconsistently: products were
*found* case-insensitively and then *edited* case-sensitively, so a product
matched on "Cotton" while searching "cotton" was reported as updated and
silently left unchanged. Making the rule explicit — and using the same rule for
both halves — is what closes that.
"""

from __future__ import annotations

import re
from enum import StrEnum


class MatchMode(StrEnum):
    LITERAL = "literal"
    """Exact, case-sensitive. The safest default for a bulk edit."""

    CASE_INSENSITIVE = "case-insensitive"
    """Matches any casing. The replacement is written as given."""

    WHOLE_WORD = "whole-word"
    """Case-insensitive, but only on word boundaries.

    "cotton" does not match "cottonseed". This is the mode that stops a
    catalogue-wide rename of "Gift" from mangling "Gifted".
    """

    REGEX = "regex"
    """Caller-supplied pattern. Never the default."""


def compile_pattern(term: str, mode: MatchMode) -> re.Pattern[str]:
    """Turn a search term into the single pattern used for both find and replace.

    One pattern for both operations is the whole point: the defect this
    replaces came from finding with one rule and replacing with another.
    """
    if mode is MatchMode.REGEX:
        return re.compile(term)
    escaped = re.escape(term)
    if mode is MatchMode.LITERAL:
        return re.compile(escaped)
    if mode is MatchMode.CASE_INSENSITIVE:
        return re.compile(escaped, re.IGNORECASE)
    if mode is MatchMode.WHOLE_WORD:
        return re.compile(rf"\b{escaped}\b", re.IGNORECASE)
    raise ValueError(f"unknown match mode: {mode!r}")


def find_matches(text: str, term: str, mode: MatchMode) -> list[str]:
    """Every matched substring, in order. Used to preview what an edit will touch."""
    return compile_pattern(term, mode).findall(text)


def count_matches(text: str, term: str, mode: MatchMode) -> int:
    return len(compile_pattern(term, mode).findall(text))
