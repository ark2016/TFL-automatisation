"""Lean 4 formalization step of the CFL pipeline (docs/VERDICT_POLICY.md R-Lean).

Mirrors ``agent_system``'s wiring (``graph.formalize_node`` + the gate in
``assemble_result_node``) for the ``cfl`` / ``non_cfl`` directions:

* ``lean_formalize_node`` -- statement from ``cfl_system.lib.lean_ir`` (never
  from the LLM), the retry loop, the non-``proved`` outcomes;
* ``apply_lean_gate`` / ``assemble_result_node`` -- ``proved`` => ``verified``
  0.98 (and a verdict flip when it proves the other direction), every other
  status changes nothing;
* the opt-in switches (``formalize=`` / ``--formalize`` / ``TFL_FORMALIZATION``);
* the ``cfl_lean_formalizer.md`` prompt: its few-shot proof body is the one
  compiled in ``TflLean/Examples/AnBnCnNotCF.lean``.

Everything except the two tests marked ``docker`` runs without Docker:
``check_lean_file`` is replaced by a scripted fake and the agent by a scripted
runner, so no API call is possible (root ``conftest.py`` blocks the client
anyway). The existing Markdown ``formalize_node`` is not touched here.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

import cfl_system.orchestrator as orch
from agent_system.lib.lean_ir import LeanStatement
from agent_system.lib.type_check import (
    check_lean_file,
    compose_lean_file,
    is_docker_available,
)
from cfl_system import config as cfl_config
from cfl_system.lib.lean_ir import render_statement
from cfl_system.orchestrator import (
    MockRunner,
    apply_lean_gate,
    assemble_result_node,
    lean_formalize_node,
    run_pipeline,
)

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
EXAMPLES = ROOT / "examples"
MOCK_DIR = EXAMPLES / "mock"
PROMPT = ROOT / "prompts" / "cfl_lean_formalizer.md"
TFL_LEAN = REPO / "agent_system" / "docker" / "tfl_lean" / "TflLean"

ANBNCN_IR = json.loads((EXAMPLES / "eval" / "cfl-01.json").read_text(encoding="utf-8"))

# A set_builder spec and a spec-less IR: shapes of the LL pipeline's inputs
# (ll_system/examples/format1_*.json, format3_*.json) that have no CFL Lean
# statement.
LL_SET_BUILDER_IR = {
    "task_type": "ll_check_language",
    "source_text": "L = {a^n b^n} u {a^n c^n}. Is this language LL(k)?",
    "language_spec": {
        "kind": "set_builder",
        "alphabet": ["a", "b", "c"],
        "variables": [{"name": "n", "domain": {"type": "nat"}}],
        "template": ["a^n", "X^n"],
    },
    "question": "is_ll",
}
LL_GRAMMAR_ONLY_IR = {
    "task_type": "ll_check_grammar",
    "source_text": "S -> aAb | bBa; A -> aA | eps. Is this grammar LL(1)?",
    "grammar": {"nonterminals": ["S", "A"], "terminals": ["a", "b"], "start": "S",
                "rules": [{"lhs": "S", "rhs": ["a", "A", "b"]}, {"lhs": "A", "rhs": []}]},
    "question": "is_ll_k",
    "k": 1,
}


# ---------------------------------------------------------------------------
# Scripted runner / fake type checker
# ---------------------------------------------------------------------------

BODY_OK = "intro h\n  exact h"


def _body(text: str = BODY_OK) -> dict:
    return {"agent": "lean_formalizer", "proof_body": text, "lemmas_used": [], "notes": ""}


class ScriptedRunner:
    """MockRunner stand-in: returns the scripted outputs one by one (the
    last one repeats) for ``lean_formalizer`` and ``None`` for every other
    agent; records every call."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls: list[tuple[str, dict | None]] = []

    def run_agent(self, agent_name, input_data=None):
        self.calls.append((agent_name, copy.deepcopy(input_data)))
        if agent_name != "lean_formalizer":
            return None
        out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        return copy.deepcopy(out)

    @property
    def lean_calls(self) -> list[dict]:
        return [inp for name, inp in self.calls if name == "lean_formalizer"]


