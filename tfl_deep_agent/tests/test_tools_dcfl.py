"""P1: tools/dcfl.py -- dpda_run, dpda_vs_ir, closure_lookup, pattern_match.

No LLM, no network, no Docker. Inputs are the repository's real example tasks
(dcfl_system/examples, plus cfl / ll / agent_system examples as cross-class
inputs) and the R2' precedent automata of dcfl_system/tests/test_dpda.py.
"""

from __future__ import annotations

import copy
import itertools
import json
import re
from pathlib import Path
from typing import Any

import pytest

from dcfl_system.lib import closure_table, pattern_db
from dcfl_system.lib import dpda as lib_dpda
from dcfl_system.lib.word_sampler import build_membership_oracle_from_ir
from tfl_deep_agent.tools import dcfl
from tfl_deep_agent.tools.base import GROUP_MAX_TRUST, REGISTRY, load_groups

ROOT = Path(__file__).resolve().parents[2]
DCFL_EX = ROOT / "dcfl_system" / "examples"


def _load(rel: str) -> Any:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def _mock_dpda(name: str) -> dict[str, Any]:
    return _load(f"dcfl_system/examples/mock/{name}")["proof_sketch"]["dpda"]


def _words(alphabet: list[str], max_len: int) -> list[str]:
    return ["".join(p) for n in range(max_len + 1) for p in itertools.product(alphabet, repeat=n)]


def _tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {"type": "tool_call", "name": name, "args": args, "id": call_id}


