"""P1: tools/reg.py -- regex_to_dfa, run_dfa, nerode_probe, dfa_vs_ir.

No LLM, no network, no Docker. The IRs are the repository's own example tasks
(agent_system/examples, cfl_system/examples, dcfl_system/examples,
ll_system/examples); the expected verdicts are those of docs/EVAL_SET.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tfl_deep_agent.tools import reg
from tfl_deep_agent.tools.base import REGISTRY, ToolInputError, load_groups
from tfl_deep_agent.tools.ir import ir_fingerprint
from tfl_deep_agent.tools.words import ClassOracle

ROOT = Path(__file__).resolve().parents[2]


def load(rel: str) -> dict[str, Any]:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def call(name: str, **args: Any):
    return REGISTRY.invoke(name, args)


# Real tasks used below.
REG04 = "agent_system/examples/eval/reg-04.json"            # regex (ab)*a|b -- regular
REG01 = "agent_system/examples/eval/reg-01.json"            # natural {a^n b^n} -- non-regular
REG12 = "agent_system/examples/eval/reg-12.json"            # S -> aS | Sb | eps = a*b* (trap)
REG13 = "agent_system/examples/eval/reg-13.json"            # S -> aSb | ab -- non-regular
TASK1 = "agent_system/examples/task1_palindrome_prefix_suffix.json"
TASK1_DFA = "agent_system/examples/task1_palindrome_prefix_suffix_dfa_builder_output.json"
TASK1_RE = "agent_system/examples/task1_palindrome_prefix_suffix_re_builder_output.json"
TASK3 = "agent_system/examples/task3_regex_backref.json"
CFL_ANBNCN = "cfl_system/examples/task_anbncn.json"
CFL_TASK11 = "cfl_system/examples/task11_ai_bj_between.json"
DCFL_ANBNCM = "dcfl_system/examples/task_anbncm.json"
DCFL_ASSB = "dcfl_system/examples/task_grammar_aSSb.json"
LL_F1 = "ll_system/examples/format1_anbn_union_ancn.json"
LL_F3 = "ll_system/examples/format3_simple_ll1.json"

# a*b* by a right-linear grammar (the regular language of reg-12, written linearly).
ASTAR_BSTAR_RIGHT_LINEAR = {
    "terminals": ["a", "b"], "nonterminals": ["S", "B"], "start": "S",
    "rules": [{"lhs": "S", "rhs": ["a", "S"]}, {"lhs": "S", "rhs": ["B"]},
              {"lhs": "B", "rhs": ["b", "B"]}, {"lhs": "B", "rhs": []}],
}


@pytest.fixture(scope="module")
def dfa_reg04() -> dict[str, Any]:
    res = call("regex_to_dfa", regex=load(REG04)["language_spec"]["pattern"])
    assert res.ok, res.error
    return res.data["dfa"]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_reg_group_registers_the_four_tools():
    load_groups(["reg"])
    assert set(REGISTRY.names("reg")) >= {"regex_to_dfa", "run_dfa", "nerode_probe", "dfa_vs_ir"}
    assert REGISTRY.get("regex_to_dfa").ceiling == "verified"
    assert REGISTRY.get("run_dfa").ceiling == "verified"
    assert REGISTRY.get("nerode_probe").ceiling == "bounded_pass"
    assert REGISTRY.get("dfa_vs_ir").ceiling == "bounded_pass"
    # Subset construction has no budget of its own: it runs in a killable process.
    assert REGISTRY.get("regex_to_dfa").timeout_mode == "process"


def test_langchain_tools_carry_schema_and_artifact(dfa_reg04):
    pytest.importorskip("langchain_core")
    tools = {t.name: t for t in REGISTRY.langchain_tools(groups=["reg"])}
    assert {"regex_to_dfa", "run_dfa", "nerode_probe", "dfa_vs_ir"} <= set(tools)
    for t in tools.values():
        assert "trust" in t.description.lower()
        props = t.args_schema.model_json_schema().get("properties", {})
        assert "trust" not in props and "confidence" not in props
    msg = tools["run_dfa"].invoke({
        "type": "tool_call", "id": "c1", "name": "run_dfa",
        "args": {"dfa": dfa_reg04, "words": ["a", "ab"]},
    })
    assert msg.artifact["trust"] == "verified"
    assert msg.artifact["data"]["accepted"] == ["a"]
    assert json.loads(msg.content)["tool"] == "run_dfa"


# ---------------------------------------------------------------------------
# regex_to_dfa
# ---------------------------------------------------------------------------

def test_regex_to_dfa_on_reg04_is_verified_and_minimal():
    res = call("regex_to_dfa", regex="(ab)*a|b")
    assert res.ok and res.trust == "verified"
    assert "not the task language" in res.trust_reason
    d = res.data
    assert d["minimal_complete"] is True
    # (ab)*a | b over {a, b}: classes ε, a/aba.., b, ab, dead -> 5 states.
    assert d["n_states"] == 5
    assert d["accepts_empty_word"] is False
    assert d["shortest_accepted"] in {"a", "b"}
    assert set(d["dfa"]) == {"states", "alphabet", "transitions", "start", "accept"}


def test_regex_to_dfa_alphabet_completion_and_epsilon():
    res = call("regex_to_dfa", regex="a*", alphabet=["a", "b"])
    assert res.ok and res.data["alphabet"] == ["a", "b"] and res.data["n_states"] == 2
    run = call("run_dfa", dfa=res.data["dfa"], words=["", "aaa", "ab", "b"])
    assert run.data["accepted"] == ["", "aaa"]
    eps = call("regex_to_dfa", regex="()", alphabet=["a"])
    assert eps.ok and eps.data["accepts_empty_word"] and eps.data["shortest_accepted"] == ""


def test_regex_to_dfa_from_right_linear_grammar_matches_reg12():
    res = call("regex_to_dfa", grammar=ASTAR_BSTAR_RIGHT_LINEAR)
    assert res.ok and res.trust == "verified" and res.data["grammar"] == "right-linear"
    # The DFA of the linear grammar agrees with the task grammar S -> aS | Sb | eps (CYK oracle).
    cmp = call("dfa_vs_ir", ir=load(REG12), dfa=res.data["dfa"])
    assert cmp.trust == "bounded_pass" and cmp.data["status"] == "pass"


def test_regex_to_dfa_negative_cases():
    # reg-12's own grammar is neither right- nor left-linear: no construction (not "non-regular").
    res = call("regex_to_dfa", grammar=load(REG12)["language_spec"])
    assert not res.ok and res.error_kind == "unsupported" and res.trust == "not_verified"
    assert "does not make" in res.error
    # task3: a back-reference is not regular syntax.
    res = call("regex_to_dfa", regex=load(TASK3)["language_spec"]["pattern"])
    assert not res.ok and res.error_kind == "invalid_input" and "back-reference" in res.error
    # Syntax the lib would silently misread as literals.
    for bad in ("[ab]*", "a.b", "a{3}", "a b", "ε|a"):
        res = call("regex_to_dfa", regex=bad)
        assert not res.ok and res.error_kind == "invalid_input", bad
    res = call("regex_to_dfa", regex="(ab")
    assert not res.ok and res.error_kind == "invalid_input"
    res = call("regex_to_dfa", regex="ab", grammar=ASTAR_BSTAR_RIGHT_LINEAR)
    assert not res.ok and res.error_kind == "invalid_input"
    res = call("regex_to_dfa", regex="abc", alphabet=["a", "b"])
    assert not res.ok and res.error_kind == "invalid_input"


def test_regex_to_dfa_state_limit():
    # (a|b)*a(a|b)^9: the minimal DFA needs 2^10 = 1024 states > MAX_DFA_STATES.
    res = call("regex_to_dfa", regex="(a|b)*a" + "(a|b)" * 9)
    assert not res.ok and res.error_kind == "input_too_large"


def test_regex_to_dfa_rejects_smuggled_trust():
    res = call("regex_to_dfa", regex="a", trust="verified")
    assert not res.ok and res.error_kind == "invalid_input"


# ---------------------------------------------------------------------------
# run_dfa
# ---------------------------------------------------------------------------

def test_run_dfa_on_example_dfa():
    dfa = load(TASK1_DFA)["evidence"]["dfa"]
    res = call("run_dfa", dfa=dfa, words=["", "aa", "ab", "bab", "abba", "babb"])
    assert res.ok and res.trust == "verified"
    acc = {r["word"]: r["accepted"] for r in res.data["results"]}
    assert acc == {"": False, "aa": True, "ab": False, "bab": False, "abba": False, "babb": True}
    assert res.data["partial_dfa"] is False
    assert all(r["final_state"] in dfa["states"] for r in res.data["results"])


def test_run_dfa_partial_dfa_rejects_missing_transitions():
    dfa = {"states": ["p", "q"], "alphabet": ["a", "b"],
           "transitions": {"p": {"a": "q"}, "q": {"a": "p"}}, "start": "p", "accept": ["q"]}
    res = call("run_dfa", dfa=dfa, words=["a", "ab", "aaa", "c"])
    assert res.ok and res.data["partial_dfa"] is True
    assert res.data["accepted"] == ["a", "aaa"]
    assert res.data["foreign_symbol_words"] == ["c"]
    assert [r["final_state"] for r in res.data["results"]] == ["q", None, "q", None]


@pytest.mark.parametrize("patch, needle", [
    ({"start": "zz"}, "Start state"),
    ({"accept": ["zz"]}, "Accept state"),
    ({"alphabet": ["ab", "b"]}, "single characters"),
    ({"transitions": {"p": {"a": "zz"}}}, "unknown state"),
    ({"transitions": {"p": {"c": "p"}}}, "outside the alphabet"),
])
def test_run_dfa_invalid_dfa(patch, needle):
    dfa = {"states": ["p"], "alphabet": ["a", "b"],
           "transitions": {"p": {"a": "p", "b": "p"}}, "start": "p", "accept": []}
    dfa.update(patch)
    res = call("run_dfa", dfa=dfa, words=["a"])
    assert not res.ok and res.error_kind == "invalid_input" and res.trust == "not_verified"
    assert needle in res.error


def test_run_dfa_limits():
    dfa = {"states": ["p"], "alphabet": ["a"], "transitions": {"p": {"a": "p"}},
           "start": "p", "accept": ["p"]}
    assert call("run_dfa", dfa=dfa, words=["a" * 101]).error_kind == "invalid_input"
    assert call("run_dfa", dfa=dfa, words=["a"] * 501).error_kind == "invalid_input"
    assert call("run_dfa", dfa=dict(dfa, verified=True), words=["a"]).error_kind == "invalid_input"


# ---------------------------------------------------------------------------
# dfa_vs_ir
# ---------------------------------------------------------------------------

def test_dfa_vs_ir_pass_is_bounded(dfa_reg04):
    res = call("dfa_vs_ir", ir=load(REG04), dfa=dfa_reg04)
    assert res.ok and res.trust == "bounded_pass"
    assert res.data["status"] == "pass" and res.data["counterexamples"] == []
    assert res.bounds.max_len == 10 and res.bounds.n_words == 2047 and res.bounds.n_unknown == 0
    assert res.data["ir_fingerprint"] == ir_fingerprint(load(REG04))
    assert res.data["oracle"]["exact"] is True
    assert "not an equivalence proof" in res.trust_reason


def test_dfa_vs_ir_refutes_the_example_dfa_and_regex():
    """task1's mock DFA / regex ("first two letters equal") are wrong: abba has the
    even palindrome prefix abba but neither starts nor ends with aa / bb."""
    ir = load(TASK1)
    res = call("dfa_vs_ir", ir=ir, dfa=load(TASK1_DFA)["evidence"]["dfa"])
    assert res.ok and res.trust == "refuted" and res.data["status"] == "fail"
    ce = res.data["counterexamples"][0]
    assert ce == {"word": "abba", "oracle_says": True, "dfa_says": False}
    built = call("regex_to_dfa", regex=load(TASK1_RE)["evidence"]["regex"])
    assert built.trust == "verified"  # exact for L(regex) ...
    res2 = call("dfa_vs_ir", ir=ir, dfa=built.data["dfa"])
    assert res2.trust == "refuted"    # ... but L(regex) is not the task language
    assert res2.data["counterexamples"][0]["word"] == "abba"


def test_dfa_vs_ir_refutes_regular_guess_for_reg13():
    plus = call("regex_to_dfa", regex="a+b+").data["dfa"]
    res = call("dfa_vs_ir", ir=load(REG13), dfa=plus)
    assert res.trust == "refuted"
    ce = res.data["counterexamples"][0]
    assert len(ce["word"]) == 3 and ce["oracle_says"] is False and ce["dfa_says"] is True


def test_dfa_vs_ir_other_class_oracles():
    # LL Format 3: S -> aAb | bBa, A -> aA | eps, B -> bB | eps is the regular aa*b | bb*a.
    dfa = call("regex_to_dfa", regex="aa*b|bb*a").data["dfa"]
    res = call("dfa_vs_ir", cls="ll", ir=load(LL_F3), dfa=dfa)
    assert res.trust == "bounded_pass" and res.data["alphabet"] == ["a", "b"]
    # DCFL grammar task (exam_04): a regular over-approximation is refuted.
    star = call("regex_to_dfa", regex="(a|b)*").data["dfa"]
    res = call("dfa_vs_ir", cls="dcfl", ir=load(DCFL_ASSB), dfa=star)
    assert res.trust == "refuted" and res.data["counterexamples"][0]["word"] == ""
    # CFL exponent-notation oracle.
    res = call("dfa_vs_ir", cls="cfl", ir=load(CFL_ANBNCN), dfa=call(
        "regex_to_dfa", regex="()", alphabet=["a", "b", "c"]).data["dfa"])
    assert res.trust == "refuted" and res.data["counterexamples"][0]["word"] == "abc"


def test_dfa_vs_ir_few_words_is_only_well_formed(dfa_reg04):
    res = call("dfa_vs_ir", ir=load(REG04), dfa=dfa_reg04, max_len=2)
    assert res.ok and res.trust == "well_formed" and res.bounds.n_words == 7


def test_dfa_vs_ir_unknown_answers_are_not_rejections(monkeypatch, dfa_reg04):
    """VERDICT_POLICY §4: None is skipped, never read as 'not in L'."""
    import re as _re

    pattern = _re.compile(r"^(?:(ab)*a|b)$")

    def fake_oracle_for(fn):
        # The shared dispatch's ClassOracle, around a scripted membership function.
        return lambda cls, ir: ClassOracle(cls, ("a", "b"), "test", True, fn)

    # Unknown for every word of length >= 4; decisive and correct below.
    monkeypatch.setattr(reg, "oracle_for", fake_oracle_for(
        lambda w: None if len(w) >= 4 else bool(pattern.match(w))))
    res = call("dfa_vs_ir", ir=load(REG04), dfa=dfa_reg04, max_len=6)
    assert res.trust == "well_formed"  # only 15 decided words
    assert res.bounds.n_words == 15 and res.bounds.n_unknown == 127 - 15
    # A DFA accepting everything is still refuted on a decided word only.
    every = call("regex_to_dfa", regex="(a|b)*").data["dfa"]
    res = call("dfa_vs_ir", ir=load(REG04), dfa=every, max_len=6)
    assert res.trust == "refuted" and res.data["counterexamples"][0]["word"] == ""
    monkeypatch.setattr(reg, "oracle_for", fake_oracle_for(lambda w: None))
    res = call("dfa_vs_ir", ir=load(REG04), dfa=every, max_len=3)
    assert res.ok and res.trust == "not_verified" and res.data["status"] == "inconclusive"


def test_dfa_vs_ir_negative_inputs(dfa_reg04):
    # No reg oracle for a natural-language spec; the cfl task11 text has none either.
    res = call("dfa_vs_ir", ir=load(REG01), dfa=dfa_reg04)
    assert not res.ok and res.error_kind == "unsupported"
    res = call("dfa_vs_ir", cls="cfl", ir=load(CFL_TASK11), dfa=dfa_reg04)
    assert not res.ok and res.error_kind == "unsupported"
    # No alphabet in the IR: no alphabet is guessed.
    ir = load(REG04)
    del ir["language_spec"]["alphabet"]
    res = call("dfa_vs_ir", ir=ir, dfa=dfa_reg04)
    assert not res.ok and res.trust == "not_verified" and "alphabet" in res.error
    # An IR the class validator rejects.
    res = call("dfa_vs_ir", ir={"task_type": "classify"}, dfa=dfa_reg04)
    assert not res.ok and res.error_kind == "invalid_input"
    # Enumeration limit (sum 2^i up to 20 > 20000 words).
    res = call("dfa_vs_ir", ir=load(REG04), dfa=dfa_reg04, max_len=20)
    assert not res.ok and res.error_kind == "input_too_large"


# ---------------------------------------------------------------------------
# nerode_probe
# ---------------------------------------------------------------------------

def test_nerode_family_reg13_is_bounded_pass():
    res = call("nerode_probe", ir=load(REG13),
               family={"prefix": "a^n", "suffix": "b^n", "values": [1, 2, 3, 4, 5, 6]})
    assert res.ok and res.trust == "bounded_pass"
    assert res.data["pairs_checked"] == 15 == res.data["pairs_separated"]
    assert res.data["family_variable"] == "n"
    assert "not an infinite index" in res.trust_reason
    first = res.data["results"][0]
    assert (first["u"], first["v"], first["suffix"]) == ("a", "aa", "b")
    assert first["u_suffix_in_L"] is True and first["v_suffix_in_L"] is False
    assert res.bounds.n_pairs == 15 and res.bounds.max_len == 11


def test_nerode_family_on_cfl_dcfl_ll_oracles():
    res = call("nerode_probe", cls="cfl", ir=load(CFL_ANBNCN),
               family={"prefix": "a^n", "suffix": "b^n c^n", "values": [0, 1, 2, 3]})
    assert res.trust == "bounded_pass"
    assert res.data["oracle"]["source"] == "cfl_system.lib.exponent_pattern"
    res = call("nerode_probe", cls="dcfl", ir=load(DCFL_ANBNCM),
               family={"prefix": "a^n", "suffix": "b^n c", "values": [1, 2, 3, 4, 5]})
    assert res.trust == "bounded_pass" and res.data["pairs_separated"] == 10
    # Format 2: S -> SabS | Sc | eps (CYK oracle, exact).
    res = call("nerode_probe", cls="ll", ir=load("ll_system/examples/format2_SabS_Sc_eps.json"),
               pairs=[{"u": "", "v": "a", "suffix": "b"}])
    assert res.trust == "bounded_pass" and res.data["oracle"]["exact"] is True


def test_nerode_approximate_oracle_is_only_well_formed():
    """LL Format 1 a^n X^n with X in {b, c}: the 'same n' constraint is a comment the
    ll template oracle ignores, so its answers are approximate (words.oracle_for)."""
    res = call("nerode_probe", cls="ll", ir=load(LL_F1),
               family={"prefix": "a^n", "suffix": "c^n", "values": [1, 2, 3, 4]})
    assert res.ok and res.data["oracle"]["exact"] is False
    assert res.trust == "well_formed" and "approximate" in res.trust_reason
    dfa = call("regex_to_dfa", regex="(a|b|c)*").data["dfa"]
    res = call("dfa_vs_ir", cls="ll", ir=load(LL_F1), dfa=dfa)
    assert res.data["status"] == "fail" and res.trust == "well_formed"  # not refuted


def test_nerode_claimed_suffix_that_does_not_separate_is_refuted():
    res = call("nerode_probe", cls="dcfl", ir=load(DCFL_ANBNCM),
               family={"prefix": "a^n", "suffix": "c", "values": [1, 2, 3]})
    assert res.ok and res.trust == "refuted"
    first = res.data["results"][0]
    assert first["status"] == "claimed_suffix_fails"
    assert first["u_suffix_in_L"] is False and first["v_suffix_in_L"] is False
    assert first["alternative_suffix"] == "bc"  # a real separator, to fix the proof
    # u = v can never be separated.
    res = call("nerode_probe", ir=load(REG13), pairs=[{"u": "aa", "v": "aa", "suffix": "bb"}])
    assert res.trust == "refuted"


def test_nerode_pair_search_including_empty_suffix():
    ir = load(TASK1)
    res = call("nerode_probe", ir=ir, pairs=[{"u": "", "v": "aa"}])
    assert res.trust == "bounded_pass"
    assert res.data["results"][0]["status"] == "distinguished"
    assert res.data["results"][0]["suffix"] == ""  # the empty suffix is allowed (§4)
    # aa and bb are both followed by anything in L: no suffix up to the bound -> not_verified.
    res = call("nerode_probe", ir=ir, pairs=[{"u": "", "v": "aa"}, {"u": "aa", "v": "bb"}])
    assert res.ok and res.trust == "not_verified"
    second = res.data["results"][1]
    assert second["status"] == "no_suffix_found" and "NOT prove" in second["note"]
    assert second["searched_up_to"] == res.data["max_suffix_len"] == 9


def test_nerode_unknown_oracle_answers_leave_the_pair_open():
    # The exponent-notation oracle answers None above length 40: a^14 b^14 c^14 is unknown.
    res = call("nerode_probe", cls="cfl", ir=load(CFL_ANBNCN),
               family={"prefix": "a^n", "suffix": "b^n c^n", "values": [2, 14],
                       "suffix_at": "larger"})
    assert res.ok and res.trust == "not_verified"
    rec = res.data["results"][0]
    assert rec["status"] == "unknown" and rec["v_suffix_in_L"] is None
    assert res.bounds.n_unknown == 1


def test_nerode_estimate_is_heuristic_only():
    res = call("nerode_probe", ir=load(REG04), estimate_depth=5)
    assert res.ok and res.trust == "well_formed"
    est = res.data["estimate"]
    assert est["status"] == "ok" and est["classes_found"] == 5 and est["looks_infinite"] is False
    # With pairs, the estimate never raises the trust above what the pairs give.
    res = call("nerode_probe", ir=load(REG04), estimate_depth=3, pairs=[{"u": "a", "v": "b"}])
    assert res.trust == "bounded_pass"  # from the pair (a·ba in L, b·ba not), not from the estimate
    assert res.data["results"][0]["suffix"] == "ba"


def test_nerode_negative_inputs():
    # No reg oracle for a natural spec.
    res = call("nerode_probe", ir=load(REG01), pairs=[{"u": "a", "v": "aa"}])
    assert not res.ok and res.error_kind == "unsupported"
    bad_families = [
        {"prefix": "a^n b^m", "values": [1, 2]},                 # two variables
        {"prefix": "a^n", "suffix": "b^(n**9**9)", "values": [1, 2]},  # no powers, no eval
        {"prefix": "x^n", "values": [1, 2]},                     # outside the alphabet
        {"prefix": "ab", "values": [1, 2]},                      # no variable
        {"prefix": "a^(n-3)", "values": [1, 2]},                 # negative exponent
    ]
    for fam in bad_families:
        res = call("nerode_probe", ir=load(REG13), family=fam)
        assert not res.ok and res.error_kind == "invalid_input", fam
    res = call("nerode_probe", ir=load(REG13), family={"prefix": "a^(10n)", "values": [1, 20]})
    assert not res.ok and res.error_kind == "input_too_large"
    res = call("nerode_probe", ir=load(REG13))
    assert not res.ok and res.error_kind == "invalid_input"
    res = call("nerode_probe", ir=load(REG13), pairs=[{"u": "c", "v": "a"}])
    assert not res.ok and res.error_kind == "invalid_input"
    res = call("nerode_probe", ir=load(REG13), pairs=[{"u": "a", "v": "b", "trust": "verified"}])
    assert not res.ok and res.error_kind == "invalid_input"
    res = call("nerode_probe", ir=load(REG13), pairs=[{"u": "a", "v": "b"}], max_suffix_len=12)
    assert not res.ok and res.error_kind == "input_too_large"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_pattern_instantiation():
    blocks, variables = reg._parse_pattern("a^(2n+1) (ab)^n c^{n-1} b^2n", ["a", "b", "c"])
    assert variables == {"n"}
    assert reg._instantiate(blocks, "n", 2, 100) == "aaaaa" + "abab" + "c" + "bbbb"
    blocks, _ = reg._parse_pattern("a^nb^n", ["a", "b"])
    assert reg._instantiate(blocks, "n", 3, 100) == "aaabbb"
    with pytest.raises(ToolInputError):
        reg._parse_pattern("a^n+1", ["a"])


@pytest.mark.parametrize(("regex", "text", "found"), [
    ("((ab)*a|b)", "задан регулярным выражением (ab)*a | b.", True),   # live run df4a3b9626cb
    ("(ab)*a|b", "задан регулярным выражением ((ab)*a | b).", True),
    ("(((a|b)))", "выражение a|b", True),
    ("(a)(b)", "выражение a)(b", False),         # the outer pair is not one group
    ("(a|b)*", "выражение a|b", False),          # a starred group is not outer parentheses
    ("(a\)", "выражение a\\", False),           # an escaped ) does not close the group
    ("((ab)*a|b)", "язык слов с равным числом a и b", False),
])
def test_definition_in_source_text_ignores_outer_parentheses(regex: str, text: str, found: bool) -> None:
    assert reg._definition_in_source_text({"source_text": text}, regex, None) is found
