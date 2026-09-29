"""CFL-direction IR -> Lean 4 statement translator (docs/VERDICT_POLICY.md R-Lean).

Thin wrapper around ``agent_system.lib.lean_ir``, which owns the shared
machinery (``LeanStatement``, the ``Letter``/``NT`` alphabet and grammar
builders, ``pattern_body``/``pattern_tree_body``, the exponent-notation
segments/condition -> Lean converter, ``predicate_to_lean``, the reversal
templates and the union/intersection composites) and the theorem-formulation
rationale (see that module's docstring for the full citation trail: why the
theorem uses Mathlib's own ``Language.IsContextFree`` rather than langlib's
``is_CF``, and langlib's availability as a Lake dependency of
``agent_system/docker/tfl_lean``).

What this module adds, that ``agent_system.lib.lean_ir`` cannot contain
itself (root ``CLAUDE.md`` "Import direction": ``cfl_system`` may import
``agent_system``, never the reverse):

- the ``"cfl"``/``"non_cfl"`` directions (``Language.IsContextFree`` /
  ``¬``), including the langlib pumping/Ogden imports a ``non_cfl`` proof
  body needs (``Langlib.Classes.ContextFree.Pumping.Pumping``,
  ``...Basics.Ogden``);
- ``grammar`` -> Mathlib ``ContextFreeGrammar`` with ``L := g.language``
  (the rules are a ``Finset`` of ``ContextFreeRule``; ε-rules and unit rules
  are ordinary rules);
- ``grammar_filter`` (``cfl_system/examples/task_grammar_filter_49.json``) ->
  ``L := g.language ⊓ {w | <filter>}`` with the IR predicate translated by
  ``agent_system.lib.lean_ir.predicate_to_lean`` (a ``natural_language_filter``
  cannot be translated -> ``None``);
- ``exists_decomposition`` (``parts`` / ``concat_pattern`` with ``rev(X)`` /
  ``alphabets`` / ``constraints``, e.g. ``{w w v vᴿ}``) ->
  ``{x | ∃ p_w p_v : List Letter, x = p_w ++ p_w ++ p_v ++ p_v.reverse ∧ ...}``;
- **full** exponent-notation support for the ``natural`` kind --
  ``cfl_system.lib.exponent_pattern.parse_exponent_pattern``'s real parser
  (unions via ``"A union B"``, richer arithmetic, multi-letter units),
  feeding its ``ExponentPattern``/union-of-``ExponentPattern`` result into
  ``agent_system.lib.lean_ir.pattern_tree_body`` -- agent_system's own
  ``natural``-kind handling only covers a small subset (its own examples
  don't need more; see its docstring) -- plus the palindrome / reversal
  templates and top-level ``∪``/``∩`` of natural branches, which
  ``agent_system.lib.lean_ir.build_language`` splits into ``⊔``/``⊓`` of
  ``Language`` definitions.

Every language whose membership Lean can compute also gets a decidable
companion (``LeanStatement.decidable_decl``) so the tests can ``#eval`` the
generated formulation against ``cfl_system.lib.cfl_oracle``; grammar
languages have none, a ``grammar_filter`` gets ``Fb`` for its filter part.
"""

from __future__ import annotations

import re

from agent_system.lib.lean_ir import (
    COMPOSITE_KINDS,
    LangDef,
    LeanStatement,
    alphabet_decl,
    build_language,
    composite_alphabet,
    grammar_decl,
    letters_only,
    make_statement,
    palindrome_body,
    pattern_tree_body,
    predicate_to_lean,
    set_language,
    word_template_body,
)
from cfl_system.lib.exponent_pattern import parse_exponent_pattern

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
    if kind in ("grammar_filter", "exists_decomposition") or kind in COMPOSITE_KINDS:
        return _render_generic_case(ir, lang_spec, negate)
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


def _statement(alpha_decl: str, ld: LangDef, negate: bool) -> LeanStatement:
    return make_statement(alpha_decl, ld, _cfl_theorem_decl(negate), _cfl_imports(negate))


# ---------------------------------------------------------------------------
# Leaves: one language_spec -> one LangDef
# ---------------------------------------------------------------------------