# The live dcfl-04 automaton (docs/VERDICT_POLICY.md R2' precedent), copied
# from dcfl_system/tests/test_dpda.py::LIVE_DCFL04_ANBNCM_DPDA.
LIVE_DCFL04_DPDA = {
    "states": ["q0", "q_a", "q_b", "q_c", "q_accept"],
    "start": "q0",
    "accept_states": ["q_accept"],
    "accept_mode": "final_state",
    "stack_alphabet": ["Z0", "A"],
    "initial_stack": ["Z0"],
    "transitions": [
        {"from": "q0", "read": "a", "top": "Z0", "to": "q_a", "push": ["A", "Z0"]},
        {"from": "q0", "read": "b", "top": "Z0", "to": "q_b", "push": ["Z0"]},
        {"from": "q0", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q0", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_a", "read": "a", "top": "A", "to": "q_a", "push": ["A", "A"]},
        {"from": "q_a", "read": "b", "top": "A", "to": "q_b", "push": []},
        {"from": "q_b", "read": "b", "top": "A", "to": "q_b", "push": []},
        {"from": "q_b", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_b", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_c", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_c", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
    ],
}

# dcfl_system/tests/test_dpda.py::SAFE_ANBNCM_DPDA -- correct for {aⁿbⁿcᵐ | n,m ≥ 0}.
SAFE_DCFL04_DPDA = {
    "states": ["q0", "q_a", "q_b_mid", "q_b", "q_c", "q_accept"],
    "start": "q0",
    "accept_states": ["q_accept"],
    "accept_mode": "final_state",
    "stack_alphabet": ["Z0", "A1", "A"],
    "initial_stack": ["Z0"],
    "transitions": [
        {"from": "q0", "read": "a", "top": "Z0", "to": "q_a", "push": ["A1", "Z0"]},
        {"from": "q0", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q0", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_a", "read": "a", "top": "A1", "to": "q_a", "push": ["A", "A1"]},
        {"from": "q_a", "read": "a", "top": "A", "to": "q_a", "push": ["A", "A"]},
        {"from": "q_a", "read": "b", "top": "A1", "to": "q_b", "push": ["Z0"]},
        {"from": "q_a", "read": "b", "top": "A", "to": "q_b_mid", "push": []},
        {"from": "q_b_mid", "read": "b", "top": "A", "to": "q_b_mid", "push": []},
        {"from": "q_b_mid", "read": "b", "top": "A1", "to": "q_b", "push": ["Z0"]},
        {"from": "q_b", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_b", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
        {"from": "q_c", "read": "c", "top": "Z0", "to": "q_c", "push": ["Z0"]},
        {"from": "q_c", "read": None, "top": "Z0", "to": "q_accept", "push": ["Z0"]},
    ],
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_tools_are_registered_with_policy_ceilings():
    load_groups(["dcfl"])
    assert set(REGISTRY.names("dcfl")) >= {"dpda_run", "dpda_vs_ir", "closure_lookup", "pattern_match"}
    assert REGISTRY.get("dpda_run").ceiling == "verified"
    assert REGISTRY.get("dpda_vs_ir").ceiling == "bounded_pass"
    for name in ("closure_lookup", "pattern_match"):
        spec = REGISTRY.get(name)
        assert spec.ceiling == "not_verified" and spec.allow_refuted is False
    assert GROUP_MAX_TRUST["dcfl"] == "verified"


def test_schemas_describe_their_fields_for_the_model():
    for name in ("dpda_run", "dpda_vs_ir", "closure_lookup", "pattern_match"):
        spec = REGISTRY.get(name)
        assert len(spec.description) > 200
        schema = spec.args_schema.model_json_schema()
        for field, prop in schema["properties"].items():
            assert prop.get("description"), f"{name}.{field} has no description"
    # the transition keeps the contract's JSON name "from"
    dpda_schema = REGISTRY.get("dpda_run").args_schema.model_json_schema()
    assert "from" in dpda_schema["$defs"]["DPDATransition"]["properties"]


def test_model_cannot_pass_trust():
    for name, args in [
        ("dpda_run", {"dpda": _mock_dpda("dcfl_anbncm_stack_strategy.json"), "trust": "verified"}),
        ("closure_lookup", {"operation": "union", "confidence": 0.9}),
        ("pattern_match", {"pattern": "triple_count", "trust": "verified"}),
    ]:
        r = REGISTRY.invoke(name, args)
        assert not r.ok and r.error_kind == "invalid_input" and r.trust == "not_verified"


# ---------------------------------------------------------------------------
# dpda_run
# ---------------------------------------------------------------------------

def test_dpda_run_on_the_anbncm_mock_matches_the_task_oracle():
    """dcfl_system/examples/mock/dcfl_anbncm_stack_strategy.json against
    task_anbncm.json ({aⁿbⁿcᵐ | n,m ≥ 1}); every word up to length 4."""
    dpda = _mock_dpda("dcfl_anbncm_stack_strategy.json")
    ir = _load("dcfl_system/examples/task_anbncm.json")
    words = _words(ir["alphabet"], 4)
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda, "words": words})
    assert r.ok and r.trust == "verified", r
    assert r.data["deterministic"] is True and r.data["conflicts"] == []
    assert r.data["normalization"] == [] and r.data["structure_errors"] == []
    assert r.data["unknown"] == []
    assert "not a check against the task language" in r.trust_reason
    oracle = build_membership_oracle_from_ir(ir)
    for run in r.data["runs"]:
        assert run["accepted"] == lib_dpda.dpda_run(dpda, run["word"])
        assert run["accepted"] == oracle(run["word"]), run
    assert "abc" in r.data["accepted"] and "b" in r.data["rejected"]


def test_dpda_run_normalizes_first_and_is_not_a_language_check():
    """R2' precedent (live dcfl-04): all three epsilons into the accepting sink
    normalize, the automaton is deterministic -- and still accepts "b", which is
    not in {aⁿbⁿcᵐ | n,m ≥ 0} (dcfl_system/examples/eval/dcfl-04.json). The tool
    reports what the automaton does; it does not vouch for the language."""
    r = REGISTRY.invoke("dpda_run", {"dpda": LIVE_DCFL04_DPDA, "words": ["", "b", "ab", "abc"]})
    assert r.ok and r.trust == "verified"
    assert len(r.data["normalization"]) == 3
    assert {tuple(c) for c in r.data["accept_configs"]} == {("q0", "Z0"), ("q_b", "Z0"),
                                                            ("q_c", "Z0")}
    assert "b" in r.data["accepted"]
    oracle = build_membership_oracle_from_ir(_load("dcfl_system/examples/eval/dcfl-04.json"))
    assert oracle("b") is False
    # the lib on the normalized automaton gives the same answers
    normalized, _ = lib_dpda.normalize_epsilon_accept_sinks(copy.deepcopy(LIVE_DCFL04_DPDA))
    for run in r.data["runs"]:
        assert run["accepted"] == lib_dpda.dpda_run(normalized, run["word"])


def test_dpda_run_on_a_correct_dcfl04_automaton_agrees_with_the_oracle():
    ir = _load("dcfl_system/examples/eval/dcfl-04.json")
    oracle = build_membership_oracle_from_ir(ir)
    r = REGISTRY.invoke("dpda_run", {"dpda": SAFE_DCFL04_DPDA, "words": _words(ir["alphabet"], 4)})
    assert r.ok and r.trust == "verified"
    assert all(run["accepted"] == oracle(run["word"]) for run in r.data["runs"])


# ---------------------------------------------------------------------------
# dpda_vs_ir (R2')
# ---------------------------------------------------------------------------

def test_dpda_run_description_names_an_existing_language_check():
    """Regression: dpda_run pointed the model at dpda_vs_ir, which was not registered."""
    assert "dpda_vs_ir" in REGISTRY.get("dpda_run").description
    assert "dpda_vs_ir" in REGISTRY.names("dcfl")


def test_dpda_vs_ir_correct_automaton_is_bounded_pass():
    r = REGISTRY.invoke("dpda_vs_ir", {"dpda": SAFE_DCFL04_DPDA,
                                       "ir": _load("dcfl_system/examples/eval/dcfl-04.json")})
    assert r.ok and r.trust == "bounded_pass", r
    assert r.data["evidence_role"] == "artifact_check" and r.data["oracle"]["class"] == "dcfl"
    assert r.data["comparison"]["counterexamples"] == []
    assert r.bounds.n_words >= 30 and r.bounds.exhaustive_up_to >= 6
    assert "runs" not in r.data


def test_dpda_vs_ir_live_dcfl04_automaton_is_refuted_on_b():
    """R2' precedent: the deterministic live dcfl-04 automaton accepts "b" not in L."""
    r = REGISTRY.invoke("dpda_vs_ir", {"dpda": LIVE_DCFL04_DPDA,
                                       "ir": _load("dcfl_system/examples/eval/dcfl-04.json")})
    assert r.ok and r.trust == "refuted"
    assert {"word": "b", "in_language": False, "artifact_accepts": True} in \
        r.data["comparison"]["counterexamples"]


def test_dpda_vs_ir_mock_against_its_task():
    r = REGISTRY.invoke("dpda_vs_ir", {"dpda": _mock_dpda("dcfl_anbncm_stack_strategy.json"),
                                       "ir": _load("dcfl_system/examples/task_anbncm.json")})
    assert r.ok and r.trust == "bounded_pass", r


def test_dpda_vs_ir_nondeterministic_is_refuted_without_running():
    dpda = copy.deepcopy(_mock_dpda("dcfl_anbncm_stack_strategy.json"))
    dpda["transitions"].append(
        {"from": "q_push", "read": "a", "top": "A", "to": "q_pop", "push": ["A"]})
    r = REGISTRY.invoke("dpda_vs_ir", {"dpda": dpda,
                                       "ir": _load("dcfl_system/examples/task_anbncm.json")})
    assert r.ok and r.trust == "refuted" and r.data["deterministic"] is False
    assert "comparison" not in r.data


def test_dpda_vs_ir_bad_ir_is_invalid_input():
    r = REGISTRY.invoke("dpda_vs_ir", {"dpda": SAFE_DCFL04_DPDA, "ir": {"task_type": "x"}})
    assert not r.ok and r.error_kind == "invalid_input"


def test_dpda_run_nondeterministic_is_refuted_and_words_are_not_run():
    dpda = copy.deepcopy(_mock_dpda("dcfl_anbncm_stack_strategy.json"))
    # a second, different move on (q_push, A, a): genuine nondeterminism
    dpda["transitions"].append(
        {"from": "q_push", "read": "a", "top": "A", "to": "q_pop", "push": ["A"]})
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda, "words": ["abc"]})
    assert r.ok and r.trust == "refuted"
    assert r.data["deterministic"] is False and r.data["conflicts"]
    assert r.data["runs"] == [] and r.data["not_run"] == ["abc"]
    assert "not deterministic" in r.trust_reason


