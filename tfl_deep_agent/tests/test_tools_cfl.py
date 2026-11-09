"""P1: tools/cfl.py -- to_cnf, cyk, pda_run, parikh, pump_check_cfl, grammar_vs_ir,
input_grammar_cfg.

Real example tasks from agent_system/, cfl_system/, dcfl_system/, ll_system/
examples and the ticket-50 golden run. No LLM, no network, no Docker. The
grammar-language checks use an independent fixed-point enumerator below, not
the lib's own CYK."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from cfl_system.lib.cnf import is_cnf
from tfl_deep_agent.tools import cfl as cfl_tools  # noqa: F401  (registers the group)
from tfl_deep_agent.tools.base import GROUP_MAX_TRUST, REGISTRY, ToolResult, trust_rank

REPO = Path(__file__).resolve().parents[2]
TOOLS = ("to_cnf", "cyk", "pda_run", "parikh", "pump_check_cfl", "grammar_vs_ir", "input_grammar_cfg")


def load(rel: str) -> dict[str, Any]:
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def call(name: str, args: dict[str, Any]) -> ToolResult:
    return REGISTRY.invoke(name, args)


def words_upto(alphabet: str, n: int) -> list[str]:
    return ["".join(p) for k in range(n + 1) for p in itertools.product(alphabet, repeat=k)]


def brute_language(grammar: dict[str, Any], n: int) -> set[str]:
    """All words of L(G) of length <= n: least fixed point of
    L_A = U_{A -> X1..Xk} L_X1 ... L_Xk, cut at length n (an independent check)."""
    terms = set(grammar["terminals"])
    lang: dict[str, set[str]] = {a: set() for a in grammar["nonterminals"]}
    changed = True
    while changed:
        changed = False
        for r in grammar["rules"]:
            acc = {""}
            for sym in r["rhs"]:
                part = {sym} if sym in terms else lang[sym]
                acc = {x + y for x in acc for y in part if len(x) + len(y) <= n}
                if not acc:
                    break
            new = acc - lang[r["lhs"]]
            if new:
                lang[r["lhs"]] |= new
                changed = True
    return lang[grammar["start"]]


# (grammar, alphabet, enumeration length) from the four pipelines' examples
GRAMMARS = {
    "agent/task2_grammar_sasb": (load("agent_system/examples/task2_grammar_sasb.json")["language_spec"], 7),
    "agent/task_grammar_bb_aA": (load("agent_system/examples/task_grammar_bb_aA.json")["language_spec"], 4),
    "cfl/grammar_filter_49.grammar": (
        load("cfl_system/examples/task_grammar_filter_49.json")["language_spec"]["grammar"], 7),
    "dcfl/task_grammar_aSSb": (load("dcfl_system/examples/task_grammar_aSSb.json")["language_spec"], 7),
    "ll/format3_simple_ll1": (load("ll_system/examples/format3_simple_ll1.json")["grammar"], 7),
    "ll/task_SabS_cSc_a": (load("ll_system/examples/task_SabS_cSc_a.json")["language_spec"], 4),
}

TICKET50_IR = load("docs/examples/cfl_ticket50_w1bw2w3_lean_proved/input.json")
ANBNCN_IR = load("cfl_system/examples/task_anbncn.json")
LL_FORMAT2_IR = load("ll_system/examples/format2_SabS_Sc_eps.json")
ANBN_IR = {"task_type": "classify_cfl", "source_text": "a^n b^n", "language_spec": {
    "kind": "grammar", "alphabet": ["a", "b"], "terminals": ["a", "b"], "nonterminals": ["S"],
    "start": "S", "rules": [{"lhs": "S", "rhs": ["a", "S", "b"]}, {"lhs": "S", "rhs": []}]}}


def _find_agent(obj: Any, agent: str) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        if obj.get("agent") == agent and isinstance(obj.get("evidence"), dict):
            return obj
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find_agent(child, agent)
        if found:
            return found
    return None


TICKET50_PUMPING = _find_agent(load("docs/examples/cfl_ticket50_w1bw2w3_lean_proved/result.json"),
                               "pumping_cfl")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_group_registered_with_policy_ceilings():
    assert set(TOOLS) <= set(REGISTRY.names("cfl"))
    ceilings = {name: REGISTRY.get(name).ceiling for name in TOOLS}
    assert ceilings == {"to_cnf": "well_formed", "cyk": "verified", "pda_run": "verified",
                        "parikh": "well_formed", "pump_check_cfl": "well_formed",
                        "grammar_vs_ir": "bounded_pass", "input_grammar_cfg": "verified"}
    for name in TOOLS:
        assert trust_rank(ceilings[name]) <= trust_rank(GROUP_MAX_TRUST["cfl"])


@pytest.mark.parametrize("name", TOOLS)
def test_every_argument_is_described_for_the_model(name):
    spec = REGISTRY.get(name)
    assert len(spec.description) > 80
    schema = spec.args_schema.model_json_schema()
    for field, prop in schema["properties"].items():
        assert prop.get("description"), f"{name}.{field} has no description"


@pytest.mark.parametrize("name", TOOLS)
def test_model_cannot_pass_trust(name):
    r = call(name, {"trust": "verified"})
    assert not r.ok and r.error_kind == "invalid_input" and r.trust == "not_verified"


# ---------------------------------------------------------------------------
# to_cnf
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(GRAMMARS))
def test_to_cnf_keeps_the_language(key):
    grammar, n = GRAMMARS[key]
    r = call("to_cnf", {"grammar": grammar})
    assert r.ok and r.trust == "well_formed", r.error
    cnf = r.data["cnf"]
    assert is_cnf(cnf) and r.data["output_is_cnf"]
    assert brute_language(cnf, n) == brute_language(grammar, n)
    assert r.data["generates_epsilon"] == ("" in brute_language(grammar, 0))
    assert len(r.data["grammar_sha256"]) == 16


def test_to_cnf_reads_epsilon_token_and_ignores_kind():
    g = {"kind": "grammar", "terminals": ["a", "b"], "nonterminals": ["S"], "start": "S",
         "rules": [{"lhs": "S", "rhs": ["a", "S", "b"]}, {"lhs": "S", "rhs": ["ε"]}]}
    r = call("to_cnf", {"grammar": g})
    assert r.ok and r.data["generates_epsilon"]
    assert any("as ε" in n for n in r.data["notes"])
    assert brute_language(r.data["cnf"], 6) == {"", "ab", "aabb", "aaabbb"}


@pytest.mark.parametrize("mutate, kind", [
    (lambda g: g.update(rules=[{"lhs": "S", "rhs": ["a"]}] * 301), "invalid_input"),  # > 300 rules
    (lambda g: g.update(rules=[{"lhs": "S", "rhs": ["a"] * 25}]), "invalid_input"),   # rhs > 24
])
def test_to_cnf_rejects_bad_grammars(mutate, kind):
    g = json.loads(json.dumps(GRAMMARS["agent/task2_grammar_sasb"][0]))
    mutate(g)
    r = call("to_cnf", {"grammar": g})
    assert not r.ok and r.error_kind == kind and r.trust == "not_verified"


@pytest.mark.parametrize("tool, extra", [("to_cnf", {}), ("cyk", {"words": ["ab"]}),
                                         ("parikh", {"max_length": 4})])
@pytest.mark.parametrize("mutate, needle", [
    (lambda g: g.update(start="X"), "Start symbol"),                                 # start not declared
    (lambda g: g.update(nonterminals=g["nonterminals"] + ["a"]), "overlap"),         # overlap
    (lambda g: g["rules"].append({"lhs": "S", "rhs": ["z"]}), "'z'"),                # undeclared
])
def test_invalid_submitted_grammar_is_refuted(tool, extra, mutate, needle):
    """VERDICT_POLICY §1: invalid grammar symbols are a refuted signal (same rule as
    dpda_run and the ll group), not a technical failure."""
    g = json.loads(json.dumps(GRAMMARS["agent/task2_grammar_sasb"][0]))
    mutate(g)
    r = call(tool, {"grammar": g, **extra})
    assert r.ok and r.trust == "refuted", r
    assert r.data["valid"] is False and r.data["evidence_role"] == cfl_tools.INVALID_ARTIFACT
    assert any(needle in e for e in r.data["validation_errors"])


# ---------------------------------------------------------------------------
# cyk
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(GRAMMARS))
def test_cyk_matches_independent_enumeration(key):
    grammar, n = GRAMMARS[key]
    words = words_upto("".join(sorted(grammar["terminals"])), n)[:200]
    n_max = max(map(len, words))
    truth = brute_language(grammar, n_max)
    r = call("cyk", {"grammar": grammar, "words": words})
    assert r.ok and r.trust == "verified", r.error
    assert r.data["membership"] == {w: w in truth for w in words}
    assert not r.data["mismatches"] and not r.data["unchecked"]


def test_cyk_contradicted_claim_is_refuted_and_kept_claims_verified():
    g = GRAMMARS["agent/task2_grammar_sasb"][0]   # S -> SaSb | ε | A, A -> bb | aa | bSb
    ok = call("cyk", {"grammar": g, "words": ["", "ab", "ba"], "expected": {"ab": True, "ba": False}})
    assert ok.trust == "verified"
    bad = call("cyk", {"grammar": g, "words": ["ab", "ba"], "expected": {"ba": True}})
    assert bad.ok and bad.trust == "refuted"
    assert bad.data["mismatches"] == [{"word": "ba", "expected": True, "actual": False}]


class _OneWordDeadline:
    """Expires after the first check: the remaining words are 'not checked (time limit)'."""

    def __init__(self, timeout_s):
        self.calls = 0

    def expired(self):
        self.calls += 1
        return self.calls > 1


def test_cyk_unchecked_expected_word_is_not_verified(monkeypatch):
    """Regression: a claimed answer cut by the time limit was folded into 'verified'."""
    monkeypatch.setattr(cfl_tools, "Deadline", _OneWordDeadline)
    g = GRAMMARS["agent/task2_grammar_sasb"][0]
    words = ["", "ab", "aabb"]
    r = call("cyk", {"grammar": g, "words": words, "expected": {"aabb": True}})
    assert r.ok and r.trust == "not_verified"
    assert r.data["unchecked_expected"] == ["aabb"] and r.data["unchecked"] == ["ab", "aabb"]
    # the same cut with no claim on the unchecked words keeps the decided answers
    r2 = call("cyk", {"grammar": g, "words": words, "expected": {"": True}})
    assert r2.trust != "not_verified" and "unchecked_expected" not in r2.data


def test_cyk_table_for_a_short_word():
    g = load("dcfl_system/examples/task_grammar_aSSb.json")["language_spec"]
    r = call("cyk", {"grammar": g, "words": ["aabb"], "table_word": "aabb"})
    assert r.data["membership"] == {"aabb": True}
    full = [c for c in r.data["table"]["cells"] if c["start"] == 0 and c["length"] == 4]
    assert full and r.data["table"]["start_symbol"] in full[0]["nonterminals"]


def test_cyk_on_an_ir_grammar_with_an_empty_language():
    g = {"terminals": ["a"], "nonterminals": ["S"], "start": "S",
         "rules": [{"lhs": "S", "rhs": ["a", "S"]}]}          # no terminating rule
    r = call("cyk", {"grammar": g, "words": ["", "a", "aa"]})
    assert r.trust == "verified" and r.data["accepted"] == []


@pytest.mark.parametrize("args, kind", [
    ({"words": ["a" * 101]}, "invalid_input"),                     # word longer than 100
    ({"words": ["a"] * 201}, "invalid_input"),                     # more than 200 words
    ({"words": ["ab"], "expected": {"ba": True}}, "invalid_input"),  # claim about an absent word
    ({"words": ["ab"], "table_word": "a" * 13}, "invalid_input"),
])
def test_cyk_limits(args, kind):
    r = call("cyk", {"grammar": GRAMMARS["agent/task2_grammar_sasb"][0], **args})
    assert not r.ok and r.error_kind == kind


def test_cyk_refuses_multi_character_terminals():
    g = {"terminals": ["ab", "c"], "nonterminals": ["S"], "start": "S",
         "rules": [{"lhs": "S", "rhs": ["ab", "S"]}, {"lhs": "S", "rhs": ["c"]}]}
    r = call("cyk", {"grammar": g, "words": ["abc"]})
    assert not r.ok and r.error_kind == "unsupported"


# ---------------------------------------------------------------------------
# pda_run
# ---------------------------------------------------------------------------

# a^n b^n (n >= 0), textbook push order (top first), final-state acceptance
ANBN_PDA = {
    "states": ["q0", "q1", "qf"], "input_alphabet": ["a", "b"], "stack_alphabet": ["Z", "A"],
    "start_state": "q0", "start_stack": "Z", "accept_states": ["qf"],
    "transitions": [
        {"from": "q0", "input": "a", "stack_top": "Z", "to": "q0", "push": ["A", "Z"]},
        {"from": "q0", "input": "a", "stack_top": "A", "to": "q0", "push": ["A", "A"]},
        {"from": "q0", "input": None, "stack_top": "Z", "to": "qf", "push": ["Z"]},
        {"from": "q0", "input": "b", "stack_top": "A", "to": "q1", "push": []},
        {"from": "q1", "input": "b", "stack_top": "A", "to": "q1", "push": []},
        {"from": "q1", "input": None, "stack_top": "Z", "to": "qf", "push": ["Z"]},
    ],
}


def _anbn(w: str) -> bool:
    n = len(w) // 2
    return w == "a" * n + "b" * n


def test_pda_run_exact_on_a_hand_built_pda():
    words = words_upto("ab", 5) + ["aaabbb", "aaaabbbb", "aaabbbb"]
    r = call("pda_run", {"pda": ANBN_PDA, "words": words})
    assert r.ok and r.trust == "verified", r.error
    assert r.data["accepted"] == {w: _anbn(w) for w in words}
    assert all(run["exact"] for run in r.data["runs"].values())


def test_pda_run_push_orders_agree():
    flipped = {**ANBN_PDA, "transitions": [{**t, "push": list(reversed(t["push"]))}
                                           for t in ANBN_PDA["transitions"]]}
    words = words_upto("ab", 5)
    a = call("pda_run", {"pda": ANBN_PDA, "words": words})
    b = call("pda_run", {"pda": flipped, "words": words, "push_order": "top_last"})
    assert a.data["accepted"] == b.data["accepted"]
    wrong = call("pda_run", {"pda": flipped, "words": words})   # misread order
    assert wrong.data["accepted"] != a.data["accepted"]


def test_pda_run_on_the_live_task11_pda():
    """The pda_builder output of a live cfl run for {a^i b^j | j < i < 2j}."""
    try:
        pda = load("cfl_system/examples/live_outputs/task11_ai_bj_between_result.json")
    except FileNotFoundError:
        pytest.skip("live_outputs is gitignored: this run's output exists only on the machine that made it")
    holder = [pda]
    while holder:
        node = holder.pop()
        if isinstance(node, dict) and isinstance(node.get("pda"), dict):
            pda = node["pda"]
            break
        holder.extend(node.values() if isinstance(node, dict) else
                      node if isinstance(node, list) else [])
    words = ["a" * i + "b" * j for i in range(8) for j in range(8)] + ["ba", "abab"]
    expected = {w: (w == "a" * w.count("a") + "b" * w.count("b")
                    and w.count("b") < w.count("a") < 2 * w.count("b")) for w in words}
    r = call("pda_run", {"pda": pda, "words": words, "expected": expected})
    assert r.trust == "verified" and not r.data["mismatches"]
    wrong = call("pda_run", {"pda": pda, "words": ["aaabb"], "expected": {"aaabb": False}})
    assert wrong.trust == "refuted"


def test_pda_run_stack_cap_gives_bounded_pass():
    loop = {"states": ["q", "f"], "input_alphabet": ["a"], "stack_alphabet": ["Z", "A"],
            "start_state": "q", "start_stack": "Z", "accept_states": ["f"],
            "transitions": [
                {"from": "q", "input": None, "stack_top": "Z", "to": "q", "push": ["A", "Z"]},
                {"from": "q", "input": None, "stack_top": "A", "to": "q", "push": ["A", "A"]}]}
    r = call("pda_run", {"pda": loop, "words": ["a"]})
    assert r.trust == "bounded_pass" and r.bounds.truncated
    assert r.data["runs"]["a"] == {**r.data["runs"]["a"], "accepted": False, "exact": False}
    # an inexact rejection cannot refute a claimed acceptance -- nor verify it
    r2 = call("pda_run", {"pda": loop, "words": ["a"], "expected": {"a": True}})
    assert r2.trust == "not_verified" and not r2.data["mismatches"]
    assert r2.data["open_expected"] == ["a"]
    # a claimed rejection is bounded by the same cap
    r3 = call("pda_run", {"pda": loop, "words": ["a"], "expected": {"a": False}})
    assert r3.trust == "bounded_pass" and not r3.data["mismatches"]


def test_pda_run_step_budget_is_unknown_not_rejected():
    branching = {"states": ["q", "f"], "input_alphabet": ["a"], "stack_alphabet": ["Z", "A", "B"],
                 "start_state": "q", "start_stack": "Z", "accept_states": ["f"],
                 "transitions": [{"from": "q", "input": None, "stack_top": s, "to": "q",
                                  "push": [t, s]} for s in "ZAB" for t in "AB"]}
    r = call("pda_run", {"pda": branching, "words": ["a"], "expected": {"a": False}})
    assert r.ok and r.trust == "not_verified"
    assert r.data["unknown"] == ["a"] and r.data["accepted"] == {} and r.bounds.n_unknown == 1


@pytest.mark.parametrize("pda, kind", [
    ({"states": ["q"]}, "invalid_input"),                                     # missing keys
    ({**ANBN_PDA, "input_alphabet": ["a", "bb"],
      "transitions": [{**ANBN_PDA["transitions"][0]}]}, "unsupported"),       # multi-char input
    ({**ANBN_PDA, "transitions": [ANBN_PDA["transitions"][0]] * 601}, "input_too_large"),
])
def test_pda_run_rejects_bad_pdas(pda, kind):
    r = call("pda_run", {"pda": pda, "words": ["ab"]})
    assert not r.ok and r.error_kind == kind and r.trust == "not_verified"


@pytest.mark.parametrize("pda, needle", [
    ({**ANBN_PDA, "start_stack": "X"}, "start_stack"),
    ({**ANBN_PDA, "start_state": "nope"}, "nope"),
    ({**ANBN_PDA, "transitions": [{**ANBN_PDA["transitions"][0], "input": "c"}]}, "'c'"),
])
def test_pda_run_invalid_pda_is_refuted_with_the_validation_errors(pda, needle):
    """Undeclared states / symbols in a submitted PDA: refuted (VERDICT_POLICY §1), as dpda_run."""
    r = call("pda_run", {"pda": pda, "words": ["ab"]})
    assert r.ok and r.trust == "refuted"
    assert r.data["evidence_role"] == cfl_tools.INVALID_ARTIFACT
    assert any(needle in e for e in r.data["validation_errors"])


# ---------------------------------------------------------------------------
# parikh
# ---------------------------------------------------------------------------

def test_parikh_words_mode():
    r = call("parikh", {"words": ["aabbc", "", "cab"], "alphabet": ["c", "b", "a"]})
    assert r.ok and r.trust == "well_formed"
    assert r.data["vectors"] == {"aabbc": [1, 2, 2], "": [0, 0, 0], "cab": [1, 1, 1]}
    r2 = call("parikh", {"words": ["ba"]})
    assert r2.data["alphabet"] == ["a", "b"] and r2.data["vectors"] == {"ba": [1, 1]}


def test_parikh_grammar_mode_samples_true_members():
    g, _ = GRAMMARS["agent/task2_grammar_sasb"]
    r = call("parikh", {"grammar": g, "max_length": 6})
    assert r.ok and r.trust == "well_formed", r.error
    truth = {(w.count("a"), w.count("b")) for w in brute_language(g, 6)}
    sampled = {tuple(v) for v in r.data["vectors_sampled"]}
    assert sampled and sampled <= truth
    assert r.bounds.max_len == 6 and r.data["sample_complete"] is False


def test_parikh_ir_mode_never_concludes():
    r = call("parikh", {"ir": load("cfl_system/examples/task_grammar_filter_49.json")})
    assert r.ok and r.trust == "well_formed" and r.data["conclusion"] is None
    assert r.data["evidence_scope"] == "finite_sample"
    # Parikh's theorem for an explicit CFG: still diagnostics only
    g = call("parikh", {"ir": load("dcfl_system/examples/task_grammar_aSSb.json")})
    assert g.data["is_semilinear"] is True and g.trust == "well_formed"


@pytest.mark.parametrize("args, kind", [
    ({"words": ["abc"], "alphabet": ["a", "b"]}, "invalid_input"),   # c outside the alphabet
    ({"words": ["ab"], "grammar": GRAMMARS["agent/task2_grammar_sasb"][0]}, "invalid_input"),
    ({}, "invalid_input"),
    ({"alphabet": ["a"], "ir": ANBNCN_IR}, "invalid_input"),
    ({"grammar": GRAMMARS["agent/task2_grammar_sasb"][0], "max_length": 16}, "invalid_input"),
    ({"ir": ANBNCN_IR}, "unsupported"),                               # kind natural
])
def test_parikh_negative(args, kind):
    r = call("parikh", args)
    assert not r.ok and r.error_kind == kind


# ---------------------------------------------------------------------------
# pump_check_cfl -- mode claim
# ---------------------------------------------------------------------------

def test_pump_claim_ticket50_wrong_p4_instance_is_refuted():
    """Regression (PLAN: the ticket-50 informal proof cites a known wrong p=4
    instance a^8 b (ab)^5 a^8, which is not in L)."""
    assert TICKET50_PUMPING is not None
    r = call("pump_check_cfl", {"ir": TICKET50_IR, "claim": TICKET50_PUMPING})
    assert r.ok and r.trust == "refuted"
    assert "aaaaaaaababababababaaaaaaaa" in r.trust_reason
    assert r.data["oracle"]["class"] == "cfl" and r.data["oracle"]["exact"] is True
    assert r.data["ir_sha256"] == cfl_tools.ir_digest(TICKET50_IR)


@pytest.mark.slow  # every split is checked; can pass the tool's 30 s limit on a loaded runner
def test_pump_claim_ticket50_corrected_instance_stays_well_formed():
    claim = json.loads(json.dumps(TICKET50_PUMPING))
    claim["evidence"]["word_instances"]["4"] = "a" * 8 + "b" + "ab" * 4 + "a" * 8
    r = call("pump_check_cfl", {"ir": TICKET50_IR, "claim": claim})
    assert r.ok and r.trust == "well_formed"          # never above, even with every split closed
    assert r.bounds.p_values == [3, 4] and r.bounds.i_values == [0, 2, 3]
    assert r.bounds.n_splits > 0


def test_pump_claim_parametric_mock_is_structure_only():
    mock = load("cfl_system/examples/mock/task_anbncn_pumping_cfl.json")
    r = call("pump_check_cfl", {"ir": ANBNCN_IR, "claim": mock})
    assert r.trust == "well_formed" and r.data["method"] == "pumping"
    assert r.bounds.p_values == []


def test_pump_claim_ogden_mock_is_detected_and_its_literal_word_refuted():
    """The mock's word_chosen 'aa bb cc aa dd' is literal (not parametric) and uses d,
    which is outside {a, b, c}: the oracle rejects it, so the witness is refuted."""
    mock = load("cfl_system/examples/mock/task_w0w1w2w1w3_ogden.json")
    r = call("pump_check_cfl", {"ir": load("cfl_system/examples/task_w0w1w2w1w3.json"), "claim": mock})
    assert r.ok and r.data["method"] == "ogden"
    assert r.trust == "refuted" and "NOT in L" in r.trust_reason


def test_pump_claim_with_missing_fields_is_not_verified():
    claim = {"word_chosen": "a^p b^p c^p", "cases": [{"case": "inside one block"}]}
    r = call("pump_check_cfl", {"ir": ANBNCN_IR, "claim": claim})
    assert r.ok and r.trust == "not_verified"
    assert any("all_cases_covered" in i for i in r.data["issues"])


def test_pump_claim_chosen_word_not_in_language_is_refuted():
    claim = {"word_chosen": "abab", "cases": [{"case": "any", "why_not_in_L": "x"}],
             "all_cases_covered": True}
    r = call("pump_check_cfl", {"ir": ANBNCN_IR, "claim": claim})
    assert r.trust == "refuted" and r.data["oracle"]["source"] == "cfl_system.lib.exponent_pattern"


def test_pump_claim_without_oracle_checks_structure_only():
    ir = load("cfl_system/examples/task11_ai_bj_between.json")     # prose, no oracle
    claim = {"word_chosen": "aaabb", "cases": [{"case": "c", "why_not_in_L": "w"}],
             "all_cases_covered": True, "word_instances": {"3": "aaaabbb"}}
    r = call("pump_check_cfl", {"ir": ir, "claim": claim})
    assert r.ok and r.data["oracle"] is None and r.trust == "not_verified"
    assert "no membership oracle" in r.trust_reason


# ---------------------------------------------------------------------------
# pump_check_cfl -- mode split
# ---------------------------------------------------------------------------

def test_pump_split_confirmed_exit_is_only_a_diagnostic():
    """One split is never a destructive argument (VERDICT_POLICY §1 / §4): not_verified."""
    r = call("pump_check_cfl", {"ir": ANBNCN_IR, "p": 3, "claimed_exit_i": [0],
                                "split": {"u": "aa", "v": "a", "w": "", "x": "b", "y": "bbccc"}})
    assert r.ok and r.trust == "not_verified" and r.data["evidence_role"] == "diagnostic"
    assert "claimed exits confirmed at i = [0]" in r.trust_reason
    assert r.data["z_in_language"] is True and r.data["closed_at"] == [0, 2, 3]
    assert r.data["claim_results"] == [{"i": 0, "status": "confirmed"}]


def test_pump_split_false_exit_claim_is_refuted():
    # S -> SabS | Sc | ε (ll Format 2): abab = ab . ab, pumping v = ab stays in L
    r = call("pump_check_cfl", {"ir": LL_FORMAT2_IR, "p": 4, "claimed_exit_i": [2],
                                "split": {"v": "ab", "y": "ab"}})
    assert r.ok and r.trust == "refuted"
    assert r.data["claim_results"] == [{"i": 2, "status": "contradicted"}]
    assert "ababab" in r.trust_reason


def test_pump_split_survival_is_not_a_refutation():
    r = call("pump_check_cfl", {"ir": LL_FORMAT2_IR, "split": {"v": "ab", "y": "ab"}})
    assert r.trust == "not_verified" and r.data["survived"] == [0, 2, 3]
    assert "not a refutation" in r.trust_reason


def test_pump_split_chosen_word_outside_language_is_refuted():
    z = "a" * 8 + "b" + "ab" * 5 + "a" * 8       # the wrong ticket-50 p=4 instance
    r = call("pump_check_cfl", {"ir": TICKET50_IR, "p": 4,
                                "split": {"u": "", "v": "a", "w": "", "x": "", "y": z[1:]}})
    assert r.trust == "refuted" and r.data["z_in_language"] is False


def test_pump_split_on_a_dcfl_set_builder_ir_uses_the_dcfl_oracle():
    ir = load("dcfl_system/examples/task_anbncm.json")               # {a^n b^n c^m | n, m >= 1}
    r = call("pump_check_cfl", {"ir": ir, "p": 4,
                                "split": {"u": "a", "v": "a", "w": "", "x": "b", "y": "bc"}})
    assert r.trust == "not_verified" and r.data["survived"] == [0, 2, 3]
    assert r.data["oracle"]["class"] == "dcfl"


def test_pump_split_ogden_marks():
    ir = load("cfl_system/examples/task_w0w1w2w1w3.json")            # w0 w1 w2 w1 w3, |w1| > 1
    split = {"u": "", "v": "a", "w": "b", "x": "", "y": "cab"}      # z = abcab
    ok = call("pump_check_cfl", {"ir": ir, "p": 2, "marked_positions": [0, 1], "split": split})
    assert ok.ok and ok.data["method"] == "ogden" and ok.data["z_in_language"] is True
    no_mark_in_vx = call("pump_check_cfl", {"ir": ir, "p": 2, "marked_positions": [3, 4],
                                            "split": split})
    assert not no_mark_in_vx.ok and no_mark_in_vx.error_kind == "invalid_input"
    too_few = call("pump_check_cfl", {"ir": ir, "p": 3, "marked_positions": [0, 1], "split": split})
    assert too_few.error_kind == "invalid_input"


@pytest.mark.parametrize("args, kind", [
    ({"split": {"u": "aaa", "v": "", "x": "", "y": "bbbccc"}}, "invalid_input"),     # |vx| = 0
    ({"p": 2, "split": {"u": "aa", "v": "a", "w": "b", "x": "b", "y": "bccc"}}, "invalid_input"),
    ({"p": 20, "split": {"v": "a", "y": "bc"}}, "invalid_input"),                     # |z| < p
    ({"split": {"v": "a"}, "claim": {"word_chosen": "abc"}}, "invalid_input"),        # two modes
    ({}, "invalid_input"),
    ({"split": {"v": "a"}, "i_values": [11]}, "invalid_input"),
    ({"claim": {"word_chosen": "abc"}, "p": 3}, "invalid_input"),
    ({"split": {"v": "a" * 20, "y": "bc"}, "i_values": [9]}, "input_too_large"),
])
def test_pump_split_negative(args, kind):
    r = call("pump_check_cfl", {"ir": ANBNCN_IR, **args})
    assert not r.ok and r.error_kind == kind and r.trust == "not_verified"


def test_pump_split_without_oracle_is_unsupported():
    ir = load("cfl_system/examples/task11_ai_bj_between.json")
    r = call("pump_check_cfl", {"ir": ir, "split": {"v": "a", "y": "ab"}})
    assert not r.ok and r.error_kind == "unsupported"
    nolang = call("pump_check_cfl", {"ir": {"task_type": "classify_cfl"}, "split": {"v": "a"}})
    assert nolang.error_kind == "invalid_input"


def test_pump_split_uses_the_shared_literal_aware_oracle():
    """Regression: the old local dispatch went through cfl_oracle.predicate_oracle, which
    binds concat_pattern literals as free parts, and refuted a correct exit claim with
    "'aaaaaaaa' IS in L". The shared words.oracle_for answers like check_membership."""
    ir = load("agent_system/examples/task_z1wawbz2.json")
    ir["task_type"] = "classify_cfl"
    split = {"u": "a", "v": "a", "w": "a", "x": "", "y": "aaaa"}
    r = call("pump_check_cfl", {"ir": ir, "split": split, "claimed_exit_i": [2]})
    assert r.ok and r.data["oracle"]["class"] == "cfl"
    assert "IS in L" not in r.trust_reason
    assert r.data["claim_results"] == [{"i": 2, "status": "confirmed"}]
    # z = aaaaaaa has no b, so it is not in L: the chosen word itself is refuted
    assert r.trust == "refuted" and r.data["z_in_language"] is False
    words = [r.data["z"]] + [p["word"] for p in r.data["pumped"]]
    member = call("check_membership", {"cls": "cfl", "ir": ir, "words": words})
    got = {row["word"]: row["in_language"] for row in member.data["results"]}
    assert got[r.data["z"]] is r.data["z_in_language"]
    assert all(got[p["word"]] is p["in_language"] for p in r.data["pumped"])
    # a z that is in L: the exit claim at i = 0 is confirmed, and that is still a diagnostic
    ok = call("pump_check_cfl", {"ir": ir, "split": {"v": "a", "y": "aaabaa"}, "claimed_exit_i": [0]})
    assert ok.data["z_in_language"] is True and ok.trust == "not_verified"
    assert ok.data["claim_results"] == [{"i": 0, "status": "confirmed"}]


def test_pump_split_explicit_class_and_invalid_ir_for_it():
    r = call("pump_check_cfl", {"ir": LL_FORMAT2_IR, "cls": "ll", "split": {"v": "ab", "y": "ab"}})
    assert r.ok and r.data["oracle"]["class"] == "ll"
    bad = call("pump_check_cfl", {"ir": LL_FORMAT2_IR, "cls": "cfl", "split": {"v": "ab", "y": "ab"}})
    assert not bad.ok and bad.error_kind == "invalid_input"


def test_pump_split_nothing_pumped_decided_is_not_verified():
    """Every pumped word past the exponent oracle's bound (40): no split was checked."""
    split = {"u": "a" * 11, "v": "a", "w": "", "x": "a", "y": "b" * 13 + "c" * 13}
    r = call("pump_check_cfl", {"ir": ANBNCN_IR, "split": split, "i_values": [3]})
    assert r.ok and r.trust == "not_verified" and r.data["z_in_language"] is True
    assert r.data["unknown"] == [3]
    assert "no pumped word could be decided" in r.trust_reason and "survived" not in r.trust_reason


