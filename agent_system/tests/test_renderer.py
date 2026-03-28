"""Tests for the renderer module."""

import sys
import os
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.renderer import render_markdown, render_html, render_dfa_table, render_to_file


def _minimal_result(**overrides):
    """Build a minimal pipeline result dict."""
    base = {
        "module": "pipeline",
        "status": "regular",
        "evidence": {},
        "confidence": 0.9,
        "errors": [],
    }
    base.update(overrides)
    return base


class TestRenderMarkdownBasic(unittest.TestCase):

    def test_render_markdown_basic(self):
        """Minimal result with hypothesis only renders key sections."""
        result = _minimal_result(evidence={
            "hypothesis": {
                "verdict": "regular",
                "confidence": 0.85,
                "agents": ["re_builder", "dfa_builder"],
            },
        })
        md = render_markdown(result)
        self.assertIn("Результат анализа", md)
        self.assertIn("Гипотеза", md)
        self.assertIn("regular", md)
        self.assertIn("0.85", md)
        self.assertIn("re_builder", md)


class TestRenderMarkdownPumping(unittest.TestCase):

    def test_render_markdown_with_pumping(self):
        """Pumping proof steps appear in markdown output."""
        result = _minimal_result(
            status="non_regular",
            evidence={
                "pumping": {
                    "word_family": "a^n b^n",
                    "steps": [
                        "Choose w = a^p b^p",
                        "For any split w = xyz with |xy| <= p",
                        "Pumping y gives a^(p+k) b^p not in L",
                    ],
                },
            },
        )
        md = render_markdown(result)
        self.assertIn("Лемма о накачке", md)
        self.assertIn("a^n b^n", md)
        self.assertIn("Choose w = a^p b^p", md)
        self.assertIn("Pumping y gives", md)


class TestRenderMarkdownDFA(unittest.TestCase):

    def test_render_markdown_with_dfa(self):
        """DFA evidence renders a transition table."""
        dfa = {
            "states": ["q0", "q1"],
            "start": "q0",
            "accept": ["q1"],
            "alphabet": ["a", "b"],
            "transitions": {
                "q0": {"a": "q1", "b": "q0"},
                "q1": {"a": "q1", "b": "q0"},
            },
        }
        result = _minimal_result(evidence={
            "dfa_builder": {"dfa": dfa, "explanation": "Two-state DFA"},
        })
        md = render_markdown(result)
        self.assertIn("ДКА", md)
        self.assertIn("q0", md)
        self.assertIn("q1", md)
        # transition table should be present
        self.assertIn("→q0", md)
        self.assertIn("✓", md)


class TestRenderHTMLBasic(unittest.TestCase):

    def test_render_html_basic(self):
        """HTML output contains expected structure."""
        result = _minimal_result(evidence={
            "hypothesis": {
                "verdict": "regular",
                "confidence": 0.9,
                "agents": [],
            },
        })
        out = render_html(result)
        self.assertIn("<!DOCTYPE html>", out)
        self.assertIn("s-head", out)
        self.assertIn("Segoe UI", out)
        self.assertIn("s-badge", out)


class TestRenderHTMLVerdictColoring(unittest.TestCase):

    def test_render_html_verdict_regular(self):
        """Regular verdict gets the s-reg badge class."""
        result = _minimal_result(evidence={
            "hypothesis": {"hypothesis": "regular", "confidence": 0.9, "suggested_agents": []},
        })
        out = render_html(result)
        self.assertIn("s-reg", out)

    def test_render_html_verdict_non_regular(self):
        """Non-regular verdict gets the s-nonreg badge class."""
        result = _minimal_result(
            status="non_regular",
            evidence={
                "hypothesis": {"hypothesis": "non_regular", "confidence": 0.8, "suggested_agents": []},
            },
        )
        out = render_html(result)
        self.assertIn("s-nonreg", out)


class TestRenderDFATable(unittest.TestCase):

    def test_render_dfa_table(self):
        """Simple 2-state DFA table has arrow and checkmark."""
        dfa = {
            "states": ["q0", "q1"],
            "start": "q0",
            "accept": ["q1"],
            "alphabet": ["a", "b"],
            "transitions": {
                "q0": {"a": "q1", "b": "q0"},
                "q1": {"a": "q1", "b": "q0"},
            },
        }
        table = render_dfa_table(dfa)
        self.assertIn("→q0", table)
        self.assertIn("✓", table)
        self.assertIn("q0", table)
        self.assertIn("q1", table)
        # Should have header row
        self.assertIn("State", table)
        self.assertIn("Accept", table)


class TestRenderMarkdownOracleTest(unittest.TestCase):

    def test_render_markdown_with_oracle_test(self):
        """Oracle test results appear in markdown."""
        result = _minimal_result(evidence={
            "oracle_test": {
                "status": "pass",
                "tested": 100,
            },
        })
        md = render_markdown(result)
        self.assertIn("Oracle Test", md)
        self.assertIn("pass", md)
        self.assertIn("100", md)


class TestRenderEmptyEvidence(unittest.TestCase):

    def test_render_empty_evidence(self):
        """Empty evidence dict produces minimal output without crashing."""
        result = _minimal_result(evidence={})
        md = render_markdown(result)
        self.assertIn("Результат анализа", md)
        self.assertIn("Итог", md)
        # Should not crash on HTML either
        out = render_html(result)
        self.assertIn("<!DOCTYPE html>", out)


class TestRenderWithErrors(unittest.TestCase):

    def test_render_with_errors(self):
        """Errors list is rendered in both markdown and HTML."""
        result = _minimal_result(errors=[
            "Timeout in DFA builder",
            "Oracle test skipped",
        ])
        md = render_markdown(result)
        self.assertIn("Ошибки", md)
        self.assertIn("Timeout in DFA builder", md)
        self.assertIn("Oracle test skipped", md)

        out = render_html(result)
        self.assertIn("Timeout in DFA builder", out)
        self.assertIn("Ошибки", out)


class TestRenderToFile(unittest.TestCase):

    def test_render_to_file_md(self):
        """render_to_file writes markdown."""
        result = _minimal_result()
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            path = f.name
        try:
            render_to_file(result, path, fmt="md")
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("Результат анализа", content)
        finally:
            os.unlink(path)

    def test_render_to_file_html(self):
        """render_to_file writes HTML."""
        result = _minimal_result()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            render_to_file(result, path, fmt="html")
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("<!DOCTYPE html>", content)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