def test_dpda_run_epsilon_next_to_a_letter_move_is_refuted():
    dpda = copy.deepcopy(_mock_dpda("dcfl_anbncm_stack_strategy.json"))
    # q_pop is not an accepting sink, so normalization leaves this epsilon alone
    dpda["transitions"].append(
        {"from": "q_push", "read": "ε", "top": "A", "to": "q_pop", "push": ["A"]})
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda})
    assert r.trust == "refuted" and r.data["normalization"] == []
    assert any("epsilon transition coexists" in c for c in r.data["conflicts"])


def test_exact_duplicate_transitions_are_not_a_conflict():
    dpda = copy.deepcopy(_mock_dpda("dcfl_anbncm_stack_strategy.json"))
    dpda["transitions"].append(dict(dpda["transitions"][0]))
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda, "words": ["aabbc"]})
    assert r.trust == "verified" and r.data["accepted"] == ["aabbc"]


def test_dpda_run_structurally_invalid_is_refuted():
    dpda = copy.deepcopy(_mock_dpda("dcfl_anbncm_stack_strategy.json"))
    dpda["transitions"][0]["to"] = "q_missing"  # undeclared state
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda, "words": ["abc"]})
    assert r.ok and r.trust == "refuted"
    assert r.data["structure_errors"] and "structurally invalid" in r.trust_reason


