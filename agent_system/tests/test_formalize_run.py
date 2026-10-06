"""Separate Lean formalization entry (agent_system/lib/formalize_run.py +
``python -m agent_system.formalize``), REG side and the shared loop.

Mock only: the prover is a scripted ``FakeAnthropic`` (or a ``MockSource``),
``check_lean_file`` is a scripted fake -- no API call and no Docker. The CFL /
DCFL wrappers have their own files (cfl_system/tests/test_formalize_cli.py,
dcfl_system/tests/test_formalize_cli.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_system import formalize as reg_formalize
from agent_system.lib import formalize_run as fr
from agent_system.lib.claim_verifier import CONFIDENCE_CAPS
from agent_system.lib.progress import read_events, read_partial
from agent_system.lib.testing.fake_anthropic import FakeAnthropic, raises_turn, text_turn
from agent_system.orchestrator import MockRunner, Pipeline, _result_verdict

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TASK = "task2_grammar_sasb"          # mock run: non_regular, 0.55, formalizable both ways
NOT_FORMALIZABLE_TASK = "task3_regex_backref"

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_run_dir(tmp_path: Path, task: str = TASK) -> Path:
    ir = json.loads((EXAMPLES / f"{task}.json").read_text(encoding="utf-8"))
    result = Pipeline().run_full_pipeline(ir, mock_runner=MockRunner(EXAMPLES, task))
    result.setdefault("verdict", _result_verdict(result))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "input.json").write_text(json.dumps(ir), encoding="utf-8")
    (run_dir / "input_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "input_result.md").write_text("# old\n", encoding="utf-8")
    (run_dir / "input_result.html").write_text("<html><body>old</body></html>", encoding="utf-8")
    return run_dir


def load_result(run_dir: Path) -> dict:
    return json.loads((run_dir / "input_result.json").read_text(encoding="utf-8"))


def body_turn(body: str = "intro h\n  exact h", *, model: str = OPUS, **kw):
    return text_turn(json.dumps({"proof_body": body, "lemmas_used": [], "notes": ""}),
                     model=model, **kw)


def tc(status: str, errors=None, axioms=None) -> dict:
    return {"status": status, "errors": errors or [], "axioms": axioms or [], "elapsed": 1.0,
            "warnings": [], "message": ""}


class CheckFake:
    def __init__(self, results: list[dict]):
        self.results = list(results)
        self.calls: list[dict] = []

    def __call__(self, text, timeout=None, theorem_name=None):
        self.calls.append({"text": text, "timeout": timeout, "theorem_name": theorem_name})
        return self.results[min(len(self.calls), len(self.results)) - 1]


@pytest.fixture
def docker(monkeypatch):
    monkeypatch.setattr(fr, "is_docker_available", lambda: True)


@pytest.fixture
def check(monkeypatch, docker):
    def install(*results: dict) -> CheckFake:
        fake = CheckFake(list(results))
        monkeypatch.setattr(fr, "check_lean_file", fake)
        return fake
    return install


@pytest.fixture
def spec():
    return reg_formalize.build_spec()


def run(run_dir, spec, turns, **kw):
    client = FakeAnthropic(turns)
    summary = fr.run_formalization(run_dir, spec, client=client, **kw)
    return summary, client


def user_json(call: dict) -> dict:
    return json.loads(call["messages"][0]["content"])


# ---------------------------------------------------------------------------
# defaults / config
# ---------------------------------------------------------------------------

def test_defaults_come_from_config(spec, monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)
    s = fr.FormalizeSettings.defaults(spec)
    assert (s.first_model, s.retry_model) == (OPUS, SONNET)
    assert (s.retries, s.max_tokens) == (2, 128000)


def test_model_override_env_forces_both_and_clamps_haiku(spec, monkeypatch):
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "claude-haiku-4-5")
    s = fr.FormalizeSettings.defaults(spec)
    assert s.first_model == s.retry_model == "claude-haiku-4-5"
    assert s.max_tokens_for("claude-haiku-4-5") == 64000
    assert s.max_tokens_for(OPUS) == 128000


# ---------------------------------------------------------------------------
# the model loop
# ---------------------------------------------------------------------------

def test_attempt_models_and_correction_inputs(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    fake = check(tc("error", [{"severity": "error", "data": "unknown identifier 'foo'"}]),
                 tc("error", [{"severity": "error", "data": "type mismatch"}]),
                 tc("proved", axioms=["propext"]))
    summary, client = run(run_dir, spec, [
        body_turn("exact foo"),
        body_turn("exact bar", model=SONNET),
        body_turn("exact baz", model=SONNET),
    ])

    assert summary["status"] == "proved" and summary["attempts"] == 3
    models = [c["model"] for c in client.stream_calls]
    assert models == [OPUS, SONNET, SONNET]
    assert {c["max_tokens"] for c in client.stream_calls} == {128000}
    # first attempt: statement + plan; corrections: + the Lean errors and the previous body
    first, second, third = (user_json(c) for c in client.stream_calls)
    assert "errors" not in first and first["statement"]["name"] == "tfl_main"
    assert second["errors"][0]["data"] == "unknown identifier 'foo'"
    assert second["previous_proof_body"] == "exact foo"
    assert third["errors"][0]["data"] == "type mismatch" and third["previous_proof_body"] == "exact bar"
    assert first["statement"] == second["statement"] == third["statement"]
    assert len(fake.calls) == 3 and fake.calls[0]["theorem_name"] == "tfl_main"

    block = load_result(run_dir)["evidence"]["formalization"]
    assert block["status"] == "proved" and block["proof_body"] == "exact baz"
    assert [a["model"] for a in block["attempts"]] == [OPUS, SONNET, SONNET]
    assert block["models"] == {"first": OPUS, "retry": SONNET}
    assert block["errors"] == []


def test_all_attempts_fail_changes_nothing_but_the_block(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    before = load_result(run_dir)
    check(tc("error", [{"severity": "error", "data": "boom"}]))
    summary, client = run(run_dir, spec, [body_turn("a"), body_turn("b", model=SONNET),
                                          body_turn("c", model=SONNET)])
    assert summary["status"] == "error" and len(client.stream_calls) == 3   # 1 + 2 corrections

    after = load_result(run_dir)
    assert after["evidence"]["formalization"]["status"] == "error"
    for key in ("status", "confidence", "verdict", "verdict_gate"):
        assert after[key] == before[key]
    assert after["evidence"]["verdict_gate"] == before["evidence"]["verdict_gate"]


def test_retries_setting_bounds_the_calls(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    check(tc("error", [{"severity": "error", "data": "boom"}]))
    settings = fr.FormalizeSettings(OPUS, SONNET, retries=0, max_tokens=1000)
    summary, client = run(run_dir, spec, [body_turn("a")], settings=settings)
    assert len(client.stream_calls) == 1 and client.stream_calls[0]["max_tokens"] == 1000


@pytest.mark.parametrize("status", ["has_sorry", "timeout", "unavailable"])
def test_terminal_lean_statuses_stop_the_loop(tmp_path, spec, check, status):
    run_dir = make_run_dir(tmp_path)
    check(tc(status))
    summary, client = run(run_dir, spec, [body_turn("a")])
    assert summary["status"] == status and len(client.stream_calls) == 1


def test_gave_up_stops_at_once(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    fake = check(tc("proved"))
    turn = text_turn(json.dumps({"proof_body": "", "lemmas_used": [], "notes": "no route"}), model=OPUS)
    summary, client = run(run_dir, spec, [turn])
    assert summary["status"] == "error" and len(client.stream_calls) == 1 and not fake.calls
    block = load_result(run_dir)["evidence"]["formalization"]
    assert "no route" in block["errors"][0] and block["attempts"][0]["status"] == "gave_up"


def test_malformed_reply_is_retried_on_the_correction_model(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    check(tc("proved"))
    summary, client = run(run_dir, spec, [text_turn("sorry, here is prose", model=OPUS),
                                          body_turn("exact h", model=SONNET)])
    assert summary["status"] == "proved"
    assert [c["model"] for c in client.stream_calls] == [OPUS, SONNET]


def test_api_error_ends_the_run_with_an_error_block(tmp_path, spec, check):
    from agent_system.lib.testing.fake_anthropic import fatal_error
    run_dir = make_run_dir(tmp_path)
    fake = check(tc("proved"))
    summary, client = run(run_dir, spec, [raises_turn(fatal_error(400, "prompt too long"))])
    assert summary["status"] == "error" and not fake.calls
    block = load_result(run_dir)["evidence"]["formalization"]
    assert "prompt too long" in block["errors"][0]


# ---------------------------------------------------------------------------
# truncation
# ---------------------------------------------------------------------------

def test_truncated_without_body_gets_one_sonnet_body_only_attempt(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    fake = check(tc("proved"))
    summary, client = run(run_dir, spec, [
        text_turn('{"proof_body": "intro h\\n  have := ', stop_reason="max_tokens", model=OPUS),
        body_turn("exact h", model=SONNET),
    ])
    assert summary["status"] == "proved" and len(client.stream_calls) == 2
    assert [c["model"] for c in client.stream_calls] == [OPUS, SONNET]
    body_only = user_json(client.stream_calls[1])
    assert "Output ONLY the proof body" in body_only["instruction"]
    assert "instruction" not in user_json(client.stream_calls[0])
    assert len(fake.calls) == 1
    atts = load_result(run_dir)["evidence"]["formalization"]["attempts"]
    assert [a["status"] for a in atts] == ["truncated", "proved"]
    assert [a["role"] for a in atts] == ["first", "body_only"]


def test_body_only_attempt_failing_stops_although_corrections_remain(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    fake = check(tc("error", [{"severity": "error", "data": "boom"}]))
    summary, client = run(run_dir, spec, [
        text_turn('{"proof_body": "cut', stop_reason="max_tokens", model=OPUS),
        body_turn("exact nope", model=SONNET),
        body_turn("never asked", model=SONNET),
    ])
    assert summary["status"] == "error"
    assert len(client.stream_calls) == 2 and len(fake.calls) == 1


def test_body_only_attempt_truncated_again_stops(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    fake = check(tc("proved"))
    summary, client = run(run_dir, spec, [
        text_turn('{"proof_body": "cut', stop_reason="max_tokens", model=OPUS),
        text_turn('{"proof_body": "cut again', stop_reason="max_tokens", model=SONNET),
        body_turn("never asked", model=SONNET),
    ])
    assert summary["status"] == "error" and len(client.stream_calls) == 2 and not fake.calls


def test_truncated_reply_with_a_closed_body_is_used(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    fake = check(tc("proved"))
    summary, client = run(run_dir, spec, [
        text_turn('{"proof_body": "exact h", "lemmas_used": [], "notes": "long expl',
                  stop_reason="max_tokens", model=OPUS),
    ])
    assert summary["status"] == "proved" and len(client.stream_calls) == 1
    assert fake.calls and "exact h" in fake.calls[0]["text"]


def test_parse_proposal_text_never_returns_a_cut_body():
    assert fr.parse_proposal_text('{"proof_body": "abc')[0] is None
    assert fr.parse_proposal_text('{"proof_body": "a\\nb", "notes": "x"}')[0] == "a\nb"
    assert fr.parse_proposal_text('{"proof_body": "  "}')[:2] == ("  ", True)


# ---------------------------------------------------------------------------
# result update / R-Lean gate
# ---------------------------------------------------------------------------

def test_proved_updates_result_json_md_html(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    before = load_result(run_dir)
    assert before["confidence"] < 0.98
    check(tc("proved", axioms=["propext"]))
    summary, _ = run(run_dir, spec, [body_turn("exact h")])

    after = load_result(run_dir)
    assert (after["status"], after["confidence"]) == ("success", CONFIDENCE_CAPS["verified"])
    assert after["verdict"] == "non_regular"        # proven direction == standing verdict
    gate = after["verdict_gate"]
    lean_basis = [b for b in gate["basis"] if b.get("basis") == "lean_proof"]
    assert len(lean_basis) == 1 and gate["contradiction"] is False
    assert after["evidence"]["verdict_gate"]["confidence_cap"] == CONFIDENCE_CAPS["verified"]
    assert summary["verdict"] == "non_regular" and summary["confidence"] == 0.98
    assert summary["files"] == ["input_result.json", "input_result.md", "input_result.html"]
    md = (run_dir / "input_result.md").read_text(encoding="utf-8")
    html = (run_dir / "input_result.html").read_text(encoding="utf-8")
    assert "Lean 4 formalization" in md and "exact h" in md
    assert "tfl-lean-formalization" in html and html.rstrip().endswith("</html>")
    assert not list(run_dir.glob("*.tmp"))


def test_proved_opposite_direction_flips_the_verdict(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    res = load_result(run_dir)
    res["evidence"]["reasoning"] = {"evidence": {"verdict": "non_regular", "confidence": 0.9}}
    (run_dir / "input_result.json").write_text(json.dumps(res), encoding="utf-8")
    check(tc("proved"))
    run(run_dir, spec, [body_turn("exact h")], direction="regular")
    after = load_result(run_dir)
    assert after["verdict"] == "regular" and after["confidence"] == 0.98
    assert after["evidence"]["reasoning"]["verdict"] == "regular"
    notes = after["verdict_gate"]["downgrades"]
    assert any("lean proof of 'regular' overrides reasoning verdict 'non_regular'" in n for n in notes)
    assert after["evidence"]["formalization"]["direction_source"] == "cli"


@pytest.mark.parametrize("with_contradiction", [False, True])
def test_gate_matches_assemble_result_node(spec, with_contradiction):
    """The separate entry's gate agrees with graph.assemble_result_node's."""
    from agent_system.graph import assemble_result_node
    from agent_system.tests.test_verdict_policy import _state

    reasoning_verdict = "regular"
    evidence = {}
    if with_contradiction:
        evidence = {
            "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
            "pumping_verification": {"trust": "well_formed", "reason": "no oracle"},
        }

    def build(formalization):
        state = _state(
            test_result={"status": "pass", "tested": 200},
            formalization=formalization,
            evidence=dict(evidence),
            reasoning_output={"evidence": {"verdict": reasoning_verdict, "confidence": 0.99}},
        )
        return assemble_result_node(state)["result"]

    block = {"status": "proved", "direction": "non_regular", "axioms": []}
    expected = build(block)
    plain = json.loads(json.dumps(build(None)))
    # what the reasoning node stores in a real run (assemble_result_node alone does not)
    plain["evidence"]["reasoning"] = {"evidence": {"verdict": reasoning_verdict, "confidence": 0.99}}
    assert plain["verdict_gate"]["contradiction"] is with_contradiction
    assert spec.apply_gate(plain, dict(block)) is True

    assert (plain["status"], plain["confidence"]) == (expected["status"], expected["confidence"])
    assert plain["verdict_gate"]["contradiction"] == expected["verdict_gate"]["contradiction"]
    assert plain["verdict_gate"]["confidence_cap"] == expected["verdict_gate"]["confidence_cap"]
    assert plain["verdict_gate"]["basis"][-1] == expected["verdict_gate"]["basis"][-1]
    assert (plain["verdict_gate"]["downgrades"][-1:] == expected["verdict_gate"]["downgrades"][-1:])
    assert plain["evidence"]["reasoning"]["verdict"] == expected["evidence"]["reasoning"]["verdict"]


def test_proof_without_reasoning_verdict_leaves_verdict_as_the_graph_does(tmp_path, spec, check):
    """No reasoning verdict: assemble_result_node does not flip anything, so
    neither does the separate entry (one shared gate, lib/reg_lean_gate.py)."""
    run_dir = make_run_dir(tmp_path)
    check(tc("proved"))
    run(run_dir, spec, [body_turn("exact h")], direction="regular")
    after = load_result(run_dir)
    assert (after["status"], after["confidence"]) == ("success", 0.98)
    assert after["verdict"] == "non_regular"
    assert after["verdict_gate"]["downgrades"] == [] or not any(
        "overrides" in n for n in after["verdict_gate"]["downgrades"])


def test_graph_and_formalize_share_one_gate():
    """Both entries go through lib.reg_lean_gate (no second copy of the rules)."""
    import inspect
    from agent_system import formalize, graph
    from agent_system.lib import reg_lean_gate
    assert graph._set_reasoning_verdict is reg_lean_gate.set_reasoning_verdict
    assert "lean_gate_decision" in inspect.getsource(graph.assemble_result_node)
    assert "lean_gate_decision" in inspect.getsource(formalize.apply_gate)
    assert "compute_lean_proof_trust" not in inspect.getsource(formalize.apply_gate)


@pytest.mark.parametrize("prior,direction,expect_verdict,expect_note", [
    ("regular", "non_regular", "non_regular", True),      # opposite: flips
    ("non_regular", "non_regular", "non_regular", False),  # confirming
    ("regular", "regular", "regular", False),
    (None, "non_regular", None, False),                   # no reasoning verdict: untouched
])
def test_gate_equivalence_with_assemble_result_node_verdicts(spec, prior, direction,
                                                             expect_verdict, expect_note):
    from agent_system.graph import assemble_result_node
    from agent_system.tests.test_verdict_policy import _state

    ro = {"evidence": {"verdict": prior, "confidence": 0.7}} if prior else None
    block = {"status": "proved", "direction": direction, "axioms": []}

    def build(f):
        return assemble_result_node(_state(
            test_result={"status": "pass", "tested": 200}, formalization=f,
            evidence={"reasoning": json.loads(json.dumps(ro))} if ro else {},
            reasoning_output=ro))["result"]

    expected = build(block)
    plain = json.loads(json.dumps(build(None)))
    if ro:
        plain["evidence"]["reasoning"] = json.loads(json.dumps(ro))
    assert spec.apply_gate(plain, dict(block)) is True
    for key in ("status", "confidence"):
        assert plain[key] == expected[key]
    g, e = plain["verdict_gate"], expected["verdict_gate"]
    assert (g["contradiction"], g["confidence_cap"]) == (e["contradiction"], e["confidence_cap"])
    assert g["basis"][-1] == e["basis"][-1]
    assert [d for d in g["downgrades"] if "overrides" in d] ==         [d for d in e["downgrades"] if "overrides" in d]
    assert bool([d for d in g["downgrades"] if "overrides" in d]) is expect_note
    rv = lambda r: ((r["evidence"].get("reasoning") or {}).get("evidence") or
                    (r["evidence"].get("reasoning") or {})).get("verdict")
    assert rv(plain) == rv(expected)
    from agent_system.orchestrator import _result_verdict
    assert _result_verdict(plain) == _result_verdict(expected)


def test_gate_confirming_proof_adds_no_override_note(spec):
    result = {"status": "success", "confidence": 0.85, "verdict": "non_regular",
              "evidence": {"reasoning": {"evidence": {"verdict": "non_regular"}},
                           "verdict_gate": {"basis": [], "contradiction": False, "downgrades": []}}}
    assert spec.apply_gate(result, {"status": "proved", "direction": "non_regular"}) is True
    assert result["confidence"] == 0.98 and result["verdict_gate"]["downgrades"] == []
    assert result["evidence"]["reasoning"]["evidence"]["verdict"] == "non_regular"


def test_gate_ignores_every_status_but_proved(spec):
    for status in ("has_sorry", "error", "timeout", "unavailable", "not_formalizable"):
        result = {"status": "partial", "confidence": 0.4, "evidence": {}}
        assert spec.apply_gate(result, {"status": status, "direction": "regular"}) is False
        assert result == {"status": "partial", "confidence": 0.4, "evidence": {}}


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------

def test_second_run_over_a_proved_result_is_a_no_op(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    check(tc("proved"))
    run(run_dir, spec, [body_turn("exact h")])
    snapshot = {p.name: p.read_bytes() for p in run_dir.iterdir()}

    summary, client = run(run_dir, spec, [])            # any call would raise: no scripted turn
    assert summary["skipped"] is True and summary["status"] == "proved"
    assert client.stream_calls == []
    assert {p.name: p.read_bytes() for p in run_dir.iterdir()} == snapshot


def test_forced_rerun_does_not_stack_lean_basis_entries(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    check(tc("proved"))
    run(run_dir, spec, [body_turn("exact h")], direction="regular")      # flips to regular
    run(run_dir, spec, [body_turn("exact g")], direction="non_regular", force=True)
    after = load_result(run_dir)
    gate = after["verdict_gate"]
    assert after["verdict"] == "non_regular" and after["confidence"] == 0.98
    assert len([b for b in gate["basis"] if b.get("basis") == "lean_proof"]) == 1
    assert not any("'regular'" in n and "lean proof" in n for n in gate["downgrades"])
    assert after["evidence"]["formalization"]["proof_body"] == "exact g"


@pytest.mark.parametrize("second", ["has_sorry", "error", "unavailable", "truncated"])
def test_forced_rerun_that_does_not_prove_keeps_the_proved_block(tmp_path, spec, check,
                                                                 monkeypatch, second):
    """A forced re-run over a proved block never replaces it with a non-proved
    one: result.json/md/html stay byte-identical, verdict and 0.98 survive."""
    run_dir = make_run_dir(tmp_path)
    check(tc("proved"))
    run(run_dir, spec, [body_turn("exact h")])
    snapshot = {n: (run_dir / n).read_bytes()
                for n in ("input_result.json", "input_result.md", "input_result.html")}
    proved = load_result(run_dir)
    assert proved["confidence"] == 0.98

    if second == "unavailable":
        monkeypatch.setattr(fr, "is_docker_available", lambda: False)
        turns: list = []
    elif second == "truncated":
        turns = [text_turn("partial reasoning", model=OPUS, stop_reason="max_tokens"),
                 text_turn("still partial", model=SONNET, stop_reason="max_tokens")]
    else:
        check(tc(second, [{"severity": "error", "data": "boom"}]))
        turns = [body_turn("a"), body_turn("b", model=SONNET), body_turn("c", model=SONNET)]
    summary, _client = run(run_dir, spec, turns, force=True)

    assert summary["proved"] is True and summary["kept_proved"] is True
    assert summary["rerun_status"] != "proved"
    assert {n: (run_dir / n).read_bytes() for n in snapshot} == snapshot
    after = load_result(run_dir)
    assert after["confidence"] == 0.98 and after["verdict"] == proved["verdict"]
    assert after["evidence"]["formalization"]["status"] == "proved"
    evs = [e for e in read_events(run_dir) if e["event"] == "formalization"]
    assert evs[-1]["payload"]["kept_proved"] is True and evs[-1]["payload"]["status"] == "proved"
    assert read_partial(run_dir)["state"]["formalization"]["status"] == "proved"


def test_failed_block_is_rerun_and_replaced(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    before = load_result(run_dir)
    check(tc("error", [{"severity": "error", "data": "boom"}]))
    run(run_dir, spec, [body_turn("a"), body_turn("b", model=SONNET), body_turn("c", model=SONNET)])
    check(tc("proved"))
    summary, client = run(run_dir, spec, [body_turn("exact h")])
    assert summary["status"] == "proved" and len(client.stream_calls) == 1
    after = load_result(run_dir)
    assert after["evidence"]["formalization"]["status"] == "proved"
    assert len(after["evidence"]["formalization"]["attempts"]) == 1
    assert after["confidence"] == 0.98 and before["confidence"] != 0.98


# ---------------------------------------------------------------------------
# not formalizable / no Docker / no direction
# ---------------------------------------------------------------------------

def test_not_formalizable_makes_no_call(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path, NOT_FORMALIZABLE_TASK)
    fake = check(tc("proved"))
    summary, client = run(run_dir, spec, [])
    assert summary["status"] == "not_formalizable" and client.stream_calls == [] and not fake.calls
    block = load_result(run_dir)["evidence"]["formalization"]
    assert block["status"] == "not_formalizable" and "backreferences" in block["reason"]


def test_live_run_without_docker_never_calls_the_model(tmp_path, spec, monkeypatch):
    monkeypatch.setattr(fr, "is_docker_available", lambda: False)
    run_dir = make_run_dir(tmp_path)
    summary, client = run(run_dir, spec, [])
    assert summary["status"] == "unavailable" and client.stream_calls == []
    assert "Docker" in load_result(run_dir)["evidence"]["formalization"]["reason"]


def test_no_direction_at_all(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    result = load_result(run_dir)
    result["verdict"] = None
    result["evidence"]["reasoning"] = {}
    result["evidence"]["hypothesis"] = {}
    (run_dir / "input_result.json").write_text(json.dumps(result), encoding="utf-8")
    summary, client = run(run_dir, spec, [])
    assert summary["status"] == "not_formalizable" and client.stream_calls == []


def test_direction_falls_back_to_the_hypothesis_when_the_verdict_was_cleared(tmp_path, spec):
    run_dir = make_run_dir(tmp_path)
    result = load_result(run_dir)
    result["verdict"] = None
    result["evidence"]["reasoning"] = {}
    result["evidence"]["hypothesis"]["hypothesis"] = "non_regular"
    (run_dir / "input_result.json").write_text(json.dumps(result), encoding="utf-8")
    prep = fr.prepare(run_dir, spec)
    assert prep.direction in ("regular", "non_regular") and prep.direction_source == "hypothesis"


def test_unknown_nested_hint_is_not_a_formalization_direction(tmp_path, spec):
    run_dir = make_run_dir(tmp_path)
    result = load_result(run_dir)
    result["verdict"] = None
    result["evidence"]["reasoning"] = {}
    assert result["evidence"]["hypothesis"]["hypothesis"] == "unknown"
    (run_dir / "input_result.json").write_text(json.dumps(result), encoding="utf-8")
    prep = fr.prepare(run_dir, spec)
    assert prep.direction is None and prep.direction_source == "none"


def test_missing_input_json_is_an_error(tmp_path, spec):
    run_dir = make_run_dir(tmp_path)
    (run_dir / "input.json").unlink()
    with pytest.raises(FileNotFoundError):
        fr.prepare(run_dir, spec)


# ---------------------------------------------------------------------------
# progress / cost
# ---------------------------------------------------------------------------

def test_progress_events_and_running_cost(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    # a finished run's progress log: the cumulative cost continues from it
    (run_dir / "progress.jsonl").write_text(json.dumps({
        "ts": "2026-09-29T00:00:00+00:00", "event": "done", "node": "", "payload": {},
        "usage": {"calls": 5, "input_tokens": 1000, "output_tokens": 500, "estimated_cost_usd": 0.25},
    }) + "\n", encoding="utf-8")
    check(tc("error", [{"severity": "error", "data": "boom"}]), tc("proved"))
    summary, _ = run(run_dir, spec, [
        body_turn("a", input_tokens=1000, output_tokens=1000),
        body_turn("b", model=SONNET, input_tokens=2000, output_tokens=2000),
    ])

    events = read_events(run_dir)[1:]                     # skip the seeded line
    kinds = [e["event"] for e in events]
    assert kinds[0] == "node_start" and events[0]["node"] == "formalize"
    assert kinds[-1] == "done" and "formalization" in kinds and "verdict" in kinds
    starts = [e for e in events if e["event"] == "llm_call" and e["payload"].get("phase") == "start"]
    dones = [e for e in events if e["event"] == "llm_call" and e["payload"].get("phase") == "done"]
    assert [s["payload"]["model"] for s in starts] == [OPUS, SONNET]
    assert [d["payload"]["model"] for d in dones] == [OPUS, SONNET]
    # running total after each call, continued from the run's own 5 calls / $0.25
    opus_cost = (1000 * 4.0 + 1000 * 20.0) / 1e6
    sonnet_cost = (2000 * 2.0 + 2000 * 10.0) / 1e6
    assert dones[0]["usage"]["calls"] == 6
    assert dones[0]["usage"]["estimated_cost_usd"] == pytest.approx(0.25 + opus_cost, abs=1e-6)
    assert dones[1]["usage"]["estimated_cost_usd"] == pytest.approx(0.25 + opus_cost + sonnet_cost, abs=1e-6)
    assert events[-1]["usage"]["calls"] == 7

    block = load_result(run_dir)["evidence"]["formalization"]
    assert block["usage"]["calls"] == 2
    assert block["usage"]["estimated_cost_usd"] == pytest.approx(opus_cost + sonnet_cost, abs=1e-6)
    assert [a["cost_usd"] for a in block["attempts"]] == pytest.approx([opus_cost, sonnet_cost])

    partial = read_partial(run_dir)
    assert partial["status"] == "done" and partial["result"]["confidence"] == 0.98
    assert partial["state"]["formalization"]["status"] == "proved"       # not left at "running"
    fz = [e for e in events if e["event"] == "formalization"][0]
    assert fz["payload"]["status"] == "proved" and fz["payload"]["attempts"] == 2
    assert fz["usage"]["estimated_cost_usd"] == pytest.approx(0.25 + opus_cost + sonnet_cost, abs=1e-6)


def test_partial_snapshot_shows_each_attempt_while_running(tmp_path, spec, monkeypatch, docker):
    run_dir = make_run_dir(tmp_path)
    seen: list[list[str]] = []

    def check_and_peek(text, timeout=None, theorem_name=None):
        partial = read_partial(run_dir) or {}
        block = (partial.get("state") or {}).get("formalization") or {}
        seen.append([a["status"] for a in block.get("attempts", [])])
        return tc("error", [{"severity": "error", "data": "boom"}])

    monkeypatch.setattr(fr, "check_lean_file", check_and_peek)
    run(run_dir, spec, [body_turn("a"), body_turn("b", model=SONNET), body_turn("c", model=SONNET)])
    assert seen == [[], ["error"], ["error", "error"]]
    final = read_partial(run_dir)["state"]["formalization"]
    assert [a["status"] for a in final["attempts"]] == ["error"] * 3


def test_no_progress_flag_writes_no_progress_files(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    check(tc("proved"))
    run(run_dir, spec, [body_turn("exact h")], progress=False)
    assert not (run_dir / "progress.jsonl").exists() and not (run_dir / "partial_result.json").exists()


def test_error_event_when_the_run_blows_up(tmp_path, spec, monkeypatch, docker):
    from agent_system.lib.testing.fake_anthropic import fatal_error
    run_dir = make_run_dir(tmp_path)
    before = (run_dir / "input_result.json").read_bytes()
    with pytest.raises(Exception):
        run(run_dir, spec, [raises_turn(fatal_error(401, "bad key"))])
    events = read_events(run_dir)
    assert events[-1]["event"] == "error"
    assert (run_dir / "input_result.json").read_bytes() == before      # result untouched


# ---------------------------------------------------------------------------
# estimate / CLI
# ---------------------------------------------------------------------------

def test_estimate_is_free_and_bounded(tmp_path, spec, capsys):
    run_dir = make_run_dir(tmp_path)
    snapshot = {p.name: p.read_bytes() for p in run_dir.iterdir()}
    rc = fr.cli_main(spec, [str(run_dir), "--estimate"])
    est = json.loads(capsys.readouterr().out)
    assert rc == 0 and est["formalizable"] is True
    assert 0 < est["min_usd"] <= est["expected_usd"] <= est["max_usd"]
    assert est["calls_max"] == 4 and est["first_model"] == OPUS and est["retry_model"] == SONNET
    # hard ceiling: Opus at 128000 out + 3 Sonnet calls at 128000 out (+ input)
    assert est["max_usd"] >= 128000 * 20 / 1e6 + 3 * 128000 * 10 / 1e6
    assert {p.name: p.read_bytes() for p in run_dir.iterdir()} == snapshot


def test_estimate_not_formalizable(tmp_path, spec, capsys):
    run_dir = make_run_dir(tmp_path, NOT_FORMALIZABLE_TASK)
    assert fr.cli_main(spec, [str(run_dir), "--estimate"]) == 0
    est = json.loads(capsys.readouterr().out)
    assert est["formalizable"] is False and est["max_usd"] == 0.0 and est["reason"]


def test_cli_needs_a_mode(tmp_path, spec, capsys):
    run_dir = make_run_dir(tmp_path)
    assert fr.cli_main(spec, [str(run_dir)]) == 2
    assert "--live" in capsys.readouterr().err


def test_cli_missing_files_exit_1(tmp_path, spec, capsys):
    assert fr.cli_main(spec, [str(tmp_path), "--mock", str(tmp_path)]) == 1


def test_cli_mock_source_end_to_end(tmp_path, spec, check, capsys):
    run_dir = make_run_dir(tmp_path)
    mock_dir = tmp_path / "mock"
    mock_dir.mkdir()
    (mock_dir / "input_formalizer_output.json").write_text(json.dumps([
        {"proof_body": "exact nope"},
        {"proof_body": "exact h"},
    ]), encoding="utf-8")
    fake = check(tc("error", [{"severity": "error", "data": "boom"}]), tc("proved"))
    rc = fr.cli_main(spec, [str(run_dir), "--mock", str(mock_dir)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["status"] == "proved" and out["attempts"] == 2 and len(fake.calls) == 2
    block = load_result(run_dir)["evidence"]["formalization"]
    assert block["usage"]["calls"] == 0 and block["usage"]["estimated_cost_usd"] == 0.0


def test_cli_mock_truncation_script(tmp_path, spec, check, capsys):
    run_dir = make_run_dir(tmp_path)
    mock_dir = tmp_path / "mock"
    mock_dir.mkdir()
    (mock_dir / "formalizer_output.json").write_text(json.dumps([
        {"stop_reason": "max_tokens"},
        {"proof_body": "exact h"},
    ]), encoding="utf-8")
    check(tc("proved"))
    rc = fr.cli_main(spec, [str(run_dir), "--mock", str(mock_dir)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["status"] == "proved" and out["attempts"] == 2
    atts = load_result(run_dir)["evidence"]["formalization"]["attempts"]
    assert [a["role"] for a in atts] == ["first", "body_only"]


def test_import_direction_agent_system_never_imports_cfl_or_dcfl():
    text = Path(fr.__file__).read_text(encoding="utf-8")
    for pkg in ("cfl_system", "dcfl_system", "ll_system"):
        assert f"import {pkg}" not in text and f"from {pkg}" not in text


# ---------------------------------------------------------------------------
# proved block from a pipeline run (no "baseline") + atomic renders
# ---------------------------------------------------------------------------

def _pipeline_proved_run_dir(tmp_path, spec, check):
    """A run dir as an in-run formalization leaves it: proved block, no baseline."""
    run_dir = make_run_dir(tmp_path)
    check(tc("proved"))
    run(run_dir, spec, [body_turn("exact h")])
    res = load_result(run_dir)
    del res["evidence"]["formalization"]["baseline"]
    (run_dir / "input_result.json").write_text(json.dumps(res), encoding="utf-8")
    return run_dir


def test_proved_without_baseline_is_skipped_with_a_clear_reason_unless_forced(tmp_path, spec, check):
    run_dir = _pipeline_proved_run_dir(tmp_path, spec, check)
    summary, client = run(run_dir, spec, [])
    assert summary["skipped"] is True and client.stream_calls == []
    assert "--force" in summary["reason"] and "already proved" in summary["reason"]

    check(tc("proved"))
    summary, client = run(run_dir, spec, [body_turn("exact g")], force=True)
    assert summary["skipped"] is False and summary["proved"] is True
    assert len(client.stream_calls) == 1                       # the re-run really happened
    after = load_result(run_dir)
    assert after["evidence"]["formalization"]["proof_body"] == "exact g"
    assert len([b for b in after["verdict_gate"]["basis"] if b.get("basis") == "lean_proof"]) == 1


def test_forced_rerun_without_baseline_that_fails_keeps_the_proof(tmp_path, spec, check):
    run_dir = _pipeline_proved_run_dir(tmp_path, spec, check)
    before = (run_dir / "input_result.json").read_bytes()
    check(tc("error", [{"severity": "error", "data": "boom"}]))
    turns = [body_turn("a"), body_turn("b", model=SONNET), body_turn("c", model=SONNET)]
    summary, _ = run(run_dir, spec, turns, force=True)
    assert summary["kept_proved"] is True
    assert (run_dir / "input_result.json").read_bytes() == before


@pytest.mark.parametrize("fmt", ["md", "html"])
def test_render_failure_midway_leaves_the_previous_file_intact(tmp_path, spec, check, monkeypatch, fmt):
    """The renderer writes a half file and dies: the existing md/html is not
    touched (rendering goes to a scratch file, replaced in one step)."""
    run_dir = make_run_dir(tmp_path)
    target = run_dir / f"input_result.{fmt}"
    old = target.read_bytes()
    real = spec.render_file

    def broken(result, path, f):
        if f == fmt:
            Path(path).write_text("HALF-WRITTEN", encoding="utf-8")
            raise RuntimeError("cancelled mid-write")
        real(result, path, f)

    monkeypatch.setattr(spec, "render_file", broken)
    check(tc("proved"))
    run(run_dir, spec, [body_turn("exact h")])
    assert target.read_bytes() == old
    assert not list(run_dir.glob("*.tmp"))
    other = "html" if fmt == "md" else "md"
    assert "Lean" in (run_dir / f"input_result.{other}").read_text(encoding="utf-8")
