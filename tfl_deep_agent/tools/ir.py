"""IR tools (P1, group ``ir``, ceiling ``well_formed``).

* ``describe_ir_schema`` -- the IR shape for one class (REG / CFL / DCFL / LL),
  the allowed task types and ``language_spec`` kinds, which kinds have a
  membership oracle, and one real example IR from ``<pkg>/examples/``.
* ``validate_ir`` -- the class's own validator (the same dispatch as
  ``tfl_eval.runners.validate``), plus whether a membership oracle and the Lean
  statement can be built from the IR, so the ir-builder knows which tools work.
* ``ir_to_text`` -- a deterministic plain-language (Russian) paraphrase of an
  IR for HITL 1, with oracle-labelled example words. No LLM.

Shared helpers used by ``tools/words.py`` (and later groups): :func:`ir_fingerprint`,
:func:`validation_errors`, :func:`require_valid`, :func:`alphabet_for`,
:func:`check_ir_limits`.

Trust (docs/VERDICT_POLICY.md §1): IR tools only check structure. A valid IR
is ``well_formed``; an invalid one, the schema reference and anything else is
``not_verified``. None of these tools says anything about the language itself.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from tfl_deep_agent.evidence.ledger import ir_fingerprint
from tfl_deep_agent.tools.base import (
    Deadline,
    ToolArgs,
    ToolError,
    ToolInputError,
    ToolResult,
    check_grammar_size,
    tfl_tool,
)

__all__ = [
    "TFLClass", "CLASSES", "ir_fingerprint", "validation_errors", "require_valid",
    "alphabet_for", "language_kind", "task_grammars", "check_ir_limits", "decomposition_literals",
    "ll_informal_constraints", "list_examples", "paraphrase_ir", "describe_ir_schema",
    "validate_ir", "ir_to_text",
]

TFLClass = Literal["reg", "cfl", "dcfl", "ll"]
CLASSES: tuple[str, ...] = ("reg", "cfl", "dcfl", "ll")

REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_DIRS: Mapping[str, Path] = {
    "reg": REPO_ROOT / "agent_system" / "examples",
    "cfl": REPO_ROOT / "cfl_system" / "examples",
    "dcfl": REPO_ROOT / "dcfl_system" / "examples",
    "ll": REPO_ROOT / "ll_system" / "examples",
}

CLASS_DESCRIPTION = (
    "Task class whose IR format and tools to use: 'reg' (regularity, agent_system), "
    "'cfl' (context-free, cfl_system), 'dcfl' (deterministic CF, dcfl_system), "
    "'ll' (LL(k), ll_system).")
IR_DESCRIPTION = (
    "The task IR as a JSON object in the format of the chosen class "
    "(call describe_ir_schema first if unsure). At most 64 KiB as JSON.")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _validator(cls: str) -> Callable[[Any], list[str]]:
    # The same dispatch as tfl_eval.runners.validate (tfl_eval may import
    # tfl_deep_agent, not the other way round, so it is repeated here).
    if cls == "reg":
        from agent_system.lib.ir_schema import validate_ir as v
    elif cls == "cfl":
        from cfl_system.lib.cfl_ir_schema import validate_cfl_ir as v
    elif cls == "dcfl":
        from dcfl_system.lib.dcfl_ir_schema import validate_dcfl_ir as v
    elif cls == "ll":
        from ll_system.lib.ll_ir_schema import validate_ll_ir as v
    else:
        raise ToolInputError(f"unknown task class {cls!r}; one of {CLASSES}")
    return v


def validation_errors(cls: str, ir: Any) -> list[str]:
    """The class validator's error list (empty = valid). The validators never
    raise; an unexpected exception is reported as an error, not propagated."""
    try:
        errors = _validator(cls)(ir)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - a validator bug must not pass an IR
        return [f"validator raised {type(exc).__name__}: {exc}"]
    return [str(e) for e in errors]


def task_grammars(cls: str, ir: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """Every grammar object in the IR, with its JSON path."""
    out: list[tuple[str, Mapping[str, Any]]] = []
    spec = ir.get("language_spec")
    if isinstance(spec, Mapping):
        if spec.get("kind") == "grammar":
            out.append(("language_spec", spec))
        g = spec.get("grammar")
        if spec.get("kind") == "grammar_filter" and isinstance(g, Mapping):
            out.append(("language_spec.grammar", g))
    if cls == "ll" and isinstance(ir.get("grammar"), Mapping):
        out.append(("grammar", ir["grammar"]))
    return out


def check_ir_limits(cls: str, ir: Mapping[str, Any]) -> None:
    """Size limits beyond the registry's JSON byte limit: grammar size."""
    for _path, g in task_grammars(cls, ir):
        check_grammar_size(g)


def require_valid(cls: str, ir: Any) -> dict[str, Any]:
    """Raise :class:`ToolInputError` (with the validator errors as ``data``)
    unless *ir* is a valid IR of class *cls*; also apply :func:`check_ir_limits`."""
    if not isinstance(ir, Mapping):
        raise ToolInputError("ir must be a JSON object")
    errors = validation_errors(cls, ir)
    if errors:
        raise ToolInputError(
            f"the IR is not a valid {cls} IR: " + "; ".join(errors[:5]),
            data={"errors": errors, "ir_fingerprint": ir_fingerprint(ir)})
    check_ir_limits(cls, ir)
    return dict(ir)


