"""Tests for `tfl_eval` (TODO.md §7 eval-set harness).

No network: mocked pipeline runs only (root `conftest.py` also blocks any
real Anthropic call). Covers:
  - the manifest is well-formed and covers every id in `docs/EVAL_SET.md`;
  - every manifest IR file passes its system's own schema validator;
  - `tfl_eval.metrics` on synthetic (hand-built) results;
  - the CLI, without `--live`, on a handful of tasks that have mock fixtures.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tfl_eval import metrics
import tfl_eval.cli as cli_mod
from tfl_eval.cli import main as cli_main
from tfl_eval.runners import REPO_ROOT, SYSTEMS, validate

MANIFEST_PATH = REPO_ROOT / "tfl_eval" / "manifest.json"
EVAL_SET_PATH = REPO_ROOT / "docs" / "EVAL_SET.md"

_ID_RE = re.compile(r"\|\s*((?:reg|cfl|dcfl|ll)-\d+)\s*\|")


@pytest.fixture(scope="module")
def manifest() -> list[dict]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def eval_set_ids() -> set[str]:
    text = EVAL_SET_PATH.read_text(encoding="utf-8")
    return set(_ID_RE.findall(text))


# ---------------------------------------------------------------------------
# 1. Manifest structure and coverage
# ---------------------------------------------------------------------------

class TestManifest:
    def test_manifest_is_a_nonempty_list(self, manifest):
        assert isinstance(manifest, list)
        assert len(manifest) > 0

    def test_every_entry_has_required_fields(self, manifest):
        required = {"id", "system", "path", "expected", "trap", "source"}
        for entry in manifest:
            missing = required - entry.keys()
            assert not missing, f"{entry.get('id')}: missing fields {missing}"
            assert entry["system"] in SYSTEMS
            assert isinstance(entry["trap"], bool)
            assert isinstance(entry["expected"], dict)
            assert "verdict" in entry["expected"]

    def test_no_duplicate_ids(self, manifest):
        ids = [e["id"] for e in manifest]
        assert len(ids) == len(set(ids)), "duplicate ids in manifest.json"

    def test_manifest_covers_all_eval_set_ids(self, manifest, eval_set_ids):
        manifest_ids = {e["id"] for e in manifest}
        assert eval_set_ids, "failed to parse any id out of docs/EVAL_SET.md"
        missing = eval_set_ids - manifest_ids
        extra = manifest_ids - eval_set_ids
        assert not missing, f"ids in EVAL_SET.md but not in manifest: {sorted(missing)}"
        assert not extra, f"ids in manifest but not in EVAL_SET.md: {sorted(extra)}"

    def test_paths_resolve_under_repo_root(self, manifest):
        for entry in manifest:
            path = REPO_ROOT / entry["path"]
            assert path.exists(), f"{entry['id']}: {path} does not exist"
            assert path.suffix == ".json"


# ---------------------------------------------------------------------------
# 2. Every IR file passes its own system's validator
# ---------------------------------------------------------------------------

class TestIRValidation:
    def test_all_manifest_irs_are_valid(self, manifest):
        failures = []
        for entry in manifest:
            ir = json.loads((REPO_ROOT / entry["path"]).read_text(encoding="utf-8"))
            errors = validate(entry["system"], ir)
            if errors:
                failures.append((entry["id"], errors))
        assert not failures, f"IR validation failures: {failures}"

    @pytest.mark.parametrize("system", SYSTEMS)
    def test_each_system_has_entries(self, manifest, system):
        assert any(e["system"] == system for e in manifest)


# ---------------------------------------------------------------------------
# 3. Metrics on synthetic results
# ---------------------------------------------------------------------------

def _rec(id_, system="cfl", trap=False, status="ran", verdict="cfl", confidence=0.8, correct=True):
    return {
        "id": id_, "system": system, "trap": trap, "status": status,
        "verdict": verdict, "confidence": confidence, "correct": correct,
    }


class TestMetrics:
    def test_accuracy_all_correct(self):
        records = [_rec("a", correct=True), _rec("b", correct=True)]
        result = metrics.accuracy(records)
        assert result["accuracy"] == 1.0
        assert result["n_scoreable"] == 2

    def test_accuracy_mixed(self):
        records = [_rec("a", correct=True), _rec("b", correct=False)]
        result = metrics.accuracy(records)
        assert result["accuracy"] == 0.5

    def test_accuracy_ignores_skipped_and_inconclusive(self):
        records = [
            _rec("a", correct=True),
            _rec("b", status="skipped", verdict=None, correct=None),
            _rec("c", verdict="uncertain", correct=False),
            _rec("d", status="error", verdict=None, correct=None),
        ]
        result = metrics.accuracy(records)
        assert result["n_scoreable"] == 1
        assert result["accuracy"] == 1.0
        # "attempted" means status == "ran" (b was skipped, d errored out).
        assert result["n_attempted"] == 2

    def test_brier_score_perfect_confident_correct(self):
        records = [_rec("a", confidence=1.0, correct=True)]
        result = metrics.brier_score(records)
        assert result["brier"] == pytest.approx(0.0)

    def test_brier_score_confident_wrong_is_worst(self):
        records = [_rec("a", confidence=1.0, correct=False)]
        result = metrics.brier_score(records)
        assert result["brier"] == pytest.approx(1.0)

    def test_brier_score_none_when_nothing_scoreable(self):
        records = [_rec("a", status="skipped", verdict=None, correct=None)]
        result = metrics.brier_score(records)
        assert result["brier"] is None
        assert result["n"] == 0

    def test_inconclusive_rate(self):
        records = [
            _rec("a", verdict="cfl", correct=True),
            _rec("b", verdict="uncertain", correct=False),
            _rec("c", status="skipped", verdict=None, correct=None),
        ]
        result = metrics.inconclusive_rate(records)
        # only "a" and "b" were attempted (ran); "b" is inconclusive
        assert result["n_attempted"] == 2
        assert result["n_inconclusive"] == 1
        assert result["rate"] == 0.5

    def test_false_confident_wrong(self):
        records = [
            _rec("a", confidence=0.9, correct=False),   # confident + wrong
            _rec("b", confidence=0.3, correct=False),   # wrong but not confident
            _rec("c", confidence=0.9, correct=True),    # confident + correct
        ]
        result = metrics.false_confident_wrong(records)
        assert result["n_false_confident"] == 1
        assert result["ids"] == ["a"]
        assert result["rate"] == pytest.approx(1 / 3)

    def test_summarize_by_system_and_traps(self):
        records = [
            _rec("cfl-01", system="cfl", trap=True, correct=True),
            _rec("ll-01", system="ll", trap=False, correct=False),
        ]
        report = metrics.summarize(records)
        assert set(report["by_system"]) == {"cfl", "ll"}
        assert report["traps"]["accuracy"]["n_scoreable"] == 1
        assert report["traps"]["accuracy"]["accuracy"] == 1.0
        assert report["overall"]["accuracy"]["n_scoreable"] == 2


# ---------------------------------------------------------------------------
# 3b. `_run_entry`'s `correct` field must check `k`, not just `verdict`
# (Format-3 k-traps: ll-08/ll-09/ll-15/ll-18 have a right-verdict-wrong-k
# distractor -- a bare verdict match would count any k as correct there).
# ---------------------------------------------------------------------------

def _stub_entry(expected: dict) -> dict:
    return {
        "id": "fake-k-trap", "system": "ll", "trap": True,
        # any already-valid IR file -- validate()/run_pipeline() are stubbed
        # below, so its content doesn't matter, only that it parses.
        "path": "ll_system/examples/eval/ll-08.json",
        "expected": expected,
    }


def _run_stub_entry(monkeypatch, expected: dict, extracted: dict) -> dict:
    monkeypatch.setattr(cli_mod, "validate", lambda system, ir: [])
    monkeypatch.setattr(cli_mod, "make_live_runner", lambda system: object())
    monkeypatch.setattr(cli_mod, "run_pipeline", lambda *a, **kw: {})
    monkeypatch.setattr(
        cli_mod, "extract",
        lambda system, result: {
            "verdict": extracted.get("verdict"),
            "confidence": extracted.get("confidence", 0.9),
            "k": extracted.get("k"),
            "basis_trust": [],
            "verdict_gate": {},
        },
    )
    return cli_mod._run_entry(_stub_entry(expected), live=True, live_runner_cache={})


class TestRunEntryKCorrectness:
    def test_right_verdict_wrong_k_is_incorrect(self, monkeypatch):
        record = _run_stub_entry(
            monkeypatch, expected={"verdict": "ll", "k": 2}, extracted={"verdict": "ll", "k": 1},
        )
        assert record["status"] == "ran"
        assert record["verdict"] == "ll"
        assert record["k_correct"] is False
        assert record["correct"] is False

    def test_right_verdict_and_k_is_correct(self, monkeypatch):
        record = _run_stub_entry(
            monkeypatch, expected={"verdict": "ll", "k": 2}, extracted={"verdict": "ll", "k": 2},
        )
        assert record["k_correct"] is True
        assert record["correct"] is True

    def test_no_expected_k_skips_the_k_check(self, monkeypatch):
        # ll-10-style expectation: "not LL(k) for all k" -- expected.k is
        # None, so any (or no) extracted k is fine, only verdict is checked.
        record = _run_stub_entry(
            monkeypatch, expected={"verdict": "not_ll", "k": None},
            extracted={"verdict": "not_ll", "k": None},
        )
        assert record["k_correct"] is True
        assert record["correct"] is True


# ---------------------------------------------------------------------------
# 3c. `_run_entry` must report a per-entry usage DELTA, not the shared
# `live_runner_cache` runner's cumulative running total (reviewer finding).
# ---------------------------------------------------------------------------

class _CumulativeUsageRunner:
    """Stub agent runner whose usage_tracker.as_dict() returns an
    ever-growing cumulative snapshot, exactly like the real UsageTracker
    (TODO.md §3) — used to exercise the shared-`live_runner_cache` usage
    delta across several `_run_entry` calls."""

    def __init__(self) -> None:
        self._calls = 0
        self.usage_tracker = self

    def as_dict(self) -> dict:
        return {
            "calls": self._calls,
            "total_tokens": self._calls * 10,
            "estimated_cost_usd": round(self._calls * 0.01, 6),
        }


class TestRunEntryUsageDelta:
    def test_reports_per_entry_delta_not_cumulative_total(self, monkeypatch):
        """3 entries x 10 calls each, sharing one cached runner, must each
        report usage=10 calls -- not the cumulative 10/20/30 that summing
        `result['usage']` verbatim used to produce."""
        runner = _CumulativeUsageRunner()
        monkeypatch.setattr(cli_mod, "validate", lambda system, ir: [])
        monkeypatch.setattr(cli_mod, "make_live_runner", lambda system: runner)

        def _fake_run_pipeline(system, ir, mock_runner=None, agent_runner=None):
            agent_runner.usage_tracker._calls += 10  # 10 LLM calls this round
            return {"verdict": None, "confidence": None, "usage": agent_runner.usage_tracker.as_dict()}

        monkeypatch.setattr(cli_mod, "run_pipeline", _fake_run_pipeline)
        monkeypatch.setattr(
            cli_mod, "extract",
            lambda system, result: {
                "verdict": None, "confidence": None, "k": None,
                "basis_trust": [], "verdict_gate": {},
            },
        )

        entry = {
            "id": "usage-delta-fake", "system": "reg", "trap": False, "expected": {},
            "path": "agent_system/examples/task1_palindrome_prefix_suffix.json",
        }
        cache: dict = {}
        records = [cli_mod._run_entry(entry, live=True, live_runner_cache=cache) for _ in range(3)]

        assert [r["usage"]["calls"] for r in records] == [10, 10, 10]
        assert [r["usage"]["total_tokens"] for r in records] == [100, 100, 100]
        # The runner itself is genuinely shared (same cache entry reused).
        assert runner._calls == 30


# ---------------------------------------------------------------------------
# 4. CLI end-to-end, mocked (no --live, no network)
# ---------------------------------------------------------------------------

# Ids whose manifest entry names an existing mock fixture (see manifest.json
# "mock_task"); picked across three of the four systems to exercise the
# mock-loading path without a live API call.
_MOCKED_IDS = ["reg-14", "cfl-07", "dcfl-08"]
_UNMOCKED_IDS = ["reg-01"]


class TestCLIMocked:
    def test_cli_runs_mocked_tasks(self, tmp_path):
        out_dir = tmp_path / "run1"
        code = cli_main(["--ids", ",".join(_MOCKED_IDS), "--out", str(out_dir)])
        assert code == 0

        report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        assert (out_dir / "report.md").exists()

        records = {r["id"]: r for r in report["records"]}
        assert set(records) == set(_MOCKED_IDS)
        for entry_id in _MOCKED_IDS:
            record = records[entry_id]
            assert record["status"] == "ran", record
            assert record["verdict"] is not None
            assert "confidence" in record
            assert "basis_trust" in record

        assert report["meta"]["live"] is False

    def test_cli_skips_tasks_without_mocks(self, tmp_path):
        out_dir = tmp_path / "run2"
        code = cli_main(["--ids", ",".join(_UNMOCKED_IDS), "--out", str(out_dir)])
        assert code == 0
        report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        for record in report["records"]:
            assert record["status"] == "skipped"
            assert record.get("verdict") is None

    def test_cli_rejects_unknown_system(self, tmp_path, capsys):
        code = cli_main(["--systems", "bogus", "--out", str(tmp_path / "run3")])
        assert code == 2

    def test_cli_filters_by_system(self, tmp_path):
        out_dir = tmp_path / "run4"
        code = cli_main(["--systems", "cfl", "--out", str(out_dir)])
        assert code == 0
        report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        assert all(r["system"] == "cfl" for r in report["records"])
        assert len(report["records"]) > 0


class TestUsageAggregation:
    """``_aggregate_usage`` (TODO.md §3): pure aggregation of each
    record's ``usage`` block (mock runs never populate one -- MockRunner
    has no ``usage_tracker`` -- so these use synthetic records)."""

    def test_empty_when_no_record_has_usage(self):
        agg = cli_mod._aggregate_usage([{"id": "a"}, {"id": "b", "usage": None}])
        assert agg["records_with_usage"] == 0
        assert agg["calls"] == 0
        assert agg["estimated_cost_usd"] is None

    def test_sums_across_records(self):
        records = [
            {"id": "a", "usage": {
                "calls": 3, "input_tokens": 100, "output_tokens": 40,
                "total_tokens": 140, "structured_output_calls": 2,
                "extraction_fallback_calls": 1, "estimated_cost_usd": 0.01,
            }},
            {"id": "b", "usage": {
                "calls": 2, "input_tokens": 50, "output_tokens": 20,
                "total_tokens": 70, "structured_output_calls": 2,
                "extraction_fallback_calls": 0, "estimated_cost_usd": 0.005,
            }},
            {"id": "c"},  # skipped task, no usage at all
        ]
        agg = cli_mod._aggregate_usage(records)
        assert agg["records_with_usage"] == 2
        assert agg["calls"] == 5
        assert agg["total_tokens"] == 210
        assert agg["structured_output_calls"] == 4
        assert agg["extraction_fallback_calls"] == 1
        assert agg["estimated_cost_usd"] == 0.015

    def test_cost_unknown_if_any_contributing_record_is_unknown(self):
        records = [
            {"id": "a", "usage": {"calls": 1, "estimated_cost_usd": 0.01}},
            {"id": "b", "usage": {"calls": 1, "estimated_cost_usd": None}},
        ]
        agg = cli_mod._aggregate_usage(records)
        assert agg["estimated_cost_usd"] is None