class FakeChecker:
    """Replacement for ``check_lean_file``: returns the scripted results one
    by one (the last one repeats), records (text, theorem_name, timeout)."""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[dict] = []

    def __call__(self, text, timeout=300, theorem_name=None):
        self.calls.append({"text": text, "theorem_name": theorem_name, "timeout": timeout})
        res = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return {"errors": [], "warnings": [], "axioms": [], "elapsed": 1.5, **copy.deepcopy(res)}


PROVED = {"status": "proved", "axioms": ["propext", "Classical.choice", "Quot.sound"]}
ERR1 = {"status": "error", "errors": [{"severity": "error", "data": "unknown identifier 'foo'"}]}
ERR2 = {"status": "error", "errors": [{"severity": "error", "data": "linarith failed"}]}


def _state(ir=ANBNCN_IR, runner=None, verdict="non_cfl", formalize=True, **extra) -> dict:
    state = {
        "ir": ir,
        "mock_runner": runner,
        "agent_runner": None,
        "verbose": False,
        "formalize": formalize,
        "reasoning_output": {
            "action": "done",
            "verdict": verdict,
            "confidence": 0.9,
            "primary_evidence": "pumping_cfl",
            "primary_justification": "pump a^p b^p c^p",
            "summary": "not context-free",
        },
        "agent_results": {"pumping_cfl": {"evidence": {"word_chosen": "a^p b^p c^p"}}},
        "errors": [],
    }
    state.update(extra)
    return state


@pytest.fixture
def checker(monkeypatch):
    def install(results):
        fake = FakeChecker(results)
        monkeypatch.setattr(orch, "check_lean_file", fake)
        return fake
    return install


# ---------------------------------------------------------------------------
# Config / opt-in switches
# ---------------------------------------------------------------------------

def test_config_constants():
    assert isinstance(cfl_config.MAX_FORMALIZE_ITERATIONS, int)
    assert cfl_config.MAX_FORMALIZE_ITERATIONS >= 1
    assert cfl_config.LEAN_TIMEOUT > 0


@pytest.mark.parametrize("value, expected", [
    ("1", True), ("true", True), ("YES", True), (" on ", True),
    ("", False), ("0", False), ("no", False), ("off", False), ("2", False),
])
def test_formalization_enabled_default_reads_env(monkeypatch, value, expected):
    monkeypatch.setenv("TFL_FORMALIZATION", value)
    assert cfl_config.formalization_enabled_default() is expected


def test_formalization_enabled_default_unset(monkeypatch):
    monkeypatch.delenv("TFL_FORMALIZATION", raising=False)
    assert cfl_config.formalization_enabled_default() is False


def test_lean_formalizer_registered_in_every_agent_table():
    for table in (cfl_config.PROMPT_FILES, cfl_config.MODELS, cfl_config.EFFORT, cfl_config.TEMPERATURES):
        assert "lean_formalizer" in table
    # the Markdown-proof agent keeps its own, separate slot
    assert cfl_config.PROMPT_FILES["formalizer"] == "cfl_formalizer.md"
    assert cfl_config.PROMPT_FILES["lean_formalizer"] == "cfl_lean_formalizer.md"


def test_disabled_by_default_is_a_noop(monkeypatch, checker):
    fake = checker([PROVED])
    monkeypatch.setattr(orch, "FORMALIZATION_ENABLED", False)
    runner = ScriptedRunner([_body()])
    assert lean_formalize_node(_state(runner=runner, formalize=None)) == {}
    assert lean_formalize_node(_state(runner=runner, formalize=False)) == {}
    assert runner.calls == [] and fake.calls == []


def test_module_flag_enables_and_per_call_override_wins(monkeypatch, checker):
    checker([PROVED])
    monkeypatch.setattr(orch, "FORMALIZATION_ENABLED", True)
    out = lean_formalize_node(_state(runner=ScriptedRunner([_body()]), formalize=None))
    assert out["formalization"]["status"] == "proved"
    # formalize=False beats the module default
    assert lean_formalize_node(_state(runner=ScriptedRunner([_body()]), formalize=False)) == {}


