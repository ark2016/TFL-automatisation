"""Tests for cfl_system.orchestrator."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from cfl_system.orchestrator import (
    MAX_RETRIES,
    MAX_INVERSIONS,
    CFL_SPECIALIST_NAMES,
    MockRunner,
    LiveRunner,
    _extract_json,
    run_pipeline,
    build_cfl_pipeline_graph,
    validate_ir_node,
    analyze_hypothesis_node,
    run_classifier_node,
    language_preprocess_node,
    setup_dispatch_node,
    run_specialist_node,
    dispatch_to_specialists,
    collect_specialists_node,
    build_oracle_node,
    verify_claims_node,
    oracle_test_node,
    run_reasoning_node,
    decide_retry,
    run_retry_planner_node,
    invert_hypothesis_node,
    formalize_node,
    assemble_result_node,
    assemble_early_failure,
    _fallback_reasoning,
    _collect_failed_agents,
    _run_agent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"


@pytest.fixture
def sample_ir():
    """Load the w1w2w1w3 task IR."""
    path = EXAMPLES_DIR / "task_w1w2w1w3.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def grammar_filter_ir():
    """Load the grammar_filter_49 task IR if it exists."""
    path = EXAMPLES_DIR / "task_grammar_filter_49.json"
    if not path.exists():
        pytest.skip("task_grammar_filter_49.json not found")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def mock_runner():
    return MockRunner(str(MOCK_DIR), "task_w1w2w1w3")


@pytest.fixture
def base_state(sample_ir, mock_runner):
    """A minimal initialized state dict."""
    return {
        "ir": sample_ir,
        "mock_runner": mock_runner,
        "agent_runner": None,
        "verbose": False,
        "hypothesis": {},
        "classifier_output": {},
        "preprocess_output": {},
        "dispatch": {},
        "agents_to_retry": None,
        "specialist_outputs": [],
        "agent_results": {},
        "oracle_fn": None,
        "oracle_ok": False,
        "claim_verification": {},
        "oracle_test_result": {},
        "reasoning_output": {},
        "proof_checker_output": {},
        "retry_round": 0,
        "inversions_done": 0,
        "retry_context": {},
        "retry_params": {},
        "evidence": {},
        "errors": [],
        "result": {},
    }


# ---------------------------------------------------------------------------
# MockRunner tests
# ---------------------------------------------------------------------------

class TestMockRunner:
    def test_loads_task_specific_file(self, mock_runner):
        out = mock_runner.run_agent("reasoning")
        assert out is not None
        assert out["action"] == "done"

    def test_falls_back_to_generic(self):
        runner = MockRunner(str(MOCK_DIR), "nonexistent_task")
        # retry_planner.json is generic (no task prefix)
        out = runner.run_agent("retry_planner")
        assert out is not None
        assert "agents_to_retry" in out

    def test_returns_none_for_missing(self, mock_runner):
        out = mock_runner.run_agent("does_not_exist_agent")
        assert out is None

    def test_with_temp_dir(self):
        with tempfile.TemporaryDirectory() as td:
            data = {"test": True}
            (Path(td) / "mytask_foo.json").write_text(json.dumps(data))
            runner = MockRunner(td, "mytask")
            assert runner.run_agent("foo") == {"test": True}
            assert runner.run_agent("bar") is None


class TestLiveRunner:
    def test_requires_api_key(self, monkeypatch):
        """LiveRunner raises ValueError without an API key anywhere.

        Blocks all three sources LiveRunner.__init__ can pull a key from —
        the explicit os.environ var, the config-module constant, and the
        project .env file — so the test is deterministic regardless of
        whether the machine running it has a real key configured, and so
        that key never leaks into os.environ as a side effect of the test
        (docs/TODO.md §5).
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr("cfl_system.config.ANTHROPIC_API_KEY", "")
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            LiveRunner(api_key="")


# ---------------------------------------------------------------------------
# Node-level tests
# ---------------------------------------------------------------------------

class TestValidateIRNode:
    def test_valid_ir(self, base_state):
        result = validate_ir_node(base_state)
        assert "errors" not in result or result["errors"] == []

    def test_invalid_ir(self):
        state = {"ir": {"bad": "data"}}
        result = validate_ir_node(state)
        assert len(result["errors"]) > 0

    def test_missing_task_type(self):
        state = {"ir": {"source_text": "some text"}}
        result = validate_ir_node(state)
        assert len(result["errors"]) > 0


