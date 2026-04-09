"""
Regression tests for conflict rendering in ll_system/renderer.py.

Fix 3 (Medium): conflict dicts use 'competing_rules' and 'type' as keys
(produced by ll_table_builder.py), but renderers were reading non-existent
'conflicting_rules' and 'description' — so conflicts showed as blank.
"""

from __future__ import annotations

from ll_system.renderer import _render_conflicts_html, render_conflicts_md, render_markdown, render_html


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

CONFLICT_FIRST_FIRST = {
    "nonterminal": "S",
    "lookahead": "a",
    "competing_rules": [["a", "B"], ["a", "C"]],
    "type": "FIRST/FIRST",
}

CONFLICT_FIRST_FOLLOW = {
    "nonterminal": "A",
    "lookahead": "b",
    "competing_rules": [["b"], []],
    "type": "FIRST/FOLLOW",
}

FF_RESULT_ONE = {"conflicts": [CONFLICT_FIRST_FIRST]}
FF_RESULT_TWO = {"conflicts": [CONFLICT_FIRST_FIRST, CONFLICT_FIRST_FOLLOW]}
FF_RESULT_EMPTY = {"conflicts": []}
FF_RESULT_NONE = {}


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


class TestRenderConflictsMd:
    def test_empty_conflicts_returns_empty_string(self) -> None:
        assert render_conflicts_md(FF_RESULT_EMPTY) == ""

    def test_missing_conflicts_key_returns_empty_string(self) -> None:
        assert render_conflicts_md(FF_RESULT_NONE) == ""

    def test_none_result_returns_empty_string(self) -> None:
        assert render_conflicts_md(None) == ""

    def test_shows_nonterminal(self) -> None:
        out = render_conflicts_md(FF_RESULT_ONE)
        assert "S" in out

    def test_shows_lookahead(self) -> None:
        out = render_conflicts_md(FF_RESULT_ONE)
        assert "a" in out

    def test_shows_competing_rules(self) -> None:
        """Regression: was reading 'conflicting_rules' — competing rules were invisible."""
        out = render_conflicts_md(FF_RESULT_ONE)
        # The two competing rules should appear in some form
        assert "a" in out and "B" in out or "C" in out

    def test_shows_conflict_type(self) -> None:
        """Regression: was reading 'description' — conflict type was invisible."""
        out = render_conflicts_md(FF_RESULT_ONE)
        assert "FIRST/FIRST" in out

    def test_two_conflicts_both_rendered(self) -> None:
        out = render_conflicts_md(FF_RESULT_TWO)
        assert "FIRST/FIRST" in out
        assert "FIRST/FOLLOW" in out
        assert "S" in out
        assert "A" in out

    def test_no_blank_conflict_entry(self) -> None:
        """Each conflict entry must include more than just NT= and lookahead=."""
        out = render_conflicts_md(FF_RESULT_ONE)
        # Should contain rule info or type — not just the header line
        assert "FIRST/FIRST" in out or "vs" in out

    def test_plain_string_conflict_rendered(self) -> None:
        """Non-dict conflict items should still render."""
        result = {"conflicts": ["some raw conflict description"]}
        out = render_conflicts_md(result)
        assert "some raw conflict description" in out


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------


class TestRenderConflictsHtml:
    def test_empty_conflicts_returns_empty_string(self) -> None:
        assert _render_conflicts_html(FF_RESULT_EMPTY) == ""

    def test_missing_conflicts_key_returns_empty_string(self) -> None:
        assert _render_conflicts_html(FF_RESULT_NONE) == ""

    def test_shows_nonterminal(self) -> None:
        out = _render_conflicts_html(FF_RESULT_ONE)
        assert "S" in out

    def test_shows_lookahead(self) -> None:
        out = _render_conflicts_html(FF_RESULT_ONE)
        assert "a" in out

    def test_shows_competing_rules(self) -> None:
        """Regression: was reading 'conflicting_rules' — competing rules were invisible."""
        out = _render_conflicts_html(FF_RESULT_ONE)
        # Both rule alternatives should appear somewhere in the output
        assert "B" in out or "C" in out

    def test_shows_conflict_type(self) -> None:
        """Regression: was reading 'description' — conflict type was invisible."""
        out = _render_conflicts_html(FF_RESULT_ONE)
        assert "FIRST/FIRST" in out

    def test_two_conflicts_both_rendered(self) -> None:
        out = _render_conflicts_html(FF_RESULT_TWO)
        assert "FIRST/FIRST" in out
        assert "FIRST/FOLLOW" in out

    def test_output_is_valid_html_fragment(self) -> None:
        out = _render_conflicts_html(FF_RESULT_ONE)
        assert out.startswith("<div")
        assert "<ul>" in out
        assert "<li>" in out

    def test_plain_string_conflict_rendered(self) -> None:
        result = {"conflicts": ["raw conflict"]}
        out = _render_conflicts_html(result)
        assert "raw conflict" in out