def _natural_leaf(spec: dict, sym_map: dict[str, str], suffix: str) -> LangDef | None:
    """Exponent notation (the real parser: unions, arithmetic, multi-letter
    units), else a strict palindrome / reversal template."""
    description = spec.get("description")
    if not isinstance(description, str):
        return None
    pattern = parse_exponent_pattern(description)
    if pattern is not None:
        body = pattern_tree_body(pattern, sym_map)
        if body is None:
            return None
        return set_language(suffix, body, pattern_tree_body(pattern, sym_map, decidable=True))
    pal = palindrome_body(description, sym_map)
    if pal is not None:
        return set_language(suffix, pal, pal)
    tpl = word_template_body(description, sym_map)
    if tpl is not None:
        return set_language(suffix, tpl[0], tpl[1])
    return None


def _grammar_leaf(spec: dict, sym_map: dict[str, str], suffix: str) -> LangDef | None:
    text = grammar_decl(spec, sym_map, suffix=suffix)
    return None if text is None else LangDef(text)


def _grammar_filter_leaf(spec: dict, sym_map: dict[str, str], suffix: str) -> LangDef | None:
    """``g.language ⊓ {w | filter}``. The oracle evaluates the filter with the
    word bound to ``w`` only (``cfl_oracle._eval_filter_predicate``), so ``w``
    is the one variable the filter may mention."""
    grammar, filt = spec.get("grammar"), spec.get("filter")
    if not isinstance(grammar, dict) or not isinstance(filt, dict):
        return None
    if filt.get("kind") == "natural_language_filter" or "natural_language_filter" in filt:
        return None  # prose: no mechanical translation (the oracle is approximate there too)
    body = predicate_to_lean(filt, sym_map, {"w": "w"})
    if body is None:
        return None
    text = grammar_decl(grammar, sym_map, suffix=suffix, filter_body=body)
    if text is None:
        return None
    return LangDef(
        text,
        f"def Fb{suffix} (w : List Letter) : Bool := decide ({body})",
        decidable_name="Fb",
        covers="filter_only",
    )


_CANDIDATES = (
    "def Cand{suffix} (x : List Letter) : List (List Letter) :=\n"
    "  (List.range (x.length + 1)).flatMap fun i => (List.range (x.length + 1)).flatMap fun j =>\n"
    "    [(x.drop i).take j, ((x.drop i).take j).reverse]\n\n"
)


def _exists_decomposition_leaf(spec: dict, sym_map: dict[str, str], suffix: str) -> LangDef | None:
    """``{x | ∃ parts, x = <concat_pattern> ∧ <alphabet restrictions> ∧
    <constraints>}`` -- ``cfl_oracle._exists_decomposition_oracle``'s
    semantics: every part occurs in the pattern (plain or as ``rev(part)``),
    an ``alphabets`` entry restricts a part's letters (an empty/missing entry
    is "any letter"), constraints see the word as ``w`` unless a part is
    called ``w`` (then the part shadows it, as the oracle's ``{"w": word,
    **bindings}`` does)."""
    parts, pattern = spec.get("parts"), spec.get("concat_pattern")
    alphabets = spec.get("alphabets", {})
    constraints = spec.get("constraints", [])
    if not (isinstance(parts, list) and parts and all(isinstance(p, str) and p for p in parts)):
        return None
    if len(set(parts)) != len(parts):
        return None
    if not (isinstance(pattern, list) and pattern) or not isinstance(alphabets, dict):
        return None
    if not isinstance(constraints, list):
        return None
    if not all(isinstance(k, str) and isinstance(v, list) for k, v in alphabets.items()):
        return None
    if any(k not in parts for k in alphabets):
        return None

    ident: dict[str, str] = {}
    used = set()
    for i, p in enumerate(parts):
        cand = f"p_{p}" if re.fullmatch(r"[A-Za-z0-9_]+", p) else f"p{i}"
        while cand in used:
            cand += "_"
        used.add(cand)
        ident[p] = cand

    terms: list[str] = []
    seen: set[str] = set()
    for elem in pattern:
        if not isinstance(elem, str):
            return None
        m = re.fullmatch(r"rev\((\w+)\)", elem)
        name, rev = (m.group(1), True) if m else (elem, False)
        if name not in ident:
            return None
        seen.add(name)
        terms.append(f"{ident[name]}.reverse" if rev else ident[name])
    if seen != set(parts):
        return None

    clauses = ["x = " + " ++ ".join(terms)]
    for p in parts:
        allowed = alphabets.get(p) or []
        if not allowed:
            continue
        if not all(isinstance(a, str) for a in allowed):
            return None
        letters = [letter for letter in sym_map if letter in allowed]
        if not letters:
            return None
        if len(letters) < len(sym_map):
            clauses.append(f"({letters_only(ident[p], letters, sym_map)})")

    env = {"w": "x", **ident}
    for c in constraints:
        prop = predicate_to_lean(c, sym_map, env)
        if prop is None:
            return None
        clauses.append(f"({prop})")
    matrix = " ∧ ".join(clauses)

    quant = " ".join(ident[p] for p in parts)
    body = f"∃ {quant} : List Letter, {matrix}"
    cands = "".join(f"∃ {ident[p]} ∈ Cand{suffix} x, " for p in parts)
    return set_language(
        suffix,
        body,
        cands + matrix,
        binder="x",
        decidable_prefix=_CANDIDATES.format(suffix=suffix),
    )