def test_no_verdict_or_no_runner_is_skipped(checker):
    fake = checker([PROVED])
    runner = ScriptedRunner([_body()])
    out = lean_formalize_node(_state(runner=runner, verdict=None))
    assert out["formalization"]["status"] == "skipped"
    assert out["formalization"]["direction"] is None
    out = lean_formalize_node(_state(runner=runner, verdict="inconclusive"))
    assert out["formalization"]["status"] == "skipped"
    out = lean_formalize_node(_state(runner=None))
    assert out["formalization"]["status"] == "skipped"
    assert out["formalization"]["direction"] == "non_cfl"
    assert runner.calls == [] and fake.calls == []


# ---------------------------------------------------------------------------
# not_formalizable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ir", [LL_SET_BUILDER_IR, LL_GRAMMAR_ONLY_IR], ids=["set_builder", "no_language_spec"])
@pytest.mark.parametrize("verdict", ["cfl", "non_cfl"])
def test_ll_like_ir_is_not_formalizable(checker, ir, verdict):
    fake = checker([PROVED])
    runner = ScriptedRunner([_body()])
    out = lean_formalize_node(_state(ir=ir, runner=runner, verdict=verdict))
    f = out["formalization"]
    assert f["status"] == "not_formalizable"
    assert f["direction"] == verdict
    assert f["reason"]
    # no statement => no agent call, no Lean check
    assert runner.calls == [] and fake.calls == []


# ---------------------------------------------------------------------------
# The retry loop
# ---------------------------------------------------------------------------

def test_proved_first_attempt(checker):
    fake = checker([PROVED])
    runner = ScriptedRunner([_body("intro h\n  exact h")])
    f = lean_formalize_node(_state(runner=runner))["formalization"]
    assert f["status"] == "proved"
    assert f["direction"] == "non_cfl"
    assert f["proof_body"] == "intro h\n  exact h"
    assert f["axioms"] == PROVED["axioms"]
    assert f["errors"] == []
    assert [a["status"] for a in f["attempts"]] == ["proved"]
    assert len(runner.lean_calls) == 1 and len(fake.calls) == 1
    assert f["lean_code"] == fake.calls[0]["text"]
    assert fake.calls[0]["theorem_name"] == "tfl_main"
    assert fake.calls[0]["timeout"] == cfl_config.LEAN_TIMEOUT


def test_agent_input_carries_statement_plan_and_lemmas(checker):
    checker([PROVED])
    runner = ScriptedRunner([_body()])
    lean_formalize_node(_state(runner=runner))
    inp = runner.lean_calls[0]
    expected = render_statement(ANBNCN_IR, "non_cfl")
    assert inp["statement"] == {
        "imports": expected.imports,
        "alphabet_decl": expected.alphabet_decl,
        "language_decl": expected.language_decl,
        "theorem_decl": "theorem tfl_main : ¬ L.IsContextFree",
        "name": "tfl_main",
    }
    assert inp["plan"]["verdict"] == "non_cfl"
    assert inp["plan"]["method"] == "pumping_cfl"
    assert inp["plan"]["specialist_evidence"] == {"word_chosen": "a^p b^p c^p"}
    assert "Language.IsContextFree.pumping" in inp["available_lemmas"]
    assert "errors" not in inp and "previous_proof_body" not in inp


def test_error_then_proved_feeds_errors_back_with_same_statement(checker):
    fake = checker([ERR1, PROVED])
    runner = ScriptedRunner([_body("exact foo"), _body("exact bar")])
    f = lean_formalize_node(_state(runner=runner))["formalization"]
    assert f["status"] == "proved"
    assert f["proof_body"] == "exact bar"
    assert [a["status"] for a in f["attempts"]] == ["error", "proved"]
    first, second = runner.lean_calls
    assert "errors" not in first
    assert second["errors"] == ERR1["errors"]
    assert second["previous_proof_body"] == "exact foo"
    assert second["statement"] == first["statement"]          # never re-rendered / changed
    assert len(fake.calls) == 2
    assert f["errors"] == []


