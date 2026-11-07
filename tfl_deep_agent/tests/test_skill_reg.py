"""P2: the REG skill (tfl_deep_agent/skills/reg/).

Three kinds of checks, none of them needs an LLM, the network or Docker:

1. Format: deepagents' own SkillsMiddleware loads the skill from a FilesystemBackend
   (frontmatter, name == directory, description length) without warnings.
2. Faithfulness of the text: every block quoted after a ``Source:`` line is
   verbatim in the cited file and section; every cited THEORY.md / tz / VERDICT_POLICY
   section and rule exists; every tool the skill tells the agent to call exists in the
   registry (or in TOOL_INVENTORY.md for later phases); the confidence caps match the
   policy; every REG trap task of the eval manifest is covered.
3. Contracts: the concrete tool behaviour the skill promises (which call gives
   bounded_pass, refuted or unsupported on which eval task) is pinned on the real
   example IRs, so a tool change that breaks the skill's advice fails here.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_system.lib.claim_verifier import CONFIDENCE_CAPS
from tfl_deep_agent.tools import theory
from tfl_deep_agent.tools.base import REGISTRY, load_groups

ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT / "tfl_deep_agent" / "skills"
REG_DIR = SKILLS_DIR / "reg"
SKILL_FILES = ("SKILL.md", "theory.md", "traps.md")
TZ_REG = "agent_system/tz_tfl_agent_system.md"

# Tools built into deepagents (filesystem, todos, task, sandbox): not in our registry.
DEEPAGENTS_BUILTINS = {
    "ls", "read_file", "write_file", "edit_file", "glob", "grep",
    "write_todos", "task", "execute",
}


def _text(name: str) -> str:
    return (REG_DIR / name).read_text(encoding="utf-8")


def _all_text() -> str:
    return "\n".join(_text(n) for n in SKILL_FILES)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


@pytest.fixture(scope="module", autouse=True)
def _tools_loaded() -> None:
    load_groups()


def call(name: str, **args: Any) -> dict[str, Any]:
    result = REGISTRY.invoke(name, args)
    return result if isinstance(result, dict) else result.model_dump()


def load(rel: str) -> dict[str, Any]:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Format: loaded by deepagents' SkillsMiddleware
# ---------------------------------------------------------------------------


def _frontmatter() -> dict[str, Any]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", _text("SKILL.md"), re.DOTALL)
    assert m, "SKILL.md must start with YAML frontmatter"
    data = yaml.safe_load(m.group(1))
    assert isinstance(data, dict)
    return data


def test_frontmatter_fields() -> None:
    fm = _frontmatter()
    assert fm["name"] == "reg" == REG_DIR.name
    desc = fm["description"]
    assert isinstance(desc, str) and 50 < len(desc) <= 1024
    # The listing is all the model sees before it reads the file: it must say when to use it.
    for word in ("regular", "classify_and_prove", "build_dfa", "compute_pumping_length", "Myhill"):
        assert word in desc
    # metadata values must be strings (deepagents coerces, but keep them honest).
    assert all(isinstance(v, str) for v in fm.get("metadata", {}).values())


def test_loaded_by_deepagents_skills_middleware(caplog: pytest.LogCaptureFixture) -> None:
    from deepagents.backends import FilesystemBackend
    from deepagents.middleware.skills import SkillsMiddleware

    mw = SkillsMiddleware(
        backend=FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True),
        sources=["/"],
    )
    with caplog.at_level(logging.WARNING, logger="deepagents"):
        update = mw.before_agent({}, None, {})  # type: ignore[arg-type]
    assert update is not None
    skills = {s["name"]: s for s in update["skills_metadata"]}
    assert "reg" in skills
    reg = skills["reg"]
    assert reg["path"].replace("\\", "/").endswith("/reg/SKILL.md")
    assert reg["description"] == _frontmatter()["description"]
    reg_warnings = [r for r in caplog.records if "reg" in r.getMessage() and "SKILL" in r.getMessage()]
    assert not reg_warnings, [r.getMessage() for r in reg_warnings]
    # The system-prompt listing renders the skill by name.
    assert "**reg**" in mw._format_skills_list([reg])


def test_companion_files_are_referenced_by_their_virtual_paths() -> None:
    skill = _text("SKILL.md")
    for name in SKILL_FILES[1:]:
        assert (REG_DIR / name).is_file()
        assert f"/skills/reg/{name}" in skill


# ---------------------------------------------------------------------------
# 2. Faithfulness of the text
# ---------------------------------------------------------------------------

_SOURCE_RE = re.compile(r"^Source: `([^`]+)`(?: (§[0-9.]+|#[a-z-]+))?\s*$")


def _section_text(rel: str, sec: str | None) -> str:
    path = ROOT / rel
    assert path.is_file(), f"cited file {rel} does not exist"
    text = path.read_text(encoding="utf-8")
    if sec is None:
        return text
    if rel == "docs/THEORY.md":
        assert sec.startswith("§")
        doc = theory.load_theory()
        return doc.full_text(doc.resolve(sec[1:]))
    if sec.startswith("#"):
        marker = f'<a id="{sec[1:]}"></a>'
        start = text.index(marker)
        end = text.find('<a id="', start + len(marker))
        return text[start:end if end >= 0 else None]
    num = re.escape(sec[1:].rstrip("."))
    heads = list(re.finditer(r"^(#+)\s+(\S+)", text, re.MULTILINE))
    for k, h in enumerate(heads):
        if re.fullmatch(num + r"\.?", h.group(2)):
            level = len(h.group(1))
            end = next((n.start() for n in heads[k + 1:] if len(n.group(1)) <= level), len(text))
            return text[h.start():end]
    raise AssertionError(f"section {sec} not found in {rel}")


def _quotes() -> list[tuple[str, str, str | None, str]]:
    """(skill file, cited file, section, joined quote) for every Source: block."""
    out = []
    for name in SKILL_FILES:
        lines = _text(name).splitlines()
        i = 0
        while i < len(lines):
            m = _SOURCE_RE.match(lines[i])
            if not m:
                assert not lines[i].startswith("Source:"), f"{name}: malformed Source line {lines[i]!r}"
                i += 1
                continue
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            block = []
            while j < len(lines) and lines[j].startswith(">"):
                block.append(lines[j][1:].strip())
                j += 1
            assert block, f"{name}: Source line {lines[i]!r} is not followed by a quote"
            out.append((name, m.group(1), m.group(2), " ".join(block)))
            i = j
    return out


def test_there_are_quotes_from_theory_and_the_spec() -> None:
    cited = {(rel, sec) for _, rel, sec, _ in _quotes()}
    assert ("docs/THEORY.md", "§0") in cited
    assert ("docs/THEORY.md", "§2.0") in cited
    for sec in ("§1.5", "§1.6", "§1.8", "§1.9"):
        assert (TZ_REG, sec) in cited
    assert ("docs/VERDICT_POLICY.md", "§4") in cited


@pytest.mark.parametrize("skill_file,rel,sec,quote", _quotes())
def test_quote_is_verbatim(skill_file: str, rel: str, sec: str | None, quote: str) -> None:
    section = _norm(_section_text(rel, sec))
    for piece in quote.split(" … "):
        assert _norm(piece) in section, f"{skill_file}: not verbatim in {rel} {sec}: {piece!r}"


# Short quotes inside running text (not Source: blocks): (quote, file it is copied from).
INLINE_QUOTES = [
    ("регулярность заданного regex/DFA", "docs/VERDICT_POLICY.md"),
    ("с проверкой слов оракулом → ≤ 0.85", "docs/VERDICT_POLICY.md"),
    ("бесконечное семейство различимых префиксов, а не конечная оценка индекса Нероуда", "docs/THEORY.md"),
    ("Более того, количество классов ≡_L равно числу состояний минимального ДКА для L", TZ_REG),
    ("Инверсия допускающих состояний ДКА", TZ_REG),
    ("δ: Q × Σ → Q — функция переходов (тотальная)", TZ_REG),
    ("L регулярен ⟺ M(L) конечен", TZ_REG),
    ("Modular(count_symbol, k, r) → finite, states = k", TZ_REG),
    ("`(a)\\1*` = `aa*`", TZ_REG),
    ("дополнение ∩ a*b* = {aⁿbⁿ}", "docs/EVAL_SET.md"),
    ("The words need not be in L themselves", "agent_system/prompts/nerode_agent.md"),
    ("S -> aSb | aS | Sb | epsilon", "agent_system/prompts/classifier.md"),
    ("проверено относительно IR №…", "tfl_deep_agent/docs/architecture.html"),
]


@pytest.mark.parametrize("quote,rel", INLINE_QUOTES)
def test_inline_quote_is_verbatim(quote: str, rel: str) -> None:
    assert _norm(quote) in _norm(_all_text())
    assert _norm(quote) in _norm((ROOT / rel).read_text(encoding="utf-8")), (quote, rel)


def test_cited_sections_exist() -> None:
    text = _all_text()
    doc = theory.load_theory()
    theory_refs = set(re.findall(r"THEORY\.md\s+§\s?([0-9]+(?:\.[0-9]+)?)", text))
    assert {"0", "2.0"} <= theory_refs
    for ref in theory_refs:
        doc.resolve(ref)  # raises on an unknown or ambiguous id
    for ref in set(re.findall(r"\btz §\s?([0-9]+(?:\.[0-9]+)*)", text)):
        _section_text(TZ_REG, "§" + ref)
    for ref in set(re.findall(r"VERDICT_POLICY(?:\.md)? §\s?([0-9]+)", text)):
        _section_text("docs/VERDICT_POLICY.md", "§" + ref)
    policy = (ROOT / "docs" / "VERDICT_POLICY.md").read_text(encoding="utf-8")
    for rule in set(re.findall(r"\bR([0-9]+′?)(?![0-9])", text)):
        assert f"**R{rule} " in policy or f"**R{rule}—" in policy or f"**R{rule} —" in policy, rule


def test_theory_status_of_reg_section_is_stated() -> None:
    doc = theory.load_theory()
    section = doc.resolve("0")
    assert section.status == "[доказано индукцией и Нероудом]"
    assert section.status in _text("theory.md")
    # The unreviewed tz §1.9 claims are flagged, not cited.
    assert "not reviewed" in _text("theory.md").lower()
    assert "not reviewed" in _text("SKILL.md").lower()


def _inventory_tool_names() -> set[str]:
    inv = (ROOT / "tfl_deep_agent" / "docs" / "TOOL_INVENTORY.md").read_text(encoding="utf-8")
    names = set(re.findall(r"^\| `([a-z_][a-z0-9_]*)`", inv, re.MULTILINE))
    # §9 lists several tools in one cell.
    for row in re.findall(r"^\| `[a-z]+` \| ([^|]+)\|", inv, re.MULTILINE):
        names.update(re.findall(r"`([a-z_][a-z0-9_]*)`", row))
    return names


def test_every_called_tool_exists() -> None:
    called = set(re.findall(r"`([a-z_][a-z0-9_]*)\(", _all_text()))
    registered = set(REGISTRY.names())
    planned = _inventory_tool_names() - registered
    unknown = called - registered - planned - DEEPAGENTS_BUILTINS
    assert not unknown, f"skill calls tools that do not exist: {sorted(unknown)}"
    core = {"regex_to_dfa", "run_dfa", "nerode_probe", "dfa_vs_ir", "check_membership",
            "sample_words", "validate_ir", "describe_ir_schema", "get_theorem", "pump_check_cfl"}
    assert core <= called | set(re.findall(r"`([a-z_][a-z0-9_]*)`", _all_text()))
    assert core <= registered


def test_deferred_pump_check_reg_is_never_called() -> None:
    text = _all_text()
    assert "pump_check_reg(" not in text
    assert "pump_check_reg" not in REGISTRY.names()
    assert "no `pump_check_reg` tool" in text.replace("There is no", "no")


def test_confidence_caps_match_policy() -> None:
    skill = _text("SKILL.md")
    caps = dict(re.findall(r"`(verified|bounded_pass|well_formed|not_verified)` (0\.\d\d)", skill))
    assert caps == {k: f"{v:.2f}" for k, v in CONFIDENCE_CAPS.items()}
    # Every cap number used in the evidence table is one of the policy caps (or the R3 contradiction cap).
    allowed = {f"{v:.2f}" for v in CONFIDENCE_CAPS.values()} | {"0.50"}
    table = skill.split("## 5.", 1)[1].split("## 6.", 1)[0]
    for row in table.splitlines():
        if row.startswith("|") and "---" not in row:
            for num in re.findall(r"\b0\.\d\d\b", row.split("|")[-2]):
                assert num in allowed, row


def test_every_reg_trap_task_is_covered() -> None:
    manifest = json.loads((ROOT / "tfl_eval" / "manifest.json").read_text(encoding="utf-8"))
    traps = sorted(t["id"] for t in manifest if t["system"] == "reg" and t.get("trap"))
    assert traps == ["reg-03", "reg-07", "reg-09", "reg-12", "reg-14"]
    desc = _frontmatter()["description"]
    trap_text = _text("traps.md")
    skill = _text("SKILL.md")
    for tid in traps:
        assert tid in desc
        assert f"### {tid}" in trap_text
        assert tid in skill.split("## 7.", 1)[1]


def test_wrong_formulations_are_only_quoted_as_errors() -> None:
    # THEORY.md §0's [ошибка] claims appear only inside the quote that marks them wrong.
    text = _all_text()
    wrong = "на самом деле пересечение = {aᵐbᵏ | m ≡ k (mod 2)}, регулярно"
    assert text.count(wrong) == 1
    assert "**[ошибка]**" in text


# ---------------------------------------------------------------------------
# 3. Contracts: the tool behaviour the skill promises
# ---------------------------------------------------------------------------

REG04 = "agent_system/examples/eval/reg-04.json"   # regex (ab)*a|b
REG07 = "agent_system/examples/eval/reg-07.json"   # natural {a^(n^2)}
REG12 = "agent_system/examples/eval/reg-12.json"   # S -> aS | Sb | eps
REG13 = "agent_system/examples/eval/reg-13.json"   # S -> aSb | ab
G_REG = "agent_system/examples/task2_grammar_sasb.json"  # reg-14, THEORY.md §0


def _cnt(sym: str, var: str = "w") -> dict[str, Any]:
    return {"kind": "count_symbol", "symbol": sym, "in_var": var}


def _in_astar_bstar() -> dict[str, Any]:
    zero = {"kind": "constant", "value": 0}
    return {"type": "exists_decomposition", "parts": ["x", "y"], "concat_pattern": ["x", "y"],
            "constraints": [{"op": "eq", "left": _cnt("b", "x"), "right": zero},
                            {"op": "eq", "left": _cnt("a", "y"), "right": zero}]}


def _pred_ir(source: str, predicate: dict[str, Any]) -> dict[str, Any]:
    return {"task_type": "classify_and_prove", "source_text": source,
            "language_spec": {"kind": "predicate", "alphabet": ["a", "b"], "variable": "w",
                              "predicate": predicate}}


def _mod2(expr: dict[str, Any], r: int) -> dict[str, Any]:
    return {"expr": expr, "modulus": 2, "remainder": r}


# traps.md reg-03: the faithful encoding (residues of both counts), not "|w| even".
REG03_PRED = _pred_ir("{w ∈ {a,b}* | #a(w) ≡ #b(w) (mod 2)}", {"op": "or", "operands": [
    {"op": "and", "operands": [_mod2(_cnt("a"), 0), _mod2(_cnt("b"), 0)]},
    {"op": "and", "operands": [_mod2(_cnt("a"), 1), _mod2(_cnt("b"), 1)]}]})
# traps.md reg-09.
REG09_PRED = _pred_ir("{a^i b^j | i + j is even}", {"op": "and", "operands": [
    _in_astar_bstar(), _mod2({"kind": "length", "of_var": "w"}, 0)]})
# traps.md B, reg-05.
REG05_PRED = _pred_ir("{a^n b^m | n >= m >= 0}", {"op": "and", "operands": [
    _in_astar_bstar(), {"op": "geq", "left": _cnt("a"), "right": _cnt("b")}]})


def _dfa(regex: str) -> dict[str, Any]:
    r = call("regex_to_dfa", regex=regex, alphabet=["a", "b"])
    assert r["ok"] and r["trust"] == "verified", r
    return r["data"]["dfa"]


def test_step0_natural_eval_irs_have_no_oracle_and_predicates_do() -> None:
    r = call("validate_ir", cls="reg", ir=load(REG07), check_tools=True)
    assert r["data"]["oracle"]["available"] is False
    for ir in (REG03_PRED, REG09_PRED, REG05_PRED):
        r = call("validate_ir", cls="reg", ir=ir, check_tools=True)
        assert r["data"]["valid"] and r["data"]["oracle"]["available"] and r["data"]["oracle"]["exact"], r


def test_step2_task_regex_is_verified_and_backrefs_refused() -> None:
    ir = load(REG04)
    r = call("regex_to_dfa", regex=ir["language_spec"]["pattern"], alphabet=["a", "b"])
    assert r["ok"] and r["trust"] == "verified"
    for rx in (r"(a)\1*", r"((a*)b?(\2)+)*"):
        r = call("regex_to_dfa", regex=rx)
        assert not r["ok"] and r["error_kind"] == "invalid_input"


def test_reg12_mixed_grammar_unsupported_but_astar_bstar_passes() -> None:
    ir = load(REG12)
    spec = ir["language_spec"]
    r = call("regex_to_dfa", grammar={k: spec[k] for k in ("terminals", "nonterminals", "start", "rules")})
    assert not r["ok"] and r["error_kind"] == "unsupported" and r["trust"] == "not_verified"
    r = call("dfa_vs_ir", cls="reg", ir=ir, dfa=_dfa("a*b*"))
    assert r["trust"] == "bounded_pass" and r["bounds"]["max_len"] == 10


@pytest.mark.parametrize("ir,regex", [
    (REG03_PRED, "((a|b)(a|b))*"),
    (REG09_PRED, "(aa)*(bb)*|a(aa)*b(bb)*"),
])
def test_reg03_reg09_regular_routes_reach_bounded_pass(ir: dict[str, Any], regex: str) -> None:
    r = call("dfa_vs_ir", cls="reg", ir=ir, dfa=_dfa(regex))
    assert r["trust"] == "bounded_pass", r["trust_reason"]


def test_reg13_wrong_dfa_is_refuted_with_aab() -> None:
    r = call("dfa_vs_ir", cls="reg", ir=load(REG13), dfa=_dfa("a+b+"))
    assert r["trust"] == "refuted"
    assert r["data"]["counterexamples"][0]["word"] == "aab"
    r = call("nerode_probe", ir=load(REG13), family={"prefix": "a^n", "suffix": "b^n", "values": [1, 2, 3, 4, 5]})
    assert r["trust"] == "bounded_pass"


def test_reg05_suffix_orientation() -> None:
    fam = {"prefix": "a^n", "suffix": "b^n", "values": [0, 1, 2, 3, 4, 5]}
    r = call("nerode_probe", ir=REG05_PRED, family={**fam, "suffix_at": "larger"})
    assert r["trust"] == "bounded_pass"
    r = call("nerode_probe", ir=REG05_PRED, family={**fam, "suffix_at": "smaller"})
    assert r["trust"] == "refuted"


def test_reg14_g_reg_family_and_intersection_claims() -> None:
    ir = load(G_REG)
    r = call("nerode_probe", ir=ir, family={"prefix": "a^(3n+2)", "suffix": "b^n", "values": [0, 1, 2, 3, 4]})
    assert r["trust"] == "bounded_pass"
    # THEORY.md §0 counterexamples to "L ∩ a*b* = {a^n b^n}" and to "≡ mod 2".
    expected = {"bb": True, "aa": True, "abbb": True, "aaaaab": True, "aaaa": False, "aaaaaaab": False}
    r = call("check_membership", cls="reg", ir=ir, words=[], expected=expected)
    assert r["trust"] == "bounded_pass"
    # The false claim itself is refuted by the exact CYK oracle.
    r = call("check_membership", cls="reg", ir=ir, words=[], expected={"bb": False, "ab": True})
    assert r["trust"] == "refuted"


def test_reg07_no_oracle_means_unsupported() -> None:
    ir = load(REG07)
    r = call("nerode_probe", ir=ir, family={"prefix": "a^n", "values": [1, 2, 3]})
    assert not r["ok"] and r["error_kind"] == "unsupported"
    r = call("pump_check_cfl", ir=ir, cls="reg", split={"u": "", "v": "a", "w": "aaa"})
    assert not r["ok"] and r["error_kind"] == "unsupported"


def test_reg_split_through_pump_check_cfl_is_a_diagnostic_and_p_is_the_cfl_window() -> None:
    ir = load(REG13)
    # z = a^4 b^4 with p = 4: x = a, y = aa, z' = ab^4 (|xy| = 3 <= 4), mapped as u, v, w.
    split = {"u": "a", "v": "aa", "w": "abbbb", "x": "", "y": ""}
    r = call("pump_check_cfl", ir=ir, cls="reg", split=split, i_values=[0, 2], claimed_exit_i=[0, 2])
    assert r["ok"] and r["trust"] == "not_verified"
    assert r["data"]["evidence_role"] == "diagnostic"
    # Passing p checks the CFL window |vwx| = |y z'| = 7 > 4 and rejects a valid REG split:
    # this is why the skill says "do not pass p".
    r = call("pump_check_cfl", ir=ir, cls="reg", split=split, p=4, i_values=[0, 2])
    assert not r["ok"] and r["error_kind"] == "invalid_input"
    assert "|vwx|" in r["error"]