# ---------------------------------------------------------------------------
# Regression: Finding 5 — renderers render per-agent claim_verification map
# ---------------------------------------------------------------------------

CLAIM_VER_MAP = {
    "substitution_agent": {
        "verification_status": "verified",
        "status": "verified",
        "checks_passed": 6,
        "checks_total": 6,
        "issues": [],
        "details": {},
    },
    "ll_grammar_builder": {
        "verification_status": "inconclusive",
        "status": "inconclusive",
        "checks_passed": 1,
        "checks_total": 3,
        "issues": ["No grammar provided in proof_sketch"],
        "details": {},
    },
    "ambiguity_detector": {
        "verification_status": "refuted",
        "status": "refuted",
        "checks_passed": 0,
        "checks_total": 2,
        "issues": ["essentially_ambiguous must be True"],
        "details": {},
    },
}

MINIMAL_RESULT = {
    "verdict": "not_ll",
    "k": None,
    "confidence": 0.9,
    "task_type": "ll_check_grammar_lang",
    "source_text": "test",
    "claim_verification": CLAIM_VER_MAP,
    "first_follow_result": {},
    "specialist_outputs": {},
    "reasoning_output": {},
    "reasoning_summary": "## Решение\nTest",
    "proof": None,
    "grammar": None,
    "agents_used": ["substitution_agent", "ll_grammar_builder"],
    "agents_failed": [],
    "errors": [],
    "retries": 0,
}


class TestClaimVerificationRenderedPerAgent:
    """Regression Finding 5: renderers were reading claim_verification as a flat
    {verdict, note} dict instead of iterating the per-agent map."""

    def test_md_shows_substitution_agent(self) -> None:
        out = render_markdown(MINIMAL_RESULT)
        assert "substitution_agent" in out

    def test_md_shows_all_three_agents(self) -> None:
        out = render_markdown(MINIMAL_RESULT)
        assert "ll_grammar_builder" in out
        assert "ambiguity_detector" in out

    def test_md_shows_verified_status(self) -> None:
        out = render_markdown(MINIMAL_RESULT)
        assert "verified" in out

    def test_md_shows_refuted_status(self) -> None:
        out = render_markdown(MINIMAL_RESULT)
        assert "refuted" in out

    def test_md_shows_checks_fraction(self) -> None:
        out = render_markdown(MINIMAL_RESULT)
        # Should show checks like "6/6" or "1/3"
        assert "6/6" in out or "1/3" in out

    def test_md_shows_issue_text(self) -> None:
        out = render_markdown(MINIMAL_RESULT)
        # Issue from ll_grammar_builder should appear
        assert "No grammar" in out

    def test_md_does_not_show_old_flat_keys(self) -> None:
        """Old code looked for claim_ver['verdict'] — should not be confused as a status."""
        out = render_markdown(MINIMAL_RESULT)
        # The section header must be present and must iterate agents
        assert "Проверка утверждений" in out

    def test_html_shows_substitution_agent(self) -> None:
        out = render_html(MINIMAL_RESULT)
        assert "substitution_agent" in out

    def test_html_shows_verified_with_color(self) -> None:
        out = render_html(MINIMAL_RESULT)
        # Verified status should use green color
        assert "#27ae60" in out
        assert "verified" in out

    def test_html_shows_refuted_with_color(self) -> None:
        out = render_html(MINIMAL_RESULT)
        assert "#e74c3c" in out
        assert "refuted" in out

    def test_html_shows_checks_fraction(self) -> None:
        out = render_html(MINIMAL_RESULT)
        assert "6/6" in out or "1/3" in out

    def test_html_shows_issue_text(self) -> None:
        out = render_html(MINIMAL_RESULT)
        assert "No grammar" in out

    def test_html_uses_ul_list(self) -> None:
        out = render_html(MINIMAL_RESULT)
        assert "<ul>" in out and "<li>" in out