def test_all_attempts_fail_stops_at_max_iterations(checker):
    fake = checker([ERR1, ERR2])
    runner = ScriptedRunner([_body("a"), _body("b"), _body("c")])
    f = lean_formalize_node(_state(runner=runner))["formalization"]
    n = cfl_config.MAX_FORMALIZE_ITERATIONS
    assert f["status"] == "error"
    assert len(runner.lean_calls) == n == len(fake.calls) == len(f["attempts"])
    assert f["errors"] == ERR2["errors"]          # the last attempt's errors
    assert f["proof_body"] == "c"


def test_iteration_cap_is_read_from_config(monkeypatch, checker):
    monkeypatch.setattr(orch, "MAX_FORMALIZE_ITERATIONS", 1)
    checker([ERR1])
    runner = ScriptedRunner([_body()])
    f = lean_formalize_node(_state(runner=runner))["formalization"]
    assert f["status"] == "error" and len(runner.lean_calls) == 1


@pytest.mark.parametrize("terminal", ["has_sorry", "timeout", "unavailable"])
def test_terminal_statuses_are_not_retried(checker, terminal):
    fake = checker([{"status": terminal}])
    runner = ScriptedRunner([_body()])
    f = lean_formalize_node(_state(runner=runner))["formalization"]
    assert f["status"] == terminal
    assert len(runner.lean_calls) == 1 and len(fake.calls) == 1


def test_agent_error_is_recorded_and_ends_the_loop(checker):
    fake = checker([PROVED])
    runner = ScriptedRunner([
        {"agent": "lean_formalizer", "status": "agent_error", "errors": ["API error: overloaded"]},
    ])
    out = lean_formalize_node(_state(runner=runner))
    f = out["formalization"]
    assert f["status"] == "error"
    assert out["errors"] == ["lean_formalizer: API error: overloaded"]
    assert len(runner.lean_calls) == 1 and fake.calls == []


def test_missing_proof_body_is_asked_again(checker):
    fake = checker([PROVED])
    runner = ScriptedRunner([{"agent": "lean_formalizer", "notes": "forgot the body"}, _body()])
    f = lean_formalize_node(_state(runner=runner))["formalization"]
    assert f["status"] == "proved"
    assert [a["status"] for a in f["attempts"]] == ["no_proof_body", "proved"]
    assert len(fake.calls) == 1


def test_no_output_at_all_ends_with_error(checker):
    fake = checker([PROVED])
    runner = ScriptedRunner([None])
    f = lean_formalize_node(_state(runner=runner))["formalization"]
    assert f["status"] == "error"
    assert len(runner.lean_calls) == 1 and fake.calls == []


def test_evidence_wrapped_output_is_accepted(checker):
    checker([PROVED])
    runner = ScriptedRunner([{"status": "success", "evidence": {"proof_body": "trivial"}}])
    f = lean_formalize_node(_state(runner=runner))["formalization"]
    assert f["status"] == "proved" and f["proof_body"] == "trivial"


def test_statement_is_generated_by_code_never_taken_from_the_agent(checker):
    fake = checker([PROVED])
    bad = _body("intro h\n  exact h")
    bad.update({
        "statement": {"theorem_decl": "theorem tfl_main : True", "language_decl": "def L := 0"},
        "theorem_decl": "theorem tfl_main : True",
    })
    f = lean_formalize_node(_state(runner=ScriptedRunner([bad])))["formalization"]
    text = fake.calls[0]["text"]
    stmt = render_statement(ANBNCN_IR, "non_cfl")
    assert stmt.theorem_decl in text and stmt.language_decl in text
    assert "theorem tfl_main : True" not in text
    assert f["statement"]["theorem_decl"] == stmt.theorem_decl


def test_cfl_direction_renders_the_positive_statement(checker):
    fake = checker([PROVED])
    f = lean_formalize_node(_state(runner=ScriptedRunner([_body()]), verdict="cfl"))["formalization"]
    assert f["direction"] == "cfl"
    assert f["statement"]["theorem_decl"] == "theorem tfl_main : L.IsContextFree"
    assert "theorem tfl_main : L.IsContextFree := by" in fake.calls[0]["text"]


