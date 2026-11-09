"""P0 skeleton: package layout, packaging metadata, CLI contract."""

from __future__ import annotations

import importlib
import json
import pkgutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import tfl_deep_agent
from tfl_deep_agent import cli

PKG = Path(tfl_deep_agent.__file__).parent
ROOT = PKG.parent

# architecture.html, "Структура пакета"
EXPECTED_MODULES = [
    "cli", "config", "settings", "agent", "roles", "models",
    "orchestration.spawn", "orchestration.workflow", "orchestration.model_policy",
    "intake.files",
    "tools.ir", "tools.words", "tools.reg", "tools.cfl", "tools.dcfl", "tools.ll",
    "tools.theory", "tools.lean", "tools.report",
    "evidence.ledger", "evidence.policy",
    "middleware.budget", "middleware.evidence", "middleware.concurrency",
    "sandbox.docker_backend",
    "reports.render",
]
EXPECTED_FILES = [
    "settings.example.json", "README.md",
    "sandbox/Dockerfile.sandbox", "sandbox/build.ps1",
    *(f"prompts/{p}.md" for p in
      ("main", "intake", "ir_builder", "prover", "critic", "lean", "coder")),
]
EXPECTED_SKILL_DIRS = ["reg", "cfl", "dcfl", "ll", "lean", "report-templates"]


@pytest.mark.parametrize("name", EXPECTED_MODULES)
def test_module_imports(name):
    if name == "sandbox.docker_backend":  # the one listed module that needs the [deep] extra at import time
        pytest.importorskip("deepagents")
    mod = importlib.import_module(f"tfl_deep_agent.{name}")
    assert mod.__doc__, f"{name} has no docstring"


def test_every_package_module_imports():
    pytest.importorskip("deepagents")
    for info in pkgutil.walk_packages([str(PKG)], prefix="tfl_deep_agent."):
        if ".tests" in info.name or info.name == "tfl_deep_agent.__main__":
            continue
        importlib.import_module(info.name)


@pytest.mark.parametrize("rel", EXPECTED_FILES)
def test_expected_file_exists(rel):
    assert (PKG / rel).is_file(), rel


@pytest.mark.parametrize("d", EXPECTED_SKILL_DIRS)
def test_skill_dirs_exist(d):
    assert (PKG / "skills" / d).is_dir()


def test_settings_example_budget_matches_plan():
    s = json.loads((PKG / "settings.example.json").read_text(encoding="utf-8"))
    # per_task_usd 10.00: user decision 2026-11-08 (PLAN.md had 5.00)
    assert s["budget"] == {"per_task_usd": 10.00, "per_day_usd": 20.00,
                           "per_subagent_max_usd": 2.00, "reserve_for_report_usd": 1.00}
    assert s["hitl"] == {"formalization": True, "report": True, "execute": False}
    assert s["models"]["opus_needs_reason"] is True


def test_pyproject_deep_extra_script_packages_and_testpaths():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deep = data["project"]["optional-dependencies"]["deep"]
    assert "deepagents==0.7.34" in deep
    assert "langchain>=1.4,<2" in deep
    names = {d.split("=")[0].split(">")[0].split("<")[0] for d in deep}
    assert {"deepagents", "langchain", "langchain-anthropic",
            "langgraph-checkpoint-sqlite", "pypdf"} <= names
    assert data["project"]["scripts"]["tfl-deep"] == "tfl_deep_agent.cli:main"
    assert "tfl_deep_agent*" in data["tool"]["setuptools"]["packages"]["find"]["include"]
    pkg_data = data["tool"]["setuptools"]["package-data"]["tfl_deep_agent"]
    for pattern in ("prompts/*.md", "skills/**/*", "templates/**/*", "examples/**/*"):
        assert pattern in pkg_data
    assert "tfl_deep_agent/tests" in data["tool"]["pytest"]["ini_options"]["testpaths"]


def test_gitignore_covers_deep_venv_and_settings():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".venv-deep/" in lines
    assert "tfl_deep_agent/settings.json" in lines


def test_import_direction_pipelines_never_import_deep_agent():
    for pkg in ("agent_system", "cfl_system", "dcfl_system", "ll_system"):
        for path in (ROOT / pkg).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "tfl_deep_agent" not in text, path


def test_cli_requires_a_task(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        cli.main(["some text", "--file", "x.pdf"])


def test_cli_without_mock_script_or_live_is_refused(capsys):
    assert cli.main(["L = {a^n b^n}", "--save", "out"]) == 2
    assert "--mock-script" in capsys.readouterr().err


def test_cli_parser_contract():
    args = cli.build_parser().parse_args(
        ["--file", "t.pdf", "--save", "d", "--live", "--auto-approve", "--template", "exam_solution"])
    assert (args.file, args.save, args.live, args.auto_approve, args.template) == (
        "t.pdf", "d", True, True, "exam_solution")
    assert cli.build_parser().parse_args(["x"]).live is False


def test_python_dash_m_help():
    r = subprocess.run([sys.executable, "-m", "tfl_deep_agent", "--help"],
                       capture_output=True, text=True, timeout=60, cwd=ROOT, check=False)
    assert r.returncode == 0
    assert "tfl-deep" in r.stdout and "--auto-approve" in r.stdout
