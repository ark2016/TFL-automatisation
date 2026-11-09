"""P1: tools/theory.py -- list_theorems / get_theorem over docs/THEORY.md.

No LLM, no network. The text checks compare against the file read
independently here, so "verbatim" means byte-for-byte the file's lines."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tfl_deep_agent.tools import theory
from tfl_deep_agent.tools.base import GROUP_MAX_TRUST, REGISTRY, load_groups

ROOT = Path(__file__).resolve().parents[2]
THEORY_FILE = ROOT / "docs" / "THEORY.md"
FILE_LINES = THEORY_FILE.read_text(encoding="utf-8").splitlines()

load_groups(["theory"])

EXAMPLE_DIRS = {
    "reg": ROOT / "agent_system" / "examples",
    "cfl": ROOT / "cfl_system" / "examples",
    "dcfl": ROOT / "dcfl_system" / "examples",
    "ll": ROOT / "ll_system" / "examples",
}


def _example_tasks() -> list[tuple[str, Path, dict[str, Any]]]:
    out = []
    for cls, d in EXAMPLE_DIRS.items():
        for f in sorted(d.glob("*.json")):
            ir = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(ir, dict) and "task_type" in ir:
                out.append((cls, f, ir))
    return out


EXAMPLES = _example_tasks()


def _ex(cls: str, name: str) -> dict[str, Any]:
    path = EXAMPLE_DIRS[cls] / name
    return json.loads(path.read_text(encoding="utf-8"))


def invoke(name: str, args: dict[str, Any]):
    return REGISTRY.invoke(name, args)


def lines(a: int, b: int) -> str:
    return "\n".join(FILE_LINES[a - 1: b])


# ---------------------------------------------------------------------------
# Registration and trust
# ---------------------------------------------------------------------------

def test_tools_registered_in_theory_group():
    assert set(REGISTRY.names("theory")) >= {"list_theorems", "get_theorem"}
    for name in ("list_theorems", "get_theorem"):
        spec = REGISTRY.get(name)
        assert spec.group == "theory"
        assert spec.ceiling == "not_verified" == GROUP_MAX_TRUST["theory"]
        assert spec.allow_refuted is False
        assert len(spec.description) > 200  # the model reads it


def test_args_schemas_describe_every_field():
    for name in ("list_theorems", "get_theorem"):
        schema = REGISTRY.get(name).args_schema.model_json_schema()
        for field, prop in schema["properties"].items():
            assert prop.get("description"), f"{name}.{field} has no description"


def test_every_success_is_not_verified_reference():
    for args in ({}, {"cls": "dcfl", "marked_only": True}, {"query": "Огден"}):
        r = invoke("list_theorems", args)
        assert r.ok and r.trust == "not_verified" and "verbatim" in r.trust_reason
    r = invoke("get_theorem", {"id": "1.1"})
    assert r.ok and r.trust == "not_verified" and r.bounds is None


# ---------------------------------------------------------------------------
# Index of the real THEORY.md
# ---------------------------------------------------------------------------

# TOOL_INVENTORY §7: the status marks at 752a0b5.
EXPECTED_STATUS = {
    "1.1": "[проверено]", "1.2": "[проверено]", "1.3": "[проверено]", "1.5": "[проверено]",
    "3.1": "[проверено]", "3.2": "[проверено]", "3.3": "[проверено]",
    "1.4": "[источники и границы указаны отдельно]",
    "2": "[проверено перебором]", "2.5": "[проверено перебором]",
    "0": "[доказано индукцией и Нероудом]",
    "1.10": "[доказано конструкцией, проверено перебором]",
    "3.4": "[доказано]",
}


def test_status_markers_match_the_file():
    doc = theory.load_theory()
    for sid, status in EXPECTED_STATUS.items():
        s = doc.get(sid)
        assert s.status == status, sid
        assert status in FILE_LINES[s.line_start - 1]  # taken from the heading line
    unmarked = {s.id for s in doc.sections if s.status is None}
    assert {"2.0", "1.6", "1.7", "2.3", "2.4", "3.5"} <= unmarked


def test_ids_unique_and_duplicate_numbers_part_qualified():
    doc = theory.load_theory()
    ids = doc.ids()
    assert len(ids) == len(set(ids))
    assert "I.4" in ids and "II.4" in ids and "4" not in ids
    assert {"I", "II", "0", "1", "1.1", "1.10", "2.0", "2", "3.3", "3.4", "5"} <= set(ids)
    assert doc.get("1.10").part == "II" and doc.get("1.1").part == "I"


def test_classes_follow_file_numbering():
    doc = theory.load_theory()
    cls = {s.id: s.cls for s in doc.sections}
    assert cls["0"] == "reg"
    assert {cls[i] for i in ("1", "1.1", "1.4", "1.6", "1.10")} == {"dcfl"}
    assert {cls[i] for i in ("2.0", "2", "2.3", "2.5")} == {"cfl"}
    assert {cls[i] for i in ("3", "3.1", "3.3", "3.4", "3.5")} == {"ll"}
    assert {cls[i] for i in ("I", "II", "I.4", "II.4", "5")} == {"meta"}
    # sanity: the class headings say so themselves
    assert doc.get("1").title == "DCFL" and doc.get("3").title == "LL(k)"


def test_titles_drop_number_and_marker_only():
    doc = theory.load_theory()
    assert doc.get("1.1").title == "Лемма о накачке для DCFL (двухсловная, [Yu])"
    assert doc.get("3.4").title == "LL: лемма об ограниченной гибкости; два DCFL-не-LL языка"
    assert doc.get("1.10").title.startswith("task_grammar_aSSb (dcfl_exam_04)")
    assert doc.get("1.10").heading == FILE_LINES[doc.get("1.10").line_start - 1].lstrip("# ")


def test_parent_child_structure():
    doc = theory.load_theory()
    assert doc.get("1").children == ("1.1", "1.2", "1.3", "1.4", "1.5")
    assert doc.get("1.1").parent == "1" and doc.get("1").parent == "I"
    assert doc.get("1.6").parent == "II"
    assert doc.get("3").children == ("3.1", "3.2", "3.3")


def test_list_default_excludes_meta_and_reports_hash():
    r = invoke("list_theorems", {})
    ids = [s["id"] for s in r.data["sections"]]
    assert "I" not in ids and "II.4" not in ids and "5" not in ids
    assert "1.1" in ids and "0" in ids and "3.4" in ids
    assert r.data["source"] == "docs/THEORY.md" and len(r.data["sha256"]) == 64
    assert r.data["total_sections"] > r.data["count"]
    with_meta = invoke("list_theorems", {"include_meta": True})
    assert with_meta.data["count"] == with_meta.data["total_sections"]


def test_marked_only_filter():
    r = invoke("list_theorems", {"marked_only": True})
    got = {s["id"]: s["status"] for s in r.data["sections"]}
    assert got == EXPECTED_STATUS


def test_error_notes_counted():
    doc = theory.load_theory()
    for s in doc.sections:
        assert s.error_notes == doc.own_text(s).count("**[ошибка]**")
    assert doc.get("1.1").error_notes == 1 and doc.get("3.3").error_notes == 2


def test_max_results_truncates():
    r = invoke("list_theorems", {"max_results": 3})
    assert r.data["count"] == 3 and r.data["truncated"] is True and r.data["matched"] > 3


# ---------------------------------------------------------------------------
# get_theorem: verbatim text
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", sorted(EXPECTED_STATUS))
def test_get_theorem_text_is_verbatim(sid):
    r = invoke("get_theorem", {"id": sid, "max_chars": 15000})
    d = r.data
    assert r.ok and d["truncated"] is False and d["next_offset"] is None
    assert d["text"] == lines(d["line_start"], d["line_end"])
    assert d["status"] == EXPECTED_STATUS[sid]
    assert d["text"].splitlines()[0] == FILE_LINES[d["line_start"] - 1]
    assert d["citation"] == f"docs/THEORY.md §{sid}, lines {d['line_start']}-{d['line_end']}"


def test_yu_lemma_text_contains_both_conditions():
    d = invoke("get_theorem", {"id": "1.1"}).data
    assert "x = x₁x₂x₃x₄x₅, |x₂x₄| ≥ 1, |x₂x₃x₄| ≤ p" in d["text"]
    assert "x₁x₂ⁱx₃·y₁y₂ⁱy₃ ∈ L" in d["text"]


def test_section_text_stops_before_rule_and_next_heading():
    d = invoke("get_theorem", {"id": "1.5"}).data
    assert d["text"].rstrip().endswith("Неоднозначность **грамматики** не говорит ничего о языке (Format 2).")
    assert "---" not in d["text"] and "## 2.0" not in d["text"]


def test_include_subsections_concatenates_children_verbatim():
    own = invoke("get_theorem", {"id": "1"}).data
    full = invoke("get_theorem", {"id": "1", "include_subsections": True, "max_chars": 15000}).data
    assert "### 1.1." not in own["text"] and "### 1.5." in full["text"]
    assert full["text"] == lines(42, full["line_end"])
    assert full["error_notes"] == full["text"].count("**[ошибка]**")


@pytest.mark.parametrize("ref", ["1.10", "§1.10", "§ 1.10.", "II.1.10", "ii.1.10", " 1.10 "])
def test_id_aliases(ref):
    assert invoke("get_theorem", {"id": ref}).data["id"] == "1.10"


def test_part_head_holds_the_sources_legend():
    d = invoke("get_theorem", {"id": "I"}).data
    assert d["cls"] == "meta" and "**[Yu]**" in d["text"] and "**[Sh]**" in d["text"]


def test_paging_reassembles_the_full_text():
    sid = "1.10"
    whole = invoke("get_theorem", {"id": sid, "max_chars": 15000}).data
    assert whole["truncated"] is False
    parts, offset, chunks = [], 0, 0
    while True:
        d = invoke("get_theorem", {"id": sid, "offset": offset, "max_chars": 1000}).data
        parts.append(d["text"])
        chunks += 1
        assert len(d["text"]) <= 1000
        # each page starts on a line of the file and cites its own lines
        assert d["text"].split("\n")[0] == FILE_LINES[d["chunk_line_start"] - 1][: len(d["text"].split("\n")[0])]
        if not d["truncated"]:
            break
        offset = d["next_offset"]
    assert chunks > 3 and "".join(parts) == whole["text"]


def test_content_for_model_is_not_shortened_at_max_chars():
    r = invoke("get_theorem", {"id": "1", "include_subsections": True, "max_chars": 15000})
    content = json.loads(r.to_content())
    assert content["data"]["text"] == r.data["text"]


# ---------------------------------------------------------------------------
# Real example tasks from the four pipelines
# ---------------------------------------------------------------------------

def test_examples_found():
    assert {cls for cls, _, _ in EXAMPLES} == {"reg", "cfl", "dcfl", "ll"}


@pytest.mark.parametrize("cls,path,ir", EXAMPLES, ids=[f"{c}/{p.name}" for c, p, _ in EXAMPLES])
def test_task_type_of_every_example_selects_its_class(cls, path, ir):
    r = invoke("list_theorems", {"task_type": ir["task_type"]})
    assert r.ok, r.error
    assert r.data["filters"]["cls"] == cls
    assert r.data["count"] > 0 and {s["cls"] for s in r.data["sections"]} == {cls}
    # each listed section is retrievable verbatim
    first = r.data["sections"][0]
    got = invoke("get_theorem", {"id": first["id"]}).data
    assert got["text"] == lines(first["line_start"], first["line_end"])


@pytest.mark.parametrize("cls,example,query,expected_id", [
    ("reg", "task2_grammar_sasb.json", "SaSb", "0"),
    ("cfl", "task_wwvvR.json", "wwvvR", "2.5"),
    ("cfl", "task_grammar_filter_49.json", "grammar_filter_49", "2.3"),
    ("dcfl", "task_grammar_aSSb.json", "dcfl_exam_04", "1.10"),
    ("dcfl", "task_u1au2_u3au4.json", "dcfl_exam_02", "1.7"),
    ("dcfl", "task_wvaavRwR.json", "dcfl_exam_01", "1.6"),
    ("ll", "format1_anbn_union_ancn.json", "{aⁿbⁿ} ∪ {aⁿcⁿ}", "3.3"),
])
def test_example_task_finds_its_theory_section(cls, example, query, expected_id):
    ir = _ex(cls, example)
    r = invoke("list_theorems", {"task_type": ir["task_type"], "query": query})
    assert r.ok, r.error
    ids = [s["id"] for s in r.data["sections"]]
    assert expected_id in ids
    sec = next(s for s in r.data["sections"] if s["id"] == expected_id)
    for hit in sec["hits"]:
        assert FILE_LINES[hit["line"] - 1].startswith(hit["text"].removesuffix(" …"))
        assert query.lower() in hit["text"].lower()


def test_exam_04_status_and_verdict_are_quoted_not_inferred():
    ir = _ex("dcfl", "task_grammar_aSSb.json")
    sec = invoke("list_theorems", {"task_type": ir["task_type"], "query": ir["task_id"]}).data
    hit = next(s for s in sec["sections"] if s["id"] == "1.10")
    assert hit["status"] == "[доказано конструкцией, проверено перебором]"
    text = invoke("get_theorem", {"id": hit["id"], "max_chars": 15000}).data["text"]
    assert "**Теорема.** L ∈ DCFL." in text


def test_cfl_examples_reach_pumping_quantifiers():
    ir = _ex("cfl", "task_anbncn.json")
    r = invoke("list_theorems", {"task_type": ir["task_type"], "query": "для всех"})
    assert "2.0" in [s["id"] for s in r.data["sections"]]


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------

def test_get_unknown_id_fails_with_known_ids():
    r = invoke("get_theorem", {"id": "9.9"})
    assert not r.ok and r.trust == "not_verified" and r.error_kind == "invalid_input"
    assert "1.1" in r.data["known_ids"]


def test_get_ambiguous_number_fails():
    r = invoke("get_theorem", {"id": "4"})
    assert not r.ok and r.error_kind == "invalid_input"
    assert r.data["candidates"] == ["I.4", "II.4"]


@pytest.mark.parametrize("bad", [
    {"id": ""}, {"id": "1.1; rm -rf"}, {"id": "x" * 30},
    {"id": "1.1", "max_chars": 100}, {"id": "1.1", "max_chars": 100_000},
    {"id": "1.1", "offset": -1},
    {"id": "1.1", "trust": "verified"},
    {"id": "1.1", "confidence": 0.9},
    {},
])
def test_get_theorem_rejects_bad_args(bad):
    r = invoke("get_theorem", bad)
    assert not r.ok and r.error_kind == "invalid_input" and r.trust == "not_verified"


def test_get_offset_past_end_fails():
    r = invoke("get_theorem", {"id": "1.5", "offset": 100_000})
    assert not r.ok and r.error_kind == "invalid_input" and "past the end" in r.error


@pytest.mark.parametrize("bad", [
    {"cls": "regular"}, {"query": "a"}, {"query": "   "}, {"query": "x" * 101},
    {"max_results": 0}, {"task_type": "Classify!"}, {"trust": "verified"},
])
def test_list_theorems_rejects_bad_args(bad):
    r = invoke("list_theorems", bad)
    assert not r.ok and r.error_kind == "invalid_input"


def test_list_unknown_task_type_fails():
    r = invoke("list_theorems", {"task_type": "prove_turing_complete"})
    assert not r.ok and r.error_kind == "invalid_input" and "unknown task_type" in r.error


def test_list_task_type_contradicting_cls_fails():
    ir = _ex("ll", "format3_simple_ll1.json")
    r = invoke("list_theorems", {"cls": "cfl", "task_type": ir["task_type"]})
    assert not r.ok and r.error_kind == "invalid_input" and "contradicts" in r.error


def test_list_query_without_match_is_empty_not_error():
    r = invoke("list_theorems", {"query": "квантовый компьютер"})
    assert r.ok and r.data["count"] == 0 and r.data["sections"] == []


def test_missing_theory_file_is_unsupported(monkeypatch, tmp_path):
    monkeypatch.setattr(theory, "THEORY_PATH", tmp_path / "nope.md")
    for name, args in (("list_theorems", {}), ("get_theorem", {"id": "1.1"})):
        r = invoke(name, args)
        assert not r.ok and r.error_kind == "unsupported" and r.trust == "not_verified"


def test_file_without_headings_is_unsupported(monkeypatch, tmp_path):
    p = tmp_path / "THEORY.md"
    p.write_text("just text\nno headings\n", encoding="utf-8")
    monkeypatch.setattr(theory, "THEORY_PATH", p)
    r = invoke("list_theorems", {})
    assert not r.ok and r.error_kind == "unsupported"


# ---------------------------------------------------------------------------
# Parser on a fixture
# ---------------------------------------------------------------------------

FIXTURE = """# Part one
intro line

