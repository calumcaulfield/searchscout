"""The property the whole tool rests on: an edit changes text and nothing else.

A bulk replacement over merchandiser-authored HTML is only safe if it cannot
touch a tag name, an attribute, a URL or a script. That is asserted here against
awkward inputs rather than assumed.
"""

from __future__ import annotations

import pytest

from searchscout.html_edit import extract_text, replace_in_text, tag_signature
from searchscout.matching import MatchMode

AWKWARD = [
    '<div class="cotton"><p>Made from cotton.</p></div>',
    '<a href="https://example.com/cotton-guide">Our cotton guide</a>',
    '<table data-material="cotton"><tr><td>cotton</td></tr></table>',
    '<p style="font-family:cotton">cotton &amp; linen</p>',
    "<p>cotton</p><!-- cotton comment --><script>var x = 'cotton';</script>",
    '<img src="cotton.jpg" alt="cotton basket"/><p>cotton</p>',
    "<p>Cotton, COTTON, cotton and cottonseed</p>",
]


class TestStructureIsPreserved:
    @pytest.mark.parametrize("html", AWKWARD)
    def test_tag_structure_is_identical_after_an_edit(self, html: str) -> None:
        before = tag_signature(html)
        result = replace_in_text(html, "cotton", "hemp", MatchMode.CASE_INSENSITIVE)
        assert tag_signature(result.html) == before

    @pytest.mark.parametrize("html", AWKWARD)
    def test_attribute_values_are_never_rewritten(self, html: str) -> None:
        result = replace_in_text(html, "cotton", "hemp", MatchMode.CASE_INSENSITIVE)
        for _tag, attributes in tag_signature(result.html):
            for _key, value in attributes:
                assert "hemp" not in value

    def test_a_url_inside_an_href_survives(self) -> None:
        html = '<a href="https://example.com/cotton-guide">See the cotton guide</a>'
        result = replace_in_text(html, "cotton", "hemp", MatchMode.CASE_INSENSITIVE)
        assert "https://example.com/cotton-guide" in result.html
        assert "See the hemp guide" in result.html

    def test_script_contents_are_left_alone(self) -> None:
        html = "<p>cotton</p><script>var material = 'cotton';</script>"
        result = replace_in_text(html, "cotton", "hemp", MatchMode.CASE_INSENSITIVE)
        assert "var material = 'cotton';" in result.html
        assert "<p>hemp</p>" in result.html

    def test_comments_are_left_alone(self) -> None:
        html = "<!-- cotton note --><p>cotton</p>"
        result = replace_in_text(html, "cotton", "hemp", MatchMode.CASE_INSENSITIVE)
        assert "cotton note" in result.html
        assert "<p>hemp</p>" in result.html


class TestReplacementCounting:
    def test_counts_every_replacement_not_every_product(self) -> None:
        html = "<p>cotton cotton</p><p>cotton</p>"
        result = replace_in_text(html, "cotton", "hemp", MatchMode.LITERAL)
        assert result.replacements == 3

    def test_reports_nothing_when_there_is_no_match(self) -> None:
        result = replace_in_text("<p>linen</p>", "cotton", "hemp", MatchMode.LITERAL)
        assert not result.changed
        assert result.changed_fragments == []

    def test_fragments_show_before_and_after(self) -> None:
        result = replace_in_text("<p>Made from cotton.</p>", "cotton", "hemp", MatchMode.LITERAL)
        assert result.changed_fragments == [("Made from cotton.", "Made from hemp.")]


class TestTheOriginalCaseMismatchDefect:
    """The bug this design exists to prevent.

    The original found products case-insensitively and then replaced
    case-sensitively, so a product matched on "Cotton" while searching "cotton"
    was reported as updated and left unchanged. One mode drives both halves now.
    """

    def test_case_insensitive_search_also_replaces_that_casing(self) -> None:
        html = "<p>Cotton and COTTON and cotton</p>"
        result = replace_in_text(html, "cotton", "hemp", MatchMode.CASE_INSENSITIVE)
        assert result.replacements == 3
        assert "Cotton" not in result.html
        assert "COTTON" not in result.html

    def test_literal_mode_is_genuinely_case_sensitive(self) -> None:
        html = "<p>Cotton and cotton</p>"
        result = replace_in_text(html, "cotton", "hemp", MatchMode.LITERAL)
        assert result.replacements == 1
        assert "Cotton and hemp" in result.html

    def test_whole_word_does_not_mangle_a_longer_word(self) -> None:
        html = "<p>cotton and cottonseed</p>"
        result = replace_in_text(html, "cotton", "hemp", MatchMode.WHOLE_WORD)
        assert result.replacements == 1
        assert "cottonseed" in result.html


class TestTextExtraction:
    def test_strips_markup_for_searching(self) -> None:
        assert "cotton" in extract_text("<div><p>cotton</p></div>").lower()

    def test_decodes_entities(self) -> None:
        assert "&" in extract_text("<p>cotton &amp; linen</p>")
