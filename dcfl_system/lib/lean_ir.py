"""DCFL-direction IR -> Lean 4 statement translator (docs/VERDICT_POLICY.md R-Lean).

Thin wrapper around ``agent_system.lib.lean_ir`` (``LeanStatement``, the
``Letter``/``NT`` alphabet and grammar builders -- see that module's docstring
for the full ``is_DCF`` provenance: langlib's own predicate,
``Langlib.Classes.DeterministicContextFree.Definition``, since Mathlib has no
deterministic-pushdown-automaton formalization at all). Adds the
``"dcfl"``/``"non_dcfl"`` directions for two IR shapes:

- ``kind == "grammar"`` (e.g. exam_04, ``task_grammar_aSSb.json`` --
  ``S -> aSSb | ba | Ab, A -> aAb | a``) -- reuses
  ``agent_system.lib.lean_ir.grammar_decl`` verbatim.
- ``kind == "set_builder"`` in the shape the real DCFL examples use
  (``dcfl_system/examples/task_anbncm.json`` et al.): each of
  ``language_spec.variables`` is a single letter with a ``"<letter>+"``/
  ``"<letter>*"`` domain, ``word_pattern`` is those variable names
  concatenated with no separator (e.g. ``"uvw"``), and ``constraints`` are
  ``length_cmp``/``integer_cmp`` between those variables (or a variable and
  an integer literal) -- the shape ``{a^n b^n c^m | n, m >= 1}`` (variables
  ``u``: ``a+``, ``v``: ``b+``, ``w``: ``c+``, constraint ``u == v``)
  actually takes in the wild. This is a different IR schema from
  ``cfl_system.lib.exponent_pattern``'s exponent-notation text (a DCFL
  ``word_pattern`` carrying that notation directly, e.g. a literal
  ``"a^n b^n"`` string, is tried as a fallback via
  ``cfl_system.lib.exponent_pattern.parse_exponent_pattern`` --
  ``dcfl_system`` may import ``cfl_system``, root CLAUDE.md "Import
  direction").

Every statement this module renders also appends a hand-written
``instance : Fintype Letter`` after ``alphabet_decl``
(``agent_system.lib.lean_ir.fintype_instance_decl`` -- see that module's
docstring for why: langlib's ``is_DPDA`` takes ``[Fintype T]`` on the
alphabet explicitly, unlike the Mathlib predicates ``agent_system.lib
.lean_ir``/``cfl_system.lib.lean_ir`` state directly).
"""

from __future__ import annotations

import re

from agent_system.lib.lean_ir import (
    LeanStatement,
    alphabet_decl,
    fintype_instance_decl,
    grammar_decl,
    pattern_body,
)
from cfl_system.lib.exponent_pattern import ExponentPattern, parse_exponent_pattern

__all__ = ["render_statement", "render_statement_verbose"]

_DIRECTIONS_DCFL = {"dcfl": False, "non_dcfl": True}
_REL_LEAN = {"==": "=", "=": "=", "!=": "≠", "<": "<", "<=": "≤", ">": ">", ">=": "≥"}
_LETTER_DOMAIN_RE = re.compile(r"([A-Za-z])([+*])")


def render_statement(ir: dict, direction: str) -> LeanStatement | None:
    stmt, _reason = render_statement_verbose(ir, direction)
    return stmt


def render_statement_verbose(ir: dict, direction: str) -> tuple[LeanStatement | None, str | None]:
    """``(LeanStatement, None)`` on success, ``(None, reason)`` otherwise.
    Never raises."""
    try:
        return _render_statement_verbose(ir, direction)
    except Exception as exc:  # pragma: no cover - defensive; never raise
        return None, f"internal error: {exc!r}"


def _render_statement_verbose(ir: dict, direction: str) -> tuple[LeanStatement | None, str | None]:
    if direction not in _DIRECTIONS_DCFL:
        return None, f"direction {direction!r} is not a dcfl_system direction ('dcfl'/'non_dcfl')"
    if not isinstance(ir, dict):
        return None, "ir must be a dict"
    negate = _DIRECTIONS_DCFL[direction]

    lang_spec = ir.get("language_spec")
    if not isinstance(lang_spec, dict):
        return None, "ir.language_spec is missing or not an object"
    kind = lang_spec.get("kind")

    if kind == "grammar":
        return _render_grammar_case(lang_spec, negate)
    if kind == "set_builder":
        return _render_setbuilder_case(ir, lang_spec, negate)
    return None, f"unsupported language_spec.kind {kind!r} for direction {direction!r}"


def _dcfl_imports(is_grammar: bool) -> list[str]:
    imports = ["import TflLean", "import Langlib.Classes.DeterministicContextFree.Definition"]
    if is_grammar:
        imports.append("import Mathlib.Computability.ContextFreeGrammar")
    return imports


def _dcfl_theorem_decl(negate: bool) -> str:
    prop = "is_DCF L"
    if negate:
        prop = f"¬ {prop}"
    return f"theorem tfl_main : {prop}"


def _alpha_decl_with_fintype(alpha_decl: str, sym_map: dict[str, str]) -> str:
    """*alpha_decl* (``alphabet_decl``'s ``inductive Letter ...``) plus the
    manual ``instance : Fintype Letter`` every dcfl statement needs
    (langlib's ``is_DPDA`` takes ``[Fintype T]`` on the alphabet -- see
    ``agent_system.lib.lean_ir``'s module docstring, "Alphabet type" ->
    ``deriving Fintype`` paragraph, for why this isn't just ``deriving
    Fintype`` on the inductive itself)."""
    return f"{alpha_decl}\n\n{fintype_instance_decl(sym_map)}"


