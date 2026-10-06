"""Exact fixed points and honest minimum-k/continuation reporting."""
from unittest.mock import patch

import pytest

from ll_system.lib.claim_verifier import (
    _check_dead_class_finite_ll,
    _continuable_ll,
    _semantic_check_prefix_classes,
)
from ll_system.lib.first_follow import compute_all, compute_local_follow_sets, k_concat
from ll_system.lib.ll_table_builder import check_ll_k, find_min_ll_k
from ll_system.orchestrator import _run_ll_k_oracle_on, run_pipeline
from ll_system.renderer import render_html, render_markdown
from ll_system.tests.test_ll_k_full import GRAMMAR_AU_LL2_NOT_STRONG, GRAMMAR_LL1


def _chain(rhs: list[str], reverse: bool = False) -> dict:
    nonterminals = ["S"] + [f"A{i}" for i in range(1101)]
    rules = [
        {"lhs": "S", "rhs": ["A0"]},
        {"lhs": "S", "rhs": rhs},
    ] + [
        {"lhs": f"A{i}", "rhs": [f"A{i + 1}"]} for i in range(1100)
    ] + [{"lhs": "A1100", "rhs": rhs}]
    return {
        "start": "S", "terminals": ["b"], "nonterminals": nonterminals,
        "rules": list(reversed(rules)) if reverse else rules,
    }


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("rhs", [["b"], []])
def test_long_dependencies_reach_exact_fixed_point(rhs, reverse):
    grammar = _chain(rhs, reverse)
    nullable, first, follow = compute_all(grammar, 2)
    expected_first = {"b"} if rhs else {""}
    assert nullable == (set() if rhs else set(grammar["nonterminals"]))
    assert all(strings == expected_first for strings in first.values())
    assert all(strings == {"$$"} for strings in follow.values())
    sigma, complete = compute_local_follow_sets(grammar, 2, first, nullable)
    assert complete
    assert all(contexts == {frozenset({""})} for contexts in sigma.values())
    result = check_ll_k(grammar, 2)
    assert result["is_ll_k"] is False
    assert result["test_complete"]
    assert result["ll_conflicts"][0]["nonterminal"] == "S"


def test_long_chain_format3_pipeline_rejects_ambiguity():
    result = run_pipeline({
        "task_type": "ll_check_grammar", "source_text": "long chain ambiguity",
        "grammar": _chain(["b"]), "k": 1,
    })
    assert result["verdict"] == "not_ll"
    assert result["verdict_gate"]["basis"][0]["trust"] == "verified"
    assert result["first_follow_result"]["first_sets"]["A0"] == ["b"]


def test_first_prefix_requires_productive_suffix():
    grammar = {
        "start": "S", "terminals": ["a"], "nonterminals": ["S", "B"],
        "rules": [{"lhs": "S", "rhs": ["a", "B"]}, {"lhs": "B", "rhs": ["B"]}],
    }
    _, first, _ = compute_all(grammar, 1)
    assert first == {"S": set(), "B": set()}


def test_zero_lookahead_still_requires_terminal_derivations():
    assert k_concat(set(), {"a"}, 0) == set()
    assert k_concat({"a"}, set(), 0) == set()
    assert k_concat({"a"}, {"b"}, 0) == {""}
    grammar = {
        "start": "S", "terminals": ["a"], "nonterminals": ["S", "B", "P"],
        "rules": [
            {"lhs": "S", "rhs": ["a", "B"]}, {"lhs": "B", "rhs": ["B"]},
            {"lhs": "P", "rhs": ["a"]},
        ],
    }
    _, first, _ = compute_all(grammar, 0)
    assert first == {"S": set(), "B": set(), "P": {""}}


def test_nonproductive_left_prefix_cannot_reach_local_context():
    grammar = {
        "start": "S", "terminals": ["a", "b"],
        "nonterminals": ["S", "D", "A", "B"],
        "rules": [
            {"lhs": "S", "rhs": ["a"]}, {"lhs": "S", "rhs": ["D", "A"]},
            {"lhs": "D", "rhs": ["D"]},
            {"lhs": "A", "rhs": ["b"]}, {"lhs": "A", "rhs": ["b", "B"]},
            {"lhs": "B", "rhs": []},
        ],
    }
    nullable, first, follow = compute_all(grammar, 1)
    sigma, complete = compute_local_follow_sets(grammar, 1, first, nullable)
    assert complete and sigma["A"] == sigma["B"] == set()
    assert follow["A"] == follow["B"] == set()
    assert check_ll_k(grammar, 1)["is_ll_k"] is True


