"""Static-frontend checks: no inline scripts, ids wired, no run-timeout wording, valid JS syntax."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")


def test_index_has_no_inline_scripts():
    html = re.sub(r"<!--.*?-->", "", INDEX, flags=re.S)
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.S | re.I):
        assert "src=" in m.group(1), "inline <script> is not allowed (CSP script-src 'self')"
        assert not m.group(2).strip()
    assert not re.search(r"\son\w+\s*=", html), "inline event handler attribute"


def test_every_dom_id_used_by_app_exists_in_index():
    ids = set(re.findall(r'\bid="([^"]+)"', INDEX))
    used = set(re.findall(r"\$\('([^']+)'\)", APP)) | set(re.findall(r"getElementById\('([^']+)'\)", APP))
    assert used - ids == set()


def test_lab_css_is_linked_and_exists():
    assert '/static/lab.css' in INDEX
    assert (STATIC / "lab.css").is_file()


@pytest.mark.parametrize("name", ["app.js", "index.html", "lab.css"])
def test_no_run_timeout_wording(name):
    text = (STATIC / name).read_text(encoding="utf-8")
    assert not re.search(r"run[-_ ]?timeout|timed out|'timeout'|\"timeout\"", text, flags=re.I)


def test_progress_and_settings_surface_present():
    for needle in ('data-tab="progress"', 'id="run-list"', 'id="btn-formalize"',
                   'id="btn-settings"', 'id="est-live"', 'id="settings-back"'):
        assert needle in INDEX
    for api in ("/runs", "/estimate", "/settings", "/formalize"):
        assert api in APP


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_js_parses():
    r = subprocess.run(["node", "--check", str(STATIC / "app.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------
# Progress tab model (buildModel), run under node on synthetic events
# ---------------------------------------------------------------------------

def _model_js() -> str:
    """The pure part of app.js the Progress tab uses (helpers + PHASES ..
    buildModel), sliced out of the script so it can run without a DOM."""
    helpers = APP[APP.index("function num(v)"):APP.index("function fmtUsd(v)")]
    model = APP[APP.index("const PHASES = ["):APP.index("function rowStatsText(")]
    return helpers + "\n" + model


def _build_model(events: list) -> dict:
    import json
    script = _model_js() + (
        "\nconst events = JSON.parse(process.argv[1]);\n"
        "const m = buildModel(events);\n"
        "const out = {};\n"
        "for (const k of Object.keys(m.phases)) out[k] = m.phases[k].rows.map(r => ({\n"
        "  node: r.node, sub: r.sub, state: r.state, calls: r.calls, kind: r.kind,\n"
        "  cost: Math.round(r.cost * 1e6) / 1e6, inTok: r.inTok, outTok: r.outTok}));\n"
        "console.log(JSON.stringify({phases: out, last: m.lastActiveKey}));\n")
    r = subprocess.run(["node", "-e", script, json.dumps(events)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _ev(event, node, payload=None, calls=0, cost=0.0):
    return {"ts": "2026-09-29T10:00:00.000+00:00", "event": event, "node": node,
            "payload": payload or {},
            "usage": {"calls": calls, "input_tokens": calls * 100, "output_tokens": calls * 10,
                      "estimated_cost_usd": cost}}


def _call(agent, calls, cost, price):
    """The two events one live call writes: phase start, then phase done."""
    return [_ev("llm_call", agent, {"phase": "start", "agent": agent}, calls - 1, round(cost - price, 6)),
            _ev("llm_call", agent, {"phase": "done", "agent": agent, "model": "m",
                                    "input_tokens": 100, "output_tokens": 10,
                                    "estimated_cost_usd": price}, calls, cost)]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_progress_calls_land_in_their_node_section_and_count_once():
    events = [
        _ev("node_start", "run_specialist_node", {"agent": "pumping_cfl"}),
        _ev("node_start", "run_specialist_node", {"agent": "closure_reduction"}),
        *_call("pumping_cfl", 1, 0.10, 0.10),
        *_call("closure_reduction", 2, 0.30, 0.20),
        _ev("node_done", "run_specialist_node", {"agent": "pumping_cfl"}, 2, 0.30),
        _ev("node_done", "run_specialist_node", {"agent": "closure_reduction"}, 2, 0.30),
        _ev("node_start", "run_reasoning_node"),
        *_call("reasoning", 3, 0.80, 0.50),
        _ev("node_done", "run_reasoning_node", {}, 3, 0.80),
    ]
    m = _build_model(events)["phases"]
    spec = {(r["sub"], r["calls"], r["cost"]) for r in m["specialists"]}
    assert spec == {("pumping_cfl", 1, 0.10), ("closure_reduction", 1, 0.20)}   # not "oracle", one call each
    assert all(r["state"] == "done" for r in m["specialists"])
    assert m["oracle"] == [] and m["hypothesis"] == []
    assert [(r["node"], r["calls"], r["cost"]) for r in m["verdict"]] == [("run_reasoning_node", 1, 0.50)]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_progress_call_outside_a_node_gets_one_row_by_agent():
    events = [_ev("node_start", "formalize"),
              *_call("lean_formalizer", 1, 0.7, 0.7),
              _ev("node_done", "formalize", {}, 1, 0.7)]
    m = _build_model(events)["phases"]
    assert [(r["node"], r["calls"], r["state"]) for r in m["formalization"]] == [("formalize", 1, "done")]
    # no node open at all: the call has a row of its own, classified by agent, counted once
    events = _call("input_parser", 1, 0.1, 0.1) + _call("cfg_builder", 2, 0.3, 0.2)
    m = _build_model(events)["phases"]
    assert [(r["sub"], r["kind"], r["calls"], r["state"]) for r in m["hypothesis"]] == [("input_parser", "llm", 1, "done")]
    assert [(r["sub"], r["kind"], r["calls"], r["state"]) for r in m["specialists"]] == [("cfg_builder", "llm", 1, "done")]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_progress_started_call_shows_running_without_counting():
    events = [_ev("node_start", "run_reasoning_node"),
              _ev("llm_call", "reasoning", {"phase": "start", "agent": "reasoning"}, 0, 0.0)]
    row = _build_model(events)["phases"]["verdict"][0]
    assert row["state"] == "running" and row["calls"] == 0 and row["cost"] == 0


def test_mock_source_warning_next_to_formalize_button():
    assert 'id="formalize-mock-warning"' in INDEX
    assert INDEX.index('id="formalize-mock-warning"') < INDEX.index('id="btn-formalize"')
    assert "mock mode" in INDEX and "paid API calls" in INDEX
    assert "mockFormalizeWarn" in APP and "source_mode" in APP