def _cfl_leaf(spec: dict, sym_map: dict[str, str], suffix: str) -> LangDef | None:
    kind = spec.get("kind")
    if kind == "grammar":
        return _grammar_leaf(spec, sym_map, suffix)
    if kind == "natural":
        return _natural_leaf(spec, sym_map, suffix)
    if kind == "grammar_filter":
        return _grammar_filter_leaf(spec, sym_map, suffix)
    if kind == "exists_decomposition":
        return _exists_decomposition_leaf(spec, sym_map, suffix)
    return None


# ---------------------------------------------------------------------------
# Top-level cases
# ---------------------------------------------------------------------------


def _render_grammar_case(lang_spec: dict, negate: bool) -> tuple[LeanStatement | None, str | None]:
    terminals = lang_spec.get("terminals")
    alpha = alphabet_decl(terminals) if isinstance(terminals, list) else None
    if alpha is None:
        return None, "grammar language_spec missing a usable 'terminals' list"
    alpha_decl, sym_map = alpha
    lang_decl = grammar_decl(lang_spec, sym_map)
    if lang_decl is None:
        return None, "grammar language_spec could not be translated (malformed rules or symbols)"
    return _statement(alpha_decl, LangDef(lang_decl), negate), None


def _render_natural_case(ir: dict, lang_spec: dict, negate: bool) -> tuple[LeanStatement | None, str | None]:
    description = lang_spec.get("description")
    alphabet = ir.get("alphabet") or lang_spec.get("alphabet")
    if not isinstance(description, str):
        return None, "natural language_spec missing 'description'"
    alpha = alphabet_decl(alphabet)
    if alpha is None:
        return None, "no usable alphabet declared in IR for a 'natural' language_spec"
    alpha_decl, sym_map = alpha

    ld = build_language(lang_spec, sym_map, "", _cfl_leaf)
    if ld is not None:
        return _statement(alpha_decl, ld, negate), None
    if parse_exponent_pattern(description) is None:
        return None, (
            "natural-language description is not exponent notation "
            "(cfl_system.lib.exponent_pattern.parse_exponent_pattern "
            "returned None), nor a palindrome / reversal template, nor a "
            "union/intersection of such"
        )
    return None, (
        "exponent-notation pattern parsed, but uses a letter outside the "
        "declared alphabet, '-' in an exponent, or another shape "
        "agent_system.lib.lean_ir.pattern_body does not support"
    )


def _render_generic_case(ir: dict, lang_spec: dict, negate: bool) -> tuple[LeanStatement | None, str | None]:
    """``grammar_filter`` / ``exists_decomposition`` / ``union`` /
    ``intersection`` -- the kinds that need :func:`composite_alphabet`."""
    kind = lang_spec.get("kind")
    symbols = composite_alphabet(ir, lang_spec)
    alpha = alphabet_decl(symbols) if symbols else None
    if alpha is None:
        return None, f"no usable alphabet declared or derivable for a {kind!r} language_spec"
    alpha_decl, sym_map = alpha
    ld = build_language(lang_spec, sym_map, "", _cfl_leaf)
    if ld is None:
        if kind == "grammar_filter":
            why = (
                "the filter is a natural-language filter or uses a predicate/"
                "expression shape agent_system.lib.lean_ir.predicate_to_lean "
                "does not support, or the grammar is malformed"
            )
        elif kind == "exists_decomposition":
            why = (
                "a part is missing from concat_pattern, an alphabet/constraint "
                "is outside what the translator supports, or the shape is malformed"
            )
        else:
            why = "a branch is not a supported language_spec"
        return None, f"{kind} language_spec could not be translated: {why}"
    return _statement(alpha_decl, ld, negate), None
