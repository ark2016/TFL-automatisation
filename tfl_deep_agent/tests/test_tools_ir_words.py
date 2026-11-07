"""P1: tools/ir.py and tools/words.py -- describe_ir_schema, validate_ir,
ir_to_text, sample_words, check_membership.

No LLM, no network, no Docker. Every IR is one of the repository's own example
tasks; labels are cross-checked against the class libraries' oracles.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tfl_deep_agent.tools import ir as ir_tools
from tfl_deep_agent.tools import words as word_tools
from tfl_deep_agent.tools.base import REGISTRY, ToolInputError, ToolUnsupported, load_groups

ROOT = Path(__file__).resolve().parents[2]

REG_GRAMMAR = "agent_system/examples/task2_grammar_sasb.json"
REG_PALINDROME = "agent_system/examples/task1_palindrome_prefix_suffix.json"
REG_LITERALS = "agent_system/examples/task_z1wawbz2.json"
REG_BACKREF = "agent_system/examples/task3_regex_backref.json"
CFL_ANBNCN = "cfl_system/examples/task_anbncn.json"
CFL_W1W2W1W3 = "cfl_system/examples/task_w1w2w1w3.json"
CFL_FILTER = "cfl_system/examples/task_grammar_filter_49.json"
CFL_TASK11 = "cfl_system/examples/task11_ai_bj_between.json"
DCFL_ANBNCM = "dcfl_system/examples/task_anbncm.json"
DCFL_GRAMMAR = "dcfl_system/examples/task_grammar_aSSb.json"
DCFL_NO_ORACLE = "dcfl_system/examples/task_anb_cnbn.json"
LL_FORMAT1 = "ll_system/examples/format1_anbn_union_ancn.json"
LL_FORMAT2 = "ll_system/examples/format2_SabS_Sc_eps.json"
LL_FORMAT3 = "ll_system/examples/format3_simple_ll1.json"

EXAMPLES_BY_CLASS = {
    "reg": [REG_GRAMMAR, REG_PALINDROME, REG_LITERALS],
    "cfl": [CFL_ANBNCN, CFL_W1W2W1W3, CFL_FILTER],
    "dcfl": [DCFL_ANBNCM, DCFL_GRAMMAR],
    "ll": [LL_FORMAT2, LL_FORMAT3],
}


def load(rel: str) -> dict[str, Any]:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def call(name: str, **args: Any):
    return REGISTRY.invoke(name, args)


@pytest.fixture(scope="module", autouse=True)
def _groups():
    load_groups(["ir", "words"])


def lib_oracle(cls: str, ir: dict[str, Any]):
    """The class library's own oracle (bool | None per word)."""
    if cls == "reg":
        from agent_system.lib.oracle import oracle_from_ir

        return oracle_from_ir(ir)
    if cls == "cfl":
        from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir

        return cfl_oracle_from_ir(ir)
    if cls == "dcfl":
        from dcfl_system.lib.word_sampler import build_membership_oracle_from_ir

        return build_membership_oracle_from_ir(ir, max_word_len=100)
    from ll_system.lib.word_oracle import oracle_from_ll_ir

    return oracle_from_ll_ir(ir)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_tools_registered_with_ceilings():
    assert set(REGISTRY.names("ir")) >= {"describe_ir_schema", "validate_ir", "ir_to_text"}
    assert set(REGISTRY.names("words")) >= {"sample_words", "check_membership"}
    assert REGISTRY.get("validate_ir").ceiling == "well_formed"
    assert REGISTRY.get("sample_words").ceiling == "not_verified"     # reference only
    assert REGISTRY.get("check_membership").ceiling == "bounded_pass"


def test_langchain_conversion():
    tools = {t.name: t for t in REGISTRY.langchain_tools(groups=["ir", "words"])}
    assert {"describe_ir_schema", "validate_ir", "ir_to_text", "sample_words",
            "check_membership"} <= set(tools)
    content, artifact = tools["check_membership"].func(
        cls="reg", ir=load(REG_GRAMMAR), expected={"": True, "a": False})
    assert isinstance(content, str) and "bounded_pass" in content
    assert artifact["trust"] == "bounded_pass"


def test_args_forbid_extra_fields():
    res = call("validate_ir", cls="reg", ir=load(REG_GRAMMAR), confidence=0.9)
    assert not res.ok and res.error_kind == "invalid_input"


