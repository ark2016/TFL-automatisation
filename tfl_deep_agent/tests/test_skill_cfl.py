"""P2: skills/cfl -- the CFL skill (SKILL.md + traps.md).

What is pinned:

- the frontmatter loads through deepagents' own SkillsMiddleware parser and lister;
- every "Verbatim, <source> §<id>:" quote block is a word-for-word copy of the source
  (THEORY.md: of the named section), so prompt drift cannot creep into the theory text;
- every THEORY.md section the skill cites exists, and the status table matches the markers;
- every tool call written in the skill names a registered tool (or a later-phase tool from
  TOOL_INVENTORY.md) and uses only that tool's argument names;
- the trust caps and the eval verdicts match VERDICT_POLICY / EVAL_SET;
- the worked recipes (pumping claim, closure check, grammar + regular filter, the cfl-11 oracle
  bound) behave on the real tools as the skill says.

No LLM, no network, no Docker.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest

from agent_system.lib.claim_verifier import CONFIDENCE_CAPS
from tfl_deep_agent.tools.base import REGISTRY, ToolResult, load_groups, trust_rank
from tfl_deep_agent.tools.theory import THEORY_PATH, load_theory

REPO = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO / "tfl_deep_agent" / "skills"
SKILL_DIR = SKILLS_DIR / "cfl"
SKILL_MD = SKILL_DIR / "SKILL.md"
TRAPS_MD = SKILL_DIR / "traps.md"
FILES = (SKILL_MD, TRAPS_MD)

SOURCES = {
    "THEORY.md": THEORY_PATH,
    "tz_cfl": REPO / "cfl_system" / "tz_cfl_agent_system.md",
    "VERDICT_POLICY.md": REPO / "docs" / "VERDICT_POLICY.md",
}
TRUST_WORDS = ("refuted", "not_verified", "well_formed", "bounded_pass", "verified")
# Tools the skill names that later phases register (TOOL_INVENTORY §8, §9), plus the deepagents
# built-ins it mentions.
LATER_PHASE_TOOLS = {"lean_statement", "lean_check", "spawn_subagent", "run_workflow", "budget_status",
                     "list_report_templates", "submit_report"}
BUILTIN_TOOLS = {"read_file", "execute"}
# Registered tools a CFL procedure needs: the skill must name each of them.
CORE_TOOLS = {"describe_ir_schema", "validate_ir", "ir_to_text", "sample_words", "check_membership",
              "list_theorems", "get_theorem", "input_grammar_cfg", "to_cnf", "cyk", "pda_run",
              "grammar_vs_ir", "pump_check_cfl", "parikh", "regex_to_dfa", "run_dfa"}

QUOTE_MARK = re.compile(r"^\s*Verbatim, (THEORY\.md|tz_cfl|VERDICT_POLICY\.md) §(\S+?)(?: [^:]*)?:\s*$")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def norm(s: str) -> str:
    return " ".join(s.split())


def load(rel: str) -> dict[str, Any]:
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def call(name: str, args: dict[str, Any]) -> ToolResult:
    return REGISTRY.invoke(name, args)


@pytest.fixture(scope="module", autouse=True)
def _registry():
    load_groups()


def quote_blocks(path: Path) -> list[tuple[int, str, str, str]]:
    """(line, source key, section id, quoted text) for every marked quote block."""
    lines = text(path).splitlines()
    out = []
    for n, line in enumerate(lines):
        m = QUOTE_MARK.match(line)
        if not m:
            continue
        body = []
        k = n + 1
        while k < len(lines) and lines[k].lstrip().startswith(">"):
            body.append(lines[k].lstrip()[1:].strip())
            k += 1
        out.append((n + 1, m.group(1), m.group(2), " ".join(body)))
    return out


# ---------------------------------------------------------------------------
# deepagents format
# ---------------------------------------------------------------------------

def test_frontmatter_parses_with_deepagents_parser(caplog):
    skills = pytest.importorskip("deepagents.middleware.skills")
    with caplog.at_level(logging.WARNING):
        meta = skills._parse_skill_metadata(text(SKILL_MD), "/cfl/SKILL.md", "cfl")
    assert meta is not None
    assert meta["name"] == "cfl"
    assert 200 < len(meta["description"]) <= skills.MAX_SKILL_DESCRIPTION_LENGTH
    assert "include_tools" not in meta["metadata"]  # would hide tools until the skill is read
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], caplog.text
    for word in ("classify_cfl", "grammar_filter_cfl", "контекстно-свободным", "dcfl"):
        assert word in meta["description"]


def test_skills_middleware_lists_the_skill():
    skills = pytest.importorskip("deepagents.middleware.skills")
    from deepagents.backends.filesystem import FilesystemBackend

    backend = FilesystemBackend(root_dir=SKILLS_DIR, virtual_mode=True)
    found = {m["name"]: m for m in skills._list_skills(backend, "/")}
    assert "cfl" in found
    assert found["cfl"]["path"].replace("\\", "/").endswith("cfl/SKILL.md")


def test_files_fit_one_read_and_supporting_files_exist():
    # the deepagents skills prompt tells the model to read a skill with limit=1000
    for path in FILES:
        assert len(text(path).splitlines()) < 1000, path
    skills = pytest.importorskip("deepagents.middleware.skills")
    meta = skills._parse_skill_metadata(text(SKILL_MD), "/cfl/SKILL.md", "cfl")
    for name in meta["metadata"]["supporting_files"].split():
        assert (SKILL_DIR / name).is_file(), name
        assert name in text(SKILL_MD).split("---", 2)[2]


# ---------------------------------------------------------------------------
# Theory text
# ---------------------------------------------------------------------------

def test_every_quote_is_verbatim_from_its_source():
    doc = load_theory()
    blocks = [b for path in FILES for b in quote_blocks(path)]
    assert len(blocks) >= 20
    for line, src, sec, quoted in blocks:
        assert quoted, f"empty quote at line {line}"
        if src == "THEORY.md":
            haystack = doc.full_text(doc.resolve(sec))
        else:
            haystack = text(SOURCES[src])
            assert f"§{sec}" in text(SKILL_MD)
        assert norm(quoted) in norm(haystack), f"line {line}: not verbatim in {src} §{sec}: {quoted[:120]}"


def test_no_unmarked_quote_blocks():
    """A blockquote is always a marked verbatim quote: paraphrase never looks like a quote."""
    for path in FILES:
        lines = text(path).splitlines()
        for n, line in enumerate(lines):
            if not line.lstrip().startswith(">"):
                continue
            k = n - 1
            while k >= 0 and lines[k].lstrip().startswith(">"):
                k -= 1
            assert QUOTE_MARK.match(lines[k]), f"{path.name}:{n + 1}: blockquote without a Verbatim marker"


def test_quotes_never_come_from_error_paragraphs():
    doc = load_theory()
    for _line, src, sec, quoted in (b for path in FILES for b in quote_blocks(path)):
        if src != "THEORY.md":
            continue
        for para in doc.full_text(doc.resolve(sec)).split("\n\n"):
            if "**[ошибка]**" in para:
                # the wrong formulation is the first sentence after the marker; the correction follows
                bad = norm(re.split(r"\.\s", para.split("**[ошибка]**", 1)[1], maxsplit=1)[0])
                assert norm(quoted) not in bad, quoted[:80]


def test_cited_theory_sections_exist():
    doc = load_theory()
    refs: set[str] = set()
    for path in FILES:
        body = text(path)
        refs.update(re.findall(r"THEORY\.md §(\d+(?:\.\d+)*)", body))
        refs.update(re.findall(r'get_theorem\("([^"]+)"\)', body))
    assert {"2.0", "2", "2.3", "2.4", "2.5", "1.5"} <= refs
    for ref in refs:
        doc.resolve(ref)  # raises on an unknown or ambiguous id
    # "THEORY.md Part II §5" (the closure_reduction live-run precedent)
    assert "closure_reduction" in doc.own_text(doc.resolve("II.5"))


def test_status_table_matches_theory_markers():
    doc = load_theory()
    rows = re.findall(r"^\| §([\d.]+) \| ([^|]+?) \|", text(SKILL_MD), flags=re.M)
    assert {r[0] for r in rows} == {"2.0", "2", "2.3", "2.4", "2.5", "1.5"}
    for sec, marker in rows:
        status = doc.resolve(sec).status
        if marker == "no marker":
            assert status is None, (sec, status)
        else:
            assert marker == status, (sec, marker, status)
    # the CFL sections themselves: no plain [проверено] marker (the skill says so)
    for s in doc.sections:
        if s.cls == "cfl":
            assert s.status != "[проверено]", s.id


# ---------------------------------------------------------------------------
# Tools named by the skill
# ---------------------------------------------------------------------------

def tool_calls(body: str) -> list[tuple[str, str]]:
    """(name, inside-of-parentheses) for every `name(...)` written in backticks."""
    out = []
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    for span in re.findall(r"`([^`\n]+)`", body):
        for m in re.finditer(r"\b([a-z_][a-z0-9_]*)\(([^()]*(?:\([^()]*\)[^()]*)*)\)?", span):
            out.append((m.group(1), m.group(2)))
    return out


def test_tool_calls_use_real_tools_and_argument_names():
    inventory = text(REPO / "tfl_deep_agent" / "docs" / "TOOL_INVENTORY.md")
    seen = set()
    for path in FILES:
        for name, inner in tool_calls(text(path)):
            seen.add(name)
            if name in REGISTRY:
                fields = set(REGISTRY.get(name).args_schema.model_fields)
                for kw in re.findall(r"(?:^|,)\s*([a-z_]+)=", inner):
                    assert kw in fields, f"{path.name}: {name}({kw}=...) is not an argument; known {sorted(fields)}"
            elif name in LATER_PHASE_TOOLS:
                assert f"`{name}`" in inventory, name
            else:
                assert name in BUILTIN_TOOLS, f"{path.name}: unknown tool {name}()"
    assert {"pump_check_cfl", "grammar_vs_ir", "input_grammar_cfg", "check_membership"} <= seen


def test_core_tools_are_all_named():
    body = text(SKILL_MD)
    for name in CORE_TOOLS:
        assert name in REGISTRY, name
        assert f"`{name}" in body, f"SKILL.md does not name {name}"


def test_tool_map_never_claims_more_than_the_tools_give():
    section = text(SKILL_MD).split("## 5. Tool map", 1)[1].split("\n## ", 1)[0]
    rows = [r for r in section.splitlines() if r.startswith("| ") and not r.startswith("| Step")]
    assert len(rows) >= 10
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        tools = [t for t in re.findall(r"`([a-z_]+)", cells[1]) if t in REGISTRY]
        if not tools:
            continue
        claimed = [w for w in re.findall(r"`([a-z_]+)`", cells[3]) if w in TRUST_WORDS and w != "refuted"]
        assert claimed, row
        best = max(trust_rank(REGISTRY.get(t).ceiling) for t in tools)
        assert max(trust_rank(w) for w in claimed) <= best, row


def test_caps_table_matches_verdict_policy():
    rows = dict(re.findall(r"^\| (verified|bounded_pass|well_formed|not_verified) \| (0\.\d\d) \|$",
                           text(SKILL_MD), flags=re.M))
    assert {k: float(v) for k, v in rows.items()} == CONFIDENCE_CAPS


# ---------------------------------------------------------------------------
# Eval traps
# ---------------------------------------------------------------------------

def table_rows(body: str) -> dict[str, list[str]]:
    rows = {}
    for line in body.splitlines():
        if not re.match(r"^\| cfl-\d\d \|", line):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        rows[cells[0]] = cells
    return rows


def test_trap_ids_and_verdicts_match_eval_set():
    eval_body = text(REPO / "docs" / "EVAL_SET.md")
    expected = {k: v[2] for k, v in table_rows(eval_body).items()}
    traps = table_rows(text(TRAPS_MD))
    assert traps
    for tid, cells in traps.items():
        assert tid in expected, tid
        assert cells[2] == expected[tid], (tid, cells[2], expected[tid])
        assert cells[1] == table_rows(eval_body)[tid][1], f"{tid}: language cell differs from EVAL_SET"
    tagged = {tid for tid, cells in table_rows(eval_body).items() if "[trap" in cells[3]}
    assert tagged and tagged <= set(traps), sorted(tagged - set(traps))


# ---------------------------------------------------------------------------
# Recipes on the real tools
# ---------------------------------------------------------------------------

def skill_json_example() -> dict[str, Any]:
    blocks = re.findall(r"```json\n(.*?)\n```", text(SKILL_MD), flags=re.S)
    assert len(blocks) == 1
    return json.loads(blocks[0])


def test_pumping_claim_example_checks_out_on_anbncn():
    claim = skill_json_example()
    ir = load("cfl_system/examples/task_anbncn.json")
    res = call("pump_check_cfl", {"ir": ir, "claim": claim})
    assert res.ok and res.trust == "well_formed", res.trust_reason
    assert res.bounds.p_values == [3, 4]
    assert res.bounds.model_dump()["unresolved_splits"] == 0
    assert res.data["bounded_check"]["status"] == "closed_for_sampled_p"
    # the instances are the parametric word at p = 3, 4
    for p, word in claim["word_instances"].items():
        assert word == "a" * int(p) + "b" * int(p) + "c" * int(p)


W1W2W1W3 = "cfl_system/examples/task_w1w2w1w3.json"


def natural_ir(description: str) -> dict[str, Any]:
    return {"task_type": "prove_non_cfl", "source_text": description,
            "language_spec": {"kind": "natural", "alphabet": ["a", "b", "c"], "description": description}}


def labels(ir: dict[str, Any], words: list[str]) -> dict[str, bool]:
    res = call("check_membership", {"cls": "cfl", "ir": ir, "words": words})
    assert res.ok
    return {r["word"]: r["in_language"] for r in res.data["results"]}


def test_closure_recipe_catches_the_theory_section2_trap():
    """SKILL.md §4.3 step 3 on cfl-06: the wrong intersection {aⁿbᵐaⁿcᵏ} is refuted by abaac
    (THEORY.md §2), the corrected L ∩ R = {aⁿbᵐac·aⁿbᵐac} passes on words of R."""
    task = load(W1W2W1W3)
    wrong = natural_ir("{a^n b^m a^n c^k | n, m, k >= 1}")
    words = ["abac", "abaac", "aabaac", "aabaaac"]
    res = call("check_membership", {"cls": "cfl", "ir": wrong, "words": words, "expected": labels(task, words)})
    assert res.trust == "refuted", res.trust_reason
    assert "abaac" in res.trust_reason

    dfa = call("regex_to_dfa", {"regex": "a+b+aca+b+ac"})
    assert dfa.ok and dfa.trust == "verified"
    words = ["a" * n + "b" * m + "ac" + "a" * n2 + "b" * m2 + "ac"
             for n in (1, 2, 3) for m in (1, 2, 3) for n2 in (1, 2, 3) for m2 in (1, 2, 3)]
    in_r = call("run_dfa", {"dfa": dfa.data["dfa"], "words": words})
    assert in_r.ok and set(in_r.data["accepted"]) == set(words)
    right = natural_ir("{a^n b^m a c a^n b^m a c | n, m >= 1}")
    res = call("check_membership", {"cls": "cfl", "ir": right, "words": words, "expected": labels(task, words)})
    assert res.trust == "bounded_pass", res.trust_reason
    assert res.bounds.n_words == len(words)


def test_grammar_filter_recipe_for_cfl17():
    ir = load("cfl_system/examples/eval/cfl-17.json")
    assert "S → aaSbb \\| ε" in text(TRAPS_MD)
    res = call("input_grammar_cfg", {"ir": ir})
    assert res.trust == "not_verified"  # R2-CFG does not apply to grammar_filter
    good = {"terminals": ["a", "b"], "nonterminals": ["S"], "start": "S",
            "rules": [{"lhs": "S", "rhs": ["a", "a", "S", "b", "b"]}, {"lhs": "S", "rhs": []}]}
    res = call("grammar_vs_ir", {"grammar": good, "ir": ir})
    assert res.trust == "bounded_pass", res.trust_reason
    unfiltered = {**good, "rules": [{"lhs": "S", "rhs": ["a", "S", "b"]}, {"lhs": "S", "rhs": []}]}
    assert call("grammar_vs_ir", {"grammar": unfiltered, "ir": ir}).trust == "refuted"


def test_input_grammar_shortcut_is_verified_for_a_plain_grammar_ir():
    res = call("input_grammar_cfg", {"ir": load("cfl_system/examples/eval/cfl-04.json")})
    assert res.trust == "verified"


def test_cfl11_instances_beyond_the_oracle_bound_are_not_checked():
    """traps.md cfl-11: a¹⁸b¹⁸c¹⁸ (54 letters) exceeds the exponent-notation oracle's
    max_word_len 40, so nothing is pumped and bounds.p_values is empty."""
    ir = load("cfl_system/examples/eval/cfl-11.json")
    status = call("validate_ir", {"cls": "cfl", "ir": ir, "check_tools": True})
    assert status.data["oracle"]["max_word_len"] == 40
    assert "max_word_len` 40" in text(TRAPS_MD)
    claim = {"word_chosen": "a^{6p} b^{6p} c^{6p}",
             "cases": [{"case": "any split", "why_not_in_L": "see the argument"}],
             "all_cases_covered": True,
             "word_instances": {"3": "a" * 18 + "b" * 18 + "c" * 18, "4": "a" * 24 + "b" * 24 + "c" * 24}}
    res = call("pump_check_cfl", {"ir": ir, "claim": claim})
    assert res.bounds.p_values == []
    assert res.bounds.n_unknown == 2


def test_copy_language_gets_an_oracle_as_exists_decomposition():
    """traps.md cfl-03: {ww} as natural text has no oracle; as exists_decomposition it has one."""
    assert call("validate_ir", {"cls": "cfl", "ir": load("cfl_system/examples/eval/cfl-03.json"),
                                "check_tools": True}).data["oracle"]["available"] is False
    ir = {"task_type": "classify_and_prove_cfl", "source_text": "{ww | w in {a,b}*}",
          "language_spec": {"kind": "exists_decomposition", "parts": ["w"], "concat_pattern": ["w", "w"],
                            "alphabets": {"w": ["a", "b"]}, "constraints": []}}
    status = call("validate_ir", {"cls": "cfl", "ir": ir, "check_tools": True})
    assert status.data["oracle"]["available"] is True and status.data["oracle"]["exact"] is True
    assert labels(ir, ["abab", "aba", "", "abba"]) == {"abab": True, "aba": False, "": True, "abba": False}


def test_ticket50_instance_lengths_in_traps():
    body = text(TRAPS_MD)
    wrong, right = "a" * 8 + "b" + "ab" * 5 + "a" * 8, "a" * 8 + "b" + "ab" * 4 + "a" * 8
    assert "a⁸b(ab)⁵a⁸`\n   (length 27)" in body or "(length 27)" in body
    assert len(wrong) == 27 and len(right) == 25 and "(length 25)" in body
    ir = load("docs/examples/cfl_ticket50_w1bw2w3_lean_proved/input.json")
    assert labels(ir, [wrong, right]) == {wrong: False, right: True}