class TestAnalyzeHypothesisNode:
    def test_returns_hypothesis(self, base_state):
        result = analyze_hypothesis_node(base_state)
        assert "hypothesis" in result
        hyp = result["hypothesis"]
        assert hyp["hypothesis"] in ("cfl", "non_cfl", "unknown")
        assert 0.0 <= hyp["confidence"] <= 1.0
        assert "suggested_agents" in hyp


class TestRunClassifierNode:
    def test_with_mock(self, base_state):
        result = run_classifier_node(base_state)
        assert "classifier_output" in result
        assert result["classifier_output"]["classification"] == "non_cfl"

    def test_without_runner(self, sample_ir):
        state = {"ir": sample_ir, "mock_runner": None, "agent_runner": None, "evidence": {}}
        result = run_classifier_node(state)
        # No output since no runner
        assert "classifier_output" not in result


class TestLanguagePreprocessNode:
    def test_returns_preprocess_output(self, base_state):
        result = language_preprocess_node(base_state)
        assert "preprocess_output" in result
        pp = result["preprocess_output"]
        assert "quick_verdict" in pp
        assert "quick_verdict_reason" in pp


class TestSetupDispatchNode:
    def test_first_run_dispatches_all(self, base_state):
        result = setup_dispatch_node(base_state)
        assert all(result["dispatch"][name] for name in CFL_SPECIALIST_NAMES)

    def test_selective_retry(self, base_state):
        base_state["agents_to_retry"] = ["pumping_cfl", "ogden"]
        result = setup_dispatch_node(base_state)
        assert result["dispatch"]["pumping_cfl"] is True
        assert result["dispatch"]["ogden"] is True
        assert result["dispatch"]["cfg_builder"] is False
        assert result["dispatch"]["morphism"] is False

    def test_empty_retry_list_falls_back_to_all(self, base_state):
        """Empty retry list is a degenerate state; fall back to all agents.
        (The terminal case is handled earlier by decide_after_retry_planner.)
        """
        base_state["agents_to_retry"] = []
        result = setup_dispatch_node(base_state)
        assert all(v is True for v in result["dispatch"].values())

    def test_invalid_agents_filtered_fallback(self, base_state):
        """Unknown agent names trigger fallback to all agents."""
        base_state["agents_to_retry"] = ["nonexistent_agent"]
        result = setup_dispatch_node(base_state)
        assert all(v is True for v in result["dispatch"].values())


class TestRunSpecialistNode:
    def test_runs_single_agent(self, base_state):
        base_state["_specialist_name"] = "pumping_cfl"
        result = run_specialist_node(base_state)
        outputs = result["specialist_outputs"]
        assert len(outputs) == 1
        assert outputs[0][0] == "pumping_cfl"
        assert outputs[0][1] is not None

    def test_missing_agent_emits_none(self, base_state):
        """Missing agent emits (name, None) marker so collect can drop stale results."""
        base_state["_specialist_name"] = "interchange"  # no mock file
        result = run_specialist_node(base_state)
        assert result["specialist_outputs"] == [("interchange", None)]

    def test_dispatch_to_specialists_sends(self, base_state):
        base_state["dispatch"] = {name: True for name in CFL_SPECIALIST_NAMES}
        sends = dispatch_to_specialists(base_state)
        assert len(sends) == len(CFL_SPECIALIST_NAMES)

    def test_dispatch_empty_goes_to_collect(self, base_state):
        base_state["dispatch"] = {name: False for name in CFL_SPECIALIST_NAMES}
        sends = dispatch_to_specialists(base_state)
        assert len(sends) == 1  # goes to collect_specialists_node