def test_pump_split_single_survivor_is_never_a_destructive_basis():
    r = call("pump_check_cfl", {"ir": ANBN_IR, "split": {"u": "a", "v": "a", "w": "", "x": "b", "y": "b"}})
    assert r.ok and r.trust == "not_verified" and r.data["survived"] == [0, 2, 3]
    assert r.data["evidence_role"] == "diagnostic"


def test_pump_split_approximate_oracle_never_refutes():
    """LL Format 1 has comment-only constraints the oracle ignores (exact=False)."""
    ir = load("ll_system/examples/format1_anbn_union_ancn.json")
    r = call("pump_check_cfl", {"ir": ir, "cls": "ll", "claimed_exit_i": [0, 2],
                                "split": {"u": "a", "v": "a", "w": "", "x": "b", "y": "b"}})
    assert r.ok and r.trust != "refuted" and r.data["oracle"]["exact"] is False


def test_pump_split_refuses_the_natural_language_filter_oracle():
    ir = load("cfl_system/examples/task_grammar_filter_49.json")
    ir["language_spec"]["filter"] = {"kind": "natural_language_filter", "description": "|w|_a = |w|_b"}
    r = call("pump_check_cfl", {"ir": ir, "split": {"v": "a", "y": "b"}})
    assert not r.ok and r.error_kind == "unsupported"
    assert "natural-language filter" in r.error


