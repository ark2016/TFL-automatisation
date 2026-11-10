"""stack_strategy DPDA designs for the eval traps dcfl-04 and dcfl-15 (TODO §7:
"R2′-нормализация ε-переходов не спасает конкретные ДМПА dcfl-04/dcfl-15").

The live Haiku automata for these two tasks were refuted (docs/EVAL_RESULTS.md): an
epsilon "accept" hop sharing (state, top) with letter moves, and a start-state move on a
later block's letter that accepted `b`, `bc`, ... . dcfl_system/prompts/stack_strategy.md
now carries "Design rules" against exactly these defects; the two mock replies
(examples/mock/dcfl-04_stack_strategy.json, dcfl-15_stack_strategy.json) are DPDAs built
by those rules. These tests check, in mock mode only, that such designs pass the same
R2' verification the live run uses (determinism + simulation against the task's own
membership oracle) and take the pipeline to `dcfl`, and that the defects the rules forbid
are still refuted.
"""
from __future__ import annotations

import copy
import itertools
import json
import re
from pathlib import Path

import pytest

from dcfl_system.lib.agent_output_schema import schema_for
from dcfl_system.lib.dpda import _normalize_read, check_determinism, dpda_run
from dcfl_system.lib.oracle_verifier import verify_agent_results
from dcfl_system.lib.word_sampler import build_membership_oracle_from_ir
from dcfl_system.orchestrator import MockRunner, run_pipeline

DCFL = Path(__file__).resolve().parent.parent
MOCK_DIR = DCFL / "examples" / "mock"
EVAL_DIR = DCFL / "examples" / "eval"
PROMPT = DCFL / "prompts" / "stack_strategy.md"

TASKS = ("dcfl-04", "dcfl-15")

# Words of length <= 3 in each language, written out by hand from the definitions:
# dcfl-04 {aⁿbⁿcᵐ | n, m >= 0}; dcfl-15 {u₁au₂ | |u₁| <= |u₂|} = {bᵏ·a·u | |u| >= k}.
SHORT_MEMBERS = {
    "dcfl-04": {"", "c", "cc", "ccc", "ab", "abc"},
    "dcfl-15": {"a", "aa", "ab", "aaa", "aab", "aba", "abb", "baa", "bab"},
}


def _ir(task: str) -> dict:
    return json.loads((EVAL_DIR / f"{task}.json").read_text(encoding="utf-8"))


def _mock(task: str) -> dict:
    return json.loads((MOCK_DIR / f"{task}_stack_strategy.json").read_text(encoding="utf-8"))


def _words(alphabet, max_len):
    for n in range(max_len + 1):
        for tup in itertools.product(alphabet, repeat=n):
            yield "".join(tup)


def _verify(task: str, output: dict) -> dict:
    return verify_agent_results({"stack_strategy": output}, _ir(task))["stack_strategy"]


@pytest.mark.parametrize("task", TASKS)
class TestDesignedDpda:
    def test_mock_matches_structured_output_schema(self, task):
        jsonschema = pytest.importorskip("jsonschema")
        jsonschema.validate(_mock(task), schema_for("stack_strategy"))

    def test_no_epsilon_and_deterministic(self, task):
        dpda = _mock(task)["proof_sketch"]["dpda"]
        assert all(_normalize_read(t["read"]) is not None for t in dpda["transitions"])
        assert check_determinism(dpda) == []

    def test_agrees_with_task_oracle_on_all_short_words(self, task):
        ir = _ir(task)
        oracle = build_membership_oracle_from_ir(ir)
        assert oracle is not None
        dpda = _mock(task)["proof_sketch"]["dpda"]
        max_len = 7 if len(ir["alphabet"]) == 3 else 10
        decided = 0
        mismatches = []
        for w in _words(ir["alphabet"], max_len):
            expected = oracle(w)
            if expected is None:
                continue
            decided += 1
            if dpda_run(dpda, w) != expected:
                mismatches.append(w)
        assert mismatches == []
        assert decided > 1000

    def test_short_words_listed_in_evidence_are_exactly_the_members(self, task):
        ir = _ir(task)
        dpda = _mock(task)["proof_sketch"]["dpda"]
        oracle = build_membership_oracle_from_ir(ir)
        accepted = {w for w in _words(ir["alphabet"], 3) if dpda_run(dpda, w)}
        assert accepted == SHORT_MEMBERS[task]
        assert {w for w in _words(ir["alphabet"], 3) if oracle(w)} == SHORT_MEMBERS[task]
        self_check = next(e for e in _mock(task)["evidence"] if e.startswith("Самопроверка"))
        listed = set(re.findall(r"\b[a-c]+\b", self_check.split(":", 1)[1].split("(")[0]))
        assert listed == {w for w in SHORT_MEMBERS[task] if w}

    def test_oracle_verifier_bounded_pass(self, task):
        result = _verify(task, _mock(task))
        assert result["verification_status"] == "bounded_pass", result
        assert result["details"]["determinism"] == "verified"
        assert "normalization" not in result["details"]

    def test_pipeline_reaches_dcfl(self, task):
        """Only the stack_strategy mock exists for these tasks: every other agent
        returns None, so the verdict comes from the gate over this artifact alone."""
        result = run_pipeline(_ir(task), mock_runner=MockRunner(str(MOCK_DIR), task))
        assert result["verdict"] == "dcfl"
        assert result["confidence"] == pytest.approx(0.85)
        assert result["verdict_gate"]["basis"] == [
            {"agent": "stack_strategy", "trust": "bounded_pass"}
        ]


