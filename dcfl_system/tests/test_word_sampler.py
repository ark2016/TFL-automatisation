"""Tests for dcfl_system.lib.word_sampler module."""

from __future__ import annotations

import json
import pathlib

import pytest

from dcfl_system.lib.word_sampler import (
    build_membership_oracle_from_ir,
    check_constraints,
    generate_negative_examples,
    sample_from_grammar,
    sample_from_set_builder,
    sample_words,
)

# ---------------------------------------------------------------------------
# Paths to example task files
# ---------------------------------------------------------------------------

_EXAMPLES_DIR = pathlib.Path(__file__).resolve().parent.parent / "examples"

_EXAMPLE_FILES = [
    "task_grammar_aSSb.json",
    "task_wvaavRwR.json",
    "task_u1au2_u3au4.json",
    "task_anb_cnbn.json",
]

REQUIRED_KEYS = {"word", "length", "in_language", "variables", "source"}


def _load_task(filename: str) -> dict:
    return json.loads((_EXAMPLES_DIR / filename).read_text(encoding="utf-8"))


# ===================================================================
# 1. check_constraints
# ===================================================================


class TestCheckConstraints:
    """Tests for check_constraints."""

    def test_empty_constraints_returns_true(self):
        assert check_constraints({"x": "abc"}, []) is True

    # -- length_cmp --

    def test_length_cmp_le_satisfied(self):
        constraint = {"kind": "length_cmp", "args": {"left": "u1", "op": "<=", "right": "u2"}}
        assert check_constraints({"u1": "ab", "u2": "abc"}, [constraint]) is True

    def test_length_cmp_le_equal_lengths(self):
        constraint = {"kind": "length_cmp", "args": {"left": "u1", "op": "<=", "right": "u2"}}
        assert check_constraints({"u1": "ab", "u2": "xy"}, [constraint]) is True

    def test_length_cmp_le_violated(self):
        constraint = {"kind": "length_cmp", "args": {"left": "u1", "op": "<=", "right": "u2"}}
        assert check_constraints({"u1": "abcd", "u2": "xy"}, [constraint]) is False

    def test_length_cmp_ge_satisfied(self):
        constraint = {"kind": "length_cmp", "args": {"left": "u3", "op": ">=", "right": "u4"}}
        assert check_constraints({"u3": "abc", "u4": "a"}, [constraint]) is True

    def test_length_cmp_missing_var_uses_empty_string(self):
        constraint = {"kind": "length_cmp", "args": {"left": "u1", "op": "<=", "right": "u2"}}
        # u1 missing => len("") = 0 <= len("a") = 1
        assert check_constraints({"u2": "a"}, [constraint]) is True

    # -- integer_cmp --

    def test_integer_cmp_gt_satisfied(self):
        constraint = {"kind": "integer_cmp", "args": {"var": "n", "op": ">", "value": 0}}
        assert check_constraints({"n": "1"}, [constraint]) is True

    def test_integer_cmp_gt_zero_fails(self):
        constraint = {"kind": "integer_cmp", "args": {"var": "n", "op": ">", "value": 0}}
        assert check_constraints({"n": "0"}, [constraint]) is False

    def test_integer_cmp_eq(self):
        constraint = {"kind": "integer_cmp", "args": {"var": "n", "op": "==", "value": 5}}
        assert check_constraints({"n": "5"}, [constraint]) is True
        assert check_constraints({"n": "4"}, [constraint]) is False

    def test_integer_cmp_non_numeric_var_returns_false(self):
        constraint = {"kind": "integer_cmp", "args": {"var": "n", "op": ">", "value": 0}}
        assert check_constraints({"n": "abc"}, [constraint]) is False

    # -- multiple constraints --

    def test_multiple_constraints_all_must_hold(self):
        c1 = {"kind": "length_cmp", "args": {"left": "u1", "op": "<=", "right": "u2"}}
        c2 = {"kind": "length_cmp", "args": {"left": "u3", "op": ">=", "right": "u4"}}
        variables = {"u1": "a", "u2": "ab", "u3": "abc", "u4": "a"}
        assert check_constraints(variables, [c1, c2]) is True

    def test_multiple_constraints_one_fails(self):
        c1 = {"kind": "length_cmp", "args": {"left": "u1", "op": "<=", "right": "u2"}}
        c2 = {"kind": "length_cmp", "args": {"left": "u3", "op": ">=", "right": "u4"}}
        variables = {"u1": "a", "u2": "ab", "u3": "a", "u4": "abcdef"}
        assert check_constraints(variables, [c1, c2]) is False

    # -- other constraint kinds --

    def test_regex_member(self):
        constraint = {"kind": "regex_member", "args": {"var": "w", "pattern": "a+b"}}
        assert check_constraints({"w": "aab"}, [constraint]) is True
        assert check_constraints({"w": "bba"}, [constraint]) is False

    def test_equal_constraint(self):
        constraint = {"kind": "equal", "args": {"left": "x", "right": "y"}}
        assert check_constraints({"x": "abc", "y": "abc"}, [constraint]) is True
        assert check_constraints({"x": "abc", "y": "xyz"}, [constraint]) is False

    def test_reverse_constraint(self):
        constraint = {"kind": "reverse", "args": {"left": "x", "right": "y"}}
        assert check_constraints({"x": "cba", "y": "abc"}, [constraint]) is True
        assert check_constraints({"x": "abc", "y": "abc"}, [constraint]) is False

    def test_disjunction_at_least_one_branch(self):
        branch_a = {"kind": "integer_cmp", "args": {"var": "n", "op": ">", "value": 5}}
        branch_b = {"kind": "integer_cmp", "args": {"var": "n", "op": "==", "value": 0}}
        constraint = {"kind": "disjunction", "args": {"branches": [branch_a, branch_b]}}
        # n=0 satisfies branch_b
        assert check_constraints({"n": "0"}, [constraint]) is True
        # n=3 satisfies neither
        assert check_constraints({"n": "3"}, [constraint]) is False

    def test_unknown_kind_returns_false(self):
        constraint = {"kind": "some_unknown_kind", "args": {}}
        assert check_constraints({"x": "a"}, [constraint]) is False