def test_duplicate_production_is_not_a_competing_alternative():
    grammar = {**GRAMMAR_AU_LL2_NOT_STRONG, "rules": [
        *GRAMMAR_AU_LL2_NOT_STRONG["rules"], {"lhs": "A", "rhs": ["b"]},
        {"lhs": "A", "rhs": ["ε"]},
    ]}
    result = check_ll_k(grammar, 2)
    assert result["is_ll_k"] is True
    assert result["is_strong_ll_k"] is False
    assert result["ll_conflicts"] == []


def test_unknown_lower_k_stops_minimum_search():
    real_check = check_ll_k

    def limited_check(grammar, k, **kwargs):
        return real_check(grammar, k, max_tables=0 if k == 2 else 20000, **kwargs)

    with patch("ll_system.lib.ll_table_builder.check_ll_k", side_effect=limited_check) as oracle:
        result = find_min_ll_k(GRAMMAR_AU_LL2_NOT_STRONG, max_k=3)
    assert [call.args[1] for call in oracle.call_args_list] == [1, 2]
    assert result["found"] is False and result["k"] is None
    assert result["undetermined"] is True
    assert result["max_k_checked"] == 2 and result["max_k_decided"] == 1
    assert result["result_for_k"]["is_ll_k"] is None


def test_search_budget_expired_before_first_k_is_unknown():
    result = find_min_ll_k(GRAMMAR_LL1, time_budget_s=-1)
    assert result["undetermined"] is True
    assert result["max_k_checked"] == result["max_k_decided"] == 0
    assert result["found"] is False


@pytest.mark.parametrize("requested_k,minimum", [(None, 1), (1, 1), (2, None)])
def test_oracle_distinguishes_witness_from_minimum(requested_k, minimum):
    ff = _run_ll_k_oracle_on(GRAMMAR_LL1, requested_k)
    assert ff["found"]
    assert ff["min_k"] == minimum
    assert ff["minimum_proven"] is (minimum is not None)
    result = {"verdict": "ll", "k": ff["k"], "first_follow_result": ff}
    for rendered in (render_markdown(result), render_html(result)):
        assert ("Минимальное k" in rendered) is (minimum is not None)
        assert ("Проверенное k" in rendered) is (minimum is None)


def test_suffix_search_exhaustion_is_unknown_not_deadness():
    oracle = lambda word: word == "a" * 20
    assert _continuable_ll(oracle, "", ["a"]) is None
    assert _continuable_ll(oracle, "a" * 19, ["a"]) is True
    ir = {"language_spec": {"kind": "set_builder", "alphabet": ["a"]}}
    with patch("ll_system.lib.claim_verifier._try_word_oracle", return_value=oracle):
        dead_ok, issues = _check_dead_class_finite_ll(ir)
        trust, details = _semantic_check_prefix_classes({
            "distinguishing_suffix": "a",
            "representative_pairs": [{"u": "a" * 19, "v": "a" * 18}],
        }, ir)
    assert dead_ok is None and issues
    assert all("unknown" in issue and "NO continuation" not in issue for issue in issues)
    assert trust == "well_formed"
    assert details["dead_class_finite_check"] == "unknown"


def test_dead_class_global_budget_stops_the_search_as_unknown():
    calls = []

    def oracle(word):
        calls.append(word)
        return False                      # never a witness: each prefix would burn its full budget

    ir = {"language_spec": {"kind": "set_builder", "alphabet": ["a", "b"]}}
    with patch("ll_system.lib.claim_verifier._try_word_oracle", return_value=oracle):
        dead_ok, issues = _check_dead_class_finite_ll(ir, total_node_budget=50)
    assert dead_ok is None
    assert len(calls) <= 50
    assert any("global search budget exhausted" in issue for issue in issues)


def test_dead_class_global_wall_clock_budget_uses_the_injected_clock():
    ticks = iter(range(0, 10_000))
    oracle_calls = []

    def oracle(word):
        oracle_calls.append(word)
        return False

    ir = {"language_spec": {"kind": "set_builder", "alphabet": ["a", "b"]}}
    with patch("ll_system.lib.claim_verifier._try_word_oracle", return_value=oracle):
        dead_ok, issues = _check_dead_class_finite_ll(
            ir, total_node_budget=10**9, total_seconds=5.0, clock=lambda: next(ticks))
    assert dead_ok is None and len(oracle_calls) <= 6
    assert any("global search budget exhausted" in issue for issue in issues)


def test_dead_class_global_budget_does_not_disturb_quick_witnesses():
    ir = {"language_spec": {"kind": "set_builder", "alphabet": ["a"]}}
    with patch("ll_system.lib.claim_verifier._try_word_oracle", return_value=lambda w: True):
        assert _check_dead_class_finite_ll(ir) == (True, [])
