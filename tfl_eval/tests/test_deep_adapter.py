"""`tfl-eval --systems deep`: the tfl_deep_agent adapter (P8).

Mock mode only: the end-to-end tests replay the scripts in
tfl_deep_agent/examples/mock/eval/; the live path is exercised with a fake
`run_deep`, so nothing here reaches the Anthropic API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tfl_eval import cli, runners
from tfl_eval.runners import (
    DEEP_MOCK_DIR,
    DeepAdapterError,
    deep_mock_script,
    deep_task_text,
    extract_deep,
    run_deep,
)

MANIFEST = {e["id"]: e for e in cli.load_manifest(cli.DEFAULT_MANIFEST)}
SCRIPTED = sorted(p.stem for p in DEEP_MOCK_DIR.glob("*.json"))


@pytest.fixture(autouse=True)
def _undo_live_model_override(monkeypatch):
    """`cli.main(["--live", ...])` writes the eval's Haiku default straight into
    os.environ (`set_live_model_env`). `monkeypatch.delenv(..., raising=False)`
    on an absent variable records no undo, so that write outlived the test and
    forced claude-haiku-5-5 on every later deep-agent test in the same xdist
    worker (test_reports.py::test_cfl_mock_run_renders_end_to_end). setenv then
    delenv registers an undo that removes the variable at teardown."""
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "placeholder")
    monkeypatch.delenv("TFL_MODEL_OVERRIDE")


def _submitted(verdict_text, lang, token, *, published=..., cap=0.85, trust="bounded_pass", basis=("ev-1",),
               **extra):
    result = {
        "status": "submitted",
        "report": {"verdict": verdict_text, "language_class": lang},
        "verdict_token": token,
        "confidence_cap": cap,
        "trust": trust,
        "basis": list(basis),
        "verdict_gate": [],
        "verdict_direction": "constructive",
        "human_review": "auto-approved (--auto-approve): without human review",
        "budget": {"spent_usd": 0.12, "calls": 4},
    }
    if published is not ...:
        result["published_verdict"] = published
    result.update(extra)
    return result


# ---------------------------------------------------------------------------
# extract_deep: result.json -> the eval's verdict vocabulary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang,text,token", [
    ("reg", "regular", "regular"),
    ("reg", "not regular", "non_regular"),
    ("cfl", "CFL", "cfl"),
    ("cfl", "not CFL", "non_cfl"),
    ("dcfl", "DCFL", "dcfl"),
    ("dcfl", "not DCFL", "non_dcfl"),
    ("ll", "not LL(k) for any k", "not_ll"),
])
def test_extract_maps_the_policy_token(lang, text, token):
    out = extract_deep(_submitted(text, lang, token, published=token), lang)
    assert out["verdict"] == token
    assert out["confidence"] == 0.85
    assert out["k"] is None
    assert out["class_mismatch"] is False
    assert out["basis_trust"] == ["bounded_pass"]
    assert out["deep_status"] == "submitted"


def test_extract_prefers_published_verdict_over_token():
    # REG: an unresolved contradiction publishes None although the gate token is "partial"
    out = extract_deep(_submitted("regular", "reg", "partial", published=None, cap=0.5), "reg")
    assert out["verdict"] is None
    # no published_verdict key (older result) -> the gate token
    out = extract_deep(_submitted("regular", "reg", "regular"), "reg")
    assert out["verdict"] == "regular"


def test_extract_never_uses_the_model_text_verdict():
    # the model said "CFL", the gate found no basis -> inconclusive token (None for cfl)
    out = extract_deep(_submitted("CFL", "cfl", None, published=None, cap=0.4, trust="not_verified",
                                  basis=()), "cfl")
    assert out["verdict"] is None
    assert out["basis_trust"] == []


@pytest.mark.parametrize("text,k", [("LL(1)", 1), ("grammar is LL( 2 )", 2), ("ll(12)", 12), ("LL", None)])
def test_extract_reads_k_of_an_ll_verdict(text, k):
    out = extract_deep(_submitted(text, "ll", "ll", published="ll", cap=0.98, trust="verified"), "ll")
    assert out["verdict"] == "ll"
    assert out["k"] == k


def test_extract_ll_inconclusive_has_no_k():
    out = extract_deep(_submitted("LL(1)", "ll", "uncertain", published="uncertain", cap=0.4), "ll")
    assert out["verdict"] == "uncertain"
    assert out["k"] is None


@pytest.mark.parametrize("status", ["incomplete", "budget_exhausted", "error", "awaiting_human"])
def test_extract_run_without_accepted_report_is_inconclusive(status):
    result = _submitted("regular", "reg", "regular", published="regular")
    result["status"] = status
    out = extract_deep(result, "reg")
    assert out["verdict"] is None
    assert out["language_class"] is None
    assert out["class_mismatch"] is False
    assert out["deep_status"] == status


def test_extract_flags_a_class_mismatch():
    out = extract_deep(_submitted("not CFL", "cfl", "non_cfl", published="non_cfl"), "dcfl")
    assert out["verdict"] == "non_cfl"
    assert out["class_mismatch"] is True


def test_extract_confidence_prefers_final_confidence_then_cap():
    out = extract_deep(_submitted("regular", "reg", "regular", published="regular", confidence=0.6), "reg")
    assert out["confidence"] == 0.6
    out = extract_deep(_submitted("regular", "reg", "regular", published="regular", confidence=True), "reg")
    assert out["confidence"] == 0.85
    out = extract_deep({"status": "incomplete"}, "reg")
    assert out["confidence"] is None and out["verdict"] is None


# ---------------------------------------------------------------------------
# inputs: source_text plus the class question; mock scripts
# ---------------------------------------------------------------------------

def _ir(entry_id):
    return json.loads((runners.REPO_ROOT / MANIFEST[entry_id]["path"]).read_text(encoding="utf-8"))


def test_task_text_is_the_source_text_and_one_class_question():
    assert deep_task_text(_ir("reg-04"), "reg") == "(ab)*a | b\nВопрос: является ли язык регулярным?"
    for bad in ({}, {"source_text": "   "}, {"source_text": 3}):
        with pytest.raises(DeepAdapterError):
            deep_task_text(bad, "reg")
    with pytest.raises(DeepAdapterError):
        deep_task_text(_ir("reg-04"), "deep")


def test_the_same_language_gets_the_question_of_its_class():
    # {a^n b^n | n >= 0} is reg-01, dcfl-01 and ll-01: only the question line differs
    texts = {i: deep_task_text(_ir(i), MANIFEST[i]["system"]) for i in ("reg-01", "dcfl-01", "ll-01")}
    assert {t.split("\n")[0] for t in texts.values()} == {"{a^n b^n | n >= 0}"}
    assert texts["reg-01"].endswith("является ли язык регулярным?")
    assert texts["dcfl-01"].endswith("детерминированным контекстно-свободным (DCFL)?")
    assert texts["ll-01"].endswith(runners.DEEP_LL_LANGUAGE_QUESTION)
    assert len(set(texts.values())) == 3


@pytest.mark.parametrize("entry_id", sorted(MANIFEST))
def test_every_manifest_entry_gets_exactly_one_question_line(entry_id):
    entry = MANIFEST[entry_id]
    ir = _ir(entry_id)
    text = deep_task_text(ir, entry["system"])
    head, question = text.rsplit("\n", 1)
    assert head == ir["source_text"].strip()
    assert question.startswith("Вопрос: ") and question.endswith("?")
    if entry["system"] == "ll":
        # Format 3 asks about the given grammar, every other LL task about the language; both ask for the
        # smallest k, which is what the manifest's expected k holds
        grammar_task = ir.get("task_type") == "ll_check_grammar"
        assert question == (runners.DEEP_LL_GRAMMAR_QUESTION if grammar_task else runners.DEEP_LL_LANGUAGE_QUESTION)
        assert "наименьшее" in question
    else:
        assert question == runners.DEEP_CLASS_QUESTIONS[entry["system"]]


def test_mock_script_lookup():
    assert deep_mock_script("reg-04") == DEEP_MOCK_DIR / "reg-04.json"
    assert deep_mock_script("no-such-task") is None


def test_run_deep_needs_exactly_one_mode(tmp_path):
    with pytest.raises(DeepAdapterError):
        run_deep("x", tmp_path / "a")
    with pytest.raises(DeepAdapterError):
        run_deep("x", tmp_path / "b", mock_script=DEEP_MOCK_DIR / "reg-04.json", live=True)


def test_there_are_scripts_for_every_class():
    assert len(SCRIPTED) >= 5
    assert {MANIFEST[i]["system"] for i in SCRIPTED} >= {"reg", "cfl", "dcfl", "ll"}


@pytest.mark.parametrize("entry_id", SCRIPTED)
def test_every_deep_script_belongs_to_a_manifest_task(entry_id):
    pytest.importorskip("deepagents")
    from tfl_deep_agent.agent import load_mock_script

    assert entry_id in MANIFEST, f"{entry_id}.json does not name a manifest entry"
    script = load_mock_script(DEEP_MOCK_DIR / f"{entry_id}.json")
    ir = json.loads((runners.REPO_ROOT / MANIFEST[entry_id]["path"]).read_text(encoding="utf-8"))
    assert script["task"] == deep_task_text(ir, MANIFEST[entry_id]["system"])
    # the IR the replay validates is the manifest's own IR
    assert script["defs"]["ir"] == ir


# ---------------------------------------------------------------------------
# end to end, mock mode (real agent loop and tools, scripted model replies)
# ---------------------------------------------------------------------------

def _main(tmp_path, *argv):
    out = tmp_path / "eval"
    code = cli.main([*argv, "--out", str(out)])
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    return code, report, out


def test_tfl_eval_system_deep_passes_in_mock_mode(tmp_path):
    pytest.importorskip("deepagents")
    pytest.importorskip("langgraph.checkpoint.sqlite")
    code, report, out = _main(tmp_path, "--systems", "deep")
    assert code == 0
    records = report["records"]
    assert len(records) == len(MANIFEST)
    assert all(r["system"] == "deep" for r in records)
    ran = {r["id"]: r for r in records if r["status"] == "ran"}
    assert sorted(ran) == SCRIPTED
    assert {r["status"] for r in records if r["id"] not in ran} == {"skipped"}
    for entry_id, r in ran.items():
        assert r["task_system"] == MANIFEST[entry_id]["system"]
        assert r["verdict"] == MANIFEST[entry_id]["expected"]["verdict"], r
        assert r["correct"] is True, r
        assert r["deep_status"] == "submitted"
        assert r["class_mismatch"] is False
        assert "without human review" in r["human_review"]
        assert r["usage"] is None  # mock: no money, no usage
        result = json.loads((Path(r["run_dir"]) / "result.json").read_text(encoding="utf-8"))
        # the agent got the source text plus the question of the entry's class
        assert result["task"] == deep_task_text(_ir(entry_id), MANIFEST[entry_id]["system"])
    assert ran["ll-17"]["k"] == 1 and ran["ll-17"]["k_correct"] is True
    assert ran["cfl-04"]["trust"] == "verified" and ran["cfl-04"]["confidence"] == 0.98
    assert ran["reg-13"]["verdict"] == "non_regular" and ran["reg-13"]["confidence"] == 0.85
    # reg-04: the task's own regex compiled from the IR (regex_to_dfa(ir=...)). The eval run
    # auto-approves HITL 1, so no human approved that IR: a bounded check like dfa_vs_ir
    # (verified, 0.98, only for a human-approved IR, VERDICT_POLICY §1)
    assert ran["reg-04"]["trust"] == "bounded_pass" and ran["reg-04"]["confidence"] == 0.85
    assert ran["reg-04"]["verdict_gate"]["basis"] == ["ev-dfa", "ev-dfa-vs-ir"]
    # dcfl-04 (a trap): a hand-written DPDA checked against the task (R2', dpda_vs_ir)
    assert ran["dcfl-04"]["verdict"] == "dcfl" and ran["dcfl-04"]["trust"] == "bounded_pass"
    assert ran["dcfl-04"]["verdict_gate"]["basis"] == ["ev-dpda-vs-ir"]
    assert report["metrics"]["overall"]["accuracy"]["accuracy"] == 1.0
    assert report["metrics"]["by_system"]["deep"]["accuracy"]["n_correct"] == len(SCRIPTED)
    assert report["deep_by_class"]["reg"]["accuracy"]["n_correct"] == 2
    assert report["meta"]["live"] is False
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "| reg-04 | deep (reg) |" in md


def test_deep_alongside_a_pipeline_and_ids_filter(tmp_path):
    pytest.importorskip("deepagents")
    pytest.importorskip("langgraph.checkpoint.sqlite")
    code, report, _ = _main(tmp_path, "--systems", "reg,deep", "--ids", "reg-04,cfl-04")
    assert code == 0
    got = sorted((r["system"], r["id"], r["status"]) for r in report["records"])
    # the reg pipeline has no mock fixtures for reg-04; the deep agent runs both ids
    assert got == [("deep", "cfl-04", "ran"), ("deep", "reg-04", "ran"), ("reg", "reg-04", "skipped")]


# ---------------------------------------------------------------------------
# the four existing systems are unchanged
# ---------------------------------------------------------------------------

def test_default_systems_do_not_include_deep(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the deep agent must not run without --systems deep")

    monkeypatch.setattr(cli, "run_deep", boom)
    code, report, _ = _main(tmp_path, "--ids", "reg-04,cfl-01")
    assert code == 0
    assert report["meta"]["systems"] == list(runners.SYSTEMS)
    assert "deep_by_class" not in report
    assert {r["system"] for r in report["records"]} == {"reg", "cfl"}
    assert all("task_system" not in r for r in report["records"])
    assert "deep" not in cli.build_arg_parser().get_default("systems").split(",")


def test_unknown_system_still_exits_2(tmp_path):
    assert cli.main(["--systems", "deeper", "--out", str(tmp_path / "x")]) == 2


def test_deep_settings_needs_system_deep(tmp_path):
    assert cli.main(["--systems", "reg", "--deep-settings", str(tmp_path / "s.json"),
                     "--out", str(tmp_path / "x")]) == 2


def test_missing_deep_settings_file_exits_2(tmp_path):
    assert cli.main(["--systems", "deep", "--deep-settings", str(tmp_path / "nope.json"),
                     "--out", str(tmp_path / "x")]) == 2


# ---------------------------------------------------------------------------
# live path and failures, with a fake run_deep (no API)
# ---------------------------------------------------------------------------

def test_live_runs_every_entry_without_scripts_and_reports_usage(tmp_path, monkeypatch):
    calls = []

    def fake_run_deep(task_text, run_dir, *, mock_script=None, live=False, settings=None):
        calls.append({"task": task_text, "run_dir": Path(run_dir), "mock_script": mock_script, "live": live})
        if "aSb | ab" in task_text:  # reg-13: a wrong, confident answer
            return _submitted("regular", "reg", "regular", published="regular")
        return {"status": "budget_exhausted", "budget": {"spent_usd": 0.05, "calls": 2}}

    monkeypatch.setattr(cli, "run_deep", fake_run_deep)
    monkeypatch.setenv("TFL_MODEL_OVERRIDE", "placeholder")
    monkeypatch.delenv("TFL_MODEL_OVERRIDE")
    code, report, out = _main(tmp_path, "--systems", "deep", "--ids", "reg-13,dcfl-03", "--live",
                              "--deep-max-usd", "1000")
    assert code == 0
    import os

    assert os.environ["TFL_MODEL_OVERRIDE"] == "claude-haiku-5-5"  # the eval's live default
    assert sorted(c["task"] for c in calls) == sorted([
        "S -> aSb | ab\nВопрос: является ли язык регулярным?",
        "{w reverse(w) | w in {a,b}*}\nВопрос: является ли язык детерминированным контекстно-свободным (DCFL)?"])
    assert all(c["live"] is True and c["mock_script"] is None for c in calls)
    assert all(c["run_dir"].parent == out / "deep_runs" for c in calls)
    recs = {r["id"]: r for r in report["records"]}
    assert recs["reg-13"]["correct"] is False
    assert report["metrics"]["overall"]["false_confident_wrong"]["ids"] == ["reg-13"]
    assert recs["dcfl-03"]["verdict"] is None and recs["dcfl-03"]["deep_status"] == "budget_exhausted"
    assert report["metrics"]["overall"]["inconclusive"]["n_inconclusive"] == 1
    assert report["usage"]["calls"] == 6
    assert report["usage"]["estimated_cost_usd"] == pytest.approx(0.17)
    assert report["meta"]["live"] is True


def test_a_class_mismatch_scores_wrong(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_deep", lambda *a, **k: _submitted("not CFL", "cfl", "non_cfl",
                                                                    published="non_cfl"))
    monkeypatch.setattr(cli, "deep_mock_script", lambda _id: Path("fake.json"))
    code, report, _ = _main(tmp_path, "--systems", "deep", "--ids", "dcfl-03")
    assert code == 0
    (rec,) = report["records"]
    assert rec["class_mismatch"] is True and rec["correct"] is False


def test_a_failing_run_is_an_error_record(tmp_path, monkeypatch):
    def fail(*_a, **_k):
        raise DeepAdapterError("the run stopped for a human decision at hitl1 despite auto-approve")

    monkeypatch.setattr(cli, "run_deep", fail)
    code, report, _ = _main(tmp_path, "--systems", "deep", "--ids", "reg-04")
    assert code == 0
    (rec,) = report["records"]
    assert rec["status"] == "error" and "despite auto-approve" in rec["error"]


def test_run_deep_reports_an_awaiting_run(tmp_path, monkeypatch):
    pytest.importorskip("deepagents")
    from tfl_deep_agent import hitl

    class Outcome:
        awaiting = True
        pending = type("P", (), {"point": "report"})()
        result = None

    monkeypatch.setattr(hitl, "start_run", lambda *a, **k: Outcome())
    with pytest.raises(DeepAdapterError, match="report"):
        run_deep("x", tmp_path / "r", mock_script=DEEP_MOCK_DIR / "reg-04.json")


def test_run_deep_passes_auto_approve_and_reads_result_json(tmp_path, monkeypatch):
    pytest.importorskip("deepagents")
    from tfl_deep_agent import hitl

    seen = {}

    from types import SimpleNamespace

    def fake_start_run(run_dir, task, **kwargs):
        seen.update(kwargs, run_dir=Path(run_dir), task=task)
        Path(run_dir).mkdir(parents=True)
        (Path(run_dir) / "result.json").write_text(json.dumps({"status": "submitted", "x": 1}), encoding="utf-8")
        return SimpleNamespace(awaiting=False, result={"status": "incomplete"})

    monkeypatch.setattr(hitl, "start_run", fake_start_run)
    out = run_deep("the task", tmp_path / "r", live=True)
    assert out == {"status": "submitted", "x": 1}
    assert seen["auto_approve"] is True and seen["dialog"] is None
    assert seen["live"] is True and seen["mock_script"] is None and seen["task"] == "the task"


# ---------------------------------------------------------------------------
# live cap, reused --out, .env key from the pipelines (fake run_deep, no API)
# ---------------------------------------------------------------------------

def _no_deep_run(*_a, **_k):
    raise AssertionError("a refused live eval must not start any deep run")


def test_live_deep_is_refused_above_the_default_cap(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_deep", _no_deep_run)
    settings = tmp_path / "s.json"
    settings.write_text(json.dumps({"budget": {"per_task_usd": 0.6, "per_day_usd": 20.0,
                                               "reserve_for_report_usd": 0.1}}), encoding="utf-8")
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)
    code = cli.main(["--systems", "deep", "--ids", "reg-04,reg-13", "--live", "--deep-settings", str(settings),
                     "--out", str(tmp_path / "x")])
    assert code == 2
    err = capsys.readouterr().err
    assert "worst case $1.20" in err and "refused" in err
    assert not (tmp_path / "x" / "report.json").exists()


def test_live_deep_without_ids_is_refused_by_the_day_bound(tmp_path, monkeypatch, capsys):
    # every manifest entry: min(74 x 5.00, 20.00) = $20.00 > $1.00
    monkeypatch.setattr(cli, "run_deep", _no_deep_run)
    monkeypatch.setattr(cli, "load_deep_settings", lambda _p=None: _settings_obj(5.0, 20.0))
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)
    assert cli.main(["--systems", "deep", "--live", "--out", str(tmp_path / "x")]) == 2
    assert "worst case $20.00" in capsys.readouterr().err


def test_live_deep_within_the_cap_runs(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "run_deep", lambda *a, **k: seen.append(a) or {"status": "incomplete"})
    monkeypatch.setattr(cli, "load_deep_settings", lambda _p=None: _settings_obj(0.5, 20.0))
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)
    code, _report, _ = _main(tmp_path, "--systems", "deep", "--ids", "reg-04,reg-13", "--live")
    assert code == 0 and len(seen) == 2  # 2 x 0.50 = 1.00, not above the default cap


def test_mock_deep_ignores_the_live_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_deep", lambda *a, **k: {"status": "incomplete"})
    monkeypatch.setattr(cli, "deep_mock_script", lambda _id: Path("fake.json"))
    code, report, _ = _main(tmp_path, "--systems", "deep", "--ids", "reg-04,reg-13", "--deep-max-usd", "0")
    assert code == 0 and {r["status"] for r in report["records"]} == {"ran"}


def _settings_obj(per_task, per_day):
    from types import SimpleNamespace

    return SimpleNamespace(budget=SimpleNamespace(per_task_usd=per_task, per_day_usd=per_day))


def test_a_second_eval_into_the_same_out_gets_fresh_run_dirs(tmp_path):
    pytest.importorskip("deepagents")
    pytest.importorskip("langgraph.checkpoint.sqlite")
    code1, report1, out = _main(tmp_path, "--systems", "deep", "--ids", "reg-04")
    code2, report2, out2 = _main(tmp_path, "--systems", "deep", "--ids", "reg-04")
    assert out2 == out and code1 == code2 == 0
    (r1,), (r2,) = report1["records"], report2["records"]
    assert r1["status"] == r2["status"] == "ran", (r1, r2)
    assert r2["correct"] is True
    assert Path(r1["run_dir"]) == out / "deep_runs" / "reg-04"
    assert Path(r2["run_dir"]) == out / "deep_runs" / "reg-04-2"
    assert (Path(r1["run_dir"]) / "result.json").is_file() and (Path(r2["run_dir"]) / "result.json").is_file()


def test_fresh_run_dir_numbers_taken_paths(tmp_path):
    assert cli._fresh_run_dir(tmp_path, "a") == tmp_path / "a"
    (tmp_path / "a").mkdir()
    (tmp_path / "a-2").mkdir()
    assert cli._fresh_run_dir(tmp_path, "a") == tmp_path / "a-3"


def test_deep_entries_do_not_inherit_a_key_the_pipelines_loaded(tmp_path, monkeypatch):
    """A pipeline's live runner copies `.env` into os.environ; the deep runs
    after it must see the key as it was before the eval (here: none)."""
    import os

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)

    def pipeline_loading_dotenv(entry, live, cache):
        os.environ["ANTHROPIC_API_KEY"] = "from-dotenv"
        return {"id": entry["id"], "system": entry["system"], "status": "skipped", "reason": "fake"}

    keys = []

    def fake_run_deep(*_a, **_k):
        keys.append(os.environ.get("ANTHROPIC_API_KEY"))
        return {"status": "incomplete"}

    monkeypatch.setattr(cli, "_run_entry", pipeline_loading_dotenv)
    monkeypatch.setattr(cli, "run_deep", fake_run_deep)
    monkeypatch.setattr(cli, "load_deep_settings", lambda _p=None: _settings_obj(0.5, 20.0))
    code, _report, _ = _main(tmp_path, "--systems", "reg,deep", "--ids", "reg-04", "--live")
    assert code == 0
    assert keys == [None]


def test_deep_entries_keep_a_key_that_was_already_set(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-the-shell")
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)

    def pipeline_loading_dotenv(entry, live, cache):
        os.environ["ANTHROPIC_API_KEY"] = "from-dotenv"
        return {"id": entry["id"], "system": entry["system"], "status": "skipped", "reason": "fake"}

    keys = []
    monkeypatch.setattr(cli, "_run_entry", pipeline_loading_dotenv)
    monkeypatch.setattr(cli, "run_deep", lambda *a, **k: keys.append(os.environ.get("ANTHROPIC_API_KEY"))
                        or {"status": "incomplete"})
    monkeypatch.setattr(cli, "load_deep_settings", lambda _p=None: _settings_obj(0.5, 20.0))
    code, _report, _ = _main(tmp_path, "--systems", "reg,deep", "--ids", "reg-04", "--live")
    assert code == 0
    assert keys == ["from-the-shell"]