def language_kind(cls: str, ir: Mapping[str, Any]) -> str | None:
    """``language_spec.kind``; for LL Format 3 (``ll_check_grammar``) ``"grammar"``."""
    if cls == "ll" and ir.get("task_type") == "ll_check_grammar":
        return "grammar"
    spec = ir.get("language_spec")
    if isinstance(spec, Mapping):
        kind = spec.get("kind")
        return kind if isinstance(kind, str) else None
    return None


def _uniq(symbols: Any) -> list[str]:
    out: list[str] = []
    if isinstance(symbols, list):
        for s in symbols:
            if isinstance(s, str) and s not in out:
                out.append(s)
    return out


def alphabet_for(cls: str, ir: Mapping[str, Any]) -> list[str]:
    """The word alphabet the class oracles work over, from the IR itself.

    DCFL: the top-level ``alphabet``. LL: the grammar's terminals (Format 2/3)
    or ``language_spec.alphabet``. REG / CFL by kind: grammar terminals, the
    union of part alphabets (``repeated_subword`` / ``exists_decomposition``),
    otherwise ``language_spec.alphabet``. ``[]`` when the IR declares none
    (for example a ``regex`` without ``alphabet``): no alphabet is guessed."""
    if cls == "dcfl":
        return _uniq(ir.get("alphabet"))
    if cls == "ll" and ir.get("task_type") == "ll_check_grammar":
        g = ir.get("grammar")
        return _uniq(g.get("terminals")) if isinstance(g, Mapping) else []
    spec = ir.get("language_spec")
    if not isinstance(spec, Mapping):
        return []
    kind = spec.get("kind")
    if kind == "grammar":
        return _uniq(spec.get("terminals"))
    if kind == "grammar_filter":
        g = spec.get("grammar")
        return _uniq(g.get("terminals")) if isinstance(g, Mapping) else []
    if kind in ("repeated_subword", "exists_decomposition") and isinstance(spec.get("alphabets"), Mapping):
        syms: set[str] = set()
        for alpha in spec["alphabets"].values():
            syms.update(s for s in _uniq(alpha))
        return sorted(syms)
    return _uniq(spec.get("alphabet"))


def _is_part_ref(elem: str, parts: set[str]) -> bool:
    if elem.startswith("rev(") and elem.endswith(")"):
        return elem[4:-1] in parts
    return elem in parts


def decomposition_literals(pred: Any) -> list[str]:
    """Literal elements (neither a part nor ``rev(part)``) in every
    exists-decomposition node of a REG predicate tree, e.g. ``a`` and ``b`` in
    ``z1 w a w b z2``. ``agent_system.lib.oracle`` treats such an element as
    one more free part; ``tools/words.py`` evaluates it as the literal text."""
    found: list[str] = []
    stack = [pred]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, Mapping):
            continue
        if isinstance(node.get("parts"), list) and isinstance(node.get("concat_pattern"), list):
            parts = {p for p in node["parts"] if isinstance(p, str)}
            for elem in node["concat_pattern"]:
                if isinstance(elem, str) and not _is_part_ref(elem, parts) and elem not in found:
                    found.append(elem)
            stack.extend(node.get("constraints") or [])
        stack.extend(node.get("operands") or [])
    return found


def ll_informal_constraints(ir: Mapping[str, Any]) -> list[Any]:
    """Comment-only constraints of an LL set_builder spec: the LL oracle skips them."""
    spec = ir.get("language_spec")
    if not isinstance(spec, Mapping) or spec.get("kind") != "set_builder":
        return []
    return [c for c in (spec.get("constraints") or []) if isinstance(c, Mapping) and "op" not in c]


def _warnings(cls: str, ir: Mapping[str, Any]) -> list[str]:
    warn: list[str] = []
    alphabet = alphabet_for(cls, ir)
    multi = [s for s in alphabet if len(s) != 1]
    if multi:
        warn.append(f"multi-character symbols {multi}: the word oracles read words character by "
                    "character, so sample_words / check_membership will refuse this IR")
    for path, g in task_grammars(cls, ir):
        bad = [t for t in _uniq(g.get("terminals")) if len(t) != 1]
        if bad and not multi:
            warn.append(f"{path}: multi-character terminals {bad} cannot be matched against words")
    if cls in ("reg", "cfl") and not isinstance(ir.get("language_spec"), Mapping):
        warn.append("no language_spec: no membership oracle can be built")
    if not alphabet and language_kind(cls, ir) not in ("natural", "arithmetic_index", None):
        warn.append("the IR declares no alphabet (add language_spec.alphabet): word tools need one")
    spec = ir.get("language_spec")
    if isinstance(spec, Mapping) and spec.get("kind") == "predicate":
        lits = decomposition_literals(spec.get("predicate"))
        if lits:
            warn.append(
                f"concat_pattern literals {lits}: evaluated as literal text by tfl_deep_agent's word "
                "tools (agent_system.lib.oracle would treat them as free parts)")
    if cls == "ll":
        informal = ll_informal_constraints(ir)
        if informal:
            warn.append(f"{len(informal)} comment-only constraint(s) are ignored by the LL oracle: "
                        "its answers ignore them, so word checks stay at well_formed; "
                        "state them as formal {op, left, right} constraints")
    if isinstance(spec, Mapping) and spec.get("kind") == "grammar_filter":
        f = spec.get("filter")
        if isinstance(f, Mapping) and (f.get("kind") == "natural_language_filter"
                                       or "natural_language_filter" in f):
            warn.append("natural-language filter: the oracle checks only the grammar (approximate); "
                        "word checks stay at well_formed")
    if isinstance(spec, Mapping) and spec.get("kind") == "regex" and spec.get("has_backreferences"):
        warn.append("regex with back-references: no membership oracle")
    return warn