def test_dpda_run_epsilon_cycle_gives_unknown_not_reject():
    dpda = {
        "states": ["p", "q", "f"], "start": "p", "accept_states": ["f"],
        "stack_alphabet": ["Z"], "initial_stack": ["Z"],
        "transitions": [
            {"from": "p", "read": None, "top": "Z", "to": "q", "push": ["Z"]},
            {"from": "q", "read": "", "top": "Z", "to": "p", "push": ["Z"]},
            {"from": "f", "read": "a", "top": "Z", "to": "f", "push": ["Z"]},
        ],
    }
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda, "words": ["", "a"]})
    assert r.trust == "verified"
    assert r.data["unknown"] == ["", "a"] and r.data["accepted"] == r.data["rejected"] == []
    assert "unknown" in r.trust_reason


def test_dpda_run_empty_stack_mode():
    # {aⁿbⁿ | n ≥ 1} by empty stack
    dpda = {
        "states": ["p", "q"], "start": "p", "accept_mode": "empty_stack",
        "stack_alphabet": ["Z", "A"], "initial_stack": ["Z"],
        "transitions": [
            {"from": "p", "read": "a", "top": "Z", "to": "p", "push": ["A"]},
            {"from": "p", "read": "a", "top": "A", "to": "p", "push": ["A", "A"]},
            {"from": "p", "read": "b", "top": "A", "to": "q", "push": []},
            {"from": "q", "read": "b", "top": "A", "to": "q", "push": []},
        ],
    }
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda, "words": ["ab", "aabb", "aab", "", "ba"]})
    assert r.trust == "verified"
    assert r.data["accepted"] == ["ab", "aabb"]


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d["transitions"][0].update(read="ab"), "single character"),
    (lambda d: d.update(accept_mode="by_magic"), "accept_mode"),
    (lambda d: d.pop("initial_stack"), "initial_stack"),
    (lambda d: d.update(initial_stack=[]), "initial_stack"),
    (lambda d: d.update(comment="extra key"), "comment"),
    (lambda d: d["transitions"][0].pop("top"), "top"),
])
def test_dpda_run_rejects_malformed_input(mutate, needle):
    dpda = copy.deepcopy(_mock_dpda("dcfl_anbncm_stack_strategy.json"))
    mutate(dpda)
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda, "words": ["abc"]})
    assert not r.ok and r.error_kind == "invalid_input" and r.trust == "not_verified"
    assert needle in r.error