# ===================================================================
# 2. sample_from_grammar
# ===================================================================


class TestSampleFromGrammar:
    """Tests for sample_from_grammar."""

    @pytest.fixture()
    def grammar_spec(self) -> dict:
        task = _load_task("task_grammar_aSSb.json")
        return task["language_spec"]

    def test_returns_non_empty_list(self, grammar_spec):
        results = sample_from_grammar(grammar_spec, count=10)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_words_use_only_terminals(self, grammar_spec):
        terminals = set(grammar_spec["terminals"])
        results = sample_from_grammar(grammar_spec, count=10)
        for sw in results:
            for ch in sw["word"]:
                assert ch in terminals, f"Non-terminal char '{ch}' in word '{sw['word']}'"

    def test_source_is_grammar_derivation(self, grammar_spec):
        results = sample_from_grammar(grammar_spec, count=10)
        for sw in results:
            assert sw["source"] == "grammar_derivation"

    def test_all_words_within_max_len(self, grammar_spec):
        max_len = 15
        results = sample_from_grammar(grammar_spec, count=10, max_len=max_len)
        for sw in results:
            assert len(sw["word"]) <= max_len

    def test_in_language_is_true(self, grammar_spec):
        results = sample_from_grammar(grammar_spec, count=5)
        for sw in results:
            assert sw["in_language"] is True


# ===================================================================
# 3. sample_from_set_builder
# ===================================================================


