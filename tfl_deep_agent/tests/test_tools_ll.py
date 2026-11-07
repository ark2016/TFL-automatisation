"""P1: tools/ll.py -- first_follow, ll_table, ll_parse, grammar_transform.

No LLM, no network, no Docker. Inputs are the repository's own example tasks
(ll_system/examples, agent_system/examples, cfl_system/examples,
dcfl_system/examples). Expected answers are the ones docs/THEORY.md §3.1 and
the eval manifest state for these grammars (ll-08: LL(2) but not SLL(2);
ll-09: not LL(2), LL(3); ll-10: left recursion; ll-15: LL(12), not LL(11)),
or are cross-checked against CYK.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from cfl_system.lib.cnf import to_cnf
from cfl_system.lib.cyk import cyk_parse
from tfl_deep_agent.tools import base
from tfl_deep_agent.tools import ll
from tfl_deep_agent.tools.base import REGISTRY, load_groups

ROOT = Path(__file__).resolve().parents[2]
LL_TOOLS = ("first_follow", "ll_table", "ll_parse", "grammar_transform")


def example(rel: str) -> dict[str, Any]:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


F3 = "ll_system/examples/format3_simple_ll1.json"            # S → aAb | bBa; A → aA | ε; B → bB | ε
F2 = "ll_system/examples/format2_SabS_Sc_eps.json"           # S → SabS | Sc | ε (Format 2)
F1 = "ll_system/examples/format1_anbn_union_ancn.json"       # set_builder, no grammar
LL08 = "ll_system/examples/eval/ll-08.json"                  # S → aAaa | bAba, A → b | ε
LL09 = "ll_system/examples/eval/ll-09.json"                  # S → AB, A → ε | b, B → aa | ba
LL10 = "ll_system/examples/eval/ll-10.json"                  # E → E+T | T, T → i
LL11 = "ll_system/examples/eval/ll-11.json"                  # the ll-10 grammar, Format 2
LL15 = "ll_system/examples/eval/ll-15.json"                  # S → a^11 b | a^11 c
LL17 = "ll_system/examples/eval/ll-17.json"                  # S → aSb | ε
LL18 = "ll_system/examples/eval/ll-18.json"                  # S → aS | a
DCFL_ASSB = "dcfl_system/examples/task_grammar_aSSb.json"    # S → aSSb | ba | Ab, A → aAb | a
REG_SASB = "agent_system/examples/task2_grammar_sasb.json"   # S → SaSb | ε | A, A → bb | aa | bSb
REG_BBAA = "agent_system/examples/task_grammar_bb_aA.json"   # S → BB | aA, A → SS | cB, B → ε | SAb
CFL_FILTER = "cfl_system/examples/task_grammar_filter_49.json"  # grammar_filter: S → aSbb | ε | bbSa | aA ...


def grammar_of(rel: str) -> dict[str, Any]:
    ir = example(rel)
    g = ir.get("grammar") or ir["language_spec"]
    if g.get("kind") == "grammar_filter":
        g = g["grammar"]
    return {key: g[key] for key in ("terminals", "nonterminals", "start", "rules")}


def call(name: str, args: dict[str, Any]) -> base.ToolResult:
    return REGISTRY.invoke(name, args)


def cyk_member(g: dict[str, Any]):
    cnf = to_cnf(g)
    return lambda w: cyk_parse(cnf, w)


def words_upto(alphabet: list[str], n: int):
    for length in range(n + 1):
        for letters in itertools.product(alphabet, repeat=length):
            yield "".join(letters)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_ll_tools_are_registered_with_policy_ceilings():
    load_groups(["ll"])
    assert set(LL_TOOLS) <= set(REGISTRY.names("ll"))
    for name in LL_TOOLS:
        spec = REGISTRY.get(name)
        assert spec.group == "ll" and spec.ceiling == "verified"
        assert spec.description.strip()
        schema = spec.args_schema.model_json_schema()
        assert not base._schema_field_names(schema) & base.RESERVED_ARG_NAMES
    assert REGISTRY.get("grammar_transform").timeout_mode == "process"


def test_schemas_describe_every_field_for_the_model():
    for name in LL_TOOLS:
        schema = REGISTRY.get(name).args_schema.model_json_schema()
        for field, prop in schema["properties"].items():
            assert prop.get("description"), f"{name}.{field} has no description"
        rule = schema["$defs"]["GrammarRule"]["properties"]
        assert "ε" in rule["rhs"]["description"]


def test_langchain_tool_returns_content_and_artifact():
    from langchain_core.messages import ToolMessage

    lc = REGISTRY.as_langchain_tool("ll_table")
    assert lc.metadata == {"tfl_group": "ll", "tfl_max_trust": "verified"}
    msg = lc.invoke({"name": "ll_table", "args": {"ir": example(LL17), "k": 1},
                     "id": "call_ll", "type": "tool_call"})
    assert isinstance(msg, ToolMessage)
    assert msg.artifact["trust"] == "verified" and msg.artifact["data"]["is_ll_k"] is True
    assert json.loads(msg.content)["tool"] == "ll_table"


def test_trust_cannot_be_passed_in():
    r = call("ll_table", {"ir": example(LL17), "k": 1, "trust": "verified"})
    assert not r.ok and r.error_kind == "invalid_input" and r.trust == "not_verified"


# ---------------------------------------------------------------------------
# Grammar input (shared by all four tools)
# ---------------------------------------------------------------------------

def test_grammar_and_ir_give_the_same_grammar_id():
    by_ir = call("first_follow", {"ir": example(F3)})
    by_grammar = call("first_follow", {"grammar": grammar_of(F3)})
    assert by_ir.ok and by_grammar.ok
    assert by_ir.data["grammar_id"] == by_grammar.data["grammar_id"]
    assert by_ir.data["grammar_source"] == "ir.grammar"
    assert by_grammar.data["grammar_source"] == "grammar"
    # Format 2 and the CFL grammar_filter spec are read from language_spec
    assert call("first_follow", {"ir": example(F2)}).data["grammar_source"] == "ir.language_spec"
    assert call("first_follow", {"ir": example(CFL_FILTER)}).data["grammar_source"] == \
        "ir.language_spec.grammar"


def test_epsilon_spelling_is_normalised():
    g = grammar_of(LL17)
    spelled = {**g, "rules": [r if r["rhs"] else {**r, "rhs": ["ε"]} for r in g["rules"]]}
    a, b = call("first_follow", {"grammar": g}), call("first_follow", {"grammar": spelled})
    assert a.data["grammar_id"] == b.data["grammar_id"]
    assert a.data["first_k"] == b.data["first_k"] == {"S": ["", "a"]}


@pytest.mark.parametrize("tool,extra", [
    ("first_follow", {}), ("ll_table", {"k": 1}), ("ll_parse", {"word": "ab"}),
])
def test_malformed_grammar_is_invalid_input(tool, extra):
    # a multi-character terminal fails the schema: a malformed call, not an artifact
    g = {**grammar_of(LL17), "terminals": ["ab", "b"]}
    r = call(tool, {"grammar": g, **extra})
    assert not r.ok and r.error_kind == "invalid_input" and r.trust == "not_verified"
    assert "at most 1 character" in r.error


@pytest.mark.parametrize("tool,extra", [
    ("first_follow", {}), ("ll_table", {"k": 1}), ("ll_parse", {"word": "ab"}),
    ("grammar_transform", {"ops": ["left_factor"]}),
])
@pytest.mark.parametrize("mutate,needle", [
    (lambda g: {**g, "terminals": ["a", "b", "$"]}, "reserved"),                 # reserved end marker
    (lambda g: {**g, "rules": g["rules"] + [{"lhs": "S", "rhs": ["x"]}]}, "not declared"),
    (lambda g: {**g, "rules": g["rules"] + [{"lhs": "Q", "rhs": ["a"]}]}, "not a declared nonterminal"),
    (lambda g: {**g, "start": "Q"}, "start symbol"),
    (lambda g: {**g, "terminals": ["a", "b", "S"]}, "both nonterminals and terminals"),
])
def test_invalid_submitted_grammar_is_refuted(tool, extra, mutate, needle):
    """VERDICT_POLICY §1: invalid grammar symbols are a refuted signal (as dpda_run / cfl)."""
    r = call(tool, {"grammar": mutate(grammar_of(LL17)), **extra})
    assert r.ok and r.trust == "refuted", r
    assert r.data["valid"] is False and r.data["evidence_role"] == "invalid_artifact"
    assert any(needle in e for e in r.data["validation_errors"])
    assert r.data["grammar_id"].startswith("g-")


def test_invalid_grammar_inside_an_ir_is_invalid_input():
    ir = example(F3)
    ir["grammar"] = {**ir["grammar"], "start": "Q"}
    r = call("first_follow", {"ir": ir})
    assert not r.ok and r.error_kind == "invalid_input" and "ir.grammar" in r.error


def test_grammar_and_ir_are_mutually_exclusive():
    r = call("first_follow", {"grammar": grammar_of(F3), "ir": example(F3)})
    assert r.error_kind == "invalid_input" and "exactly one" in r.error
    assert call("first_follow", {}).error_kind == "invalid_input"


def test_ir_without_a_grammar_is_unsupported():
    for tool, extra in (("first_follow", {}), ("ll_table", {"max_k": 2}), ("ll_parse", {"word": "a"})):
        r = call(tool, {"ir": example(F1), **extra})
        assert not r.ok and r.error_kind == "unsupported" and "no explicit grammar" in r.error


def test_size_limits():
    g = grammar_of(LL17)
    big = {**g, "rules": [{"lhs": "S", "rhs": ["a"]}] * (base.DEFAULT_LIMITS.max_grammar_rules + 1)}
    assert call("first_follow", {"grammar": big}).error_kind == "invalid_input"
    long_rhs = {**g, "rules": [{"lhs": "S", "rhs": ["a"] * (base.DEFAULT_LIMITS.max_rhs_len + 1)}]}
    assert call("ll_table", {"grammar": long_rhs}).error_kind == "invalid_input"
    assert call("first_follow", {"grammar": g, "k": ll.LL_MAX_K + 1}).error_kind == "invalid_input"
    assert call("first_follow", {"grammar": g, "k": 0}).error_kind == "invalid_input"
    assert call("ll_parse", {"grammar": g, "word": "a" * 101}).error_kind == "invalid_input"


# ---------------------------------------------------------------------------
# first_follow
# ---------------------------------------------------------------------------

def test_first_follow_format3_k1():
    r = call("first_follow", {"ir": example(F3), "k": 1})
    assert r.ok and r.trust == "verified"
    d = r.data
    assert d["nullable"] == ["A", "B"]
    assert d["first_k"] == {"S": ["a", "b"], "A": ["", "a"], "B": ["", "b"]}
    assert d["follow_k"] == {"S": ["$"], "A": ["b"], "B": ["a"]}
    director = {(row["lhs"], tuple(row["rhs"])): row["director"] for row in d["director_sets"]}
    assert director[("A", ())] == ["b"] and director[("A", ("a", "A"))] == ["a"]
    assert director[("S", ("a", "A", "b"))] == ["a"]
    assert d["left_recursive"] is False and d["useless_nonterminals"] == []


def test_first_follow_ll08_follow2_matches_theory():
    # docs/THEORY.md §3.1: S → aAaa | bAba, A → b | ε — FOLLOW₂(A) = {aa, ba}
    r = call("first_follow", {"ir": example(LL08), "k": 2})
    assert r.trust == "verified"
    assert r.data["follow_k"]["A"] == ["aa", "ba"]
    assert r.data["follow_k"]["S"] == ["$$"]
    rows = {tuple(row["rhs"]): row for row in r.data["director_sets"] if row["lhs"] == "A"}
    # the strong conflict on "ba": A → b and A → ε both predict it
    assert "ba" in rows[("b",)]["director"] and "ba" in rows[()]["director"]


def test_first_follow_without_director_sets_and_on_other_examples():
    r = call("first_follow", {"ir": example(DCFL_ASSB), "k": 2, "include_director_sets": False})
    assert r.trust == "verified" and "director_sets" not in r.data
    assert r.data["first_k"]["A"] == ["a", "aa"]      # A → aAb | a: a, aab, aaabb, ...
    r = call("first_follow", {"ir": example(REG_BBAA), "k": 1})
    assert r.trust == "verified" and r.data["left_recursive"] is True
    assert set(r.data["nullable"]) == {"S", "A", "B"}  # B → ε, S → BB, A → SS


def test_first_follow_reports_useless_nonterminals():
    g = {"terminals": ["a"], "nonterminals": ["S", "D", "U"], "start": "S",
         "rules": [{"lhs": "S", "rhs": ["a"]}, {"lhs": "S", "rhs": ["D"]}, {"lhs": "D", "rhs": ["D", "a"]},
                   {"lhs": "U", "rhs": ["a"]}]}
    r = call("first_follow", {"grammar": g})
    assert r.data["useless_nonterminals"] == ["D", "U"]
    assert r.data["first_k"]["D"] == [] and r.data["follow_k"]["U"] == []
    # D's left recursion is not counted: D is not productive
    assert r.data["left_recursive"] is False


# ---------------------------------------------------------------------------
# ll_table
# ---------------------------------------------------------------------------

def test_ll_table_format3_is_ll1_with_parse_table():
    r = call("ll_table", {"ir": example(F3), "k": 1})
    assert r.ok and r.trust == "verified"
    d = r.data
    assert d["is_ll_k"] is True and d["is_strong_ll_k"] is True and d["mode"] == "check"
    assert d["parse_table"]["A"] == {"a": ["a", "A"], "b": []}
    assert d["scope"] == "grammar" and "not evidence about the language" in d["scope_note"]
    assert d["claim"] == f"grammar {d['grammar_id']} is LL(1)"
    assert "parse_table" not in call("ll_table", {"ir": example(F3), "k": 1, "include_table": False}).data


def test_ll_table_ll08_ll2_but_not_strong():
    r1 = call("ll_table", {"ir": example(LL08), "k": 1})
    assert r1.trust == "refuted" and r1.data["is_ll_k"] is False
    r2 = call("ll_table", {"ir": example(LL08), "k": 2})
    assert r2.trust == "verified" and r2.data["is_ll_k"] is True and r2.data["is_strong_ll_k"] is False
    assert "parse_table" not in r2.data  # no single global table for a non-strong LL(k) grammar
    assert any(c["lookahead"] == "ba" for c in r2.data["strong_conflicts"])
    r3 = call("ll_table", {"ir": example(LL08), "k": 3})
    assert r3.trust == "verified" and r3.data["is_strong_ll_k"] is True


def test_ll_table_ll09_conflict_only_with_the_whole_local_follow_set():
    # docs/THEORY.md / ll_table_builder regression: not LL(2) only when {aa, ba} is one context
    r2 = call("ll_table", {"ir": example(LL09), "k": 2})
    assert r2.trust == "refuted" and r2.data["is_ll_k"] is False
    assert any(c["nonterminal"] == "A" and ["aa", "ba"] in c["local_follow"]
               for c in r2.data["ll_conflicts"])
    assert "not LL(2)" in r2.data["claim"]
    r3 = call("ll_table", {"ir": example(LL09), "k": 3})
    assert r3.trust == "verified" and r3.data["is_ll_k"] is True


def test_ll_table_min_k_found():
    r = call("ll_table", {"ir": example(LL09), "max_k": 4})
    assert r.trust == "verified" and r.data["found"] is True and r.data["k"] == 3
    assert "not LL(j) for 1 ≤ j < 3" in r.data["claim"]
    r = call("ll_table", {"ir": example(LL18), "max_k": 3})
    assert r.trust == "verified" and r.data["k"] == 2
    r = call("ll_table", {"ir": example(LL17), "max_k": 3})
    assert r.trust == "verified" and r.data["k"] == 1 and r.data["claim"].endswith("least k = 1")


@pytest.mark.parametrize("rel", [LL10, LL11, F2, REG_SASB, REG_BBAA])
def test_ll_table_left_recursion_certificate(rel):
    r = call("ll_table", {"ir": example(rel), "max_k": 3})
    assert r.ok and r.trust == "verified"
    assert r.data["not_ll_any_k"] is True and r.data["certificate"]["type"] == "left_recursion"
    assert "not LL(k) for any k" in r.data["claim"]
    assert "not about the language" in r.trust_reason


@pytest.mark.parametrize("rel", [DCFL_ASSB, CFL_FILTER])
def test_ll_table_no_k_found_without_certificate_is_bounded(rel):
    r = call("ll_table", {"ir": example(rel), "max_k": 3})
    assert r.ok and r.trust == "bounded_pass"
    assert r.bounds.max_k == 3 and r.data["found"] is False and r.data["certificate"] is None
    assert "for k ≤ 3" in r.data["claim"]


def test_ll_table_large_k_runs_in_a_worker_process():
    # eval ll-15: S → a^11 b | a^11 c is LL(12), not LL(11). (|Σ|+1)^k is far
    # above the inline limit, so the lib runs in a killable child process.
    g = grammar_of(LL15)
    assert ll._lookahead_universe(g, 11) > ll.LOOKAHEAD_INLINE_LIMIT
    r11 = call("ll_table", {"ir": example(LL15), "k": 11})
    assert r11.trust == "refuted" and r11.data["is_ll_k"] is False
    r12 = call("ll_table", {"ir": example(LL15), "k": 12})
    assert r12.trust == "verified" and r12.data["is_ll_k"] is True


def test_ll_table_k_and_max_k_are_exclusive():
    r = call("ll_table", {"ir": example(F3), "k": 1, "max_k": 2})
    assert r.error_kind == "invalid_input" and "not both" in r.error


def test_ll_table_budget_exhausted_is_not_verified(monkeypatch):
    def undecided(grammar, k, *, time_budget_s=None, max_tables=20000):
        return {"is_ll_k": None, "is_strong_ll_k": False, "k": k, "first_sets": {}, "follow_sets": {},
                "conflicts": [{"nonterminal": "A"}], "ll_conflicts": [], "parse_table": None,
                "test_complete": False}

    monkeypatch.setattr(ll._tb, "check_ll_k", undecided)
    r = call("ll_table", {"ir": example(LL08), "k": 2, "time_budget_s": 1})
    assert r.ok and r.trust == "not_verified" and r.bounds.truncated is True
    assert r.data["claim"].startswith("unknown")


def test_ll_table_min_k_undetermined_is_not_verified(monkeypatch):
    def undecided(grammar, max_k=10, *, time_budget_s=None):
        return {"found": False, "k": None, "strong_k": None, "max_k_checked": 2, "max_k_decided": 1,
                "undetermined": True, "result_for_k": {}, "certificate": None, "not_ll_any_k": False}

    monkeypatch.setattr(ll._tb, "find_min_ll_k", undecided)
    r = call("ll_table", {"ir": example(LL08), "max_k": 4})
    assert r.trust == "not_verified" and r.bounds.max_k == 1 and r.bounds.truncated is True
    assert "unknown from k = 2" in r.data["claim"]


# ---------------------------------------------------------------------------
# ll_parse
# ---------------------------------------------------------------------------

def test_ll_parse_accepts_with_leftmost_derivation():
    r = call("ll_parse", {"ir": example(F3), "k": 1, "word": "aab"})
    assert r.ok and r.trust == "verified" and r.data["accepted"] is True
    assert r.data["derivation"] == ["S", "aAb", "aaAb", "aab"]
    assert [p["rhs"] for p in r.data["productions"]] == [["a", "A", "b"], ["a", "A"], []]
    assert "steps" not in r.data
    assert call("ll_parse", {"ir": example(F3), "word": "aab", "include_steps": True}).data["steps"]


def test_ll_parse_rejects_with_reason():
    r = call("ll_parse", {"ir": example(F3), "k": 1, "word": "abab"})
    assert r.ok and r.trust == "verified" and r.data["accepted"] is False
    assert "derivation" not in r.data and r.data["error"]
    r = call("ll_parse", {"ir": example(F3), "word": "ab$"})  # $ is not a terminal
    assert r.trust == "verified" and r.data["accepted"] is False and r.data["outside_symbols"] == ["$"]


@pytest.mark.parametrize("rel,k,n", [(F3, 1, 6), (LL17, 1, 8), (LL08, 3, 5), (LL18, 2, 6)])
def test_ll_parse_agrees_with_cyk(rel, k, n):
    g = grammar_of(rel)
    member = cyk_member(g)
    for w in words_upto(g["terminals"], n):
        r = call("ll_parse", {"grammar": g, "k": k, "word": w})
        assert r.trust == "verified", (w, r.error)
        assert r.data["accepted"] is member(w), w
        if r.data["accepted"]:
            assert r.data["derivation"][-1] == (w or "ε")


def test_ll_parse_epsilon_word():
    r = call("ll_parse", {"ir": example(LL17), "word": ""})
    assert r.data["accepted"] is True and r.data["derivation"] == ["S", "ε"]


def test_ll_parse_not_ll_k_is_invalid_input():
    r = call("ll_parse", {"ir": example(LL18), "k": 1, "word": "aa"})
    assert not r.ok and r.error_kind == "invalid_input" and "not LL(1)" in r.error
    assert r.data["strong_conflicts"]


def test_ll_parse_ll_but_not_strong_is_unsupported():
    r = call("ll_parse", {"ir": example(LL08), "k": 2, "word": "bba"})
    assert not r.ok and r.error_kind == "unsupported" and "not strong LL(2)" in r.error


# ---------------------------------------------------------------------------
# grammar_transform
# ---------------------------------------------------------------------------

def test_left_recursion_removal_ll10_then_ll1():
    r = call("grammar_transform", {"ir": example(LL10), "ops": ["eliminate_left_recursion"]})
    assert r.ok and r.trust == "bounded_pass"
    d = r.data
    assert d["left_recursive_before"] is True and d["left_recursive_after"] is False
    assert d["equivalence"]["status"] == "no_mismatch" and d["equivalence"]["counterexamples"] == []
    assert r.bounds.max_len == 8 and r.bounds.n_words == 2 ** 9 - 1
    assert "not a proof of equivalence" in r.trust_reason
    assert d["grammar_id"] != d["input_grammar_id"]
    # the new grammar is a different grammar; ll_table answers for it
    t = call("ll_table", {"grammar": d["grammar"], "k": 1})
    assert t.trust == "verified" and t.data["is_ll_k"] is True and t.data["grammar_id"] == d["grammar_id"]


def test_left_factoring_ll18():
    r = call("grammar_transform", {"ir": example(LL18), "ops": ["left_factor"], "max_len": 10})
    assert r.trust == "bounded_pass" and r.bounds.max_len == 10 and r.bounds.n_words == 11
    g = r.data["grammar"]
    s_rules = [x["rhs"] for x in g["rules"] if x["lhs"] == "S"]
    assert len(s_rules) == 1 and s_rules[0][0] == "a"
    assert call("ll_table", {"grammar": g, "k": 1}).data["is_ll_k"] is True


def test_to_ll_normal_form_on_format2_language_grammar():
    r = ll.grammar_transform(ll.GrammarTransformArgs(ir=example(LL11), ops=["to_ll_normal_form"]))
    assert r.trust == "bounded_pass" and r.data["left_recursive_after"] is False
    assert [s["op"] for s in r.data["steps"]] == ["to_ll_normal_form"]


def test_transform_without_equivalence_check_is_well_formed():
    r = ll.grammar_transform(ll.GrammarTransformArgs(
        grammar=grammar_of(DCFL_ASSB), ops=["remove_useless_symbols", "left_factor"],
        check_equivalence=False))
    assert r.trust == "well_formed" and r.data["equivalence"] == {"status": "skipped"}
    assert len(r.data["steps"]) == 2


def test_transform_output_is_deterministic():
    args = ll.GrammarTransformArgs(grammar=grammar_of(REG_BBAA), ops=["eliminate_epsilon_rules"],
                                   check_equivalence=False)
    first = ll.grammar_transform(args).data["grammar"]
    assert first["terminals"] == ["a", "b", "c"]
    assert ll.grammar_transform(args).data["grammar"] == first


def test_hidden_nullable_left_recursion_is_unsupported():
    # S → A S a | b, A → ε: S ⇒ A S a ⇒ S a is left recursion behind a nullable prefix
    g = {"terminals": ["a", "b"], "nonterminals": ["S", "A"], "start": "S",
         "rules": [{"lhs": "S", "rhs": ["A", "S", "a"]}, {"lhs": "S", "rhs": ["b"]},
                   {"lhs": "A", "rhs": []}]}
    r = call("grammar_transform", {"grammar": g, "ops": ["eliminate_left_recursion"]})
    assert not r.ok and r.error_kind == "unsupported" and "to_ll_normal_form" in r.error
    ok = ll.grammar_transform(ll.GrammarTransformArgs(grammar=g, ops=["to_ll_normal_form"]))
    assert ok.trust == "bounded_pass" and ok.data["left_recursive_after"] is False


def test_a_wrong_transformation_is_refuted(monkeypatch):
    def broken_left_factor(grammar):  # drops the rule S → a
        return {**grammar, "rules": [r for r in grammar["rules"] if r["rhs"] != ["a"]]}

    monkeypatch.setattr(ll._gt, "left_factor", broken_left_factor)
    r = ll.grammar_transform(ll.GrammarTransformArgs(ir=example(LL18), ops=["left_factor"]))
    assert r.ok and r.trust == "refuted"
    eq = r.data["equivalence"]
    assert eq["status"] == "mismatch"
    assert eq["counterexamples"][0] == {"word": "a", "in_input": True, "in_transformed": False}


def test_equivalence_length_limit():
    g = grammar_of(REG_BBAA)  # 3 terminals: 3^10 words is far above the enumeration limit
    r = call("grammar_transform", {"grammar": g, "ops": ["left_factor"], "max_len": 10})
    assert not r.ok and r.error_kind == "input_too_large"
    assert call("grammar_transform", {"grammar": g, "ops": ["left_factor"], "max_len": 11}).error_kind \
        == "invalid_input"
    assert call("grammar_transform", {"grammar": g, "ops": []}).error_kind == "invalid_input"
    assert call("grammar_transform", {"grammar": g, "ops": ["reverse"]}).error_kind == "invalid_input"


def test_epsilon_elimination_blowup_is_refused():
    rhs = ["N"] * 20  # 2^20 variants of one rule
    g = {"terminals": ["a"], "nonterminals": ["S", "N"], "start": "S",
         "rules": [{"lhs": "S", "rhs": rhs}, {"lhs": "N", "rhs": []}, {"lhs": "N", "rhs": ["a"]}]}
    with pytest.raises(base.InputTooLarge, match="ε-elimination"):
        ll.grammar_transform(ll.GrammarTransformArgs(grammar=g, ops=["eliminate_epsilon_rules"]))


def test_compare_grammars_truncates_at_the_deadline():
    g = grammar_of(LL17)
    eq = ll.compare_grammars(g, g, 6, base.Deadline(0))
    assert eq["truncated"] is True and eq["covered_any"] is False and eq["n_words"] == 0
    full = ll.compare_grammars(g, g, 6)
    assert full["truncated"] is False and full["n_words"] == 2 ** 7 - 1 and full["max_len"] == 6
