"""Regressions for identical set-builder meaning across IR, oracle and Lean."""

import itertools
import json
import re
from pathlib import Path

import pytest

from agent_system.lib.lean_ir import alphabet_decl
from agent_system.lib.type_check import is_docker_available
from agent_system.tests.lean_eval import all_words, eval_decidable
from dcfl_system.lib.dcfl_ir_schema import validate_dcfl_ir
from dcfl_system.lib.lean_ir import render_statement
from dcfl_system.lib.word_sampler import (
    build_membership_oracle_from_ir,
    check_constraints,
    sample_from_set_builder,
)


def block_ir(constraints=None):
    return {
        "source_text": "positive unary block",
        "task_type": "classify_dcfl", "input_format": "set_builder",
        "alphabet": ["a", "b"],
        "language_spec": {
            "kind": "set_builder", "word_pattern": "u",
            "variables": [{"name": "u", "domain": "a*", "quantifier": "forall"}],
            "constraints": constraints if constraints is not None else [
                {"kind": "integer_cmp", "args": {"left": "u", "op": ">", "right": 0}},
            ],
        },
    }


def test_canonical_integer_cmp_is_block_length_not_decimal_string():
    ir = block_ir()
    assert validate_dcfl_ir(ir) == []
    oracle = build_membership_oracle_from_ir(ir)
    for n in range(7):
        for letters in itertools.product("ab", repeat=n):
            word = "".join(letters)
            assert oracle(word) is (bool(word) and set(word) == {"a"})
    statement = render_statement(ir, "dcfl")
    assert statement is not None
    assert "n_u > 0" in statement.language_decl


def test_variable_comparison_compares_lengths_in_both_consumers():
    ir = block_ir([{"kind": "integer_cmp", "args": {"left": "u", "op": ">", "right": "v"}}])
    ir["language_spec"]["word_pattern"] = "uv"
    ir["language_spec"]["variables"].append({"name": "v", "domain": "b*", "quantifier": "forall"})
    assert validate_dcfl_ir(ir) == []
    oracle = build_membership_oracle_from_ir(ir)
    for n in range(5):
        for m in range(5):
            assert oracle("a" * n + "b" * m) is (n > m)
    statement = render_statement(ir, "dcfl")
    assert statement is not None
    assert "n_u > n_v" in statement.language_decl


@pytest.mark.parametrize("extra", [
    {"constraints": [{"kind": "integer_cmp", "args": {"left": "m", "op": ">", "right": "n"}}]},
    {"variables": [{"name": "n", "domain": "a+", "quantifier": "forall"}]},
])
def test_exponent_fallback_does_not_drop_structured_conditions(extra):
    ir = block_ir([])
    ir["language_spec"].update(word_pattern="{a^n b^n c^m | n, m >= 0}", variables=[], constraints=[])
    ir["language_spec"].update(extra)
    ir["alphabet"] = ["a", "b", "c"]
    assert build_membership_oracle_from_ir(ir) is None
    assert render_statement(ir, "dcfl") is None


def test_unconstrained_exponent_pattern_still_builds():
    ir = block_ir([])
    ir["language_spec"].update(word_pattern="{a^n b^n | n >= 0}", variables=[])
    oracle = build_membership_oracle_from_ir(ir)
    assert oracle is not None and oracle("ab") is True and oracle("aab") is False


def test_schema_valid_exponent_ir_with_extra_inequality_is_unavailable():
    ir = block_ir([{"kind": "integer_cmp", "args": {"left": "m", "op": ">", "right": "n"}}])
    ir["alphabet"] = ["a", "b", "c"]
    ir["language_spec"].update(
        word_pattern="{a^n b^n c^m | n, m >= 0}",
        variables=[{"name": n, "domain": None, "quantifier": "exists"} for n in ("n", "m")],
    )
    assert validate_dcfl_ir(ir) == []
    # The old text-only fallback accepted epsilon, ab and abc, losing m > n.
    assert build_membership_oracle_from_ir(ir) is None
    assert render_statement(ir, "dcfl") is None