class TestSampleFromSetBuilder:
    """Tests for sample_from_set_builder."""

    @pytest.fixture()
    def set_builder_task(self) -> dict:
        return _load_task("task_wvaavRwR.json")

    def test_returns_non_empty_list(self, set_builder_task):
        spec = set_builder_task["language_spec"]
        alphabet = set_builder_task["alphabet"]
        results = sample_from_set_builder(spec, alphabet, count=10)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_each_has_required_fields(self, set_builder_task):
        spec = set_builder_task["language_spec"]
        alphabet = set_builder_task["alphabet"]
        results = sample_from_set_builder(spec, alphabet, count=10)
        for sw in results:
            assert "word" in sw
            assert "length" in sw
            assert "source" in sw

    def test_source_is_substitution(self, set_builder_task):
        spec = set_builder_task["language_spec"]
        alphabet = set_builder_task["alphabet"]
        results = sample_from_set_builder(spec, alphabet, count=10)
        for sw in results:
            assert sw["source"] == "substitution"

    def test_length_matches_word(self, set_builder_task):
        spec = set_builder_task["language_spec"]
        alphabet = set_builder_task["alphabet"]
        results = sample_from_set_builder(spec, alphabet, count=10)
        for sw in results:
            assert sw["length"] == len(sw["word"])

    def test_variables_field_present(self, set_builder_task):
        spec = set_builder_task["language_spec"]
        alphabet = set_builder_task["alphabet"]
        results = sample_from_set_builder(spec, alphabet, count=10)
        for sw in results:
            assert sw["variables"] is not None
            assert isinstance(sw["variables"], dict)

    def test_constraints_satisfied(self, set_builder_task):
        spec = set_builder_task["language_spec"]
        alphabet = set_builder_task["alphabet"]
        constraints = spec.get("constraints", [])
        results = sample_from_set_builder(spec, alphabet, count=10)
        for sw in results:
            assert check_constraints(sw["variables"], constraints)


# ===================================================================
# 4. sample_words (dispatch)
# ===================================================================


class TestSampleWords:
    """Tests for the top-level sample_words dispatcher."""

    def test_dispatches_grammar(self):
        task = _load_task("task_grammar_aSSb.json")
        results = sample_words(task, count=5)
        assert len(results) > 0
        # Should contain both positive (grammar_derivation) and negative words
        sources = {sw["source"] for sw in results}
        assert "grammar_derivation" in sources

    def test_dispatches_set_builder(self):
        task = _load_task("task_wvaavRwR.json")
        results = sample_words(task, count=5)
        assert len(results) > 0
        sources = {sw["source"] for sw in results}
        assert "substitution" in sources

    def test_unknown_format_returns_random(self):
        ir = {"input_format": "unknown", "alphabet": ["a", "b"]}
        results = sample_words(ir, count=5)
        assert len(results) > 0
        positive = [sw for sw in results if sw["source"] == "random"]
        assert len(positive) > 0

    def test_includes_negative_examples(self):
        task = _load_task("task_grammar_aSSb.json")
        results = sample_words(task, count=10)
        negatives = [sw for sw in results if sw["in_language"] is False]
        assert len(negatives) > 0


# ===================================================================
# 5. generate_negative_examples
# ===================================================================