class TestCollectSpecialistsNode:
    def test_merges_outputs(self, base_state):
        base_state["dispatch"] = {"pumping_cfl": True, "ogden": True}
        base_state["specialist_outputs"] = [
            ("pumping_cfl", {"verdict": "non_cfl"}),
            ("ogden", {"verdict": "non_cfl"}),
        ]
        result = collect_specialists_node(base_state)
        assert "pumping_cfl" in result["agent_results"]
        assert "ogden" in result["agent_results"]
        assert "pumping_cfl" in result["evidence"]

    def test_overwrites_on_retry(self, base_state):
        base_state["dispatch"] = {"pumping_cfl": True}
        base_state["agent_results"] = {"pumping_cfl": {"verdict": "old"}}
        base_state["specialist_outputs"] = [
            ("pumping_cfl", {"verdict": "new"}),
        ]
        result = collect_specialists_node(base_state)
        assert result["agent_results"]["pumping_cfl"]["verdict"] == "new"

    def test_preserves_old_results(self, base_state):
        """Non-dispatched agents keep their prior results across retries."""
        base_state["dispatch"] = {"pumping_cfl": True}  # only pumping retried
        base_state["agent_results"] = {"cfg_builder": {"grammar": {}}}
        base_state["specialist_outputs"] = [
            ("pumping_cfl", {"verdict": "non_cfl"}),
        ]
        result = collect_specialists_node(base_state)
        assert "cfg_builder" in result["agent_results"]  # preserved
        assert "pumping_cfl" in result["agent_results"]  # newly added

    def test_failed_retry_drops_stale_result(self, base_state):
        """When a retried agent emits None, its prior result is dropped."""
        base_state["dispatch"] = {"cfg_builder": True}
        base_state["agent_results"] = {"cfg_builder": {"grammar": {"old": True}}}
        base_state["specialist_outputs"] = [("cfg_builder", None)]
        result = collect_specialists_node(base_state)
        assert "cfg_builder" not in result["agent_results"]


class TestBuildOracleNode:
    def test_builds_oracle(self, base_state):
        result = build_oracle_node(base_state)
        assert result.get("oracle_ok") is True
        assert result["oracle_fn"] is not None

    def test_skips_on_retry_if_exists(self, base_state):
        base_state["retry_round"] = 1
        base_state["oracle_fn"] = lambda w: False
        result = build_oracle_node(base_state)
        assert result == {}

    def test_builds_on_retry_if_missing(self, base_state):
        base_state["retry_round"] = 1
        base_state["oracle_fn"] = None
        result = build_oracle_node(base_state)
        assert result.get("oracle_ok") is True


class TestVerifyClaimsNode:
    def test_verifies_pumping_claim(self, base_state):
        base_state["agent_results"] = {
            "pumping_cfl": {
                "agent": "pumping_cfl",
                "status": "success",
                "evidence": {
                    "word_chosen": "abba",
                    "cases": [{"case": "test", "why_not_in_L": "broken structure"}],
                    "all_cases_covered": True,
                },
            },
        }
        result = verify_claims_node(base_state)
        assert "pumping_cfl" in result["claim_verification"]

    def test_empty_results(self, base_state):
        base_state["agent_results"] = {}
        result = verify_claims_node(base_state)
        assert result["claim_verification"] == {}


class TestOracleTestNode:
    def test_skips_without_oracle(self, base_state):
        base_state["oracle_fn"] = None
        result = oracle_test_node(base_state)
        assert result["oracle_test_result"]["status"] == "not_applicable"

    def test_skips_without_constructive_evidence(self, base_state):
        base_state["oracle_fn"] = lambda w: False
        base_state["agent_results"] = {"pumping_cfl": {"verdict": "non_cfl"}}
        result = oracle_test_node(base_state)
        assert result["oracle_test_result"]["status"] == "not_applicable"


class TestDecideRetry:
    def test_done(self):
        state = {"reasoning_output": {"action": "done"}, "retry_round": 0, "inversions_done": 0}
        assert decide_retry(state) == "done"

    def test_retry_within_limit(self):
        state = {"reasoning_output": {"action": "retry"}, "retry_round": 1, "inversions_done": 0}
        assert decide_retry(state) == "retry"

    def test_retry_at_limit(self):
        state = {"reasoning_output": {"action": "retry"}, "retry_round": MAX_RETRIES, "inversions_done": 0}
        assert decide_retry(state) == "fail"

    def test_invert_within_limit(self):
        state = {"reasoning_output": {"action": "invert"}, "retry_round": 0, "inversions_done": 0}
        assert decide_retry(state) == "invert"

    def test_invert_at_limit(self):
        state = {"reasoning_output": {"action": "invert"}, "retry_round": 0, "inversions_done": MAX_INVERSIONS}
        assert decide_retry(state) == "fail"

    def test_missing_action(self):
        state = {"reasoning_output": {}, "retry_round": 0, "inversions_done": 0}
        assert decide_retry(state) == "done"


