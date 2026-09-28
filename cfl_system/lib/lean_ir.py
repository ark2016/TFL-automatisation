"""CFL-direction IR -> Lean 4 statement translator (docs/VERDICT_POLICY.md R-Lean).

Thin wrapper around ``agent_system.lib.lean_ir``, which owns the shared
machinery (``LeanStatement``, the ``Sym``/``NT`` alphabet and grammar
builders, and ``pattern_body``, the exponent-notation segments/condition ->
Lean converter) and the theorem-formulation rationale (see that module's
docstring for the full citation trail: why the theorem uses Mathlib's own
``Language.IsContextFree`` rather than langlib's ``is_CF``, and the known gap
that langlib isn't yet a Lake dependency of ``agent_system/docker/tfl_lean``).

What this module adds, that ``agent_system.lib.lean_ir`` cannot contain
itself (root ``CLAUDE.md`` "Import direction": ``cfl_system`` may import
``agent_system``, never the reverse):

- the ``"cfl"``/``"non_cfl"`` directions (``Language.IsContextFree`` /
  ``¬``), including the langlib pumping/Ogden imports a ``non_cfl`` proof
  body needs (``Langlib.Classes.ContextFree.Pumping.Pumping``,
  ``...Basics.Ogden``);
- **full** exponent-notation support for the ``natural`` kind --
  ``cfl_system.lib.exponent_pattern.parse_exponent_pattern``'s real parser
  (unions via ``"A union B"``, richer arithmetic, multi-letter units),
  feeding its ``ExponentPattern``/union-of-``ExponentPattern`` result into
  ``agent_system.lib.lean_ir.pattern_body`` -- agent_system's own
  ``natural``-kind handling only covers a small subset (its own examples
  don't need more; see its docstring).
"""

from __future__ import annotations

from agent_system.lib.lean_ir import (
    LeanStatement,
    alphabet_decl,
    grammar_decl,
    pattern_body,
)
from cfl_system.lib.exponent_pattern import ExponentPattern, parse_exponent_pattern

__all__ = ["render_statement", "render_statement_verbose"]

_DIRECTIONS_CFL = {"cfl": False, "non_cfl": True}


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
    if direction not in _DIRECTIONS_CFL:
        return None, f"direction {direction!r} is not a cfl_system direction ('cfl'/'non_cfl')"
    if not isinstance(ir, dict):
        return None, "ir must be a dict"
    negate = _DIRECTIONS_CFL[direction]

    lang_spec = ir.get("language_spec")
    if not isinstance(lang_spec, dict):
        return None, "ir.language_spec is missing or not an object"
    kind = lang_spec.get("kind")

    if kind == "grammar":
        return _render_grammar_case(lang_spec, negate)
    if kind == "natural":
        return _render_natural_case(ir, lang_spec, negate)
    return None, f"unsupported language_spec.kind {kind!r} for direction {direction!r}"


def _cfl_imports(negate: bool) -> list[str]:
    imports = ["import TflLean", "import Mathlib.Computability.ContextFreeGrammar"]
    if negate:
        imports += [
            "import Langlib.Classes.ContextFree.Pumping.Pumping",
            "import Langlib.Classes.ContextFree.Basics.Ogden",
        ]
    return imports


def _cfl_theorem_decl(negate: bool) -> str:
    prop = "L.IsContextFree"
    if negate:
        prop = f"¬ {prop}"
    return f"theorem tfl_main : {prop}"


def _render_grammar_case(lang_spec: dict, negate: bool) -> tuple[LeanStatement | None, str | None]:
    terminals = lang_spec.get("terminals")
    alpha = alphabet_decl(terminals) if isinstance(terminals, list) else None
    if alpha is None:
        return None, "grammar language_spec missing a usable 'terminals' list"
    alpha_decl, sym_map = alpha
    lang_decl = grammar_decl(lang_spec, sym_map)
    if lang_decl is None:
        return None, "grammar language_spec could not be translated (malformed rules or symbols)"
    stmt = LeanStatement(alpha_decl, lang_decl, _cfl_theorem_decl(negate), _cfl_imports(negate))
    return stmt, None


def _pattern_body_or_none(pattern, sym_map: dict[str, str]) -> str | None:
    """``agent_system.lib.lean_ir.pattern_body`` for one ``ExponentPattern``,
    or the ``∨``-join of it over each branch of a
    ``cfl_system.lib.exponent_pattern`` union (duck-typed via ``.parts``,
    since ``_UnionPattern`` is a private name of that module)."""
    if isinstance(pattern, ExponentPattern):
        return pattern_body(pattern.segments, pattern.condition, sym_map)
    parts = getattr(pattern, "parts", None)
    if parts is None:
        return None
    bodies = [_pattern_body_or_none(p, sym_map) for p in parts]
    if any(b is None for b in bodies):
        return None
    return " ∨ ".join(f"({b})" for b in bodies)


def _render_natural_case(ir: dict, lang_spec: dict, negate: bool) -> tuple[LeanStatement | None, str | None]:
    description = lang_spec.get("description")
    alphabet = ir.get("alphabet") or lang_spec.get("alphabet")
    if not isinstance(description, str):
        return None, "natural language_spec missing 'description'"
    alpha = alphabet_decl(alphabet)
    if alpha is None:
        return None, "no usable alphabet declared in IR for a 'natural' language_spec"
    alpha_decl, sym_map = alpha

    pattern = parse_exponent_pattern(description)
    if pattern is None:
        return None, (
            "natural-language description is not exponent notation "
            "(cfl_system.lib.exponent_pattern.parse_exponent_pattern "
            "returned None)"
        )
    body = _pattern_body_or_none(pattern, sym_map)
    if body is None:
        return None, (
            "exponent-notation pattern parsed, but uses a letter outside the "
            "declared alphabet, '-' in an exponent, or another shape "
            "agent_system.lib.lean_ir.pattern_body does not support"
        )
    lang_decl = f"def L : Language Sym := {{w : List Sym | {body}}}"
    stmt = LeanStatement(alpha_decl, lang_decl, _cfl_theorem_decl(negate), _cfl_imports(negate))
    return stmt, None
