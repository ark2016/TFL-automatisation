"""LL(k) tools (P1, group ``ll``), wrapped from ``ll_system/lib``.

Four tools, inventory ``docs/TOOL_INVENTORY.md`` §6:

* ``first_follow``      -- NULLABLE, FIRST_k, FOLLOW_k and the strong director
  sets of a given grammar (``first_follow.compute_all``). Exact: ``verified``.
* ``ll_table``          -- the LL(k) test of a given grammar for one k
  (``ll_table_builder.check_ll_k``: strong test, then the full test with local
  follow sets, docs/THEORY.md §3.1), or the search for the least k
  (``find_min_ll_k``). LL(k) -> ``verified``; a conflict for that k ->
  ``refuted`` (VERDICT_POLICY §1); budget exhausted -> ``not_verified``;
  "no k ≤ K" without a certificate -> ``bounded_pass`` (R5).
* ``ll_parse``          -- a predictive parse of one word with the strong
  LL(k) table (``ll_parser.LLParser``). Exact for that grammar: ``verified``.
* ``grammar_transform`` -- left-recursion removal, left factoring and the other
  ``grammar_transforms`` steps, plus an exhaustive membership comparison of the
  input and output grammars up to a length (CYK on both): no mismatch ->
  ``bounded_pass``, a mismatch -> ``refuted``, never ``verified`` (§4
  ll/constructive).

Every answer is about the **given grammar**. Whether the *language* is LL(k)
is a different question: left recursion or a conflict of one grammar is not
evidence about the language (docs/THEORY.md §3.1, VERDICT_POLICY R5,
ll_system/CLAUDE.md "left recursion ≠ not LL").

Grammar format (shared, TOOL_INVENTORY §0): ``{"terminals": [...],
"nonterminals": [...], "start": "S", "rules": [{"lhs": "S", "rhs": [...]}]}``;
``rhs: []`` (or ``["ε"]``) is ε. Terminals are single characters: FIRST_k
strings, lookaheads and parsed words are character strings in the lib.
``$`` and ``ε`` are reserved. Each tool takes either ``grammar`` or an ``ir``
that carries one (LL Format 3 top-level ``grammar``; ``language_spec`` of kind
``grammar``; the ``grammar`` inside a CFL ``grammar_filter`` spec).
A submitted ``grammar`` with undeclared, reserved or overlapping symbols (or a
start symbol that is no nonterminal) is ``refuted`` with the errors in
``data.validation_errors`` (VERDICT_POLICY §1); the same defect in a grammar
read from an ``ir`` is ``invalid_input`` (a broken IR, not an artifact).

Cost control: FIRST_k / FOLLOW_k have no budget of their own and the number of
k-prefixes grows like (|Σ|+1)^k. When that bound is small the lib runs in the
tool's thread; above :data:`LOOKAHEAD_INLINE_LIMIT` it runs in a killable
child process (``run_with_timeout(mode="process")``). ``check_ll_k`` /
``find_min_ll_k`` always get ``time_budget_s`` for the full test.
``grammar_transform`` runs in a child process as a whole (the transforms have
no budget, and ε-elimination is exponential in the nullable positions of a
rule, which is also bounded up front).
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ll_system.lib import first_follow as _ff
from ll_system.lib import grammar_transforms as _gt
from ll_system.lib import ll_table_builder as _tb
from ll_system.lib.ll_parser import LLParser
from ll_system.lib.utils import RESERVED_SYMBOLS, normalize_grammar, validate_grammar_symbols

from tfl_deep_agent.tools.base import (
    DEFAULT_LIMITS,
    Deadline,
    InputTooLarge,
    ToolArgs,
    ToolInputError,
    ToolResult,
    ToolTimeout,
    ToolUnsupported,
    Word,
    check_enumeration,
    check_grammar_size,
    check_word,
    enumeration_size,
    max_len_within,
    run_with_timeout,
    tfl_tool,
)

__all__ = [
    "LL_MAX_K", "LOOKAHEAD_INLINE_LIMIT", "Grammar", "GrammarRule",
    "FirstFollowArgs", "LLTableArgs", "LLParseArgs", "GrammarTransformArgs", "TransformOp",
    "first_follow", "ll_table", "ll_parse", "grammar_transform",
    "resolve_grammar", "grammar_id", "compare_grammars", "InvalidGrammar",
]

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

#: Largest k the ll tools accept. Above DEFAULT_LIMITS.max_k (6) on purpose:
#: eval task ll-15 (S → a^11 b | a^11 c) asks about LL(11) / LL(12). Large k
#: with a large alphabet goes to a killable process (LOOKAHEAD_INLINE_LIMIT).
LL_MAX_K = 12

#: Up to this many possible k-prefixes (words of length ≤ k over Σ ∪ {$}) the
#: lib runs in the tool's thread; above it, in a child process that is killed
#: on timeout.
LOOKAHEAD_INLINE_LIMIT = 5_000

#: Wall clock of one ll tool call, and of the child process inside it.
LL_TOOL_TIMEOUT_S = 60.0
LL_PROCESS_TIMEOUT_S = 52.0
#: Default / maximum ``time_budget_s`` handed to check_ll_k / find_min_ll_k
#: (it covers only the full LL(k) test, so it stays well below the timeout).
DEFAULT_TIME_BUDGET_S = 20.0
MAX_TIME_BUDGET_S = 40.0

#: Nonterminal names: the transforms invent names such as ``E_prime_1_lf_2``.
MAX_NONTERMINAL_NAME = 40
#: A transformed grammar may grow, but not without bound.
MAX_TRANSFORMED_RULES = 3_000
MAX_TRANSFORMED_RHS = 100
MAX_TRANSFORMED_NONTERMINALS = 600
#: ε-elimination adds up to 2^m variants of a rule with m nullable positions.
MAX_EPSILON_VARIANTS = 20_000
#: grammar_transform's equivalence check: words up to max_len over the
#: terminals, all of them (default length: as many as fit into this budget).
EQUIV_DEFAULT_WORDS = 2_000
EQUIV_DEFAULT_MAX_LEN = 8
EQUIV_MAX_LEN = 10
EQUIV_TIME_S = 35.0
MAX_COUNTEREXAMPLES = 5
#: Parse steps / derivation forms returned to the model.
MAX_STEPS_SHOWN = 200

_LEGEND = {
    "": "ε (the empty string); in FIRST_k: the sequence derives ε",
    "$": "end-of-input marker; FOLLOW_k and lookaheads are padded with $ up to length k",
}
_SCOPE_NOTE = (
    "This is a statement about the given grammar only. Whether the language is LL(k) is a separate "
    "question: a conflict or left recursion of one grammar is not evidence about the language "
    "(docs/THEORY.md §3.1, VERDICT_POLICY R5)."
)


# ---------------------------------------------------------------------------
# Args schemas
# ---------------------------------------------------------------------------

NonterminalName = Annotated[str, Field(
    min_length=1, max_length=MAX_NONTERMINAL_NAME,
    description="a nonterminal name, e.g. S, A, E_prime_1")]
Terminal = Annotated[str, Field(
    min_length=1, max_length=1,
    description="one terminal character (the LL lib is character-level); not $ or ε")]
GrammarSymbol = Annotated[str, Field(
    min_length=1, max_length=MAX_NONTERMINAL_NAME,
    description="a terminal or nonterminal of this grammar")]


class GrammarRule(BaseModel):
    """One production lhs → rhs."""

    model_config = ConfigDict(extra="forbid")

    lhs: NonterminalName
    rhs: list[GrammarSymbol] = Field(
        default_factory=list, max_length=DEFAULT_LIMITS.max_rhs_len,
        description='right-hand side as a list of symbols; [] (or ["ε"]) is the empty production')


class Grammar(BaseModel):
    """A context-free grammar in the shared format (TOOL_INVENTORY §0)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["grammar"] | None = Field(
        default=None, description='optional; "grammar" when copied from an IR language_spec (ignored)')
    terminals: list[Terminal] = Field(
        max_length=DEFAULT_LIMITS.max_alphabet,
        description="terminal characters, each of length 1, disjoint from the nonterminals")
    nonterminals: list[NonterminalName] = Field(
        min_length=1, max_length=DEFAULT_LIMITS.max_nonterminals,
        description="nonterminal names; must contain start")
    start: NonterminalName = Field(description="the start nonterminal")
    rules: list[GrammarRule] = Field(
        max_length=DEFAULT_LIMITS.max_grammar_rules,
        description="productions; several rules may share an lhs (alternatives)")