def test_harness_rejects_escape_hatches_before_docker():
    """The real compose_lean_file/check_lean_file pair -- not the fake --
    turns a `sorry`-free but forbidden proof body into `error` without ever
    invoking Docker (scan_proof_body); a plain `sorry` is has_sorry on the
    real compiler (Docker test below)."""
    stmt = render_statement(ANBNCN_IR, "non_cfl")
    text = compose_lean_file(stmt, "native_decide")
    res = check_lean_file(text, theorem_name="tfl_main")
    assert res["status"] == "error"
    assert "rejected" in res["errors"][0]["data"]


def test_live_runner_without_docker_never_calls_the_agent(monkeypatch):
    monkeypatch.setattr(orch, "is_docker_available", lambda: False)
    live = ScriptedRunner([_body()])
    state = _state(runner=None, agent_runner=live)
    f = lean_formalize_node(state)["formalization"]
    assert f["status"] == "unavailable"
    assert f["direction"] == "non_cfl"
    assert live.calls == []


def test_mock_runner_without_docker_reports_unavailable_from_the_check(monkeypatch):
    """Free mock calls: the check itself reports `unavailable`."""
    import agent_system.lib.type_check as tc
    monkeypatch.setattr(tc, "is_docker_available", lambda: False)
    runner = ScriptedRunner([_body()])
    f = lean_formalize_node(_state(runner=runner))["formalization"]
    assert f["status"] == "unavailable"
    assert len(runner.lean_calls) == 1


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def _gate(**over) -> dict:
    gate = {
        "basis": [{"agent": "pumping_cfl", "trust": "well_formed"}],
        "basis_trust": "well_formed",
        "contradiction": False,
        "downgrades": [],
        "confidence_cap": 0.55,
        "proof_verified": False,
    }
    gate.update(over)
    return gate


def _proved(direction: str) -> dict:
    return {"status": "proved", "direction": direction, "proof_body": "x",
            "statement": {"theorem_decl": "theorem tfl_main : ..."}}


def test_gate_proved_same_direction_is_verified_098():
    gate = _gate()
    before = copy.deepcopy(gate)
    out = apply_lean_gate(_proved("non_cfl"), "non_cfl", 0.55, gate)
    assert out["proved"] is True and out["flipped"] is False
    assert out["verdict"] == "non_cfl"
    assert out["confidence"] == 0.98
    g = out["verdict_gate"]
    assert g["basis_trust"] == "verified" and g["proof_verified"] is True
    assert g["confidence_cap"] == 0.98 and g["contradiction"] is False
    assert {"agent": "lean_formalizer", "trust": "verified", "basis": "lean_proof"} in g["basis"]
    assert {"agent": "pumping_cfl", "trust": "well_formed"} in g["basis"]
    assert g["downgrades"] == []
    assert gate == before                       # pure: input untouched


def test_gate_proved_is_not_bounded_by_low_reasoning_confidence():
    out = apply_lean_gate(_proved("cfl"), "cfl", 0.10, _gate())
    assert out["confidence"] == 0.98


@pytest.mark.parametrize("standing", ["cfl", None])
def test_gate_proved_opposite_direction_flips_the_verdict(standing):
    out = apply_lean_gate(_proved("non_cfl"), standing, 0.80, _gate(contradiction=True, basis_trust="not_verified"))
    assert out["proved"] is True and out["flipped"] is True
    assert out["verdict"] == "non_cfl" and out["confidence"] == 0.98
    g = out["verdict_gate"]
    assert g["contradiction"] is False and g["proof_verified"] is True
    assert len(g["downgrades"]) == 1
    assert "non_cfl" in g["downgrades"][0] and "R-Lean" in g["downgrades"][0]
    assert g["lean_proof"] == {"direction": "non_cfl", "flipped": True}


