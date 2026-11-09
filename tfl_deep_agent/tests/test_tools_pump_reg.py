"""REG gate parity (2026-11-08, Track A): ``pump_check_reg`` and ``regex_to_dfa(ir=...)``.

* ``pump_check_reg`` checks a pumping-lemma claim for a non-regular verdict
  (VERDICT_POLICY §4 reg/pumping): at most ``well_formed``, so the verdict gate
  publishes ``non_regular`` at confidence <= 0.55 (R6); a wrong claim is
  ``refuted`` and is no basis.
* ``regex_to_dfa(ir=...)`` compiles the task's own regex / linear grammar, read
  from the IR by the tool: ``verified`` (§1 "регулярность заданного regex/DFA"),
  the basis of ``regular`` at 0.98 only for a human-approved IR whose source
  text holds the definition, else a bounded check (0.85) like ``dfa_vs_ir``.
  A regex the model wrote is never that basis.

All checks run on the real eval IRs, in mock mode (no LLM, no network).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tfl_deep_agent import agent as A
from tfl_deep_agent.evidence import policy as P
from tfl_deep_agent.evidence.ledger import EvidenceLedger
from tfl_deep_agent.tools import reg as R
from tfl_deep_agent.tools.base import REGISTRY, load_groups
from tfl_deep_agent.tools.ir import ir_fingerprint

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "agent_system" / "examples" / "eval"


@pytest.fixture(scope="module", autouse=True)
def _tools_loaded() -> None:
    load_groups()


def load(task: str) -> dict[str, Any]:
    return json.loads((EVAL / f"{task}.json").read_text(encoding="utf-8"))


def regex_ir(pattern: str, alphabet: list[str], source: str | None = None) -> dict[str, Any]:
    return {"task_type": "classify_and_prove", "source_text": pattern if source is None else source,
            "language_spec": {"kind": "regex", "alphabet": alphabet, "pattern": pattern,
                              "has_backreferences": False}}


def human(fp: str) -> dict[str, Any]:
    """The HITL 1 binding of an IR a human approved (not --auto-approve)."""
    return {"status": "approved", "ir_fingerprint": fp, "auto": False}


def call(name: str, **args: Any) -> dict[str, Any]:
    return REGISTRY.invoke(name, args).model_dump()


def entry(eid: str, name: str, **args: Any) -> dict[str, Any]:
    """A gate entry as ``evidence.policy._gate_entry`` builds it from a ledger record."""
    r = call(name, **args)
    return {"id": eid, "tool": name, "ok": r["ok"], "trust": r["trust"], "data": r["data"] or {},
            "args": args, "trust_reason": r["trust_reason"], "bounds": r["bounds"], "error": r["error"]}


def gate(verdict: str, entries: list[dict[str, Any]], *, fp: str | None, cited: list[str] = (),
         formalization: dict[str, Any] | None = None) -> dict[str, Any]:
    report = {"verdict": verdict, "language_class": "reg", "ir_fingerprint": fp}
    return A.verdict_gate(report, entries, list(cited), formalization=formalization)


# ---------------------------------------------------------------------------
# pump_check_reg: the tool
# ---------------------------------------------------------------------------

def test_registered_in_the_reg_group_with_a_well_formed_ceiling() -> None:
    assert "pump_check_reg" in REGISTRY.names("reg")
    assert R.MAX_PUMP_I == 6


def test_reg07_without_an_oracle_checks_the_structure_only() -> None:
    r = call("pump_check_reg", ir=load("reg-07"), word="a^(p^2)", exit_i=[2])
    assert r["ok"] and r["trust"] == "well_formed"
    d = r["data"]
    assert d["semantic_check"] is False and d["oracle"] is None and d["refutations"] == []
    assert [len(x["word"]) for x in d["per_p"]] == [4, 9, 16]       # a^(p^2) at p = 2, 3, 4
    assert "NOT checked" in r["trust_reason"]


def test_reg07_structural_refutation_when_the_word_is_too_short() -> None:
    r = call("pump_check_reg", ir=load("reg-07"), word="a^(p-1)", exit_i=[2])
    assert r["ok"] and r["trust"] == "refuted"
    assert any("|w(p)|" in x for x in r["data"]["refutations"])


@pytest.mark.parametrize("task,word,exits", [
    ("reg-13", "a^p b^p", [2]),      # grammar S -> aSb | ab, exact CYK oracle
    ("reg-13", "a^p b^p", [0]),
    ("reg-01", "a^p b^p", [0, 2]),   # natural {a^n b^n | n >= 0}: exponent-notation oracle
])
def test_a_correct_claim_closes_every_split_and_stays_well_formed(task: str, word: str, exits: list[int]) -> None:
    r = call("pump_check_reg", ir=load(task), word=word, exit_i=exits)
    assert r["ok"] and r["trust"] == "well_formed", r["trust_reason"]
    d, b = r["data"], r["bounds"]
    assert d["semantic_check"] is True and d["refutations"] == []
    assert b["n_splits"] == b["splits_closed"] > 0 and b["unresolved_splits"] == 0
    assert d["split_condition"].startswith("|xy| <= p")
    assert "never a proof for all p" in r["trust_reason"]


@pytest.mark.parametrize("task,word,exits", [
    ("reg-01", "a^p b^(p+1)", [2]),        # the chosen word is outside L
    ("reg-13", "(ab)^p", [2]),
])
def test_a_word_outside_L_is_refuted(task: str, word: str, exits: list[int]) -> None:
    r = call("pump_check_reg", ir=load(task), word=word, exit_i=exits)
    assert r["ok"] and r["trust"] == "refuted"
    assert any("NOT in L" in x for x in r["data"]["refutations"]), r["data"]["refutations"]


def test_a_split_in_L_at_every_claimed_exit_refutes_the_claim() -> None:
    # reg-04 is regular: on w = (ab)^p a the split y = ab stays in L at i = 0, the claimed exit.
    # exit_i is the argument's own claim (some i in exit_i leaves L on every split); the exact
    # regex oracle shows it false at that p: refuted, as pump_check_cfl claim mode (§1 refuted).
    r = call("pump_check_reg", ir=load("reg-04"), word="(ab)^p a", exit_i=[0])
    assert r["ok"] and r["trust"] == "refuted", r["trust_reason"]
    assert r["data"]["exit_failures"] and r["data"]["exit_failures"][0] in r["data"]["refutations"]
    assert "EVERY claimed exit" in r["trust_reason"] and "exits_at" in r["trust_reason"]
    assert "not all i" not in r["trust_reason"] and "proves nothing for all" not in r["trust_reason"]
    ex = [e for x in r["data"]["per_p"] for e in x.get("examples", []) if e["status"] == "claimed_exit_fails"]
    assert ex and all(0 in e["in_L_at"] for e in ex)


def a_star_ir() -> dict[str, Any]:
    return regex_ir("a*", ["a"])


def test_a_star_with_exit_2_is_refuted() -> None:
    # L = a*: every x y^2 z of a^p is in L, so "every split leaves L at i = 2" is false.
    r = call("pump_check_reg", ir=a_star_ir(), word="a^p", exit_i=[2])
    assert r["ok"] and r["trust"] == "refuted", r["trust_reason"]
    assert r["data"]["oracle"]["exact"] is True
    assert len(r["data"]["exit_failures"]) == len(r["data"]["p_values"]) == 3
    assert r["bounds"]["splits_closed"] == 0


def test_a_star_with_an_approximate_oracle_is_not_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    approx = R.ClassOracle("reg", ("a",), "test (approximate)", False, lambda w: set(w) <= {"a"},
                           note="APPROXIMATE test oracle")
    monkeypatch.setattr(R, "_pump_oracle", lambda ir: (approx, []))
    r = call("pump_check_reg", ir=a_star_ir(), word="a^p", exit_i=[2])
    assert r["ok"] and r["trust"] == "not_verified", r["trust_reason"]
    assert r["data"]["exit_failures"] and "not a refutation" in r["trust_reason"]


def test_a_star_with_unknown_answers_is_not_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    # The oracle decides w(p) = a^(10p) (lengths 20, 30, 40) and nothing else: every pumped word
    # at the claimed exit is unknown, which is never "in L" nor "not in L".
    lengths = {20, 30, 40}
    unknown = R.ClassOracle("reg", ("a",), "test (bounded)", True,
                            lambda w: (set(w) <= {"a"}) if len(w) in lengths else None)
    monkeypatch.setattr(R, "_pump_oracle", lambda ir: (unknown, []))
    r = call("pump_check_reg", ir=a_star_ir(), word="a^(10p)", exit_i=[2])
    assert r["ok"] and r["trust"] == "well_formed", r["trust_reason"]
    assert r["data"]["refutations"] == [] and r["data"]["exit_failures"] == []
    assert r["bounds"]["splits_closed"] == 0 and r["bounds"]["unresolved_splits"] == r["bounds"]["n_splits"] > 0
    assert "undecided" in r["trust_reason"]


def test_a_p_dependent_exit_cannot_be_stated_and_its_fixed_exits_are_refuted() -> None:
    # reg-10 {a^i b^j | i != j}: the textbook proof w = a^p b^(p+p!), i = p!/|y| + 1 has an exit
    # that depends on p and |y|, which exit_i cannot state. Stated with the fixed exits 0, 2..6
    # on a^p b^(p+6), the split y = a stays in L at every one of them (p = 4: a^(3+i) b^10, i <= 6),
    # so the claim as stated is false: refuted. The skill says so (an argument of this kind needs a
    # fixed-exit word or another method).
    r = call("pump_check_reg", ir=load("reg-10"), word="a^p b^(p+6)", exit_i=[0, 2, 3, 4, 5, 6])
    assert r["ok"] and r["trust"] == "refuted", r["trust_reason"]
    assert r["data"]["exit_failures"]
    assert "cannot be checked here" in r["trust_reason"]


def test_the_policy_p_values_are_always_checked() -> None:
    # (aaa)* is regular. p_values [1, 2] alone would close every split of a^(3p) at exit 2;
    # p = 3 (always checked, VERDICT_POLICY §4) has the split y = aaa, which stays in L.
    ir = regex_ir("(aaa)*", ["a"])
    r = call("pump_check_reg", ir=ir, word="a^(3p)", exit_i=[2], p_values=[1, 2])
    assert r["data"]["p_values"] == [1, 2, 3, 4]
    assert r["ok"] and r["trust"] == "refuted" and r["data"]["exit_failures"]
    assert all("at p = 1," not in x and "at p = 2," not in x for x in r["data"]["exit_failures"])


def test_p_min_shifts_the_policy_p_values_up() -> None:
    r = call("pump_check_reg", ir=load("reg-13"), word="a^p b^p", exit_i=[0, 2], p_min=3, p_values=[1, 7])
    assert r["ok"] and r["trust"] == "well_formed"
    assert r["data"]["p_values"] == [3, 4, 5, 7] and r["data"]["p_min"] == 3


@pytest.mark.parametrize("args,needle", [
    ({"word": "a^p b^p", "exit_i": [1]}, "exit"),          # i = 1 pumps nothing
    ({"word": "a^p b^q", "exit_i": [2]}, "variable"),
    ({"word": "a^p b^p", "exit_i": [2], "p_values": [13]}, ""),
    ({"word": "a^p b^p", "exit_i": [2], "p_min": 11}, ""),
    ({"word": "a^p b^p", "exit_i": [7]}, ""),
])
def test_invalid_claims_are_invalid_input(args: dict[str, Any], needle: str) -> None:
    r = call("pump_check_reg", ir=load("reg-13"), **args)
    assert not r["ok"] and r["error_kind"] == "invalid_input"
    assert needle in (r["error"] or "")


def test_larger_p_values_are_added_to_the_policy_ones() -> None:
    r = call("pump_check_reg", ir=load("reg-13"), word="a^p b^p", exit_i=[0, 2], p_values=[3, 5])
    assert r["ok"] and r["trust"] == "well_formed" and r["data"]["p_values"] == [2, 3, 4, 5]


def test_a_regular_language_with_a_long_period_closes_every_small_split() -> None:
    # THEORY §2.0 / VERDICT_POLICY §4: closing p = 2, 3, 4 does not prove the claim;
    # (a^5)* is regular, yet every split of a^(5p) leaves L at i = 0 for p <= 4.
    r = call("pump_check_reg", ir=regex_ir("(aaaaa)*", ["a"]), word="a^(5p)", exit_i=[0])
    assert r["ok"] and r["trust"] == "well_formed"
    assert r["bounds"]["n_splits"] == r["bounds"]["splits_closed"] > 0
    assert "period" in r["data"]["scope"]


@pytest.mark.parametrize("description", [
    "{a^n b^m | n != m, n, m >= 0}",       # cfl_system's parser reads it as the empty language
    "{a^n b^m | m >= 0, n != m}",          # ... or drops n > m
])
def test_a_misread_exponent_description_is_an_approximate_oracle(description: str) -> None:
    ir = {"task_type": "classify_and_prove", "source_text": description,
          "language_spec": {"kind": "natural", "alphabet": ["a", "b"], "description": description}}
    r = call("pump_check_reg", ir=ir, word="a^p b^(p+12)", exit_i=[0])
    assert r["ok"] and r["trust"] != "refuted"
    assert r["data"]["oracle"]["exact"] is False
    assert any("comma-separated" in n for n in r["data"]["oracle_notes"])


def test_an_exponent_description_that_accepts_nothing_is_approximate() -> None:
    oracle, notes = R._pump_oracle({"task_type": "classify_and_prove", "source_text": "x",
                                    "language_spec": {"kind": "natural", "alphabet": ["a", "b"],
                                                      "description": "{a^n b^n | n > n}"}})
    if oracle is not None:  # the parser may also reject it outright
        assert oracle.exact is False and any("accepts no word" in n for n in notes)
    reg01 = R._pump_oracle(load("reg-01"))[0]
    assert reg01 is not None and reg01.exact is True


# ---------------------------------------------------------------------------
# pump_check_reg: the verdict gate (R1, R6)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task,word,exits", [
    ("reg-07", "a^(p^2)", [2]),
    ("reg-13", "a^p b^p", [0, 2]),
    ("reg-01", "a^p b^p", [0, 2]),
])
def test_pumping_reaches_non_regular_at_055(task: str, word: str, exits: list[int]) -> None:
    ir = load(task)
    e = entry("ev-pump", "pump_check_reg", ir=ir, word=word, exit_i=exits)
    g = gate("not regular", [e], fp=ir_fingerprint(ir), cited=["ev-pump"])
    assert g["trust"] == "well_formed" and g["confidence_cap"] == 0.55
    assert g["verdict_token"] == "non_regular" and g["basis"] == ["ev-pump"]


def test_pumping_through_the_ledger_and_evaluate_report(tmp_path: Path) -> None:
    ir = load("reg-07")
    args = {"ir": ir, "word": "a^(p^2)", "exit_i": [2]}
    res = REGISTRY.invoke("pump_check_reg", args)
    led = EvidenceLedger.open(tmp_path)
    rec = led.append(tool="pump_check_reg", tool_call_id="c1", args=args, result=res.model_dump(),
                     trust=res.trust, trust_reason=res.trust_reason or "", has_tool_result=True, role="main")
    report = {"verdict": "non_regular", "language_class": "reg", "ir_fingerprint": ir_fingerprint(ir),
              "evidence_ids": [rec.id], "confidence": 0.9}
    ev = P.evaluate_report(report, led)
    assert ev["trust"] == "well_formed" and ev["confidence"] == 0.55 and ev["verdict"] == "non_regular"


def test_a_refuted_pumping_claim_is_no_basis_and_blocks_when_cited() -> None:
    ir = load("reg-13")
    e = entry("ev-bad", "pump_check_reg", ir=ir, word="(ab)^p", exit_i=[2])
    assert e["trust"] == "refuted"
    g = gate("not regular", [e], fp=ir_fingerprint(ir))
    assert g["trust"] == "not_verified" and g["verdict_token"] == "partial"
    g = gate("not regular", [e], fp=ir_fingerprint(ir), cited=["ev-bad"])
    assert g["trust"] == "not_verified" and any("R2" in x for x in g["verdict_gate"])


def test_a_cited_exit_refutation_blocks_non_regular_like_r2() -> None:
    # reg-01 {a^n b^n | n >= 0}: a correct claim is a well_formed basis; a second claim whose
    # stated exit fails (w = a^(p-1) b^(p-1), exit 0: at p = 2 the split y = ab gives
    # x y^0 z = ε ∈ L) is refuted. Cited, it blocks the verdict exactly as R2 blocks a cited
    # refuted result of the verdict's own side; uncited, the correct claim stays the basis.
    ir = load("reg-01")
    fp = ir_fingerprint(ir)
    good = entry("ev-good", "pump_check_reg", ir=ir, word="a^p b^p", exit_i=[0, 2])
    bad = entry("ev-bad", "pump_check_reg", ir=ir, word="a^(p-1) b^(p-1)", exit_i=[0])
    assert good["trust"] == "well_formed"
    assert bad["trust"] == "refuted" and bad["data"]["exit_failures"], bad["trust_reason"]
    assert A._classify(bad, "reg", None, [bad]) == [("destructive", "refuted")]
    g = gate("not regular", [good, bad], fp=fp, cited=["ev-good", "ev-bad"])
    assert g["trust"] == "not_verified" and g["verdict_token"] == "partial"
    assert any("R2" in x and "ev-bad" in x for x in g["verdict_gate"]), g["verdict_gate"]
    g = gate("not regular", [good, bad], fp=fp, cited=["ev-good"])
    assert g["trust"] == "well_formed" and g["confidence_cap"] == 0.55 and g["basis"] == ["ev-good"]


def test_the_gate_clamps_pumping_to_well_formed_whatever_the_record_says() -> None:
    forged = {"id": "x", "tool": "pump_check_reg", "ok": True, "trust": "verified",
              "data": {"cls": "reg", "mode": "claim", "ir_fingerprint": "fp"}, "args": {}}
    assert A._classify(forged, "reg", None, [forged]) == [("destructive", "well_formed")]
    assert A._classify({**forged, "data": {"cls": "cfl", "mode": "claim"}}, "reg", None, [forged]) == []
    assert A._classify(forged, "cfl", None, [forged]) == []


def test_pumping_counts_against_a_regular_verdict() -> None:
    # R3 on a regular language whose period (5) exceeds the policy p: the pumping check
    # closes every split (well_formed) and contradicts the constructive basis.
    ir = regex_ir("(aaaaa)*", ["a"])
    fp = ir_fingerprint(ir)
    pump = entry("ev-pump", "pump_check_reg", ir=ir, word="a^(5p)", exit_i=[0])
    assert pump["trust"] == "well_formed"
    dfa = call("regex_to_dfa", regex="(aaaaa)*", alphabet=["a"])["data"]["dfa"]
    bounded = entry("ev-dfa-vs-ir", "dfa_vs_ir", cls="reg", ir=ir, dfa=dfa)
    assert bounded["trust"] == "bounded_pass"
    g = gate("regular", [pump, bounded], fp=fp)
    assert g["contradiction"] is True and g["confidence_cap"] == 0.50 and g["verdict_token"] == "partial"
    assert any(x.startswith("R3") and "ev-pump" in x for x in g["verdict_gate"]), g["verdict_gate"]
    # a verified basis (the task's own regex, IR approved by a human) keeps the verdict at 0.85
    task = entry("ev-dfa", "regex_to_dfa", ir=ir)
    g = gate("regular", [pump, task], fp=None, formalization=human(fp))
    assert g["trust"] == "verified" and g["contradiction"] is True and g["confidence_cap"] == 0.85
    assert g["verdict_token"] == "regular"
    assert any(x.startswith("R3") and "ev-pump" in x for x in g["verdict_gate"]), g["verdict_gate"]
    # ... and the pumping verdict loses against it
    g = gate("not regular", [pump, task], fp=None, formalization=human(fp))
    assert g["trust"] == "not_verified" and any("R3" in x for x in g["verdict_gate"])


# ---------------------------------------------------------------------------
# regex_to_dfa(ir=...): the task's own definition
# ---------------------------------------------------------------------------

def test_regex_to_dfa_ir_mode_on_reg04() -> None:
    ir = load("reg-04")
    r = call("regex_to_dfa", ir=ir)
    assert r["ok"] and r["trust"] == "verified"
    d = r["data"]
    assert d["regex"] == ir["language_spec"]["pattern"] == "(ab)*a|b"
    assert d["source"] == "ir.language_spec" and d["task_definition"] is True and d["cls"] == "reg"
    assert d["ir_fingerprint"] == ir_fingerprint(ir)
    assert d["oracle_cross_check"]["n_words"] > 100 and d["oracle_cross_check"]["n_unknown"] == 0


@pytest.mark.parametrize("task", ["reg-07", "reg-12", "reg-13"])
def test_regex_to_dfa_ir_mode_is_unsupported_without_a_regular_definition(task: str) -> None:
    r = call("regex_to_dfa", ir=load(task))
    assert not r["ok"] and r["error_kind"] == "unsupported"


def test_regex_to_dfa_takes_exactly_one_source() -> None:
    r = call("regex_to_dfa", ir=load("reg-04"), regex="a")
    assert not r["ok"] and r["error_kind"] == "invalid_input"


def test_reg04_reaches_regular_at_verified_through_the_task_regex() -> None:
    ir = load("reg-04")
    fp = ir_fingerprint(ir)
    e = entry("ev-dfa", "regex_to_dfa", ir=ir)
    assert e["data"]["definition_in_source_text"] is True   # "(ab)*a | b" holds "(ab)*a|b"
    # HITL 1 on, a human approved the IR: the approved IR names it, whatever the report says
    g = gate("regular", [e], fp=None, cited=["ev-dfa"], formalization=human(fp))
    assert g["trust"] == "verified" and g["confidence_cap"] == 0.98 and g["basis"] == ["ev-dfa"]
    assert g["verdict_token"] == "regular" and g["verdict_gate"] == []


@pytest.mark.parametrize("formalization,fp,why", [
    (None, "named", "HITL 1 is off"),                        # the report's own ir_fingerprint only
    (None, None, "HITL 1 is off"),
    ("auto", None, "auto-approved"),                         # every tfl-eval deep run
])
def test_the_task_regex_without_a_human_approval_is_only_bounded(formalization: str | None, fp: str | None,
                                                                 why: str) -> None:
    ir = load("reg-04")
    real = ir_fingerprint(ir)
    e = entry("ev-dfa", "regex_to_dfa", ir=ir)
    form = {"status": "approved", "ir_fingerprint": real, "auto": True} if formalization == "auto" else None
    g = gate("regular", [e], fp=real if fp == "named" else None, formalization=form)
    assert g["trust"] == "bounded_pass" and g["confidence_cap"] == 0.85 and g["basis"] == ["ev-dfa"]
    assert any(why in x and "bounded_pass" in x for x in g["verdict_gate"]), g["verdict_gate"]


@pytest.mark.parametrize("auto", [True, False])
def test_a_natural_task_re_encoded_as_a_regex_never_reaches_098(auto: bool) -> None:
    # reg-02 {w | #a(w) = #b(w)} (non-regular) formalized by the ir-builder as the regex (a|b)*:
    # the regex does not occur in the task text, so even a human approval gives no verified basis.
    src = load("reg-02")["source_text"]
    ir = regex_ir("(a|b)*", ["a", "b"], source=src)
    fp = ir_fingerprint(ir)
    e = entry("ev-dfa", "regex_to_dfa", ir=ir)
    assert e["trust"] == "verified" and e["data"]["definition_in_source_text"] is False
    g = gate("regular", [e], fp=None, formalization={"status": "approved", "ir_fingerprint": fp, "auto": auto})
    assert g["trust"] == "bounded_pass" and g["confidence_cap"] == 0.85
    g = gate("regular", [e], fp=fp)   # HITL 1 off, the report names the IR
    assert g["trust"] == "bounded_pass" and g["confidence_cap"] == 0.85


def test_the_definition_must_occur_in_the_source_text() -> None:
    assert R._definition_in_source_text({"source_text": "L = (ab)*a | b ?"}, "(ab)*a|b", None) is True
    assert R._definition_in_source_text({"source_text": "{w | #a(w) = #b(w)}"}, "(a|b)*", None) is False
    assert R._definition_in_source_text({"source_text": ""}, "a", None) is False
    g = {"rules": [{"lhs": "S", "rhs": ["a", "S"]}, {"lhs": "S", "rhs": ["b"]}, {"lhs": "S", "rhs": []}]}
    assert R._definition_in_source_text({"source_text": "S -> aS | b | ε"}, None, g) is True
    assert R._definition_in_source_text({"source_text": "S -> bS | b"}, None, g) is False


def test_the_task_regex_of_another_ir_is_excluded() -> None:
    e = entry("ev-dfa", "regex_to_dfa", ir=load("reg-04"))
    g = gate("regular", [e], fp="ir-000000000000")
    assert g["trust"] == "not_verified" and g["excluded_ir_mismatch"] == ["ev-dfa"]


def test_a_model_written_regex_is_no_basis() -> None:
    ir = load("reg-04")
    fp = ir_fingerprint(ir)
    e = entry("ev-mine", "regex_to_dfa", regex="(ab)*a|b", alphabet=["a", "b"])  # same text, typed by the model
    assert e["trust"] == "verified" and e["data"]["task_definition"] is False
    g = gate("regular", [e], fp=fp, cited=["ev-mine"])
    assert g["trust"] == "not_verified" and g["basis"] == []
    assert A._classify(e, "reg", None, [e], ir_named=True) == []


@pytest.mark.parametrize("data", [
    {"task_definition": True, "source": "regex", "cls": "reg", "ir_fingerprint": "fp"},
    {"task_definition": "yes", "source": "ir.language_spec", "cls": "reg", "ir_fingerprint": "fp"},
    {"task_definition": True, "source": "ir.language_spec", "cls": "reg"},
])
def test_only_the_tools_own_ir_mode_record_counts(data: dict[str, Any]) -> None:
    e = {"id": "x", "tool": "regex_to_dfa", "ok": True, "trust": "verified",
         "data": {**data, "definition_in_source_text": True, "oracle_cross_check": {"n_words": 10}}, "args": {}}
    assert A._classify(e, "reg", None, [e], ir_named=True, ir_human_approved=True) == []
    ok = {**e, "data": {"task_definition": True, "source": "ir.language_spec", "cls": "reg",
                        "ir_fingerprint": "fp", "definition_in_source_text": True,
                        "oracle_cross_check": {"n_words": 10}}}
    assert A._classify(ok, "reg", None, [ok], ir_named=True, ir_human_approved=True) == [
        ("constructive", "verified")]
    assert A._classify(ok, "reg", None, [ok], ir_named=True) == [("constructive", "bounded_pass")]
    off_text = {**ok, "data": {**ok["data"], "definition_in_source_text": False}}
    assert A._classify(off_text, "reg", None, [ok], ir_named=True, ir_human_approved=True) == [
        ("constructive", "bounded_pass")]
    unchecked = {**ok, "data": {**ok["data"], "oracle_cross_check": {"n_words": 0}}}
    assert A._classify(unchecked, "reg", None, [ok], ir_named=True) == []
    assert A._classify({**ok, "trust": "bounded_pass"}, "reg", None, [ok], ir_named=True,
                       ir_human_approved=True) == []
