"""P2: skills/ll -- the deepagents skill for LL(k) tasks.

What is pinned (no LLM, no network, no Docker):

- the frontmatter parses with deepagents' own SkillsMiddleware loader (name = folder name,
  description within the Agent Skills limit, allowed-tools all registered);
- every ``<!-- quote: ID -->`` block in the skill files is a verbatim substring of the cited
  section of docs/THEORY.md (or of ll_system/tz_ll_agent_system.md for ``tz_ll``), and no
  quoted block is an ``[ошибка]`` paragraph (PLAN non-negotiable 5);
- the status marks the skill reports for its sections are the ones THEORY.md has;
- every tool call written as ``name(arg=...)`` names a registered tool and real argument names;
- the example set_builder IRs validate and their oracles are exact and agree with a brute-force
  reference predicate;
- the tool outcomes the skill and traps.md promise for the eval trap tasks (ll-08, ll-09, ll-10,
  ll-11, ll-15, ll-17, ll-18) and for the constructive path (ll-06) are what the tools return;
- the trap table covers every LL task of tfl_eval/manifest.json with its expected verdict.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

import pytest

from tfl_deep_agent.tools import ll as ll_tools
from tfl_deep_agent.tools.base import load_groups
from tfl_deep_agent.tools.theory import load_theory
from tfl_deep_agent.tools.words import oracle_for

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "tfl_deep_agent"
SKILL_DIR = PKG / "skills" / "ll"
SKILL_MD = SKILL_DIR / "SKILL.md"
THEORY_MD = SKILL_DIR / "theory.md"
TRAPS_MD = SKILL_DIR / "traps.md"
SKILL_FILES = (SKILL_MD, THEORY_MD, TRAPS_MD)
TZ_LL = ROOT / "ll_system" / "tz_ll_agent_system.md"
EVAL_DIR = ROOT / "ll_system" / "examples" / "eval"

# Tools that are not in the P1 registry but are part of the agent (P3/P4). The skill may name them.
NON_REGISTRY_TOOLS = {"spawn_subagent", "run_workflow", "budget_status", "submit_report",
                      "list_report_templates", "read_file"}
# Backticked `name(...)` forms that are not tool calls: the set_builder template token rev(w).
NOT_TOOL_CALLS = {"rev"}

QUOTE_RE = re.compile(r"<!-- quote: ([^>]+?) -->\n```text\n(.*?)\n```", re.S)
CALL_RE = re.compile(r"`([a-z_]+)\(([^`]*)\)`")


@pytest.fixture(scope="module")
def registry():
    return load_groups()


@pytest.fixture(scope="module")
def theory():
    return load_theory()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def eval_ir(task_id: str) -> dict[str, Any]:
    return json.loads((EVAL_DIR / f"{task_id}.json").read_text(encoding="utf-8"))


def invoke(registry, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return registry.invoke(name, args).model_dump()


# ---------------------------------------------------------------------------
# Frontmatter and loading through deepagents
# ---------------------------------------------------------------------------

def test_frontmatter_parses_with_deepagents(caplog, registry):
    skills = pytest.importorskip("deepagents.middleware.skills")
    content = read(SKILL_MD)
    with caplog.at_level(logging.WARNING, logger=skills.logger.name):
        meta = skills._parse_skill_metadata(content=content, skill_path="/skills/ll/SKILL.md",
                                            directory_name="ll")
    assert meta is not None
    assert not [r for r in caplog.records if r.name == skills.logger.name], caplog.text
    assert meta["name"] == "ll"
    # within the limit, so deepagents does not truncate it
    assert 100 < len(meta["description"]) <= skills.MAX_SKILL_DESCRIPTION_LENGTH
    assert meta["allowed_tools"], "allowed-tools should list the recommended tools"
    unknown = [t for t in meta["allowed_tools"] if t not in registry.names()]
    assert not unknown, f"allowed-tools names unregistered tools: {unknown}"
    assert all(isinstance(v, str) for v in meta["metadata"].values())


def test_skills_middleware_lists_ll_skill():
    skills = pytest.importorskip("deepagents.middleware.skills")
    fs = pytest.importorskip("deepagents.backends.filesystem")
    backend = fs.FilesystemBackend(root_dir=PKG, virtual_mode=True)
    mw = skills.SkillsMiddleware(backend=backend, sources=["/skills/"])
    update = mw.before_agent({}, None, {})
    assert update is not None
    by_name = {s["name"]: s for s in update["skills_metadata"]}
    assert "ll" in by_name, update.get("skills_load_errors")
    assert by_name["ll"]["path"] == "/skills/ll/SKILL.md"
    listing = mw._format_skills_list([by_name["ll"]])
    assert "**ll**" in listing and "/skills/ll/SKILL.md" in listing
    # the supporting files the skill tells the agent to read exist and are reachable through the backend
    refs = set(re.findall(r"`(/skills/ll/[\w.-]+)`", read(SKILL_MD)))
    assert refs >= {"/skills/ll/theory.md", "/skills/ll/traps.md"}
    for ref in sorted(refs):
        resp = backend.download_files([ref])[0]
        assert resp.error is None and resp.content, ref


def test_skill_files_are_utf8_without_bom():
    for path in SKILL_FILES:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        raw.decode("utf-8")
        assert b"\r\n" not in raw, f"{path.name}: use LF line ends"


# ---------------------------------------------------------------------------
# Theory quotes: verbatim, from the cited section, never an [ошибка] paragraph
# ---------------------------------------------------------------------------

def all_quotes() -> list[tuple[str, str, str]]:
    out = []
    for path in SKILL_FILES:
        for ref, body in QUOTE_RE.findall(read(path)):
            out.append((path.name, ref.strip(), body))
    return out


def test_quotes_exist_and_cover_the_ll_sections():
    quotes = all_quotes()
    assert len(quotes) >= 25
    refs = {ref for _, ref, _ in quotes}
    assert refs >= {"3.1", "3.2", "3.3", "3.4", "3.5", "1.2", "1.5", "tz_ll"}
    # every fenced text block that looks like a quote carries a marker (no unmarked theory text)
    for path in (SKILL_MD, THEORY_MD):
        text = read(path)
        assert text.count("```text\n") == len(QUOTE_RE.findall(text)), path.name


@pytest.mark.parametrize("fname,ref,body", all_quotes(),
                         ids=[f"{f}:{r}:{i}" for i, (f, r, _) in enumerate(all_quotes())])
def test_quote_is_verbatim_from_cited_section(theory, fname, ref, body):
    assert body.strip(), "empty quote"
    assert "[ошибка]" not in body, "an [ошибка] paragraph must never be quoted as a statement"
    if ref == "tz_ll":
        assert body in read(TZ_LL)
        return
    section = theory.resolve(ref)
    assert body in theory.own_text(section), (
        f"{fname}: quote marked §{ref} is not a verbatim part of THEORY.md §{section.id}")


def test_section_status_marks_match_theory(theory):
    text = read(THEORY_MD)
    # the preamble's claims about status marks
    expected = {"3.1": "[проверено]", "3.2": "[проверено]", "3.3": "[проверено]",
                "1.2": "[проверено]", "1.5": "[проверено]", "3.4": "[доказано]", "3.5": None,
                "1.4": "[источники и границы указаны отдельно]"}
    for sid, status in expected.items():
        assert theory.resolve(sid).status == status, sid
    assert "§3.5 has **no status mark**" in text
    assert "§3.4 is **[доказано]**" in text
    # unread / unverified sources the skill must keep flagging, as THEORY.md states them
    t31 = theory.own_text(theory.resolve("3.1"))
    t32 = theory.own_text(theory.resolve("3.2"))
    assert "[AU]\nне прочитан" in t31 and "[AU]" in text
    assert "в этой ревизии не прочитано" in t32 and "Kurki-Suonio" in text


def test_metadata_sections_and_get_theorem_ids_resolve(theory):
    skill = read(SKILL_MD)
    m = re.search(r'theory_sections: "([^"]+)"', skill)
    assert m
    for sid in m.group(1).split():
        theory.resolve(sid)
    for path in SKILL_FILES:
        for sid in re.findall(r'get_theorem\(id="([^"]+)"\)', read(path)):
            theory.resolve(sid)


# ---------------------------------------------------------------------------
# Tool calls written in the skill
# ---------------------------------------------------------------------------

def test_tool_calls_name_registered_tools_and_real_arguments(registry):
    seen = set()
    for path in SKILL_FILES:
        for name, argtext in CALL_RE.findall(read(path)):
            if name in NON_REGISTRY_TOOLS or name in NOT_TOOL_CALLS:
                continue
            assert name in registry.names(), f"{path.name}: `{name}(...)` is not a registered tool"
            fields = registry.get(name).args_schema.model_fields
            for arg in re.findall(r"(?:^|,\s*)(\w+)=", argtext):
                assert arg in fields, f"{path.name}: `{name}` has no argument {arg!r}"
            seen.add(name)
    assert seen >= {"ll_table", "first_follow", "ll_parse", "grammar_transform", "grammar_vs_ir",
                    "validate_ir", "check_membership", "get_theorem", "list_theorems"}


def test_transform_ops_named_in_skill_exist():
    ops = set(ll_tools.TransformOp.__args__)
    for path in SKILL_FILES:
        for oplist in re.findall(r"ops=\[([^\]]*)\]", read(path)):
            for op in re.findall(r'"(\w+)"', oplist):
                assert op in ops, op


def test_max_k_in_skill_is_the_tool_maximum():
    assert ll_tools.LL_MAX_K == 12
    assert "max_k=12" in read(SKILL_MD)


def test_skill_does_not_recommend_unimplemented_tools():
    for path in SKILL_FILES:
        text = read(path)
        for missing in ("ll_claim_check", "pump_check_reg"):
            assert missing not in text, f"{path.name} names the unimplemented tool {missing}"


# ---------------------------------------------------------------------------
# Example IRs (SKILL.md §5.1)
# ---------------------------------------------------------------------------

def _ref_ll07(w: str) -> bool:
    m = re.fullmatch(r"(a*)(b*)", w)
    return bool(m) and len(m.group(1)) >= len(m.group(2))


def _ref_ll05(w: str) -> bool:
    if w.count("c") != 1:
        return False
    left, right = w.split("c")
    if "c" in left or set(right) - {"a", "b"}:
        return False
    for split in range(len(left) + 1):
        x, tail = left[:split], left[split:]
        if set(tail) <= {"b"} and right == x[::-1]:
            return True
    return False


def _ref_ll02(w: str) -> bool:
    m = re.fullmatch(r"(a*)(b*|c*)", w)
    return bool(m) and len(m.group(1)) == len(m.group(2))


REFERENCE: dict[str, tuple[Callable[[str], bool], int]] = {
    "ll-07": (_ref_ll07, 8), "ll-05": (_ref_ll05, 6), "ll-02": (_ref_ll02, 6),
}


def skill_example_irs() -> dict[str, dict[str, Any]]:
    blocks = re.findall(r"```json (ll-\d+)\n(.*?)\n```", read(SKILL_MD), re.S)
    return {tid: json.loads(body) for tid, body in blocks}


def test_skill_has_the_three_example_irs():
    assert set(skill_example_irs()) == set(REFERENCE)


@pytest.mark.parametrize("task_id", sorted(REFERENCE))
def test_example_ir_validates_and_oracle_is_exact(registry, task_id):
    ir = skill_example_irs()[task_id]
    res = invoke(registry, "validate_ir", {"cls": "ll", "ir": ir})
    assert res["ok"] and res["trust"] == "well_formed" and res["data"]["valid"], res
    oracle = oracle_for("ll", ir)
    assert oracle.exact, oracle.describe()
    ref, max_len = REFERENCE[task_id]
    alphabet = ir["language_spec"]["alphabet"]
    for n in range(max_len + 1):
        for letters in itertools.product(alphabet, repeat=n):
            w = "".join(letters)
            assert oracle.query(w)[0] is ref(w), (task_id, w)


def test_eval_ll02_oracle_is_inexact_as_the_skill_says():
    ir = json.loads((ROOT / "ll_system/examples/format1_anbn_union_ancn.json").read_text(encoding="utf-8"))
    assert not oracle_for("ll", ir).exact
    assert "comment-only constraint" in read(SKILL_MD)


def test_natural_ir_has_no_ll_oracle(registry):
    res = invoke(registry, "grammar_vs_ir", {"grammar": T_B_GRAMMAR, "ir": eval_ir("ll-06"), "cls": "ll"})
    assert not res["ok"] and res["trust"] == "not_verified"
    assert "no LL oracle for kind 'natural'" in (res.get("error") or "")
    for tid in ("ll-01", "ll-03", "ll-04", "ll-05", "ll-06", "ll-07", "ll-12", "ll-13", "ll-14", "ll-16"):
        assert eval_ir(tid)["language_spec"]["kind"] == "natural", tid


# ---------------------------------------------------------------------------
# Tool outcomes the skill promises (SKILL.md §3/§4/§5, traps.md §1)
# ---------------------------------------------------------------------------

def table(registry, ir: dict[str, Any] | None = None, grammar: dict[str, Any] | None = None, **kw):
    args: dict[str, Any] = {"include_table": False, **kw}
    if ir is not None:
        args["ir"] = ir
    else:
        args["grammar"] = grammar
    res = invoke(registry, "ll_table", args)
    assert res["ok"], res
    return res


def test_ll08_strong_vs_full(registry):
    res = table(registry, eval_ir("ll-08"), k=2)
    assert res["trust"] == "verified" and res["data"]["is_ll_k"] is True
    assert res["data"]["is_strong_ll_k"] is False
    res = table(registry, eval_ir("ll-08"), max_k=12)
    assert res["trust"] == "verified" and res["data"]["k"] == 2 and res["data"]["strong_k"] == 3


def test_ll09_plain_sigma(registry):
    assert table(registry, eval_ir("ll-09"), k=2)["trust"] == "refuted"
    assert table(registry, eval_ir("ll-09"), k=3)["trust"] == "verified"


def test_ll10_left_recursion_certificate_is_about_the_grammar(registry):
    res = table(registry, eval_ir("ll-10"), max_k=12)
    assert res["trust"] == "verified"
    assert res["data"]["certificate"]["type"] == "left_recursion"
    assert res["data"]["scope"] == "grammar"


def test_ll11_grammar_is_not_language(registry):
    ir = eval_ir("ll-11")
    given = table(registry, ir, max_k=12)
    assert given["data"]["certificate"]["type"] == "left_recursion"
    tr = invoke(registry, "grammar_transform", {"ir": ir, "ops": ["eliminate_left_recursion"]})
    assert tr["ok"] and tr["trust"] == "bounded_pass", tr
    g2 = tr["data"]["grammar"]
    res = table(registry, grammar=g2, max_k=12)
    assert res["trust"] == "verified" and res["data"]["k"] == 1
    gvi = invoke(registry, "grammar_vs_ir", {"grammar": g2, "ir": ir, "cls": "ll"})
    assert gvi["ok"] and gvi["trust"] == "bounded_pass", gvi


def test_ll15_search_to_twelve(registry):
    ir = eval_ir("ll-15")
    short = table(registry, ir, max_k=10)
    assert short["trust"] == "bounded_pass" and short["bounds"]["max_k"] == 10
    assert "k ≤ 10" in short["data"]["claim"]
    full = table(registry, ir, max_k=12)
    assert full["trust"] == "verified" and full["data"]["k"] == 12


def test_ll17_and_ll18(registry):
    assert table(registry, eval_ir("ll-17"), k=1)["trust"] == "verified"
    ir = eval_ir("ll-18")
    assert table(registry, ir, k=1)["trust"] == "refuted"
    res = table(registry, ir, max_k=12)
    assert res["trust"] == "verified" and res["data"]["k"] == 2


# S → TB, T → aTb | ε, B → bB | ε: the LL(1) grammar for {aⁱbʲ | i ≤ j} that THEORY §3.4 gives
T_B_GRAMMAR = {
    "terminals": ["a", "b"], "nonterminals": ["S", "T", "B"], "start": "S",
    "rules": [{"lhs": "S", "rhs": ["T", "B"]}, {"lhs": "T", "rhs": ["a", "T", "b"]},
              {"lhs": "T", "rhs": []}, {"lhs": "B", "rhs": ["b", "B"]}, {"lhs": "B", "rhs": []}],
}
LL06_IR = {
    "task_type": "ll_check_language", "source_text": "{a^i b^j | i <= j}",
    "language_spec": {"kind": "set_builder", "alphabet": ["a", "b"],
                      "variables": [{"name": "i", "domain": {"type": "nat"}},
                                    {"name": "j", "domain": {"type": "nat"}}],
                      "template": ["aⁱ", "bʲ"], "constraints": [{"op": "leq", "left": "i", "right": "j"}]},
}


def test_constructive_path_reaches_bounded_pass(registry):
    assert table(registry, grammar=T_B_GRAMMAR, k=1)["trust"] == "verified"
    res = invoke(registry, "grammar_vs_ir", {"grammar": T_B_GRAMMAR, "ir": LL06_IR, "cls": "ll"})
    assert res["ok"] and res["trust"] == "bounded_pass", res
    assert res["bounds"]["n_words"] >= 30 and res["bounds"]["n_unknown"] == 0


def test_constructive_path_refutes_the_mirror_language(registry):
    # the ll-06 grammar against the ll-07 IR (the mirror trap): a counterexample, never a pass
    ir07 = skill_example_irs()["ll-07"]
    res = invoke(registry, "grammar_vs_ir", {"grammar": T_B_GRAMMAR, "ir": ir07, "cls": "ll"})
    assert res["ok"] and res["trust"] == "refuted", res


def test_witness_check_for_branch_words(registry):
    # SKILL.md §6 step 2: instantiate the ll-02 branch words at n = k + 2 and check them with
    # expected labels; the substituted word aⁿb^{n′} (n ≠ n′) is outside L.
    ir = skill_example_irs()["ll-02"]
    expected = {"aaabbb": True, "aaaccc": True, "aaaabbbb": True, "aaaacccc": True,
                "aaabbbb": False, "aaaabbb": False}
    res = invoke(registry, "check_membership",
                 {"cls": "ll", "ir": ir, "words": list(expected), "expected": expected})
    assert res["ok"] and res["trust"] == "bounded_pass", res
    wrong = dict(expected, aaabbbb=True)
    res = invoke(registry, "check_membership",
                 {"cls": "ll", "ir": ir, "words": list(wrong), "expected": wrong})
    assert res["trust"] == "refuted"


# ---------------------------------------------------------------------------
# traps.md covers the eval LL tasks with the manifest's expected verdicts
# ---------------------------------------------------------------------------

def test_trap_table_matches_manifest():
    manifest = json.loads((ROOT / "tfl_eval" / "manifest.json").read_text(encoding="utf-8"))
    tasks = manifest if isinstance(manifest, list) else manifest.get("tasks", manifest)
    ll_tasks = {t["id"]: t for t in tasks if t["id"].startswith("ll-")}
    rows = {}
    for line in read(TRAPS_MD).splitlines():
        m = re.match(r"\| (ll-\d+) \| (.*?) \| (.*?) \| (.*) \|$", line)
        if m:
            rows[m.group(1)] = (m.group(2), m.group(3), m.group(4))
    assert set(rows) == set(ll_tasks)
    for tid, task in ll_tasks.items():
        _, expected_col, trap_col = rows[tid]
        exp = task["expected"]
        if exp["verdict"] == "not_ll":
            assert "not_ll" in expected_col or "not LL" in expected_col, tid
        else:
            k = exp["k"]
            assert f"k = {k}" in expected_col or f"LL({k})" in expected_col, tid
        if task.get("trap"):
            assert "**[trap:" in trap_col or "**[trap" in trap_col, f"{tid} is a trap in the manifest"


def test_regular_route_dfa_grammar_is_ll1(registry):
    # SKILL.md §5.3 step 1 / traps ll-13: regex → DFA → Q → aδ(Q,a), Q → ε for accepting Q,
    # dead state dropped → LL(1).
    dfa = invoke(registry, "regex_to_dfa", {"regex": "(ab)*c"})["data"]["dfa"]
    trans = dfa["transitions"]
    live = {q for q in dfa["states"] if q in dfa["accept"]}
    changed = True
    while changed:  # states from which an accepting state is reachable
        changed = False
        for q in dfa["states"]:
            if q not in live and any(t in live for t in trans.get(q, {}).values()):
                live.add(q)
                changed = True
    nts = {q: f"Q{i}" for i, q in enumerate(sorted(live))}
    rules = [{"lhs": nts[q], "rhs": [a, nts[t]]}
             for q in sorted(live) for a, t in sorted(trans.get(q, {}).items()) if t in live]
    rules += [{"lhs": nts[q], "rhs": []} for q in sorted(live) if q in dfa["accept"]]
    g = {"terminals": dfa["alphabet"], "nonterminals": sorted(nts.values()),
         "start": nts[dfa["start"]], "rules": rules}
    assert table(registry, grammar=g, k=1)["trust"] == "verified"
    for w, accepted in (("c", True), ("abc", True), ("ababc", True), ("ab", False), ("ac", False)):
        res = invoke(registry, "ll_parse", {"grammar": g, "k": 1, "word": w})
        assert res["ok"] and res["data"]["accepted"] is accepted, (w, res)
