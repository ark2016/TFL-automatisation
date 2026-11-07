"""Theory lookup over docs/THEORY.md statements marked [проверено] (P1).

Two tools of the ``theory`` group (ceiling ``not_verified``):

* ``list_theorems`` -- the section index of ``docs/THEORY.md``: id, heading,
  status marker (``[проверено]``, ``[проверено перебором]``, ``[доказано ...]``,
  ...), class, line range; optional filters by class / task type / marker and a
  plain substring search.
* ``get_theorem`` -- one section's text **verbatim** (PLAN non-negotiable 5:
  the agent quotes theory, it never paraphrases it from memory), with its
  status marker, line range and a citation string, paged by ``offset``.

There is no lib function behind these tools (TOOL_INVENTORY §7): the file is
parsed here. The parser only splits the file at Markdown headings of levels
1-3 (outside code fences) and copies text; it never rewrites a statement.

Section ids follow the file's own numbering: ``"1.1"``, ``"2.0"``, ``"3.4"``.
Part I and Part II share one numbering, so a number that occurs in both parts
(today only ``4``: the two checklists) gets part-qualified ids ``"I.4"`` /
``"II.4"``; every section can also be addressed as ``"<part>.<number>"``
(``"II.1.10"``). The level-1 part heads are ``"I"`` (title, sources legend)
and ``"II"``. A ``§`` prefix and a trailing dot are accepted.

Class tags come from the file's numbering convention (Part II preamble: "§0 --
REG, §1.6-1.9 -- DCFL, §2.3-2.4 -- CFL, §3.4-3.5 -- LL, §4 -- чеклист"):
top-level number 0 -> reg, 1 -> dcfl, 2 -> cfl, 3 -> ll; the part heads, the
repository checklists (§4) and the live-run log (§5) are ``meta``.

Trust: always ``not_verified``. A theorem text is reference material, not
evidence about the task language (VERDICT_POLICY §1: reference /
``not_applicable`` -> ``not_verified``); a ``[проверено]`` marker says the
*statement* was checked during the revision, it does not check the agent's use
of it. The registry clamps to the group ceiling anyway, and the tools may not
report ``refuted``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import threading
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from agent_system.lib.ir_schema import _VALID_TASK_TYPES as _REG_TASK_TYPES
from cfl_system.lib.cfl_ir_schema import _CFL_TASK_TYPES
from dcfl_system.lib.dcfl_ir_schema import _DCFL_TASK_TYPES
from ll_system.lib.ll_ir_schema import _LL_TASK_TYPES
from tfl_deep_agent.tools.base import (
    ToolArgs,
    ToolInputError,
    ToolResult,
    ToolUnsupported,
    tfl_tool,
)

__all__ = [
    "THEORY_PATH", "THEORY_REL_PATH", "TheorySection", "TheoryDoc",
    "parse_theory", "load_theory", "theory_class_for_task_type",
    "ListTheoremsArgs", "GetTheoremArgs", "list_theorems", "get_theorem",
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
#: The one source of theory statements (CLAUDE.md). Module-level so tests can
#: point it at a fixture file.
THEORY_PATH: Path = _REPO_ROOT / "docs" / "THEORY.md"
THEORY_REL_PATH = "docs/THEORY.md"

TheoryClass = Literal["reg", "cfl", "dcfl", "ll", "meta"]

#: Top-level section number -> class (THEORY.md Part II preamble).
_CLASS_BY_TOP_NUMBER: dict[str, str] = {"0": "reg", "1": "dcfl", "2": "cfl", "3": "ll"}

#: task_type -> class, from the four IR validators' own task-type sets.
_TASK_TYPE_CLASS: dict[str, str] = {
    **{t: "reg" for t in _REG_TASK_TYPES},
    **{t: "cfl" for t in _CFL_TASK_TYPES},
    **{t: "dcfl" for t in _DCFL_TASK_TYPES},
    **{t: "ll" for t in _LL_TASK_TYPES},
}

_HEADING_RE = re.compile(r"^(#{1,3})[ \t]+(\S.*?)[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(```|~~~)")
_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?[ \t]+(.*)$")
#: ``— **[проверено]**`` in a heading; the dash before the marker goes with it.
_STATUS_RE = re.compile(r"[ \t]*[—–-]?[ \t]*\*\*\[([^\]\n]+)\]\*\*")
_ERROR_NOTE = "**[ошибка]**"
_RULE_RE = re.compile(r"^[ \t]*(-{3,}|\*{3,}|_{3,})[ \t]*$")
_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")

_MAX_HITS = 3
_HIT_CHARS = 240


def theory_class_for_task_type(task_type: str) -> str:
    """The theory class of an IR ``task_type`` (the validators' sets); raises
    :class:`ToolInputError` for an unknown type."""
    try:
        return _TASK_TYPE_CLASS[task_type]
    except KeyError:
        raise ToolInputError(
            f"unknown task_type {task_type!r}; known: {sorted(_TASK_TYPE_CLASS)}") from None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class TheorySection:
    """One heading of THEORY.md and the text under it (copied, never edited).

    ``line_start`` is the heading line (1-based). ``line_end`` ends the
    section's own text (up to the next heading of any level);
    ``full_line_end`` ends it together with its subsections. Trailing blank
    lines and ``---`` rules are not part of either range."""

    id: str
    part: str
    number: str | None
    level: int
    heading: str
    title: str
    status: str | None
    cls: str
    parent: str | None
    children: tuple[str, ...]
    line_start: int
    line_end: int
    full_line_end: int
    error_notes: int

    @property
    def label(self) -> str:
        """``§1.1`` for a numbered section, else the id."""
        return f"§{self.number}" if self.number else self.id


@dataclasses.dataclass(frozen=True)
class TheoryDoc:
    """A parsed THEORY.md: the lines (newline-normalised) and the sections."""

    lines: tuple[str, ...]
    sections: tuple[TheorySection, ...]
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {s.id: s for s in self.sections})
        aliases: dict[str, list[str]] = {}
        for s in self.sections:
            keys = {s.id.lower()}
            if s.number is not None:
                keys.add(f"{s.part}.{s.number}".lower())
                keys.add(s.number.lower())
            for k in keys:
                aliases.setdefault(k, [])
                if s.id not in aliases[k]:
                    aliases[k].append(s.id)
        object.__setattr__(self, "_aliases", aliases)

    def ids(self) -> list[str]:
        return [s.id for s in self.sections]

    def get(self, section_id: str) -> TheorySection:
        return self._by_id[section_id]  # type: ignore[attr-defined]

    def resolve(self, ref: str) -> TheorySection:
        """Section for an id or alias (``"1.1"``, ``"§1.1."``, ``"II.1.10"``)."""
        key = _normalise_ref(ref).lower()
        found = self._aliases.get(key, [])  # type: ignore[attr-defined]
        if len(found) == 1:
            return self.get(found[0])
        if len(found) > 1:
            raise ToolInputError(
                f"theorem id {ref!r} is ambiguous: one of {found}", data={"candidates": found})
        raise ToolInputError(
            f"unknown theorem id {ref!r}; call list_theorems for the ids. Known: {self.ids()}",
            data={"known_ids": self.ids()})

    def own_text(self, s: TheorySection) -> str:
        return "\n".join(self.lines[s.line_start - 1: s.line_end])

    def full_text(self, s: TheorySection) -> str:
        return "\n".join(self.lines[s.line_start - 1: s.full_line_end])


def _normalise_ref(ref: str) -> str:
    text = ref.strip()
    if text.startswith("§"):
        text = text[1:].strip()
    return text.rstrip(".").strip()


def _trim_end(lines: tuple[str, ...], start: int, end: int) -> int:
    """Last line (1-based, ≥ start) of lines[start-1:end] that is not blank or a rule."""
    while end > start and (not lines[end - 1].strip() or _RULE_RE.match(lines[end - 1])):
        end -= 1
    return end


def parse_theory(text: str, *, sha256: str = "") -> TheoryDoc:
    """Split THEORY.md at its level-1..3 headings (outside code fences)."""
    lines = tuple(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    if lines and lines[-1] == "":
        lines = lines[:-1]

    heads: list[tuple[int, int, str]] = []  # (line_no, level, heading text)
    in_fence = False
    for no, line in enumerate(lines, start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            heads.append((no, len(m.group(1)), m.group(2)))

    # First pass: part, number, title, status, ranges.
    raw: list[dict] = []
    part_index = -1
    for i, (no, level, heading) in enumerate(heads):
        if level == 1:
            part_index += 1
        idx = max(part_index, 0)  # headings before the first '#' belong to part I
        part = _ROMAN[idx] if idx < len(_ROMAN) else f"P{idx + 1}"
        number = None
        rest = heading
        if level > 1:
            nm = _NUMBER_RE.match(heading)
            if nm:
                number, rest = nm.group(1), nm.group(2)
        statuses = list(_STATUS_RE.finditer(rest))
        status = None
        title = rest
        if statuses:
            last = statuses[-1]
            status = f"[{last.group(1).strip()}]"
            title = rest[: last.start()] + rest[last.end():]
        title = title.strip().rstrip("—–-").strip() or rest.strip()

        next_any = heads[i + 1][0] if i + 1 < len(heads) else len(lines) + 1
        next_same = len(lines) + 1
        for no2, level2, _ in heads[i + 1:]:
            if level2 <= level:
                next_same = no2
                break
        own_end = _trim_end(lines, no, next_any - 1)
        full_end = _trim_end(lines, no, next_same - 1)
        own = "\n".join(lines[no - 1: own_end])

        if level == 1:
            cls = "meta"
        elif number is not None:
            cls = _CLASS_BY_TOP_NUMBER.get(number.split(".")[0], "meta")
        else:
            cls = "meta"
        raw.append(dict(part=part, number=number, level=level, heading=heading, title=title,
                        status=status, cls=cls, line_start=no, line_end=own_end,
                        full_line_end=full_end, error_notes=own.count(_ERROR_NOTE)))

    # Ids: the bare number when it is unique in the file, else part-qualified.
    counts: dict[str, int] = {}
    for r in raw:
        if r["number"] is not None:
            counts[r["number"]] = counts.get(r["number"], 0) + 1
    for r in raw:
        if r["level"] == 1:
            r["id"] = r["part"]
        elif r["number"] is None:
            r["id"] = f"{r['part']}.h{r['line_start']}"
        elif counts[r["number"]] > 1:
            r["id"] = f"{r['part']}.{r['number']}"
        else:
            r["id"] = r["number"]

    # Parent / children: the nearest earlier heading of a lower level.
    stack: list[dict] = []
    children: dict[str, list[str]] = {r["id"]: [] for r in raw}
    for r in raw:
        while stack and stack[-1]["level"] >= r["level"]:
            stack.pop()
        r["parent"] = stack[-1]["id"] if stack else None
        if r["parent"] is not None:
            children[r["parent"]].append(r["id"])
        stack.append(r)

    sections = tuple(
        TheorySection(children=tuple(children[r["id"]]), **r) for r in raw)
    return TheoryDoc(lines=lines, sections=sections, sha256=sha256)


_cache_lock = threading.Lock()
_cache: dict[tuple[str, int, int], TheoryDoc] = {}


def load_theory(path: Path | str | None = None) -> TheoryDoc:
    """Parse THEORY.md (default :data:`THEORY_PATH`), cached by path, mtime and size."""
    p = Path(path) if path is not None else THEORY_PATH
    try:
        st = p.stat()
    except OSError:
        raise ToolUnsupported(
            f"{THEORY_REL_PATH} is not available at {p}; theory tools need the repository checkout"
        ) from None
    key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
    with _cache_lock:
        doc = _cache.get(key)
    if doc is not None:
        return doc
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolUnsupported(f"{p} is not UTF-8: {exc}") from None
    doc = parse_theory(text, sha256=hashlib.sha256(raw).hexdigest())
    if not doc.sections:
        raise ToolUnsupported(f"{p} has no Markdown headings; nothing to index")
    with _cache_lock:
        _cache.clear()
        _cache[key] = doc
    return doc


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

_TRUST_REASON = (
    "reference text quoted verbatim from docs/THEORY.md; a theory statement is not evidence "
    "about this task's language, and its status marker does not raise trust "
    "(VERDICT_POLICY §1: reference -> not_verified)")


def _section_entry(s: TheorySection, doc: TheoryDoc) -> dict:
    return {
        "id": s.id, "part": s.part, "number": s.number, "level": s.level,
        "title": s.title, "heading": s.heading, "status": s.status, "cls": s.cls,
        "parent": s.parent, "children": list(s.children),
        "line_start": s.line_start, "line_end": s.line_end, "full_line_end": s.full_line_end,
        "chars": len(doc.own_text(s)), "error_notes": s.error_notes,
    }


class ListTheoremsArgs(ToolArgs):
    cls: Literal["all", "reg", "cfl", "dcfl", "ll"] = Field(
        default="all",
        description="Language class whose sections to list (by THEORY.md numbering: §0 REG, "
                    "§1.x DCFL, §2.x CFL, §3.x LL). 'all' lists every class.")
    task_type: str | None = Field(
        default=None, min_length=1, max_length=40, pattern=r"^[a-z_]+$",
        description="Optional IR task_type (e.g. 'classify_and_prove_dcfl', 'll_check_grammar'); "
                    "selects the class for you. Must agree with cls when both are given.")
    marked_only: bool = Field(
        default=False,
        description="Only sections whose heading carries a status marker such as "
                    "[проверено], [проверено перебором] or [доказано ...].")
    include_meta: bool = Field(
        default=False,
        description="Also list the non-theory sections: the part heads (sources legend), the "
                    "repository checklists (§4) and the live-run log (§5).")
    query: str | None = Field(
        default=None, min_length=2, max_length=100,
        description="Optional case-insensitive substring searched in each section's heading and "
                    "own text (e.g. 'Огден', 'Shallit', 'aSSb', 'dcfl_exam_02'). Matching lines "
                    "are returned verbatim as hits.")
    max_results: int = Field(default=60, ge=1, le=100, description="Maximum sections returned.")

    @model_validator(mode="after")
    def _query_not_blank(self) -> ListTheoremsArgs:
        if self.query is not None and not self.query.strip():
            raise ValueError("query must not be blank")
        return self


@tfl_tool(group="theory", max_trust="not_verified", timeout_s=10, allow_refuted=False)
def list_theorems(args: ListTheoremsArgs) -> ToolResult:
    """List the sections of docs/THEORY.md, the repository's only source of theorem statements
    (Yu's pumping lemma for DCFL, Shallit's theorem, the continuation lemma, DCFL closure, CFL
    pumping quantifiers and Ogden, LL(k) vs strong LL(k), how to prove "not LL(k) for any k", ...).

    Each entry gives the section id (pass it to get_theorem), heading, status marker verbatim
    ("[проверено]" = checked against a source or proved in the file; "[проверено перебором]" = only
    the stated finite range was enumerated; "[доказано ...]" = proved in the file;
    "[источники и границы указаны отдельно]" = see the section for sources and limits; null = no
    marker), class, line range and the number of "[ошибка]" notes (paragraphs that quote a WRONG
    formulation in order to correct it). Quote statements with get_theorem; never restate a lemma
    from memory. Reference only: trust is always not_verified."""
    doc = load_theory()
    cls = args.cls
    if args.task_type is not None:
        derived = theory_class_for_task_type(args.task_type)
        if cls != "all" and cls != derived:
            raise ToolInputError(
                f"cls={cls!r} contradicts task_type={args.task_type!r} (class {derived!r})")
        cls = derived
    needle = args.query.strip().lower() if args.query else None

    out: list[dict] = []
    matched = 0
    for s in doc.sections:
        if s.cls == "meta":
            if not args.include_meta:
                continue
        elif cls != "all" and s.cls != cls:
            continue
        if args.marked_only and s.status is None:
            continue
        entry = _section_entry(s, doc)
        if needle is not None:
            hits = []
            for no in range(s.line_start, s.line_end + 1):
                line = doc.lines[no - 1]
                if needle in line.lower():
                    hits.append({"line": no, "text": line if len(line) <= _HIT_CHARS
                                 else line[:_HIT_CHARS] + " …"})
            if not hits:
                continue
            entry["hit_count"] = len(hits)
            entry["hits"] = hits[:_MAX_HITS]
        matched += 1
        if len(out) < args.max_results:
            out.append(entry)

    data = {
        "source": THEORY_REL_PATH, "sha256": doc.sha256,
        "filters": {"cls": cls, "task_type": args.task_type, "marked_only": args.marked_only,
                    "include_meta": args.include_meta, "query": args.query},
        "count": len(out), "matched": matched, "truncated": matched > len(out),
        "total_sections": len(doc.sections),
        "sections": out,
    }
    return ToolResult.success(data, trust="not_verified", reason=_TRUST_REASON)


class GetTheoremArgs(ToolArgs):
    id: str = Field(
        min_length=1, max_length=24, pattern=r"^\s*(§\s*)?[A-Za-z0-9.]+\s*$",
        description="Section id from list_theorems, e.g. '1.1' (Yu's lemma), '1.2' (Shallit), "
                    "'3.3', 'II.4', or 'I' (title and sources legend). A leading '§' and a "
                    "trailing '.' are accepted.")
    include_subsections: bool = Field(
        default=False,
        description="Return the section together with its subsections (e.g. '1' with 1.1-1.5) "
                    "instead of only its own text up to the next heading.")
    offset: int = Field(
        default=0, ge=0, le=500_000,
        description="Character offset into the section text, for reading a long section in pages "
                    "(use next_offset from the previous call).")
    max_chars: int = Field(
        default=8000, ge=500, le=15_000,
        description="Maximum characters of text returned in this call; the cut falls on a line "
                    "end when possible.")


@tfl_tool(group="theory", max_trust="not_verified", timeout_s=10, allow_refuted=False)
def get_theorem(args: GetTheoremArgs) -> ToolResult:
    """Return one section of docs/THEORY.md verbatim: the exact text (heading line included), its
    status marker, line range and a citation string such as "docs/THEORY.md §1.1, lines 50-110".

    Quote the returned text (or a contiguous excerpt of it) in proofs and reports instead of
    paraphrasing; cite the section. Paragraphs marked "[ошибка]" quote a WRONG formulation that the
    section corrects: never use them as statements. "[проверено перебором]" covers only the finite
    range the section states. Long sections come in pages: when truncated is true, call again with
    offset=next_offset. Reference only: trust is always not_verified."""
    doc = load_theory()
    s = doc.resolve(args.id)
    text = doc.full_text(s) if args.include_subsections else doc.own_text(s)
    if args.offset > len(text):
        raise ToolInputError(f"offset {args.offset} is past the end of section {s.id} "
                             f"({len(text)} chars)")
    end = min(len(text), args.offset + args.max_chars)
    if end < len(text):
        cut = text.rfind("\n", args.offset, end)
        if cut > args.offset + args.max_chars // 2:
            end = cut + 1
    chunk = text[args.offset:end]
    first_line = s.line_start + text.count("\n", 0, args.offset)
    last_line = first_line + chunk.rstrip("\n").count("\n")
    full_end = s.full_line_end if args.include_subsections else s.line_end
    data = {
        "id": s.id, "part": s.part, "number": s.number, "level": s.level,
        "title": s.title, "heading": s.heading, "status": s.status, "cls": s.cls,
        "parent": s.parent, "children": list(s.children),
        "include_subsections": args.include_subsections,
        "line_start": s.line_start, "line_end": full_end,
        "chunk_line_start": first_line, "chunk_line_end": last_line,
        "text": chunk, "chars": len(text), "offset": args.offset,
        "truncated": end < len(text), "next_offset": end if end < len(text) else None,
        "error_notes": doc.full_text(s).count(_ERROR_NOTE) if args.include_subsections
        else s.error_notes,
        "source": THEORY_REL_PATH, "sha256": doc.sha256,
        "citation": f"{THEORY_REL_PATH} {s.label}, lines {first_line}-{last_line}",
    }
    return ToolResult.success(data, trust="not_verified", reason=_TRUST_REASON)