class TestGenerateNegativeExamples:
    """Tests for generate_negative_examples."""

    def test_returns_negatives(self):
        ir = {"alphabet": ["a", "b"]}
        positives = [
            {"word": "aabb", "length": 4, "in_language": True, "variables": None, "source": "grammar_derivation"},
            {"word": "ab", "length": 2, "in_language": True, "variables": None, "source": "grammar_derivation"},
        ]
        results = generate_negative_examples(ir, positives, count=5)
        assert len(results) > 0

    def test_source_is_boundary_or_random(self):
        ir = {"alphabet": ["a", "b"]}
        positives = [
            {"word": "aabb", "length": 4, "in_language": True, "variables": None, "source": "test"},
            {"word": "ab", "length": 2, "in_language": True, "variables": None, "source": "test"},
            {"word": "aaabbb", "length": 6, "in_language": True, "variables": None, "source": "test"},
        ]
        results = generate_negative_examples(ir, positives, count=5)
        for sw in results:
            assert sw["source"] in ("boundary", "random")

    def test_in_language_is_false(self):
        ir = {"alphabet": ["a", "b"]}
        positives = [
            {"word": "aabb", "length": 4, "in_language": True, "variables": None, "source": "test"},
        ]
        results = generate_negative_examples(ir, positives, count=5)
        for sw in results:
            assert sw["in_language"] is False

    def test_negatives_not_in_positive_set(self):
        ir = {"alphabet": ["a", "b"]}
        positives = [
            {"word": "aabb", "length": 4, "in_language": True, "variables": None, "source": "test"},
            {"word": "ab", "length": 2, "in_language": True, "variables": None, "source": "test"},
        ]
        positive_words = {pw["word"] for pw in positives}
        results = generate_negative_examples(ir, positives, count=5)
        for sw in results:
            assert sw["word"] not in positive_words

    def test_respects_count_limit(self):
        ir = {"alphabet": ["a", "b"]}
        positives = [
            {"word": "a" * i, "length": i, "in_language": True, "variables": None, "source": "test"}
            for i in range(1, 20)
        ]
        results = generate_negative_examples(ir, positives, count=3)
        assert len(results) <= 3


# ===================================================================
# 6. SampleWord structure
# ===================================================================


class TestSampleWordStructure:
    """Every returned dict must have all required keys."""

    @pytest.mark.parametrize("filename", _EXAMPLE_FILES)
    def test_all_keys_present(self, filename):
        task = _load_task(filename)
        results = sample_words(task, count=5)
        for sw in results:
            missing = REQUIRED_KEYS - sw.keys()
            assert not missing, f"Missing keys {missing} in word from {filename}"

    @pytest.mark.parametrize("filename", _EXAMPLE_FILES)
    def test_length_matches_word_len(self, filename):
        task = _load_task(filename)
        results = sample_words(task, count=5)
        for sw in results:
            assert sw["length"] == len(sw["word"]), (
                f"length={sw['length']} != len(word)={len(sw['word'])} for '{sw['word']}'"
            )

    @pytest.mark.parametrize("filename", _EXAMPLE_FILES)
    def test_word_is_string(self, filename):
        task = _load_task(filename)
        results = sample_words(task, count=5)
        for sw in results:
            assert isinstance(sw["word"], str)

    @pytest.mark.parametrize("filename", _EXAMPLE_FILES)
    def test_source_is_string(self, filename):
        task = _load_task(filename)
        results = sample_words(task, count=5)
        for sw in results:
            assert isinstance(sw["source"], str)
            assert sw["source"] != ""


# ===================================================================
# 7. Determinism
# ===================================================================


class TestDeterminism:
    """Calling sample_words twice with the same input gives the same results."""

    @pytest.mark.parametrize("filename", _EXAMPLE_FILES)
    def test_deterministic_output(self, filename):
        task = _load_task(filename)
        run1 = sample_words(task, count=10)
        run2 = sample_words(task, count=10)
        assert len(run1) == len(run2)
        for sw1, sw2 in zip(run1, run2):
            assert sw1 == sw2, f"Non-determinism detected for {filename}"


# ===================================================================
# 8. Test with all example tasks
# ===================================================================