# ---------------------------------------------------------------------------
# describe_ir_schema
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", ["reg", "cfl", "dcfl", "ll"])
def test_describe_schema_example_validates(cls):
    res = call("describe_ir_schema", cls=cls)
    assert res.ok and res.trust == "not_verified"
    d = res.data
    assert d["task_types"] and d["language_spec_kinds"] and d["available_examples"]
    example = d["example"]
    assert example is not None and (ROOT / example["path"]).is_file()
    assert ir_tools.validation_errors(cls, example["ir"]) == []


def test_describe_schema_kind_filter():
    res = call("describe_ir_schema", cls="cfl", kind="exists_decomposition")
    assert res.ok
    assert set(res.data["kind_fields"]) == {"exists_decomposition"}
    assert res.data["example"]["ir"]["language_spec"]["kind"] == "exists_decomposition"


def test_describe_schema_unknown_kind_is_invalid_input():
    res = call("describe_ir_schema", cls="reg", kind="turing_machine")
    assert not res.ok and res.error_kind == "invalid_input"


def test_describe_schema_unknown_class_is_invalid_input():
    res = call("describe_ir_schema", cls="csl")
    assert not res.ok and res.error_kind == "invalid_input"


# ---------------------------------------------------------------------------
# validate_ir
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls,rel", [(c, r) for c, rs in EXAMPLES_BY_CLASS.items() for r in rs])
def test_validate_examples(cls, rel):
    res = call("validate_ir", cls=cls, ir=load(rel))
    assert res.ok and res.trust == "well_formed", res
    assert res.data["valid"] is True and res.data["errors"] == []
    assert res.data["ir_fingerprint"].startswith("ir-")
    assert res.data["oracle"]["available"] is True


def test_validate_reports_lean_and_oracle_status():
    reg = call("validate_ir", cls="reg", ir=load(REG_GRAMMAR)).data
    assert reg["lean_statement"]["available"] is True
    ll = call("validate_ir", cls="ll", ir=load(LL_FORMAT2)).data
    assert ll["lean_statement"]["available"] is False
    backref = call("validate_ir", cls="reg", ir=load(REG_BACKREF)).data
    assert backref["valid"] and backref["oracle"]["available"] is False
    assert any("back-reference" in w for w in backref["warnings"])


def test_validate_fingerprint_is_stable_and_order_free():
    ir = load(REG_GRAMMAR)
    shuffled = dict(reversed(list(ir.items())))
    a = call("validate_ir", cls="reg", ir=ir).data["ir_fingerprint"]
    b = call("validate_ir", cls="reg", ir=shuffled).data["ir_fingerprint"]
    assert a == b
    changed = copy.deepcopy(ir)
    changed["language_spec"]["alphabet"] = ["a", "b", "c"]
    assert call("validate_ir", cls="reg", ir=changed).data["ir_fingerprint"] != a


def test_validate_invalid_ir_is_not_verified_with_errors():
    ir = load(REG_GRAMMAR)
    ir["language_spec"]["kind"] = "nonsense"
    res = call("validate_ir", cls="reg", ir=ir)
    assert res.ok and res.trust == "not_verified"
    assert res.data["valid"] is False and res.data["errors"]


def test_validate_wrong_class_fails_validation():
    # The DCFL validator needs input_format / alphabet / language_spec.
    res = call("validate_ir", cls="dcfl", ir={"task_type": "classify_and_prove"})
    assert res.ok and res.data["valid"] is False and res.trust == "not_verified"


def test_validate_literals_warning():
    res = call("validate_ir", cls="reg", ir=load(REG_LITERALS))
    assert any("literal" in w for w in res.data["warnings"])


# ---------------------------------------------------------------------------
# ir_to_text
# ---------------------------------------------------------------------------

def test_ir_to_text_grammar():
    res = call("ir_to_text", cls="reg", ir=load(REG_GRAMMAR))
    assert res.ok and res.trust == "well_formed"
    text = res.data["text"]
    assert "S → SaSb | ε | A" in text and "A → bb | aa | bSb" in text
    assert res.data["ir_fingerprint"] in text