class TestInvertHypothesisNode:
    def test_cfl_to_non_cfl(self, base_state):
        base_state["hypothesis"] = {"hypothesis": "cfl", "confidence": 0.7}
        result = invert_hypothesis_node(base_state)
        assert result["hypothesis"]["hypothesis"] == "non_cfl"
        assert result["inversions_done"] == 1
        assert result["agents_to_retry"] is None

    def test_non_cfl_to_cfl(self, base_state):
        base_state["hypothesis"] = {"hypothesis": "non_cfl", "confidence": 0.7}
        result = invert_hypothesis_node(base_state)
        assert result["hypothesis"]["hypothesis"] == "cfl"

    def test_unknown_to_cfl(self, base_state):
        base_state["hypothesis"] = {"hypothesis": "unknown", "confidence": 0.3}
        result = invert_hypothesis_node(base_state)
        assert result["hypothesis"]["hypothesis"] == "cfl"


class TestRetryPlannerNode:
    def test_with_mock(self, base_state):
        result = run_retry_planner_node(base_state)
        assert result["agents_to_retry"] == ["pumping_cfl", "ogden"]
        assert "pumping_cfl" in result["retry_params"]

    def test_without_runner(self, sample_ir):
        state = {"ir": sample_ir, "mock_runner": None, "agent_runner": None, "reasoning_output": {}}
        result = run_retry_planner_node(state)
        assert result["agents_to_retry"] is None  # retry all


class TestFormalizeNodeBaseState:
    # Renamed from the duplicate `TestFormalizeNode` (there is another one
    # below, further down the file) — same class name twice meant this one
    # silently overwrote the other in the module namespace and pytest never
    # collected it.
    def test_noop(self, base_state):
        result = formalize_node(base_state)
        assert result == {}


class TestAssembleResultNode:
    def test_basic_assembly(self, base_state):
        base_state["reasoning_output"] = {"verdict": "non_cfl", "confidence": 0.85}
        base_state["agent_results"] = {"pumping_cfl": {"verdict": "non_cfl"}}
        base_state["oracle_test_result"] = {"status": "skipped"}
        result = assemble_result_node(base_state)
        r = result["result"]
        assert r["verdict"] == "non_cfl"
        assert r["confidence"] == 0.85
        assert r["task"] == "classify_and_prove_cfl"
        assert "pumping_cfl" in r["agents_used"]
        assert r["retries"] == 0

    def test_with_constructive_grammar(self, base_state):
        base_state["reasoning_output"] = {"verdict": "cfl", "confidence": 0.9}
        base_state["agent_results"] = {
            "cfg_builder": {"grammar": {"start": "S", "rules": []}},
        }
        result = assemble_result_node(base_state)
        assert result["result"]["grammar"] is not None


class TestAssembleEarlyFailure:
    def test_validation_errors(self, base_state):
        base_state["errors"] = ["Missing task_type"]
        result = assemble_early_failure(base_state)
        r = result["result"]
        assert r["verdict"] == "failure"
        assert "Missing task_type" in r["errors"]

    def test_inconclusive(self, base_state):
        base_state["errors"] = []
        base_state["reasoning_output"] = {"reasoning": "Not enough evidence"}
        result = assemble_early_failure(base_state)
        assert result["result"]["verdict"] == "inconclusive"