def test_dpda_run_without_any_accept_condition_is_rejected():
    dpda = copy.deepcopy(_mock_dpda("dcfl_anbncm_stack_strategy.json"))
    dpda["accept_states"] = []
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda, "words": ["abc"]})
    # the structural check (to_cfl_pda) catches it first, as in R2'
    assert r.trust == "refuted" and r.data["structure_errors"]


def test_dpda_run_input_limits():
    dpda = _mock_dpda("dcfl_anbncm_stack_strategy.json")
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda, "words": ["a"] * 201})
    assert not r.ok and r.error_kind == "invalid_input"
    r = REGISTRY.invoke("dpda_run", {"dpda": dpda, "words": ["a" * 101]})
    assert not r.ok and r.error_kind == "invalid_input"
    # the exam_04 certificate (5835 transitions) is far above the call size limit
    big = _mock_dpda("dcfl_exam_04_stack_strategy.json")
    r = REGISTRY.invoke("dpda_run", {"dpda": big, "words": ["ab"]})
    assert not r.ok and r.error_kind == "input_too_large"


def test_dpda_run_warns_about_letters_no_transition_reads():
    r = REGISTRY.invoke("dpda_run", {"dpda": _mock_dpda("dcfl_anbncm_stack_strategy.json"),
                                     "words": ["abx"]})
    assert r.trust == "verified" and r.data["rejected"] == ["abx"]
    assert "x" in r.data["warnings"][0]


def test_dpda_run_id_is_stable_and_content_based():
    dpda = _mock_dpda("dcfl_anbncm_stack_strategy.json")
    a = REGISTRY.invoke("dpda_run", {"dpda": dpda}).data["dpda_id"]
    assert a == REGISTRY.invoke("dpda_run", {"dpda": copy.deepcopy(dpda)}).data["dpda_id"]
    other = copy.deepcopy(dpda)
    other["accept_states"] = ["q_pop"]
    assert REGISTRY.invoke("dpda_run", {"dpda": other}).data["dpda_id"] != a


def test_dpda_run_through_langchain_keeps_the_from_key():
    """LangChain hands nested pydantic models to the tool; the alias "from"
    must survive the round trip into REGISTRY.invoke."""
    lc = REGISTRY.as_langchain_tool("dpda_run")
    msg = lc.invoke(_tool_call("dpda_run", {"dpda": _mock_dpda("dcfl_anbncm_stack_strategy.json"),
                                            "words": ["aabbcc", "abb"]}))
    assert msg.artifact["ok"] is True and msg.artifact["trust"] == "verified", msg.artifact
    assert msg.artifact["data"]["accepted"] == ["aabbcc"]
    assert json.loads(msg.content)["trust"] == "verified"
    bad = lc.invoke(_tool_call("dpda_run", {"dpda": {"states": []}, "words": []}, "c2"))
    assert json.loads(bad.content)["error_kind"] == "invalid_input"


# ---------------------------------------------------------------------------
# closure_lookup
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "")).strip()


def test_every_closure_quote_is_verbatim_from_theory_section_1_4():
    section = dcfl.theory_section_1_4()
    assert section is not None
    assert section["text"].startswith("### 1.4. Замкнутость DCFL")
    assert dcfl.THEORY_SECTION_STATUS in section["text"].splitlines()[0]
    body = _norm(section["text"])
    for op, fact in dcfl.THEORY_CLOSURE_FACTS.items():
        assert _norm(fact["quote"]) in body, f"{op}: quote not found verbatim in THEORY.md §1.4"
    lines = (ROOT / "docs" / "THEORY.md").read_text(encoding="utf-8").splitlines()
    a, b = section["lines"]
    assert "\n".join(lines[a - 1:b]) == section["text"]