def test_ir_to_text_is_deterministic():
    a = call("ir_to_text", cls="cfl", ir=load(CFL_W1W2W1W3)).data["text"]
    b = call("ir_to_text", cls="cfl", ir=load(CFL_W1W2W1W3)).data["text"]
    assert a == b and "w1·w2·w1·w3" in a


def test_ir_to_text_examples_are_oracle_labels():
    ir = load(DCFL_ANBNCM)
    res = call("ir_to_text", cls="dcfl", ir=ir, n_examples=4, example_max_len=6)
    assert res.ok
    oracle = lib_oracle("dcfl", ir)
    for w in res.data["examples"]["in_language"]:
        assert oracle(w) is True
    for w in res.data["examples"]["not_in_language"]:
        assert oracle(w) is False


def test_ir_to_text_literals_quoted():
    text = call("ir_to_text", cls="reg", ir=load(REG_LITERALS)).data["text"]
    assert "'a'" in text and "'b'" in text


def test_ir_to_text_without_oracle_still_paraphrases():
    res = call("ir_to_text", cls="cfl", ir=load(CFL_TASK11))
    assert res.ok and res.trust == "well_formed"
    assert "нет оракула" in res.data["text"]


def test_ir_to_text_invalid_ir_fails():
    ir = load(REG_GRAMMAR)
    del ir["language_spec"]["rules"]
    res = call("ir_to_text", cls="reg", ir=ir)
    assert not res.ok and res.error_kind == "invalid_input" and res.trust == "not_verified"


# ---------------------------------------------------------------------------
# sample_words
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls,rel", [(c, r) for c, rs in EXAMPLES_BY_CLASS.items() for r in rs
                                     if r not in (REG_LITERALS, CFL_FILTER)])
def test_sample_words_labels_match_lib_oracle(cls, rel):
    ir = load(rel)
    res = call("sample_words", cls=cls, ir=ir, max_len=8, count=6, random_words=5)
    # oracle-only labelling checks no claim: reference, never a bounded check
    assert res.ok and res.trust == "not_verified", res
    d = res.data
    assert d["evidence_role"] == word_tools.REFERENCE_ROLE and d["oracle_exact"] is True
    assert d["labels_from"] == "oracle" and d["in_language"] and d["not_in_language"]
    assert res.bounds is not None and res.bounds.n_words == d["counts"]["decisive"]
    assert res.bounds.max_len <= 8
    assert d["ir_fingerprint"] in res.trust_reason
    oracle = lib_oracle(cls, ir)
    for w in d["in_language"]:
        assert oracle(w) is True, (rel, w)
    for w in d["not_in_language"]:
        assert oracle(w) is False, (rel, w)


def test_sample_words_known_members():
    res = call("sample_words", cls="cfl", ir=load(CFL_ANBNCN), max_len=9, count=10,
               random_words=0, use_generators=False)
    assert res.ok and res.trust == "not_verified"
    # Exhaustive up to length 6 over {a, b, c} (1093 words <= 2500), so these
    # are all the members of that length.
    assert res.data["exhaustive_up_to"] == 6
    assert set(w for w in res.data["in_language"] if len(w) <= 6) == {"", "abc", "aabbcc"}


def test_sample_words_respects_max_len_and_count():
    res = call("sample_words", cls="reg", ir=load(REG_GRAMMAR), max_len=5, count=3)
    d = res.data
    assert len(d["in_language"]) <= 3 and len(d["not_in_language"]) <= 3
    assert all(len(w) <= 5 for w in d["in_language"] + d["not_in_language"])


def test_sample_words_deterministic_for_seed():
    a = call("sample_words", cls="dcfl", ir=load(DCFL_GRAMMAR), max_len=12, seed=7).data
    b = call("sample_words", cls="dcfl", ir=load(DCFL_GRAMMAR), max_len=12, seed=7).data
    assert a["in_language"] == b["in_language"] and a["not_in_language"] == b["not_in_language"]


def test_sample_words_approximate_oracle_is_reference_too():
    # LL Format 1 has a comment-only constraint the LL oracle ignores.
    res = call("sample_words", cls="ll", ir=load(LL_FORMAT1), max_len=8)
    assert res.ok and res.trust == "not_verified"
    assert res.data["oracle"]["exact"] is False and "approximate" in res.trust_reason


