"""HTML-preserving text replacement.

This is the part of the original worth keeping. Product descriptions are
merchandiser-authored HTML — tables, inline styles, links, entities — and a
naive string replacement over the raw markup will happily rewrite a tag name, a
class attribute or a URL and corrupt the page.

The rule is narrow and testable: replace inside text nodes only, never inside
tags, attributes, comments, `<script>` or `<style>`. `tests/test_html_edit.py`
asserts that property against generated inputs rather than trusting the
implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, NavigableString

from searchscout.matching import MatchMode, compile_pattern

#: Text inside these elements is code, not copy. A replacement there changes
#: behaviour rather than wording.
NON_CONTENT_TAGS = frozenset({"script", "style", "template"})


@dataclass
class EditResult:
    html: str
    replacements: int
    #: `(before, after)` for each text node that changed — the material a
    #: reviewer reads in a dry run.
    changed_fragments: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.replacements > 0


def _editable(node: NavigableString) -> bool:
    if isinstance(node, Comment):
        return False
    parent = node.parent
    while parent is not None:
        if getattr(parent, "name", None) in NON_CONTENT_TAGS:
            return False
        parent = parent.parent
    return True


def replace_in_text(
    html: str,
    term: str,
    replacement: str,
    mode: MatchMode = MatchMode.LITERAL,
) -> EditResult:
    """Replace `term` with `replacement` in the document's text nodes only.

    Returns the new HTML plus what changed. The document is re-serialised from
    the parse tree, so malformed input is normalised — which is why the tests
    compare tag structure before and after rather than comparing strings.
    """
    soup = BeautifulSoup(html, "html.parser")
    pattern = compile_pattern(term, mode)

    total = 0
    fragments: list[tuple[str, str]] = []

    for node in list(soup.find_all(string=True)):
        if not _editable(node):
            continue
        original = str(node)
        updated, n = pattern.subn(replacement, original)
        if n:
            node.replace_with(NavigableString(updated))
            total += n
            fragments.append((original.strip(), updated.strip()))

    return EditResult(html=str(soup), replacements=total, changed_fragments=fragments)


def extract_text(html: str, separator: str = "\n") -> str:
    """Plain text for searching and for showing a match in context."""
    return BeautifulSoup(html, "html.parser").get_text(separator=separator)


def tag_signature(html: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """A structural fingerprint: every tag and its attributes, in document order.

    Used by the tests to assert that an edit changed text and nothing else. If
    this differs before and after, the edit corrupted the markup.
    """
    soup = BeautifulSoup(html, "html.parser")
    signature: list[tuple[str, list[tuple[str, str]]]] = []
    for tag in soup.find_all(True):
        attributes = sorted(
            (key, " ".join(value) if isinstance(value, list) else str(value))
            for key, value in tag.attrs.items()
        )
        signature.append((tag.name, attributes))
    return signature