def test_closure_facts_agree_with_the_lib_table():
    lib_ops = {e["operation"] for e in closure_table.CLOSURE_TABLE}
    assert lib_ops <= set(dcfl.CLOSURE_OPERATIONS)
    for op in lib_ops:
        assert dcfl.THEORY_CLOSURE_FACTS[op]["closed"] is closure_table.is_closed(op)


@pytest.mark.parametrize("op", ["complement", "reg_intersection", "union", "reversal"])
def test_closure_lookup_table_operation(op):
    r = REGISTRY.invoke("closure_lookup", {"operation": op})
    assert r.ok and r.trust == "not_verified"
    e = r.data["entry"]
    assert e["in_lib_table"] is True and e["closed"] is closure_table.is_closed(op)
    assert e["implication_if_closed"] == closure_table.get_implication(op)
    assert e["proof_direction"] == closure_table.get_proof_direction(op)
    assert r.data["theory_ref"]["section"] == "1.4"


@pytest.mark.parametrize("op, closed", [
    ("intersection", False), ("difference", False), ("right_quotient_regular", True),
    ("min", True), ("max", True), ("left_quotient_word", True),
    ("left_quotient_regular", False), ("inverse_nondeterministic_transducer", False),
])
def test_closure_lookup_theory_only_operation(op, closed):
    r = REGISTRY.invoke("closure_lookup", {"operation": op})
    e = r.data["entry"]
    assert e["in_lib_table"] is False and e["closed"] is closed
    assert e["source"] == "docs/THEORY.md §1.4" and e["theory_quote"]


def test_closure_lookup_full_table_and_theory_text():
    r = REGISTRY.invoke("closure_lookup", {"include_theory_text": True})
    assert r.trust == "not_verified"
    assert [e["operation"] for e in r.data["table"]] == list(dcfl.CLOSURE_OPERATIONS)
    assert r.data["theory_text"] == dcfl.theory_section_1_4()["text"]
    assert r.data["theory_ref"]["lines"] == dcfl.theory_section_1_4()["lines"]


@pytest.mark.parametrize("rel", sorted(p.relative_to(ROOT).as_posix()
                                       for p in DCFL_EX.glob("task_*.json")))
def test_closure_scan_on_dcfl_examples_matches_the_lib(rel):
    ir = _load(rel)
    r = REGISTRY.invoke("closure_lookup", {"ir": ir})
    assert r.ok and r.trust == "not_verified"
    assert r.data["ir_errors"] == []
    lib = closure_table.closure_scan(ir)
    got = r.data["scan"]
    assert [s["operation"] for s in got["applicable_strategies"]] == \
        [s["operation"] for s in lib["applicable_strategies"]]
    assert [s["heuristic_weight"] for s in got["applicable_strategies"]] == \
        [s["confidence"] for s in lib["applicable_strategies"]]
    assert got["warnings"] == lib["warnings"]
    assert "confidence" not in json.dumps(r.data)  # no confidence-looking numbers for the model


def test_closure_scan_on_a_cfl_ir_reports_schema_errors():
    r = REGISTRY.invoke("closure_lookup", {"ir": _load("cfl_system/examples/task_anbncn.json")})
    assert r.ok and r.trust == "not_verified"
    assert any("task_type" in e for e in r.data["ir_errors"])


@pytest.mark.parametrize("args", [
    {"operation": "intersection_with_cfl"},
    {"operation": "Complement"},
    {"ir": {"source_text": "no language_spec"}},
    {"ir": {"language_spec": ["not", "an", "object"]}},
    {"include_theory_text": "yes please"},
])
def test_closure_lookup_rejects_bad_input(args):
    r = REGISTRY.invoke("closure_lookup", args)
    assert not r.ok and r.error_kind == "invalid_input" and r.trust == "not_verified"


# ---------------------------------------------------------------------------
# pattern_match
# ---------------------------------------------------------------------------

def test_pattern_codes_are_in_sync_with_the_db():
    assert set(dcfl.PATTERN_CODES) == {p["pattern"] for p in pattern_db.list_patterns()}


_DCFL_IRS = sorted(p.relative_to(ROOT).as_posix()
                   for p in [*DCFL_EX.glob("task_*.json"), *DCFL_EX.glob("eval/*.json")])