@pytest.mark.parametrize("status", [
    "has_sorry", "error", "timeout", "unavailable", "not_formalizable", "skipped",
])
def test_gate_other_statuses_change_nothing(status):
    gate = _gate()
    f = {"status": status, "direction": "cfl"}
    out = apply_lean_gate(f, "non_cfl", 0.55, gate)
    assert out == {"verdict": "non_cfl", "confidence": 0.55, "verdict_gate": gate,
                   "proved": False, "flipped": False}


@pytest.mark.parametrize("f", [None, {}, "proved", {"status": "proved"},
                               {"status": "proved", "direction": "regular"}])
def test_gate_ignores_missing_or_foreign_formalization(f):
    out = apply_lean_gate(f, "cfl", 0.6, _gate())
    assert out["proved"] is False and out["verdict"] == "cfl" and out["confidence"] == 0.6


def _assemble_state(verdict="non_cfl", formalization=None, gate=None) -> dict:
    return {
        "ir": ANBNCN_IR,
        "reasoning_output": {
            "action": "done", "verdict": verdict, "confidence": 0.55,
            "summary": "reasoning summary", "primary_evidence": "pumping_cfl",
        },
        "agent_results": {"pumping_cfl": {"status": "success", "verdict": "non_cfl",
                                          "evidence": {"word_chosen": "a^p b^p c^p"}}},
        "verdict_gate": gate if gate is not None else _gate(),
        "trust": {"pumping_cfl": "well_formed"},
        "errors": [],
        "retry_round": 0,
        "inversions_done": 0,
        "formalization": formalization,
    }


def test_assemble_without_formalization_is_unchanged():
    res = assemble_result_node(_assemble_state())["result"]
    assert res["verdict"] == "non_cfl" and res["confidence"] == 0.55
    assert res["proof_verified"] is False
    assert res["formalization"] is None
    assert res["verdict_gate"]["basis_trust"] == "well_formed"


def test_assemble_proved_same_direction_lifts_to_verified():
    res = assemble_result_node(_assemble_state(formalization=_proved("non_cfl")))["result"]
    assert res["verdict"] == "non_cfl" and res["confidence"] == 0.98
    assert res["proof_verified"] is True
    assert res["verdict_gate"]["basis_trust"] == "verified"
    assert res["formalization"]["status"] == "proved"
    # the specialists' proof was for the same verdict: still the proof of record
    assert res["proof"]["source"] == "pumping_cfl"


def test_assemble_proved_opposite_direction_flips_verdict_and_replaces_proof():
    res = assemble_result_node(_assemble_state(verdict="cfl", formalization=_proved("non_cfl")))["result"]
    assert res["verdict"] == "non_cfl" and res["confidence"] == 0.98
    assert res["proof_verified"] is True
    assert res["verdict_gate"]["downgrades"], "the flip must be logged (R4)"
    assert res["proof"]["source"] == "lean_formalizer"
    assert res["proof"]["evidence"]["proof_body"] == "x"


@pytest.mark.parametrize("status", ["has_sorry", "error", "timeout", "unavailable",
                                    "not_formalizable", "skipped"])
def test_assemble_non_proved_status_never_changes_verdict_or_confidence(status):
    f = {"status": status, "direction": "non_cfl", "attempts": []}
    res = assemble_result_node(_assemble_state(formalization=f))["result"]
    assert res["verdict"] == "non_cfl" and res["confidence"] == 0.55
    assert res["proof_verified"] is False
    assert res["verdict_gate"]["basis_trust"] == "well_formed"
    assert res["formalization"]["status"] == status     # still reported


# ---------------------------------------------------------------------------
# Whole pipeline (mock agents, fake type checker)
# ---------------------------------------------------------------------------

def _run(formalize=None, **kw):
    return run_pipeline(ANBNCN_IR, mock_runner=MockRunner(str(MOCK_DIR), "task_anbncn"),
                        formalize=formalize, **kw)


def test_pipeline_without_the_flag_has_no_formalization(monkeypatch, checker):
    fake = checker([PROVED])
    monkeypatch.setattr(orch, "FORMALIZATION_ENABLED", False)
    res = _run()
    assert res["verdict"] == "non_cfl" and res["confidence"] == 0.55
    assert res["formalization"] is None
    assert fake.calls == []


