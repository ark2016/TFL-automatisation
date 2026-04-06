"""
Tests for the DCFL renderer module.

Covers render_json, render_markdown, render_html, render_to_file, render_all,
and guard tests for None/empty edge cases.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dcfl_system.orchestrator import run_pipeline, MockRunner
from dcfl_system.renderer import (
    render_json,
    render_markdown,
    render_html,
    render_to_file,
    render_all,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MOCK_DIR = EXAMPLES_DIR / "mock"

TASK_FILES = [
    "task_wvaavRwR",
    "task_u1au2_u3au4",
    "task_anb_cnbn",
    "task_grammar_aSSb",
]


def _load_ir(task_filename: str) -> dict:
    path = EXAMPLES_DIR / f"{task_filename}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _run_task(task_filename: str) -> dict:
    ir = _load_ir(task_filename)
    mock = MockRunner(str(MOCK_DIR), ir["task_id"])
    return run_pipeline(ir, mock_runner=mock)


@pytest.fixture(params=TASK_FILES)
def pipeline_result(request) -> dict:
    """Run pipeline for each task and return the result."""
    return _run_task(request.param)


@pytest.fixture
def sample_result() -> dict:
    """A single result for non-parametrized tests."""
    return _run_task("task_wvaavRwR")


# ---------------------------------------------------------------------------
# render_json
# ---------------------------------------------------------------------------

def test_render_json_valid(pipeline_result):
    output = render_json(pipeline_result)
    parsed = json.loads(output)
    assert isinstance(parsed, dict)
    assert "verdict" in parsed


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

def test_render_markdown_contains_verdict(pipeline_result):
    md = render_markdown(pipeline_result)
    assert isinstance(md, str)
    assert "Вердикт" in md


def test_render_markdown_contains_source_text(sample_result):
    md = render_markdown(sample_result)
    source = sample_result.get("source_text", "")
    if source:
        assert source[:30] in md


def test_render_markdown_agent_table(pipeline_result):
    """Markdown renders agent table when agent_results key is present."""
    # The pipeline result uses 'specialist_outputs', not 'agent_results'.
    # Inject agent_results to exercise the table branch.
    result_with_agents = dict(pipeline_result)
    specialist = pipeline_result.get("specialist_outputs") or {}
    result_with_agents["agent_results"] = specialist
    md = render_markdown(result_with_agents)
    if specialist:
        assert "\u0410\u0433\u0435\u043d\u0442" in md


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------

def test_render_html_structure(pipeline_result):
    html = render_html(pipeline_result)
    assert "<html" in html
    assert "</html>" in html


def test_render_html_has_tabs(pipeline_result):
    html = render_html(pipeline_result)
    assert "s-tab" in html


def test_render_html_has_css(pipeline_result):
    html = render_html(pipeline_result)
    assert "<style>" in html


# ---------------------------------------------------------------------------
# render_to_file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["json", "md", "html"])
def test_render_to_file_creates_file(tmp_path, sample_result, fmt):
    out_file = tmp_path / f"test_output.{fmt}"
    render_to_file(sample_result, str(out_file), fmt)
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert len(content) > 0


# ---------------------------------------------------------------------------
# render_all
# ---------------------------------------------------------------------------

def test_render_all_creates_three_files(tmp_path, sample_result):
    paths = render_all(sample_result, str(tmp_path), "test_task")
    assert "json" in paths
    assert "md" in paths
    assert "html" in paths
    for fmt, fpath in paths.items():
        p = Path(fpath)
        assert p.exists(), f"{fmt} file not created: {fpath}"
        assert p.stat().st_size > 0, f"{fmt} file is empty"


# ---------------------------------------------------------------------------
# Guard tests: edge cases
# ---------------------------------------------------------------------------

def test_render_proof_sketch_none(sample_result):
    """Result with proof_sketch=None should not crash any renderer."""
    result = dict(sample_result)
    result["proof_sketch"] = None
    # All three renderers should complete without error
    render_json(result)
    render_markdown(result)
    render_html(result)


def test_render_empty_agent_results():
    """Result with empty agent_results should not crash any renderer."""
    result = {
        "verdict": "inconclusive",
        "confidence": 0.0,
        "source_text": "test",
        "agent_results": {},
        "errors": [],
    }
    render_json(result)
    md = render_markdown(result)
    assert "Вердикт" in md
    html = render_html(result)
    assert "<html" in html


def test_render_none_result():
    """None result should produce safe output, not crash."""
    json_out = render_json(None)
    assert "error" in json_out
    md_out = render_markdown(None)
    assert isinstance(md_out, str)
    html_out = render_html(None)
    assert "<html" in html_out or "<body" in html_out


# ---------------------------------------------------------------------------
# All 4 tasks produce renderable output
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_filename", TASK_FILES)
def test_all_tasks_render_all_formats(tmp_path, task_filename):
    result = _run_task(task_filename)
    paths = render_all(result, str(tmp_path), task_filename)
    for fmt, fpath in paths.items():
        p = Path(fpath)
        assert p.exists()
        assert p.stat().st_size > 0