class _GrammarArgs(ToolArgs):
    grammar: Grammar | None = Field(
        default=None,
        description="the grammar to analyse; give exactly one of grammar / ir")
    ir: dict[str, Any] | None = Field(
        default=None,
        description=("a task IR that contains an explicit grammar: LL Format 3 top-level `grammar`, "
                     "a `language_spec` of kind `grammar` (LL Format 2, REG/CFL/DCFL grammar tasks), or "
                     "the `grammar` of a CFL `grammar_filter` spec; give exactly one of grammar / ir"))

    @model_validator(mode="after")
    def _exactly_one_source(self) -> _GrammarArgs:
        if (self.grammar is None) == (self.ir is None):
            raise ValueError("give exactly one of `grammar` or `ir`")
        return self


class FirstFollowArgs(_GrammarArgs):
    k: int = Field(default=1, ge=1, le=LL_MAX_K, description="lookahead length k ≥ 1")
    include_director_sets: bool = Field(
        default=True,
        description=("also return, per rule A → α, FIRST_k(α) and the strong director set "
                     "FIRST_k(α) ⊕_k FOLLOW_k(A)"))


class LLTableArgs(_GrammarArgs):
    k: int | None = Field(
        default=None, ge=1, le=LL_MAX_K,
        description="test whether the grammar is LL(k) for this k (default 1 when max_k is not given)")
    max_k: int | None = Field(
        default=None, ge=1, le=LL_MAX_K,
        description=("instead of one k: find the least k ≤ max_k for which the grammar is LL(k) "
                     "(also detects the left-recursion certificate 'not LL(k) for any k')"))
    include_table: bool = Field(
        default=True,
        description="return the strong LL(k) parse table {nonterminal: {lookahead: rhs}} when it exists")
    time_budget_s: float = Field(
        default=DEFAULT_TIME_BUDGET_S, ge=1.0, le=MAX_TIME_BUDGET_S,
        description="seconds for the full LL(k) test (local follow sets); running out gives 'unknown'")

    @model_validator(mode="after")
    def _one_mode(self) -> LLTableArgs:
        if self.k is not None and self.max_k is not None:
            raise ValueError("give `k` (test one k) or `max_k` (search the least k), not both")
        return self


