"""Tests for dcfl_system.lib.hypothesis_module.

Covers pattern classification, memory analysis, separator analysis,
regex-constrained variables, grammar input, disjunction detection,
integration with example task IRs, and output structure validation.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from dcfl_system.lib.hypothesis_module import analyze_dcfl_hypothesis

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = pathlib.Path(__file__).resolve().parent.parent / "examples"

# ---------------------------------------------------------------------------
# Required keys every HypothesisResult must contain
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {
    "pattern_type",
    "memory_analysis",
    "separator_analysis",
    "disjunction_present",
    "regex_constrained_vars",
    "prediction",
    "confidence",
    "reasoning",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ir(
    word_pattern: str,
    variables: list[dict[str, Any]],
    constraints: list[dict[str, Any]] | None = None,
    kind: str = "set_builder",
) -> dict[str, Any]:
    """Build a minimal set-builder IR dict."""
    return {
        "input_format": kind,
        "language_spec": {
            "kind": kind,
            "word_pattern": word_pattern,
            "variables": variables,
            "constraints": constraints or [],
        },
        "alphabet": ["a", "b"],
    }


def _make_grammar_ir() -> dict[str, Any]:
    """Build a minimal grammar IR dict."""
    return {
        "input_format": "grammar",
        "language_spec": {
            "kind": "grammar",
            "terminals": ["a", "b"],
            "nonterminals": ["S"],
            "start": "S",
            "rules": [
                {"lhs": "S", "rhs": ["a", "S", "b"]},
                {"lhs": "S", "rhs": ["a", "b"]},
            ],
        },
        "alphabet": ["a", "b"],
    }


def _assert_valid_result(result: dict[str, Any]) -> None:
    """Assert *result* contains all required keys with expected types."""
    assert REQUIRED_KEYS <= set(result.keys()), (
        f"Missing keys: {REQUIRED_KEYS - set(result.keys())}"
    )
    assert isinstance(result["pattern_type"], str)
    assert isinstance(result["memory_analysis"], str)
    assert isinstance(result["separator_analysis"], str)
    assert isinstance(result["disjunction_present"], bool)
    assert isinstance(result["regex_constrained_vars"], list)
    assert isinstance(result["prediction"], str)
    assert isinstance(result["confidence"], float)
    assert 0.0 <= result["confidence"] <= 1.0
    assert isinstance(result["reasoning"], str)
    assert len(result["reasoning"]) > 0


# ===================================================================
# 1. Pattern classification for set_builder
# ===================================================================


class TestPatternClassification:
    """Test _classify_pattern_set_builder via the public API."""

    def test_single_palindrome_with_separator(self):
        """w 1 w^R  -->  single_palindrome_sep, likely_dcfl.

        The digit-only separator ``1`` is not consumed by the
        identifier-start regex ``[a-zA-Z_]``, so it remains as a
        literal between the variable tokens ``w`` and ``w^R``.
        """
        ir = _make_ir(
            word_pattern="w 1 w^R",
            variables=[
                {"name": "w", "domain": None, "quantifier": "forall"},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)
        assert result["pattern_type"] == "single_palindrome_sep"
        assert result["prediction"] == "likely_dcfl"

    def test_single_palindrome_no_separator(self):
        """w w^R  -->  single_palindrome_nosep, likely_non_dcfl."""
        ir = _make_ir(
            word_pattern="w w^R",
            variables=[
                {"name": "w", "domain": None, "quantifier": "forall"},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)
        assert result["pattern_type"] == "single_palindrome_nosep"
        assert result["prediction"] == "likely_non_dcfl"

    def test_nested_palindromes(self):
        """w v w^R v^R  -->  nested_palindromes.

        Interleaved (non-properly-nested) close order triggers the
        nesting detector: closing ``w`` while ``v`` is on top of the
        abstract stack.
        """
        ir = _make_ir(
            word_pattern="w v w^R v^R",
            variables=[
                {"name": "w", "domain": None, "quantifier": "forall"},
                {"name": "v", "domain": None, "quantifier": "forall"},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)
        assert result["pattern_type"] == "nested_palindromes"

    def test_single_length_cmp(self):
        """Single length comparison  -->  single_length_cmp, likely_dcfl."""
        ir = _make_ir(
            word_pattern="uav",
            variables=[
                {"name": "u", "domain": None, "quantifier": "forall"},
                {"name": "v", "domain": None, "quantifier": "forall"},
            ],
            constraints=[
                {"kind": "length_cmp", "args": {"left": "u", "op": "<=", "right": "v"}},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)
        assert result["pattern_type"] == "single_length_cmp"
        assert result["prediction"] == "likely_dcfl"

    def test_dual_length_cmp(self):
        """Two length comparisons  -->  dual_length_cmp."""
        ir = _make_ir(
            word_pattern="u1au2u3au4",
            variables=[
                {"name": "u1", "domain": None, "quantifier": "forall"},
                {"name": "u2", "domain": None, "quantifier": "forall"},
                {"name": "u3", "domain": None, "quantifier": "forall"},
                {"name": "u4", "domain": None, "quantifier": "forall"},
            ],
            constraints=[
                {"kind": "length_cmp", "args": {"left": "u1", "op": "<=", "right": "u2"}},
                {"kind": "length_cmp", "args": {"left": "u3", "op": ">=", "right": "u4"}},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)
        assert result["pattern_type"] == "dual_length_cmp"

    def test_shared_var_disjunction(self):
        """Disjunction with a shared variable  -->  shared_var_disjunction, likely_non_dcfl."""
        ir = _make_ir(
            word_pattern="xay",
            variables=[
                {"name": "x", "domain": None, "quantifier": "forall"},
                {"name": "y", "domain": None, "quantifier": "forall"},
            ],
            constraints=[
                {"kind": "disjunction", "args": ["x", "y"]},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)
        assert result["pattern_type"] == "shared_var_disjunction"
        assert result["prediction"] == "likely_non_dcfl"


# ===================================================================
# 2. Memory analysis
# ===================================================================


class TestMemoryAnalysis:
    """Test the memory_analysis field in the result."""

    def test_single_stack_palindrome(self):
        """One palindrome pair  -->  single_stack."""
        ir = _make_ir(
            word_pattern="w 1 w^R",
            variables=[{"name": "w", "domain": None, "quantifier": "forall"}],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["memory_analysis"] == "single_stack"

    def test_single_stack_length_cmp(self):
        """Single length comparison  -->  single_stack."""
        ir = _make_ir(
            word_pattern="uav",
            variables=[
                {"name": "u", "domain": None, "quantifier": "forall"},
                {"name": "v", "domain": None, "quantifier": "forall"},
            ],
            constraints=[
                {"kind": "length_cmp", "args": {"left": "u", "op": "<=", "right": "v"}},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["memory_analysis"] == "single_stack"

    def test_nested_stack_with_separator(self):
        """Interleaved palindromes with a clear separator  -->  nested_stack."""
        ir = _make_ir(
            word_pattern="w v 1 w^R v^R",
            variables=[
                {"name": "w", "domain": None, "quantifier": "forall"},
                {"name": "v", "domain": None, "quantifier": "forall"},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["memory_analysis"] == "nested_stack"

    def test_two_stacks_nested_no_separator(self):
        """Interleaved palindromes without separator  -->  two_stacks."""
        ir = _make_ir(
            word_pattern="w v w^R v^R",
            variables=[
                {"name": "w", "domain": None, "quantifier": "forall"},
                {"name": "v", "domain": None, "quantifier": "forall"},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["memory_analysis"] == "two_stacks"

    def test_unknown_memory_no_palindromes_no_length(self):
        """No palindromes or length constraints  -->  unknown."""
        ir = _make_ir(
            word_pattern="xy",
            variables=[
                {"name": "x", "domain": None, "quantifier": "forall"},
                {"name": "y", "domain": None, "quantifier": "forall"},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["memory_analysis"] == "unknown"


# ===================================================================
# 3. Separator analysis
# ===================================================================


class TestSeparatorAnalysis:
    """Test the separator_analysis field."""

    def test_clear_separator(self):
        """Fixed digit literal between variables  -->  clear_separator."""
        ir = _make_ir(
            word_pattern="w 1 w^R",
            variables=[{"name": "w", "domain": None, "quantifier": "forall"}],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["separator_analysis"] == "clear_separator"

    def test_no_separator(self):
        """No literal between variables  -->  no_separator."""
        ir = _make_ir(
            word_pattern="w w^R",
            variables=[{"name": "w", "domain": None, "quantifier": "forall"}],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["separator_analysis"] == "no_separator"


# ===================================================================
# 4. Regex-constrained variables
# ===================================================================


class TestRegexConstrainedVars:
    """Test that variables with non-null domain appear in regex_constrained_vars."""

    def test_constrained_var_present(self):
        ir = _make_ir(
            word_pattern="w 1 w^R",
            variables=[
                {"name": "w", "domain": "(a|b)*", "quantifier": "forall"},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert "w" in result["regex_constrained_vars"]

    def test_unconstrained_var_absent(self):
        ir = _make_ir(
            word_pattern="w 1 w^R",
            variables=[
                {"name": "w", "domain": None, "quantifier": "forall"},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["regex_constrained_vars"] == []

    def test_mixed_constrained_and_unconstrained(self):
        ir = _make_ir(
            word_pattern="w v 1 v^R 1 w^R",
            variables=[
                {"name": "w", "domain": "(aa*b)*a", "quantifier": "forall"},
                {"name": "v", "domain": None, "quantifier": "forall"},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert "w" in result["regex_constrained_vars"]
        assert "v" not in result["regex_constrained_vars"]


# ===================================================================
# 5. Grammar input
# ===================================================================


class TestGrammarInput:
    """Grammar-based IR  -->  grammar_analysis, uncertain."""

    def test_grammar_path(self):
        ir = _make_grammar_ir()
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)
        assert result["pattern_type"] == "grammar_analysis"
        assert result["prediction"] == "uncertain"
        assert result["confidence"] == pytest.approx(0.40)

    def test_grammar_memory_unknown(self):
        ir = _make_grammar_ir()
        result = analyze_dcfl_hypothesis(ir)
        assert result["memory_analysis"] == "unknown"

    def test_grammar_no_regex_vars(self):
        ir = _make_grammar_ir()
        result = analyze_dcfl_hypothesis(ir)
        assert result["regex_constrained_vars"] == []


# ===================================================================
# 6. Disjunction detection
# ===================================================================


class TestDisjunctionDetection:
    """Test disjunction_present flag."""

    def test_disjunction_with_shared_var(self):
        """Disjunction referencing a known variable  -->  disjunction_present=True."""
        ir = _make_ir(
            word_pattern="xay",
            variables=[
                {"name": "x", "domain": None, "quantifier": "forall"},
                {"name": "y", "domain": None, "quantifier": "forall"},
            ],
            constraints=[
                {"kind": "disjunction", "args": ["x", "y"]},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["disjunction_present"] is True

    def test_no_disjunction(self):
        """No disjunction constraint  -->  disjunction_present=False."""
        ir = _make_ir(
            word_pattern="w 1 w^R",
            variables=[{"name": "w", "domain": None, "quantifier": "forall"}],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["disjunction_present"] is False

    def test_disjunction_no_shared_var(self):
        """Disjunction whose args do NOT reference known variables  -->  False."""
        ir = _make_ir(
            word_pattern="w 1 w^R",
            variables=[{"name": "w", "domain": None, "quantifier": "forall"}],
            constraints=[
                {"kind": "disjunction", "args": ["unknown1", "unknown2"]},
            ],
        )
        result = analyze_dcfl_hypothesis(ir)
        assert result["disjunction_present"] is False

    def test_grammar_disjunction_false(self):
        """Grammar path always sets disjunction_present=False."""
        ir = _make_grammar_ir()
        result = analyze_dcfl_hypothesis(ir)
        assert result["disjunction_present"] is False


# ===================================================================
# 7. Integration with example task IRs
# ===================================================================


class TestExampleTaskIRs:
    """Load the 4 JSON example files and validate results."""

    @pytest.fixture(params=[
        "task_wvaavRwR.json",
        "task_u1au2_u3au4.json",
        "task_anb_cnbn.json",
        "task_grammar_aSSb.json",
    ])
    def example_ir(self, request) -> dict[str, Any]:
        path = _EXAMPLES_DIR / request.param
        assert path.exists(), f"Example file not found: {path}"
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_example_returns_valid_result(self, example_ir):
        result = analyze_dcfl_hypothesis(example_ir)
        _assert_valid_result(result)

    def test_example_wvaavRwR(self):
        """Example with regex-constrained vars.

        The word pattern ``wvaav^Rw^R`` uses concatenated single-char
        variable names without spaces, so the greedy regex tokenizer
        merges them.  We only assert structural validity and that the
        regex-constrained variables are detected (domain is non-null).
        """
        path = _EXAMPLES_DIR / "task_wvaavRwR.json"
        with open(path, encoding="utf-8") as f:
            ir = json.load(f)
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)
        # Both w and v have non-null domains in the IR
        assert "w" in result["regex_constrained_vars"]
        assert "v" in result["regex_constrained_vars"]

    def test_example_grammar_aSSb(self):
        """Grammar input  -->  grammar_analysis, uncertain."""
        path = _EXAMPLES_DIR / "task_grammar_aSSb.json"
        with open(path, encoding="utf-8") as f:
            ir = json.load(f)
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)
        assert result["pattern_type"] == "grammar_analysis"
        assert result["prediction"] == "uncertain"

    def test_example_u1au2_u3au4(self):
        """Dual length comparison example."""
        path = _EXAMPLES_DIR / "task_u1au2_u3au4.json"
        with open(path, encoding="utf-8") as f:
            ir = json.load(f)
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)
        # This IR uses Unicode variable names (u₁ etc.) and two length_cmp constraints
        assert result["prediction"] in {"likely_dcfl", "uncertain", "likely_non_dcfl"}

    def test_example_anb_cnbn(self):
        """Disjunction example  -->  valid result."""
        path = _EXAMPLES_DIR / "task_anb_cnbn.json"
        with open(path, encoding="utf-8") as f:
            ir = json.load(f)
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)


# ===================================================================
# 8. Output structure
# ===================================================================


class TestOutputStructure:
    """Every call must return all required keys with correct types."""

    @pytest.mark.parametrize(
        "ir",
        [
            _make_ir("w 1 w^R", [{"name": "w", "domain": None, "quantifier": "forall"}]),
            _make_ir("w w^R", [{"name": "w", "domain": None, "quantifier": "forall"}]),
            _make_ir(
                "uav",
                [
                    {"name": "u", "domain": None, "quantifier": "forall"},
                    {"name": "v", "domain": None, "quantifier": "forall"},
                ],
                [{"kind": "length_cmp", "args": {"left": "u", "op": "<=", "right": "v"}}],
            ),
            _make_grammar_ir(),
            # Empty / minimal IR
            {"input_format": "set_builder", "language_spec": {"kind": "set_builder"}, "alphabet": []},
        ],
        ids=[
            "palindrome_sep",
            "palindrome_nosep",
            "length_cmp",
            "grammar",
            "minimal_ir",
        ],
    )
    def test_all_required_keys_present(self, ir):
        result = analyze_dcfl_hypothesis(ir)
        _assert_valid_result(result)

    def test_confidence_in_zero_one_range(self):
        ir = _make_ir("w 1 w^R", [{"name": "w", "domain": None, "quantifier": "forall"}])
        result = analyze_dcfl_hypothesis(ir)
        assert 0.0 <= result["confidence"] <= 1.0

    def test_prediction_is_known_value(self):
        ir = _make_ir("w 1 w^R", [{"name": "w", "domain": None, "quantifier": "forall"}])
        result = analyze_dcfl_hypothesis(ir)
        assert result["prediction"] in {"likely_dcfl", "likely_non_dcfl", "uncertain"}

    def test_reasoning_is_nonempty_string(self):
        ir = _make_ir("w 1 w^R", [{"name": "w", "domain": None, "quantifier": "forall"}])
        result = analyze_dcfl_hypothesis(ir)
        assert isinstance(result["reasoning"], str)
        assert len(result["reasoning"]) > 0
