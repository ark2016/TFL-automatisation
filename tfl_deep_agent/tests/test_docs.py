"""P8 docs: tfl_deep_agent/README.md, tfl_deep_agent/CLAUDE.md and the root
README.md / CLAUDE.md stay consistent with the code.

What is pinned: every CLI flag the docs name exists and every flag the parser
has is documented; the commands in the docs' ``bash`` blocks parse; the
settings defaults, templates, exit codes, run-directory files, environment
variables and the spend-log name match the code; the mock scripts and the
relative links the docs name exist; the root docs carry the TFL Deep Agent
section and rules.

The CLAUDE.md files are gitignored in this repository (``**/CLAUDE.md``), so
their tests skip when the file is not there (a fresh clone, CI).
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

import tfl_deep_agent
from tfl_deep_agent import cli, hitl, settings as settings_mod
from tfl_deep_agent.middleware import budget as budget_mod

PKG = Path(tfl_deep_agent.__file__).resolve().parent
ROOT = PKG.parent
DEEP_README = PKG / "README.md"
DEEP_CLAUDE = PKG / "CLAUDE.md"
ROOT_README = ROOT / "README.md"
ROOT_CLAUDE = ROOT / "CLAUDE.md"

TEMPLATES = ("full_proof", "exam_solution", "short_verdict", "lean_appendix")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body of the Markdown section whose heading line is ``heading``,
    up to the next heading of the same or a higher level."""
    lines = text.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    try:
        start = lines.index(heading)
    except ValueError:
        raise AssertionError(f"missing section {heading!r}") from None
    out = []
    for line in lines[start + 1:]:
        m = re.match(r"(#+) ", line)
        if m and len(m.group(1)) <= level and not _in_fence(out):
            break
        out.append(line)
    return "\n".join(out)


def _in_fence(lines: list[str]) -> bool:
    return sum(1 for ln in lines if ln.startswith("```")) % 2 == 1


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", text, flags=re.S)


def _deep_commands(text: str) -> list[list[str]]:
    """argv of every tfl-deep / python -m tfl_deep_agent command in the bash blocks
    (continuation lines joined, comments and env prefixes dropped)."""
    cmds = []
    for block in _bash_blocks(text):
        joined = re.sub(r"\\\n\s*", " ", block)
        for line in joined.splitlines():
            line = line.split("  #")[0].strip()
            if not line or line.startswith("#") or not ("tfl-deep" in line or "tfl_deep_agent" in line):
                continue
            argv = shlex.split(line, posix=True)
            while argv and re.fullmatch(r"[A-Z_]+=\S*", argv[0]):
                argv.pop(0)
            if not argv:
                continue
            if argv[0] == "tfl-deep":
                cmds.append(argv[1:])
            elif len(argv) >= 3 and argv[0].endswith("python") and argv[1:3] == ["-m", "tfl_deep_agent"]:
                cmds.append(argv[3:])
    return cmds


def _parser_flags() -> set[str]:
    flags = set()
    for action in cli.build_parser()._actions:
        flags.update(s for s in action.option_strings if s.startswith("--"))
    flags.discard("--help")
    return flags


# ---------------------------------------------------------------------------
# tfl_deep_agent/README.md
# ---------------------------------------------------------------------------

def test_readme_cli_flags_match_the_parser():
    cli_section = _section(_text(DEEP_README), "## CLI")
    documented = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", cli_section))
    parser = _parser_flags()
    assert documented - parser == set(), "the README names flags the CLI does not have"
    assert parser - documented == set(), "CLI flags missing from the README's CLI section"


@pytest.mark.parametrize("doc", [DEEP_README, ROOT_README], ids=["deep_readme", "root_readme"])
def test_documented_commands_parse(doc):
    cmds = _deep_commands(_text(doc))
    assert cmds, f"no tfl-deep commands found in {doc.name}"
    parser = cli.build_parser()
    for argv in cmds:
        args = parser.parse_args(argv)  # SystemExit on an unknown flag / bad choice
        if args.mock_script:
            assert (ROOT / args.mock_script).is_file(), args.mock_script
        if args.resume:
            assert args.decision or args.message or args.abandon, argv
        assert not (args.live and args.mock_script), argv


def test_readme_settings_table_matches_the_defaults():
    table = _section(_text(DEEP_README), "## Settings")
    defaults = settings_mod.DEFAULTS
    for section in ("budget", "limits", "hitl", "intake"):
        row = next((ln for ln in table.splitlines() if ln.startswith(f"| `{section}` |")), None)
        assert row, f"settings table has no row for {section}"
        for key, value in defaults[section].items():
            if isinstance(value, bool):
                shown = "true" if value else "false"
            elif isinstance(value, float) and section == "budget":
                shown = f"{value:.2f}"
            else:
                shown = str(value)
            assert re.search(rf"`{key}` {re.escape(shown)}\b", row), f"{section}.{key} should read {shown}"
    assert "`opus_needs_reason` true" in table and defaults["models"]["opus_needs_reason"] is True


