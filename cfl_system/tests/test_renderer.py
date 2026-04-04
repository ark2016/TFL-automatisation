"""Tests for cfl_renderer module."""

import os
import tempfile

import pytest

from cfl_system.lib.cfl_renderer import (
    render_grammar,
    render_html,
    render_markdown,
    render_pda_table,
    render_to_file,
)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

FULL_RESULT = {
    "task": "classify_and_prove_cfl",
    "source_text": "{w₁w₂w₁w₃ | w₂ ∈ {b,c}*, w₁ ∈ {a,b}*, w₃ ∈ {a,c}*}",
    "verdict": "non_cfl",
    "confidence": 0.95,
    "proof": {
        "method": "closure_reduction + pumping",
        "summary": "Intersected L with R = a*(b|c)*a*(a|c)* (regular).",
        "details": {
            "word_chosen": "a^p b a^p c",
            "cases": [
                {
                    "case": "v and x in first a-block",
                    "pumped_word": "a^{p+|vx|} b a^p c",
                    "why_not_in_L": "Blocks differ in length",
                },
            ],
        },
    },
    "grammar": None,
    "pda": None,
    "oracle_test": {"status": "not_applicable"},
    "agents_used": ["pumping_cfl", "closure_reduction"],
    "retries": 0,
}

CFL_RESULT = {
    "task": "classify_and_prove_cfl",
    "source_text": "{a^n b^n | n >= 0}",
    "verdict": "cfl",
    "confidence": 0.99,
    "proof": {
        "method": "cfg_builder",
        "summary": "Constructed grammar S → aSb | ε.",
        "details": {},
    },
    "grammar": {
        "terminals": ["a", "b"],
        "nonterminals": ["S"],
        "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "b"]},
            {"lhs": "S", "rhs": []},
        ],
    },
    "pda": {
        "states": ["q0", "q1", "q_accept"],
        "transitions": [
            {"from": "q0", "input": "a", "stack_top": "Z", "to": "q0", "push": ["A", "Z"]},
            {"from": "q0", "input": "b", "stack_top": "A", "to": "q1", "push": []},
            {"from": "q1", "input": None, "stack_top": "Z", "to": "q_accept", "push": []},
        ],
    },
    "oracle_test": {
        "status": "pass",
        "positive_checked": 20,
        "negative_checked": 20,
        "counterexamples": [],
    },
    "agents_used": ["cfg_builder", "pda_builder"],
    "retries": 0,
}


# ---------------------------------------------------------------------------
# Markdown tests
# ---------------------------------------------------------------------------

class TestRenderMarkdown:
    def test_none_result(self):
        md = render_markdown(None)
        assert "error" in md.lower() or "отсутствует" in md.lower()

    def test_verdict_non_cfl(self):
        md = render_markdown(FULL_RESULT)
        assert "не КС" in md
        assert "❌" in md

    def test_verdict_cfl(self):
        md = render_markdown(CFL_RESULT)
        assert "КС" in md
        assert "✅" in md

    def test_confidence(self):
        md = render_markdown(FULL_RESULT)
        assert "95%" in md

    def test_proof_section(self):
        md = render_markdown(FULL_RESULT)
        assert "closure_reduction" in md
        assert "pumping" in md

    def test_pumping_cases_table(self):
        md = render_markdown(FULL_RESULT)
        assert "Случай" in md
        assert "v and x in first a-block" in md

    def test_grammar_rendered(self):
        md = render_markdown(CFL_RESULT)
        assert "Грамматика" in md
        assert "S" in md

    def test_pda_rendered(self):
        md = render_markdown(CFL_RESULT)
        assert "МП-автомат" in md
        assert "q0" in md

    def test_oracle_test_pass(self):
        md = render_markdown(CFL_RESULT)
        assert "Oracle тест" in md
        assert "pass" in md

    def test_oracle_test_not_applicable_hidden(self):
        md = render_markdown(FULL_RESULT)
        # Should not render oracle section if not_applicable
        assert "Oracle тест" not in md

    def test_agents_listed(self):
        md = render_markdown(FULL_RESULT)
        assert "pumping_cfl" in md
        assert "closure_reduction" in md

    def test_source_text(self):
        md = render_markdown(FULL_RESULT)
        assert "w₁w₂w₁w₃" in md

    def test_no_proof(self):
        result = {"verdict": "unknown", "source_text": "test"}
        md = render_markdown(result)
        assert "неизвестно" in md


# ---------------------------------------------------------------------------
# HTML tests
# ---------------------------------------------------------------------------

class TestRenderHtml:
    def test_none_result(self):
        html = render_html(None)
        assert "Результат" in html

    def test_basic_structure(self):
        html = render_html(FULL_RESULT)
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html
        assert "verdict-non-cfl" in html

    def test_cfl_verdict_class(self):
        html = render_html(CFL_RESULT)
        assert "verdict-cfl" in html

    def test_grammar_in_html(self):
        html = render_html(CFL_RESULT)
        assert "<table>" in html or "Грамматика" in html

    def test_pda_in_html(self):
        html = render_html(CFL_RESULT)
        assert "МП-автомат" in html
        assert "q0" in html


# ---------------------------------------------------------------------------
# Component renderers
# ---------------------------------------------------------------------------

class TestRenderGrammar:
    def test_none(self):
        assert render_grammar(None) == ""

    def test_empty_rules(self):
        assert render_grammar({"rules": []}) == ""

    def test_anbn(self):
        g = {
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S", "b"]},
                {"lhs": "S", "rhs": []},
            ],
        }
        result = render_grammar(g)
        assert "S" in result
        assert "ε" in result

    def test_multiple_nonterminals(self):
        g = {
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "A"]},
                {"lhs": "A", "rhs": ["b"]},
            ],
        }
        result = render_grammar(g)
        assert "A" in result
        assert "S" in result


class TestRenderPdaTable:
    def test_none(self):
        assert render_pda_table(None) == ""

    def test_empty_transitions(self):
        assert render_pda_table({"transitions": []}) == ""

    def test_basic(self):
        pda = {
            "transitions": [
                {"from": "q0", "input": "a", "stack_top": "Z", "to": "q0", "push": ["A", "Z"]},
            ]
        }
        result = render_pda_table(pda)
        assert "q0" in result
        assert "A Z" in result


# ---------------------------------------------------------------------------
# File output
# ---------------------------------------------------------------------------

class TestRenderToFile:
    def test_md_file(self, tmp_path):
        path = str(tmp_path / "out.md")
        render_to_file(FULL_RESULT, path, fmt="md")
        content = open(path, encoding="utf-8").read()
        assert "не КС" in content

    def test_html_file(self, tmp_path):
        path = str(tmp_path / "out.html")
        render_to_file(CFL_RESULT, path, fmt="html")
        content = open(path, encoding="utf-8").read()
        assert "<!DOCTYPE html>" in content