class TestExampleTasks:
    """Load all 4 JSON files, call sample_words, verify results."""

    @pytest.mark.parametrize("filename", _EXAMPLE_FILES)
    def test_non_empty_results(self, filename):
        task = _load_task(filename)
        results = sample_words(task, count=10)
        assert len(results) > 0, f"Empty results for {filename}"

    @pytest.mark.parametrize("filename", _EXAMPLE_FILES)
    def test_valid_structure(self, filename):
        task = _load_task(filename)
        results = sample_words(task, count=10)
        for sw in results:
            assert REQUIRED_KEYS.issubset(sw.keys())
            assert isinstance(sw["word"], str)
            assert isinstance(sw["length"], int)
            assert sw["length"] == len(sw["word"])
            assert sw["in_language"] in (True, False, None)
            assert isinstance(sw["source"], str)

    def test_grammar_task_produces_grammar_derivation_source(self):
        task = _load_task("task_grammar_aSSb.json")
        results = sample_words(task, count=10)
        positives = [sw for sw in results if sw["in_language"] is True]
        assert len(positives) > 0
        for sw in positives:
            assert sw["source"] == "grammar_derivation"

    def test_set_builder_task_produces_substitution_source(self):
        task = _load_task("task_wvaavRwR.json")
        results = sample_words(task, count=10)
        positives = [sw for sw in results if sw["in_language"] is True]
        assert len(positives) > 0
        for sw in positives:
            assert sw["source"] == "substitution"


# ===================================================================
# 9. build_membership_oracle_from_ir (single entry point, root CLAUDE.md
#    item 3 / docs/VERDICT_POLICY.md §4)
# ===================================================================


class TestBuildMembershipOracleFromIR:
    def test_set_builder_with_variables_uses_segment_matcher(self):
        task = _load_task("task_u1au2_u3au4.json")
        oracle = build_membership_oracle_from_ir(task)
        assert oracle is not None
        assert oracle("aa") is True  # u1="" a u2="a" u3="" a u4="" (fits)
        assert oracle("bb") is False

    def test_grammar_input_format_uses_cyk(self):
        task = _load_task("task_grammar_aSSb.json")
        oracle = build_membership_oracle_from_ir(task)
        assert oracle is not None
        assert oracle("ab") is True
        assert oracle("aa") is False

    def test_set_builder_without_variables_falls_back_to_exponent_pattern(self):
        # dcfl-04 shape: {a^n b^n c^m | n, m >= 0}, variables=[].
        ir = {
            "input_format": "set_builder",
            "alphabet": ["a", "b", "c"],
            "language_spec": {
                "kind": "set_builder",
                "word_pattern": "{a^n b^n c^m | n, m >= 0}",
                "variables": [],
                "constraints": [],
            },
        }
        oracle = build_membership_oracle_from_ir(ir)
        assert oracle is not None
        assert oracle("aabbccc") is True
        assert oracle("aab") is False

    def test_natural_kind_uses_exponent_pattern(self):
        ir = {
            "language_spec": {
                "kind": "natural",
                "description": "{a^i b^j c^k d^l | i = 0 or j = k = l}",
            },
            "alphabet": ["a", "b", "c", "d"],
        }
        oracle = build_membership_oracle_from_ir(ir)
        assert oracle is not None
        assert oracle("aabbccdd") is True
        assert oracle("aabccddd") is False

    def test_generic_word_variable_pattern_not_exponent_notation_returns_none(self):
        # dcfl-15/16 shape before their IR fix: plain word variables
        # ("u1 a u2"), no exponent blocks — exponent_pattern correctly
        # refuses rather than mis-parsing "u1"/"u2" as letters.
        ir = {
            "input_format": "set_builder",
            "alphabet": ["a", "b"],
            "language_spec": {
                "kind": "set_builder",
                "word_pattern": "{u1 a u2 | |u1| <= |u2|}",
                "variables": [],
                "constraints": [],
            },
        }
        assert build_membership_oracle_from_ir(ir) is None

    def test_unknown_format_returns_none(self):
        ir = {"input_format": "unknown", "alphabet": ["a", "b"], "language_spec": {}}
        assert build_membership_oracle_from_ir(ir) is None

    def test_dcfl_15_and_16_eval_json_build_after_fix(self):
        eval_dir = _EXAMPLES_DIR / "eval"
        for name in ("dcfl-15.json", "dcfl-16.json"):
            ir = json.loads((eval_dir / name).read_text(encoding="utf-8"))
            oracle = build_membership_oracle_from_ir(ir)
            assert oracle is not None, f"{name}: oracle should build"
            assert oracle("a") is True, f"{name}: 'a' (u=empty, u'=empty) should be in L"