class LLParseArgs(_GrammarArgs):
    k: int = Field(default=1, ge=1, le=LL_MAX_K, description="lookahead length k of the parse table")
    word: Word = Field(description="the word to parse, a plain string of terminal characters; '' is ε")
    include_steps: bool = Field(
        default=False, description="also return the raw parser steps (stack top, lookahead, position)")
    time_budget_s: float = Field(
        default=DEFAULT_TIME_BUDGET_S, ge=1.0, le=MAX_TIME_BUDGET_S,
        description="seconds for the LL(k) test that precedes the parse")


TransformOp = Literal[
    "remove_useless_symbols", "eliminate_epsilon_rules", "eliminate_unit_rules",
    "eliminate_left_recursion", "left_factor", "to_ll_normal_form",
]


class GrammarTransformArgs(_GrammarArgs):
    ops: list[TransformOp] = Field(
        min_length=1, max_length=8,
        description=("transformations applied in this order. remove_useless_symbols: drop non-productive "
                     "and unreachable nonterminals. eliminate_epsilon_rules: remove A → ε (keeps S → ε when "
                     "ε ∈ L). eliminate_unit_rules: remove A → B. eliminate_left_recursion: direct and indirect "
                     "(fails on hidden nullable-prefix recursion; use to_ll_normal_form then). left_factor: "
                     "factor common prefixes. to_ll_normal_form: ε, unit, left recursion, factoring in one go"))
    check_equivalence: bool = Field(
        default=True,
        description=("compare membership in the input and the result on every word up to max_len (CYK on "
                     "both grammars)"))
    max_len: int | None = Field(
        default=None, ge=0, le=EQUIV_MAX_LEN,
        description=("longest word of the equivalence check; default: the largest length whose words fit "
                     f"into {EQUIV_DEFAULT_WORDS} words, at most {EQUIV_DEFAULT_MAX_LEN}"))


# ---------------------------------------------------------------------------
# Grammar helpers
# ---------------------------------------------------------------------------

def _grammar_from_ir(ir: dict[str, Any]) -> tuple[Any, str]:
    top = ir.get("grammar")
    if isinstance(top, dict):
        return top, "ir.grammar"
    spec = ir.get("language_spec")
    if isinstance(spec, dict):
        if spec.get("kind") == "grammar":
            return spec, "ir.language_spec"
        inner = spec.get("grammar")
        if spec.get("kind") == "grammar_filter" and isinstance(inner, dict):
            return inner, "ir.language_spec.grammar"
    kind = spec.get("kind") if isinstance(spec, dict) else None
    raise ToolUnsupported(
        f"this IR has no explicit grammar (task_type={ir.get('task_type')!r}, language_spec.kind={kind!r}); "
        "the ll tools analyse a given grammar. Write a grammar and pass it as `grammar`.")


def _semantic_errors(g: dict[str, Any]) -> list[str]:
    errors = list(validate_grammar_symbols(g))
    terminals, nonterminals = g["terminals"], g["nonterminals"]
    if len(set(terminals)) != len(terminals):
        errors.append("terminals contain duplicates")
    if len(set(nonterminals)) != len(nonterminals):
        errors.append("nonterminals contain duplicates")
    declared = set(terminals) | set(nonterminals)
    for i, rule in enumerate(g["rules"]):
        if rule["lhs"] not in nonterminals:
            errors.append(f"rules[{i}]: lhs {rule['lhs']!r} is not a declared nonterminal")
        rhs = rule["rhs"]
        if rhs == ["ε"]:
            continue
        for j, sym in enumerate(rhs):
            if sym in RESERVED_SYMBOLS:
                errors.append(f"rules[{i}].rhs[{j}]: {sym!r} is reserved; write ε as rhs [] (or exactly [\"ε\"])")
            elif sym not in declared:
                errors.append(f"rules[{i}].rhs[{j}]: symbol {sym!r} is not declared (terminals ∪ nonterminals)")
        if len(errors) > 20:
            break
    return errors


class InvalidGrammar(ToolInputError):
    """A submitted ``grammar`` that parses but is not a valid grammar
    (undeclared, reserved or overlapping symbols, a start symbol that is no
    nonterminal). The tools report it as ``refuted`` (VERDICT_POLICY §1:
    "невалидные символы грамматики"), the same rule as dpda_run and the cfl
    group. A grammar read from an ``ir`` stays ``invalid_input``: that is a
    broken task IR, not an artifact of the agent."""

    def __init__(self, errors: list[str], grammar: dict[str, Any]) -> None:
        super().__init__("invalid grammar: " + "; ".join(errors[:20]))
        self.errors = errors
        self.grammar = grammar