# ---------------------------------------------------------------------------
# grammar_vs_ir / input_grammar_cfg
# ---------------------------------------------------------------------------

def test_grammar_vs_ir_agreement_is_bounded_pass():
    r = call("grammar_vs_ir", {"grammar": ANBN_IR["language_spec"], "ir": ANBN_IR})
    assert r.ok and r.trust == "bounded_pass"
    assert r.bounds.exhaustive_up_to == 10 and r.bounds.n_words >= 2047 and r.bounds.n_unknown == 0
    assert r.data["comparison"]["counterexamples"] == []
    assert r.data["evidence_role"] == "artifact_check"


def test_grammar_vs_ir_counterexample_is_refuted():
    g = json.loads(json.dumps(ANBN_IR["language_spec"]))
    g["rules"].append({"lhs": "S", "rhs": ["b"]})
    r = call("grammar_vs_ir", {"grammar": g, "ir": ANBN_IR})
    assert r.ok and r.trust == "refuted"
    first = r.data["comparison"]["counterexamples"][0]
    assert first == {"word": "b", "in_language": False, "artifact_accepts": True}


def test_grammar_vs_ir_against_an_exponent_ir():
    # a^n b^n c^n: S -> aSbc | eps is a wrong guess (abcbc is not in L)
    wrong = {"terminals": ["a", "b", "c"], "nonterminals": ["S"], "start": "S",
             "rules": [{"lhs": "S", "rhs": ["a", "S", "b", "c"]}, {"lhs": "S", "rhs": []}]}
    r = call("grammar_vs_ir", {"grammar": wrong, "ir": ANBNCN_IR})
    assert r.trust == "refuted"
    assert any(c["word"] == "aabcbc" for c in r.data["comparison"]["counterexamples"])


