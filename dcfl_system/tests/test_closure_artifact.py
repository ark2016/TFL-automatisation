"""closure_reduction's executable artifact (R2″ policy, user decision 2026-11-10).

A constructive closure_reduction carries an artifact -- the known language S,
a DFA/regex for R, a homomorphism h -- that oracle_verifier simulates against
the task's own membership oracle on all short words, as it does for
stack_strategy's dpda: agreement with S given as a dpda -> bounded_pass (cap
0.85), agreement with S as a pattern or grammar (nothing shows S is a DCFL) ->
at most well_formed, a counterexample or a malformed artifact -> refuted, no
artifact -> at most well_formed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dcfl_system.lib import closure_artifact as ca
from dcfl_system.lib.agent_output_schema import schema_for
from dcfl_system.lib.oracle_verifier import verify_agent_results
from dcfl_system.orchestrator import MockRunner, _apply_verdict_gate, run_pipeline

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"
PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "closure_reduction.md"

A2KB2K_IR = json.loads((EXAMPLES_DIR / "task_a2kb2k.json").read_text(encoding="utf-8"))


def _mock(name: str) -> dict:
    return json.loads((MOCK_DIR / name).read_text(encoding="utf-8"))


def _ir(pattern: str, alphabet: list[str]) -> dict:
    return {
        "task_id": "t", "input_format": "set_builder", "task_type": "classify_and_prove_dcfl",
        "language_spec": {"kind": "set_builder", "word_pattern": pattern,
                          "variables": [], "constraints": []},
        "alphabet": alphabet,
    }


def _verify(operation, artifact, ir: dict, direction="constructive", **proof_fields) -> dict:
    proof = {
        "kind": "closure_reduction", "operation": operation, "direction": direction,
        "source_language": "S", "transformation": "T", "result_argument": "A",
        "artifact": artifact, **proof_fields,
    }
    proof = {k: v for k, v in proof.items() if v is not None or k == "artifact"}
    output = {
        "agent_name": "closure_reduction", "status": "success",
        "verdict": "dcfl" if direction == "constructive" else "non_dcfl",
        "proof_sketch": proof,
        "evidence": [], "confidence": 0.9, "errors": [],
    }
    return verify_agent_results({"closure_reduction": output}, ir)["closure_reduction"]


ANBN = {"alphabet": ["a", "b"], "pattern": "{a^n b^n | n >= 1}", "grammar": None, "dpda": None}

EVEN_DFA = _mock("dcfl_a2kb2k_closure_reduction.json")["proof_sketch"]["artifact"]["regular"]["dfa"]

# A deterministic automaton for S = {a^n b^n | n >= 1}.
ANBN_DPDA = {
    "states": ["q0", "q1", "q2"], "start": "q0", "accept_states": ["q2"],
    "accept_mode": "final_state", "stack_alphabet": ["Z", "A"], "initial_stack": ["Z"],
    "transitions": [
        {"from": "q0", "read": "a", "top": "Z", "to": "q0", "push": ["A", "Z"]},
        {"from": "q0", "read": "a", "top": "A", "to": "q0", "push": ["A", "A"]},
        {"from": "q0", "read": "b", "top": "A", "to": "q1", "push": []},
        {"from": "q1", "read": "b", "top": "A", "to": "q1", "push": []},
        {"from": "q1", "read": None, "top": "Z", "to": "q2", "push": ["Z"]},
    ],
}
ANBN_SOURCE = {"alphabet": ["a", "b"], "pattern": None, "grammar": None, "dpda": ANBN_DPDA}


# ---------------------------------------------------------------------------
# reg_intersection: the shipped mocks
# ---------------------------------------------------------------------------

def test_mock_reduction_reaches_bounded_pass():
    entry = verify_agent_results(
        {"closure_reduction": _mock("dcfl_a2kb2k_closure_reduction.json")}, A2KB2K_IR,
    )["closure_reduction"]
    assert entry["trust"] == "bounded_pass", entry
    details = entry["details"]
    assert details["equation"] == "L = S ∩ R"
    assert details["source_kind"] == "dpda" and details["regular_kind"] == "dfa"
    assert details["source_certificate"] == "dpda"
    assert details["words_checked"] >= 30
    assert "closure_artifact_simulation" in entry["checks_run"]
    assert entry["checks_passed"] == entry["checks_total"]


def test_mock_with_wrong_dfa_is_refuted_by_the_shortest_counterexample():
    entry = verify_agent_results(
        {"closure_reduction": _mock("dcfl_a2kb2k_wrong_dfa_closure_reduction.json")}, A2KB2K_IR,
    )["closure_reduction"]
    assert entry["trust"] == "refuted", entry
    counterexamples = entry["details"]["counterexamples"]
    # ab ∈ {a^n b^n} ∩ a⁺b⁺ but ab ∉ L: the shortest word where the sides differ.
    assert counterexamples[0] == "word='ab': in_L=False, in(S∩R)=True"
    assert any("L = S ∩ R" in issue for issue in entry["issues"])


def test_regex_form_of_r_is_simulated_too():
    entry = _verify("reg_intersection", {"source": ANBN_SOURCE, "regular": {"dfa": None, "regex": "(aa)*(bb)*"},
                                         "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "bounded_pass", entry
    assert entry["details"]["regular_kind"] == "regex"


def test_wrong_regex_is_refuted():
    entry = _verify("reg_intersection", {"source": ANBN, "regular": {"dfa": None, "regex": "a*b*"},
                                         "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "refuted", entry


def test_source_as_dpda_is_determinism_checked_and_simulated():
    entry = _verify("reg_intersection", {"source": ANBN_SOURCE, "regular": {"dfa": EVEN_DFA, "regex": None},
                                         "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "bounded_pass", entry
    assert entry["details"]["source_kind"] == "dpda"
    assert entry["details"]["determinism"] == "verified"


def test_nondeterministic_source_dpda_is_refuted():
    dpda = dict(ANBN_DPDA)
    dpda["transitions"] = ANBN_DPDA["transitions"] + [
        {"from": "q0", "read": "a", "top": "Z", "to": "q1", "push": ["Z"]},
    ]
    source = {"alphabet": ["a", "b"], "pattern": None, "grammar": None, "dpda": dpda}
    entry = _verify("reg_intersection", {"source": source, "regular": {"dfa": EVEN_DFA, "regex": None},
                                         "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "refuted", entry
    assert any("not deterministic" in issue for issue in entry["issues"])


@pytest.mark.parametrize("source", [
    ANBN,
    {"alphabet": ["a", "b"], "pattern": None, "dpda": None,
     "grammar": {"start": "S", "rules": [{"lhs": "S", "rhs": ["a", "S", "b"]},
                                         {"lhs": "S", "rhs": ["a", "b"]}]}},
], ids=["pattern", "grammar"])
def test_source_as_pattern_or_grammar_confirms_the_equality_but_stays_well_formed(source):
    # The equality L = S ∩ R holds, but nothing machine-checks that S is a DCFL.
    entry = _verify("reg_intersection", {"source": source, "regular": {"dfa": EVEN_DFA, "regex": None},
                                         "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "well_formed", entry
    assert entry["details"]["artifact"] == "bounded_pass"
    assert entry["details"]["source_certificate"] == "missing"
    assert entry["checks_passed"] == entry["checks_total"]
    assert any("artifact.source.dpda" in issue for issue in entry["issues"])


# ---------------------------------------------------------------------------
# Trivial reductions: the equality alone must not certify a non-DCFL language
# ---------------------------------------------------------------------------

ANBNCN = _ir("{a^n b^n c^n | n >= 1}", ["a", "b", "c"])
ANBNCN_SOURCE = {"alphabet": ["a", "b", "c"], "pattern": "{a^n b^n c^n | n >= 1}",
                 "grammar": None, "dpda": None}


def test_s_equal_to_l_with_r_all_words_does_not_reach_bounded_pass():
    # L is not even a CFL; L = L ∩ Σ* holds on every word.
    entry = _verify("reg_intersection", {"source": ANBNCN_SOURCE,
                                         "regular": {"dfa": None, "regex": "(a|b|c)*"},
                                         "homomorphism": None}, ANBNCN)
    assert entry["trust"] == "well_formed", entry
    assert entry["details"]["artifact"] == "bounded_pass"
    assert entry["details"]["words_checked"] >= 30


def test_s_equal_to_l_with_identity_homomorphism_does_not_reach_bounded_pass():
    identity = [{"letter": c, "image": c} for c in "abc"]
    entry = _verify("inv_homomorphism", {"source": ANBNCN_SOURCE, "regular": None,
                                         "homomorphism": identity}, ANBNCN)
    assert entry["trust"] == "well_formed", entry


def test_trivial_reduction_gives_no_dcfl_verdict_at_the_gate():
    output = _mock("dcfl_a2kb2k_closure_reduction.json")
    output["proof_sketch"]["artifact"] = {
        "source": ANBNCN_SOURCE, "regular": {"dfa": None, "regex": "(a|b|c)*"}, "homomorphism": None,
    }
    verification = verify_agent_results({"closure_reduction": output}, ANBNCN)
    assert verification["closure_reduction"]["trust"] == "well_formed"
    out = _apply_verdict_gate(_mock("dcfl_a2kb2k_reasoning.json"), {"closure_reduction": output},
                              verification, retry_count=0, max_retries=2)
    assert out["verdict"] != "dcfl", out["verdict_gate"]


@pytest.mark.parametrize("bad_dfa_change,needle", [
    ({"transitions": EVEN_DFA["transitions"] + [{"from": "a_even", "read": "a", "to": "b_even"}]},
     "not deterministic"),
    ({"start": "nowhere"}, "dfa.start"),
    ({"transitions": [{"from": "a_even", "read": "c", "to": "a_odd"}]}, "not a letter"),
    ({"accept": ["ghost"]}, "dfa.accept"),
])
def test_malformed_dfa_is_refuted(bad_dfa_change, needle):
    dfa = {**EVEN_DFA, **bad_dfa_change}
    entry = _verify("reg_intersection", {"source": ANBN, "regular": {"dfa": dfa, "regex": None},
                                         "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "refuted", entry
    assert any(needle in issue for issue in entry["issues"]), entry["issues"]


def test_two_descriptions_of_one_part_are_refuted():
    both_sources = {**ANBN, "grammar": {"start": "S", "rules": [{"lhs": "S", "rhs": ["a", "b"]}]}}
    entry = _verify("reg_intersection", {"source": both_sources, "regular": {"dfa": EVEN_DFA, "regex": None},
                                         "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "refuted"
    entry = _verify("reg_intersection", {"source": ANBN, "regular": {"dfa": EVEN_DFA, "regex": "(aa)*(bb)*"},
                                         "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "refuted"


# ---------------------------------------------------------------------------
# Missing / unchecked artifacts: as a missing DPDA, at most well_formed
# ---------------------------------------------------------------------------

def test_missing_artifact_stays_well_formed():
    entry = _verify("reg_intersection", None, A2KB2K_IR)
    assert entry["trust"] == "well_formed", entry
    assert entry["details"] == {"artifact": "missing"}
    assert "closure_artifact_simulation" not in entry["checks_run"]


def test_missing_regular_part_stays_well_formed():
    entry = _verify("reg_intersection", {"source": ANBN, "regular": None, "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "well_formed", entry
    assert entry["details"] == {"artifact": "missing"}


def test_missing_homomorphism_stays_well_formed():
    entry = _verify("inv_homomorphism", {"source": ANBN, "regular": None, "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "well_formed", entry


def test_unparseable_source_pattern_stays_well_formed():
    source = {**ANBN, "pattern": "the usual balanced language"}
    entry = _verify("reg_intersection", {"source": source, "regular": {"dfa": EVEN_DFA, "regex": None},
                                         "homomorphism": None}, A2KB2K_IR)
    assert entry["trust"] == "well_formed", entry
    assert entry["details"]["artifact"] == "unchecked"


def test_without_a_task_oracle_structure_is_still_checked():
    no_oracle = {**A2KB2K_IR, "language_spec": {"kind": "set_builder", "word_pattern": "prose only",
                                                "variables": [], "constraints": []}}
    good = _verify("reg_intersection", {"source": ANBN, "regular": {"dfa": EVEN_DFA, "regex": None},
                                        "homomorphism": None}, no_oracle)
    assert good["trust"] == "well_formed", good
    assert good["details"]["artifact"] == "unchecked"
    bad = _verify("reg_intersection", {"source": ANBN, "regular": {"dfa": {**EVEN_DFA, "start": "x"},
                                                                   "regex": None},
                                       "homomorphism": None}, no_oracle)
    assert bad["trust"] == "refuted", bad


# ---------------------------------------------------------------------------
# inv_homomorphism and complement
# ---------------------------------------------------------------------------

A_N_B_2N = _ir("{a^n b^(2n) | n >= 1}", ["a", "b"])


def test_inverse_homomorphism_constructive():
    # h(a) = aa, h(b) = b: h(w) ∈ {a^k b^k | k >= 1} iff w = a^n b^(2n), n >= 1.
    h = [{"letter": "a", "image": "aa"}, {"letter": "b", "image": "b"}]
    for operation in ("inv_homomorphism", "inverse_homomorphism"):
        entry = _verify(operation, {"source": ANBN_SOURCE, "regular": None, "homomorphism": h}, A_N_B_2N)
        assert entry["trust"] == "bounded_pass", entry
        assert entry["details"]["equation"] == "L = h⁻¹(S)"
    # The same equality with S as a pattern: confirmed, but not certified.
    entry = _verify("inv_homomorphism", {"source": ANBN, "regular": None, "homomorphism": h}, A_N_B_2N)
    assert entry["trust"] == "well_formed", entry
    assert entry["details"]["artifact"] == "bounded_pass"


# ---------------------------------------------------------------------------
# An artifact never raises the status over failed or absent structural checks
# ---------------------------------------------------------------------------

def _good_artifact() -> dict:
    return {"source": ANBN_SOURCE, "regular": {"dfa": EVEN_DFA, "regex": None}, "homomorphism": None}


@pytest.mark.parametrize("operation", [None, "union"])
def test_artifact_without_a_checkable_operation_stays_not_verified(operation):
    entry = _verify(operation, _good_artifact(), A2KB2K_IR, direction=None,
                    source_language="", transformation="")
    assert entry["trust"] == "not_verified", entry
    assert entry["checks_run"] == []


def test_inverse_homomorphism_without_direction_is_lifted_to_well_formed_at_most():
    h = [{"letter": "a", "image": "aa"}, {"letter": "b", "image": "b"}]
    entry = _verify("inv_homomorphism", {"source": ANBN_SOURCE, "regular": None, "homomorphism": h},
                    A_N_B_2N, direction=None)
    # No structural check ran; the artifact's equality holds but there is no
    # direction for it to vouch for, so it never gives bounded_pass.
    assert entry["trust"] == "well_formed", entry
    assert entry["checks_run"] == ["closure_artifact_simulation"]


def test_inverse_homomorphism_without_direction_and_without_artifact_is_not_verified():
    entry = _verify("inv_homomorphism", None, A_N_B_2N, direction=None)
    assert entry["trust"] == "not_verified", entry
    assert entry["checks_run"] == []


def test_failed_structural_check_is_not_lifted_by_a_passing_artifact():
    entry = _verify("reg_intersection", _good_artifact(), A2KB2K_IR, transformation="")
    assert entry["trust"] == "not_verified", entry
    assert "closure_artifact_simulation" in entry["checks_run"]


def test_wrong_homomorphism_is_refuted():
    h = [{"letter": "a", "image": "a"}, {"letter": "b", "image": "b"}]
    entry = _verify("inv_homomorphism", {"source": ANBN, "regular": None, "homomorphism": h}, A_N_B_2N)
    assert entry["trust"] == "refuted", entry
    assert entry["details"]["counterexamples"][0].startswith("word='ab'")


def test_homomorphism_undefined_on_a_letter_is_refuted():
    entry = _verify("inv_homomorphism", {"source": ANBN, "regular": None,
                                         "homomorphism": [{"letter": "a", "image": "aa"}]}, A_N_B_2N)
    assert entry["trust"] == "refuted", entry
    assert any("not defined on letter" in issue for issue in entry["issues"])


# Σ* ∖ L for L = {a^n b^m | n != m} is {a^n b^n | n >= 0} ∪ Σ* b a Σ*.
COMPLEMENT_GRAMMAR = {"start": "S", "rules": [
    {"lhs": "S", "rhs": ["T"]}, {"lhs": "S", "rhs": ["U"]},
    {"lhs": "T", "rhs": ["a", "T", "b"]}, {"lhs": "T", "rhs": []},
    {"lhs": "U", "rhs": ["X", "b", "a", "X"]},
    {"lhs": "X", "rhs": ["a", "X"]}, {"lhs": "X", "rhs": ["b", "X"]}, {"lhs": "X", "rhs": []},
]}
A_N_B_M_NEQ = _ir("{a^n b^m | n != m}", ["a", "b"])


# A deterministic automaton for the same S: q0 counts a's, q1 matches b's
# against them, q_over has read more b's than a's (still in a*b*), q_ba has
# read "ba" and accepts everything after it. Accepting: (q0, Z) = ε and
# (q1, Z) = a^n b^n, n >= 1 (ε-moves into the sink q_acc), and q_ba.
COMPLEMENT_DPDA = {
    "states": ["q0", "q1", "q_over", "q_ba", "q_acc"], "start": "q0",
    "accept_states": ["q_ba", "q_acc"], "accept_mode": "final_state",
    "stack_alphabet": ["Z", "A"], "initial_stack": ["Z"],
    "transitions": [
        {"from": "q0", "read": "a", "top": "Z", "to": "q0", "push": ["A", "Z"]},
        {"from": "q0", "read": "a", "top": "A", "to": "q0", "push": ["A", "A"]},
        {"from": "q0", "read": "b", "top": "A", "to": "q1", "push": []},
        {"from": "q0", "read": "b", "top": "Z", "to": "q_over", "push": ["Z"]},
        {"from": "q0", "read": None, "top": "Z", "to": "q_acc", "push": ["Z"]},
        {"from": "q1", "read": "b", "top": "A", "to": "q1", "push": []},
        {"from": "q1", "read": "b", "top": "Z", "to": "q_over", "push": ["Z"]},
        {"from": "q1", "read": "a", "top": "A", "to": "q_ba", "push": ["A"]},
        {"from": "q1", "read": "a", "top": "Z", "to": "q_ba", "push": ["Z"]},
        {"from": "q1", "read": None, "top": "Z", "to": "q_acc", "push": ["Z"]},
        {"from": "q_over", "read": "b", "top": "Z", "to": "q_over", "push": ["Z"]},
        {"from": "q_over", "read": "a", "top": "Z", "to": "q_ba", "push": ["Z"]},
    ] + [
        {"from": "q_ba", "read": c, "top": top, "to": "q_ba", "push": [top]}
        for c in "ab" for top in "ZA"
    ],
}


def test_complement_constructive():
    source = {"alphabet": ["a", "b"], "pattern": None, "grammar": None, "dpda": COMPLEMENT_DPDA}
    entry = _verify("complement", {"source": source, "regular": None, "homomorphism": None}, A_N_B_M_NEQ)
    assert entry["trust"] == "bounded_pass", entry
    assert entry["details"]["equation"] == "L = Σ* ∖ S"
    assert entry["details"]["determinism"] == "verified"


def test_complement_constructive_with_a_grammar_source_stays_well_formed():
    source = {"alphabet": ["a", "b"], "pattern": None, "grammar": COMPLEMENT_GRAMMAR, "dpda": None}
    entry = _verify("complement", {"source": source, "regular": None, "homomorphism": None}, A_N_B_M_NEQ)
    assert entry["trust"] == "well_formed", entry
    assert entry["details"]["artifact"] == "bounded_pass"


def test_complement_forgetting_the_unordered_words_is_refuted():
    # Σ* ∖ {a^n b^n | n >= 0} contains ba, which is not in L.
    source = {"alphabet": ["a", "b"], "pattern": "{a^n b^n | n >= 0}", "grammar": None, "dpda": None}
    entry = _verify("complement", {"source": source, "regular": None, "homomorphism": None}, A_N_B_M_NEQ)
    assert entry["trust"] == "refuted", entry
    assert entry["details"]["counterexamples"][0] == "word='ba': in_L=False, in(Σ*∖S)=True"


# ---------------------------------------------------------------------------
# Destructive direction: a wrong equality is refuted; a right one does not
# raise trust above the structural well_formed
# ---------------------------------------------------------------------------

ABC_OR = _ir("{a^i b^j c^k | i = j or j = k}", ["a", "b", "c"])


def test_destructive_equality_confirmed_does_not_raise_trust():
    source = {"alphabet": ["a", "b", "c"], "pattern": "{a^i b^j c^k | i = j or j = k}",
              "grammar": None, "dpda": None}
    entry = _verify("reg_intersection", {"source": source, "regular": {"dfa": None, "regex": "a*b*c*"},
                                         "homomorphism": None}, ABC_OR, direction="destructive")
    assert entry["trust"] == "well_formed", entry
    assert entry["details"]["artifact"] == "bounded_pass"
    assert entry["details"]["equation"] == "L ∩ R = S"


def test_destructive_wrong_equality_is_refuted():
    source = {"alphabet": ["a", "b", "c"], "pattern": "{a^n b^n c^n | n >= 0}",
              "grammar": None, "dpda": None}
    entry = _verify("reg_intersection", {"source": source, "regular": {"dfa": None, "regex": "a*b*c*"},
                                         "homomorphism": None}, ABC_OR, direction="destructive")
    assert entry["trust"] == "refuted", entry


def test_destructive_inverse_homomorphism_uses_the_source_alphabet():
    abc = _ir("{a^n b^n c^n | n >= 1}", ["a", "b", "c"])
    source = {"alphabet": ["x", "y", "z"], "pattern": "{x^n y^n z^n | n >= 1}", "grammar": None, "dpda": None}
    h = [{"letter": "x", "image": "a"}, {"letter": "y", "image": "b"}, {"letter": "z", "image": "c"}]
    entry = _verify("inv_homomorphism", {"source": source, "regular": None, "homomorphism": h}, abc,
                    direction="destructive")
    assert entry["trust"] == "well_formed", entry
    assert entry["details"]["equation"] == "h⁻¹(L) = S"
    assert entry["details"]["artifact"] == "bounded_pass"


def test_destructive_source_dpda_is_refuted():
    entry = _verify("complement", {"source": ANBN_SOURCE, "regular": None, "homomorphism": None},
                    A2KB2K_IR, direction="destructive")
    assert entry["trust"] == "refuted", entry
    assert any("destructive" in issue for issue in entry["issues"])


# ---------------------------------------------------------------------------
# Library-level helpers
# ---------------------------------------------------------------------------

def test_erasing_homomorphism_image():
    h = ca.build_homomorphism([{"letter": "a", "image": "ab"}, {"letter": "c", "image": "ε"}], ["a", "c"])
    assert ca.apply_homomorphism(h, "aca") == "abab"


def test_normalize_operation_aliases():
    assert ca.normalize_operation("inverse_homomorphism") == "inv_homomorphism"
    assert ca.normalize_operation("union") is None


def test_closure_table_names_the_artifact_parts_each_operation_needs():
    from dcfl_system.lib.closure_table import get_required_artifact
    assert get_required_artifact("complement") == ("source",)
    assert get_required_artifact("reg_intersection") == ("source", "regular")
    assert get_required_artifact("inv_homomorphism") == ("source", "homomorphism")
    assert get_required_artifact("inverse_homomorphism") == ("source", "homomorphism")
    assert get_required_artifact("union") == ()


@pytest.mark.parametrize("operation,part", [
    ("complement", "source"), ("reg_intersection", "source"), ("reg_intersection", "regular"),
    ("inv_homomorphism", "source"), ("inv_homomorphism", "homomorphism"),
])
def test_each_required_part_missing_is_treated_as_missing(operation, part):
    artifact = {"source": ANBN, "regular": {"dfa": EVEN_DFA, "regex": None},
                "homomorphism": [{"letter": "a", "image": "aa"}, {"letter": "b", "image": "b"}]}
    artifact[part] = None
    entry = _verify(operation, artifact, A2KB2K_IR)
    assert entry["trust"] == "well_formed", entry
    assert entry["details"] == {"artifact": "missing"}
    assert any(f"artifact.{part}" in issue for issue in entry["issues"])


# ---------------------------------------------------------------------------
# Schema, prompt and mocks agree
# ---------------------------------------------------------------------------

def _validator():
    jsonschema = pytest.importorskip("jsonschema")
    return jsonschema.Draft202012Validator(schema_for("closure_reduction"))


@pytest.mark.parametrize("name", sorted(p.name for p in MOCK_DIR.glob("*closure_reduction*.json")))
def test_every_closure_reduction_mock_fits_the_output_schema(name):
    errors = sorted(str(e.message) for e in _validator().iter_errors(_mock(name)))
    assert not errors, errors


def test_prompt_worked_example_fits_the_schema_and_passes_the_verifier():
    import re
    blocks = re.findall(r"```json\n(.*?)\n```", PROMPT.read_text(encoding="utf-8"), re.S)
    examples = []
    for block in blocks:
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("agent_name") == "closure_reduction":
            examples.append(obj)
    assert examples
    for example in examples:
        assert not list(_validator().iter_errors(example))
        entry = verify_agent_results({"closure_reduction": example}, A2KB2K_IR)["closure_reduction"]
        assert entry["trust"] == "bounded_pass", entry


def test_schema_artifact_is_required_and_nullable():
    proof = schema_for("closure_reduction")["properties"]["proof_sketch"]["anyOf"][0]
    assert "artifact" in proof["required"]
    assert {"type": "null"} in proof["properties"]["artifact"]["anyOf"]


# ---------------------------------------------------------------------------
# Verdict gate and end-to-end pipeline
# ---------------------------------------------------------------------------

def _gate(output: dict) -> tuple[dict, str]:
    """The gate on the trust ``verify_agent_results`` gives *output* (never an
    injected trust value)."""
    agent_results = {"closure_reduction": output}
    verification = verify_agent_results(agent_results, A2KB2K_IR)
    out = _apply_verdict_gate(_mock("dcfl_a2kb2k_reasoning.json"), agent_results, verification,
                              retry_count=0, max_retries=2)
    return out, verification["closure_reduction"]["trust"]


def test_gate_dpda_backed_reduction_caps_at_085():
    out, trust = _gate(_mock("dcfl_a2kb2k_closure_reduction.json"))
    assert trust == "bounded_pass"
    assert out["verdict"] == "dcfl" and out["confidence"] == 0.85


def _with_artifact_change(**change) -> dict:
    output = _mock("dcfl_a2kb2k_closure_reduction.json")
    artifact = output["proof_sketch"]["artifact"]
    output["proof_sketch"]["artifact"] = None if change.get("drop") else {**artifact, **change}
    return output


@pytest.mark.parametrize("output,expected_trust", [
    (_mock("dcfl_a2kb2k_wrong_dfa_closure_reduction.json"), "refuted"),
    (_with_artifact_change(drop=True), "well_formed"),
    (_with_artifact_change(source=ANBN), "well_formed"),
], ids=["refuted", "missing", "pattern_source"])
def test_gate_blocks_dcfl_without_a_certified_artifact(output, expected_trust):
    out, trust = _gate(output)
    assert trust == expected_trust
    assert out["verdict"] is None and out["action"] == "retry", out["verdict_gate"]


def test_pipeline_dcfl_from_closure_reduction_capped_at_085():
    result = run_pipeline(A2KB2K_IR, mock_runner=MockRunner(str(MOCK_DIR), "dcfl_a2kb2k"), formalize=False)
    assert result["verdict"] == "dcfl", result.get("verdict_gate")
    assert result["confidence"] == 0.85
    assert {"agent": "closure_reduction", "trust": "bounded_pass"} in result["verdict_gate"]["basis"]
    assert result["oracle_verification"]["closure_reduction"]["trust"] == "bounded_pass"


class _WrongDfaRunner(MockRunner):
    """dcfl_a2kb2k's mocks, with the wrong-DFA closure_reduction output."""

    def run_agent(self, agent_name, input_data=None):
        if agent_name == "closure_reduction":
            return _mock("dcfl_a2kb2k_wrong_dfa_closure_reduction.json")
        return super().run_agent(agent_name, input_data)


def test_pipeline_wrong_dfa_is_refuted_and_gives_no_dcfl_verdict():
    result = run_pipeline(A2KB2K_IR, mock_runner=_WrongDfaRunner(str(MOCK_DIR), "dcfl_a2kb2k"),
                          formalize=False)
    assert result["oracle_verification"]["closure_reduction"]["trust"] == "refuted"
    assert result["verdict"] != "dcfl", result.get("verdict_gate")