class TestForbiddenDefectsAreRefuted:
    """The defects the prompt's design rules forbid still end in `refuted`."""

    def test_dcfl04_start_state_reading_b_without_a(self):
        # Rule 2: a start-state move on the second block's letter (live dcfl-04).
        bad = copy.deepcopy(_mock("dcfl-04"))
        bad["proof_sketch"]["dpda"]["transitions"].append(
            {"from": "q0", "read": "b", "top": "Z0", "to": "qc", "push": ["Z0"]}
        )
        result = _verify("dcfl-04", bad)
        assert result["verification_status"] == "refuted"
        assert any("'b'" in c for c in result["details"]["counterexamples"])

    def test_dcfl04_epsilon_accept_hop_sharing_state_with_letters(self):
        # Rule 1: an epsilon "accept" hop out of a state that also reads letters with the
        # same top is non-determinism unless it targets a dead-end accepting sink.
        bad = copy.deepcopy(_mock("dcfl-04"))
        dpda = bad["proof_sketch"]["dpda"]
        dpda["states"].append("qd")
        dpda["transitions"].append({"from": "qc", "read": None, "top": "Z0", "to": "qd", "push": ["Z0"]})
        dpda["transitions"].append({"from": "qd", "read": "c", "top": "Z0", "to": "qc", "push": ["Z0"]})
        result = _verify("dcfl-04", bad)
        assert result["verification_status"] == "refuted"
        assert result["details"]["determinism"] == "violated"

    def test_dcfl15_accepting_before_the_block_is_matched(self):
        # Accepting as soon as the witness `a` is read ignores |u₁| <= |u₂|: "ba" is accepted.
        bad = copy.deepcopy(_mock("dcfl-15"))
        bad["proof_sketch"]["dpda"]["accept_states"].append("qpop")
        result = _verify("dcfl-15", bad)
        assert result["verification_status"] == "refuted"
        assert any("'ba'" in c for c in result["details"]["counterexamples"])


# ---------------------------------------------------------------------------
# The prompt itself
# ---------------------------------------------------------------------------

_EXAMPLE_RE = re.compile(r"^(\$a+b+c+|da+b+c+)$")


def _example_member(w: str) -> bool:
    """{$aⁿbⁿcᵐ | n,m >= 1} ∪ {d aᵐbⁿcⁿ | m,n >= 1} -- the prompt's worked example."""
    if not _EXAMPLE_RE.match(w):
        return False
    if w[0] == "$":
        return w.count("a") == w.count("b")
    return w.count("b") == w.count("c")


def _prompt_example() -> dict:
    text = PROMPT.read_text(encoding="utf-8")
    for block in re.findall(r"```json\n(.*?)\n```", text, re.S):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("agent_name") == "stack_strategy":
            return obj
    raise AssertionError("no worked stack_strategy example in the prompt")


class TestPromptContract:
    def test_key_principle_no_longer_forbids_the_table(self):
        text = PROMPT.read_text(encoding="utf-8")
        assert "Do NOT build a formal DPDA transition table" not in text
        assert "### Design rules" in text

    def test_worked_example_dpda_is_correct_without_epsilon(self):
        dpda = _prompt_example()["proof_sketch"]["dpda"]
        assert check_determinism(dpda) == []
        assert all(_normalize_read(t["read"]) is not None for t in dpda["transitions"])
        mismatches = [
            w for w in _words("$dabc", 6) if dpda_run(dpda, w) != _example_member(w)
        ]
        assert mismatches == []

    def test_worked_example_self_check_line_is_true(self):
        example = _prompt_example()
        dpda = example["proof_sketch"]["dpda"]
        assert not any(dpda_run(dpda, w) for w in _words("$dabc", 3))
        line = next(e for e in example["evidence"] if e.startswith("Самопроверка"))
        before, after = line.split("; ", 1)[1].split("приняты", 1)
        accepted = re.findall(r"[$d][abc]+", before)
        rejected = re.findall(r"[$d][abc]+", after.split("отвергнуты")[0])
        assert accepted and rejected
        assert all(dpda_run(dpda, w) and _example_member(w) for w in accepted)
        assert not any(dpda_run(dpda, w) or _example_member(w) for w in rejected)
