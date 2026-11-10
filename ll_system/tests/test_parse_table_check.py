"""docs/VERDICT_POLICY.md R3′ (1): the LL(k) parse table an "is LL(k)" claim
carries is run as a predictive parser against the task oracle.

Mock mode only: pure functions and MockRunner, no LLM.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agent_system.lib.word_enumeration import full_enumeration_max_len, words_up_to
from ll_system.lib.claim_verifier import verify_ll_claim
from ll_system.lib.ll_parser import LLParser
from ll_system.lib.parse_table_check import (
    PARSE_TABLE_MIN_WORDS, ParseTableFormatError, check_parse_table, normalize_parse_table,
)
from ll_system.orchestrator import MockRunner, _agent_retry_hint, run_pipeline

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"

# L = {w b c w^R | w in {a,b}*}, LL(1) grammar S -> aSa | bT, T -> c | aSab | bTb.
GRAMMAR = {
    "nonterminals": ["S", "T"],
    "terminals": ["a", "b", "c"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S", "a"]},
        {"lhs": "S", "rhs": ["b", "T"]},
        {"lhs": "T", "rhs": ["c"]},
        {"lhs": "T", "rhs": ["a", "S", "a", "b"]},
        {"lhs": "T", "rhs": ["b", "T", "b"]},
    ],
}
TABLE = {
    "S": {"a": ["a", "S", "a"], "b": ["b", "T"]},
    "T": {"c": ["c"], "a": ["a", "S", "a", "b"], "b": ["b", "T", "b"]},
}
ENTRIES = {
    "k": 1,
    "start": "S",
    "entries": [
        {"nonterminal": nt, "lookahead": la, "rhs": rhs}
        for nt, row in TABLE.items() for la, rhs in row.items()
    ],
}


def _ir() -> dict:
    return json.loads((EXAMPLES_DIR / "format1_wbcwR.json").read_text(encoding="utf-8"))


def _oracle(word: str) -> bool:
    if len(word) < 2:
        return False
    for i in range(len(word) - 1):
        w = word[:i]
        if word[i:i + 2] == "bc" and set(w) <= {"a", "b"} and word[i + 2:] == w[::-1]:
            return True
    return False


def _check(raw, *, k=1, grammar=GRAMMAR, oracle=_oracle, terminals=("a", "b", "c")):
    return check_parse_table(
        raw, k=k, grammar=grammar, terminals=list(terminals) if terminals else terminals,
        oracle_factory=lambda: oracle,
    )


def _builder(table) -> dict:
    sketch = {"method": "ll_grammar_construction", "k": 1, "grammar": GRAMMAR}
    if table is not None:
        sketch["parse_table"] = table
    return {"agent_name": "ll_grammar_builder", "verdict": "ll", "proof_sketch": sketch, "artifacts": {}}


# ---------------------------------------------------------------- shared enumeration

def test_word_enumeration_bound_matches_the_dpda_budget():
    assert full_enumeration_max_len(3) == 6      # 1 + 3 + ... + 729 = 1093 <= 3000
    assert full_enumeration_max_len(2) == 10     # capped at 10
    assert full_enumeration_max_len(1) == 10
    words = words_up_to(["a", "b"], 2)
    assert words == ["", "a", "b", "aa", "ab", "ba", "bb"]


def test_dcfl_and_congruence_use_the_shared_enumeration():
    from agent_system.lib import congruence
    from dcfl_system.lib.oracle_verifier import _full_enumeration_max_len

    assert congruence._words_up_to is words_up_to
    assert _full_enumeration_max_len(3) == full_enumeration_max_len(3) == 6


# ---------------------------------------------------------------- the check itself

def test_correct_table_is_bounded_pass_over_the_full_enumeration():
    for raw in (TABLE, ENTRIES, {"k": 1, "start": "S", "table": TABLE}):
        res = _check(raw)
        assert res["trust"] == "bounded_pass", res
        assert res["details"]["max_len"] == 6
        assert res["details"]["words_checked"] == len(words_up_to(["a", "b", "c"], 6))


def test_missing_table_is_reported_as_missing():
    for raw in (None, {}, {"entries": []}, []):
        assert _check(raw)["trust"] == "missing"


def test_conflict_cell_refutes_in_both_formats():
    nested = deepcopy(TABLE)
    nested["S"]["a"] = [["a", "S", "a"], ["b", "T"]]
    res = _check(nested)
    assert res["trust"] == "refuted" and res["details"]["status"] == "conflict"

    entries = deepcopy(ENTRIES)
    entries["entries"].append({"nonterminal": "T", "lookahead": "c", "rhs": ["b", "T", "b"]})
    res = _check(entries)
    assert res["trust"] == "refuted"
    assert res["details"]["conflicts"][0]["nonterminal"] == "T"


def test_exact_duplicate_entries_are_not_a_conflict():
    entries = deepcopy(ENTRIES)
    entries["entries"].append(dict(entries["entries"][0]))
    assert _check(entries)["trust"] == "bounded_pass"


def test_table_disagreeing_with_the_oracle_is_refuted_with_counterexamples():
    wrong = deepcopy(TABLE)
    del wrong["T"]["a"]          # babcab (w = ba) is in L but now rejected
    res = _check(wrong)
    assert res["trust"] == "refuted"
    assert res["details"]["status"] == "mismatch"
    assert "babcab" in res["details"]["counterexamples"]
    assert all(m["in_language"] and not m["parser_accepts"] for m in res["details"]["mismatches"])


def test_table_accepting_a_word_outside_the_language_is_refuted():
    g = deepcopy(GRAMMAR)
    g["rules"].append({"lhs": "T", "rhs": []})
    wrong = deepcopy(TABLE)
    wrong["T"]["$"] = []          # now accepts "b" (w = ε without the c)
    res = _check(wrong, grammar=g)
    assert res["trust"] == "refuted"
    assert "b" in res["details"]["counterexamples"]


def test_production_outside_the_claimed_grammar_is_refuted():
    wrong = deepcopy(TABLE)
    wrong["T"]["c"] = ["c", "c"]
    res = _check(wrong)
    assert res["trust"] == "refuted" and res["details"]["status"] == "foreign_production"


@pytest.mark.parametrize("raw, fragment", [
    ({"S": {"ab": ["a"]}}, "longer than k"),
    ({"S": {"d": ["a"]}}, "not a task terminal"),
    ({"S": {"a": "aSa"}}, "list of symbols"),
    ({"X": {"a": ["a"]}}, "not a nonterminal"),
    ({"k": 2, "start": "S", "table": TABLE}, "does not certify"),
    ({"k": 1, "start": "T", "table": TABLE}, "differs from the grammar start"),
    ("S -> aSa", "must be an object"),
])
def test_malformed_table_is_refuted(raw, fragment):
    res = _check(raw)
    assert res["trust"] == "refuted"
    assert res["details"]["status"] == "malformed"
    assert fragment in res["details"]["error"]


def test_end_marker_only_pads_the_end_of_a_lookahead():
    res = _check({"S": {"$a": ["a", "S", "a"]}}, k=2)
    assert res["trust"] == "refuted"
    assert "after the end marker" in res["details"]["error"]


def test_table_for_a_smaller_k_certifies_the_claim():
    assert _check({"k": 1, "start": "S", "table": TABLE}, k=2)["trust"] == "bounded_pass"


def test_lookahead_shorter_than_k_means_end_of_input():
    # {a^n b^n}, LL(1) grammar used with k = 2: "b" and "" are the end-padded cells.
    g = {"nonterminals": ["S"], "terminals": ["a", "b"], "start": "S",
         "rules": [{"lhs": "S", "rhs": ["a", "S", "b"]}, {"lhs": "S", "rhs": []}]}
    raw = {"S": {"aa": ["a", "S", "b"], "ab": ["a", "S", "b"], "bb": [], "b": ["ε"], "": []}}
    table = normalize_parse_table(raw, k=2, grammar=g, terminals=["a", "b"])
    assert set(table.cells["S"]) == {"aa", "ab", "bb", "b$", "$$"}

    def anbn(w: str) -> bool:
        n = len(w) // 2
        return w == "a" * n + "b" * n

    res = check_parse_table(raw, k=2, grammar=g, terminals=["a", "b"], oracle_factory=lambda: anbn)
    assert res["trust"] == "bounded_pass"


def test_no_oracle_or_no_alphabet_is_well_formed():
    assert _check(TABLE, oracle=None)["trust"] == "well_formed"
    assert _check(TABLE, terminals=None)["trust"] == "well_formed"
    assert _check(TABLE, k=None)["trust"] == "well_formed"


def test_undecided_words_are_skipped_and_low_coverage_is_well_formed():
    assert PARSE_TABLE_MIN_WORDS == 30
    res = _check(TABLE, oracle=lambda w: None if len(w) > 2 else _oracle(w))
    assert res["trust"] == "well_formed"
    assert res["details"]["status"] == "low_coverage"
    assert res["details"]["words_undecided"] > 0


def test_non_halting_table_word_is_undecided_not_rejected():
    g = {"nonterminals": ["S"], "terminals": ["a"], "start": "S",
         "rules": [{"lhs": "S", "rhs": ["S", "a"]}, {"lhs": "S", "rhs": ["a"]}]}
    run = LLParser.from_table(g, {"S": {"a": ["S", "a"]}}, 1).parse("a", record_steps=False, max_steps=50)
    assert run["step_limit_exceeded"] is True and run["steps"] == []
    res = check_parse_table({"S": {"a": ["S", "a"]}}, k=1, grammar=g, terminals=["a"],
                            oracle_factory=lambda: (lambda w: len(w) >= 1))
    # every non-empty word loops; only "" is decided -> too few words, not refuted
    assert res["trust"] == "well_formed"


def test_normalize_requires_a_start_symbol_without_grammar():
    with pytest.raises(ParseTableFormatError):
        normalize_parse_table(TABLE, k=1, grammar=None, terminals=["a", "b", "c"])


# ---------------------------------------------------------------- claim verifier wiring

def test_builder_claim_trust_follows_the_table():
    ir = _ir()
    assert verify_ll_claim(_builder(TABLE), ir)["trust"] == "bounded_pass"
    missing = verify_ll_claim(_builder(None), ir)
    assert missing["trust"] == "well_formed"
    assert missing["details"]["parse_table_check"]["trust"] == "missing"
    # the grammar alone passes step 2: only the missing table caps it
    assert missing["details"]["language_equivalence"]["method"] == "word_oracle_equivalence"
    wrong = deepcopy(TABLE)
    del wrong["T"]["a"]
    assert verify_ll_claim(_builder(wrong), ir)["trust"] == "refuted"


def test_table_in_artifacts_is_read():
    agent = _builder(None)
    agent["artifacts"] = {"parse_table": TABLE}
    assert verify_ll_claim(agent, _ir())["trust"] == "bounded_pass"


def test_marker_claim_without_grammar_is_checked_on_its_table_alone():
    agent = {
        "agent_name": "marker_analyzer", "verdict": "ll",
        "proof_sketch": {
            "method": "marker_detection", "marker_symbol": "c", "suggested_k": 1,
            "marker_description": "c is the unique centre", "parse_table": ENTRIES,
        },
        "artifacts": {"ll_grammar": None},
    }
    assert verify_ll_claim(agent, _ir())["trust"] == "bounded_pass"
    agent["proof_sketch"]["parse_table"] = None
    assert verify_ll_claim(agent, _ir())["trust"] == "well_formed"
    bad = deepcopy(ENTRIES)
    bad["entries"] = [e for e in bad["entries"] if not (e["nonterminal"] == "T" and e["lookahead"] == "b")]
    agent["proof_sketch"]["parse_table"] = bad
    assert verify_ll_claim(agent, _ir())["trust"] == "refuted"


def test_transformer_claim_trust_follows_the_table():
    sketch = {
        "method": "grammar_transformation", "k": 1, "transformed_grammar": GRAMMAR,
        "transformation_steps": ["none needed"], "conflicts_remaining": [],
    }
    agent = {"agent_name": "grammar_transformer", "verdict": "ll", "proof_sketch": sketch}
    assert verify_ll_claim(agent, _ir())["trust"] == "well_formed"
    agent["proof_sketch"] = {**sketch, "parse_table": TABLE}
    assert verify_ll_claim(agent, _ir())["trust"] == "bounded_pass"


def test_retry_hint_carries_the_table_counterexamples():
    wrong = deepcopy(TABLE)
    del wrong["T"]["a"]
    cv = {"ll_grammar_builder": verify_ll_claim(_builder(wrong), _ir())}
    hint = _agent_retry_hint(cv, "ll_grammar_builder")
    assert hint["trust"] == "refuted"
    assert "babcab" in hint["counterexamples"]


# ---------------------------------------------------------------- mock E2E

def test_mock_pipeline_with_correct_tables_is_ll_bounded_pass():
    result = run_pipeline(_ir(), mock_runner=MockRunner(str(MOCK_DIR), "wbcwR"))
    assert result["verdict"] == "ll"
    assert result["confidence"] <= 0.85
    cv = result["claim_verification"]
    assert cv["ll_grammar_builder"]["trust"] == "bounded_pass"
    assert cv["marker_analyzer"]["trust"] == "bounded_pass"
    assert result["verdict_gate"]["confidence_cap"] == 0.85


def test_mock_pipeline_with_a_wrong_table_refutes_the_claim():
    result = run_pipeline(_ir(), mock_runner=MockRunner(str(MOCK_DIR), "wbcwR_bad_table"))
    cv = result["claim_verification"]["ll_grammar_builder"]
    assert cv["trust"] == "refuted"
    assert cv["details"]["parse_table_check"]["status"] == "mismatch"
    assert result["verdict"] == "uncertain"
    assert result["confidence"] <= 0.40