def test_grammar_vs_ir_invalid_grammar_and_no_oracle():
    g = {**ANBN_IR["language_spec"], "start": "X"}
    assert call("grammar_vs_ir", {"grammar": g, "ir": ANBN_IR}).trust == "refuted"
    no = call("grammar_vs_ir", {"grammar": ANBN_IR["language_spec"],
                                "ir": load("cfl_system/examples/task11_ai_bj_between.json")})
    assert not no.ok and no.error_kind == "unsupported"


def test_input_grammar_cfg():
    r = call("input_grammar_cfg", {"ir": ANBN_IR})
    assert r.ok and r.trust == "verified" and r.data["certified"] is True
    f = call("input_grammar_cfg", {"ir": load("cfl_system/examples/task_grammar_filter_49.json")})
    assert f.ok and f.trust == "not_verified" and "grammar_filter" in f.trust_reason
    p = call("input_grammar_cfg", {"ir": ANBNCN_IR})
    assert p.ok and p.trust == "not_verified"
    bad = call("input_grammar_cfg", {"ir": {"task_type": "x"}})
    assert not bad.ok and bad.error_kind == "invalid_input"


# ---------------------------------------------------------------------------
# LangChain path
# ---------------------------------------------------------------------------

def test_langchain_tool_call_carries_the_result_artifact():
    pytest.importorskip("langchain_core")
    from langchain_core.messages import ToolMessage

    tools = {t.name: t for t in REGISTRY.langchain_tools(names=list(TOOLS))}
    assert set(tools) == set(TOOLS)
    g = GRAMMARS["dcfl/task_grammar_aSSb"][0]
    msg = tools["cyk"].invoke({"name": "cyk", "id": "c1", "type": "tool_call",
                               "args": {"grammar": g, "words": ["ba", "bb"]}})
    assert isinstance(msg, ToolMessage)
    assert msg.artifact["trust"] == "verified" and msg.artifact["tool"] == "cyk"
    assert msg.artifact["data"]["membership"] == {"ba": True, "bb": False}
    assert json.loads(msg.content)["trust"] == "verified"