def test_sample_words_exhaustive_length_is_only_fully_enumerated_lengths():
    """Regression: with max_enumerated < |Σ| + 1, length 1 was reported exhaustive although
    'b' was never enumerated, and the note claimed no word of length <= 1 is in L."""
    ir = {"task_type": "classify", "source_text": "b", "language_spec": {
        "kind": "regex", "alphabet": ["a", "b"], "pattern": "b", "has_backreferences": False}}
    res = call("sample_words", cls="reg", ir=ir, max_len=1, max_enumerated=2, random_words=0,
               use_generators=False)
    assert res.ok and res.data["exhaustive_up_to"] == 0 and res.bounds.exhaustive_up_to == 0
    assert res.data["in_language"] == []
    assert not any("length <= 1" in n for n in res.data["notes"])
    full = call("sample_words", cls="reg", ir=ir, max_len=1, max_enumerated=3, random_words=0,
                use_generators=False)
    assert full.data["exhaustive_up_to"] == 1 and full.data["in_language"] == ["b"]
    none = call("sample_words", cls="reg", ir=ir, max_len=0, max_enumerated=1, random_words=0,
                use_generators=False)
    assert none.data["exhaustive_up_to"] == 0


def test_sample_words_no_oracle_is_unsupported():
    res = call("sample_words", cls="cfl", ir=load(CFL_TASK11))
    assert not res.ok and res.error_kind == "unsupported" and res.trust == "not_verified"


def test_sample_words_invalid_ir_fails():
    res = call("sample_words", cls="ll", ir={"task_type": "nope"})
    assert not res.ok and res.error_kind == "invalid_input"


def test_sample_words_limits_enforced():
    res = call("sample_words", cls="reg", ir=load(REG_GRAMMAR), max_len=500)
    assert not res.ok and res.error_kind == "invalid_input"


def test_sample_words_tiny_budget_is_partial_not_wrong():
    res = call("sample_words", cls="cfl", ir=load(CFL_W1W2W1W3), max_len=10,
               max_enumerated=20000, time_budget_s=0.001)
    assert res.ok
    if res.data["truncated"]:
        assert res.bounds.truncated is True


# ---------------------------------------------------------------------------
# check_membership
# ---------------------------------------------------------------------------

def test_check_membership_grammar():
    res = call("check_membership", cls="reg", ir=load(REG_GRAMMAR), words=["", "ab", "a", "aabb"])
    # no expected labels: the oracle only answered, nothing was claimed
    assert res.ok and res.trust == "not_verified" and res.data["evidence_role"] == "reference"
    got = {r["word"]: r["in_language"] for r in res.data["results"]}
    oracle = lib_oracle("reg", load(REG_GRAMMAR))
    assert got == {w: oracle(w) for w in got}
    assert got["a"] is False and got[""] is True
    assert res.bounds.n_words == 4 and res.bounds.n_unknown == 0


def test_check_membership_literal_aware_reg_predicate():
    ir = load(REG_LITERALS)
    # s = z1 w 'a' w 'b' z2 needs a 'b'; agent_system's oracle accepts it
    # because it binds the literal elements as free parts.
    assert lib_oracle("reg", ir)("aaaaaaa") is True
    res = call("check_membership", cls="reg", ir=ir, words=["aaaaaaa", "aaaabaa", "baaabab"])
    got = {r["word"]: r["in_language"] for r in res.data["results"]}
    assert got == {"aaaaaaa": False, "aaaabaa": True, "baaabab": True}
    assert res.trust == "not_verified"
    claimed = call("check_membership", cls="reg", ir=ir, expected={"aaaaaaa": False, "aaaabaa": True})
    assert claimed.trust == "bounded_pass" and claimed.data["evidence_role"] == "claim_check"


def test_check_membership_expected_mismatch_refutes():
    res = call("check_membership", cls="dcfl", ir=load(DCFL_ANBNCM),
               expected={"abc": True, "aabc": True, "": False})
    assert res.ok and res.trust == "refuted"
    assert res.data["mismatches"] == [{"word": "aabc", "expected": True, "oracle": False}]
    assert "aabc" in res.trust_reason


