"""P2: skills/dcfl -- the DCFL skill text (SKILL.md, traps.md, dpda.md).

Checks that the skill loads through deepagents' SkillsMiddleware parser, that every
quoted statement is a verbatim excerpt of its source (docs/THEORY.md, docs/VERDICT_POLICY.md,
the tz spec), that cited THEORY.md sections exist with the status the skill claims, that every
tool name it mentions is registered, and that the worked DPDA passes the R2' tool check.

No LLM, no network, no Docker.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills, _parse_skill_metadata

from tfl_deep_agent.tools.base import REGISTRY, load_groups
from tfl_deep_agent.tools.theory import load_theory

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "tfl_deep_agent" / "skills" / "dcfl"
SKILL_FILES = ("SKILL.md", "traps.md", "dpda.md")

SOURCES = {
    "THEORY.md": ROOT / "docs" / "THEORY.md",
    "VERDICT_POLICY.md": ROOT / "docs" / "VERDICT_POLICY.md",
    "tz_dcfl_agent_system.md": ROOT / "dcfl_system" / "tz_dcfl_agent_system.md",
}

# Tools the skill names that later phases register (P3 orchestration, P6 Lean),
# plus deepagents built-ins.
FUTURE_OR_BUILTIN_TOOLS = {"lean_statement", "lean_check", "read_file"}


def _text(name: str) -> str:
    return (SKILL_DIR / name).read_text(encoding="utf-8")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _quotes(text: str) -> list[tuple[str, str]]:
    """(source label, quoted text) for every '<Source> ... (verbatim):' + blockquote."""
    out: list[tuple[str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        head = lines[i].strip()
        if head.endswith("(verbatim):"):
            label = next((k for k in SOURCES if head.startswith(k)), None)
            assert label, f"unknown quote source in: {head}"
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            block: list[str] = []
            while j < len(lines) and lines[j].lstrip().startswith(">"):
                block.append(re.sub(r"^\s*>\s?", "", lines[j]))
                j += 1
            assert block, f"no blockquote after: {head}"
            out.append((label, "\n".join(block)))
            i = j
        else:
            i += 1
    return out


@pytest.fixture(scope="module", autouse=True)
def _groups():
    load_groups()


# ---------------------------------------------------------------------------
# frontmatter / SkillsMiddleware
# ---------------------------------------------------------------------------

def test_frontmatter_parses_with_deepagents():
    meta = _parse_skill_metadata(_text("SKILL.md"), "/skills/dcfl/SKILL.md", "dcfl")
    assert meta is not None
    assert meta["name"] == "dcfl"
    assert 100 < len(meta["description"]) <= 1024
    for kw in ("DCFL", "ДМПА", "classify_dcfl", "prove_non_dcfl", "dpda_vs_ir"):
        assert kw in meta["description"], kw
    assert set(meta["allowed_tools"]) >= {"dpda_run", "dpda_vs_ir", "validate_ir", "check_membership"}
    assert "include_tools" not in (meta.get("metadata") or {})


def test_skill_listed_by_filesystem_backend():
    backend = FilesystemBackend(root_dir=str(ROOT / "tfl_deep_agent"), virtual_mode=True)
    skills = _list_skills(backend, "/skills")
    names = {s["name"] for s in skills}
    assert "dcfl" in names
    (dcfl,) = [s for s in skills if s["name"] == "dcfl"]
    assert dcfl["path"].replace("\\", "/").endswith("/skills/dcfl/SKILL.md")


def test_allowed_tools_are_registered():
    meta = _parse_skill_metadata(_text("SKILL.md"), "/skills/dcfl/SKILL.md", "dcfl")
    names = set(REGISTRY.names())
    missing = [t for t in meta["allowed_tools"] if t not in names]
    assert not missing, missing


def test_supporting_files_are_referenced():
    body = _text("SKILL.md")
    for f in SKILL_FILES[1:]:
        assert f"/skills/dcfl/{f}" in body, f


# ---------------------------------------------------------------------------
# verbatim quotes and section citations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname", SKILL_FILES)
def test_every_quote_is_verbatim(fname):
    quotes = _quotes(_text(fname))
    assert quotes, f"{fname} quotes nothing"
    sources = {k: _norm(p.read_text(encoding="utf-8")) for k, p in SOURCES.items()}
    for label, q in quotes:
        assert _norm(q) in sources[label], f"{fname}: not verbatim in {label}:\n{q[:200]}"


def test_skill_quotes_cover_the_methods():
    quoted = " ".join(q for _, q in _quotes(_text("SKILL.md")))
    for marker in ("Теорема 4.7.4 [Sh]", "L_$ = {x$y | x ∈ L, xy ∈ L}", "(1) — накачка внутри префикса",
                   "Существенно неоднозначный", "Замкнуты:", "для каждого", "⋃_k LL(k) ⊊ DCFL = LR(1)"):
        assert marker in quoted, marker


INLINE_THEORY_QUOTES = [
    "Оригинал [GG] пока не прочитан целиком; его атрибуция не означает проверки исходного доказательства",
    "не устанавливает DCFL или LL(k)",
    "лежат в CFL",
    "REG ⊊ LL(1) (языки)",
    "MIN(S) ∩ a⁺b⁺c²c* = {aⁱbʲcʲ | i > j ≥ 2} = K",
    "Теорема 4.7.4 напрямую не применима: bbΣ* — бесконечное множество мёртвых слов.",
]


@pytest.mark.parametrize("q", INLINE_THEORY_QUOTES)
def test_inline_quotes_are_in_theory(q):
    assert _norm(q) in _norm(" ".join(_text(f) for f in SKILL_FILES))
    assert _norm(q) in _norm(SOURCES["THEORY.md"].read_text(encoding="utf-8"))


def test_inline_policy_quotes():
    pol = SOURCES["VERDICT_POLICY.md"].read_text(encoding="utf-8")
    for q in ("провал ≠ опровержение", "dcfl-21 дважды: shallit, затем dcfl_pumping"):
        assert q in pol and _norm(q) in _norm(" ".join(_text(f) for f in SKILL_FILES))


@pytest.mark.parametrize("ref", ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9",
                                 "1.10", "2.0", "3.2", "3.3"])
def test_cited_sections_exist(ref):
    doc = load_theory()
    assert doc.resolve(ref) is not None
    assert f"§{ref}" in "".join(_text(f) for f in SKILL_FILES)


def test_status_markers_match_theory():
    doc = load_theory()
    heads = {r: doc.resolve(r).heading for r in ("1.1", "1.2", "1.3", "1.4", "1.5", "1.10")}
    for r in ("1.1", "1.2", "1.3", "1.5"):
        assert "[проверено]" in heads[r], (r, heads[r])
    assert "[источники и границы указаны отдельно]" in heads["1.4"]
    assert "[проверено]" not in heads["1.4"]
    assert "[доказано конструкцией, проверено перебором]" in heads["1.10"]
    body = _text("SKILL.md")
    assert "**[источники и границы указаны отдельно]**, not [проверено]" in body


def test_no_error_formulation_is_recommended():
    # the false single-factor Yu condition must only appear as a trap
    body = _text("SKILL.md")
    assert "single-factor version is false" in body
    assert "pair** (x₂, x₄)" in body


# ---------------------------------------------------------------------------
# tool names
# ---------------------------------------------------------------------------

def test_backticked_tool_names_are_registered():
    names = set(REGISTRY.names())
    text = "".join(_text(f) for f in SKILL_FILES)
    # every backticked snake_case identifier is either a registered tool, a future /
    # built-in tool, or one of these known non-tool names (IR fields, task types,
    # trust labels, verdicts, old pipeline agents and files)
    not_tools = {
        "accept_configs", "accept_mode", "initial_stack", "final_state",
        "input_format", "word_pattern", "set_builder", "length_cmp",
        "classify_dcfl", "prove_dcfl", "prove_non_dcfl", "classify_and_prove_dcfl",
        "bounded_pass", "well_formed", "not_verified", "not_applicable", "non_dcfl",
        "dcfl_system", "stack_strategy", "reversal_of_dcfl",
    }
    candidates = set(re.findall(r"`([a-z][a-z0-9_]+)(?:\(|`)", text))
    unknown = sorted(c for c in candidates
                     if "_" in c and c not in names and c not in not_tools
                     and c not in FUTURE_OR_BUILTIN_TOOLS)
    assert not unknown, f"unregistered tool names in the skill: {unknown}"
    used = {c for c in candidates if c in names}
    assert {"dpda_run", "dpda_vs_ir", "validate_ir", "cyk"} <= used


def test_tool_table_covers_core_tools():
    body = _text("SKILL.md")
    for t in ("validate_ir", "ir_to_text", "check_membership", "dpda_run", "dpda_vs_ir",
              "nerode_probe", "pump_check_cfl", "dfa_vs_ir", "closure_lookup", "pattern_match"):
        assert f"`{t}" in body, t


# ---------------------------------------------------------------------------
# the worked DPDA of dpda.md
# ---------------------------------------------------------------------------

def _dpda_md_example() -> dict:
    blocks = re.findall(r"```json\n(.*?)```", _text("dpda.md"), re.S)
    return json.loads(blocks[-1])


def _tz_example() -> dict:
    tz = SOURCES["tz_dcfl_agent_system.md"].read_text(encoding="utf-8")
    m = re.search(r'"dpda": (\{.*?\n  \})\n\}', tz, re.S)
    assert m
    return json.loads(m.group(1))


def test_example_dpda_is_the_tz_automaton():
    assert _dpda_md_example() == _tz_example()


def _renamed(d: dict) -> dict:
    d = json.loads(json.dumps(d).replace('"$"', '"e"'))
    return d


def _dcfl11_ir_renamed() -> dict:
    ir = json.loads((ROOT / "dcfl_system/examples/eval/dcfl-11.json").read_text(encoding="utf-8"))
    ir["alphabet"] = [("e" if c == "$" else c) for c in ir["alphabet"]]
    wp = "{e a^n b^n c^m | n,m >= 1} union {d a^m b^n c^n | n,m >= 1}"
    ir["language_spec"]["word_pattern"] = wp
    ir["source_text"] = wp
    return ir


def test_example_dpda_is_deterministic_and_runs():
    words = ["eabc", "eaabbc", "eabcc", "dabc", "daabbcc", "dabbcc", "eab", "dab", "abc", "", "e", "d",
             "eaabbbc", "dabbc", "daabbc"]
    r = REGISTRY.invoke("dpda_run", {"dpda": _renamed(_dpda_md_example()), "words": words})
    assert r.ok, r.error
    assert r.trust == "verified"
    runs = {w["word"]: w["accepted"] for w in r.data["runs"]}
    assert runs["eabc"] and runs["eaabbc"] and runs["eabcc"]
    assert runs["dabc"] and runs["dabbcc"] and runs["daabbcc"]
    for w in ("eab", "dab", "abc", "", "e", "d", "eaabbbc", "dabbc", "daabbc"):
        assert runs[w] is False, w


def test_example_dpda_bounded_pass_on_renamed_dcfl11():
    r = REGISTRY.invoke("dpda_vs_ir", {"dpda": _renamed(_dpda_md_example()), "ir": _dcfl11_ir_renamed()})
    assert r.ok, r.error
    assert r.trust == "bounded_pass", (r.trust, r.trust_reason)


def test_dcfl11_as_written_has_no_oracle():
    ir = json.loads((ROOT / "dcfl_system/examples/eval/dcfl-11.json").read_text(encoding="utf-8"))
    r = REGISTRY.invoke("validate_ir", {"cls": "dcfl", "ir": ir, "check_tools": True})
    assert r.ok, r.error
    assert r.data["oracle"]["available"] is False
    r2 = REGISTRY.invoke("validate_ir", {"cls": "dcfl", "ir": _dcfl11_ir_renamed(), "check_tools": True})
    assert r2.data["oracle"]["available"] is True