def _resolve_or_refute(args: _GrammarArgs) -> tuple[dict[str, Any], str] | ToolResult:
    try:
        return resolve_grammar(args)
    except InvalidGrammar as exc:
        return ToolResult.success(
            {"valid": False, "validation_errors": exc.errors[:50], "grammar_source": "grammar",
             "grammar_id": grammar_id(exc.grammar), "evidence_role": "invalid_artifact",
             "scope": "grammar", "claim": "the submitted grammar is a valid context-free grammar"},
            trust="refuted",
            reason="the submitted grammar is invalid (VERDICT_POLICY §1: invalid grammar symbols): "
                   + "; ".join(exc.errors[:5]))


def resolve_grammar(args: _GrammarArgs) -> tuple[dict[str, Any], str]:
    """The grammar of the call as a plain normalized dict (ε rules as ``[]``)
    and where it came from. Raises ``InvalidGrammar`` (a submitted grammar with
    bad symbols), ``ToolInputError`` / ``ToolUnsupported``."""
    if args.grammar is not None:
        model, source = args.grammar, "grammar"
    else:
        raw, source = _grammar_from_ir(args.ir or {})
        try:
            model = Grammar.model_validate(raw)
        except ValidationError as exc:
            msgs = "; ".join(
                f"{'.'.join(map(str, e.get('loc', ()))) or '(root)'}: {e.get('msg')}"
                for e in exc.errors(include_url=False))
            raise ToolInputError(f"{source} is not a valid grammar: {msgs[:1500]}") from None
    g = model.model_dump(exclude={"kind"})
    check_grammar_size(g)
    errors = _semantic_errors(g)
    if errors:
        if source == "grammar":
            raise InvalidGrammar(errors, g)
        raise ToolInputError(f"invalid grammar in {source}: " + "; ".join(errors[:20]))
    g = normalize_grammar(g)
    return ({"terminals": list(g["terminals"]), "nonterminals": list(g["nonterminals"]),
             "start": g["start"], "rules": [{"lhs": r["lhs"], "rhs": list(r["rhs"])} for r in g["rules"]]},
            source)