class TestFallbackReasoning:
    def test_quick_verdict_wins(self, base_state):
        base_state["preprocess_output"] = {
            "quick_verdict": "cfl",
            "quick_verdict_reason": "grammar + regular filter",
        }
        result = _fallback_reasoning(base_state)
        assert result["action"] == "done"
        assert result["verdict"] == "cfl"

    def test_oracle_pass_wins(self, base_state):
        base_state["preprocess_output"] = {}
        base_state["oracle_test_result"] = {"status": "pass"}
        result = _fallback_reasoning(base_state)
        assert result["action"] == "done"
        assert result["verdict"] == "cfl"

    def test_strong_hypothesis_with_verification(self, base_state):
        base_state["preprocess_output"] = {}
        base_state["oracle_test_result"] = {}
        base_state["hypothesis"] = {"hypothesis": "non_cfl", "confidence": 0.85}
        base_state["claim_verification"] = {
            "pumping_cfl": {"verification_status": "verified"},
        }
        result = _fallback_reasoning(base_state)
        assert result["action"] == "done"
        assert result["verdict"] == "non_cfl"

    def test_agent_verdict(self, base_state):
        base_state["preprocess_output"] = {}
        base_state["oracle_test_result"] = {}
        base_state["hypothesis"] = {"hypothesis": "unknown", "confidence": 0.3}
        base_state["claim_verification"] = {}
        base_state["agent_results"] = {
            "ogden": {"verdict": "non_cfl", "confidence": 0.8},
        }
        result = _fallback_reasoning(base_state)
        assert result["action"] == "done"
        assert result["verdict"] == "non_cfl"

    def test_retry_when_inconclusive(self, base_state):
        base_state["preprocess_output"] = {}
        base_state["oracle_test_result"] = {}
        base_state["hypothesis"] = {"hypothesis": "unknown", "confidence": 0.3}
        base_state["claim_verification"] = {}
        base_state["agent_results"] = {}
        base_state["retry_round"] = 0
        result = _fallback_reasoning(base_state)
        assert result["action"] == "retry"

    def test_gives_up_at_max_retries(self, base_state):
        base_state["preprocess_output"] = {}
        base_state["oracle_test_result"] = {}
        base_state["hypothesis"] = {"hypothesis": "unknown", "confidence": 0.3}
        base_state["claim_verification"] = {}
        base_state["agent_results"] = {}
        base_state["retry_round"] = MAX_RETRIES
        result = _fallback_reasoning(base_state)
        assert result["action"] == "done"


# ---------------------------------------------------------------------------
# Integration tests: full pipeline
# ---------------------------------------------------------------------------

class TestRunPipeline:
    def test_with_mock_runner(self, sample_ir, mock_runner):
        """Full pipeline with mock data should produce a result."""
        result = run_pipeline(sample_ir, mock_runner=mock_runner)
        assert result["task"] == "classify_and_prove_cfl"
        assert result["verdict"] in ("cfl", "non_cfl", "inconclusive", "failure")
        assert "agents_used" in result
        assert "retries" in result

    def test_invalid_ir(self):
        """Pipeline should fail fast on invalid IR."""
        result = run_pipeline({"bad": "data"})
        assert result["verdict"] == "failure"
        assert len(result["errors"]) > 0

    def test_without_any_runner(self, sample_ir):
        """Pipeline should still complete with fallback reasoning."""
        result = run_pipeline(sample_ir)
        assert result["verdict"] in ("cfl", "non_cfl", "inconclusive", "failure")

    def test_result_shape(self, sample_ir, mock_runner):
        """Verify the result dict has all required keys."""
        result = run_pipeline(sample_ir, mock_runner=mock_runner)
        required_keys = {
            "task", "source_text", "verdict", "confidence",
            "proof", "grammar", "pda", "oracle_test",
            "agents_used", "retries",
        }
        assert required_keys.issubset(set(result.keys()))

    def test_verbose_mode(self, sample_ir, mock_runner):
        """Verbose mode should not crash."""
        result = run_pipeline(sample_ir, mock_runner=mock_runner, verbose=True)
        assert result["verdict"] in ("cfl", "non_cfl", "inconclusive", "failure")


class TestRetryLoop:
    def test_retry_with_selective_agents(self, sample_ir):
        """Test retry loop with a mock that triggers retry then done."""
        with tempfile.TemporaryDirectory() as td:
            # Round 0: reasoning says retry
            (Path(td) / "test_reasoning.json").write_text(json.dumps({
                "action": "retry",
                "reasoning": "Need more evidence",
            }))
            (Path(td) / "test_retry_planner.json").write_text(json.dumps({
                "agents_to_retry": ["pumping_cfl"],
                "retry_params": {},
            }))

            runner = MockRunner(td, "test")
            # This will retry once, then fallback reasoning kicks in on round 1
            # (no reasoning mock for round 1 since same file returns retry again,
            # but eventually MAX_RETRIES is hit)
            result = run_pipeline(sample_ir, mock_runner=runner)
            # Should eventually terminate
            assert result["verdict"] in ("cfl", "non_cfl", "inconclusive", "failure")
            assert result["retries"] >= 0

    def test_inversion(self, sample_ir):
        """Test hypothesis inversion flow."""
        with tempfile.TemporaryDirectory() as td:
            # reasoning says invert
            (Path(td) / "test_reasoning.json").write_text(json.dumps({
                "action": "invert",
                "reasoning": "Try the other hypothesis",
            }))

            runner = MockRunner(td, "test")
            result = run_pipeline(sample_ir, mock_runner=runner)
            # Should complete (after inversion + fallback)
            assert result["verdict"] in ("cfl", "non_cfl", "inconclusive", "failure")