def test_pipeline_formalize_proved_gives_098(checker):
    fake = checker([PROVED])
    res = _run(formalize=True)
    assert res["verdict"] == "non_cfl" and res["confidence"] == 0.98
    assert res["proof_verified"] is True
    assert res["verdict_gate"]["basis_trust"] == "verified"
    assert res["verdict_gate"]["lean_proof"] == {"direction": "non_cfl", "flipped": False}
    assert res["formalization"]["status"] == "proved"
    assert res["formalization"]["direction"] == "non_cfl"
    assert len(fake.calls) == 1
    assert "theorem tfl_main : ¬ L.IsContextFree := by" in fake.calls[0]["text"]


def test_pipeline_formalize_env_default(monkeypatch, checker):
    checker([PROVED])
    monkeypatch.setattr(orch, "FORMALIZATION_ENABLED", True)
    assert _run()["confidence"] == 0.98
    assert _run(formalize=False)["confidence"] == 0.55


def test_pipeline_formalize_has_sorry_changes_nothing(checker):
    checker([{"status": "has_sorry"}])
    res = _run(formalize=True)
    assert res["verdict"] == "non_cfl" and res["confidence"] == 0.55
    assert res["proof_verified"] is False
    assert res["formalization"]["status"] == "has_sorry"


def test_pipeline_markdown_formalizer_is_independent(checker):
    """The Markdown `formalize_node` (agent "formalizer") still runs on its
    own, before and separately from the Lean agent (`lean_formalizer`)."""
    checker([PROVED])
    mock = MockRunner(str(MOCK_DIR), "task_anbncn")
    seen: list[str] = []
    orig = mock.run_agent

    def spy(name, data=None):
        seen.append(name)
        return orig(name, data)

    mock.run_agent = spy
    res = run_pipeline(ANBNCN_IR, mock_runner=mock, formalize=True)
    assert "formalizer" in seen and "lean_formalizer" in seen
    assert seen.index("formalizer") < seen.index("lean_formalizer")
    assert res["formalization"]["status"] == "proved"


def test_cli_formalize_flag_is_passed_through(monkeypatch, tmp_path):
    captured = {}

    def fake_run_pipeline(ir, **kw):
        captured.update(kw)
        return {"verdict": "non_cfl", "confidence": 0.5}

    monkeypatch.setattr(orch, "run_pipeline", fake_run_pipeline)
    task = tmp_path / "t.json"
    task.write_text(json.dumps(ANBNCN_IR), encoding="utf-8")

    for argv, expected in ((["--formalize"], True), ([], None)):
        monkeypatch.setattr(sys, "argv", ["cfl", str(task), "--mock", str(MOCK_DIR), *argv])
        with pytest.raises(SystemExit):
            orch.main()
        assert captured["formalize"] is expected


# ---------------------------------------------------------------------------
# Prompt / examples / mocks
# ---------------------------------------------------------------------------

_PROBE = "___TFL_EXAMPLE_PROBE_BODY___"


def _example_body() -> str:
    """The proof body of TflLean/Examples/AnBnCnNotCF.lean exactly as the
    agent would return it (see agent_system/tests/test_lean_examples.py)."""
    stmt = render_statement(ANBNCN_IR, "non_cfl")
    scaffold = compose_lean_file(stmt, _PROBE)
    at = scaffold.index(_PROBE)
    prefix, suffix = scaffold[:at], scaffold[at + len(_PROBE):]
    text = (TFL_LEAN / "Examples" / "AnBnCnNotCF.lean").read_text(encoding="utf-8")
    assert text.startswith(prefix), "example statement differs from cfl lean_ir's rendering"
    assert text.endswith(suffix)
    return text[len(prefix):len(text) - len(suffix)]


def test_example_file_is_the_harness_composition():
    body = _example_body()
    stmt = render_statement(ANBNCN_IR, "non_cfl")
    assert compose_lean_file(stmt, body) == (TFL_LEAN / "Examples" / "AnBnCnNotCF.lean").read_text(encoding="utf-8")
    for bad in ("sorry", "admit", "native_decide", "ofReduceBool", "axiom "):
        assert bad not in body