def grammar_id(g: dict[str, Any]) -> str:
    """A short content hash of a normalized grammar, so evidence can name the
    exact grammar it is about."""
    canon = json.dumps({"terminals": sorted(g["terminals"]), "nonterminals": sorted(g["nonterminals"]),
                        "start": g["start"],
                        "rules": sorted([r["lhs"], list(r["rhs"])] for r in g["rules"])},
                       ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return "g-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def _structure(g: dict[str, Any]) -> dict[str, Any]:
    """Left recursion (after removing useless symbols, as in find_min_ll_k)
    and the useless nonterminals. Cheap."""
    reduced = _gt.remove_useless_symbols(g)
    kept = set(reduced["nonterminals"]) if reduced["rules"] else set()
    return {
        "left_recursive": _gt.is_left_recursive(reduced),
        "useless_nonterminals": sorted(set(g["nonterminals"]) - kept),
    }


def _lookahead_universe(g: dict[str, Any], k: int) -> int:
    return enumeration_size(len(g["terminals"]) + 1, k)


def _call_lib(fn: Callable[..., Any], /, *args: Any, risky: bool, **kwargs: Any) -> Any:
    """Run a lib call in this thread, or in a killable child process when its
    lookahead universe is large (see module docstring)."""
    if not risky:
        return fn(*args, **kwargs)
    return run_with_timeout(fn, *args, timeout_s=LL_PROCESS_TIMEOUT_S, mode="process", **kwargs)


def _first_follow_data(g: dict[str, Any], k: int, include_director_sets: bool) -> dict[str, Any]:
    """Module level (picklable) so it can run in a child process."""
    nullable, first_k, follow_k = _ff.compute_all(g, k)
    out: dict[str, Any] = {
        "nullable": sorted(nullable),
        "first_k": {nt: sorted(first_k.get(nt, set())) for nt in g["nonterminals"]},
        "follow_k": {nt: sorted(follow_k.get(nt, set())) for nt in g["nonterminals"]},
    }
    if include_director_sets:
        rows = []
        for rule in g["rules"]:
            lhs, rhs = rule["lhs"], rule["rhs"]
            rows.append({
                "lhs": lhs, "rhs": rhs,
                "first_k_rhs": sorted(_ff.compute_first_k_seq(rhs, g, k, first_k, nullable)),
                "director": sorted(_ff.director_set(g, lhs, rhs, k, first_k, follow_k, nullable)),
            })
        out["director_sets"] = rows
    return out


# ---------------------------------------------------------------------------
# first_follow
# ---------------------------------------------------------------------------

@tfl_tool(group="ll", max_trust="verified", timeout_s=LL_TOOL_TIMEOUT_S)
def first_follow(args: FirstFollowArgs) -> ToolResult:
    """Compute NULLABLE, FIRST_k and FOLLOW_k of a given context-free grammar (exact fixed point), and per
    rule A → α the strong director set FIRST_k(α) ⊕_k FOLLOW_k(A). In the sets, "" is ε and "$" pads the
    end of input up to length k. Terminals must be single characters. Pass `grammar` or an `ir` that
    contains one. Result trust: verified (an exact computation for this grammar)."""
    resolved = _resolve_or_refute(args)
    if isinstance(resolved, ToolResult):
        return resolved
    g, source = resolved
    risky = _lookahead_universe(g, args.k) > LOOKAHEAD_INLINE_LIMIT
    sets = _call_lib(_first_follow_data, g, args.k, args.include_director_sets, risky=risky)
    data = {"grammar_id": grammar_id(g), "grammar_source": source, "k": args.k, "start": g["start"],
            **sets, **_structure(g), "legend": _LEGEND}
    return ToolResult.success(
        data, trust="verified",
        reason=f"exact fixed-point computation of NULLABLE / FIRST_{args.k} / FOLLOW_{args.k} for the given "
               "grammar (ll_system first_follow.compute_all)")


# ---------------------------------------------------------------------------
# ll_table
# ---------------------------------------------------------------------------

def _trim_check(res: dict[str, Any], include_table: bool) -> dict[str, Any]:
    out = {
        "k": res.get("k"),
        "is_ll_k": res.get("is_ll_k"),
        "is_strong_ll_k": res.get("is_strong_ll_k"),
        "test_complete": res.get("test_complete"),
        "strong_conflicts": list(res.get("conflicts") or [])[:30],
        "ll_conflicts": list(res.get("ll_conflicts") or [])[:30],
        "first_sets": res.get("first_sets"),
        "follow_sets": res.get("follow_sets"),
    }
    if include_table and res.get("parse_table") is not None:
        out["parse_table"] = res["parse_table"]
    return out


def _ll_table_check(args: LLTableArgs, g: dict[str, Any], base: dict[str, Any]) -> ToolResult:
    k = args.k or 1
    risky = _lookahead_universe(g, k) > LOOKAHEAD_INLINE_LIMIT
    res = _call_lib(_tb.check_ll_k, g, k, time_budget_s=args.time_budget_s, risky=risky)
    data = {**base, "mode": "check", **_trim_check(res, args.include_table)}
    gid = base["grammar_id"]
    verdict = res.get("is_ll_k")
    if verdict is True:
        strong = "strong LL(k) test passed" if res.get("is_strong_ll_k") else \
            "not strong LL(k), but the full test with local follow sets passed"
        data["claim"] = f"grammar {gid} is LL({k})"
        return ToolResult.success(
            data, trust="verified",
            reason=f"complete LL({k}) test of the given grammar ({strong}; docs/THEORY.md §3.1)")
    if verdict is False:
        data["claim"] = f"grammar {gid} is not LL({k})"
        return ToolResult.success(
            data, trust="refuted",
            reason=f"conflict in the full LL({k}) test of the given grammar (local follow sets; the strong "
                   f"test fails too); refutes 'this grammar is "
                   f"LL({k})' (VERDICT_POLICY §1), says nothing about the language")
    data["claim"] = f"unknown whether grammar {gid} is LL({k})"
    return ToolResult.success(
        data, trust="not_verified", bounds={"max_k": k, "truncated": True, "timeout_s": args.time_budget_s},
        reason=f"the full LL({k}) test ran out of its {args.time_budget_s} s budget before a conclusive answer "
               "(budget exhausted means unknown, docs/THEORY.md §3.1)")


def _ll_table_min(args: LLTableArgs, g: dict[str, Any], base: dict[str, Any]) -> ToolResult:
    max_k = args.max_k or 1
    risky = _lookahead_universe(g, max_k) > LOOKAHEAD_INLINE_LIMIT
    res = _call_lib(_tb.find_min_ll_k, g, max_k, time_budget_s=args.time_budget_s, risky=risky)
    gid = base["grammar_id"]
    data = {
        **base, "mode": "min_k", "max_k": max_k,
        **{key: res.get(key) for key in ("found", "k", "strong_k", "max_k_checked", "max_k_decided",
                                         "undetermined", "certificate", "not_ll_any_k")},
        "result_for_k": _trim_check(res["result_for_k"], args.include_table) if res.get("result_for_k") else {},
    }
    if res.get("found"):
        k = res["k"]
        lower = f" and not LL(j) for 1 ≤ j < {k}" if k > 1 else ""
        data["claim"] = f"grammar {gid} is LL({k}){lower}; least k = {k}"
        return ToolResult.success(
            data, trust="verified",
            reason=f"complete LL(j) tests for j = 1..{k}, each conclusive (docs/THEORY.md §3.1: minimality "
                   "needs a definite negative for every smaller k)")
    if res.get("certificate"):
        data["claim"] = f"grammar {gid} is not LL(k) for any k (left recursion after removing useless symbols)"
        return ToolResult.success(
            data, trust="verified",
            reason="left-recursion certificate for the given grammar (docs/THEORY.md §3.1, VERDICT_POLICY R5); "
                   "a statement about this grammar, not about the language")
    if res.get("undetermined"):
        decided = res.get("max_k_decided", 0)
        data["claim"] = (f"grammar {gid} is not LL(k) for k ≤ {decided}; unknown from k = {decided + 1}"
                         if decided else f"unknown whether grammar {gid} is LL(1)")
        return ToolResult.success(
            data, trust="not_verified",
            bounds={"max_k": decided, "truncated": True, "timeout_s": args.time_budget_s},
            reason="the search hit its time budget at an undecided k; the least k is unknown "
                   "(docs/THEORY.md §3.1: a limit means unknown)")
    checked = res.get("max_k_checked", max_k)
    data["claim"] = f"grammar {gid} is not LL(k) for k ≤ {checked} (no certificate for larger k)"
    return ToolResult.success(
        data, trust="bounded_pass", bounds={"max_k": checked},
        reason=f"LL(k) tests conclusive and negative for every k ≤ {checked}, no left-recursion certificate: "
               f"'not LL(k) for k ≤ {checked}' only (VERDICT_POLICY R5, confidence ≤ 0.85)")


@tfl_tool(group="ll", max_trust="verified", timeout_s=LL_TOOL_TIMEOUT_S)
def ll_table(args: LLTableArgs) -> ToolResult:
    """Test whether a given grammar is LL(k): the strong LL(k) test (global FOLLOW_k) and, when it fails, the
    full LL(k) test with local follow sets (docs/THEORY.md §3.1). With `k`: one k, returns conflicts and,
    for a strong LL(k) grammar, the parse table. With `max_k`: the least k ≤ max_k, or the left-recursion
    certificate "not LL(k) for any k". Trust: LL(k) -> verified; a conflict for that k -> refuted; least k
    found / certificate -> verified; no k ≤ max_k -> bounded_pass; budget exhausted -> not_verified. This is
    about the grammar only, never about whether the language is LL."""
    resolved = _resolve_or_refute(args)
    if isinstance(resolved, ToolResult):
        return resolved
    g, source = resolved
    base = {"grammar_id": grammar_id(g), "grammar_source": source, **_structure(g),
            "scope": "grammar", "scope_note": _SCOPE_NOTE, "legend": _LEGEND}
    if args.max_k is not None:
        return _ll_table_min(args, g, base)
    return _ll_table_check(args, g, base)


# ---------------------------------------------------------------------------
# ll_parse
# ---------------------------------------------------------------------------

def _render(symbols: list[str], sep: str) -> str:
    return sep.join(symbols) if symbols else "ε"


def _derivation(g: dict[str, Any], table: dict[str, dict[str, list[str]]],
                steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Productions the parser applied (in order: a leftmost derivation) and
    the sentential forms they produce."""
    nts = set(g["nonterminals"])
    sep = "" if all(len(s) == 1 for s in nts | set(g["terminals"])) else " "
    productions: list[dict[str, Any]] = []
    for st in steps:
        top = st.get("stack_top")
        if top in nts:
            rhs = table.get(top, {}).get(st.get("lookahead"))
            if rhs is not None:
                productions.append({"lhs": top, "rhs": list(rhs), "lookahead": st["lookahead"],
                                    "pos": st["pos"]})
    form = [g["start"]]
    forms = [_render(form, sep)]
    for p in productions[:MAX_STEPS_SHOWN]:
        idx = next((i for i, s in enumerate(form) if s in nts), None)
        if idx is None or form[idx] != p["lhs"]:
            break  # cannot happen for a predictive parse; stop rather than show a wrong form
        form = form[:idx] + [s for s in p["rhs"] if s != "ε"] + form[idx + 1:]
        forms.append(_render(form, sep))
    return productions, forms


@tfl_tool(group="ll", max_trust="verified", timeout_s=LL_TOOL_TIMEOUT_S)
def ll_parse(args: LLParseArgs) -> ToolResult:
    """Parse one word with the predictive (strong) LL(k) parser of a given grammar and return whether it is
    accepted, the productions applied (a leftmost derivation) and the sentential forms. The grammar must be
    strong LL(k) for this k: a grammar that is not LL(k) is rejected as invalid input, and one that is LL(k)
    but not strong LL(k) is unsupported (try a larger k, or the cfl `cyk` tool for membership). Trust:
    verified (an exact parse; for a strong LL(k) grammar the parser accepts exactly L(G))."""
    resolved = _resolve_or_refute(args)
    if isinstance(resolved, ToolResult):
        return resolved
    g, source = resolved
    word = check_word(args.word)
    k = args.k
    gid = grammar_id(g)
    base = {"grammar_id": gid, "grammar_source": source, "k": k, "word": word}
    outside = sorted({ch for ch in word if ch not in set(g["terminals"])})
    if outside:
        return ToolResult.success(
            {**base, "accepted": False, "outside_symbols": outside,
             "error": f"symbols {outside} are not terminals of the grammar", "productions": []},
            trust="verified",
            reason="the word uses symbols that are not terminals of the grammar, so it is not in L(G)")

    risky = _lookahead_universe(g, k) > LOOKAHEAD_INLINE_LIMIT
    res = _call_lib(_tb.check_ll_k, g, k, time_budget_s=args.time_budget_s, risky=risky)
    if res.get("is_ll_k") is False:
        raise ToolInputError(
            f"grammar {gid} is not LL({k}), so there is no LL({k}) parse table; use ll_table to see the "
            "conflicts or a larger k",
            data={"strong_conflicts": list(res.get("conflicts") or [])[:10],
                  "ll_conflicts": list(res.get("ll_conflicts") or [])[:10]})
    if res.get("is_ll_k") is None:
        raise ToolTimeout(f"the LL({k}) test of grammar {gid} did not finish within {args.time_budget_s} s")
    if not res.get("is_strong_ll_k") or res.get("parse_table") is None:
        raise ToolUnsupported(
            f"grammar {gid} is LL({k}) but not strong LL({k}); the lib's predictive parser needs one global "
            "strong LL(k) table. Try a larger k (ll_table), or the cfl `cyk` tool for membership.")

    table = res["parse_table"]
    out = LLParser.from_table(g, table, k).parse(word)
    steps = list(out.get("steps") or [])
    productions, forms = _derivation(g, table, steps)
    data: dict[str, Any] = {
        **base, "accepted": bool(out.get("accepted")), "error": out.get("error"),
        "n_steps": len(steps), "productions": productions[:MAX_STEPS_SHOWN],
    }
    if data["accepted"]:
        data["derivation"] = forms
    if args.include_steps:
        data["steps"] = steps[:MAX_STEPS_SHOWN]
    if len(productions) > MAX_STEPS_SHOWN or len(steps) > MAX_STEPS_SHOWN:
        data["shown_truncated"] = True
    if str(out.get("error") or "").startswith("Step limit"):
        data["accepted"] = None
        return ToolResult.success(
            data, trust="not_verified",
            reason="the parser hit its step limit; membership of this word is unknown")
    verdict = "w ∈ L(G)" if data["accepted"] else "w ∉ L(G)"
    return ToolResult.success(
        data, trust="verified",
        reason=f"exact predictive parse with the strong LL({k}) table of grammar {gid}: {verdict}")


# ---------------------------------------------------------------------------
# grammar_transform
# ---------------------------------------------------------------------------

def _epsilon_variants(g: dict[str, Any]) -> int:
    nullable = _ff.compute_nullable(g)
    return sum(2 ** sum(1 for s in r["rhs"] if s in nullable) for r in g["rules"])


def _check_transformed_size(g: dict[str, Any], op: str) -> None:
    try:
        check_grammar_size(g, max_rules=MAX_TRANSFORMED_RULES, max_rhs_len=MAX_TRANSFORMED_RHS,
                           max_nonterminals=MAX_TRANSFORMED_NONTERMINALS)
    except InputTooLarge as exc:
        raise InputTooLarge(f"after {op}: the transformed grammar is too large ({exc})") from None


def _apply(op: str, g: dict[str, Any]) -> dict[str, Any]:
    if op in ("eliminate_epsilon_rules", "to_ll_normal_form"):
        variants = _epsilon_variants(g)
        if variants > MAX_EPSILON_VARIANTS:
            raise InputTooLarge(
                f"{op}: ε-elimination would build {variants} rule variants, limit {MAX_EPSILON_VARIANTS}")
    fn = {
        "remove_useless_symbols": _gt.remove_useless_symbols,
        "eliminate_epsilon_rules": _gt.eliminate_epsilon_rules,
        "eliminate_unit_rules": _gt.eliminate_unit_rules,
        "eliminate_left_recursion": _gt.eliminate_left_recursion,
        "left_factor": _gt.left_factor,
        "to_ll_normal_form": _gt.to_ll_normal_form,
    }[op]
    try:
        out = fn(g)
    except ValueError as exc:
        hint = " Apply eliminate_epsilon_rules and eliminate_unit_rules first, or use to_ll_normal_form." \
            if op == "eliminate_left_recursion" else ""
        raise ToolUnsupported(f"{op}: {exc}{hint}") from None
    # The lib rebuilds terminals from a set: keep the input order (deterministic output).
    out_terminals = set(out["terminals"])
    terminals = [t for t in g["terminals"] if t in out_terminals]
    terminals += sorted(out_terminals - set(terminals))
    # ε / unit elimination build their rules from sets too: order rules by lhs
    # (nonterminal order), then rhs.
    order = {nt: i for i, nt in enumerate(out["nonterminals"])}
    rules = sorted(({"lhs": r["lhs"], "rhs": list(r["rhs"])} for r in out["rules"]),
                   key=lambda r: (order.get(r["lhs"], len(order)), r["rhs"]))
    return {"terminals": terminals, "nonterminals": list(out["nonterminals"]),
            "start": out["start"], "rules": rules}


def _membership(g: dict[str, Any]) -> Callable[[str], bool]:
    from cfl_system.lib.cnf import to_cnf
    from cfl_system.lib.cyk import cyk_parse

    cnf = to_cnf(g)
    terminals = set(g["terminals"])

    def accepts(word: str) -> bool:
        if any(ch not in terminals for ch in word):
            return False
        return bool(cyk_parse(cnf, word))

    return accepts


def compare_grammars(g1: dict[str, Any], g2: dict[str, Any], max_len: int,
                     deadline: Deadline | None = None) -> dict[str, Any]:
    """Exact CYK membership of both grammars on **every** word up to *max_len*
    over the union of their terminals (shortest words first). Stops at the
    deadline; ``max_len`` in the result is then the longest length covered
    completely."""
    deadline = deadline or Deadline(None)
    alphabet = sorted(set(g1["terminals"]) | set(g2["terminals"]))
    in1, in2 = _membership(g1), _membership(g2)
    counterexamples: list[dict[str, Any]] = []
    n_words = 0
    covered = -1
    truncated = False
    for length in range(max_len + 1):
        for letters in itertools.product(alphabet, repeat=length):
            if deadline.expired():
                truncated = True
                break
            w = "".join(letters)
            a, b = in1(w), in2(w)
            n_words += 1
            if a != b and len(counterexamples) < MAX_COUNTEREXAMPLES:
                counterexamples.append({"word": w, "in_input": a, "in_transformed": b})
        if truncated:
            break
        covered = length
    return {"alphabet": alphabet, "max_len": max(covered, 0), "covered_any": covered >= 0, "n_words": n_words, "truncated": truncated,
            "counterexamples": counterexamples}


@tfl_tool(group="ll", max_trust="verified", timeout_s=LL_TOOL_TIMEOUT_S, timeout_mode="process")
def grammar_transform(args: GrammarTransformArgs) -> ToolResult:
    """Transform a given grammar: remove left recursion (eliminate_left_recursion), left-factor (left_factor),
    remove useless symbols, ε-rules or unit rules, or all of it (to_ll_normal_form). Returns the new grammar,
    the size and left recursion after each step and, by default, an exhaustive membership comparison of the
    input and the result on every word up to max_len (CYK on both). Trust: no mismatch -> bounded_pass
    (agreement up to max_len, never a proof of equivalence); a mismatch -> refuted (with the word); check
    skipped or impossible -> well_formed. Check the result with ll_table: the transformed grammar is a new
    grammar, LL facts about it do not carry over to the input grammar."""
    resolved = _resolve_or_refute(args)
    if isinstance(resolved, ToolResult):
        return resolved
    g, source = resolved
    before = _structure(g)
    current = g
    steps: list[dict[str, Any]] = []
    for op in args.ops:
        current = _apply(op, current)
        _check_transformed_size(current, op)
        steps.append({"op": op, "rules": len(current["rules"]), "nonterminals": len(current["nonterminals"]),
                      "left_recursive": _gt.is_left_recursive(_gt.remove_useless_symbols(current))})
    after = _structure(current)
    data: dict[str, Any] = {
        "grammar_source": source, "input_grammar_id": grammar_id(g), "grammar_id": grammar_id(current),
        "ops": list(args.ops), "steps": steps, "grammar": current,
        "left_recursive_before": before["left_recursive"], "left_recursive_after": after["left_recursive"],
        "useless_nonterminals_after": after["useless_nonterminals"],
        "note": "a transformation changes derivations, not (by design) the language; LL facts about the "
                "new grammar do not carry over to the input grammar",
    }
    if not args.check_equivalence:
        data["equivalence"] = {"status": "skipped"}
        return ToolResult.success(
            data, trust="well_formed",
            reason="transformed by trusted lib code; language equality was not checked")

    alphabet_size = len(set(g["terminals"]) | set(current["terminals"]))
    if args.max_len is None:
        max_len = max_len_within(alphabet_size, EQUIV_DEFAULT_WORDS, EQUIV_DEFAULT_MAX_LEN)
    else:
        max_len = args.max_len
        check_enumeration(alphabet_size, max_len)
    try:
        eq = compare_grammars(g, current, max_len, Deadline(EQUIV_TIME_S))
    except Exception as exc:  # noqa: BLE001 - a CNF/CYK failure means "not checked", not a tool failure
        data["equivalence"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:500]}
        return ToolResult.success(
            data, trust="well_formed",
            reason="transformed by trusted lib code; the membership comparison could not run")

    if eq["counterexamples"]:
        data["equivalence"] = {"status": "mismatch", **eq}
        w = eq["counterexamples"][0]["word"]
        return ToolResult.success(
            data, trust="refuted", bounds={"max_len": max_len, "n_words": eq["n_words"]},
            reason=f"the input and the transformed grammar disagree on {w!r} (exact CYK on both): "
                   "L(result) ≠ L(input)")
    if not eq["covered_any"]:
        data["equivalence"] = {"status": "error", "error": "no word was checked before the time limit", **eq}
        return ToolResult.success(
            data, trust="well_formed", reason="transformed by trusted lib code; no word could be compared")
    data["equivalence"] = {"status": "no_mismatch", **eq}
    return ToolResult.success(
        data, trust="bounded_pass",
        bounds={"max_len": eq["max_len"], "n_words": eq["n_words"], "truncated": eq["truncated"]},
        reason=f"the input and the transformed grammar agree on all {eq['n_words']} words up to length "
               f"{eq['max_len']} (exact CYK on both; agreement on a bounded sample, not a proof of equivalence, "
               "VERDICT_POLICY §4 ll/constructive)")