# ---------------------------------------------------------------------------
# Examples (real IRs from <pkg>/examples/)
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _example_index(cls: str) -> tuple[tuple[str, str, str], ...]:
    """(repo-relative path, kind, task_type) of every top-level example IR that
    passes the class validator, sorted by file name."""
    folder = _EXAMPLE_DIRS[cls]
    out: list[tuple[str, str, str]] = []
    for path in sorted(folder.glob("*.json")):
        if path.name.endswith("_output.json"):
            continue
        try:
            ir = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(ir, dict) or "task_type" not in ir or validation_errors(cls, ir):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        out.append((rel, language_kind(cls, ir) or "", str(ir.get("task_type"))))
    return tuple(out)


def list_examples(cls: str) -> list[dict[str, str]]:
    return [{"path": p, "kind": k, "task_type": t} for p, k, t in _example_index(cls)]


def _load_example(rel: str) -> dict[str, Any]:
    return json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Schema reference (hand-written from the validators; there is no schema
# constant for CFL / DCFL / LL in the libs)
# ---------------------------------------------------------------------------

_PREDICATE_TEXT = (
    "Predicate is one of: {op: and|or|not, operands: [Predicate]} ('not' takes exactly one operand); "
    "{op: eq|neq|lt|leq|gt|geq, left: Expr, right: Expr}; {expr: Expr, modulus: int >= 2, remainder: int >= 0}; "
    "{parts: [names], concat_pattern: [...], constraints: [Predicate]} (there is a split of the word "
    "into the pattern; an element is a part name, 'rev(part)' or a literal string); "
    "{op: is_substring|is_not_substring, substring_expr, in_var}; "
    "{op: starts_with|not_starts_with, prefix_expr, of_var}; {op: is_palindrome|is_not_palindrome, var}. "
    "Expr is {kind: 'count_symbol', symbol, in_var} | {kind: 'count_subword', subword, in_var} "
    "(overlapping occurrences) | {kind: 'length', of_var} | {kind: 'constant', value: int}.")
_GRAMMAR_TEXT = (
    "{kind: 'grammar', terminals: [single-char symbols], nonterminals: [...], start, "
    "rules: [{lhs, rhs: [symbols]}]}; the empty rhs [] is ε; terminals and nonterminals must not overlap.")
_REG_KINDS = {
    "predicate": "{kind: 'predicate', alphabet: [single-char symbols], variable: 'w', predicate: Predicate}. "
                 + _PREDICATE_TEXT,
    "grammar": _GRAMMAR_TEXT,
    "regex": "{kind: 'regex', pattern, has_backreferences: bool, alphabet?: [...]}; the oracle is a Python "
             "full match of the pattern and is unavailable with back-references.",
    "natural": "{kind: 'natural', description, alphabet?}: prose. No oracle (CFL/DCFL: only when the "
               "description is exponent notation such as '{a^n b^n c^n | n >= 0}').",
    "arithmetic_index": "{kind: 'arithmetic_index', symbol, index_function}. No oracle.",
}
_SCHEMA: Mapping[str, Mapping[str, Any]] = {
    "reg": {
        "top_level": {
            "task_type": "required, one of task_types",
            "source_text": "required non-empty string: the original task text",
            "language_spec": "optional object (needed by every oracle); 'kind' selects its shape",
            "student_notes": "optional string",
        },
        "kinds": _REG_KINDS,
        "oracle": {"predicate": "exact", "grammar": "exact (CYK)", "regex": "exact without back-references",
                   "natural": "none", "arithmetic_index": "none"},
    },
    "cfl": {
        "top_level": {
            "task_type": "required, one of task_types",
            "source_text": "required non-empty string",
            "language_spec": "optional object (needed by every oracle)",
        },
        "kinds": {
            **_REG_KINDS,
            "grammar_filter": "{kind: 'grammar_filter', grammar: {terminals, nonterminals, start, rules}, "
                              "filter: Predicate over the variable 'w' | {kind: 'natural_language_filter', "
                              "description}}: the words of the grammar that satisfy the filter.",
            "repeated_subword": "{kind: 'repeated_subword', parts: [names], concat_pattern: [part names only; "
                                "a repeated name is the same word], alphabets: {part: [symbols]} (keys exactly "
                                "the parts), constraints?: [Predicate over the part names]}.",
            "exists_decomposition": "{kind: 'exists_decomposition', parts, concat_pattern: [part | 'rev(part)'], "
                                    "alphabets?: {part: [symbols]}, constraints?: [Predicate]}. No literals: a "
                                    "fixed letter is a part with a one-letter alphabet and a length constraint.",
        },
        "oracle": {"predicate": "exact", "grammar": "exact (CYK)", "regex": "exact without back-references",
                   "grammar_filter": "exact with a Predicate filter; approximate (grammar only) with a "
                                     "natural-language filter",
                   "repeated_subword": "exact (bounded split search)",
                   "exists_decomposition": "exact (bounded split search)",
                   "natural": "only for exponent notation (bounded: |w| <= 40)", "arithmetic_index": "none"},
    },
    "dcfl": {
        "top_level": {
            "task_id": "optional string",
            "task_type": "optional, one of task_types",
            "source_text": "required non-empty string",
            "input_format": "'set_builder' or 'grammar'",
            "language_spec": "object; 'kind' is 'set_builder' or 'grammar'",
            "alphabet": "required non-empty list of single-char symbols (the word alphabet)",
        },
        "kinds": {
            "set_builder": "{kind: 'set_builder', word_pattern: str, variables: [{name, domain: regex string or "
                           "null (any word), quantifier: 'forall'|'exists'}], constraints: [{kind, args}]}. "
                           "Constraint kinds: length_cmp {left, op, right} (op one of <, <=, ==, !=, >=, >; "
                           "compares lengths), integer_cmp {left, op, right: int or variable}, equal {left, right}, "
                           "reverse {left, right} (left is right reversed), regex_member {var, pattern}, "
                           "disjunction {branches: [constraint]}. With variables: [] the word_pattern is read as "
                           "exponent notation, e.g. '{aⁿbⁿcᵐ | n,m >= 1}'.",
            "grammar": _GRAMMAR_TEXT + " Use input_format 'grammar'.",
        },
        "oracle": {"set_builder": "exact with variables (segment matching); exponent notation otherwise "
                                  "(bounded; unparseable patterns have no oracle)",
                   "grammar": "exact (CYK)"},
    },
    "ll": {
        "top_level": {
            "task_type": "required: 'll_check_language' (Format 1, language_spec kind set_builder), "
                         "'ll_check_grammar_lang' (Format 2, language_spec kind grammar: is the LANGUAGE LL?), "
                         "'ll_check_grammar' (Format 3: is THIS GRAMMAR LL(k)?)",
            "source_text": "required non-empty string",
            "language_spec": "Formats 1 and 2",
            "grammar": "Format 3 only: top-level grammar {terminals, nonterminals, start, rules}",
            "question": "optional: 'is_ll' / 'is_ll_language' / 'is_ll_k'",
            "k": "optional int (Format 3)",
        },
        "kinds": {
            "set_builder": "{kind: 'set_builder', alphabet: [...], variables: [{name, domain: {type: 'nat', min?} | "
                           "{type: 'enum', values: [...]} | {type: 'word', alphabet?}}], template: [tokens], "
                           "constraints?: [...]}. A token is a literal run ('c'), a variable name, 'rev(w)' for a "
                           "word variable, or base + superscript exponent ('aⁿ', 'Xⁿ' with an enum base). The same "
                           "variable twice binds the same word. Constraints: {op: and|or|not, operands} or "
                           "{op: eq|neq|lt|leq|gt|geq, left, right} with ints or variable names (a nat variable is "
                           "its value, a word variable its length). {comment: ...} entries are ignored by the oracle.",
            "grammar": _GRAMMAR_TEXT + " '$' and 'ε' are reserved; ε may also be written rhs ['ε'].",
            **{k: v for k, v in _REG_KINDS.items() if k != "grammar"},
        },
        "oracle": {"set_builder": "exact for formal constraints (step budget)", "grammar": "exact (CYK)",
                   "predicate": "none", "regex": "none", "natural": "none", "arithmetic_index": "none"},
    },
}


def _task_types(cls: str) -> list[str]:
    if cls == "reg":
        from agent_system.lib.ir_schema import _VALID_TASK_TYPES as t
    elif cls == "cfl":
        from cfl_system.lib.cfl_ir_schema import _CFL_TASK_TYPES as t
    elif cls == "dcfl":
        from dcfl_system.lib.dcfl_ir_schema import _DCFL_TASK_TYPES as t
    else:
        from ll_system.lib.ll_ir_schema import _LL_TASK_TYPES as t
    return sorted(t)


def _lang_kinds(cls: str) -> list[str]:
    if cls == "reg":
        from agent_system.lib.ir_schema import _VALID_LANG_KINDS as k
    elif cls == "cfl":
        from cfl_system.lib.cfl_ir_schema import _CFL_LANG_KINDS as k
    elif cls == "dcfl":
        from dcfl_system.lib.dcfl_ir_schema import _DCFL_LANG_KINDS as k
    else:
        from ll_system.lib.ll_ir_schema import _LL_LANG_KINDS as k
    return sorted(k)


# ---------------------------------------------------------------------------
# Paraphrase (deterministic, Russian, for HITL 1)
# ---------------------------------------------------------------------------

_CLASS_TITLE = {"reg": "REG (регулярность)", "cfl": "CFL (контекстная свобода)",
                "dcfl": "DCFL (детерминированная контекстная свобода)", "ll": "LL(k)"}
_TASK_PHRASE = {
    "classify": "определить, регулярен ли язык",
    "prove_regular": "доказать, что язык регулярен",
    "prove_non_regular": "доказать, что язык не регулярен",
    "classify_and_prove": "определить, регулярен ли язык, и доказать ответ",
    "compute_pumping_length": "найти длину накачки",
    "build_dfa": "построить ДКА",
    "build_complement": "построить дополнение",
    "build_regex": "построить регулярное выражение",
    "parametric_analysis": "параметрический анализ",
    "classify_cfl": "определить, является ли язык контекстно-свободным",
    "prove_cfl": "доказать, что язык контекстно-свободен",
    "prove_non_cfl": "доказать, что язык не контекстно-свободен",
    "classify_and_prove_cfl": "определить, является ли язык контекстно-свободным, и доказать ответ",
    "grammar_filter_cfl": "исследовать на контекстную свободу язык грамматики с фильтром",
    "classify_dcfl": "определить, является ли язык детерминированным КС-языком",
    "prove_dcfl": "доказать, что язык детерминированный КС",
    "prove_non_dcfl": "доказать, что язык не детерминированный КС",
    "classify_and_prove_dcfl": "определить, является ли язык детерминированным КС-языком, и доказать ответ",
    "ll_check_language": "является ли язык LL(k)-языком при некотором k (Format 1)",
    "ll_check_grammar_lang": "является ли язык данной грамматики LL-языком (Format 2)",
    "ll_check_grammar": "является ли сама грамматика LL(k) (Format 3)",
}
_CMP_SYM = {"eq": "=", "neq": "≠", "lt": "<", "leq": "≤", "gt": ">", "geq": "≥",
            "==": "=", "!=": "≠", "<": "<", "<=": "≤", ">": ">", ">=": "≥"}


def _w(word: str) -> str:
    return word if word else "ε"


def _set(symbols: Any) -> str:
    return "{" + ", ".join(str(s) for s in (symbols or [])) + "}"


def _expr(e: Any) -> str:
    if not isinstance(e, Mapping):
        return str(e)
    kind = e.get("kind")
    if kind == "constant":
        return str(e.get("value"))
    if kind == "length":
        return f"|{e.get('of_var')}|"
    if kind == "count_symbol":
        return f"|{e.get('in_var')}|_{e.get('symbol')}"
    if kind == "count_subword":
        return f"#{e.get('subword')}({e.get('in_var')})"
    return json.dumps(e, ensure_ascii=False)


def _pattern_elem(elem: str, parts: set[str]) -> str:
    if elem.startswith("rev(") and elem.endswith(")") and elem[4:-1] in parts:
        return f"{elem[4:-1]}ᴿ"
    if elem in parts:
        return elem
    return f"'{elem}'"


def _pred(p: Any, main_var: str = "w", top: bool = True) -> str:
    if not isinstance(p, Mapping):
        return str(p)
    op = p.get("op")
    if op in ("and", "or"):
        joiner = " ∧ " if op == "and" else " ∨ "
        text = joiner.join(_pred(o, main_var, False) for o in p.get("operands") or [])
        return text if top else f"({text})"
    if op == "not":
        ops = p.get("operands") or [None]
        return f"¬({_pred(ops[0], main_var, True)})"
    if op in ("eq", "neq", "lt", "leq", "gt", "geq"):
        return f"{_expr(p.get('left'))} {_CMP_SYM[op]} {_expr(p.get('right'))}"
    if "modulus" in p:
        return f"{_expr(p.get('expr'))} ≡ {p.get('remainder')} (mod {p.get('modulus')})"
    if isinstance(p.get("parts"), list) and isinstance(p.get("concat_pattern"), list):
        parts = {x for x in p["parts"] if isinstance(x, str)}
        pattern = "·".join(_pattern_elem(e, parts) for e in p["concat_pattern"] if isinstance(e, str))
        cons = p.get("constraints") or []
        text = f"∃ {', '.join(p['parts'])}: {main_var} = {pattern}"
        if cons:
            text += ", где " + " ∧ ".join(_pred(c, main_var, False) for c in cons)
        return text if top else f"({text})"
    if op in ("is_substring", "is_not_substring"):
        neg = "" if op == "is_substring" else "не "
        return f"{p.get('substring_expr')} {neg}входит в {p.get('in_var')} как подслово"
    if op in ("starts_with", "not_starts_with"):
        neg = "" if op == "starts_with" else "не "
        return f"{p.get('of_var')} {neg}начинается с {p.get('prefix_expr')}"
    if op in ("is_palindrome", "is_not_palindrome"):
        neg = "" if op == "is_palindrome" else "не "
        return f"{p.get('var')} — {neg}палиндром"
    return json.dumps(p, ensure_ascii=False)


def _rhs(rhs: Any) -> str:
    syms = [s for s in (rhs or []) if s != "ε"]
    if not syms:
        return "ε"
    sep = "" if all(isinstance(s, str) and len(s) == 1 for s in syms) else " "
    return sep.join(str(s) for s in syms)


def _grammar(g: Mapping[str, Any]) -> str:
    by_lhs: dict[str, list[str]] = {}
    for r in g.get("rules") or []:
        if isinstance(r, Mapping):
            by_lhs.setdefault(str(r.get("lhs")), []).append(_rhs(r.get("rhs")))
    rules = "; ".join(f"{lhs} → {' | '.join(alts)}" for lhs, alts in by_lhs.items())
    return (f"грамматика с аксиомой {g.get('start')}, нетерминалы {_set(g.get('nonterminals'))}, "
            f"терминалы {_set(g.get('terminals'))}; правила: {rules}")


def _part_domains(spec: Mapping[str, Any]) -> str:
    alphabets = spec.get("alphabets") if isinstance(spec.get("alphabets"), Mapping) else {}
    doms = []
    for part in spec.get("parts") or []:
        alpha = alphabets.get(part)
        doms.append(f"{part} ∈ {_set(alpha)}*" if alpha else f"{part} — слово над алфавитом языка")
    return ", ".join(doms)


def _dcfl_constraint(c: Any) -> str:
    if not isinstance(c, Mapping):
        return str(c)
    kind, a = c.get("kind"), c.get("args") if isinstance(c.get("args"), Mapping) else {}
    if kind == "length_cmp":
        return f"|{a.get('left')}| {_CMP_SYM.get(a.get('op'), a.get('op'))} |{a.get('right')}|"
    if kind == "integer_cmp":
        if "var" in a:  # legacy numeric scalar
            return f"{a.get('var')} {_CMP_SYM.get(a.get('op'), a.get('op'))} {a.get('value')}"
        right = a.get("right")
        right_text = f"|{right}|" if isinstance(right, str) else str(right)
        return f"|{a.get('left')}| {_CMP_SYM.get(a.get('op'), a.get('op'))} {right_text}"
    if kind == "equal":
        return f"{a.get('left')} = {a.get('right')}"
    if kind == "reverse":
        return f"{a.get('left')} = {a.get('right')}ᴿ"
    if kind == "regex_member":
        return f"{a.get('var')} ∈ L({a.get('pattern')})"
    if kind == "disjunction":
        return "(" + " ∨ ".join(_dcfl_constraint(b) for b in a.get("branches") or []) + ")"
    return json.dumps(c, ensure_ascii=False)


def _ll_operand(x: Any, nat: set[str]) -> str:
    if isinstance(x, str) and x not in nat:
        return f"|{x}|"
    return str(x)


def _ll_constraint(c: Any, nat: set[str]) -> str:
    if not isinstance(c, Mapping):
        return str(c)
    op = c.get("op")
    if op in ("and", "or"):
        j = " ∧ " if op == "and" else " ∨ "
        return "(" + j.join(_ll_constraint(o, nat) for o in c.get("operands") or []) + ")"
    if op == "not":
        ops = c.get("operands") or [None]
        return f"¬{_ll_constraint(ops[0], nat)}"
    if op in _CMP_SYM:
        return f"{_ll_operand(c.get('left'), nat)} {_CMP_SYM[op]} {_ll_operand(c.get('right'), nat)}"
    if "op" not in c:
        return f"неформально: {c.get('comment', json.dumps(c, ensure_ascii=False))}"
    return json.dumps(c, ensure_ascii=False)


def _ll_set_builder(spec: Mapping[str, Any]) -> str:
    variables = [v for v in spec.get("variables") or [] if isinstance(v, Mapping)]
    nat = {v.get("name") for v in variables
           if isinstance(v.get("domain"), Mapping) and v["domain"].get("type") == "nat"}
    doms = []
    for v in variables:
        d = v.get("domain") if isinstance(v.get("domain"), Mapping) else {}
        t = d.get("type")
        if t == "nat":
            doms.append(f"{v.get('name')} ∈ ℕ, {v.get('name')} ≥ {d.get('min', 0)}")
        elif t == "enum":
            doms.append(f"{v.get('name')} ∈ {_set(d.get('values'))}")
        elif t in ("word", "string"):
            doms.append(f"{v.get('name')} ∈ {_set(d.get('alphabet') or spec.get('alphabet'))}*")
        else:
            doms.append(f"{v.get('name')}: {json.dumps(d, ensure_ascii=False)}")
    template = " · ".join(str(t) for t in spec.get("template") or [])
    cons = [_ll_constraint(c, nat) for c in spec.get("constraints") or []]
    text = f"{{ {template} | {', '.join(doms)}"
    if cons:
        text += "; " + "; ".join(cons)
    return text + " }"


def _language_text(cls: str, ir: Mapping[str, Any]) -> str:
    if cls == "ll" and ir.get("task_type") == "ll_check_grammar":
        g = ir.get("grammar") if isinstance(ir.get("grammar"), Mapping) else {}
        k = ir.get("k")
        k_text = f" при k = {k}" if k is not None else ""
        return f"вопрос о самой грамматике (LL(k){k_text}): " + _grammar(g)
    spec = ir.get("language_spec")
    if not isinstance(spec, Mapping):
        return "language_spec не задан: язык описан только текстом условия"
    kind = spec.get("kind")
    if kind == "grammar":
        return "язык, порождаемый грамматикой: " + _grammar(spec)
    if kind == "predicate":
        var = spec.get("variable", "w")
        text = (f"{{ {var} ∈ {_set(spec.get('alphabet'))}* | {_pred(spec.get('predicate'), var)} }}")
        lits = decomposition_literals(spec.get("predicate"))
        if lits:
            text += f" (в шаблонах разбиения {', '.join(repr(x) for x in lits)} — буквальные подслова)"
        return text
    if kind == "regex":
        text = f"язык регулярного выражения {spec.get('pattern')}"
        if spec.get("alphabet"):
            text += f" над алфавитом {_set(spec.get('alphabet'))}"
        if spec.get("has_backreferences"):
            text += " (с обратными ссылками)"
        return text
    if kind == "natural":
        return f"описание текстом: {spec.get('description')}"
    if kind == "arithmetic_index":
        return (f"арифметический индекс: символ {spec.get('symbol')}, "
                f"функция индекса {spec.get('index_function')}")
    if kind == "grammar_filter":
        g = spec.get("grammar") if isinstance(spec.get("grammar"), Mapping) else {}
        f = spec.get("filter") if isinstance(spec.get("filter"), Mapping) else {}
        if f.get("kind") == "natural_language_filter":
            cond = f"(текстом) {f.get('description')}"
        elif "natural_language_filter" in f:
            cond = f"(текстом) {f.get('natural_language_filter')}"
        else:
            cond = _pred(f, "w")
        return f"слова w языка грамматики, для которых выполнено {cond}; " + _grammar(g)
    if kind in ("repeated_subword", "exists_decomposition"):
        parts = {x for x in spec.get("parts") or [] if isinstance(x, str)}
        pattern = "·".join(_pattern_elem(e, parts) for e in spec.get("concat_pattern") or []
                           if isinstance(e, str))
        cons = spec.get("constraints") or []
        text = f"{{ {pattern} | {_part_domains(spec)}"
        if cons:
            text += "; " + " ∧ ".join(_pred(c, "w", False) for c in cons)
        text += " }"
        if kind == "repeated_subword":
            text += " (одинаковые имена частей — одно и то же слово)"
        return text
    if kind == "set_builder" and cls == "dcfl":
        variables = [v for v in spec.get("variables") or [] if isinstance(v, Mapping)]
        if not variables:
            return f"шаблон в экспоненциальной записи: {spec.get('word_pattern')}"
        doms = []
        for v in variables:
            dom = v.get("domain")
            q = " (квантор ∃)" if v.get("quantifier") == "exists" else ""
            doms.append(f"{v.get('name')} ∈ L({dom}){q}" if dom else f"{v.get('name')} ∈ Σ*{q}")
        cons = [_dcfl_constraint(c) for c in spec.get("constraints") or []]
        text = f"{{ {spec.get('word_pattern')} | {', '.join(doms)}"
        if cons:
            text += "; " + " ∧ ".join(cons)
        return text + " }"
    if kind == "set_builder":
        return _ll_set_builder(spec)
    return json.dumps(spec, ensure_ascii=False)


def paraphrase_ir(cls: str, ir: Mapping[str, Any]) -> dict[str, str]:
    """Plain-language parts of an IR (no examples): task, language, alphabet."""
    task_type = str(ir.get("task_type") or "")
    task = _TASK_PHRASE.get(task_type, task_type or "тип задачи не указан")
    alphabet = alphabet_for(cls, ir)
    return {
        "class": _CLASS_TITLE[cls],
        "task": f"{task} ({task_type})" if task_type else task,
        "source_text": str(ir.get("source_text") or ""),
        "alphabet": _set(alphabet) if alphabet else "не задан",
        "language": _language_text(cls, ir),
    }


# ---------------------------------------------------------------------------
# Tool: describe_ir_schema
# ---------------------------------------------------------------------------

class DescribeIRSchemaArgs(ToolArgs):
    cls: TFLClass = Field(description=CLASS_DESCRIPTION)
    kind: str | None = Field(
        default=None, max_length=40,
        description="Optional language_spec kind (e.g. 'grammar', 'set_builder', 'repeated_subword'): "
                    "describe only this kind and pick an example IR of it. Default: every kind, first example.")
    include_json_schema: bool = Field(
        default=False, description="REG only: also return agent_system's JSON-schema dicts (long).")


@tfl_tool(group="ir", max_trust="not_verified", timeout_s=10)
def describe_ir_schema(args: DescribeIRSchemaArgs) -> ToolResult:
    """Describe the task IR format of one class (reg / cfl / dcfl / ll): required fields, allowed
    task_type values, every language_spec kind with its fields, which kinds have a membership oracle
    (needed by sample_words / check_membership), and one real example IR from the repository's
    examples. Use it before building an IR. Reference only: trust not_verified."""
    cls = args.cls
    examples = list_examples(cls)
    kinds = _lang_kinds(cls)
    if args.kind is not None and args.kind not in kinds:
        raise ToolInputError(f"kind {args.kind!r} is not a {cls} language_spec kind; one of {kinds}")
    chosen = [e for e in examples if args.kind is None or e["kind"] == args.kind]
    schema = _SCHEMA[cls]
    shown = [args.kind] if args.kind is not None else kinds
    data: dict[str, Any] = {
        "class": cls,
        "task_types": _task_types(cls),
        "language_spec_kinds": kinds,
        "top_level_fields": dict(schema["top_level"]),
        "kind_fields": {k: v for k, v in schema["kinds"].items() if k in shown},
        "oracle_support": {k: v for k, v in schema["oracle"].items() if k in shown},
        "conventions": "words are plain strings of single-character symbols; the empty string is ε",
        "available_examples": examples,
        "example": ({"path": chosen[0]["path"], "ir": _load_example(chosen[0]["path"])}
                    if chosen else None),
    }
    if args.kind is not None and not chosen:
        data["example_note"] = f"no shipped {cls} example has kind {args.kind!r}"
    if args.include_json_schema:
        if cls != "reg":
            data["json_schema_note"] = "only agent_system (reg) defines a JSON schema constant"
        else:
            from agent_system.lib.ir_schema import IR_SCHEMA
            data["json_schema"] = IR_SCHEMA
    return ToolResult.success(data, trust="not_verified",
                              reason="schema reference: says nothing about any language")


# ---------------------------------------------------------------------------
# Tool: validate_ir
# ---------------------------------------------------------------------------

class ValidateIRArgs(ToolArgs):
    cls: TFLClass = Field(description=CLASS_DESCRIPTION)
    ir: dict[str, Any] = Field(description=IR_DESCRIPTION)
    check_tools: bool = Field(
        default=True,
        description="Also report whether a membership oracle and the Lean theorem statement can be "
                    "built from this IR (which later tools will work).")


_LEAN_DIRECTIONS = {"reg": ("regular", "non_regular"), "cfl": ("cfl", "non_cfl"),
                    "dcfl": ("dcfl", "non_dcfl")}


def _lean_status(cls: str, ir: Mapping[str, Any]) -> dict[str, Any]:
    if cls == "ll":
        return {"available": False, "reason": "LL(k) is not formalized in Lean"}
    if cls == "reg":
        from agent_system.lib.lean_ir import render_statement_verbose
    elif cls == "cfl":
        from cfl_system.lib.lean_ir import render_statement_verbose
    else:
        from dcfl_system.lib.lean_ir import render_statement_verbose
    out: dict[str, Any] = {}
    for direction in _LEAN_DIRECTIONS[cls]:
        stmt, reason = render_statement_verbose(dict(ir), direction)
        out[direction] = {"ok": stmt is not None} if stmt is not None else {"ok": False, "reason": reason}
    out["available"] = any(v.get("ok") for v in out.values() if isinstance(v, dict))
    return out


def _oracle_status(cls: str, ir: Mapping[str, Any]) -> dict[str, Any]:
    from tfl_deep_agent.tools.words import oracle_for

    try:
        oracle = oracle_for(cls, ir, validated=True)
    except ToolError as exc:
        return {"available": False, "reason": str(exc), "error_kind": exc.kind}
    return {"available": True, **oracle.describe()}


@tfl_tool(group="ir", max_trust="well_formed", timeout_s=20)
def validate_ir(args: ValidateIRArgs) -> ToolResult:
    """Validate a task IR with the class's own validator (agent_system / cfl_system / dcfl_system /
    ll_system ir_schema). Returns valid, the error list, warnings, the IR fingerprint, other classes
    whose validator also accepts it, and (check_tools) whether a membership oracle and a Lean statement
    can be built. trust well_formed when valid (structure only: it does not mean the IR describes the
    task correctly), not_verified when not."""
    cls, ir = args.cls, args.ir
    check_ir_limits(cls, ir)
    errors = validation_errors(cls, ir)
    valid = not errors
    data: dict[str, Any] = {
        "class": cls,
        "valid": valid,
        "errors": errors,
        "ir_fingerprint": ir_fingerprint(ir),
        "task_type": ir.get("task_type"),
        "kind": language_kind(cls, ir),
        "alphabet": alphabet_for(cls, ir),
        "warnings": _warnings(cls, ir) if valid else [],
        "also_valid_for": [c for c in CLASSES if c != cls and not validation_errors(c, ir)],
    }
    if valid and args.check_tools:
        data["oracle"] = _oracle_status(cls, ir)
        data["lean_statement"] = _lean_status(cls, ir)
    if valid:
        return ToolResult.success(
            data, trust="well_formed",
            reason=f"{cls} validator accepts IR {data['ir_fingerprint']} (structure only)")
    return ToolResult.success(
        data, trust="not_verified",
        reason=f"{cls} validator rejects IR {data['ir_fingerprint']}: {len(errors)} error(s)")


# ---------------------------------------------------------------------------
# Tool: ir_to_text
# ---------------------------------------------------------------------------

class IRToTextArgs(ToolArgs):
    cls: TFLClass = Field(description=CLASS_DESCRIPTION)
    ir: dict[str, Any] = Field(description=IR_DESCRIPTION)
    n_examples: int = Field(
        default=6, ge=0, le=10,
        description="How many example words in L and not in L to add (oracle-labelled, short words).")
    example_max_len: int = Field(default=8, ge=1, le=10, description="Longest example word.")


@tfl_tool(group="ir", max_trust="well_formed", timeout_s=30)
def ir_to_text(args: IRToTextArgs) -> ToolResult:
    """Paraphrase a valid IR in plain Russian (deterministic code, no LLM) for the human check after
    transcription (HITL 1): the task, the alphabet, the language as the IR defines it, and a few
    oracle-labelled words in L and not in L. Show it to the user to confirm the IR says what the task
    says. Fails (invalid_input) on an IR the class validator rejects. trust well_formed."""
    cls = args.cls
    ir = require_valid(cls, args.ir)
    parts = paraphrase_ir(cls, ir)
    fingerprint = ir_fingerprint(ir)
    examples: dict[str, Any] = {"available": False}
    if args.n_examples > 0:
        from tfl_deep_agent.tools.words import sample_language_words

        try:
            sample = sample_language_words(
                cls, ir, max_len=args.example_max_len, count=args.n_examples, max_enumerated=2000,
                random_words=0, use_generators=False, seed=0, deadline=Deadline(8.0), validated=True)
        except ToolError as exc:
            examples = {"available": False, "reason": str(exc)}
        else:
            examples = {
                "available": True,
                "in_language": sample["in_language"],
                "not_in_language": sample["not_in_language"],
                "max_len": args.example_max_len,
                "labels_from": sample["labels_from"],
            }
    lines = [
        f"Класс: {parts['class']}. Задача: {parts['task']}.",
        f"Условие: «{parts['source_text']}»",
        f"Алфавит: {parts['alphabet']}.",
        f"Язык по IR: {parts['language']}.",
    ]
    if examples.get("available"):
        ins = ", ".join(_w(w) for w in examples["in_language"]) or "не найдено"
        outs = ", ".join(_w(w) for w in examples["not_in_language"]) or "не найдено"
        lines.append(f"Примеры (слова длины ≤ {args.example_max_len}, метки по оракулу): "
                     f"в L: {ins}; не в L: {outs}.")
    elif args.n_examples > 0:
        lines.append(f"Примеры слов: нет оракула ({examples.get('reason', 'не построен')}).")
    warnings = _warnings(cls, ir)
    lines.append(f"Все проверки относятся к этому IR ({fingerprint}).")
    data = {"text": "\n".join(lines), "ir_fingerprint": fingerprint, **parts,
            "examples": examples, "warnings": warnings}
    return ToolResult.success(
        data, trust="well_formed",
        reason=f"code-generated paraphrase of valid IR {fingerprint}; a human must confirm it "
               "matches the task")