def test_root_docs_state_the_budget_defaults():
    b = settings_mod.DEFAULTS["budget"]
    section = _section(_text(ROOT_README), "## TFL Deep Agent")
    for value in (b["per_task_usd"], b["per_day_usd"], b["per_subagent_max_usd"], b["reserve_for_report_usd"]):
        assert f"${value:.2f}" in section


def test_readme_names_the_templates_and_roles():
    from tfl_deep_agent import agent, config, roles

    text = _text(DEEP_README)
    assert tuple(agent.TEMPLATES) == TEMPLATES
    for t in TEMPLATES:
        assert f"`{t}`" in text
    models = _section(text, "### Models")
    for key, role in roles.ROLES.items():
        assert f"| `{key}` | `{role.name}` |" in models, key
    assert "| `main` | — | Opus |" in models
    tier = {config.OPUS: "Opus", config.SONNET: "Sonnet", config.HAIKU: "Haiku"}
    for key, model in config.MODELS.items():
        row = next(ln for ln in models.splitlines() if ln.startswith(f"| `{key}` |"))
        assert f"| {tier[model]} |" in row, key


def test_readme_exit_codes_match_the_code():
    text = _text(DEEP_README)
    assert (hitl.EXIT_SUBMITTED, hitl.EXIT_NO_REPORT, cli.EXIT_USAGE, hitl.EXIT_AWAITING) == (0, 1, 2, 3)
    assert re.search(r"`0` the report was accepted, `1` the run ended without a report", text)
    assert "`2` usage or setup error, `3` waiting for a human decision" in text


def test_readme_run_directory_files_match_hitl():
    table = _section(_text(DEEP_README), "### The run directory")
    for name in (hitl.RUN_FILE, hitl.CHECKPOINT_FILE, hitl.MOCK_SCRIPT_FILE, hitl.MOCK_CURSORS_FILE,
                 hitl.CARD_FILE, "evidence.jsonl", "progress.jsonl", f"{hitl.INPUT_DIR}/task.*",
                 "result.{json,md,html}"):
        assert f"`{name}`" in table, name


def test_docs_name_the_real_env_vars_and_spend_log():
    deep = _text(DEEP_README)
    root = _text(ROOT_README)
    for name in (budget_mod.RUNS_DIR_ENV, settings_mod.SETTINGS_PATH_ENV, "TFL_MODEL_OVERRIDE",
                 "TFL_MAX_CONCURRENCY", budget_mod.SPEND_LOG_NAME):
        assert name in deep, name
    for name in (budget_mod.RUNS_DIR_ENV, settings_mod.SETTINGS_PATH_ENV, budget_mod.SPEND_LOG_NAME):
        assert name in root, name
    assert settings_mod.DEFAULT_SETTINGS_PATH.name == "settings.json"
    assert "tfl_deep_agent/settings.json" in deep + root


def test_readme_lists_every_shipped_mock_script():
    text = _text(DEEP_README)
    shipped = sorted(p.name for p in (PKG / "examples" / "mock").glob("*.json"))
    assert shipped
    for name in shipped:
        assert f"| `{name}` |" in text, name
    for name in re.findall(r"\| `([a-z0-9_]+\.json)` \|", _section(text, "## Quick start (mock mode, free)")):
        assert name in shipped, name


def test_readme_relative_links_resolve():
    text = _text(DEEP_README)
    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
        if target.startswith(("http://", "https://")):
            continue
        assert (PKG / target).exists(), target


def test_readme_anchor_links_resolve():
    text = _text(DEEP_README)
    slugs = set()
    for heading in re.findall(r"^#+ (.+)$", text, flags=re.M):
        slugs.add(re.sub(r"[^\w\- ]", "", heading.lower()).strip().replace(" ", "-"))
    for anchor in re.findall(r"\]\(#([^)]+)\)", text):
        assert anchor in slugs, anchor


def test_readme_layout_names_existing_paths():
    layout = _section(_text(DEEP_README), "## Package layout")
    names = re.findall(r"[├└]── ([^#]+?)\s+#", layout)
    assert names
    for entry in names:
        for part in entry.split(","):
            part = part.strip()
            assert (PKG / part.rstrip("/")).exists(), part


def test_readme_sandbox_and_lean_facts_match_the_code():
    pytest.importorskip("deepagents")
    from tfl_deep_agent.sandbox import docker_backend

    text = _section(_text(DEEP_README), "## Sandbox and Lean")
    assert "tfl-sandbox:latest" in text
    assert getattr(docker_backend, "DEFAULT_IMAGE", "tfl-sandbox:latest") == "tfl-sandbox:latest"
    assert "tfl-lean4" in text
    for script in ("build.ps1", "build.sh", "Dockerfile.sandbox"):
        assert (PKG / "sandbox" / script).is_file()