def test_check_membership_expected_agreement_is_bounded_pass():
    res = call("check_membership", cls="ll", ir=load(LL_FORMAT3),
               expected={"ab": True, "ba": True, "": False, "aab": True, "abb": False})
    assert res.ok and res.trust == "bounded_pass" and res.data["mismatches"] == []


def test_check_membership_undecided_expected_word_is_not_verified():
    """Regression: an expected label past the exponent oracle's bound (40) was reported as
    bounded_pass with '1 word(s) agree with the expected labels'."""
    long_word = "a" * 20 + "b" * 20 + "c" * 20
    res = call("check_membership", cls="cfl", ir=load(CFL_ANBNCN), words=["abc"],
               expected={long_word: False})
    assert res.ok and res.trust == "not_verified"
    assert res.data["undecided_expected"] == [long_word]
    assert "agree" not in res.trust_reason and "not verified" in res.trust_reason
    assert res.bounds.n_words == 0 and res.bounds.n_unknown == 1
    # one decided and agreeing expected word next to an undecided one is still not verified
    mixed = call("check_membership", cls="cfl", ir=load(CFL_ANBNCN),
                 expected={"abc": True, long_word: False})
    assert mixed.trust == "not_verified"
    # trust is over the expected words only; extra words do not count
    ok = call("check_membership", cls="cfl", ir=load(CFL_ANBNCN), words=["a" * 41],
              expected={"abc": True})
    assert ok.trust == "bounded_pass" and ok.bounds.n_words == 1 and ok.bounds.n_unknown == 0


def test_check_membership_approximate_oracle_never_refutes():
    ir = load(LL_FORMAT1)
    res = call("check_membership", cls="ll", ir=ir, expected={"ab": False})
    assert res.ok and res.trust == "well_formed"
    assert res.data["mismatches"] and "not a refutation" in res.trust_reason


def test_check_membership_out_of_alphabet_is_invalid_input():
    res = call("check_membership", cls="cfl", ir=load(CFL_ANBNCN), words=["abd"])
    assert not res.ok and res.error_kind == "invalid_input"
    res = call("check_membership", cls="cfl", ir=load(CFL_ANBNCN), words=["ε"])
    assert not res.ok and res.error_kind == "invalid_input" and "''" in res.error


def test_check_membership_no_oracle_is_unsupported():
    res = call("check_membership", cls="dcfl", ir=load(DCFL_NO_ORACLE), words=["ab"])
    assert not res.ok and res.error_kind == "unsupported"


def test_check_membership_needs_a_word():
    res = call("check_membership", cls="reg", ir=load(REG_GRAMMAR), words=[])
    assert not res.ok and res.error_kind == "invalid_input"


def test_check_membership_all_unknown_is_not_verified():
    # 41 symbols is past the exponent-pattern oracle's length bound (40).
    res = call("check_membership", cls="cfl", ir=load(CFL_ANBNCN), words=["a" * 41])
    assert res.ok and res.trust == "not_verified"
    assert res.data["results"][0]["in_language"] is None
    assert res.data["counts"]["unknown"] == 1


def test_check_membership_too_many_words():
    res = call("check_membership", cls="reg", ir=load(REG_GRAMMAR), words=["a"] * 501)
    assert not res.ok and res.error_kind == "invalid_input"


# ---------------------------------------------------------------------------
# oracle_for (shared with other groups)
# ---------------------------------------------------------------------------

def test_oracle_for_errors_are_unknown_not_false():
    oracle = word_tools.ClassOracle("reg", ("a",), "test", True, fn=lambda w: 1 / 0)
    answer, note = oracle.query("a")
    assert answer is None and "ZeroDivisionError" in note


def test_oracle_for_cost_guard(monkeypatch):
    oracle = word_tools.oracle_for("reg", load(REG_LITERALS))
    assert oracle.cost is not None and oracle.cost(30) > oracle.cost(10)
    monkeypatch.setattr(word_tools, "MAX_SPLIT_WORK", oracle.cost(10))
    assert oracle.query("aaaabaa")[0] is True
    answer, note = oracle.query("ab" * 10)
    assert answer is None and "work limit" in note


def test_oracle_for_invalid_and_unsupported():
    with pytest.raises(ToolInputError):
        word_tools.oracle_for("reg", {"task_type": "x"})
    with pytest.raises(ToolUnsupported):
        word_tools.oracle_for("reg", load(REG_BACKREF))
