"""P2 core: the whole deep-agent loop driven by scripted fake chat models.

No test here reaches the Anthropic API: every model is a ``ScriptedChatModel``
replaying ``tfl_deep_agent/examples/mock/*.json`` (or an inline script), and
the network guard in ``conftest.py`` blocks the SDK clients anyway. The tools
are the real P1 tools, so the trust levels asserted below come from the
REG / CFL / LL libraries, not from the script.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("deepagents")

from langchain.agents.middleware import AgentMiddleware  # noqa: E402
from langchain_core.messages import ToolMessage  # noqa: E402

from tfl_deep_agent import agent as A  # noqa: E402
from tfl_deep_agent import cli  # noqa: E402
from tfl_deep_agent import config  # noqa: E402
from tfl_deep_agent.middleware import budget as B  # noqa: E402

PKG = Path(A.__file__).resolve().parent
MOCK = PKG / "examples" / "mock"
SETTINGS_EXAMPLE = PKG / "settings.example.json"


@pytest.fixture(autouse=True)
def _no_model_override(monkeypatch):
    monkeypatch.delenv("TFL_MODEL_OVERRIDE", raising=False)


def example_settings() -> dict[str, Any]:
    return json.loads(SETTINGS_EXAMPLE.read_text(encoding="utf-8"))


class ToolRecorder(AgentMiddleware):
    """Records every tool call an agent executes, with its result status."""

    def __init__(self, role: str, log: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self.role = role
        self.log = log

    @property
    def name(self) -> str:  # unique per role agent
        return f"ToolRecorder_{self.role}"

    def wrap_tool_call(self, request, handler):
        out = handler(request)
        status = getattr(out, "status", "success") if isinstance(out, ToolMessage) else "command"
        self.log.append((self.role, request.tool_call["name"], status))
        return out


def run_script(script: Any, *, settings: Any = None, template: str | None = None,
               middleware: list[Any] | None = None, record: bool = True, **kw: Any):
    """Build the agent over ``script``, run its task, return
    (result, state, factory, tool log)."""
    factory = A.scripted_model_factory(script)
    log: list[tuple[str, str, str]] = []
    sub_mw = (lambda role: [ToolRecorder(role, log)]) if record else None
    main_mw = [ToolRecorder("main", log)] if record and middleware is None else middleware
    graph = A.create_tfl_agent(
        settings if settings is not None else example_settings(),
        model_factory=factory,
        template=template or factory.script.get("template") or "full_proof",
        middleware=main_mw, subagent_middleware=sub_mw, **kw)
    state = A.run_task(graph, factory.script["task"])
    return A.collect_result(state), state, factory, log


def evidence_by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in result["evidence"]}


# ---------------------------------------------------------------------------
# The three example scripts end to end
# ---------------------------------------------------------------------------

def test_reg_script_reaches_submit_report_with_real_tool_evidence():
    result, _state, factory, log = run_script(MOCK / "reg_regex_regular.json")

    assert result["status"] == "submitted"
    assert result["report"]["verdict"] == "regular"
    assert result["report"]["language_class"] == "reg"
    ev = evidence_by_id(result)
    assert ev["ev-dfa"]["tool"] == "regex_to_dfa" and ev["ev-dfa"]["trust"] == "verified"
    assert ev["ev-dfa-vs-ir"]["tool"] == "dfa_vs_ir" and ev["ev-dfa-vs-ir"]["trust"] == "bounded_pass"
    # VERDICT_POLICY R2/R6: the basis is the check of the DFA against the task
    # (dfa_vs_ir, bounded_pass), not the exact but task-free regex -> DFA
    assert result["trust"] == "bounded_pass" and result["confidence_cap"] == 0.85
    assert result["basis"] == ["ev-dfa-vs-ir"] and result["verdict_token"] == "regular"
    assert result["verdict_gate"] == [] and result["contradiction"] is False
    assert result["unknown_ids"] == [] and result["refuted_evidence"] == []
    assert [s["trust"] for s in result["steps"]] == ["not_verified", "verified", "bounded_pass"]

    main_tools = [name for role, name, _ in log if role == "main"]
    for name in ("write_todos", "task", "regex_to_dfa", "list_theorems", "dfa_vs_ir", "submit_report"):
        assert name in main_tools, name
    assert main_tools.count("task") == 2
    # The subagents ran their own real tools inside their own loops.
    assert ("ir_builder", "validate_ir", "success") in log
    assert ("ir_builder", "sample_words", "success") in log
    assert ("critic", "run_dfa", "success") in log
    assert ("critic", "check_membership", "success") in log
    assert all(status in ("success", "command") for _, _, status in log), log  # write_todos / task: Command
    assert factory.unused_replies() == {}
    assert factory.models["main"].calls_made == 7
    assert factory.models["ir_builder"].calls_made == 4
    assert factory.models["critic"].calls_made == 2


def test_reg_critic_gets_a_fresh_context():
    _result, _state, factory, _log = run_script(MOCK / "reg_regex_regular.json")
    critic = factory.models["critic"]
    first_call = critic.received[0]
    texts = [str(m.content) for m in first_call]
    # system prompt + the one task description, nothing of the main thread
    assert len(first_call) == 2
    assert "Найди ошибку" in texts[-1]
    assert not any("regex_to_dfa" in t and "ev-dfa" in t for t in texts)
    assert "critic" in texts[0].lower()


def test_cfl_script_runs_prover_and_grammar_oracles():
    result, _state, factory, log = run_script(MOCK / "cfl_anbmcm.json")

    assert result["status"] == "submitted"
    assert result["report"]["verdict"] == "CFL"
    ev = evidence_by_id(result)
    assert ev["ev-grammar-vs-ir"]["tool"] == "grammar_vs_ir"
    assert ev["ev-grammar-vs-ir"]["trust"] == "bounded_pass"
    assert ev["ev-cyk"]["tool"] == "cyk" and ev["ev-cyk"]["trust"] == "verified"
    # verdict-level evidence is the bounded comparison, not the exact CYK runs
    assert result["trust"] == "bounded_pass" and result["confidence_cap"] == 0.85
    assert ("prover", "get_theorem", "success") in log
    assert ("prover", "cyk", "success") in log
    assert ("prover", "grammar_vs_ir", "success") in log
    assert ("ir_builder", "validate_ir", "success") in log
    assert factory.unused_replies() == {}


def test_ll_script_builds_table_and_parses():
    result, _state, factory, log = run_script(MOCK / "ll_simple_ll1.json")

    assert result["status"] == "submitted"
    assert result["report"]["verdict"] == "LL(1)"
    assert result["report"]["template"] == "short_verdict"
    ev = evidence_by_id(result)
    assert ev["ev-ll-table"]["tool"] == "ll_table" and ev["ev-ll-table"]["trust"] == "verified"
    assert ev["ev-first-follow"]["tool"] == "first_follow"
    assert ev["ev-parse-aab"]["tool"] == "ll_parse" and ev["ev-parse-ba"]["tool"] == "ll_parse"
    # the LL(1) test of the task's own grammar (ll_table over the IR): verified (R2, Format 3)
    assert result["trust"] == "verified" and result["confidence_cap"] == 0.98
    assert result["basis"] == ["ev-ll-table"] and result["verdict_token"] == "ll"
    assert ev["ev-ll-table"]["ir_fingerprint"].startswith("ir-")
    assert {name for role, name, _ in log if role == "main"} >= {
        "first_follow", "ll_table", "get_theorem", "ll_parse", "submit_report"}
    assert ("ir_builder", "validate_ir", "success") in log
    assert factory.unused_replies() == {}


# ---------------------------------------------------------------------------
# Tool binding, skills, report contract
# ---------------------------------------------------------------------------

def test_bound_tools_per_role():
    _result, _state, factory, _log = run_script(MOCK / "reg_regex_regular.json")
    main = set(factory.models["main"].bound_tools)
    assert {"write_todos", "task", "submit_report", "regex_to_dfa", "cyk", "ll_table",
            "get_theorem", "read_file", "ls"} <= main
    assert "execute" not in main
    ir_builder = set(factory.models["ir_builder"].bound_tools)
    assert {"validate_ir", "describe_ir_schema", "sample_words"} <= ir_builder
    assert not ir_builder & {"submit_report", "execute", "task", "regex_to_dfa"}
    critic = set(factory.models["critic"].bound_tools)
    assert {"run_dfa", "check_membership", "cyk", "get_theorem"} <= critic
    assert not critic & {"submit_report", "execute"}


def _mini_script(main_replies: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"task": "Является ли язык (ab)*a | b регулярным?", "defs": {}, "models": {"main": main_replies},
            **extra}


def _report(**over: Any) -> dict[str, Any]:
    args = {"verdict": "regular", "language_class": "reg", "method": "конструкция",
            "steps": [{"claim": "ДКА построен", "justification": "regex_to_dfa", "evidence_ids": ["ev-dfa"]}],
            "evidence_ids": ["ev-dfa"]}
    args.update(over)
    return {"tool_calls": [{"name": "submit_report", "id": "call-report", "args": args}]}


DFA_CALL = {"tool_calls": [{"name": "regex_to_dfa", "id": "ev-dfa", "args": {"regex": "(ab)*a|b"}}]}


def test_skills_are_readable_but_read_only():
    skill = PKG / "skills" / "reg" / "SKILL.md"
    before = skill.read_bytes()
    script = _mini_script([
        {"tool_calls": [{"name": "write_file", "id": "w1",
                         "args": {"file_path": "/skills/reg/SKILL.md", "content": "hacked"}}]},
        {"tool_calls": [{"name": "edit_file", "id": "e1",
                         "args": {"file_path": "/skills/reg/SKILL.md", "old_string": "---",
                                  "new_string": "+++"}}]},
        {"tool_calls": [{"name": "read_file", "id": "r1", "args": {"file_path": "/skills/reg/SKILL.md"}}]},
        {"tool_calls": [{"name": "write_file", "id": "w2",
                         "args": {"file_path": "/notes/plan.md", "content": "scratch"}}]},
        DFA_CALL,
        _report(),
    ])
    result, state, _factory, _log = run_script(script)
    assert skill.read_bytes() == before
    msgs = {m.tool_call_id: m for m in state["messages"] if isinstance(m, ToolMessage)}
    for denied in ("w1", "e1"):
        text = str(msgs[denied].content).lower()
        assert "denied" in text or "read-only" in text or "not allowed" in text or "permission" in text, text
    assert "name: reg" in str(msgs["r1"].content)
    assert "error" not in str(msgs["w2"].content).lower()  # the scratch filesystem stays writable
    assert result["status"] == "submitted"


def test_invalid_report_keeps_the_loop_running():
    script = _mini_script([DFA_CALL, _report(language_class="regularish"), _report()])
    result, state, factory, _log = run_script(script)
    reports = [m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "submit_report"]
    assert len(reports) == 2
    assert reports[0].status == "error" or "error" in str(reports[0].content).lower()
    assert result["status"] == "submitted" and result["report"]["language_class"] == "reg"
    assert factory.models["main"].calls_made == 3


def test_run_ends_right_after_a_valid_report():
    # a scripted reply after the report would raise ScriptError if it were requested
    script = _mini_script([DFA_CALL, _report(), {"content": "never requested"}])
    result, _state, factory, _log = run_script(script)
    assert result["status"] == "submitted"
    assert factory.models["main"].calls_made == 2
    assert factory.unused_replies() == {"main": 1}


def test_unknown_evidence_id_gives_no_trust():
    script = _mini_script([DFA_CALL, _report(
        steps=[{"claim": "ДКА построен", "justification": "", "evidence_ids": ["ev-dfa"]},
               {"claim": "минимальность доказана", "justification": "", "evidence_ids": ["ev-bogus"]}],
        evidence_ids=["ev-bogus"])])
    result, _state, _factory, _log = run_script(script)
    assert result["status"] == "submitted"
    assert result["steps"][0]["trust"] == "verified"
    assert result["steps"][1]["trust"] == "not_verified"
    assert result["steps"][1]["unknown_ids"] == ["ev-bogus"]
    assert result["trust"] == "not_verified" and result["confidence_cap"] == 0.40
    assert result["unknown_ids"] == ["ev-bogus"]


def test_no_report_is_incomplete():
    script = _mini_script([DFA_CALL, {"content": "Готово, язык регулярен."}])
    result, _state, _factory, _log = run_script(script)
    assert result["status"] == "incomplete"
    assert result["report"] is None
    assert "Готово" in result["last_message"]


def test_script_calling_an_unbound_tool_fails_loudly():
    script = _mini_script([{"tool_calls": [{"name": "rm_rf", "args": {}}]}])
    with pytest.raises(A.ScriptError, match="not bound"):
        run_script(script)


def test_script_validation():
    with pytest.raises(A.ScriptError, match="has no 'main'"):
        A.load_mock_script({"models": {"critic": []}})
    with pytest.raises(A.ScriptError, match="unknown role"):
        A.load_mock_script({"models": {"main": [], "wizard": []}})
    with pytest.raises(A.ScriptError, match="not found"):
        A.load_mock_script(MOCK / "missing.json")


def test_every_example_script_loads_and_names_known_tools():
    from tfl_deep_agent.tools.base import REGISTRY, load_groups

    load_groups()
    builtin = {"write_todos", "task", "submit_report", "read_file", "write_file", "edit_file", "ls",
               "glob", "grep"}
    for path in sorted(MOCK.glob("*.json")):
        script = A.load_mock_script(path)
        assert script.get("task"), path.name
        for role, replies in script["models"].items():
            for reply in replies:
                for tc in reply.get("tool_calls") or []:
                    assert tc["name"] in builtin or tc["name"] in REGISTRY, (path.name, role, tc["name"])


# ---------------------------------------------------------------------------
# Models and settings
# ---------------------------------------------------------------------------

def test_disallowed_model_is_refused():
    from tfl_deep_agent.settings import SettingsError

    # a role model outside models.allowed: settings validation fails closed
    settings = example_settings()
    settings["models"]["allowed"] = [config.HAIKU]
    with pytest.raises(SettingsError, match="not in models.allowed"):
        A.create_tfl_agent(settings, model_factory=A.scripted_model_factory(MOCK / "reg_regex_regular.json"))
    # an explicit request outside models.allowed is still refused
    settings = example_settings()
    settings["models"]["allowed"] = [config.SONNET, config.OPUS]
    with pytest.raises(A.ModelNotAllowedError):
        A.resolve_role_model(A.settings_dict(settings), "critic", config.HAIKU)


def test_general_role_falls_back_to_the_cheapest_allowed_model():
    # valid settings without Haiku: the general-purpose role (code default Haiku,
    # not in config.MODELS) takes the cheapest allowed model instead of failing
    settings = example_settings()
    settings["models"]["allowed"] = [config.OPUS, config.SONNET]
    settings["models"]["roles"] = {r: config.SONNET for r in settings["models"]["roles"]}
    _result, _state, factory, _log = run_script(MOCK / "reg_regex_regular.json", settings=settings)
    assert factory.models["general"].model == config.SONNET


def test_general_role_is_configurable():
    settings = example_settings()
    settings["models"]["roles"]["general"] = "sonnet"
    _result, _state, factory, _log = run_script(MOCK / "reg_regex_regular.json", settings=settings)
    assert factory.models["general"].model == config.SONNET
    # and a general model outside models.allowed fails settings validation
    from tfl_deep_agent.settings import SettingsError

    settings["models"]["allowed"] = [config.OPUS, config.HAIKU]
    settings["models"]["roles"] = {"general": "sonnet"}
    with pytest.raises(SettingsError, match="not in models.allowed"):
        A.create_tfl_agent(settings, model_factory=A.scripted_model_factory(MOCK / "reg_regex_regular.json"))


def test_role_models_follow_settings():
    settings = example_settings()
    settings["models"]["roles"]["critic"] = config.SONNET
    _result, _state, factory, _log = run_script(MOCK / "reg_regex_regular.json", settings=settings)
    assert factory.models["main"].model == settings["models"]["roles"]["main"]
    assert factory.models["critic"].model == config.SONNET
    assert factory.models["general"].model == config.HAIKU  # the cheap general-purpose role


def test_system_prompt_carries_run_parameters():
    text = A.main_system_prompt(example_settings(), template="exam_solution")
    assert "exam_solution" in text
    assert "5" in text  # per-task budget from settings.example.json
    assert "submit_report" in text and "write_todos" in text


# ---------------------------------------------------------------------------
# Budget middleware integration (middleware/budget.py)
# ---------------------------------------------------------------------------

def test_budget_middleware_charges_every_role():
    ledger = B.BudgetLedger(5.0, reserve_for_report_usd=0.3, per_subagent_max_usd=2.0, spend_log=None)
    with B.use_budget(ledger):
        result, _state, _factory, _log = run_script(MOCK / "reg_regex_regular.json", middleware=None,
                                                    record=False)
    assert result["status"] == "submitted"
    roles = {r.role for r in ledger.records}
    assert {"main", "ir_builder", "critic"} <= roles
    assert len(ledger.records) == 13
    assert 0 < ledger.spent_usd < 5.0 and not ledger.exhausted


def test_budget_refusal_ends_the_run_without_report():
    ledger = B.BudgetLedger(0.01, spend_log=None)
    with B.use_budget(ledger):
        result, _state, factory, _log = run_script(MOCK / "reg_regex_regular.json", middleware=None,
                                                   record=False)
    assert ledger.exhausted
    assert result["status"] == "budget_exhausted"
    assert result["report"] is None and result["confidence_cap"] == 0.40
    assert factory.models["main"].calls_made == 0  # the refused call never reached the model


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@pytest.fixture()
def runs_dir(tmp_path, monkeypatch):
    d = tmp_path / "runs"
    monkeypatch.setenv(B.RUNS_DIR_ENV, str(d))
    return d


def test_cli_mock_run_end_to_end(tmp_path, runs_dir, capsys):
    out = tmp_path / "out"
    rc = cli.main(["--mock-script", str(MOCK / "ll_simple_ll1.json"), "--save", str(out),
                   "--settings", str(SETTINGS_EXAMPLE), "--auto-approve"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "LL(1)" in captured.out
    data = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert data["status"] == "submitted" and data["mode"] == "mock"
    assert data["template"] == "short_verdict"
    assert data["human_review"].startswith("auto-approved")
    assert data["budget"]["calls"] > 0 and "no money spent" in data["budget"]["note"]
    md = (out / "result.md").read_text(encoding="utf-8")
    assert "LL(1)" in md and "ev-ll-table" in md
    assert not (runs_dir / B.SPEND_LOG_NAME).exists()  # mock runs never touch the day's log


def test_cli_task_text_overrides_script_task(tmp_path, runs_dir, capsys):
    out = tmp_path / "out"
    rc = cli.main(["Является ли (ab)*a|b регулярным?", "--mock-script", str(MOCK / "reg_regex_regular.json"),
                   "--save", str(out), "--settings", str(SETTINGS_EXAMPLE), "--template", "exam_solution",
                   "--auto-approve"])
    assert rc == 0, capsys.readouterr().err
    data = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert data["task"] == "Является ли (ab)*a|b регулярным?"
    assert data["template"] == "exam_solution"
    assert data["human_review"].startswith("auto-approved")


def test_cli_script_error_writes_partial_result(tmp_path, runs_dir, capsys):
    script = A.load_mock_script(MOCK / "reg_regex_regular.json")
    broken = copy.deepcopy(script)
    broken["models"]["main"] = broken["models"]["main"][:3]  # runs out before the report
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    rc = cli.main(["--mock-script", str(path), "--save", str(out), "--settings", str(SETTINGS_EXAMPLE),
                   "--auto-approve"])
    assert rc == 1
    data = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert data["status"] == "error" and "mock script" in data["reason"]
    assert {e["tool"] for e in data["evidence"]} >= {"regex_to_dfa"}  # partial state was kept
    assert "no report" in capsys.readouterr().err


def test_cli_without_script_or_live_is_refused(capsys):
    assert cli.main(["L = {a^n b^n}"]) == 2
    err = capsys.readouterr().err
    assert "--mock-script" in err and "--live" in err


def test_cli_live_without_key_is_refused(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["L = {a^n b^n}", "--live", "--settings", str(SETTINGS_EXAMPLE)]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_cli_live_and_script_exclude_each_other():
    with pytest.raises(SystemExit) as exc:
        cli.main(["x", "--live", "--mock-script", str(MOCK / "ll_simple_ll1.json")])
    assert exc.value.code == 2


@pytest.mark.parametrize("name, needle", [("task.pdf", "type_mismatch"), ("task.docx", "unsupported"),
                                          ("missing.txt", "not found")])
def test_cli_task_file_errors(tmp_path, name, needle, capsys):
    path = tmp_path / name
    if name != "missing.txt":
        path.write_bytes(b"x")
    rc = cli.main(["--file", str(path), "--mock-script", str(MOCK / "ll_simple_ll1.json")])
    assert rc == 2
    assert needle in capsys.readouterr().err


def test_cli_text_file_task(tmp_path, runs_dir, capsys):
    task = tmp_path / "task.md"
    task.write_text("S → aAb | bBa; A → aA | ε; B → bB | ε. LL(1)?", encoding="utf-8")
    out = tmp_path / "out"
    rc = cli.main(["--file", str(task), "--mock-script", str(MOCK / "ll_simple_ll1.json"),
                   "--save", str(out), "--settings", str(SETTINGS_EXAMPLE), "--auto-approve"])
    assert rc == 0, capsys.readouterr().err
    assert json.loads((out / "result.json").read_text(encoding="utf-8"))["task"].startswith("S → aAb")


def test_cli_bad_script_is_a_usage_error(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert cli.main(["x", "--mock-script", str(path), "--settings", str(SETTINGS_EXAMPLE)]) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_cli_budget_exhausted_status(tmp_path, runs_dir, capsys):
    settings = example_settings()
    settings["budget"].update({"per_task_usd": 0.01, "reserve_for_report_usd": 0.001})
    spath = tmp_path / "settings.json"
    spath.write_text(json.dumps(settings), encoding="utf-8")
    out = tmp_path / "out"
    rc = cli.main(["--mock-script", str(MOCK / "reg_regex_regular.json"), "--save", str(out),
                   "--settings", str(spath), "--auto-approve"])
    assert rc == 1
    data = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert data["status"] == "budget_exhausted"
    assert data["budget"]["exhausted"] is True
    assert "budget_exhausted" in capsys.readouterr().err
