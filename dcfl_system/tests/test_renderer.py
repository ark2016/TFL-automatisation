"""
Tests for the DCFL renderer module.

Covers render_json, render_markdown, render_html, render_to_file, render_all,
and guard tests for None/empty edge cases.
"""
from __future__ import annotations

import functools
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


@functools.lru_cache(maxsize=None)
def _run_task(task_filename: str) -> dict:
    """Run the (deterministic, mock-backed) pipeline once per task and reuse
    the result across every test that just reads it (read-only in this
    module, including the shallow `dict(...)` copies used before mutating a
    guard case -- see test_render_proof_sketch_none). task_grammar_aSSb's
    certificate DPDA (345 states, 5835 transitions) makes a fresh run take
    ~25-55s, and without this cache ~7 call sites below each paid that cost
    separately."""
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


# ---------------------------------------------------------------------------
# dcfl_pumping word_instances (docs/VERDICT_POLICY.md §4) rendering
# ---------------------------------------------------------------------------

def _pumping_sketch_with_instances() -> dict:
    return {
        "kind": "dcfl_pumping",
        "pumping_length": "p",
        "word_w": "aⁿbⁿ",
        "word_w_prime": "aⁿb²ⁿ",
        "common_prefix_x": "aⁿbⁿ⁻¹",
        "suffix_y": "b",
        "suffix_z": "bⁿ⁺¹",
        "first_letters_match": "обе 'b'",
        "condition1_argument": "...",
        "condition2_argument": "...",
        "word_instances": {
            "2": {"w": "aaabbb", "w_prime": "aaabbbbbb", "x_length": 5},
            "3": {"w": "aaaabbbb", "w_prime": "aaaabbbbbbbb", "x_length": 7},
        },
    }


def test_render_markdown_shows_word_instances():
    from dcfl_system.renderer import _render_proof_sketch_md
    md = _render_proof_sketch_md(_pumping_sketch_with_instances(), "dcfl_pumping")
    assert "Конкретные инстансы" in md
    assert "aaabbb" in md and "aaabbbbbb" in md
    assert "aaaabbbb" in md and "aaaabbbbbbbb" in md
    assert "p=2" in md and "p=3" in md


def test_render_html_shows_word_instances():
    from dcfl_system.renderer import _render_proof_sketch_html
    html = _render_proof_sketch_html(_pumping_sketch_with_instances(), "dcfl_pumping")
    assert "Конкретные инстансы" in html
    assert "aaabbb" in html and "aaabbbbbb" in html


def test_panel_dcfl_pumping_shows_word_instances():
    from dcfl_system.renderer import _panel_dcfl_pumping
    output = {
        "agent_name": "dcfl_pumping", "status": "success", "verdict": "non_dcfl",
        "confidence": 0.85, "proof_sketch": _pumping_sketch_with_instances(),
        "evidence": [], "errors": [],
    }
    panel = _panel_dcfl_pumping(output)
    assert "Конкретные инстансы" in panel
    assert "aaaabbbb" in panel


# ---------------------------------------------------------------------------
# result["formalization"] (Lean block)
# ---------------------------------------------------------------------------

LEAN_BLOCK = {
    "status": "proved", "direction": "dcfl", "elapsed": 12.3,
    "statement": {"imports": "import Mathlib", "alphabet_decl": "inductive Letter | a | b",
                  "language_decl": "def L : Language Letter := ∅",
                  "theorem_decl": "theorem tfl_main : is_DCF L", "name": "tfl_main"},
    "proof_body": "exact FANCY_PROOF_BODY", "axioms": ["propext", "Quot.sound"],
    "attempts": [{"attempt": 1, "status": "error", "errors": ["boom"]},
                 {"attempt": 2, "status": "proved", "errors": []}],
    "errors": [],
}


def test_markdown_shows_lean_block(sample_result):
    md = render_markdown({**sample_result, "formalization": LEAN_BLOCK})
    assert "Формализация (Lean 4)" in md
    assert "Статус: proved" in md and "Направление: dcfl" in md and "Попыток: 2" in md
    assert "theorem tfl_main : is_DCF L" in md
    assert "exact FANCY_PROOF_BODY" in md
    assert "`propext`" in md and "`Quot.sound`" in md


def test_html_shows_lean_block_escaped(sample_result):
    fm = {**LEAN_BLOCK, "proof_body": "exact <b>x</b>"}
    html = render_html({**sample_result, "formalization": fm})
    assert "Формализация (Lean 4)" in html
    assert "exact &lt;b&gt;x&lt;/b&gt;" in html
    assert "exact <b>x</b>" not in html


@pytest.mark.parametrize("fm", [None, {}])
def test_no_lean_block_when_absent(sample_result, fm):
    assert "Формализация (Lean 4)" not in render_markdown({**sample_result, "formalization": fm})
    assert "Формализация (Lean 4)" not in render_html({**sample_result, "formalization": fm})