@pytest.mark.parametrize("constraint", [
    {"kind": "integer_cmp", "args": {"left": "u", "op": ">", "right": True}},
    {"kind": "integer_cmp", "args": {"var": "u", "op": ">", "value": 0, "left": "u"}},
    {"kind": "integer_cmp", "args": {"left": "u", "op": "?", "right": 0}},
    {"kind": "length_cmp", "args": {"left": "missing", "op": "==", "right": "u"}},
    {"kind": "equal", "args": {}},
    {"kind": "regex_member", "args": {"var": "u", "pattern": "["}},
    {"kind": "disjunction", "args": {"branches": ["a^n", "b^n"]}},
    {"kind": "disjunction", "args": {"branches": [{"kind": "equal", "args": {}}]}},
])
def test_invalid_predicate_is_rejected_instead_of_deciding_another_language(constraint):
    ir = block_ir([constraint])
    assert validate_dcfl_ir(ir)
    assert build_membership_oracle_from_ir(ir) is None
    assert render_statement(ir, "dcfl") is None


def test_legacy_scalar_comparison_remains_explicitly_numeric():
    c = {"kind": "integer_cmp", "args": {"var": "u", "op": ">", "value": 2}}
    assert check_constraints({"u": "10"}, [c])
    assert not check_constraints({"u": "aa"}, [c])
    # Do not translate decimal-string parsing as a block-length theorem.
    assert render_statement(block_ir([c]), "dcfl") is None


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_disjunction_is_independent_of_failing_numeric_branch_order(reverse, nested):
    branches = [
        {"kind": "integer_cmp", "args": {"var": "u", "op": ">", "value": 2}},
        {"kind": "integer_cmp", "args": {"left": "u", "op": ">", "right": 0}},
    ]
    if reverse:
        branches.reverse()
    c = {"kind": "disjunction", "args": {"branches": branches}}
    if nested:
        c = {"kind": "disjunction", "args": {"branches": [c]}}
    ir = block_ir([c])
    assert validate_dcfl_ir(ir) == []
    oracle = build_membership_oracle_from_ir(ir)
    assert oracle("a") is True
    assert oracle("") is False


def test_alphabet_unused_variables_and_domain_are_not_silently_ignored():
    ir = block_ir([])
    ir["language_spec"]["variables"][0]["domain"] = None
    oracle = build_membership_oracle_from_ir(ir)
    assert oracle("c") is False
    ir["language_spec"]["variables"].append({"name": "v", "domain": "b+", "quantifier": "forall"})
    assert build_membership_oracle_from_ir(ir) is None


def test_positive_sampler_respects_nonempty_domain():
    ir = block_ir([])
    ir["language_spec"]["variables"][0]["domain"] = "a+"
    words = sample_from_set_builder(ir["language_spec"], ir["alphabet"], count=10, max_len=6)
    assert words
    assert all(w["word"] and set(w["word"]) == {"a"} for w in words)


def test_input_parser_complete_examples_validate():
    prompt = (Path(__file__).parents[1] / "prompts/input_parser.md").read_text(encoding="utf-8")
    complete = []
    for block in re.findall(r"```json\s*(.*?)```", prompt, re.DOTALL):
        try:
            value = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "source_text" in value:
            complete.append(value)
            assert validate_dcfl_ir(value) == []
    assert len(complete) >= 2


@pytest.mark.skipif(not is_docker_available(), reason="Docker with tfl-lean4 image not available")
@pytest.mark.parametrize("compare_variables", [False, True])
def test_lean_evaluation_and_oracle_agree_on_integer_comparisons(compare_variables):
    ir = block_ir()
    if compare_variables:
        ir["language_spec"]["word_pattern"] = "uv"
        ir["language_spec"]["variables"].append({"name": "v", "domain": "b*", "quantifier": "forall"})
        ir["language_spec"]["constraints"][0]["args"]["right"] = "v"
    statement = render_statement(ir, "dcfl")
    assert statement is not None
    oracle = build_membership_oracle_from_ir(ir)
    words = all_words(ir["alphabet"], 5)
    _, symbols = alphabet_decl(ir["alphabet"])
    errors, _, answers = eval_decidable(statement, words, symbols)
    assert errors == []
    assert len(answers) == len(words)
    for word in words:
        expected = (
            bool(re.fullmatch("a*b*", word)) and word.count("a") > word.count("b")
            if compare_variables else bool(word) and set(word) == {"a"}
        )
        assert oracle(word) is expected
        assert answers[word] is expected