# ===================================================================
# 10. sample_words enumeration fallback (plain oracle function, no
#     `variables`) -- root CLAUDE.md item 3
# ===================================================================


class TestSampleWordsEnumerationFallback:
    def test_dcfl04_shape_produces_positive_and_negative_via_enumeration(self):
        ir = {
            "task_id": "dcfl-04-like",
            "input_format": "set_builder",
            "alphabet": ["a", "b", "c"],
            "language_spec": {
                "kind": "set_builder",
                "word_pattern": "{a^n b^n c^m | n, m >= 0}",
                "variables": [],
                "constraints": [],
            },
        }
        results = sample_words(ir, count=10, max_len=8)
        assert len(results) > 0
        positives = [sw for sw in results if sw["in_language"] is True]
        negatives = [sw for sw in results if sw["in_language"] is False]
        assert len(positives) > 0
        assert len(negatives) > 0
        for sw in positives:
            assert sw["source"] == "enumeration"

    def test_no_oracle_available_falls_back_to_random(self):
        ir = {
            "input_format": "set_builder",
            "alphabet": ["a", "b"],
            "language_spec": {
                "kind": "set_builder",
                "word_pattern": "{ww | w in {a,b}*}",
                "variables": [],
                "constraints": [],
            },
        }
        results = sample_words(ir, count=5)
        assert len(results) > 0
        sources = {sw["source"] for sw in results}
        assert "random" in sources

    def test_deterministic(self):
        ir = {
            "input_format": "set_builder",
            "alphabet": ["a", "b"],
            "language_spec": {
                "kind": "set_builder",
                "word_pattern": "a^n b^m",
                "variables": [],
                "constraints": [],
            },
        }
        run1 = sample_words(ir, count=8, max_len=8)
        run2 = sample_words(ir, count=8, max_len=8)
        assert run1 == run2


# ===================================================================
# 11. sample_words negatives must be verified against the IR's own oracle
#     when one is available (dcfl-15/16 trap: a mutated positive word can
#     still be in L for a dense language) -- root CLAUDE.md item 3 /
#     docs/VERDICT_POLICY.md §4 "never guess".
# ===================================================================


class TestSampleWordsNegativesMatchOracle:
    @pytest.mark.parametrize("filename", ["dcfl-15.json", "dcfl-16.json"])
    def test_every_in_language_label_matches_the_oracle(self, filename):
        eval_dir = _EXAMPLES_DIR / "eval"
        ir = json.loads((eval_dir / filename).read_text(encoding="utf-8"))
        oracle = build_membership_oracle_from_ir(ir)
        assert oracle is not None

        results = sample_words(ir, count=20, max_len=30)
        negatives = [sw for sw in results if sw["in_language"] is False]
        assert len(negatives) > 0, f"{filename}: expected at least one negative"
        for sw in negatives:
            verdict = oracle(sw["word"])
            assert verdict is not True, (
                f"{filename}: {sw['word']!r} labelled in_language=False but "
                f"the oracle says it IS in L"
            )

    @pytest.mark.parametrize("filename", ["dcfl-15.json", "dcfl-16.json"])
    def test_positive_labels_also_match_the_oracle(self, filename):
        eval_dir = _EXAMPLES_DIR / "eval"
        ir = json.loads((eval_dir / filename).read_text(encoding="utf-8"))
        oracle = build_membership_oracle_from_ir(ir)
        assert oracle is not None

        results = sample_words(ir, count=20, max_len=30)
        positives = [sw for sw in results if sw["in_language"] is True]
        assert len(positives) > 0
        for sw in positives:
            assert oracle(sw["word"]) is not False, (
                f"{filename}: {sw['word']!r} labelled in_language=True but "
                f"the oracle says it is NOT in L"
            )