## 1. Alpha — **[проверено]**
alpha body
**[ошибка]** wrong claim

### 1.1. Alpha one
```
# not a heading inside a fence
```
tail

---

## 2. Beta
beta

# Part two

## 2. Beta again — **[доказано]**; with tail
b2

## Unnumbered
u
"""


def test_parser_on_fixture():
    doc = theory.parse_theory(FIXTURE)
    assert doc.ids() == ["I", "1", "1.1", "I.2", "II", "II.2", "II.h24"]
    a = doc.get("1")
    assert a.status == "[проверено]" and a.title == "Alpha" and a.error_notes == 1
    assert a.children == ("1.1",) and a.parent == "I"
    one = doc.get("1.1")
    assert doc.own_text(one).splitlines()[-1] == "tail"  # rule and blank trimmed
    assert "# not a heading" in doc.own_text(one)
    b2 = doc.get("II.2")
    assert b2.title == "Beta again; with tail" and b2.status == "[доказано]" and b2.cls == "cfl"
    assert doc.get("II.h24").cls == "meta"
    with pytest.raises(theory.ToolInputError):
        doc.resolve("2")
    assert doc.resolve("ii.2").id == "II.2"


def test_parser_accepts_crlf():
    doc = theory.parse_theory(FIXTURE.replace("\n", "\r\n"))
    assert doc.ids() == theory.parse_theory(FIXTURE).ids()
    assert "\r" not in doc.own_text(doc.get("1"))


def test_load_theory_cache_follows_file_changes(monkeypatch, tmp_path):
    import os
    p = tmp_path / "THEORY.md"
    p.write_text("# T\n\n## 1. A\nx\n", encoding="utf-8")
    first = theory.load_theory(p)
    assert theory.load_theory(p) is first
    p.write_text("# T\n\n## 1. A\nx\n\n## 2. B\ny\n", encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert theory.load_theory(p).ids() == ["I", "1", "2"]


# ---------------------------------------------------------------------------
# LangChain path
# ---------------------------------------------------------------------------

def test_langchain_tool_returns_verbatim_text_and_artifact():
    from langchain_core.messages import ToolMessage

    lc = REGISTRY.as_langchain_tool("get_theorem")
    msg = lc.invoke({"name": "get_theorem", "args": {"id": "1.2"}, "id": "c1", "type": "tool_call"})
    assert isinstance(msg, ToolMessage)
    assert msg.artifact["trust"] == "not_verified" and msg.artifact["data"]["id"] == "1.2"
    content = json.loads(msg.content)
    s = theory.load_theory().get("1.2")
    assert content["data"]["text"] == lines(s.line_start, s.line_end)


def test_langchain_rejects_smuggled_trust():
    lc = REGISTRY.as_langchain_tool("list_theorems")
    msg = lc.invoke({"name": "list_theorems", "args": {"trust": "verified"}, "id": "c2",
                     "type": "tool_call"})
    body = json.loads(msg.content)
    assert body["ok"] is False and body["error_kind"] == "invalid_input"