def _render_grammar_case(lang_spec: dict, negate: bool) -> tuple[LeanStatement | None, str | None]:
    terminals = lang_spec.get("terminals")
    alpha = alphabet_decl(terminals) if isinstance(terminals, list) else None
    if alpha is None:
        return None, "grammar language_spec missing a usable 'terminals' list"
    alpha_decl, sym_map = alpha
    lang_decl = grammar_decl(lang_spec, sym_map)
    if lang_decl is None:
        return None, "grammar language_spec could not be translated (malformed rules or symbols)"
    stmt = LeanStatement(
        _alpha_decl_with_fintype(alpha_decl, sym_map),
        lang_decl,
        _dcfl_theorem_decl(negate),
        _dcfl_imports(True),
    )
    return stmt, None


def _letter_domain_setbuilder(lang_spec: dict, sym_map: dict[str, str]) -> str | None:
    """The ``{a^n b^n c^m | ...}``-shaped ``set_builder`` (see module
    docstring) -- ``None`` if it isn't that exact shape."""
    word_pattern = lang_spec.get("word_pattern")
    variables = lang_spec.get("variables")
    constraints = lang_spec.get("constraints", [])
    if not isinstance(word_pattern, str) or not isinstance(variables, list) or not variables:
        return None
    if not isinstance(constraints, list):
        return None
    if len(word_pattern) != len(variables):
        return None

    var_by_name: dict[str, tuple[str, str]] = {}
    for v in variables:
        if not isinstance(v, dict):
            return None
        name = v.get("name")
        domain = v.get("domain")
        if not isinstance(name, str) or len(name) != 1 or name in var_by_name:
            return None
        m = _LETTER_DOMAIN_RE.fullmatch(domain or "")
        if not m:
            return None
        letter, rep = m.group(1), m.group(2)
        if letter not in sym_map:
            return None
        var_by_name[name] = (letter, rep)

    if sorted(word_pattern) != sorted(var_by_name):
        return None
    seen_in_pattern: set[str] = set()
    for ch in word_pattern:
        if ch in seen_in_pattern:
            return None
        seen_in_pattern.add(ch)

    count_ident = {name: f"n_{name}" for name in var_by_name}
    parts: list[str] = []
    domain_conds: list[str] = []
    for ch in word_pattern:
        letter, rep = var_by_name[ch]
        cid = count_ident[ch]
        parts.append(f"List.replicate {cid} Letter.{sym_map[letter]}")
        if rep == "+":
            domain_conds.append(f"{cid} ≥ 1")

    extra_conds: list[str] = []
    for c in constraints:
        if not isinstance(c, dict):
            return None
        args = c.get("args")
        if not isinstance(args, dict):
            return None
        kind = c.get("kind")
        left, op, right = args.get("left"), args.get("op"), args.get("right")
        lean_op = _REL_LEAN.get(op) if isinstance(op, str) else None
        if lean_op is None or left not in count_ident:
            return None
        if kind == "length_cmp":
            if right not in count_ident:
                return None
            extra_conds.append(f"{count_ident[left]} {lean_op} {count_ident[right]}")
        elif kind == "integer_cmp":
            if not isinstance(right, int) or isinstance(right, bool):
                return None
            extra_conds.append(f"{count_ident[left]} {lean_op} {right}")
        else:
            return None

    word_expr = " ++ ".join(parts)
    all_conds = domain_conds + extra_conds
    quant = " ".join(count_ident[name] for name in var_by_name)
    body = f"w = {word_expr}"
    if all_conds:
        body += " ∧ " + " ∧ ".join(all_conds)
    inner = f"∃ {quant} : ℕ, {body}" if quant else body
    return f"def L : Language Letter := {{w : List Letter | {inner}}}"


def _pattern_body_or_none(pattern, sym_map: dict[str, str]) -> str | None:
    if isinstance(pattern, ExponentPattern):
        return pattern_body(pattern.segments, pattern.condition, sym_map)
    parts = getattr(pattern, "parts", None)
    if parts is None:
        return None
    bodies = [_pattern_body_or_none(p, sym_map) for p in parts]
    if any(b is None for b in bodies):
        return None
    return " ∨ ".join(f"({b})" for b in bodies)


def _render_setbuilder_case(ir: dict, lang_spec: dict, negate: bool) -> tuple[LeanStatement | None, str | None]:
    alphabet = ir.get("alphabet") or lang_spec.get("alphabet")
    alpha = alphabet_decl(alphabet)
    if alpha is None:
        return None, "no usable alphabet declared in IR for a dcfl 'set_builder' language_spec"
    alpha_decl, sym_map = alpha

    lang_decl = _letter_domain_setbuilder(lang_spec, sym_map)
    if lang_decl is None:
        # Fallback: word_pattern carries exponent notation directly (a plain
        # "a^n b^n"-style string) rather than the variables+domains shape.
        word_pattern = lang_spec.get("word_pattern")
        pattern = parse_exponent_pattern(word_pattern) if isinstance(word_pattern, str) else None
        body = _pattern_body_or_none(pattern, sym_map) if pattern is not None else None
        if body is None:
            return None, (
                "dcfl 'set_builder' language_spec is neither the letter-domain "
                "shape (each variable a single letter with a '+'/'*' domain, "
                "word_pattern a straight concatenation of the variable names, "
                "constraints limited to length_cmp/integer_cmp) nor exponent "
                "notation in word_pattern"
            )
        lang_decl = f"def L : Language Letter := {{w : List Letter | {body}}}"

    stmt = LeanStatement(
        _alpha_decl_with_fintype(alpha_decl, sym_map),
        lang_decl,
        _dcfl_theorem_decl(negate),
        _dcfl_imports(False),
    )
    return stmt, None
