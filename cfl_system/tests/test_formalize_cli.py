"""``python -m cfl_system.formalize`` -- the separate Lean formalization entry
over a finished CFL run (wrapper around agent_system/lib/formalize_run.py).

Mock only: scripted ``FakeAnthropic`` prover + scripted ``check_lean_file``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_system.lib import formalize_run as fr
from agent_system.lib.testing.fake_anthropic import FakeAnthropic
from agent_system.tests.test_formalize_run import CheckFake, body_turn, tc
from cfl_system import config, formalize as cfl_formalize
from cfl_system.orchestrator import MockRunner, run_pipeline

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"


@pytest.fixture
def spec():
    return cfl_formalize.build_spec()


@pytest.fixture
def check(monkeypatch):
    monkeypatch.setattr(fr, "is_docker_available", lambda: True)

    def install(*results):
        fake = CheckFake(list(results))
        monkeypatch.setattr(fr, "check_lean_file", fake)
        return fake
    return install


def make_run_dir(tmp_path: Path) -> Path:
    ir = json.loads((EXAMPLES / "eval" / "cfl-01.json").read_text(encoding="utf-8"))
    result = run_pipeline(ir, mock_runner=MockRunner(str(EXAMPLES / "mock"), "task_anbncn"))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "input.json").write_text(json.dumps(ir), encoding="utf-8")
    (run_dir / "input_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return run_dir


def load(run_dir: Path) -> dict:
    return json.loads((run_dir / "input_result.json").read_text(encoding="utf-8"))


def test_config_defaults(spec, monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)
    assert config.MODELS["lean_formalizer"] == OPUS
    assert config.MODELS["lean_formalizer_retry"] == SONNET
    assert config.EFFORT["lean_formalizer_retry"] and config.TEMPERATURES["lean_formalizer_retry"] == 0.0
    s = fr.FormalizeSettings.defaults(spec)
    assert (s.first_model, s.retry_model, s.retries, s.max_tokens) == (OPUS, SONNET, 2, 128000)
    assert spec.prompt_path.name == "cfl_lean_formalizer.md" and spec.prompt_path.is_file()
    assert "TflLean.not_isContextFree_of_slice" in spec.available_lemmas


def test_direction_plan_and_statement_from_the_stored_result(tmp_path, spec):
    run_dir = make_run_dir(tmp_path)
    result = load(run_dir)
    assert result["verdict"] == "non_cfl" and result["confidence"] < 0.98
    prep = fr.prepare(run_dir, spec)
    assert (prep.direction, prep.direction_source) == ("non_cfl", "verdict")
    assert "¬" in prep.snapshot["theorem_decl"] or "IsContextFree" in prep.snapshot["theorem_decl"]
    assert prep.plan["verdict"] == "non_cfl" and prep.plan.get("method")    # a specialist argued it
    assert prep.plan["specialist_evidence"]


def test_proved_same_direction_is_verified(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    fake = check(tc("error", [{"severity": "error", "data": "boom"}]), tc("proved", axioms=["propext"]))
    client = FakeAnthropic([body_turn("exact foo"), body_turn("exact h", model=SONNET)])
    summary = fr.run_formalization(run_dir, spec, client=client)

    assert summary["status"] == "proved" and summary["attempts"] == 2
    assert [c["model"] for c in client.stream_calls] == [OPUS, SONNET]
    assert client.stream_calls[0]["max_tokens"] == client.stream_calls[1]["max_tokens"] == 128000
    assert "output_config" in client.stream_calls[0]           # structured output requested
    after = load(run_dir)
    assert (after["verdict"], after["confidence"]) == ("non_cfl", 0.98)
    assert after["verdict_gate"]["contradiction"] is False
    assert any(b.get("basis") == "lean_proof" for b in after["verdict_gate"]["basis"])
    assert after["proof_verified"] is True
    assert after["formalization"]["status"] == "proved" and after["formalization"]["proof_body"] == "exact h"
    assert len(fake.calls) == 2
    md = (run_dir / "input_result.md").read_text(encoding="utf-8")
    html = (run_dir / "input_result.html").read_text(encoding="utf-8")
    assert "Lean 4 formalization" in md and "tfl-lean-formalization" in html


def test_proved_opposite_direction_flips_and_replaces_the_proof(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    check(tc("proved"))
    fr.run_formalization(run_dir, spec, client=FakeAnthropic([body_turn("exact h")]), direction="cfl")
    after = load(run_dir)
    assert (after["verdict"], after["confidence"]) == ("cfl", 0.98)
    assert after["proof"]["source"] == "lean_formalizer"
    assert after["proof"]["evidence"]["proof_body"] == "exact h"
    assert any("lean proof of 'cfl' overrides reasoning verdict 'non_cfl'" in n
               for n in after["verdict_gate"]["downgrades"])


def test_idempotent_and_forced_rerun_restores_the_baseline(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    before = load(run_dir)
    check(tc("proved"))
    fr.run_formalization(run_dir, spec, client=FakeAnthropic([body_turn("exact h")]), direction="cfl")
    snap = (run_dir / "input_result.json").read_bytes()
    again = fr.run_formalization(run_dir, spec, client=FakeAnthropic([]))
    assert again["skipped"] is True and (run_dir / "input_result.json").read_bytes() == snap

    fr.run_formalization(run_dir, spec, client=FakeAnthropic([body_turn("exact g")]),
                         direction="non_cfl", force=True)
    after = load(run_dir)
    assert after["verdict"] == "non_cfl" and after["proof"] == before["proof"]
    assert len([b for b in after["verdict_gate"]["basis"] if b.get("basis") == "lean_proof"]) == 1
    assert not any("'cfl'" in n for n in after["verdict_gate"]["downgrades"])


def test_failed_run_leaves_verdict_alone(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    before = load(run_dir)
    check(tc("error", [{"severity": "error", "data": "boom"}]))
    fr.run_formalization(run_dir, spec, client=FakeAnthropic(
        [body_turn("a"), body_turn("b", model=SONNET), body_turn("c", model=SONNET)]))
    after = load(run_dir)
    for key in ("verdict", "confidence", "verdict_gate", "proof", "proof_verified"):
        assert after[key] == before[key]
    assert after["formalization"]["status"] == "error"


def test_cli_estimate_and_mock(tmp_path, spec, check, capsys):
    run_dir = make_run_dir(tmp_path)
    assert fr.cli_main(spec, [str(run_dir), "--estimate"]) == 0
    est = json.loads(capsys.readouterr().out)
    assert est["formalizable"] and est["direction"] == "non_cfl" and est["max_usd"] > est["expected_usd"] > 0

    mock_dir = tmp_path / "mock"
    mock_dir.mkdir()
    (mock_dir / "input_lean_formalizer_output.json").write_text(
        json.dumps({"agent": "lean_formalizer", "proof_body": "exact h"}), encoding="utf-8")
    check(tc("proved"))
    assert fr.cli_main(spec, [str(run_dir), "--mock", str(mock_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "proved"
    assert load(run_dir)["confidence"] == 0.98
