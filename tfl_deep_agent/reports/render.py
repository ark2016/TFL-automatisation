"""Render a deep-agent report to ``result.json`` / ``result.md`` / ``result.html`` (P3).

Input: the run's result dict (:func:`tfl_deep_agent.agent.collect_result`:
``status``, ``report`` -- the fields the model passed to ``submit_report`` --,
``trust`` / ``confidence_cap`` / ``verdict_gate`` computed by code,
``evidence``, ``partial_report``, ``human_review`` ...), the evidence ledger and
the budget ledger. Output: :class:`RenderedReport` (Markdown, HTML, JSON data).

What is the model's and what is code's:

* the model's fields (verdict, method, claims, steps, conclusion, ...) are
  substituted into a template of ``skills/report-templates/`` with
  :class:`string.Template`. Lines of model text that start with ``#``, ``>``
  or three backticks are escaped, so model text cannot open a section, a
  callout or a code block of its own;
* the document reads as a solution (2026-11-08, "report as a solution
  document"): title, "Условие", the "Ответ" box (the published verdict in
  words, the class, "Уверенность: N%" = the code-set ceiling, one line on
  human review, and a warning callout whenever trust is below ``verified``),
  "Решение" (idea and method, lemmas, the construction, the steps
  "**Шаг k. title**" + claim + "*Обоснование:*"), "Заключение", the Lean
  section, and the appendix "Проверка";
* every trust label, the confidence ceiling, the small per-step marks
  ("(проверено: regex_to_dfa ✓)", only for a step whose own trust is at least
  ``well_formed``), the gate notes ("держится только на словах модели",
  "id нет в журнале", refuted citations, budget refusals), the theory
  citations (resolved against docs/THEORY.md by section id), the objects read
  from tool results (a DFA transition table, a grammar, an LL table), the Lean
  status (read from ``lean_check`` ledger entries) and the appendix
  "Проверка" are generated here from the ledgers. The appendix is appended
  after the template, so no template can drop it;
* a run without an accepted report (``budget_exhausted``, ``incomplete``,
  ``error``) is rendered with the internal ``partial`` template from
  ``result["partial_report"]`` / the ledger: verdict "не установлен", trust
  ``not_verified``;
* a run that no human approved (``--auto-approve``, or HITL 2 not held) is
  marked "без проверки человеком"; fields a human edited are listed.

The verdict's trust comes from ``result["trust"]`` (the agent's verdict gate).
The renderer never raises it. Only when the result has no trust at all does
it run :func:`tfl_deep_agent.agent.verdict_gate` itself over the ledger.

The evidence ledger is read duck-typed, so the renderer does not depend on
the ledger's class: a path to ``evidence.jsonl``, a list of mappings, or an
object with ``entries`` (attribute or method) / ``records``. An entry needs
``id``; it may carry ``tool``, ``ok``, ``trust``, ``trust_reason``, ``bounds``,
``error``, ``role`` (or ``agent`` / ``scope`` / ``node``), ``model``,
``cost_usd`` (or ``usd``), ``data``, ``args``. The budget ledger is a
:class:`tfl_deep_agent.middleware.budget.BudgetLedger` (or a node of one), or
its ``summary()`` dict, optionally with ``records``.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import html as _html
import json
import os
import re
import string
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_system.lib.renderer import _TRUST_LABELS_RU, _esc

from . import diagrams as _diagrams

__all__ = [
    "EVIDENCE_BLOCK_TITLE",
    "MARK",
    "NO_HUMAN_REVIEW_MARK",
    "PARTIAL_TEMPLATE",
    "PLACEHOLDERS",
    "SELECTABLE_TEMPLATES",
    "TEMPLATES_DIR",
    "TEMPLATE_INFO",
    "RenderedReport",
    "ledger_integrity",
    "load_template",
    "markdown_to_html",
    "normalize_budget",
    "normalize_evidence",
    "normalize_review",
    "render_report",
    "safe_block",
    "safe_text",
    "template_placeholders",
    "trust_label",
    "write_report",
]

PKG_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PKG_DIR / "skills" / "report-templates"

#: Templates the model may choose in ``submit_report``.
SELECTABLE_TEMPLATES: tuple[str, ...] = ("full_proof", "exam_solution", "short_verdict", "lean_appendix")
#: Internal template for a run without an accepted report.
PARTIAL_TEMPLATE = "partial"

#: What each template is for (``list_report_templates`` shows this).
TEMPLATE_INFO: Mapping[str, Mapping[str, Any]] = {
    "full_proof": {
        "title": "Полное доказательство",
        "when": "the default: task, answer, the solution (idea and method, lemmas, construction, key "
                "claims, every proof step with its justification), conclusion, the check appendix "
                "(the structure of the pipelines' result.md files)",
        "fields": ["verdict", "answer", "language_class", "method", "hypothesis", "lemmas", "claims",
                   "construction", "steps", "conclusion", "evidence_ids", "ir_fingerprint", "lean"],
    },
    "exam_solution": {
        "title": "Решение для сдачи",
        "when": "a compact solution to hand in: answer, idea, the chosen word or construction, the key "
                "steps as a numbered list, conclusion",
        "fields": ["verdict", "answer", "language_class", "method", "construction", "steps", "conclusion",
                   "evidence_ids", "ir_fingerprint", "lean"],
    },
    "short_verdict": {
        "title": "Краткий вердикт",
        "when": "a short answer: verdict, one or two sentences (summary), the construction and the "
                "steps as a short numbered list, plus the check appendix",
        "fields": ["verdict", "answer", "language_class", "method", "summary", "construction", "steps",
                   "evidence_ids", "ir_fingerprint"],
    },
    "lean_appendix": {
        "title": "Доказательство с Lean-приложением",
        "when": "full_proof plus the Lean section (status, axioms, statement, proof body, proof file). "
                "The Lean section is also appended to any other template when the report has a lean "
                "block or the run has a lean_check result",
        "fields": ["verdict", "answer", "language_class", "method", "hypothesis", "lemmas", "construction",
                   "steps", "conclusion", "lean", "evidence_ids", "ir_fingerprint"],
    },
}

#: Every placeholder a template may use (string.Template ``$name``).
PLACEHOLDERS: frozenset[str] = frozenset({
    "title", "header", "task", "verdict_block", "idea_section", "method", "hypothesis_section",
    "lemmas_section", "claims_section", "steps", "key_steps", "construction_section", "conclusion",
    "summary", "lean_section", "lean_status", "lean_statement", "lean_proof_body", "lean_note",
    "partial_banner", "established", "refuted", "subagents_section", "todos_section",
    "last_message_section",
})

#: The heading of the code-generated appendix (evidence, gate notes, models, costs).
EVIDENCE_BLOCK_TITLE = "Проверка"
NO_HUMAN_REVIEW_MARK = "без проверки человеком"

#: Prefix of a code-made evidence mark ("(проверено: regex_to_dfa ✓)"). An
#: invisible character that :func:`safe_text` strips from model text, so the
#: model cannot write a mark the HTML would style as a code-made one.
MARK = "⁣"

_TRUST_LEVELS = ("refuted", "not_verified", "well_formed", "bounded_pass", "verified")
_TRUST_RANK = {t: i for i, t in enumerate(_TRUST_LEVELS)}
_CAPS = {"verified": 0.98, "bounded_pass": 0.85, "well_formed": 0.55, "not_verified": 0.40}

_TITLES = {
    "reg": "Анализ регулярности языка",
    "cfl": "Анализ КС-свойства",
    "dcfl": "Анализ ДКС-свойства (DCFL)",
    "ll": "Анализ LL(k)-свойства",
}

_CLASS_LABELS = {
    "reg": "REG",
    "cfl": "CFL (контекстно-свободные языки)",
    "dcfl": "DCFL (детерминированные КС-языки)",
    "ll": "LL(k)",
}

#: The published verdict token in words (``agent.VERDICT_TOKENS``). The LL
#: verdict is printed in the model's words (they carry k and say whether the
#: grammar or the language is meant).
_TOKEN_WORDS = {
    "regular": "язык регулярен",
    "non_regular": "язык не регулярен",
    "cfl": "язык контекстно-свободный",
    "non_cfl": "язык не контекстно-свободный",
    "dcfl": "язык детерминированный контекстно-свободный",
    "non_dcfl": "язык не детерминированный контекстно-свободный",
}
_DECISIVE_TOKENS = frozenset({*_TOKEN_WORDS, "ll", "not_ll"})

#: What a tool checks, in the appendix's words (fallback: the tool's trust reason).
_TOOL_WHAT = {
    "describe_ir_schema": "справка по схеме IR",
    "validate_ir": "формализация задачи (IR): структура",
    "ir_to_text": "пересказ IR",
    "sample_words": "примеры слов языка задачи",
    "check_membership": "принадлежность слов языку задачи",
    "regex_to_dfa": "ДКА по выражению или грамматике",
    "run_dfa": "прогон ДКА на словах",
    "nerode_probe": "различимость слов (Майхилл–Нероуд)",
    "dfa_vs_ir": "ДКА против языка задачи",
    "pump_check_reg": "накачка (регулярные языки)",
    "to_cnf": "приведение грамматики к НФХ",
    "cyk": "разбор слов (CYK)",
    "pda_run": "прогон МП-автомата",
    "parikh": "образ Париха",
    "pump_check_cfl": "накачка (КС-языки)",
    "grammar_vs_ir": "грамматика против языка задачи",
    "input_grammar_cfg": "грамматика из условия",
    "dpda_run": "прогон ДМП-автомата",
    "dpda_vs_ir": "ДМП-автомат против языка задачи",
    "closure_lookup": "свойства замкнутости",
    "pattern_match": "сопоставление с типовыми задачами",
    "first_follow": "множества FIRST/FOLLOW",
    "ll_table": "таблица LL(k) и конфликты",
    "ll_parse": "разбор слова LL-анализатором",
    "grammar_transform": "преобразование грамматики",
    "list_theorems": "справка по docs/THEORY.md",
    "get_theorem": "справка по docs/THEORY.md",
    "lean_statement": "формулировка теоремы Lean",
    "lean_check": "доказательство в Lean 4",
    "list_report_templates": "шаблоны отчёта",
    "submit_report": "сдача отчёта",
    "task": "ответ субагента",
    "write_todos": "план решения",
}

#: Bookkeeping calls left out of the check table (they stay in the full call
#: log) unless the report cites them.
_BOOKKEEPING_TOOLS = frozenset({"write_todos", "submit_report", "list_report_templates", "describe_ir_schema"})

_RESULT_WORDS = {
    "verified": "✓ проверено полностью",
    "bounded_pass": "✓ проверено выборочно",
    "well_formed": "корректно оформлено",
    "not_verified": "не проверено",
    "refuted": "✗ опровергнуто",
}

_STATUS_LABELS = {
    "submitted": "отчёт сдан",
    "approved": "отчёт утверждён",
    "awaiting_human": "черновик, ожидает проверки человеком",
    "budget_exhausted": "бюджет исчерпан, отчёт частичный",
    "incomplete": "прогон завершился без отчёта",
    "error": "ошибка прогона",
}

_LEAN_TOOLS = ("lean_check",)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _template_path(name: str) -> Path:
    if name not in SELECTABLE_TEMPLATES and name != PARTIAL_TEMPLATE:
        raise ValueError(f"unknown report template {name!r}; one of {SELECTABLE_TEMPLATES}")
    return TEMPLATES_DIR / f"{name}.md"


def load_template(name: str) -> string.Template:
    """The ``string.Template`` of ``skills/report-templates/<name>.md``."""
    return string.Template(_template_path(name).read_text(encoding="utf-8"))


def template_placeholders(name: str) -> set[str]:
    """Placeholder names a template uses."""
    return set(load_template(name).get_identifiers())


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def trust_label(trust: str | None) -> str:
    """Russian trust words (VERDICT_POLICY §5), e.g. "проверено выборочно"."""
    return _TRUST_LABELS_RU.get(trust or "", "не проверено")


def _valid_trust(trust: Any) -> str:
    return trust if isinstance(trust, str) and trust in _TRUST_RANK else "not_verified"


def _strongest(trusts: Iterable[str]) -> str:
    best = "not_verified"
    for t in trusts:
        if t != "refuted" and _TRUST_RANK.get(t, 0) > _TRUST_RANK[best]:
            best = t
    return best


_BLOCK_START_RE = re.compile(r"^(\s*)(#|>|```)")


def safe_text(text: Any, *, max_chars: int | None = None) -> str:
    """Model (or task) text made safe for the report's Markdown: a line that
    would open a heading, a callout / quote or a code fence is escaped with a
    backslash, so it stays a plain paragraph line."""
    s = "" if text is None else str(text).replace(MARK, "")
    s = s.replace("\r\n", "\n").replace("\r", "\n").strip()
    if max_chars is not None and len(s) > max_chars:
        s = s[: max_chars - 1].rstrip() + "…"
    return "\n".join(_BLOCK_START_RE.sub(r"\1\\\2", line) for line in s.split("\n"))


_FENCE_LANG_RE = re.compile(r"[^A-Za-z0-9_+-]")


def safe_block(text: Any, *, max_chars: int | None = None) -> str:
    """Like :func:`safe_text`, but a fenced code block of the model (three
    backticks on a line of their own) stays a code block: the renderer closes
    it itself, so an unclosed fence cannot swallow the rest of the report, and
    the text outside the fences is escaped as by :func:`safe_text`. Used for
    the ``construction`` field (an automaton, a grammar, a table)."""
    s = "" if text is None else str(text).replace(MARK, "")
    s = s.replace("\r\n", "\n").replace("\r", "\n").strip()
    if max_chars is not None and len(s) > max_chars:
        s = s[: max_chars - 1].rstrip() + "…"
    out: list[str] = []
    prose: list[str] = []
    code: list[str] | None = None
    lang = ""
    for line in s.split("\n"):
        st = line.strip()
        if st.startswith("```"):
            if code is None:
                if prose:
                    out.append(safe_text("\n".join(prose)))
                    prose = []
                code, lang = [], _FENCE_LANG_RE.sub("", st[3:].strip())[:20]
            else:
                out.append(f"```{lang}\n" + "\n".join(code) + "\n```")
                code = None
            continue
        (prose if code is None else code).append(line)
    if code is not None:
        out.append(f"```{lang}\n" + "\n".join(code) + "\n```")
    if prose:
        out.append(safe_text("\n".join(prose)))
    return "\n\n".join(x for x in out if x.strip())


def _one_line(text: Any, max_chars: int = 200) -> str:
    s = " ".join(str(text or "").replace(MARK, "").split())
    return s if len(s) <= max_chars else s[: max_chars - 1] + "…"


def _cell(text: Any, max_chars: int = 200) -> str:
    """A Markdown table cell: one line, pipes escaped."""
    return _one_line(text, max_chars).replace("|", "\\|")


def _code(text: Any) -> str:
    return "`" + str(text).replace("`", "'").replace("\n", " ") + "`"


def _money(x: Any) -> str:
    try:
        return f"${float(x):.4f}"
    except (TypeError, ValueError):
        return "n/a"


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# Evidence ledger
# ---------------------------------------------------------------------------

def _as_mapping(item: Any) -> Mapping[str, Any] | None:
    if isinstance(item, Mapping):
        return item
    for attr in ("to_dict", "model_dump", "dict"):
        fn = getattr(item, attr, None)
        if callable(fn):
            try:
                out = fn()
            except TypeError:
                continue
            if isinstance(out, Mapping):
                return out
    if dataclasses.is_dataclass(item) and not isinstance(item, type):
        return dataclasses.asdict(item)
    return None


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn last line of an interrupted run
        if isinstance(obj, Mapping):
            out.append(obj)
    return out


def _raw_entries(source: Any) -> list[Any]:
    if source is None:
        return []
    if isinstance(source, (str, os.PathLike)):
        p = Path(source)
        return list(_read_jsonl(p / "evidence.jsonl" if p.is_dir() else p))
    if isinstance(source, Mapping):
        inner = source.get("entries", source.get("evidence"))
        return list(inner) if isinstance(inner, Iterable) and not isinstance(inner, (str, bytes)) else []
    for attr in ("entries", "records"):
        if hasattr(source, attr):
            val = getattr(source, attr)
            val = val() if callable(val) else val
            return list(val or [])
    path = getattr(source, "path", None)
    if isinstance(path, (str, os.PathLike)):
        return list(_read_jsonl(Path(path)))
    if isinstance(source, Iterable) and not isinstance(source, (bytes, bytearray)):
        return list(source)
    raise TypeError(f"cannot read an evidence ledger from {type(source).__name__}")


def _norm_entry(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    eid = raw.get("id", raw.get("evidence_id", raw.get("tool_call_id")))
    if eid is None or str(eid) == "":
        return None
    result = raw.get("result") if isinstance(raw.get("result"), Mapping) else {}

    def pick(*keys: str) -> Any:
        for k in keys:
            if raw.get(k) is not None:
                return raw[k]
            if result.get(k) is not None:
                return result[k]
        return None

    ok = pick("ok")
    error = pick("error")
    entry: dict[str, Any] = {
        "id": str(eid),
        "tool": pick("tool", "name", "tool_name") or "?",
        "ok": bool(ok) if ok is not None else error is None,
        "trust": _valid_trust(pick("trust")),
        "trust_reason": pick("trust_reason") or None,
        "bounds": pick("bounds"),
        "error": error,
        "role": pick("role", "agent", "subagent"),
        "scope": pick("scope", "node"),
        "model": pick("model"),
        "cost_usd": pick("cost_usd", "usd", "cost"),
        "turn_usd": pick("turn_usd"),
        "has_tool_result": pick("has_tool_result"),
        "ir_fingerprint": pick("ir_fingerprint"),
        "ts": pick("ts", "time"),
    }
    data = pick("data")
    if isinstance(data, Mapping):
        entry["data"] = dict(data)
        if entry["ir_fingerprint"] is None and data.get("ir_fingerprint"):
            entry["ir_fingerprint"] = data.get("ir_fingerprint")
    args = pick("args", "arguments")
    if isinstance(args, Mapping):
        entry["args"] = dict(args)
    return entry


def normalize_evidence(*sources: Any) -> list[dict[str, Any]]:
    """One list of ledger entries, keyed by ``id`` in first-seen order. An
    entry seen again (the ledger and the result's own ``evidence``) only
    fills the fields the first one lacks."""
    by_id: dict[str, dict[str, Any]] = {}
    for source in sources:
        for item in _raw_entries(source):
            m = _as_mapping(item)
            if m is None:
                continue
            e = _norm_entry(m)
            if e is None:
                continue
            old = by_id.get(e["id"])
            if old is None:
                by_id[e["id"]] = e
            else:
                for k, v in e.items():
                    if old.get(k) is None and v is not None:
                        old[k] = v
    return list(by_id.values())


def ledger_integrity(source: Any) -> list[str] | None:
    """Hash-chain problems of the evidence ledger (``[]`` = intact), via its
    ``verify()``; a path is loaded with ``EvidenceLedger.load``. ``None`` when
    the source cannot be checked (a plain list, no ledger module)."""
    if source is None:
        return None
    try:
        if isinstance(source, (str, os.PathLike)):
            from ..evidence.ledger import EvidenceLedger

            source = EvidenceLedger.load(source)
        verify = getattr(source, "verify", None)
        if not callable(verify):
            return None
        return [str(p) for p in verify()]
    except Exception as exc:  # noqa: BLE001 - an unreadable ledger is itself a finding
        return [f"ledger not readable: {_one_line(exc, 200)}"]


# ---------------------------------------------------------------------------
# Budget ledger
# ---------------------------------------------------------------------------

def _walk_refusals(tree: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stack = [tree] if isinstance(tree, Mapping) else []
    while stack:
        n = stack.pop()
        if n.get("refusals"):
            out.append({"node": n.get("node"), "refusals": n.get("refusals"), "state": n.get("state")})
        stack.extend(reversed([c for c in n.get("children") or [] if isinstance(c, Mapping)]))
    return out


def normalize_budget(budget: Any) -> dict[str, Any] | None:
    """Totals, cost by model (calls, tokens, cache) and by role, and the
    scopes that refused a call, from a ``BudgetLedger`` / ``BudgetNode`` or a
    summary dict. ``None`` when no budget information was given."""
    if budget is None:
        return None
    ledger = getattr(budget, "ledger", None)
    if ledger is not None and hasattr(ledger, "summary"):
        budget = ledger  # a BudgetNode -> its ledger
    if hasattr(budget, "summary") and callable(budget.summary):
        summary = dict(budget.summary())
        records = [_as_mapping(r) or {} for r in getattr(budget, "records", []) or []]
    elif isinstance(budget, Mapping):
        summary = dict(budget)
        records = [_as_mapping(r) or {} for r in budget.get("records") or []]
    else:
        raise TypeError(f"cannot read a budget ledger from {type(budget).__name__}")

    by_model: dict[str, dict[str, Any]] = {}
    by_role: dict[str, dict[str, Any]] = {}
    for r in records:
        model = str(r.get("model") or "?")
        role = str(r.get("role") or "?")
        m = by_model.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                        "cache_read_tokens": 0, "cache_write_tokens": 0, "usd": 0.0,
                                        "usage_missing": 0})
        m["calls"] += 1
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            m[k] += int(r.get(k) or 0)
        m["usd"] += float(r.get("usd") or 0.0)
        m["usage_missing"] += 1 if r.get("usage_missing") else 0
        ro = by_role.setdefault(role, {"calls": 0, "usd": 0.0})
        ro["calls"] += 1
        ro["usd"] += float(r.get("usd") or 0.0)
    if not records:
        for model, usd in (summary.get("by_model") or {}).items():
            by_model[str(model)] = {"usd": float(usd or 0.0)}
        for role, usd in (summary.get("by_role") or {}).items():
            by_role[str(role)] = {"usd": float(usd or 0.0)}
    tree = summary.get("tree") if isinstance(summary.get("tree"), Mapping) else None
    return {
        "task_id": summary.get("task_id"),
        "spent_usd": float(summary.get("spent_usd") or 0.0),
        "limit_usd": summary.get("limit_usd"),
        "calls": summary.get("calls", len(records)),
        "exhausted": bool(summary.get("exhausted")),
        "state": (tree or {}).get("state"),
        "by_model": by_model,
        "by_role": by_role,
        "refusals": _walk_refusals(tree) if tree else list(summary.get("refusals") or []),
    }


# ---------------------------------------------------------------------------
# Human review
# ---------------------------------------------------------------------------

_DECISIONS = ("approved", "edited", "auto_approved", "rejected", "awaiting", "not_reviewed")
_DECISION_ALIASES = {
    "approve": "approved", "accept": "approved", "accepted": "approved",
    "edit": "edited", "approved_with_edits": "edited",
    "auto": "auto_approved", "auto_approve": "auto_approved", "auto-approved": "auto_approved",
    "auto-approve": "auto_approved",
    "reject": "rejected", "respond": "rejected", "returned": "rejected",
    "awaiting_human": "awaiting", "pending": "awaiting",
}


def normalize_review(value: Any, *, status: str | None = None) -> dict[str, Any]:
    """``{"decision", "reviewed", "label", "edited_fields", "comment", "reviewer"}``.

    ``value`` is a mapping (``decision`` / ``action`` / ``type``,
    ``edited_fields``, ``comment``, ``reviewer``, ``at``) or a string such as
    the CLI's "auto-approved (--auto-approve): without human review". Anything
    that is not an explicit human approval is ``reviewed: False`` and carries
    the "без проверки человеком" mark (awaiting / rejected drafts say so)."""
    decision = "not_reviewed"
    edited: list[str] = []
    comment = reviewer = at = None
    if isinstance(value, Mapping):
        raw = str(value.get("decision") or value.get("action") or value.get("type") or "").strip().lower()
        decision = _DECISION_ALIASES.get(raw, raw) if raw else "not_reviewed"
        edited = [str(f) for f in value.get("edited_fields") or []]
        comment = value.get("comment") or value.get("message")
        reviewer = value.get("reviewer")
        at = value.get("at")
        if decision == "approved" and edited:
            decision = "edited"
    elif isinstance(value, str) and value.strip():
        s = value.strip().lower()
        if "auto" in s:
            decision = "auto_approved"
        elif s.startswith(("edit", "approved with edits")):
            decision = "edited"
        elif s.startswith(("approve", "accept", "утвержд")):
            decision = "approved"
        elif s.startswith(("reject", "return")):
            decision = "rejected"
        elif s.startswith(("awaiting", "pending")):
            decision = "awaiting"
    if decision not in _DECISIONS:
        decision = "not_reviewed"
    if decision == "not_reviewed" and status == "awaiting_human":
        decision = "awaiting"
    who = f" ({reviewer})" if reviewer else ""
    when = f", {at}" if at else ""
    labels = {
        "approved": f"Проверено человеком{who}{when}: отчёт утверждён.",
        "edited": (f"Проверено человеком{who}{when}: отчёт утверждён с правками; изменённые поля: "
                   + (", ".join(_code(f) for f in edited) if edited else "не указаны") + "."),
        "auto_approved": "**Без проверки человеком** (утверждено автоматически: режим подтверждений или `--auto-approve`).",
        "rejected": ("**Без проверки человеком**: черновик возвращён на доработку"
                     + (f" с замечанием: {_one_line(comment, 300)}" if comment else "") + "."),
        "awaiting": "**Без проверки человеком**: черновик ожидает решения человека (HITL 2).",
        "not_reviewed": "**Без проверки человеком.**",
    }
    return {"decision": decision, "reviewed": decision in ("approved", "edited"),
            "label": labels[decision], "edited_fields": edited,
            "comment": None if comment is None else str(comment), "reviewer": reviewer}


# ---------------------------------------------------------------------------
# Claims, steps, theory, Lean: code-made marks
# ---------------------------------------------------------------------------

def _judge(ids: Sequence[str], by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The trust of one claim from the entries it cites -- the rule of
    ``evidence.policy.claim_trust``: the strongest non-refuted trust among the
    successful cited calls, and **an id that is not in the ledger makes the
    claim ``not_verified``** whatever else it cites."""
    ids = [str(i) for i in dict.fromkeys(ids or [])]
    found = [by_id[i] for i in ids if i in by_id]
    unknown = [i for i in ids if i not in by_id]
    refuted = [e["id"] for e in found if e.get("trust") == "refuted"]
    supporting = [e for e in found if e.get("ok") and e.get("trust") != "refuted"]
    trust = _strongest(e["trust"] for e in supporting) if supporting and not unknown else "not_verified"
    return {"evidence_ids": ids, "trust": trust, "found": [e["id"] for e in found],
            "unknown_ids": unknown, "refuted_ids": refuted,
            "model_only": not ids}


_MARK_WORDS = {"verified": "проверено", "bounded_pass": "проверено выборочно",
               "well_formed": "корректно оформлено"}


def _mark(j: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> str:
    """The small code-made mark at the end of a claim or step:
    "(проверено: regex_to_dfa ✓)" when the step's own trust (``_judge``) is
    at least ``well_formed``, "(опровергнуто: run_dfa ✗)" for a cited refuted
    result, "" otherwise. A step without ids or with an id missing from the
    ledger gets no mark here; the appendix "Проверка" lists it."""
    out: list[str] = []
    word = _MARK_WORDS.get(j["trust"])
    if word:
        tools = list(dict.fromkeys(str(by_id[i].get("tool")) for i in j["found"]
                                   if i not in j["refuted_ids"] and by_id[i].get("ok")
                                   and by_id[i].get("trust") in _MARK_WORDS))
        tick = " ✓" if j["trust"] != "well_formed" else ""
        out.append(f"{MARK}({word}: {', '.join(tools)}{tick})")
    if j["refuted_ids"]:
        tools = list(dict.fromkeys(str(by_id[i].get("tool")) for i in j["refuted_ids"]))
        out.append(f"{MARK}(опровергнуто: {', '.join(tools)} ✗)")
    return " ".join(out)


def _with_mark(text: str, mark: str) -> str:
    """``text`` with ``mark`` at the end of its last line, or on a line of its
    own when the last line is a table row, a fence or display math."""
    if not mark:
        return text
    last = text.rstrip().rsplit("\n", 1)[-1].strip()
    if last.startswith(("|", "```", "$$")) or last.endswith("$$"):
        return f"{text}\n\n{mark}"
    return f"{text} {mark}"


def _theory_ref(theorem_id: str) -> dict[str, Any]:
    """Resolve a THEORY.md section id to a citation made by code."""
    try:
        from ..tools.theory import THEORY_REL_PATH, load_theory

        doc = load_theory()
        s = doc.resolve(str(theorem_id))
    except Exception as exc:  # noqa: BLE001 - unknown id, ambiguous id, no checkout
        return {"id": str(theorem_id), "found": False, "error": _one_line(exc, 200)}
    status = f" {s.status}" if s.status else ""
    return {"id": s.id, "found": True, "title": s.title, "status": s.status,
            "short": f"{THEORY_REL_PATH} {s.label} «{s.title}»",
            "citation": f"{THEORY_REL_PATH} {s.label} «{s.title}»{status}, строки {s.line_start}–{s.line_end}"}


def _theory_text(ref: Mapping[str, Any], *, short: bool = False) -> str:
    if ref.get("found"):
        return str(ref["short" if short else "citation"])
    return f"{_code(ref['id'])}: раздела нет в docs/THEORY.md ({ref.get('error')})"


# ---------------------------------------------------------------------------
# Building the placeholders
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _Ctx:
    result: Mapping[str, Any]
    report: Mapping[str, Any]
    status: str
    template: str
    evidence: list[dict[str, Any]]
    by_id: dict[str, dict[str, Any]]
    budget: dict[str, Any] | None
    review: dict[str, Any]
    task: str
    integrity: list[str] | None = None
    usage: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    # code-made DOT source -> inline SVG ("" when dot is unavailable): only these fences become pictures
    diagrams: dict[str, str] = dataclasses.field(default_factory=dict)

    def diagram(self, dot: str | None, caption: str) -> str | None:
        """A ```` ```dot ```` block for a code-made automaton, registered so the HTML draws it."""
        if not dot:
            return None
        if dot not in self.diagrams:
            self.diagrams[dot] = _diagrams.dot_to_svg(dot) or ""
        return f"**{caption}:**\n\n```dot\n{dot}\n```"

    def use(self, ids: Iterable[str], where: str) -> None:
        for i in ids:
            lst = self.usage.setdefault(str(i), [])
            if where not in lst:
                lst.append(where)


def _verdict_trust(ctx: _Ctx) -> dict[str, Any]:
    """The verdict's trust as computed by the agent's gate (``result``), or by
    :func:`agent.verdict_gate` over the ledger when the result has none."""
    res = ctx.result
    if res.get("trust") is not None:
        trust = _valid_trust(res.get("trust"))
        cap = res.get("confidence_cap")
        cap = float(cap) if isinstance(cap, (int, float)) else _CAPS.get(trust, 0.40)
        return {"trust": trust, "confidence_cap": min(cap, _CAPS.get(trust, 0.40)),
                "basis": list(res.get("basis") or []), "verdict_token": res.get("verdict_token"),
                "verdict_gate": list(res.get("verdict_gate") or []), "source": "result"}
    if not ctx.report:
        return {"trust": "not_verified", "confidence_cap": _CAPS["not_verified"], "basis": [],
                "verdict_token": None, "verdict_gate": [], "source": "default"}
    cited = list(ctx.report.get("evidence_ids") or [])
    for item in [*(ctx.report.get("steps") or []), *(ctx.report.get("claims") or [])]:
        cited += list(item.get("evidence_ids") or [])
    lean = ctx.report.get("lean") if isinstance(ctx.report.get("lean"), Mapping) else {}
    cited += list(lean.get("evidence_ids") or [])
    form = ctx.result.get("formalization")
    try:
        from ..agent import verdict_gate

        gate = verdict_gate(ctx.report, ctx.evidence, cited,
                            formalization=form if isinstance(form, Mapping) else None)
    except Exception as exc:  # noqa: BLE001 - no gate available: the safe floor
        return {"trust": "not_verified", "confidence_cap": _CAPS["not_verified"], "basis": [],
                "verdict_token": None, "verdict_gate": [f"verdict gate unavailable: {_one_line(exc)}"],
                "source": "default"}
    return {"trust": gate["trust"], "confidence_cap": gate["confidence_cap"], "basis": gate["basis"],
            "verdict_token": gate["verdict_token"], "verdict_gate": gate["verdict_gate"],
            "source": "verdict_gate"}


def _run_id(ctx: _Ctx) -> str | None:
    rid = (ctx.budget or {}).get("task_id") or ctx.result.get("run_id")
    return str(rid) if rid else None


def _review_short(ctx: _Ctx) -> str:
    """One line on who reviewed what, for the answer box (the full labels are
    in the appendix)."""
    dec = ctx.review["decision"]
    form = ctx.result.get("formalization")
    form = form if isinstance(form, Mapping) else None
    if dec in ("approved", "edited"):
        report_part = "отчёт утверждён человеком с правками" if dec == "edited" else "отчёт проверил человек"
        if form is None:
            return report_part
        if form.get("status") == "approved" and not form.get("auto"):
            return ("формализацию и отчёт проверил человек" if dec == "approved"
                    else "формализацию проверил человек, " + report_part)
        if form.get("status") == "approved":
            return report_part + "; формализация одобрена автоматически"
        return report_part + "; формализация не утверждена человеком"
    if dec == "auto_approved" and form is not None and form.get("status") == "approved" and not form.get("auto"):
        return "формализацию проверил человек, отчёт принят автоматически"
    return {
        "auto_approved": "одобрено автоматически, без проверки человеком",
        "awaiting": "черновик, ожидает проверки человеком",
        "rejected": "черновик возвращён человеком на доработку",
    }.get(dec, "без проверки человеком")


_INTAKE_RE = re.compile(r"^\s*\\?##\s*Tasks\s*$(?P<tasks>.*?)^\s*\\?##\s*Uncertain readings\s*$(?P<unsure>.*)\Z",
                        re.M | re.S)
_INTAKE_NOTE_RE = re.compile(r"^\(.*\bin the file\b.*\)$", re.M)


def _task_statement(task: str) -> str:
    """The task as the report's «Условие» prints it. The intake subagent answers
    a file task in its fixed format (``## Tasks`` / ``## Uncertain readings``,
    prompts/intake.md): keep the task text, drop the headings, and keep any
    uncertain reading, since the human has to see it. Other text is unchanged."""
    m = _INTAKE_RE.match(task)
    if not m:
        return task
    tasks = _INTAKE_NOTE_RE.sub("", m.group("tasks")).strip()
    if not re.search(r"^2\.\s", tasks, re.M):
        tasks = re.sub(r"^1\.\s+", "", tasks)
    unsure = [ln.strip() for ln in m.group("unsure").splitlines() if ln.strip()]
    if not unsure or unsure == ["- none"]:
        return tasks
    return tasks + "\n\nНеуверенное чтение файла:\n" + "\n".join(unsure)


def formalization_label(result: Mapping[str, Any]) -> str | None:
    """What HITL 1 decided, in the report's words (``None`` when HITL 1 is
    off: ``result["formalization"]`` is the binding of ``hitl.formalization_binding``)."""
    form = result.get("formalization")
    if not isinstance(form, Mapping):
        return None
    status, fp = form.get("status"), form.get("ir_fingerprint")
    if status == "not_held":
        return ("**формализация не проверена человеком**: ни ответ ir-builder, ни вызов validate_ir не дошли "
                "до HITL 1, IR никто не утверждал; вердикт `not_verified`")
    if status == "rejected":
        return (f"**формализация отклонена** на HITL 1 ({_code(fp) if fp else 'без IR'}), новый IR после этого "
                "не утверждён; вердикт `not_verified`")
    if status == "no_ir":
        return "**формализация утверждена без IR**: результаты оракулов не к чему привязать; вердикт `not_verified`"
    how = ("утверждена автоматически (`--auto-approve`), **без проверки человеком**" if form.get("auto")
           else "утверждена человеком с правками" if form.get("decision") == "edited"
           else "утверждена человеком")
    return f"{how} на HITL 1, IR {_code(fp)}; учитываются только результаты оракулов для этого IR"


def _ir_line(ctx: _Ctx, basis: Sequence[str]) -> str:
    form = ctx.result.get("formalization")
    if isinstance(form, Mapping):
        label = formalization_label(ctx.result) or ""
        line = f"**Формализация (HITL 1):** {label}."
        rfp = ctx.report.get("ir_fingerprint") if ctx.report else None
        if form.get("status") == "approved" and rfp and rfp != form.get("ir_fingerprint"):
            line += (f" Отчёт называет **другой** IR {_code(rfp)}: проверки оценены относительно утверждённого "
                     f"IR {_code(form.get('ir_fingerprint'))}.")
        excluded = [str(i) for i in ctx.result.get("excluded_ir_mismatch") or []]
        if excluded:
            line += " Исключены результаты для другого IR: " + ", ".join(_code(i) for i in excluded) + "."
        return line
    fp = ctx.report.get("ir_fingerprint") if ctx.report else None
    if not fp:
        fps = sorted({str(ctx.by_id[i]["ir_fingerprint"]) for i in basis
                      if i in ctx.by_id and ctx.by_id[i].get("ir_fingerprint")})
        fp = fps[0] if len(fps) == 1 else None
    if fp:
        return (f"**Проверено относительно IR** {_code(fp)}: оракулы сверяли утверждения с IR, "
                "который построил агент; если IR неверен, проверки относятся к другому языку.")
    return ("**IR не указан:** отпечаток IR в отчёте не назван, проверки относятся к IR, "
            "который построил агент.")


#: Russian gist of the verdict gate's notes (agent.verdict_gate), for the
#: warning callout; the notes themselves are printed verbatim in the appendix.
_GATE_GIST = (
    ("without a tool result", "решение ссылается на id, которых нет в журнале вызовов"),
    ("HITL 1", "формализацию задачи не утвердил человек"),
    ("approved IR", "формализацию задачи не утвердил человек"),
    ("formalization", "формализацию задачи не утвердил человек"),
    ("regex_to_dfa(ir=", "построение по определению задачи засчитано как ограниченная проверка"),
    ("R3", "свидетельства противоречат друг другу"),
    ("refute", "процитированный результат опровергает ответ"),
    ("another IR", "часть результатов относится к другому IR"),
    ("different IRs", "результаты относятся к разным IR"),
    ("inconclusive", "ответ не установлен"),
)


def _gate_gist(lines: Sequence[str]) -> list[str]:
    out: list[str] = []
    for g in lines:
        for needle, words in _GATE_GIST:
            if needle in g:
                if words not in out:
                    out.append(words)
                break
    return out


def _published_token(ctx: _Ctx, vt: Mapping[str, Any]) -> str | None:
    """The verdict token the result publishes, ``None`` when the answer is not
    established (an inconclusive token, REG ``published_verdict: null``)."""
    token = vt.get("verdict_token")
    if vt.get("source") == "result" and "published_verdict" in ctx.result:
        token = ctx.result.get("published_verdict")
    return str(token) if token in _DECISIVE_TOKENS else None


def _verdict_block(ctx: _Ctx, vt: Mapping[str, Any]) -> str:
    """The "Ответ" box: the published verdict in words and the class, the
    confidence ceiling as a percentage, who reviewed it, and a warning callout
    whenever trust is below ``verified`` (VERDICT_POLICY §5: green only for
    ``verified``)."""
    rep = ctx.report
    trust = vt["trust"]
    lang = str(rep.get("language_class") or "")
    verdict = _one_line(rep.get("verdict"), 200)
    token = _published_token(ctx, vt)
    if token is None:
        head = "Ответ не установлен"
        sub = (f"> Гипотеза решения: «{verdict}»; проверки этого прогона не дают основания опубликовать "
               "ответ (docs/VERDICT_POLICY.md).") if verdict else ""
    else:
        words = _TOKEN_WORDS.get(token) or verdict
        head = f"Ответ: {words}"
        if trust == "not_verified":
            head = f"Ответ (не подтверждён): {words}"
        plain = verdict.isascii() and len(verdict) <= 24
        sub = "" if plain or lang == "ll" else f"> Формулировка решения: {verdict}"
    lines = [f"> [!theorem] {head}"]
    if sub:
        lines.append(sub)
    cls = _CLASS_LABELS.get(lang)
    # a negative answer is not "class: CFL" under "the language is not context-free"
    cls_word = "Вопрос о классе" if ctx.report.get("answer") == "no" else "Класс"
    lines.append(f"> {cls_word + ': ' + cls + ' · ' if cls else ''}Уверенность: {round(vt['confidence_cap'] * 100)}%"
                 f" · {_review_short(ctx)}")
    out = ["\n".join(lines)]
    gate = [g for g in vt.get("verdict_gate") or [] if g]
    gist = _gate_gist(gate)
    why = (" Причины: " + "; ".join(gist) + "." if gist else
           " Подробности в разделе «Проверка»." if gate else "")
    if trust == "verified":
        out.append("> [!success] Ответ подтверждён полной машинной проверкой (`verified`)")
    elif trust == "bounded_pass" and gist:
        out.append("> [!warning] Ответ проверен машинно не полностью\n"
                   "> Доверие понижено до «проверено выборочно» (`bounded_pass`)." + why)
    elif trust == "bounded_pass":
        out.append("> [!warning] Ответ проверен машинно не полностью\n"
                   "> Доверие: проверено выборочно (`bounded_pass`): проверка ограничена конечным набором слов, "
                   "это не полное доказательство." + why)
    elif trust == "well_formed":
        out.append("> [!warning] Доказательство НЕ прошло проверку по существу\n"
                   "> Доверие: корректно оформлено (`well_formed`): машинно проверена только форма; "
                   "рассуждение ниже — аргумент модели. Требуется ручная проверка." + why)
    else:
        out.append("> [!warning] Доказательство НЕ прошло независимую проверку\n"
                   f"> Доверие: {trust_label(trust)} (`{trust}`): у ответа нет машинного основания; ниже — "
                   "аргумент модели, а не проверенная теорема. Требуется ручная проверка." + why)
    return "\n\n".join(out)


def _basis_line(ctx: _Ctx, vt: Mapping[str, Any]) -> str:
    """The trust basis in one line (appendix)."""
    trust = vt["trust"]
    basis = [str(b) for b in vt.get("basis") or []]
    ctx.use(basis, "ответ")
    basis_txt = (", ".join(f"{_code(b)} ({ctx.by_id[b]['tool']})" if b in ctx.by_id else _code(b)
                           for b in basis) if basis else "нет допустимого основания")
    line = (f"**Основание ответа:** {trust_label(trust)} ({_code(trust)}); "
            f"Потолок уверенности (docs/VERDICT_POLICY.md): {vt['confidence_cap']:.2f}; основание: {basis_txt}.")
    extra = []
    if vt.get("verdict_token"):
        extra.append(f"токен вердикта {_code(vt['verdict_token'])}")
    if ctx.report.get("answer"):
        extra.append(f"направление (answer) {_code(ctx.report.get('answer'))}")
    if ctx.report.get("verdict"):
        extra.append(f"вердикт в отчёте «{_one_line(ctx.report.get('verdict'), 200)}»")
    if extra:
        s = "; ".join(extra)
        line += " " + s[0].upper() + s[1:] + "."
    return line


def _claims_section(ctx: _Ctx, claims_out: list[dict[str, Any]]) -> str:
    claims = list(ctx.report.get("claims") or [])
    if not claims:
        return ""
    lines = ["### Ключевые утверждения", ""]
    for n, c in enumerate(claims, 1):
        ids = list(c.get("evidence_ids") or [])
        j = _judge(ids, ctx.by_id)
        ctx.use(j["evidence_ids"], f"утверждение {n}")
        claims_out.append({"n": n, "text": c.get("text"), **j})
        lines.append(f"{n}. " + _with_mark(safe_text(c.get("text")).replace("\n", "\n   "), _mark(j, ctx.by_id)))
    return "\n".join(lines)


def _step_refs(step: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [_theory_ref(t) for t in step.get("theorem_ids") or []]


def _steps(ctx: _Ctx, steps_out: list[dict[str, Any]], *, compact: bool) -> str:
    steps = list(ctx.report.get("steps") or [])
    if not steps:
        return "Шагов доказательства нет."
    blocks: list[str] = []
    for n, step in enumerate(steps, 1):
        j = _judge(list(step.get("evidence_ids") or []), ctx.by_id)
        ctx.use(j["evidence_ids"], f"шаг {n}")
        refs = _step_refs(step)
        steps_out.append({"n": n, "title": step.get("title"), "claim": step.get("claim"),
                          "theory": refs, **j})
        title = _one_line(step.get("title"), 120) if step.get("title") else ""
        claim = safe_text(step.get("claim"))
        just = safe_text(step.get("justification"))
        mark = _mark(j, ctx.by_id)
        theory = "; ".join(_theory_text(r, short=True) for r in refs)
        if compact:
            head = f"{n}. " + (f"**{title}.** " if title else "") + claim.replace("\n", " ")
            if just:
                head += " — " + just.replace("\n", " ")
            if theory:
                head += f" (теория: {theory})"
            blocks.append(_with_mark(head, mark))
            continue
        lines = [f"**Шаг {n}." + (f" {title}" if title else "") + "**", ""]
        if just:
            lines += [claim, "", f"*Обоснование:* {just}"]
        else:
            lines += [claim]
        if theory:
            lines += ["", f"*Теория:* {theory}"]
        lines[-1] = _with_mark(lines[-1], mark)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _lemmas_section(ctx: _Ctx, lemmas_out: list[dict[str, Any]]) -> str:
    lemmas = list(ctx.report.get("lemmas") or [])
    if not lemmas:
        return ""
    lines = ["### Используемые утверждения теории", ""]
    for lem in lemmas:
        ref = _theory_ref(str(lem.get("theorem_id") or ""))
        lemmas_out.append({**ref, "use": lem.get("use")})
        use = safe_text(lem.get("use")).replace("\n", " ")
        lines.append(f"- {_theory_text(ref)}" + (f": {use}" if use else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The construction: the model's text and the objects of the cited results
# ---------------------------------------------------------------------------

_DFA_TOOLS = ("regex_to_dfa", "dfa_vs_ir", "run_dfa", "nerode_probe")
_DPDA_TOOLS = ("dpda_vs_ir", "dpda_run")
_GRAMMAR_TOOLS = ("grammar_vs_ir", "input_grammar_cfg", "cyk", "ll_table", "first_follow", "ll_parse")
_MAX_TABLE_ROWS = 40


def _cited_entries(ctx: _Ctx, vt: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Successful, non-refuted ledger entries the answer rests on or the
    report cites: the verdict basis first, then the report's own ids."""
    ids: list[str] = [str(b) for b in vt.get("basis") or []]
    ids += [str(i) for i in ctx.report.get("evidence_ids") or []]
    for item in [*(ctx.report.get("steps") or []), *(ctx.report.get("claims") or [])]:
        ids += [str(i) for i in item.get("evidence_ids") or []]
    out = []
    for i in dict.fromkeys(ids):
        e = ctx.by_id.get(i)
        if e and e.get("ok") and e.get("trust") != "refuted":
            out.append(e)
    return out


def _sym(x: Any) -> str:
    return _cell(x, 40) if str(x) != "" else "ε"


def _dfa_table(dfa: Mapping[str, Any]) -> str | None:
    trans = dfa.get("transitions")
    if not isinstance(trans, Mapping) or not trans:
        return None
    states = [str(s) for s in dfa.get("states") or trans.keys()]
    if len(states) > _MAX_TABLE_ROWS:
        return None
    alphabet = [str(a) for a in dfa.get("alphabet") or []]
    if not alphabet:
        alphabet = sorted({str(a) for row in trans.values() if isinstance(row, Mapping) for a in row})
    start = str(dfa.get("start"))
    accept = {str(s) for s in dfa.get("accept") or dfa.get("accepting") or dfa.get("final") or []}
    rows = ["| состояние | " + " | ".join(_sym(a) for a in alphabet) + " | принимающее |",
            "|---|" + "---|" * len(alphabet) + "---|"]
    missing = False
    for s in states:
        row = trans.get(s) if isinstance(trans.get(s), Mapping) else {}
        name = ("→ " if s == start else "") + _cell(s, 40)
        cells = [_cell(row.get(a), 40) if row.get(a) is not None else "—" for a in alphabet]
        missing = missing or "—" in cells
        rows.append(f"| {name} | " + " | ".join(cells) + f" | {'да' if s in accept else ''} |")
    return "\n".join(rows) + "\n\n→ — начальное состояние" + ("; «—» — перехода нет." if missing else ".")


def _rhs_text(rhs: Any) -> str:
    if isinstance(rhs, str):
        return rhs or "ε"
    syms = [str(x) for x in rhs or []]
    if not syms:
        return "ε"
    return ("" if all(len(x) == 1 for x in syms) else " ").join(syms)


def _grammar_text(g: Any) -> str | None:
    if isinstance(g, str):
        return g.strip() or None
    if not isinstance(g, Mapping):
        return None
    rules = g.get("rules") or g.get("productions")
    by_lhs: dict[str, list[str]] = {}
    if isinstance(rules, Mapping):
        for lhs, alts in rules.items():
            for alt in alts or [[]]:
                by_lhs.setdefault(str(lhs), []).append(_rhs_text(alt))
    elif isinstance(rules, Sequence):
        for r in rules:
            if isinstance(r, Mapping) and r.get("lhs") is not None:
                by_lhs.setdefault(str(r["lhs"]), []).append(_rhs_text(r.get("rhs")))
    if not by_lhs or len(by_lhs) > _MAX_TABLE_ROWS:
        return None
    start = str(g.get("start") or "")
    order = sorted(by_lhs, key=lambda x: x != start)  # the start symbol first, else the given order
    return "\n".join(f"{lhs} → {' | '.join(by_lhs[lhs])}" for lhs in order).replace("```", "'''")


def _ll_table_md(table: Any, order: Sequence[str] = ()) -> str | None:
    if not isinstance(table, Mapping) or not table or len(table) > _MAX_TABLE_ROWS:
        return None
    rank = {str(x): i for i, x in enumerate(order)}
    table = dict(sorted(table.items(), key=lambda kv: rank.get(str(kv[0]), len(rank))))
    looks: list[str] = []
    for row in table.values():
        if isinstance(row, Mapping):
            looks += [str(k) for k in row if str(k) not in looks]
    if not looks or len(looks) > 20:
        return None
    rows = ["| | " + " | ".join(_sym(x) if x else "$" for x in looks) + " |",
            "|---|" + "---|" * len(looks)]
    for nt, row in table.items():
        row = row if isinstance(row, Mapping) else {}
        cells = []
        for la in looks:
            rhs = row.get(la)
            cells.append(_cell(f"{nt} → {_rhs_text(rhs)}", 60) if rhs is not None else "")
        rows.append(f"| {_cell(nt, 40)} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _same_lines(text: str, model_text: str) -> bool:
    """Every rule line of ``text`` already stands in the model's construction
    (spaces ignored): the code-made copy would only repeat it."""
    have = {re.sub(r"\s+", "", ln) for ln in model_text.splitlines()}
    return bool(model_text) and all(re.sub(r"\s+", "", ln) in have for ln in text.splitlines())


def _ledger_objects(ctx: _Ctx, vt: Mapping[str, Any], model_text: str = "") -> list[str]:
    """Blocks for the objects the cited tool results carry (code-made): the
    DFA transition table, the grammar, the LL table."""
    blocks: list[str] = []
    seen: set[str] = set()
    for e in _cited_entries(ctx, vt):
        tool, data, args = str(e.get("tool")), e.get("data") or {}, e.get("args") or {}
        src = f"из результата {tool} ({_code(e['id'])})"
        if "dfa" not in seen and tool in _DFA_TOOLS:
            dfa = data.get("dfa") if isinstance(data.get("dfa"), Mapping) else args.get("dfa")
            table = _dfa_table(dfa) if isinstance(dfa, Mapping) else None
            if table:
                seen.add("dfa")
                n = len(dfa.get("states") or dfa.get("transitions") or [])
                blocks.append(f"**Таблица переходов ДКА** ({n} состояний, {src}):\n\n{table}")
                pic = ctx.diagram(_diagrams.dfa_to_dot(dfa), "Диаграмма состояний ДКА")
                if pic:
                    blocks.append(pic)
        if "dpda" not in seen and tool in _DPDA_TOOLS and isinstance(args.get("dpda"), Mapping):
            pic = ctx.diagram(_diagrams.dpda_to_dot(args["dpda"]),
                              f"Диаграмма ДМП-автомата ({src}; на дуге: буква, вершина стека / что кладётся)")
            if pic:
                seen.add("dpda")
                blocks.append(pic)
        g = args.get("grammar")
        if g is None and isinstance(args.get("ir"), Mapping):
            g = args["ir"].get("grammar")
        if "grammar" not in seen and tool in _GRAMMAR_TOOLS:
            text = _grammar_text(g)
            if text:
                seen.add("grammar")
                if _same_lines(text, model_text):
                    blocks.append(f"Грамматика выше совпадает с той, которую проверил {tool} ({_code(e['id'])}).")
                else:
                    blocks.append(f"**Грамматика** ({src}):\n\n```\n{text}\n```")
        if "ll" not in seen and tool == "ll_table":
            order = [str(x) for x in (g.get("nonterminals") or [])] if isinstance(g, Mapping) else []
            # ll_table with max_k (search mode) keeps the table of the k found under result_for_k
            found = data.get("result_for_k") if isinstance(data.get("result_for_k"), Mapping) else {}
            table = _ll_table_md(data.get("parse_table") or found.get("parse_table"), order)
            if table:
                seen.add("ll")
                k = data.get("k") or found.get("k") or args.get("k")
                blocks.append(f"**Таблица LL({k or 'k'})-анализа** ({src}):\n\n{table}")
    return blocks


def _construction_section(ctx: _Ctx, vt: Mapping[str, Any], *, heading: str = "### Конструкция") -> str:
    model = safe_block(ctx.report.get("construction"))
    objects = _ledger_objects(ctx, vt, model)
    if not model and not objects:
        return ""
    return "\n\n".join([heading, *([model] if model else []), *objects])


def _idea_section(ctx: _Ctx) -> str:
    """The idea paragraph: the hypothesis (or the summary) and the method."""
    rep = ctx.report
    idea = safe_text(rep.get("hypothesis"))
    if not idea and safe_text(rep.get("conclusion")):
        idea = safe_text(rep.get("summary"))  # the summary is not printed as the conclusion then
    method = safe_text(rep.get("method"))
    method = method[:1].upper() + method[1:]
    parts = []
    if idea:
        parts.append(f"**Идея.** {idea}")
    if method:
        parts.append(f"**Метод.** {method}")
    return "\n\n".join(parts)


def _lean_entries(ctx: _Ctx) -> list[dict[str, Any]]:
    lean = ctx.report.get("lean") if ctx.report else None
    cited = [str(i) for i in (lean or {}).get("evidence_ids") or []]
    chosen = [ctx.by_id[i] for i in cited if i in ctx.by_id]
    if not chosen:
        chosen = [e for e in ctx.evidence if str(e.get("tool")) in _LEAN_TOOLS]
    return chosen


def _lean_values(ctx: _Ctx, lean_out: dict[str, Any]) -> dict[str, str]:
    lean = (ctx.report.get("lean") if ctx.report else None) or {}
    cited = [str(i) for i in lean.get("evidence_ids") or []]
    entries = _lean_entries(ctx)
    ctx.use([e["id"] for e in entries], "Lean")
    unknown = [i for i in cited if i not in ctx.by_id]
    status_lines: list[str] = []
    statement = proof = ""
    lean_out.update(entries=[e["id"] for e in entries], unknown_ids=unknown, proof_path=lean.get("proof_path"))
    if not entries:
        status_lines.append("- статус: **не подтверждён журналом** (в журнале нет результата `lean_check`)")
        lean_out["status"] = None
        lean_out["trust"] = "not_verified"
    for e in entries:
        data = e.get("data") or {}
        st = data.get("status") or ("proved" if e.get("ok") and e.get("trust") == "verified"
                                    else "failed" if not e.get("ok") else "unknown")
        status_lines.append(f"- статус (по журналу): **{_one_line(st, 60)}** · доверие: "
                            f"{trust_label(e['trust'])} ({_code(e['trust'])}) · {_code(e['id'])}")
        axioms = data.get("axioms")
        if axioms:
            status_lines.append("- axioms: " + ", ".join(str(a) for a in axioms))
        if data.get("direction"):
            status_lines.append(f"- direction: {_code(data['direction'])}")
        if e.get("model"):
            status_lines.append(f"- model: {_code(e['model'])}")
        lean_out.setdefault("status", st)
        lean_out["trust"] = _strongest([lean_out.get("trust", "not_verified"), e["trust"]])
        statement = statement or str(data.get("statement") or "")
        proof = proof or str(data.get("proof_body") or data.get("proof") or "")
    if unknown:
        status_lines.append("- id нет в журнале: " + ", ".join(_code(i) for i in unknown))
    if lean.get("proof_path"):
        status_lines.append(f"- файл доказательства: {_code(lean['proof_path'])}")

    def fence(text: str) -> str:
        return "```lean\n" + text.replace("```", "``\u200b`").rstrip() + "\n```"

    note = safe_text(lean.get("note"))
    return {
        "lean_status": "\n".join(status_lines),
        "lean_statement": ("Statement (из журнала, сгенерирован по IR):\n\n" + fence(statement)) if statement else "",
        "lean_proof_body": ("Proof body:\n\n" + fence(proof)) if proof else "",
        "lean_note": f"**Примечание модели:** {note}" if note else "",
    }


def _wants_lean(ctx: _Ctx) -> bool:
    if ctx.template == "lean_appendix":
        return True
    if ctx.report and ctx.report.get("lean"):
        return True
    return any(str(e.get("tool")) in _LEAN_TOOLS for e in ctx.evidence)


def _partial_values(ctx: _Ctx) -> dict[str, str]:
    part = ctx.result.get("partial_report") if isinstance(ctx.result.get("partial_report"), Mapping) else {}
    reason = part.get("reason") or ctx.result.get("reason") or ""
    if ctx.status == "budget_exhausted":
        banner = ("> [!warning] Бюджет исчерпан: модель не сдала отчёт, он собран кодом из журнала "
                  "свидетельств. Вердикт не установлен.")
    elif ctx.status == "error":
        banner = "> [!warning] Прогон завершился ошибкой; отчёт собран кодом из журнала. Вердикт не установлен."
    else:
        banner = "> [!warning] Прогон завершился без отчёта; отчёт собран кодом из журнала. Вердикт не установлен."
    if reason:
        banner += f"\n> Причина: {_one_line(reason, 400)}"
    verdict = ("> [!theorem] Ответ не установлен\n"
               f"> Уверенность: {round(_CAPS['not_verified'] * 100)}% · {_review_short(ctx)}\n"
               "> Ниже — только то, что успели установить инструменты; доверие: не проверено (`not_verified`).")
    est = [e for e in ctx.evidence if e.get("ok") and e.get("trust") != "refuted"]
    ref = [e for e in ctx.evidence if e.get("trust") == "refuted"]
    failed = [e for e in ctx.evidence if not e.get("ok")]
    ctx.use([e["id"] for e in est], "частичный отчёт")
    established = "\n".join(
        f"- {_code(e['id'])} {e['tool']}: {trust_label(e['trust'])} ({_code(e['trust'])})"
        + (f": {_one_line(e.get('trust_reason'), 240)}" if e.get("trust_reason") else "") for e in est
    ) or "Ничего: в журнале нет успешных результатов."
    refuted_lines = [f"- {_code(e['id'])} {e['tool']}: опровергнуто"
                     + (f": {_one_line(e.get('trust_reason'), 240)}" if e.get("trust_reason") else "")
                     for e in ref]
    refuted_lines += [f"- {_code(e['id'])} {e['tool']}: ошибка: {_one_line(e.get('error'), 240)}" for e in failed]
    subagents = list(part.get("subagents") or [])
    sub = ""
    if subagents:
        sub = "## Ответы субагентов (слова модели, не свидетельства)\n\n" + "\n\n".join(
            f"**{_one_line(s.get('subagent') or '?', 60)}** ({_code(s.get('id'))}):\n\n"
            f"{safe_text(s.get('answer'), max_chars=1500)}" for s in subagents)
    todos = list(part.get("todos") or [])
    todo = ""
    if todos:
        todo = "## План на момент остановки\n\n" + "\n".join(
            f"- [{'x' if t.get('status') == 'completed' else ' '}] {_one_line(t.get('content'), 200)}"
            f" ({_one_line(t.get('status'), 20)})" for t in todos if isinstance(t, Mapping))
    last = part.get("last_message") or ctx.result.get("last_message") or ""
    last_sec = ("## Последнее сообщение модели\n\n" + safe_text(last, max_chars=3000)) if last else ""
    if part.get("rejected_reports"):
        last_sec += ("\n\n" if last_sec else "") + (
            f"Отклонённых попыток сдать отчёт (невалидные поля): {int(part['rejected_reports'])}.")
    return {"partial_banner": banner, "verdict_block": verdict, "established": established,
            "refuted": "\n".join(refuted_lines) or "Нет.", "subagents_section": sub,
            "todos_section": todo, "last_message_section": last_sec}


# ---------------------------------------------------------------------------
# The code-generated evidence block
# ---------------------------------------------------------------------------

def _entry_note(e: Mapping[str, Any]) -> str:
    if e.get("ok"):
        return str(e.get("trust_reason") or "")
    if e.get("error"):
        return f"ошибка: {e.get('error')}"
    if e.get("has_tool_result") is False:
        return "без результата инструмента (ответ субагента или встроенный инструмент)"
    return "неуспешный вызов"


def _entry_result(e: Mapping[str, Any]) -> str:
    """The "результат" cell of the check table: the trust in words, the bound
    of a bounded check, or the error."""
    if not e.get("ok"):
        if e.get("error"):
            return "✗ ошибка: " + _one_line(e.get("error"), 120)
        return "— без результата" if e.get("has_tool_result") is False else "✗ неуспешный вызов"
    words = _RESULT_WORDS.get(e["trust"], trust_label(e["trust"]))
    bounds = e.get("bounds") if isinstance(e.get("bounds"), Mapping) else {}
    n = bounds.get("max_len") or bounds.get("max_length")
    if n is not None and e["trust"] == "bounded_pass":
        words += f" (слова до длины {n})"
    return f"{words} ({e['trust']})"


def _evidence_block(ctx: _Ctx, claims_out: Sequence[Mapping[str, Any]],
                    steps_out: Sequence[Mapping[str, Any]], vt: Mapping[str, Any] | None = None) -> str:
    """The appendix "Проверка" (generated by code; the model never writes it):
    the trust basis in one line, one table of the tool calls, the gate notes as
    short bullets, the IR / review lines, one line on the run, and the full
    call log with models and costs in a collapsed block."""
    out = [f"## {EVIDENCE_BLOCK_TITLE}", "",
           ("Раздел сформирован кодом из журнала вызовов инструментов и журнала бюджета; модель его не пишет. "
            "Уровни доверия: docs/VERDICT_POLICY.md."), ""]
    if vt is not None:
        out += [_basis_line(ctx, vt), ""]
    if ctx.evidence:
        out += ["| инструмент | что проверено | результат | в решении |", "|---|---|---|---|"]
        for e in ctx.evidence:
            if str(e["tool"]) in _BOOKKEEPING_TOOLS and e["id"] not in ctx.usage:
                continue
            what = _TOOL_WHAT.get(str(e["tool"])) or _one_line(e.get("trust_reason") or "—", 120)
            where = ", ".join(ctx.usage.get(e["id"], [])) or "—"
            out.append(f"| {_cell(e['tool'], 60)} {_cell(_code(e['id']), 80)} | {_cell(what, 120)} | "
                       f"{_cell(_entry_result(e), 160)} | {_cell(where, 120)} |")
    else:
        out.append("Журнал пуст: ни один инструмент не дал результата.")

    model_only = [f"шаг {s['n']}" for s in steps_out if s["model_only"]]
    model_only += [f"утверждение {c['n']}" for c in claims_out if c["model_only"]]
    unknown: dict[str, list[str]] = {}
    for item, kind in [*((s, "шаг") for s in steps_out), *((c, "утверждение") for c in claims_out)]:
        for i in item["unknown_ids"]:
            unknown.setdefault(i, []).append(f"{kind} {item['n']}")
    for i in (ctx.report.get("evidence_ids") if ctx.report else None) or []:
        if str(i) not in ctx.by_id:
            unknown.setdefault(str(i), []).append("ответ")
    refuted_cited = sorted({i for item in [*steps_out, *claims_out] for i in item["refuted_ids"]})
    notes: list[str] = []
    for g in (vt or {}).get("verdict_gate") or []:
        if g:
            notes.append(f"- Понижение доверия (verdict gate): {_one_line(g, 400)}")
    if (vt or {}).get("source") == "default" and ctx.report:
        notes.append("- Доверие не вычислено гейтом (нет данных гейта): принят нижний уровень `not_verified`.")
    if model_only:
        notes.append("- Без ссылок на журнал (держится только на словах модели): " + ", ".join(model_only) + ".")
    if unknown:
        notes.append("- id, которых нет в журнале (утверждения с ними не проверены, `not_verified`): " + "; ".join(
            f"{_code(i)} ({', '.join(w)})" for i, w in unknown.items()) + ".")
    if refuted_cited:
        notes.append("- Процитированы опровергнутые результаты: " + ", ".join(_code(i) for i in refuted_cited) + ".")
    b = ctx.budget
    refusals: list[dict[str, Any]] = list((b or {}).get("refusals") or [])
    for r in ctx.result.get("budget_refusals") or []:
        if isinstance(r, Mapping) and r.get("node") not in {x.get("node") for x in refusals}:
            refusals.append(dict(r))
    if refusals:
        notes.append("- Отказы бюджета: " + ", ".join(
            f"{_code(r.get('node'))} ({r.get('refusals')})" for r in refusals) + ".")
    if (b or {}).get("exhausted") or ctx.status == "budget_exhausted":
        notes.append("- Бюджет исчерпан: часть вызовов моделей была отклонена.")
    if ctx.integrity:
        notes.append("- Целостность журнала (hash chain): **нарушена**: "
                     + "; ".join(_one_line(p, 160) for p in ctx.integrity[:10]) + ".")
    if notes:
        out += ["", "**Замечания проверки:**", ""] + notes

    out += ["", _ir_line(ctx, [str(x) for x in (vt or {}).get("basis") or []]),
            "", f"**Проверка человеком:** {ctx.review['label']}"]
    if not ctx.review["reviewed"] and NO_HUMAN_REVIEW_MARK not in ctx.review["label"].lower():
        out[-1] += f" Отметка: {NO_HUMAN_REVIEW_MARK}."
    run = [f"Прогон {_code(_run_id(ctx))}" if _run_id(ctx) else "Прогон",
           f"шаблон {_code(ctx.template)}",
           f"статус: {_STATUS_LABELS.get(ctx.status, ctx.status)} ({_code(ctx.status)})"]
    if b is not None:
        limit = b.get("limit_usd")
        run.append(f"потрачено {_money(b['spent_usd'])}" + (f" из {_money(limit)}" if limit is not None else ""))
    if ctx.integrity is not None and not ctx.integrity:
        run.append("Целостность журнала (hash chain): цела.")
    out += ["", " · ".join(run) + ("" if run[-1].endswith(".") else ".")]

    out += ["", "<details><summary>Журнал вызовов, модели и расходы</summary>", ""]
    if ctx.evidence:
        out += ["| # | id | инструмент | кто вызвал | доверие | модель | стоимость | примечание |",
                "|---|---|---|---|---|---|---|---|"]
        for n, e in enumerate(ctx.evidence, 1):
            who = e.get("role") or "main"
            if e.get("scope") and e.get("scope") != who:
                who = f"{who} ({e['scope']})"
            cost = e.get("cost_usd")
            cost_txt = _money(cost) if cost is not None else (
                f"ход {_money(e['turn_usd'])}" if e.get("turn_usd") is not None else "—")
            out.append(f"| {n} | {_cell(_code(e['id']), 80)} | {_cell(e['tool'], 60)} | {_cell(who, 60)} | "
                       f"{trust_label(e['trust'])} ({e['trust']}) | {_cell(e.get('model') or '—', 40)} | "
                       f"{cost_txt} | {_cell(_entry_note(e), 240)} |")
        out.append("")
    if b is None:
        out.append("Журнал бюджета не передан.")
    else:
        if b["by_model"]:
            if any("calls" in m for m in b["by_model"].values()):
                out += ["| модель | вызовов | токены вход/выход | кэш чтение/запись | стоимость |",
                        "|---|---|---|---|---|"]
                for model, m in sorted(b["by_model"].items()):
                    miss = f" (без usage: {m['usage_missing']})" if m.get("usage_missing") else ""
                    out.append(f"| {_cell(model, 60)} | {m['calls']}{miss} | {m['input_tokens']}/{m['output_tokens']} | "
                               f"{m['cache_read_tokens']}/{m['cache_write_tokens']} | {_money(m['usd'])} |")
            else:
                out += ["| модель | стоимость |", "|---|---|"]
                out += [f"| {_cell(model, 60)} | {_money(m['usd'])} |" for model, m in sorted(b["by_model"].items())]
        else:
            out.append("Вызовов моделей не было.")
        if b["by_role"]:
            out += ["", "| роль | вызовов | стоимость |", "|---|---|---|"]
            out += [f"| {_cell(role, 40)} | {r.get('calls', '—')} | {_money(r['usd'])} |"
                    for role, r in sorted(b["by_role"].items())]
        limit = b.get("limit_usd")
        out += ["", f"Потрачено: {_money(b['spent_usd'])}"
                + (f" из {_money(limit)} (лимит задачи)" if limit is not None else "")
                + (f"; состояние бюджета: {_code(b['state'])}" if b.get("state") else "") + "."]
    out += ["", "</details>"]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Markdown -> HTML (no new dependencies)
# ---------------------------------------------------------------------------

_INLINE_ESC_RE = re.compile(r"\\([`*#>])")
_BOLD_RE = re.compile(r"\*\*(\S(?:.*?\S)?)\*\*")
_CODE_RE = re.compile(r"(`[^`\n]+`)")
#: "*Обоснование:*" at the start of a line: the only italics the converter
#: knows (a bare "*" is too common in regexes and formulas).
_LABEL_RE = re.compile(r"^\*([A-Za-zА-Яа-яЁё ]{1,40}:)\*")
_MARK_RE = re.compile(MARK + r"\(([^()\n]*)\)")


def _inline(text: str) -> str:
    out: list[str] = []
    for n, part in enumerate(_CODE_RE.split(text)):
        if part.startswith("`") and part.endswith("`") and len(part) >= 2 and _CODE_RE.fullmatch(part):
            out.append(f"<code>{_esc(part[1:-1])}</code>")
            continue
        s = _html.escape(part, quote=False)
        if n == 0:
            s = _LABEL_RE.sub(r'<em class="label">\1</em>', s)
        s = _INLINE_ESC_RE.sub(lambda m: f"&#{ord(m.group(1))};", s)
        s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
        s = _MARK_RE.sub(r'<span class="ev-mark">(\1)</span>', s)
        out.append(s.replace(MARK, ""))
    return "".join(out)


def _join_lines(lines: Sequence[str]) -> str:
    """Paragraph lines joined with ``<br>``, except inside ``$$ … $$`` display
    math, which KaTeX must see as one text run."""
    parts: list[str] = []
    in_math = False
    for k, line in enumerate(lines):
        if k:
            parts.append(" " if in_math else "<br>")
        parts.append(_inline(line))
        if line.count("$$") % 2:
            in_math = not in_math
    return "".join(parts)


_DETAILS_RE = re.compile(r"^<details><summary>(.*)</summary>$")


def _split_row(row: str) -> list[str]:
    row = row.strip()
    row = row.removeprefix("|")
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    cells, buf, i = [], [], 0
    while i < len(row):
        if row[i] == "\\" and i + 1 < len(row) and row[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if row[i] == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(row[i])
        i += 1
    cells.append("".join(buf).strip())
    return cells


_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
_UL_RE = re.compile(r"^[-*]\s+(.*)$")
_OL_RE = re.compile(r"^(\d+)\.\s+(.*)$")
_H_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_CALLOUT_RE = re.compile(r"^\[!(\w+)\]\s*(.*)$")


def markdown_to_html(md: str, *, details: bool = False, diagrams: Mapping[str, str] | None = None) -> str:
    """The subset of Markdown the templates and the renderer produce:
    headings, paragraphs (line breaks kept), ``-`` / ``1.`` lists with
    indented continuation lines, tables, fenced code, ``>`` quotes and
    ``> [!kind]`` callouts, ``**bold**``, ```code```, a leading
    ``*Label:*``, the code-made evidence marks, backslash escapes of
    ``` ` * # > ```. Everything is HTML-escaped; ``$…$`` is left for KaTeX.
    With ``details`` (the code-made appendix only, never model text) the
    lines ``<details><summary>…</summary>`` and ``</details>`` open and close
    a collapsed block. A ```` ```dot ```` fence whose text is a key of
    ``diagrams`` (code-made DOT, see :mod:`.diagrams`) becomes its inline SVG;
    any other ``dot`` fence stays code."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)

    def is_block_start(s: str) -> bool:
        return bool(s.startswith(("```", ">", "|")) or _H_RE.match(s) or _UL_RE.match(s) or _OL_RE.match(s)
                    or (details and (s == "</details>" or _DETAILS_RE.match(s))))

    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if details and (s == "</details>" or _DETAILS_RE.match(s)):
            dm = _DETAILS_RE.match(s)
            out.append(f"<details><summary>{_inline(dm.group(1))}</summary>" if dm else "</details>")
            i += 1
            continue
        if s.startswith("```"):
            lang = s[3:].strip()
            buf: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            svg = (diagrams or {}).get(chr(10).join(buf)) if lang == "dot" else None
            if svg:
                out.append(f'<figure class="diagram">{svg}</figure>')
                continue
            cls = f' class="language-{_esc(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{_esc(chr(10).join(buf))}</code></pre>")
            continue
        m = _H_RE.match(s)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            cm = _CALLOUT_RE.match(buf[0]) if buf else None
            if cm:
                kind = cm.group(1).lower()
                body = markdown_to_html("\n".join(buf[1:])) if len(buf) > 1 else ""
                out.append(f'<div class="callout callout-{_esc(kind)}"><div class="callout-title">'
                           f"{_inline(cm.group(2))}</div>{body}</div>")
            else:
                out.append(f"<blockquote>{markdown_to_html(chr(10).join(buf))}</blockquote>")
            continue
        if s.startswith("|") and i + 1 < n and _SEP_RE.match(lines[i + 1].strip()):
            header = _split_row(s)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            t = ["<table><thead><tr>", *(f"<th>{_inline(h)}</th>" for h in header), "</tr></thead><tbody>"]
            for r in rows:
                r = (r + [""] * len(header))[: max(len(header), len(r))]
                t.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue
        lm = _UL_RE.match(s) or _OL_RE.match(s)
        if lm:
            ordered = bool(_OL_RE.match(s))
            items: list[list[str]] = []
            start = int(_OL_RE.match(s).group(1)) if ordered else 1  # type: ignore[union-attr]
            while i < n:
                raw = lines[i]
                st = raw.strip()
                im = (_OL_RE.match(st) if ordered else _UL_RE.match(st)) if st else None
                if im and not raw.startswith((" ", "\t")):
                    items.append([im.group(2) if ordered else im.group(1)])
                elif st and raw.startswith((" ", "\t")) and items:
                    items[-1].append(st)
                else:
                    break
                i += 1
            tag = "ol" if ordered else "ul"
            attr = f' start="{start}"' if ordered and start != 1 else ""
            out.append(f"<{tag}{attr}>" + "".join(
                "<li>" + "<br>".join(_inline(x) for x in it) + "</li>" for it in items) + f"</{tag}>")
            continue
        if s in ("---", "***"):
            out.append("<hr>")
            i += 1
            continue
        buf = []
        while i < n and lines[i].strip() and (not buf or not is_block_start(lines[i].strip())):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>" + _join_lines(buf) + "</p>")
    return "\n".join(out)


_CSS = """\
:root{--fg:#1a1a1a;--muted:#666;--bg:#fff;--line:#e0e0e0;--soft:#f6f7f9;--accent:#1565c0;
--ok:#2e7d32;--ok-bg:#e8f5e9;--warn:#e65100;--warn-bg:#fff3e0;--thm-bg:#eef3fb;--step:#fafbfc}
@media (prefers-color-scheme:dark){:root{--fg:#e8e8e8;--muted:#a0a0a0;--bg:#16181c;--line:#33363c;
--soft:#1f2227;--accent:#8ab4f8;--ok:#81c995;--ok-bg:#1e3326;--warn:#fbbc6b;--warn-bg:#3a2c18;--thm-bg:#1c2638;
--step:#1b1e23}}
*{box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;max-width:780px;margin:1.2rem auto;padding:0 16px;
font-size:15px;line-height:1.65;color:var(--fg);background:var(--bg);overflow-wrap:anywhere}
h1{font-size:1.45rem;line-height:1.3;margin:.2rem 0 1rem}
h2{font-size:1.15rem;margin:1.6rem 0 .6rem;padding-top:.8rem;border-top:1px solid var(--line)}
h3{font-size:1rem;margin:1.3rem 0 .45rem}
p{margin:.5rem 0}ol,ul{padding-left:1.4rem}li{margin:.2rem 0}
code{font-family:Consolas,'Courier New',monospace;font-size:.88em;background:var(--soft);padding:1px 4px;
border-radius:4px;overflow-wrap:anywhere}
pre{background:var(--soft);padding:10px 12px;border-radius:6px;overflow-x:auto;line-height:1.45}
pre code{background:none;padding:0;overflow-wrap:normal}
table{border-collapse:collapse;margin:8px 0;font-size:13.5px;display:block;max-width:100%;overflow-x:auto}
th,td{border:1px solid var(--line);padding:5px 9px;text-align:left;vertical-align:top}th{background:var(--soft)}
blockquote{border-left:3px solid var(--line);margin:.6rem 0;padding:.2rem .8rem;color:var(--muted)}
.callout{border-radius:6px;padding:10px 14px;margin:.7rem 0;background:var(--soft);border-left:4px solid var(--accent)}
.callout p{margin:.25rem 0}.callout-title{font-weight:600}
.callout-theorem{background:var(--thm-bg)}.callout-theorem .callout-title{font-size:1.08rem}
.callout-success{background:var(--ok-bg);border-left-color:var(--ok)}
.callout-warning{background:var(--warn-bg);border-left-color:var(--warn)}
em.label{color:var(--muted)}
.ev-mark{font-size:.82em;color:var(--ok);white-space:nowrap}
.generated{margin-top:2.2rem;padding:2px 14px 10px;border:1px dashed var(--line);border-radius:8px;
font-size:13.5px;color:var(--fg)}
.generated h2{border-top:none;padding-top:.4rem;font-size:1.05rem}
.generated table{font-size:12.5px}
details{margin:.8rem 0}summary{cursor:pointer;color:var(--muted)}
figure.diagram{margin:.6rem 0 1rem;overflow-x:auto;color:var(--fg)}
figure.diagram svg{max-width:100%;height:auto}figure.diagram svg text{fill:currentColor}
"""

_KATEX = (
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css" '
    'integrity="sha384-nB0miv6/jRmo5UMMR1wu3Gz6NLsoTkbqJghGIsx//Rlm+ZU03BU6SQNC66uf4l5+" crossorigin="anonymous">\n'
    '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js" '
    'integrity="sha384-7zkQWkzuo3B5mTepMUcHkMB5jZaolc2xDwL6VFqjFALcbeS9Ggm/Yr2r3Dy4lfFg" '
    'crossorigin="anonymous"></script>\n'
    '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js" '
    'integrity="sha384-43gviWU0YVjaDtb/GhzOouOXtZMP/7XUzwPTstBeZFe/+rCMvRwr4yROQP43s0Xk" '
    "crossorigin=\"anonymous\" onload=\"renderMathInElement(document.querySelector('main'),{delimiters:["
    "{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false},"
    "{left:'\\\\(',right:'\\\\)',display:false}],throwOnError:false});\"></script>\n"
    # long display formulas scroll inside a narrow panel instead of widening the page
    "<style>.katex-display{overflow-x:auto;overflow-y:hidden;padding:2px 0}</style>\n"
)
_MATH_RE = re.compile(r"\$\$|\\\(|\$[^$\n]{1,200}\$")


def _html_page(title: str, body_md: str, evidence_md: str, diagrams: Mapping[str, str] | None = None) -> str:
    body = markdown_to_html(body_md, diagrams=diagrams)
    ev = markdown_to_html(evidence_md, details=True)
    katex = _KATEX if _MATH_RE.search(body_md) else ""
    return (
        "<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{_esc(title)}</title>\n<style>\n{_CSS}</style>\n</head>\n<body>\n"
        f"<main>\n{body}\n</main>\n"
        f'<section class="generated" data-generated-by="code">\n{ev}\n</section>\n'
        f"{katex}</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RenderedReport:
    """A rendered report: ``markdown``, ``html`` and ``data`` (written as
    ``result.md`` / ``result.html`` / ``result.json``)."""

    markdown: str
    html: str
    data: dict[str, Any]

    def write(self, run_dir: str | os.PathLike[str]) -> dict[str, Path]:
        d = Path(run_dir)
        d.mkdir(parents=True, exist_ok=True)
        paths = {"json": d / "result.json", "md": d / "result.md", "html": d / "result.html"}
        paths["json"].write_text(json.dumps(self.data, ensure_ascii=False, indent=2, default=str) + "\n",
                                 encoding="utf-8")
        paths["md"].write_text(self.markdown, encoding="utf-8")
        paths["html"].write_text(self.html, encoding="utf-8")
        return paths


def _collapse(md: str) -> str:
    md = "\n".join(line.rstrip() for line in md.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def render_report(
    result: Mapping[str, Any],
    *,
    evidence: Any = None,
    budget: Any = None,
    task_text: str | None = None,
    human_review: Any = None,
    template: str | None = None,
    status: str | None = None,
    title: str | None = None,
) -> RenderedReport:
    """Render ``result`` (the dict of :func:`agent.collect_result`).

    Args:
        evidence: the evidence ledger (path to ``evidence.jsonl`` or its run
            directory, a list of entries, or a ledger object); merged with
            ``result["evidence"]``. Ids cited by the report and found in
            neither are marked "id нет в журнале", and their claims are
            ``not_verified``.
        budget: the ``BudgetLedger`` (or a node, or its ``summary()`` dict).
        task_text: the task as the human gave it (else ``result["task"]``).
        human_review: the HITL 2 decision (mapping or string); default
            ``result["human_review"]``. Anything but an explicit human approval
            is marked "без проверки человеком".
        template: override the report's own ``template``.
        status: override ``result["status"]`` (e.g. ``awaiting_human`` for the
            HITL 2 preview of a draft).
    """
    report = result.get("report") if isinstance(result.get("report"), Mapping) else None
    st = status or str(result.get("status") or ("submitted" if report else "incomplete"))
    entries = normalize_evidence(evidence, result.get("evidence"))
    by_id = {e["id"]: e for e in entries}
    review = normalize_review(human_review if human_review is not None else result.get("human_review"),
                              status=st)
    if report is None:
        tpl = PARTIAL_TEMPLATE
    else:
        tpl = template or str(report.get("template") or "full_proof")
        if tpl not in SELECTABLE_TEMPLATES:
            raise ValueError(f"unknown report template {tpl!r}; one of {SELECTABLE_TEMPLATES}")
    task = task_text if task_text is not None else (result.get("task") or result.get("task_text") or "")
    ctx = _Ctx(result=result, report=report or {}, status=st, template=tpl, evidence=entries, by_id=by_id,
               budget=normalize_budget(budget), review=review, task=str(task),
               integrity=ledger_integrity(evidence))
    lang = str((report or {}).get("language_class") or "")
    page_title = title or _TITLES.get(lang, "Отчёт TFL Deep Agent")

    claims_out: list[dict[str, Any]] = []
    steps_out: list[dict[str, Any]] = []
    lemmas_out: list[dict[str, Any]] = []
    lean_out: dict[str, Any] = {}
    values: dict[str, str] = dict.fromkeys(PLACEHOLDERS, "")
    values.update(title=page_title.replace("\n", " "), task=safe_text(_task_statement(str(task))) or "—")
    if report is None:
        vt = {"trust": "not_verified", "confidence_cap": _CAPS["not_verified"], "basis": [],
              "verdict_token": None, "verdict_gate": [], "source": "partial"}
        values.update(_partial_values(ctx))
        body_tpl = load_template(PARTIAL_TEMPLATE)
    else:
        vt = _verdict_trust(ctx)
        values["verdict_block"] = _verdict_block(ctx, vt)
        values["method"] = safe_text(report.get("method")) or "—"
        hyp = safe_text(report.get("hypothesis"))
        values["hypothesis_section"] = f"**Гипотеза.** {hyp}" if hyp else ""
        values["idea_section"] = _idea_section(ctx)
        values["lemmas_section"] = _lemmas_section(ctx, lemmas_out)
        values["claims_section"] = _claims_section(ctx, claims_out)
        compact = tpl in ("exam_solution", "short_verdict")
        values["steps" if not compact else "key_steps"] = _steps(ctx, steps_out, compact=compact)
        values["construction_section"] = _construction_section(ctx, vt)
        summary = safe_text(report.get("summary"))
        conclusion = safe_text(report.get("conclusion")) or summary or safe_text(report.get("verdict"))
        values["conclusion"] = conclusion
        values["summary"] = summary or conclusion
        if _wants_lean(ctx):
            lean_vals = _lean_values(ctx, lean_out)
            values.update(lean_vals)
            values["lean_section"] = load_template("lean_appendix").substitute(values)
        body_name = "full_proof" if tpl == "lean_appendix" else tpl
        body_tpl = load_template(body_name)
    body_md = _collapse(body_tpl.substitute(values))
    evidence_md = _collapse(_evidence_block(ctx, claims_out, steps_out, vt))
    # the marks' sentinel only steers the HTML; the Markdown file gets plain text
    markdown = (body_md + "\n" + evidence_md).replace(MARK, "")

    data = {
        **_jsonable(dict(result)),
        "status": st,
        "rendered": {
            "renderer": "tfl_deep_agent.reports.render",
            "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "template": tpl,
            "title": page_title,
            "task": str(task),
            "human_review": review,
            "verdict": {"text": (report or {}).get("verdict", "не установлен"), **_jsonable(vt)},
            "claims": _jsonable(claims_out),
            "steps": _jsonable(steps_out),
            "lemmas": _jsonable(lemmas_out),
            "lean": _jsonable(lean_out) if lean_out else None,
            "evidence": _jsonable([{k: v for k, v in e.items() if k not in ("data", "args")}
                                   for e in entries]),
            "evidence_usage": ctx.usage,
            "evidence_integrity": ctx.integrity,
            "budget": _jsonable(ctx.budget),
        },
    }
    return RenderedReport(markdown=markdown, html=_html_page(page_title, body_md, evidence_md, ctx.diagrams), data=data)


def write_report(run_dir: str | os.PathLike[str], result: Mapping[str, Any], **kwargs: Any) -> dict[str, Path]:
    """Render ``result`` (see :func:`render_report`) and write
    ``result.json`` / ``result.md`` / ``result.html`` into ``run_dir``.
    ``evidence`` defaults to ``run_dir/evidence.jsonl`` when it exists."""
    if kwargs.get("evidence") is None:
        p = Path(run_dir) / "evidence.jsonl"
        if p.exists():
            kwargs["evidence"] = p
    return render_report(result, **kwargs).write(run_dir)