@pytest.mark.parametrize("rel", _DCFL_IRS)
def test_pattern_match_on_dcfl_examples_matches_the_lib(rel):
    ir = _load(rel)
    r = REGISTRY.invoke("pattern_match", {"ir": ir, "max_results": 20})
    assert r.ok and r.trust == "not_verified"
    assert r.data["ir_errors"] == []
    lib = pattern_db.match_patterns(ir)
    assert [(m["pattern"], m["score"], m["suggested_verdict"], m["suggested_method"])
            for m in r.data["matches"]] == \
        [(h["pattern"], h["score"], h["verdict"], h["method"]) for h in lib]
    assert r.data["n_matches"] == len(lib)
    assert "ADVISORY" in r.trust_reason


@pytest.mark.parametrize("rel, pattern, verdict", [
    ("dcfl_system/examples/task_wvaavRwR.json", "regex_constrained_palindrome", "non_dcfl"),
    ("dcfl_system/examples/task_grammar_aSSb.json", "grammar_lr_like", "dcfl"),
    ("dcfl_system/examples/task_anbncm.json", "single_length_cmp", "dcfl"),
])
def test_pattern_match_top_hit_on_exam_tasks(rel, pattern, verdict):
    r = REGISTRY.invoke("pattern_match", {"ir": _load(rel), "max_results": 1})
    assert len(r.data["matches"]) == 1
    top = r.data["matches"][0]
    assert (top["pattern"], top["suggested_verdict"]) == (pattern, verdict)


def test_pattern_match_can_be_wrong_so_it_stays_not_verified():
    """dcfl-04 ({aⁿbⁿcᵐ | n,m ≥ 0}, expected `dcfl` in docs/EVAL_SET.md, a trap):
    the pattern rules see three powers and suggest non_dcfl, while a
    deterministic automaton that agrees with the task oracle exists
    (test_dpda_run_on_a_correct_dcfl04_automaton_agrees_with_the_oracle).
    The hint never rises above not_verified."""
    r = REGISTRY.invoke("pattern_match", {"ir": _load("dcfl_system/examples/eval/dcfl-04.json")})
    assert r.data["matches"][0]["suggested_verdict"] == "non_dcfl"
    assert r.trust == "not_verified"


def test_pattern_match_lookup_and_listing():
    r = REGISTRY.invoke("pattern_match", {"pattern": "reversal_of_dcfl"})
    assert r.data["entry"]["suggested_method"] is None and r.trust == "not_verified"
    r = REGISTRY.invoke("pattern_match", {})
    assert [p["pattern"] for p in r.data["patterns"]] == list(dcfl.PATTERN_CODES)


def test_pattern_match_on_a_reg_ir_reports_schema_errors():
    ir = _load("agent_system/examples/task2_grammar_sasb.json")
    r = REGISTRY.invoke("pattern_match", {"ir": ir})
    assert r.ok and r.trust == "not_verified" and r.data["ir_errors"]


@pytest.mark.parametrize("args, kind", [
    # ll Format 1 IR: dict-valued variable domains crash pattern_db -> a clear input error
    ({"ir": _load("ll_system/examples/format1_anbn_union_ancn.json")}, "invalid_input"),
    ({"pattern": "sequential_palindromes"}, "invalid_input"),  # removed from the DB
    ({"pattern": "triple_count", "max_results": 0}, "invalid_input"),
    ({"ir": {"language_spec": "a^n b^n"}}, "invalid_input"),
    ({"ir": "not an object"}, "invalid_input"),
])
def test_pattern_match_rejects_bad_input(args, kind):
    r = REGISTRY.invoke("pattern_match", args)
    assert not r.ok and r.error_kind == kind and r.trust == "not_verified"


def test_pattern_match_ll_ir_error_names_the_expected_format():
    r = REGISTRY.invoke("pattern_match",
                        {"ir": _load("ll_system/examples/format1_anbn_union_ancn.json")})
    assert "DCFL IR format" in r.error
