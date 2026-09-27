"""Tests for the stateful retry/invert/Level-1 flow in agent_system/graph.py
(TODO.md §2, §5; tz_tfl_agent_system.md §5.2).

Covers:
  1. A retried specialist receives its OWN previous output (`previous_output`
     in `retry_context`), and the retry planner receives a compact
     `previous_output` summary per specialist, not just status/verdict.
  2. Level-1 retry (oracle counterexample -> DFA/RE builder) re-tests the
     rebuilt artifact against the oracle every attempt (even a
     re_builder-only retry with no dfa_builder in this round's dispatch),
     bounded at 2 attempts (§5.2).
  3. The retry planner may ADD a specialist that never ran this round; an
     explicitly empty `agents_to_retry` is a terminal decision (not a full
     re-dispatch); `should_invert_hypothesis` is honored, subject to the
     same one-inversion budget as the reasoning-agent-triggered inversion.

`ScriptedRunner` is a reusable `agent_runner` test double: a per-agent
sequence of canned responses (the last one repeats once exhausted), with
every input it was called with recorded for assertions.
"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from agent_system.graph import (
    MAX_INVERSIONS,
    build_specialist_input,
    decide_after_retry_planner,
    run_retry_planner_node,
    run_pipeline,
)
from agent_system.lib.hypothesis_module import analyze_hypothesis


# ---------------------------------------------------------------------------
# ScriptedRunner — agent_runner test double
# ---------------------------------------------------------------------------

class ScriptedRunner:
    """Test double for `agent_runner`.

    `scripts` maps agent_name -> list of canned output dicts, consumed in
    order; once a list is down to its last element, that element repeats
    for any further call (so a test only has to script as many responses
    as it cares to distinguish). Every call's `input_data` is recorded in
    `self.calls[agent_name]` for assertions on what graph.py actually sent.
    """

    def __init__(self, scripts: dict[str, list[dict]]):
        self._scripts = {k: list(v) for k, v in scripts.items()}
        self.calls: dict[str, list[Any]] = {}

    def run_agent(self, agent_name: str, input_data: Any = None) -> dict:
        self.calls.setdefault(agent_name, []).append(input_data)
        queue = self._scripts.get(agent_name)
        if not queue:
            return {
                "module": agent_name,
                "status": "failure",
                "evidence": {},
                "confidence": 0.0,
                "errors": [f"ScriptedRunner: no response scripted for {agent_name!r}"],
            }
        return queue.pop(0) if len(queue) > 1 else queue[0]


# ---------------------------------------------------------------------------
# A tiny, cheap-to-verify regular language: even-length words over {a, b}.
# ---------------------------------------------------------------------------

def _even_length_ir() -> dict:
    return {
        "task_type": "classify_and_prove",
        "source_text": "even-length words over {a,b}",
        "language_spec": {
            "kind": "predicate",
            "alphabet": ["a", "b"],
            "variable": "w",
            "predicate": {
                "expr": {"kind": "length", "of_var": "w"},
                "modulus": 2,
                "remainder": 0,
            },
        },
    }


_WRONG_REGEX = "(a|b)((a|b)(a|b))*"   # odd length -- wrong
_RIGHT_REGEX = "((a|b)(a|b))*"        # even length -- correct

_RIGHT_DFA = {
    "states": ["q0", "q1"],
    "alphabet": ["a", "b"],
    "transitions": {"q0": {"a": "q1", "b": "q1"}, "q1": {"a": "q0", "b": "q0"}},
    "start": "q0",
    "accept": ["q0"],
}


def _re_out(regex: str) -> dict:
    return {
        "module": "re_builder",
        "status": "success",
        "evidence": {"regex": regex},
        "confidence": 0.9,
        "errors": [],
    }


_CLASSIFIER_ONLY_RE_BUILDER = {
    "module": "classifier",
    "status": "success",
    "evidence": {
        "verdict": "regular",
        "confidence": 0.9,
        "dispatch": {
            "re_builder": True, "dfa_builder": False,
            "pumping": False, "nerode": False, "closure": False,
        },
    },
    "confidence": 0.9,
    "errors": [],
}

_DONE_REASONING = {
    "evidence": {"verdict": "regular", "confidence": 0.9, "action": "done"},
}

_PROOF_CHECKER_NOOP = {"evidence": {}}


# ---------------------------------------------------------------------------
# 2. Level-1 retry — re-tests every attempt, bounded at 2
# ---------------------------------------------------------------------------

class TestLevel1RetryReTestsAndIsBounded(unittest.TestCase):

    def _run(self, re_builder_responses: list[dict]) -> tuple[dict, ScriptedRunner]:
        runner = ScriptedRunner({
            "classifier": [_CLASSIFIER_ONLY_RE_BUILDER],
            "re_builder": re_builder_responses,
            "reasoning": [_DONE_REASONING],
            "proof_checker": [_PROOF_CHECKER_NOOP],
        })
        result = run_pipeline(_even_length_ir(), agent_runner=runner, verbose=False)
        return result, runner

    def test_recovers_on_second_attempt_without_exhausting_budget(self):
        """initial (wrong) -> retry 1 (right) -> stop early, only 1 retry used."""
        result, runner = self._run([_re_out(_WRONG_REGEX), _re_out(_RIGHT_REGEX)])

        self.assertEqual(len(runner.calls["re_builder"]), 2)
        self.assertEqual(result["evidence"]["oracle_test"]["status"], "pass")

    def test_reruns_dfa_test_for_re_builder_only_retry_up_to_two_attempts(self):
        """dfa_builder is never dispatched -- a re_builder-only Level-1 retry
        must still convert the freshly rebuilt regex to a DFA and re-test it
        (previously only happened when dfa_builder itself was re-dispatched).
        Needs both budgeted attempts to land on the correct regex."""
        result, runner = self._run([
            _re_out(_WRONG_REGEX), _re_out(_WRONG_REGEX), _re_out(_RIGHT_REGEX),
        ])

        self.assertEqual(len(runner.calls["re_builder"]), 3)  # initial + 2 retries
        self.assertEqual(result["evidence"]["oracle_test"]["status"], "pass")
        # The Level 1 retry's enriched input must carry the oracle's own
        # counterexample so the agent knows exactly what was wrong.
        retry_call = runner.calls["re_builder"][1]
        self.assertIn("oracle_counterexample", retry_call["retry_context"])

    def test_budget_of_two_retries_is_not_exceeded(self):
        """tz_tfl_agent_system.md §5.2: 'Max 2 retries' -- a THIRD attempt
        must never happen even if the DFA/regex keeps failing."""
        result, runner = self._run(
            [_re_out(_WRONG_REGEX), _re_out(_WRONG_REGEX), _re_out(_WRONG_REGEX)],
        )

        # initial dispatch + exactly 2 retries = 3 calls, never a 4th.
        self.assertEqual(len(runner.calls["re_builder"]), 3)
        self.assertEqual(result["evidence"]["oracle_test"]["status"], "fail")


# ---------------------------------------------------------------------------
# 1. Retries carry state: previous_output (agent) + previous_output (planner)
# ---------------------------------------------------------------------------

class TestRetryCarriesPreviousOutput(unittest.TestCase):

    def test_retried_agent_sees_its_own_previous_output(self):
        classifier_out = {
            "module": "classifier", "status": "success",
            "evidence": {
                "verdict": "non_regular", "confidence": 0.6,
                "dispatch": {
                    "re_builder": False, "dfa_builder": False,
                    "pumping": True, "nerode": False, "closure": False,
                },
            },
            "confidence": 0.6, "errors": [],
        }
        pumping_1 = {
            "module": "pumping", "status": "success",
            "evidence": {"verdict": "non_regular", "proof": "first attempt proof"},
            "confidence": 0.7, "errors": [],
        }
        pumping_2 = {
            "module": "pumping", "status": "success",
            "evidence": {"verdict": "non_regular", "proof": "revised proof"},
            "confidence": 0.8, "errors": [],
        }
        reasoning_retry = {
            "evidence": {
                "action": "retry_enriched",
                "issues_found": ["pumping proof looks incomplete"],
            },
        }
        planner_out = {
            "evidence": {
                "agents_to_retry": ["pumping"],
                "feedback": {"pumping": "be more rigorous about the partition"},
            },
        }

        runner = ScriptedRunner({
            "classifier": [classifier_out],
            "pumping": [pumping_1, pumping_2],
            "reasoning": [reasoning_retry, _DONE_REASONING],
            "retry_planner": [planner_out],
            "proof_checker": [_PROOF_CHECKER_NOOP],
        })
        run_pipeline(_even_length_ir(), agent_runner=runner, verbose=False)

        # The retry planner got a compact previous_output summary, not just
        # status/verdict.
        planner_input = runner.calls["retry_planner"][0]
        prev = planner_input["specialist_results"]["pumping"]["previous_output"]
        self.assertEqual(prev["status"], "success")
        self.assertEqual(prev["verdict"], "non_regular")
        self.assertIn("first attempt proof", prev["summary"])

        # The retried pumping agent got its OWN full previous artifact, plus
        # the planner's targeted feedback.
        self.assertEqual(len(runner.calls["pumping"]), 2)
        retry_ctx = runner.calls["pumping"][1]["retry_context"]
        self.assertEqual(retry_ctx["previous_output"], pumping_1)
        self.assertEqual(retry_ctx["agent_feedback"], "be more rigorous about the partition")

    def test_build_specialist_input_omits_previous_output_for_a_fresh_agent(self):
        """An agent the planner just ADDED (never ran before) has no prior
        artifact -- previous_output must not be fabricated for it."""
        state = {
            "ir": _even_length_ir(),
            "hypothesis": {}, "classifier_evidence": {},
            "retry_context": {"feedback": {}},
            "evidence": {},  # nerode never ran
        }
        inp = build_specialist_input(state, "nerode")
        self.assertNotIn("previous_output", inp["retry_context"])

    def test_build_specialist_input_truncates_an_oversized_previous_output(self):
        """A previous artifact large enough on its own to risk a 400
        "prompt is too long" (TODO.md §2/§3) must be bounded, not embedded
        verbatim -- but the retried agent should still see a compact
        summary plus a real (truncated) excerpt, not nothing."""
        huge_proof = "x" * 20_000
        pumping_1 = {
            "module": "pumping", "status": "success",
            "evidence": {"verdict": "non_regular", "proof": huge_proof},
            "confidence": 0.7, "errors": [],
        }
        state = {
            "ir": _even_length_ir(),
            "hypothesis": {}, "classifier_evidence": {},
            "retry_context": {"feedback": {}},
            "evidence": {"pumping": pumping_1},
        }
        inp = build_specialist_input(state, "pumping")
        prev = inp["retry_context"]["previous_output"]
        self.assertIsInstance(prev, dict)
        self.assertTrue(prev.get("truncated"))
        self.assertLessEqual(len(prev["excerpt"]), 6000)
        self.assertEqual(prev["summary"]["verdict"], "non_regular")
        # Still valid JSON-serializable input for the retried agent.
        json.dumps(inp)


# ---------------------------------------------------------------------------
# 3a. Planner may ADD an agent that never ran this round
# ---------------------------------------------------------------------------

class TestPlannerCanAddAgent(unittest.TestCase):

    def test_agents_to_retry_may_include_a_never_dispatched_agent(self):
        classifier_out = {
            "module": "classifier", "status": "success",
            "evidence": {
                "verdict": "non_regular", "confidence": 0.6,
                "dispatch": {
                    "re_builder": False, "dfa_builder": False,
                    "pumping": True, "nerode": False, "closure": False,
                },
            },
            "confidence": 0.6, "errors": [],
        }
        pumping_out = {
            "module": "pumping", "status": "success",
            "evidence": {"verdict": "non_regular", "proof": "..."},
            "confidence": 0.7, "errors": [],
        }
        nerode_out = {
            "module": "nerode", "status": "success",
            "evidence": {"verdict": "non_regular", "proof": "..."},
            "confidence": 0.7, "errors": [],
        }
        reasoning_retry = {"evidence": {"action": "retry_enriched", "issues_found": []}}
        planner_out = {"evidence": {"agents_to_retry": ["pumping", "nerode"]}}

        runner = ScriptedRunner({
            "classifier": [classifier_out],
            "pumping": [pumping_out, pumping_out],
            "nerode": [nerode_out],
            "reasoning": [reasoning_retry, _DONE_REASONING],
            "retry_planner": [planner_out],
            "proof_checker": [_PROOF_CHECKER_NOOP],
        })
        run_pipeline(_even_length_ir(), agent_runner=runner, verbose=False)

        # nerode was never in the original dispatch -- it must still run.
        self.assertEqual(len(runner.calls.get("nerode", [])), 1)
        self.assertEqual(len(runner.calls["pumping"]), 2)


# ---------------------------------------------------------------------------
# 3b. Empty agents_to_retry is terminal, not a full re-dispatch
# ---------------------------------------------------------------------------

class TestEmptyAgentsToRetryIsTerminal(unittest.TestCase):

    def test_empty_list_stops_the_cycle_instead_of_rerunning_everyone(self):
        classifier_out = {
            "module": "classifier", "status": "success",
            "evidence": {
                "verdict": "non_regular", "confidence": 0.6,
                "dispatch": {
                    "re_builder": False, "dfa_builder": False,
                    "pumping": True, "nerode": False, "closure": False,
                },
            },
            "confidence": 0.6, "errors": [],
        }
        pumping_out = {
            "module": "pumping", "status": "success",
            "evidence": {"verdict": "non_regular", "proof": "..."},
            "confidence": 0.7, "errors": [],
        }
        reasoning_retry = {"evidence": {"action": "retry_enriched", "issues_found": []}}
        planner_out = {"evidence": {"agents_to_retry": []}}

        runner = ScriptedRunner({
            "classifier": [classifier_out],
            "pumping": [pumping_out],
            "reasoning": [reasoning_retry],  # only ONE scripted -- a 2nd call is a bug
            "retry_planner": [planner_out],
            "proof_checker": [_PROOF_CHECKER_NOOP],
        })
        result = run_pipeline(_even_length_ir(), agent_runner=runner, verbose=False)

        self.assertEqual(result["module"], "orchestrator")
        # Nothing re-ran: pumping was not re-dispatched, and reasoning /
        # proof_checker were not looped through a second time either.
        self.assertEqual(len(runner.calls["pumping"]), 1)
        self.assertEqual(len(runner.calls["reasoning"]), 1)
        self.assertEqual(len(runner.calls["proof_checker"]), 1)

    def test_decide_after_retry_planner_routes_empty_plan_to_terminal(self):
        self.assertEqual(
            decide_after_retry_planner({"retry_plan": {"should_invert": False, "has_retry": False}}),
            "terminal",
        )
        self.assertEqual(
            decide_after_retry_planner({"retry_plan": {"should_invert": False, "has_retry": True}}),
            "retry",
        )
        self.assertEqual(
            decide_after_retry_planner({"retry_plan": {"should_invert": True, "has_retry": False}}),
            "invert",
        )
        self.assertEqual(decide_after_retry_planner({}), "terminal")


# ---------------------------------------------------------------------------
# 3c. should_invert_hypothesis is read and honored, with a budget
# ---------------------------------------------------------------------------

class TestShouldInvertHypothesisIsHonored(unittest.TestCase):

    def test_planner_can_trigger_inversion_once(self):
        ir = _even_length_ir()
        original_hyp = analyze_hypothesis(ir)["hypothesis"]

        classifier_out = {
            "module": "classifier", "status": "success",
            "evidence": {
                "verdict": original_hyp, "confidence": 0.6,
                "dispatch": {
                    "re_builder": False, "dfa_builder": False,
                    "pumping": True, "nerode": False, "closure": False,
                },
            },
            "confidence": 0.6, "errors": [],
        }
        pumping_out = {
            "module": "pumping", "status": "success",
            "evidence": {"verdict": "non_regular", "proof": "..."},
            "confidence": 0.7, "errors": [],
        }
        fresh_out = lambda name: {  # noqa: E731
            "module": name, "status": "success",
            "evidence": {"regex": _RIGHT_REGEX, "dfa": _RIGHT_DFA, "verdict": "non_regular", "proof": "..."},
            "confidence": 0.7, "errors": [],
        }
        reasoning_retry = {"evidence": {"action": "retry_enriched", "issues_found": []}}
        planner_out = {
            "evidence": {"should_invert_hypothesis": True, "agents_to_retry": ["pumping"]},
        }

        runner = ScriptedRunner({
            "classifier": [classifier_out],
            "pumping": [pumping_out, pumping_out],
            "re_builder": [fresh_out("re_builder")],
            "dfa_builder": [fresh_out("dfa_builder")],
            "nerode": [fresh_out("nerode")],
            "closure": [fresh_out("closure")],
            "reasoning": [reasoning_retry, _DONE_REASONING],
            "retry_planner": [planner_out],
            "proof_checker": [_PROOF_CHECKER_NOOP],
        })
        result = run_pipeline(ir, agent_runner=runner, verbose=False)

        expected_flip = "non_regular" if original_hyp == "regular" else "regular"
        self.assertEqual(result["evidence"]["hypothesis"]["hypothesis"], expected_flip)

        # Inversion resets dispatch to the full specialist set.
        for name in ("re_builder", "dfa_builder", "nerode", "closure"):
            self.assertEqual(len(runner.calls[name]), 1)
        self.assertEqual(len(runner.calls["pumping"]), 2)

    def test_run_retry_planner_node_ignores_invert_request_once_budget_spent(self):
        """When inversions_done already == MAX_INVERSIONS, a planner asking
        for should_invert_hypothesis must fall back to normal agent-retry
        handling instead of inverting again."""
        state = {
            "reasoning_output": {"evidence": {"issues_found": []}},
            "dispatch": {"pumping": True},
            "evidence": {
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
            },
            "test_result": None,
            "hypothesis": {"hypothesis": "non_regular"},
            "retry_round": 0,
            "inversions_done": MAX_INVERSIONS,
            "lang_kind": "",
            "mock_runner": None, "agent_runner": None, "verbose": False,
        }
        with patch(
            "agent_system.graph.run_agent",
            return_value={
                "evidence": {"should_invert_hypothesis": True, "agents_to_retry": []},
            },
        ):
            result = run_retry_planner_node(state)

        self.assertFalse(result["retry_plan"]["should_invert"])
        self.assertIn("dispatch", result)  # normal agent-retry path ran instead


if __name__ == "__main__":
    unittest.main()