class TestPipelineWithGrammarFilterIR:
    def test_grammar_filter_quick_verdict(self, grammar_filter_ir):
        """Grammar filter with regular filter should get quick verdict."""
        result = run_pipeline(grammar_filter_ir)
        # May get cfl from quick verdict or inconclusive without mocks
        assert result["verdict"] in ("cfl", "non_cfl", "inconclusive", "failure")


# ---------------------------------------------------------------------------
# _extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_markdown_fenced(self):
        text = 'Some text\n```json\n{"a": 1}\n```\nMore text'
        assert _extract_json(text) == {"a": 1}

    def test_embedded_braces(self):
        text = 'The result is {"a": 1, "b": 2} and that is it.'
        assert _extract_json(text) == {"a": 1, "b": 2}

    def test_no_json(self):
        assert _extract_json("no json here") is None

    def test_whitespace(self):
        assert _extract_json('  \n{"x": true}\n  ') == {"x": True}

    def test_nested_json(self):
        text = '{"agent": "pumping", "evidence": {"word": "abc"}}'
        result = _extract_json(text)
        assert result["agent"] == "pumping"
        assert result["evidence"]["word"] == "abc"


# ---------------------------------------------------------------------------
# LiveRunner tests (mocked — no actual API calls)
# ---------------------------------------------------------------------------

class TestLiveRunnerUnit:
    """Test LiveRunner components without making real API calls."""

    def test_no_api_key_raises(self, monkeypatch):
        """LiveRunner raises ValueError if no API key is available anywhere.

        Same rationale as TestLiveRunner.test_requires_api_key: block the
        env var, the config constant, and .env loading so the assertion is
        deterministic and no real key is ever written into os.environ.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr("cfl_system.config.ANTHROPIC_API_KEY", "")
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            LiveRunner(api_key="")

    def test_load_prompt(self):
        """Verify prompt loading from prompts/ directory."""
        # We can test prompt loading without an API key by checking the file exists
        prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
        from cfl_system.config import PROMPT_FILES
        for agent_name, filename in PROMPT_FILES.items():
            path = prompts_dir / filename
            assert path.exists(), f"Missing prompt file for '{agent_name}': {path}"

    def test_prompt_files_all_agents_covered(self):
        """Every specialist + system agent should have a prompt file."""
        from cfl_system.config import PROMPT_FILES
        expected = {
            "input_parser", "classifier",
            "cfg_builder", "pda_builder", "decomposition", "parikh",
            "pumping_cfl", "ogden", "closure_reduction", "interchange", "morphism",
            "reasoning", "retry_planner", "proof_checker", "formalizer",
        }
        assert expected == set(PROMPT_FILES.keys())

    def test_models_all_agents_covered(self):
        """Every agent with a prompt should have a model assignment."""
        from cfl_system.config import MODELS, PROMPT_FILES
        for agent_name in PROMPT_FILES:
            assert agent_name in MODELS, f"Missing model for '{agent_name}'"

    def test_temperatures_all_agents_covered(self):
        """Every agent should have a temperature setting."""
        from cfl_system.config import TEMPERATURES, PROMPT_FILES
        for agent_name in PROMPT_FILES:
            assert agent_name in TEMPERATURES, f"Missing temperature for '{agent_name}'"

    def test_extract_json_from_typical_llm_response(self):
        """Test _extract_json with a typical LLM response that wraps JSON in text."""
        response = """Here is my analysis:

```json
{
  "agent": "classifier",
  "verdict": "non_cfl",
  "confidence": 0.8,
  "reasoning": "Repeated subword pattern",
  "advisory_only": true
}
```

