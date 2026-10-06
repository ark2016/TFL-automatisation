"""``python -m dcfl_system.formalize`` -- the separate Lean formalization entry
over a finished DCFL run (wrapper around agent_system/lib/formalize_run.py).

Mock only: scripted ``FakeAnthropic`` prover + scripted ``check_lean_file``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_system.lib import formalize_run as fr
from agent_system.lib.testing.fake_anthropic import FakeAnthropic
from agent_system.tests.test_formalize_run import CheckFake, body_turn, tc
from dcfl_system import config, formalize as dcfl_formalize
from dcfl_system.orchestrator import MockRunner, run_pipeline

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"


@pytest.fixture
def spec():
    return dcfl_formalize.build_spec()


@pytest.fixture
def check(monkeypatch):
    monkeypatch.setattr(fr, "is_docker_available", lambda: True)

    def install(*results):
        fake = CheckFake(list(results))
        monkeypatch.setattr(fr, "check_lean_file", fake)
        return fake
    return install


def make_run_dir(tmp_path: Path) -> Path:
    ir = json.loads((EXAMPLES / "task_anbncm.json").read_text(encoding="utf-8"))
    result = run_pipeline(ir, mock_runner=MockRunner(str(EXAMPLES / "mock"), ir["task_id"]))
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
    assert spec.prompt_path.name == "dcfl_lean_formalizer.md" and spec.prompt_path.is_file()


def test_direction_and_plan_from_the_stored_result(tmp_path, spec):
    run_dir = make_run_dir(tmp_path)
    result = load(run_dir)
    assert result["verdict"] == "dcfl" and result["confidence"] < 0.98
    prep = fr.prepare(run_dir, spec)
    assert (prep.direction, prep.direction_source) == ("dcfl", "verdict")
    assert prep.snapshot["theorem_decl"] and "Direction to prove: dcfl" in prep.plan


def test_hypothesis_is_the_fallback_direction(tmp_path, spec):
    run_dir = make_run_dir(tmp_path)
    result = load(run_dir)
    result["verdict"] = "inconclusive"
    result["hypothesis"] = {"prediction": "non_dcfl"}
    (run_dir / "input_result.json").write_text(json.dumps(result), encoding="utf-8")
    prep = fr.prepare(run_dir, spec)
    assert (prep.direction, prep.direction_source) == ("non_dcfl", "hypothesis")


def test_proved_models_and_gate(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    fake = check(tc("error", [{"severity": "error", "data": "boom"}]), tc("proved", axioms=["propext"]))
    client = FakeAnthropic([body_turn("exact foo"), body_turn("exact h", model=SONNET)])
    summary = fr.run_formalization(run_dir, spec, client=client)

    assert summary["status"] == "proved" and [c["model"] for c in client.stream_calls] == [OPUS, SONNET]
    assert {c["max_tokens"] for c in client.stream_calls} == {128000}
    after = load(run_dir)
    assert (after["verdict"], after["confidence"]) == ("dcfl", 0.98)
    gate = after["verdict_gate"]
    assert gate["contradiction"] is False and gate["confidence_cap"] == 0.98
    assert any(b.get("basis") == "lean_proof" for b in gate["basis"])
    assert after["formalization"]["proof_body"] == "exact h" and len(fake.calls) == 2
    assert "Lean 4 formalization" in (run_dir / "input_result.md").read_text(encoding="utf-8")


def test_flip_then_forced_rerun_restores_the_baseline(tmp_path, spec, check):
    run_dir = make_run_dir(tmp_path)
    before = load(run_dir)
    before["proof_text"] = "OLD_PROOF_CLAIMS_DCFL"
    before["proof_sketch"] = {"argument": "OLD_SKETCH_CLAIMS_DCFL"}
    (run_dir / "input_result.json").write_text(json.dumps(before), encoding="utf-8")
    check(tc("proved"))
    fr.run_formalization(run_dir, spec, client=FakeAnthropic([body_turn("exact h")]), direction="non_dcfl")
    flipped = load(run_dir)
    assert flipped["verdict"] == "non_dcfl" and flipped["primary_evidence"] == "lean_formalizer"
    assert "overruled" in flipped["reasoning_summary"]
    assert flipped["proof_method"] == "lean_formalizer" and flipped["proof_sketch"] is None
    assert "exact h" in flipped["proof_text"]
    for fmt in ("md", "html"):
        rendered = (run_dir / f"input_result.{fmt}").read_text(encoding="utf-8")
        assert "OLD_PROOF_CLAIMS_DCFL" not in rendered
        assert "OLD_SKETCH_CLAIMS_DCFL" not in rendered
        assert "Lean-verified non_dcfl" in rendered and "exact h" in rendered

    again = fr.run_formalization(run_dir, spec, client=FakeAnthropic([]))
    assert again["skipped"] is True
    assert load(run_dir) == flipped

    fr.run_formalization(run_dir, spec, client=FakeAnthropic([body_turn("exact g")]),
                         direction="dcfl", force=True)
    after = load(run_dir)
    assert after["verdict"] == "dcfl" and after["primary_evidence"] == before["primary_evidence"]
    assert after["reasoning_summary"] == before["reasoning_summary"]
    for field in ("proof_method", "proof_sketch", "proof_text"):
        assert after[field] == before[field]
    assert len([b for b in after["verdict_gate"]["basis"] if b.get("basis") == "lean_proof"]) == 1
    assert not any("'non_dcfl'" in n for n in after["verdict_gate"]["downgrades"])


def test_truncation_then_body_only_attempt(tmp_path, spec, check):
    from agent_system.lib.testing.fake_anthropic import text_turn
    run_dir = make_run_dir(tmp_path)
    check(tc("proved"))
    client = FakeAnthropic([text_turn('{"proof_body": "cut', stop_reason="max_tokens", model=OPUS),
                            body_turn("exact h", model=SONNET)])
    summary = fr.run_formalization(run_dir, spec, client=client)
    assert summary["status"] == "proved" and len(client.stream_calls) == 2
    assert "Output ONLY the proof body" in json.loads(client.stream_calls[1]["messages"][0]["content"])["instruction"]


def test_legacy_baseline_gains_original_proof_fields(spec):
    result = {
        "verdict": "dcfl", "confidence": 0.85, "primary_evidence": "stack_strategy",
        "proof_method": "stack_strategy", "proof_text": "OLD_PROOF_CLAIMS_DCFL",
        "proof_sketch": {"argument": "OLD_SKETCH_CLAIMS_DCFL"},
        "reasoning_summary": "old summary", "verdict_gate": {},
    }
    original_proof = {key: result[key] for key in ("proof_method", "proof_text", "proof_sketch")}
    legacy_paths = tuple(key for key in spec.baseline_paths if key not in original_proof)
    block = {"status": "proved", "direction": "non_dcfl", "proof_body": "exact h",
             "baseline": fr.snapshot_baseline(result, legacy_paths)}

    assert dcfl_formalize.apply_gate(result, block)
    assert result["proof_method"] == "lean_formalizer"
    fr.restore_baseline(result, block["baseline"])

    assert result["verdict"] == "dcfl"
    assert {key: result[key] for key in original_proof} == original_proof


def test_cli_estimate(tmp_path, spec, capsys):
    run_dir = make_run_dir(tmp_path)
    assert fr.cli_main(spec, [str(run_dir), "--estimate"]) == 0
    est = json.loads(capsys.readouterr().out)
    assert est["formalizable"] and est["direction"] == "dcfl" and est["max_usd"] > est["expected_usd"] > 0