def test_prompt_few_shot_body_is_the_compiled_example():
    prompt = PROMPT.read_text(encoding="utf-8")
    assert json.dumps(_example_body(), ensure_ascii=False) in prompt


def test_prompt_few_shot_statement_is_the_renderers():
    prompt = PROMPT.read_text(encoding="utf-8")
    stmt = render_statement(ANBNCN_IR, "non_cfl")
    for field in ("imports", "alphabet_decl", "language_decl", "theorem_decl"):
        value = getattr(stmt, field)
        assert f'"{field}": {json.dumps(value, ensure_ascii=False)}' in prompt, field


def test_prompt_documents_both_directions_and_the_hard_rule():
    prompt = PROMPT.read_text(encoding="utf-8")
    assert "theorem tfl_main : ¬ L.IsContextFree" in prompt
    assert "theorem tfl_main : L.IsContextFree" in prompt
    assert "never write the formulation" in prompt
    assert '"agent": "lean_formalizer"' in prompt


def test_prompt_and_hint_list_name_only_existing_tfllean_lemmas():
    import re
    lemmas = (TFL_LEAN / "Lemmas.lean").read_text(encoding="utf-8")
    prompt = PROMPT.read_text(encoding="utf-8")
    named = set(re.findall(r"TflLean\.([A-Za-z_][A-Za-z0-9_.]*)", prompt))
    named |= {n.split(".", 1)[1] for n in orch._LEAN_AVAILABLE_LEMMAS if n.startswith("TflLean.")}
    assert "not_isContextFree_of_not_cfPumping" in named
    for name in named:
        bare = name.rstrip(".")
        last = bare.split(".")[-1]
        if bare in ("Lemmas", "Examples") or bare.startswith(("Lemmas", "Examples")):
            continue
        assert re.search(rf"\b(theorem|def|lemma)\s+(\S*\.)?{re.escape(last)}\b", lemmas), name


def test_hint_lemmas_appear_in_the_prompt():
    prompt = PROMPT.read_text(encoding="utf-8")
    for name in orch._LEAN_AVAILABLE_LEMMAS:
        assert name in prompt, name


def test_mock_lean_formalizer_output_matches_the_contract():
    data = json.loads((MOCK_DIR / "task_anbncn_lean_formalizer.json").read_text(encoding="utf-8"))
    from cfl_system.lib.agent_output_schema import REQUIRED_KEYS
    assert set(data) >= REQUIRED_KEYS["lean_formalizer"]
    assert data["proof_body"] == _example_body()


# ---------------------------------------------------------------------------
# Docker: the real compiler
# ---------------------------------------------------------------------------

docker = pytest.mark.skipif(
    not is_docker_available(), reason="Docker with the tfl-lean4 image not available",
)


@docker
def test_anbncn_statement_with_sorry_is_has_sorry():
    """Statement for {a^n b^n c^n} (non_cfl) from the IR + a `sorry` body:
    it compiles, and the harness reports has_sorry (never proved)."""
    stmt = render_statement(ANBNCN_IR, "non_cfl")
    assert isinstance(stmt, LeanStatement)
    assert stmt.theorem_decl == "theorem tfl_main : ¬ L.IsContextFree"
    result = check_lean_file(compose_lean_file(stmt, "sorry"), timeout=cfl_config.LEAN_TIMEOUT,
                             theorem_name=stmt.name)
    assert result["status"] == "has_sorry", result
    assert result["errors"] == []


@docker
def test_pipeline_end_to_end_proved_on_the_real_compiler():
    """task_anbncn mocks + the compiled example body: the whole chain
    (IR -> statement -> agent -> compose -> Docker -> gate) ends verified 0.98."""
    res = _run(formalize=True)
    f = res["formalization"]
    assert f["status"] == "proved", f
    assert set(f["axioms"]) <= {"propext", "Classical.choice", "Quot.sound"}
    assert res["verdict"] == "non_cfl" and res["confidence"] == 0.98
    assert res["proof_verified"] is True