This is my reasoning above."""
        result = _extract_json(response)
        assert result is not None
        assert result["agent"] == "classifier"
        assert result["verdict"] == "non_cfl"


class TestFormalizeNode:
    """Test the updated formalize_node (Markdown output, not Lean)."""

    def test_no_runner_noop(self, sample_ir):
        """Without a runner, formalize_node is a no-op."""
        state = {"ir": sample_ir, "reasoning_output": {"verdict": "non_cfl"}}
        result = formalize_node(state)
        assert result == {}

    def test_inconclusive_skipped(self, sample_ir):
        """If verdict is inconclusive, formalizer is not called."""
        state = {
            "ir": sample_ir,
            "mock_runner": MockRunner(str(MOCK_DIR), "task_w1w2w1w3"),
            "agent_runner": None,
            "reasoning_output": {"verdict": "inconclusive"},
            "agent_results": {},
        }
        result = formalize_node(state)
        assert result == {}

    def test_with_runner_calls_formalizer(self, sample_ir):
        """Formalize node should call formalizer agent when verdict is definite."""
        with tempfile.TemporaryDirectory() as td:
            # Create a mock formalizer output
            formalizer_output = {
                "agent": "formalizer",
                "status": "success",
                "proof_document": {
                    "title": "Доказательство",
                    "verdict": "non_cfl",
                    "method": "pumping_cfl",
                    "steps": [{"step_number": 1, "title": "Step", "content": "..."}],
                },
                "markdown": "# Proof\n...",
            }
            (Path(td) / "test_formalizer.json").write_text(
                json.dumps(formalizer_output), encoding="utf-8",
            )
            runner = MockRunner(td, "test")
            state = {
                "ir": sample_ir,
                "mock_runner": runner,
                "agent_runner": None,
                "reasoning_output": {
                    "verdict": "non_cfl",
                    "primary_evidence": "pumping_cfl",
                },
                "agent_results": {"pumping_cfl": {"status": "success"}},
                "evidence": {},
            }
            result = formalize_node(state)
            assert "evidence" in result
            assert "formalizer" in result["evidence"]
            assert "formatted_proof" in result["evidence"]


# ---------------------------------------------------------------------------
# docs/TODO.md §2 — retry-round error bookkeeping (_collect_failed_agents,
# _run_agent) and Haiku-repair-of-a-truncated-response flagging.
# ---------------------------------------------------------------------------

class TestCollectFailedAgentsAcrossRounds:
    """An agent that failed in round 1 but succeeded on retry must not stay
    in agents_failed forever — state["errors"] is append-only across the
    whole run (Annotated with operator.add), so naively scanning it used to
    keep the round-1 failure message alive even after the retry recovered.
    """

    def test_agent_dropped_after_successful_retry(self, base_state):
        base_state["errors"] = ["pumping_cfl: JSON parse failed (round 1)"]
        base_state["agent_results"] = {
            # pumping_cfl now has a real, current result (the retry
            # succeeded) — it must not appear in agents_failed any more.
            "pumping_cfl": {"agent": "pumping_cfl", "status": "success"},
        }
        failed = _collect_failed_agents(base_state)
        assert failed == []

    def test_agent_kept_if_still_absent(self, base_state):
        base_state["errors"] = ["pumping_cfl: JSON parse failed (round 1)"]
        base_state["agent_results"] = {}  # never recovered
        failed = _collect_failed_agents(base_state)
        assert failed == [{"agent": "pumping_cfl", "error": "JSON parse failed (round 1)"}]

    def test_uses_latest_error_message_not_first(self, base_state):
        base_state["errors"] = [
            "pumping_cfl: first failure (round 1)",
            "pumping_cfl: second failure (round 2)",
        ]
        base_state["agent_results"] = {}
        failed = _collect_failed_agents(base_state)
        assert failed == [{"agent": "pumping_cfl", "error": "second failure (round 2)"}]

    def test_proof_checker_and_formalizer_use_their_own_slots(self, base_state):
        base_state["errors"] = [
            "proof_checker: agent_error (round 1)",
            "formalizer: agent_error (round 1)",
        ]
        # proof_checker recovered on retry (fresh per-round slot, not additive)
        base_state["proof_checker_output"] = {"status": "verified"}
        # formalizer never recovered
        base_state["evidence"] = {}
        failed = _collect_failed_agents(base_state)
        assert failed == [{"agent": "formalizer", "error": "agent_error (round 1)"}]


class TestRunAgentRunnerException:
    """A runner exception must surface as agent_error, not vanish as None
    (docs/TODO.md §2: "исключения раннера возвращаются как None без записи —
    упавший агент выглядит пропущенным").
    """

    class _ExplodingRunner:
        def run_agent(self, agent_name, input_data=None):
            raise RuntimeError("boom: simulated network failure")

    def test_run_agent_returns_agent_error_dict(self, base_state):
        base_state["mock_runner"] = self._ExplodingRunner()
        out = _run_agent(base_state, "pumping_cfl", {})
        assert out is not None
        assert out["status"] == "agent_error"
        assert "boom" in out["errors"][0]

    def test_run_specialist_node_records_the_error(self, base_state):
        base_state["mock_runner"] = self._ExplodingRunner()
        base_state["dispatch"] = {"pumping_cfl": True}
        base_state["_specialist_name"] = "pumping_cfl"
        result = run_specialist_node(base_state)
        assert result["specialist_outputs"] == [("pumping_cfl", None)]
        assert any("pumping_cfl" in e and "boom" in e for e in result["errors"])


class TestHaikuRepairTruncatedFlag:
    """A response that hit max_tokens and had to be Haiku-repaired is not a
    full agent output: flag it (_truncated/_repaired), downgrade its status
    to inconclusive, and cap its confidence at 0.40 so it is never treated
    as a fully successful result (docs/TODO.md §2, docs/VERDICT_POLICY.md §2).
    """

    class _FakeFinalMessage:
        def __init__(self, model, stop_reason, tokens_in, tokens_out):
            self.model = model
            self.stop_reason = stop_reason
            self.usage = type("Usage", (), {
                "input_tokens": tokens_in, "output_tokens": tokens_out,
            })()

    class _FakeStream:
        def __init__(self, text, final_message):
            self._text = text
            self._final_message = final_message

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        @property
        def text_stream(self):
            return iter([self._text])

        def get_final_message(self):
            return self._final_message

    class _FakeMessages:
        def __init__(self, stream_text, stop_reason, repaired_json):
            self._stream_text = stream_text
            self._stop_reason = stop_reason
            self._repaired_json = repaired_json

        def stream(self, **kwargs):
            final = TestHaikuRepairTruncatedFlag._FakeFinalMessage(
                model=kwargs["model"], stop_reason=self._stop_reason,
                tokens_in=100, tokens_out=8000,
            )
            return TestHaikuRepairTruncatedFlag._FakeStream(self._stream_text, final)

        def create(self, **kwargs):
            block = type("Block", (), {"text": self._repaired_json})()
            return type("Response", (), {
                "content": [block],
                "usage": type("Usage", (), {"input_tokens": 10, "output_tokens": 20})(),
            })()

    class _FakeClient:
        def __init__(self, stream_text, stop_reason, repaired_json):
            self.messages = TestHaikuRepairTruncatedFlag._FakeMessages(
                stream_text, stop_reason, repaired_json,
            )

    def _make_runner(self, stream_text, stop_reason, repaired_json):
        runner = LiveRunner(api_key="fake-key-for-unit-test")
        runner.client = self._FakeClient(stream_text, stop_reason, repaired_json)
        return runner

    def test_truncated_repair_is_flagged_and_capped(self):
        runner = self._make_runner(
            stream_text='{"agent": "pumping_cfl", "status": "success", "confidence": 0.95, "evi',
            stop_reason="max_tokens",
            repaired_json=(
                '{"agent": "pumping_cfl", "status": "success", "confidence": 0.95, '
                '"evidence": {}, "errors": []}'
            ),
        )
        out = runner.run_agent("pumping_cfl", {})
        assert out["_truncated"] is True
        assert out["_repaired"] is True
        assert out["status"] == "inconclusive"
        assert out["confidence"] <= 0.40

    def test_non_truncated_repair_is_marked_but_not_downgraded(self):
        # A trailing comma (not truncation — stop_reason="end_turn") is a
        # syntax slip none of the three extraction strategies can parse, so
        # Haiku has to repair it, but the underlying agent did finish
        # reasoning: only `_repaired` is set, no truncation, no downgrade.
        runner = self._make_runner(
            stream_text='{"agent": "pumping_cfl", "status": "success", '
                        '"confidence": 0.9, "evidence": {}, "errors": [],}',
            stop_reason="end_turn",
            repaired_json=(
                '{"agent": "pumping_cfl", "status": "success", "confidence": 0.9, '
                '"evidence": {}, "errors": []}'
            ),
        )
        out = runner.run_agent("pumping_cfl", {})
        assert out["_repaired"] is True
        assert "_truncated" not in out
        assert out["status"] == "success"
        assert out["confidence"] == 0.9