def test_readme_says_the_runners_open_no_sandbox_while_that_is_true():
    """The README states that CLI / UI runs have no coder. If hitl starts
    passing ``sandbox=`` to ``create_tfl_agent``, this test fails so the docs
    get updated together with the behaviour."""
    source = _text(PKG / "hitl.py")
    runner_opens_sandbox = re.search(r"create_tfl_agent\([^)]*sandbox=", source, flags=re.S) is not None
    for doc in (DEEP_README, ROOT_README):
        claims_no_sandbox = "do not open a sandbox yet" in _text(doc)
        assert claims_no_sandbox != runner_opens_sandbox, doc.name


def test_docs_state_the_derived_max_tokens():
    """Both READMEs say that max_tokens is derived from the remaining budget
    with the 4096-token floor (user decision 2026-11-08), and no longer
    describe the closed gap (main's fixed $1.28 worst case vs the reserve)."""
    floor = budget_mod.MIN_OUTPUT_TOKENS
    deep = _section(_text(DEEP_README), "### Budget semantics")
    root = _section(_text(ROOT_README), "## TFL Deep Agent")
    for name, text in (("tfl_deep_agent/README.md", deep), ("README.md", root)):
        assert f"{floor}-token floor" in text, name
        assert "max_tokens" in text and "derived" in text, name
        assert "Known gap" not in text and "still open" not in text, name
    assert "floor((room − worst-case input cost) / output price)" in deep
    assert "the reserve" in deep and "allocation" in deep


def test_eval_section_matches_the_tfl_eval_cli():
    runners = pytest.importorskip("tfl_eval.runners")
    from tfl_eval import cli as eval_cli

    if not hasattr(runners, "DEEP_SYSTEM"):
        pytest.skip("tfl_eval has no deep adapter in this checkout")
    text = _section(_text(DEEP_README), "## Eval")
    assert f"--systems {runners.DEEP_SYSTEM}" in text
    assert runners.DEEP_SYSTEM not in runners.SYSTEMS  # documented as opt-in
    flags = {s for a in eval_cli.build_arg_parser()._actions for s in a.option_strings}
    for flag in set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", text)):
        assert flag in flags, flag
    assert runners.DEEP_MOCK_DIR == PKG / "examples" / "mock" / "eval"
    assert "examples/mock/eval/<id>.json" in text


# ---------------------------------------------------------------------------
# root README.md
# ---------------------------------------------------------------------------

def test_root_readme_has_the_deep_agent_section():
    text = _text(ROOT_README)
    section = _section(text, "## TFL Deep Agent")
    for needle in ("tfl_deep_agent/README.md", "tfl_deep_agent/PLAN.md", "[deep", "--mock-script", "--resume",
                   "HITL", "Deep Agent", "docs/VERDICT_POLICY.md", "TFL_MODEL_OVERRIDE=claude-haiku-5-5"):
        assert needle in section, needle
    for t in TEMPLATES:
        assert f"`{t}`" in section
    layout = _section(text, "## Repository layout")
    assert "tfl_deep_agent/" in layout


# ---------------------------------------------------------------------------
# CLAUDE.md files (gitignored: present only in a working copy that has them)
# ---------------------------------------------------------------------------

def _claude(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path.relative_to(ROOT)} is not present (CLAUDE.md files are gitignored)")
    return _text(path)


def test_root_claude_md_covers_the_deep_agent():
    text = _claude(ROOT_CLAUDE)
    projects = _section(text, "## Projects")
    assert "`tfl_deep_agent/`" in projects
    assert re.search(r"`tfl_deep_agent` imports from the four pipeline packages", projects)
    assert "never import it" in projects
    models = _section(text, "## Models and LLM calls")
    assert "TFL Deep Agent" in models and "tfl-deep --live" in models
    assert "tfl_deep_agent/settings.json" in models and budget_mod.SPEND_LOG_NAME in models
    assert "tfl_deep_agent/config.py" in models
    commands = _section(text, "## Commands")
    deep_lines = [ln for ln in commands.splitlines() if "tfl_deep_agent" in ln or ln.startswith("    [")]
    named = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", "\n".join(deep_lines)))
    assert {"--mock-script", "--resume", "--decision"} <= named
    assert named <= _parser_flags(), named - _parser_flags()


def test_deep_claude_md_paths_exist():
    text = _claude(DEEP_CLAUDE)
    for rel in re.findall(r"`((?:[a-z_]+/)*[a-z_]+\.(?:py|md|json))`", text):
        if rel == "settings.json":  # the user's gitignored settings file
            continue
        candidates = [PKG / rel, ROOT / rel]
        assert any(p.exists() for p in candidates), rel
