"""R-Lean for dcfl_system (docs/VERDICT_POLICY.md R-Lean): ``lean_formalize_node``
and the ``_apply_lean_gate`` verdict gate, mirroring agent_system's
``formalize_node`` / ``assemble_result_node`` tests.

Everything but the last class runs without Docker and without any API call:
the ``lean_formalizer`` agent is a scripted fake and ``check_lean_file`` is
patched on ``dcfl_system.orchestrator``. The last class needs Docker + the
``tfl-lean4`` image (skipped otherwise): a ``sorry`` body against the real
``task_anbncm`` statement is ``has_sorry``, the prompt's worked example and the
mock proof for ``dcfl_anbncm`` compile to ``proved``, and the whole mock
pipeline with ``formalize=True`` ends in ``dcfl`` at 0.98.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agent_system.lib.type_check import (
    check_lean_file,
    compose_lean_file,
    is_docker_available,
    scan_proof_body,
)
from dcfl_system import config
from dcfl_system.lib.lean_ir import render_statement, render_statement_verbose
from dcfl_system.orchestrator import (
    DCFL_SPECIALIST_NAMES,
    LiveRunner,
    MockRunner,
    _apply_lean_gate,
    build_dcfl_pipeline_graph,
    lean_formalize_node,
    renderer_node,
    run_pipeline,
)

DCFL_DIR = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = DCFL_DIR / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"
PROMPT_PATH = DCFL_DIR / "prompts" / "dcfl_lean_formalizer.md"

# {a^n b^n | n >= 1} in the letter-domain set_builder shape (the prompt's worked example).
IR_ANBN = {
    "task_id": "dcfl_anbn_lean",
    "language_spec": {
        "kind": "set_builder",
        "word_pattern": "uv",
        "variables": [
            {"name": "u", "domain": "a+", "quantifier": "forall"},
            {"name": "v", "domain": "b+", "quantifier": "forall"},
        ],
        "constraints": [{"kind": "length_cmp", "args": {"left": "u", "op": "==", "right": "v"}}],
    },
    "alphabet": ["a", "b"],
}


def _anbncm_ir() -> dict:
    return json.loads((EXAMPLES_DIR / "task_anbncm.json").read_text(encoding="utf-8"))


class FakeRunner:
    """Scripted ``lean_formalizer``: ``outputs`` is consumed one per call
    (the last one repeats); every input is recorded."""

    def __init__(self, outputs: list[Any]):
        self.outputs = list(outputs)
        self.inputs: list[dict] = []

    def run_agent(self, agent_name: str, input_data: dict | None = None):
        assert agent_name == "lean_formalizer"
        self.inputs.append(copy.deepcopy(input_data))
        out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        return out


def _body(text: str = "trivial") -> dict:
    return {"status": "success", "evidence": {"proof_body": text, "lemmas_used": [], "notes": ""}}


def _tc(status: str, errors: list | None = None, axioms: list | None = None) -> dict:
    return {
        "status": status,
        "errors": errors or [],
        "warnings": [],
        "axioms": axioms or [],
        "elapsed": 1.5,
        "message": "",
    }


def _state(runner: Any = None, *, verdict: str | None = "dcfl", formalize: bool | None = True,
           ir: dict | None = None, hypothesis: dict | None = None, **extra) -> dict:
    return {
        "task_ir": ir if ir is not None else IR_ANBN,
        "hypothesis": hypothesis if hypothesis is not None else {},
        "reasoning": {"verdict": verdict, "summary": "push/pop", "primary_evidence": "stack_strategy"},
        "agent_results": {
            "stack_strategy": {
                "status": "success", "verdict": "dcfl", "proof_sketch": {"kind": "stack_strategy", "phases": []},
            },
        },
        "mock_runner": runner,
        "agent_runner": None,
        "formalize": formalize,
        "verbose": False,
        **extra,
    }


# ---------------------------------------------------------------------------
# _apply_lean_gate (R-Lean priority over R1/R2/R3/R4')
# ---------------------------------------------------------------------------

def _reasoning(verdict, confidence, *, contradiction=False, primary="stack_strategy", cap=0.85):
    return {
        "action": "done", "decision": "done", "verdict": verdict, "confidence": confidence,
        "primary_evidence": primary, "summary": "specialists' summary",
        "verdict_gate": {
            "basis": [{"agent": "stack_strategy", "trust": "bounded_pass"}],
            "contradiction": contradiction, "downgrades": ["earlier note"], "confidence_cap": cap,
        },
    }


def _proved(direction):
    return {"status": "proved", "direction": direction, "axioms": ["propext"], "attempts": [], "errors": []}


class TestApplyLeanGate:
    def test_proved_confirming_direction_is_verified_098(self):
        out = _apply_lean_gate(_reasoning("dcfl", 0.6), _proved("dcfl"))
        assert out["verdict"] == "dcfl"
        assert out["confidence"] == 0.98
        assert out["verdict_gate"]["confidence_cap"] == 0.98
        assert out["verdict_gate"]["contradiction"] is False
        assert {"agent": "lean_formalizer", "trust": "verified", "basis": "lean_proof"} in out["verdict_gate"]["basis"]
        # the specialists' own basis and notes are kept, not erased
        assert {"agent": "stack_strategy", "trust": "bounded_pass"} in out["verdict_gate"]["basis"]
        assert "earlier note" in out["verdict_gate"]["downgrades"]
        assert out["primary_evidence"] == "stack_strategy"

    def test_proved_opposite_direction_flips_the_verdict(self):
        out = _apply_lean_gate(_reasoning("non_dcfl", 0.55, primary="dcfl_pumping"), _proved("dcfl"))
        assert out["verdict"] == "dcfl"
        assert out["confidence"] == 0.98
        gate = out["verdict_gate"]
        assert gate["contradiction"] is True
        assert any("opposite" in d and "dcfl" in d for d in gate["downgrades"])
        assert out["primary_evidence"] == "lean_formalizer"
        assert "overruled" in out["summary"] and "specialists' summary" in out["summary"]

    def test_proved_non_dcfl_overrules_dcfl(self):
        out = _apply_lean_gate(_reasoning("dcfl", 0.85), _proved("non_dcfl"))
        assert (out["verdict"], out["confidence"]) == ("non_dcfl", 0.98)
        assert out["verdict_gate"]["contradiction"] is True

    def test_proved_beats_an_inconclusive_verdict(self):
        reasoning = _reasoning(None, 0.4, primary=None, cap=0.4)
        out = _apply_lean_gate(reasoning, _proved("dcfl"))
        assert (out["verdict"], out["confidence"]) == ("dcfl", 0.98)
        assert out["action"] == "done" and out["decision"] == "done"
        assert out["verdict_gate"]["contradiction"] is False

    def test_proved_resolves_an_earlier_r3_contradiction(self):
        reasoning = _reasoning(None, 0.5, contradiction=True, cap=0.5)
        out = _apply_lean_gate(reasoning, _proved("non_dcfl"))
        assert out["verdict"] == "non_dcfl" and out["confidence"] == 0.98
        assert out["verdict_gate"]["contradiction"] is False

        confirming = _reasoning("dcfl", 0.85, contradiction=True, cap=0.85)
        out = _apply_lean_gate(confirming, _proved("dcfl"))
        assert out["verdict"] == "dcfl" and out["verdict_gate"]["contradiction"] is False
        assert any("resolved by a machine-checked proof" in d for d in out["verdict_gate"]["downgrades"])

    @pytest.mark.parametrize("status", [
        "has_sorry", "error", "timeout", "unavailable", "not_formalizable", "", None,
    ])
    def test_other_statuses_are_not_evidence(self, status):
        reasoning = _reasoning("dcfl", 0.85)
        formalization = {"status": status, "direction": "non_dcfl"}
        assert _apply_lean_gate(reasoning, formalization) == reasoning

    @pytest.mark.parametrize("formalization", [None, {}, "proved", {"status": "proved", "direction": "uncertain"},
                                               {"status": "proved"}])
    def test_malformed_or_directionless_input_is_a_noop(self, formalization):
        reasoning = _reasoning("dcfl", 0.85)
        assert _apply_lean_gate(reasoning, formalization) == reasoning

    def test_input_is_never_mutated(self):
        reasoning = _reasoning("non_dcfl", 0.55)
        snapshot = copy.deepcopy(reasoning)
        _apply_lean_gate(reasoning, _proved("dcfl"))
        assert reasoning == snapshot

    def test_direction_aliases_are_normalized(self):
        out = _apply_lean_gate(_reasoning("dcfl", 0.6), _proved("not_dcfl"))
        assert out["verdict"] == "non_dcfl"


class TestRendererAppliesTheLeanGate:
    """`renderer_node` applies R-Lean AFTER its own forced-done re-gate, so a
    proved proof also wins on the terminal (retry-exhausted) path."""

    def _state(self, reasoning: dict, formalization: dict | None) -> dict:
        return {
            "task_ir": IR_ANBN, "hypothesis": {}, "reasoning": reasoning, "agent_results": {},
            "oracle_verification": {}, "errors": [], "retry_count": 0, "formalization": formalization,
        }

    def test_done_path_proved_flips_and_exposes_formalization(self):
        formalization = {**_proved("dcfl"), "statement": {"name": "tfl_main"}, "proof_body": "trivial"}
        res = renderer_node(self._state(_reasoning("non_dcfl", 0.55), formalization))["result"]
        assert (res["verdict"], res["confidence"]) == ("dcfl", 0.98)
        assert res["verdict_gate"]["contradiction"] is True
        assert res["formalization"] == formalization

    def test_terminal_path_retry_action_is_regated_then_lean_wins(self):
        reasoning = {"action": "retry", "decision": "retry", "verdict": None, "confidence": 0.25,
                     "summary": "unsure", "primary_evidence": None}
        res = renderer_node(self._state(reasoning, _proved("dcfl")))["result"]
        assert (res["verdict"], res["confidence"]) == ("dcfl", 0.98)

    def test_non_proved_leaves_the_gate_result_untouched(self):
        reasoning = {"action": "retry", "decision": "retry", "verdict": None, "confidence": 0.25,
                     "summary": "unsure", "primary_evidence": None}
        base = renderer_node(self._state(reasoning, None))["result"]
        for status in ("has_sorry", "error", "timeout", "unavailable", "not_formalizable"):
            f = {"status": status, "direction": "dcfl"}
            res = renderer_node(self._state(reasoning, f))["result"]
            assert (res["verdict"], res["confidence"]) == (base["verdict"], base["confidence"])
            assert res["verdict_gate"] == base["verdict_gate"]
            assert res["formalization"] == f
        assert base["formalization"] is None

    def test_result_renders_to_md_and_html_after_a_flip(self):
        from dcfl_system.renderer import render_html, render_markdown
        res = renderer_node(self._state(_reasoning("non_dcfl", 0.55), _proved("dcfl")))["result"]
        assert "dcfl" in render_markdown(res)
        assert "dcfl" in render_html(res)


# ---------------------------------------------------------------------------
# lean_formalize_node
# ---------------------------------------------------------------------------

def _run_node(state: dict, tc_results: list[dict] | dict | None = None):
    """Run the node with check_lean_file patched; returns (node_output, patch_mock)."""
    if tc_results is None or isinstance(tc_results, dict):
        kw = {"return_value": tc_results or _tc("proved")}      # same answer for every call
    else:
        kw = {"side_effect": tc_results}                         # one answer per call, in order
    with patch("dcfl_system.orchestrator.check_lean_file", **kw) as m:
        return lean_formalize_node(state), m


class TestLeanFormalizeNode:
    def test_disabled_by_default_and_never_calls_the_agent(self):
        runner = FakeRunner([_body()])
        with patch("dcfl_system.orchestrator.FORMALIZATION_ENABLED", False):
            out, check = _run_node(_state(runner, formalize=None))
        assert out == {} and runner.inputs == [] and check.call_count == 0

    def test_module_default_enables_it_and_per_call_false_overrides(self):
        with patch("dcfl_system.orchestrator.FORMALIZATION_ENABLED", True):
            out, _ = _run_node(_state(FakeRunner([_body()]), formalize=None))
            assert out["formalization"]["status"] == "proved"
            runner = FakeRunner([_body()])
            out, _ = _run_node(_state(runner, formalize=False))
            assert out == {} and runner.inputs == []

    def test_no_runner_is_a_noop(self):
        out, check = _run_node(_state(None))
        assert out == {} and check.call_count == 0

    def test_live_run_without_docker_does_not_call_the_agent(self):
        runner = FakeRunner([_body()])
        state = _state(None, agent_runner=runner)
        with patch("dcfl_system.orchestrator.is_docker_available", return_value=False):
            out, check = _run_node(state)
        f = out["formalization"]
        assert f["status"] == "unavailable" and f["direction"] == "dcfl" and f["attempts"] == []
        assert f["statement"]["theorem_decl"] == "theorem tfl_main : is_DCF L"
        assert runner.inputs == [] and check.call_count == 0

    def test_live_run_with_docker_calls_the_agent(self):
        runner = FakeRunner([_body()])
        with patch("dcfl_system.orchestrator.is_docker_available", return_value=True):
            out, _ = _run_node(_state(None, agent_runner=runner))
        assert out["formalization"]["status"] == "proved" and len(runner.inputs) == 1

    def test_mock_run_never_probes_docker(self):
        with patch("dcfl_system.orchestrator.is_docker_available") as probe:
            _run_node(_state(FakeRunner([_body()])))
        assert probe.call_count == 0

    def test_env_flag_parsing(self, monkeypatch):
        for value in ("1", "true", "YES", " on "):
            monkeypatch.setenv("TFL_FORMALIZATION", value)
            assert config.formalization_enabled_default() is True
        for value in ("", "0", "no", "off", "2"):
            monkeypatch.setenv("TFL_FORMALIZATION", value)
            assert config.formalization_enabled_default() is False
        monkeypatch.delenv("TFL_FORMALIZATION")
        assert config.formalization_enabled_default() is False

    def test_proved_first_attempt_result_shape(self):
        runner = FakeRunner([_body("exact foo")])
        out, check = _run_node(_state(runner), _tc("proved", axioms=["propext", "Quot.sound"]))
        f = out["formalization"]
        assert f["status"] == "proved" and f["direction"] == "dcfl"
        assert f["proof_body"] == "exact foo" and f["errors"] == []
        assert f["axioms"] == ["propext", "Quot.sound"] and f["elapsed"] == 1.5
        assert [a["status"] for a in f["attempts"]] == ["proved"]
        # statement comes from the IR via render_statement, not from any LLM output
        expected = render_statement(IR_ANBN, "dcfl")
        assert f["statement"] == {
            "imports": expected.imports, "alphabet_decl": expected.alphabet_decl,
            "language_decl": expected.language_decl, "theorem_decl": expected.theorem_decl, "name": "tfl_main",
        }
        assert runner.inputs[0]["statement"] == f["statement"]
        # the file handed to Docker is the composed one, checked for the trusted theorem name
        (text,), kwargs = check.call_args
        assert text == compose_lean_file(expected, "exact foo")
        assert kwargs["theorem_name"] == "tfl_main" and kwargs["timeout"] == config.LEAN_TIMEOUT

    def test_direction_follows_the_reasoning_verdict(self):
        out, _ = _run_node(_state(FakeRunner([_body()]), verdict="non_dcfl"))
        f = out["formalization"]
        assert f["direction"] == "non_dcfl"
        assert f["statement"]["theorem_decl"] == "theorem tfl_main : ¬ is_DCF L"

    def test_direction_falls_back_to_the_hypothesis(self):
        state = _state(FakeRunner([_body()]), verdict=None, hypothesis={"prediction": "dcfl"})
        out, _ = _run_node(state)
        assert out["formalization"]["direction"] == "dcfl"

    def test_no_direction_is_not_formalizable(self):
        runner = FakeRunner([_body()])
        for hyp in ({}, {"prediction": "uncertain"}):
            out, check = _run_node(_state(runner, verdict=None, hypothesis=hyp))
            assert out["formalization"]["status"] == "not_formalizable"
            assert out["formalization"]["direction"] is None
        assert runner.inputs == [] and check.call_count == 0

    def test_untranslatable_ir_is_not_formalizable_with_a_reason(self):
        ir = {"language_spec": {"kind": "natural", "description": "something"}}
        assert render_statement_verbose(ir, "dcfl")[0] is None
        runner = FakeRunner([_body()])
        out, check = _run_node(_state(runner, ir=ir))
        f = out["formalization"]
        assert f["status"] == "not_formalizable" and f["direction"] == "dcfl" and f["reason"]
        assert runner.inputs == [] and check.call_count == 0

    def test_plan_carries_direction_summary_and_the_matching_proof_sketch(self):
        runner = FakeRunner([_body()])
        _run_node(_state(runner))
        plan = runner.inputs[0]["plan"]
        assert "dcfl" in plan and "push/pop" in plan and "stack_strategy" in plan and '"phases"' in plan
        assert runner.inputs[0]["available_lemmas"] == []

    def test_plan_ignores_evidence_for_the_other_direction(self):
        runner = FakeRunner([_body()])
        state = _state(runner, verdict="non_dcfl")   # only stack_strategy (dcfl) evidence exists
        _run_node(state)
        assert "stack_strategy" not in runner.inputs[0]["plan"]

    def test_retry_feeds_errors_back_with_an_unchanged_statement(self):
        runner = FakeRunner([_body("bad 1"), _body("good 2")])
        err = [{"severity": "error", "data": "unknown identifier 'foo'", "pos": {"line": 30, "column": 2}}]
        out, check = _run_node(_state(runner), [_tc("error", errors=err), _tc("proved")])
        f = out["formalization"]
        assert f["status"] == "proved" and f["proof_body"] == "good 2" and f["errors"] == []
        assert [a["status"] for a in f["attempts"]] == ["error", "proved"]
        first, second = runner.inputs
        assert "errors" not in first and "previous_proof_body" not in first
        assert second["errors"] == err and second["previous_proof_body"] == "bad 1"
        assert second["statement"] == first["statement"]
        assert check.call_count == 2

    def test_error_every_time_stops_at_max_formalize_iterations(self):
        runner = FakeRunner([_body("nope")])
        with patch("dcfl_system.orchestrator.MAX_FORMALIZE_ITERATIONS", 2):
            out, check = _run_node(_state(runner), _tc("error", errors=[{"data": "e"}]))
        f = out["formalization"]
        assert f["status"] == "error" and len(runner.inputs) == 2 and check.call_count == 2
        assert f["errors"] == [{"data": "e"}] and f["proof_body"] == "nope"

    def test_default_iteration_cap_is_three(self):
        assert config.MAX_FORMALIZE_ITERATIONS == 3
        runner = FakeRunner([_body("nope")])
        out, check = _run_node(_state(runner), [_tc("error", errors=[{"data": "e"}])] * 3)
        assert len(runner.inputs) == 3 and out["formalization"]["status"] == "error"

    @pytest.mark.parametrize("status", ["has_sorry", "timeout", "unavailable"])
    def test_non_retryable_statuses_stop_at_once(self, status):
        runner = FakeRunner([_body("whatever")])
        out, check = _run_node(_state(runner), _tc(status))
        assert out["formalization"]["status"] == status
        assert len(runner.inputs) == 1 and check.call_count == 1

    def test_agent_error_is_recorded_not_raised_and_stops(self):
        runner = FakeRunner([{"agent": "lean_formalizer", "status": "agent_error", "verdict": None,
                              "confidence": 0.0, "evidence": {}, "errors": ["API error: 529 overloaded"]}])
        out, check = _run_node(_state(runner))
        f = out["formalization"]
        assert f["status"] == "error" and "529" in f["errors"][0]
        assert "errors" not in out            # never a pipeline error (R1)
        assert check.call_count == 0 and len(runner.inputs) == 1

    def test_no_output_stops(self):
        runner = FakeRunner([None])
        out, check = _run_node(_state(runner))
        assert out["formalization"]["status"] == "error" and len(runner.inputs) == 1 and check.call_count == 0

    def test_missing_proof_body_is_retried(self):
        runner = FakeRunner([{"status": "success", "evidence": {"notes": "hmm"}}, _body("ok")])
        out, check = _run_node(_state(runner), _tc("proved"))
        f = out["formalization"]
        assert f["status"] == "proved" and len(runner.inputs) == 2 and check.call_count == 1
        assert f["attempts"][0]["status"] == "no_proof_body"

    def test_empty_proof_body_is_an_honest_give_up_without_retries(self):
        give_up = {"status": "success", "evidence": {"proof_body": "", "notes": "no route in the image"}}
        runner = FakeRunner([give_up])
        out, check = _run_node(_state(runner, verdict="non_dcfl"))
        f = out["formalization"]
        assert f["status"] == "error" and "no route in the image" in f["errors"][0]
        assert f["attempts"][0]["status"] == "gave_up"
        assert len(runner.inputs) == 1 and check.call_count == 0

    def test_flat_agent_output_without_an_evidence_wrapper_is_read_too(self):
        out, _ = _run_node(_state(FakeRunner([{"proof_body": "trivial", "lemmas_used": [], "notes": ""}])))
        assert out["formalization"]["proof_body"] == "trivial"

    def test_llm_supplied_statement_is_never_used(self):
        evil = {"status": "success", "evidence": {
            "proof_body": "trivial", "statement": {"theorem_decl": "theorem tfl_main : True"},
            "theorem_decl": "theorem tfl_main : True",
        }}
        runner = FakeRunner([evil])
        out, check = _run_node(_state(runner))
        (text,), _kw = check.call_args
        assert "theorem tfl_main : is_DCF L" in text and "theorem tfl_main : True" not in text
        assert out["formalization"]["statement"]["theorem_decl"] == "theorem tfl_main : is_DCF L"

    def test_forbidden_proof_body_is_rejected_before_docker(self):
        """compose_lean_file's lexical gate (scan_proof_body) runs for real here;
        check_lean_file answers `error` without Docker, and the loop retries."""
        runner = FakeRunner([_body("run_cmd Lean.logInfo \"x\""), _body("trivial")])
        with patch("agent_system.lib.type_check.is_docker_available", return_value=False):
            out = lean_formalize_node(_state(runner))
        f = out["formalization"]
        assert f["attempts"][0]["status"] == "error"
        assert "rejected" in json.dumps(f["attempts"][0]["errors"])
        assert f["status"] == "unavailable"          # 2nd attempt: clean body, no Docker
        assert len(runner.inputs) == 2


# ---------------------------------------------------------------------------
# Graph wiring / run_pipeline
# ---------------------------------------------------------------------------

class TestPipelineWiring:
    def test_graph_routes_every_render_path_through_the_lean_node(self):
        g = build_dcfl_pipeline_graph().get_graph()
        assert "lean_formalize_node" in g.nodes
        edges = {(e.source, e.target) for e in g.edges}
        assert ("lean_formalize_node", "renderer_node") in edges
        assert ("reasoning_agent_node", "lean_formalize_node") in edges
        assert ("retry_planner_node", "lean_formalize_node") in edges
        assert ("reasoning_agent_node", "renderer_node") not in edges
        assert ("retry_planner_node", "renderer_node") not in edges

    def test_default_run_has_no_formalization_and_never_calls_the_agent(self):
        ir = _anbncm_ir()

        class Spy(MockRunner):
            called = False

            def run_agent(self, agent_name, input_data=None):
                Spy.called = Spy.called or agent_name == "lean_formalizer"
                return super().run_agent(agent_name, input_data)

        with patch("dcfl_system.orchestrator.FORMALIZATION_ENABLED", False), \
                patch("dcfl_system.orchestrator.check_lean_file") as check:
            res = run_pipeline(ir, mock_runner=Spy(str(MOCK_DIR), ir["task_id"]))
        assert res["formalization"] is None and not Spy.called and check.call_count == 0
        assert (res["verdict"], res["confidence"]) == ("dcfl", 0.85)

    def test_mock_pipeline_proved_becomes_verified_098(self):
        ir = _anbncm_ir()
        with patch("dcfl_system.orchestrator.check_lean_file", return_value=_tc("proved", axioms=["propext"])) as check:
            res = run_pipeline(ir, mock_runner=MockRunner(str(MOCK_DIR), ir["task_id"]), formalize=True)
        assert (res["verdict"], res["confidence"]) == ("dcfl", 0.98)
        assert res["formalization"]["status"] == "proved"
        gate = res["verdict_gate"]
        assert {"agent": "lean_formalizer", "trust": "verified", "basis": "lean_proof"} in gate["basis"]
        assert gate["contradiction"] is False and gate["confidence_cap"] == 0.98
        # the mock proof (not an LLM statement) is what was composed and checked
        mock_body = json.loads((MOCK_DIR / "dcfl_anbncm_lean_formalizer.json").read_text(encoding="utf-8"))
        assert res["formalization"]["proof_body"] == mock_body["evidence"]["proof_body"]
        assert check.call_count == 1

    @pytest.mark.parametrize("status", ["has_sorry", "error", "timeout", "unavailable"])
    def test_mock_pipeline_other_statuses_change_nothing(self, status):
        ir = _anbncm_ir()
        with patch("dcfl_system.orchestrator.check_lean_file", return_value=_tc(status)):
            res = run_pipeline(ir, mock_runner=MockRunner(str(MOCK_DIR), ir["task_id"]), formalize=True)
        assert (res["verdict"], res["confidence"]) == ("dcfl", 0.85)
        assert res["verdict_gate"]["basis"] == [{"agent": "stack_strategy", "trust": "bounded_pass"}]
        assert res["formalization"]["status"] == status

    def test_mock_give_up_records_an_error_not_a_crash(self):
        ir = json.loads((EXAMPLES_DIR / "task_grammar_aSSb.json").read_text(encoding="utf-8"))
        # dcfl_exam_04's lean_formalizer mock is an honest give-up (empty proof_body): no Docker call
        with patch("dcfl_system.orchestrator.check_lean_file") as check:
            res = run_pipeline(ir, mock_runner=MockRunner(str(MOCK_DIR), ir["task_id"]), formalize=True)
        assert res["formalization"]["status"] == "error" and check.call_count == 0
        assert res["verdict"] == "dcfl"

    def test_formalize_true_without_a_runner_does_nothing(self):
        ir = _anbncm_ir()
        with patch("dcfl_system.orchestrator.check_lean_file") as check:
            res = run_pipeline(ir, formalize=True)
        assert res["formalization"] is None and check.call_count == 0


# ---------------------------------------------------------------------------
# Config / prompt / mock artifacts
# ---------------------------------------------------------------------------

def _json_blocks(text: str) -> list[dict]:
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
    out = []
    for b in blocks:
        try:
            out.append(json.loads(b))
        except json.JSONDecodeError:
            pass                 # illustrative blocks with "..." placeholders
    return out


def _prompt_example() -> tuple[dict, dict]:
    blocks = _json_blocks(PROMPT_PATH.read_text(encoding="utf-8"))
    inp = next(b for b in blocks if "statement" in b and "plan" in b and "errors" not in b
               and "..." not in b["statement"].get("language_decl", "..."))
    # the worked example's output is the one with a full proof (the "Output format" block is a stub)
    out = max((b for b in blocks if "proof_body" in b), key=lambda b: len(b["proof_body"]))
    return inp, out


class TestConfigAndArtifacts:
    def test_agent_is_configured_everywhere_the_live_runner_looks(self):
        assert config.MODELS["lean_formalizer"] == "claude-opus-5-5"
        assert config.EFFORT["lean_formalizer"] == "high"
        assert "lean_formalizer" in config.TEMPERATURES
        assert config.PROMPT_FILES["lean_formalizer"] == "dcfl_lean_formalizer.md"
        assert set(config.EFFORT) == set(config.MODELS)
        assert "lean_formalizer" not in DCFL_SPECIALIST_NAMES        # not a specialist: never dispatched

    def test_live_runner_can_load_the_prompt(self):
        text = LiveRunner(api_key="sk-ant-test-not-used")._load_prompt("lean_formalizer")
        assert "you never write the formulation" in text.lower()

    def test_prompt_example_statement_is_what_render_statement_produces(self):
        inp, out = _prompt_example()
        expected = render_statement(IR_ANBN, "dcfl")
        assert inp["statement"] == {
            "imports": expected.imports, "alphabet_decl": expected.alphabet_decl,
            "language_decl": expected.language_decl, "theorem_decl": expected.theorem_decl, "name": expected.name,
        }
        assert scan_proof_body(out["proof_body"]) == []
        assert "sorry" not in re.sub(r"--.*", "", out["proof_body"])
        assert not out["proof_body"].startswith("by")

    def test_mock_outputs(self):
        anbncm = json.loads((MOCK_DIR / "dcfl_anbncm_lean_formalizer.json").read_text(encoding="utf-8"))
        body = anbncm["evidence"]["proof_body"]
        assert scan_proof_body(body) == [] and "sorry" not in re.sub(r"--.*", "", body)
        assert set(anbncm["evidence"]) == {"proof_body", "lemmas_used", "notes"}    # no formulation
        for task in ("dcfl_exam_01", "dcfl_exam_02", "dcfl_exam_03", "dcfl_exam_04"):
            m = json.loads((MOCK_DIR / f"{task}_lean_formalizer.json").read_text(encoding="utf-8"))
            assert m["evidence"]["proof_body"] == "" and m["evidence"]["notes"]

    def test_lean_file_is_written_without_bom(self):
        for p in [PROMPT_PATH, *MOCK_DIR.glob("*_lean_formalizer.json")]:
            assert not p.read_bytes().startswith(b"\xef\xbb\xbf"), p


# ---------------------------------------------------------------------------
# Real Docker (skipped without Docker + the tfl-lean4 image)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not is_docker_available(), reason="Docker with the tfl-lean4 image not available")
class TestRealDocker:
    def test_anbncm_dcfl_statement_with_sorry_is_has_sorry(self):
        stmt = render_statement(_anbncm_ir(), "dcfl")
        assert stmt is not None
        result = check_lean_file(compose_lean_file(stmt, "sorry"), timeout=180, theorem_name=stmt.name)
        assert result["status"] == "has_sorry", result
        assert result["errors"] == []

    def test_anbncm_non_dcfl_statement_with_sorry_is_has_sorry(self):
        stmt = render_statement(_anbncm_ir(), "non_dcfl")
        assert stmt.theorem_decl == "theorem tfl_main : ¬ is_DCF L"
        result = check_lean_file(compose_lean_file(stmt, "sorry"), timeout=180, theorem_name=stmt.name)
        assert result["status"] == "has_sorry", result

    def test_prompt_worked_example_is_proved(self):
        _inp, out = _prompt_example()
        stmt = render_statement(IR_ANBN, "dcfl")
        result = check_lean_file(compose_lean_file(stmt, out["proof_body"]), timeout=180, theorem_name=stmt.name)
        assert result["status"] == "proved", result
        assert set(result["axioms"]) <= {"propext", "Classical.choice", "Quot.sound"}

    def test_mock_pipeline_with_the_mock_proof_ends_verified_098(self):
        """The whole chain, unpatched: mock specialists -> mock lean_formalizer
        (a real DPDA proof for {a^n b^n c^m}) -> render_statement ->
        compose_lean_file -> Docker check_lean_file -> R-Lean gate."""
        ir = _anbncm_ir()
        res = run_pipeline(ir, mock_runner=MockRunner(str(MOCK_DIR), ir["task_id"]), formalize=True)
        assert res["formalization"]["status"] == "proved", res["formalization"]
        assert (res["verdict"], res["confidence"]) == ("dcfl", 0.98)
        assert {"agent": "lean_formalizer", "trust": "verified", "basis": "lean_proof"} in res["verdict_gate"]["basis"]
