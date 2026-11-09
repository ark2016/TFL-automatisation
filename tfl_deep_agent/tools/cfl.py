"""CFL tool group (P1): typed wrappers over ``cfl_system.lib``.

Tools (inventory: ``tfl_deep_agent/docs/TOOL_INVENTORY.md`` §4):

* ``to_cnf``         -- Chomsky normal form of a grammar (``cnf.to_cnf``).
* ``cyk``            -- exact membership in L(G) for a given grammar (``cyk.cyk_parse``).
* ``pda_run``        -- run words through a nondeterministic PDA (``pda_simulator``).
* ``parikh``         -- Parikh vectors and a finite Parikh-image sample (``parikh``).
* ``pump_check_cfl`` -- check a pumping / Ogden argument, or one concrete
  ``u v^i w x^i y`` split, against the IR's membership oracle
  (``claim_verifier.verify_pumping_claim``).

Trust is computed here from what the lib returned (docs/VERDICT_POLICY.md):

* ``cyk`` and an accepting ``pda_run`` are complete checks of the *given*
  artifact -> ``verified``; a claimed answer that disagrees -> ``refuted``.
* a ``pda_run`` rejection is ``verified`` only when the breadth-first search
  provably explored every reachable configuration (no stack was cut at the
  simulator's depth bound), otherwise ``bounded_pass`` with those bounds.
* ``to_cnf`` is a construction by trusted code (``well_formed``), not evidence
  about the task language.
* ``parikh``: a finite sample of vectors is always semilinear; at most
  ``well_formed`` and never a basis for a CFL / non-CFL verdict (§4 cfl/parikh).
* ``pump_check_cfl``: at most ``well_formed``; ``refuted`` when the chosen word
  is not in L or a word the argument declares outside L is in L (§4 cfl/pumping,
  ogden). A surviving split is never a refutation.

Every oracle check is relative to the IR the agent built: results carry
``ir_sha256`` (the first 16 hex digits of the SHA-256 of the IR's canonical JSON).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cfl_system.lib import cnf as _cnf
from cfl_system.lib import cyk as _cyk
from cfl_system.lib import parikh as _parikh
from cfl_system.lib import pda_simulator as _pda
from cfl_system.lib.cfl_oracle_test import normalize_agent_pda
from cfl_system.lib.claim_verifier import input_grammar_is_cfg, verify_pumping_claim

from tfl_deep_agent.tools.base import (
    DEFAULT_LIMITS,
    Deadline,
    InputTooLarge,
    Symbol,
    ToolArgs,
    ToolError,
    ToolInputError,
    ToolResult,
    ToolUnsupported,
    Word,
    check_grammar_size,
    check_word,
    run_with_timeout,
    tfl_tool,
)
from tfl_deep_agent.tools.ir import CLASS_DESCRIPTION, TFLClass, ir_fingerprint, require_valid
from tfl_deep_agent.tools.words import (
    ClassOracle,
    compare_with_oracle,
    comparison_result,
    generator_candidates,
    oracle_for,
)

__all__ = [
    "GrammarRule", "Grammar", "ToCnfArgs", "CykArgs", "PdaRunArgs", "ParikhArgs",
    "PumpSplit", "PumpCheckCflArgs", "GrammarVsIrArgs", "InputGrammarCfgArgs",
    "to_cnf", "cyk", "pda_run", "parikh", "pump_check_cfl", "grammar_vs_ir", "input_grammar_cfg",
    "ir_digest", "language_oracle", "AUTO_ORACLE_CLASSES", "INVALID_ARTIFACT",
]

# Limits specific to this group (DEFAULT_LIMITS covers the rest).
MAX_CYK_WORDS = 200
CYK_TABLE_MAX_LEN = 12
MAX_PDA_WORDS = 100
MAX_PDA_STATES = 200
MAX_PDA_TRANSITIONS = 600
MAX_PARIKH_WORDS = 500
PARIKH_MAX_LENGTH = 15            # TOOL_INVENTORY §4: max_length <= 15
PARIKH_PROCESS_TIMEOUT_S = 35.0   # the grammar / IR modes run in a killable process
MAX_PUMP_I = 10
MAX_PUMP_P = 50
EPSILON_TOKENS = frozenset({"ε", "eps", "epsilon", "λ"})


# ---------------------------------------------------------------------------
# Shared argument models and helpers
# ---------------------------------------------------------------------------

class GrammarRule(BaseModel):
    """One production ``lhs -> rhs``."""

    model_config = ConfigDict(extra="forbid")

    lhs: str = Field(min_length=1, max_length=32, description="a nonterminal")
    rhs: list[str] = Field(
        default_factory=list, max_length=DEFAULT_LIMITS.max_rhs_len,
        description="right-hand side as a list of symbols; [] is ε. A lone 'ε' "
                    "(or 'eps') is read as ε unless it is a declared symbol.")


class Grammar(BaseModel):
    """A context-free grammar in the shared IR format (``language_spec`` of kind
    ``grammar``). Unknown keys such as ``kind`` are ignored."""

    model_config = ConfigDict(extra="ignore")

    terminals: list[str] = Field(
        min_length=1, max_length=DEFAULT_LIMITS.max_alphabet,
        description="terminal symbols; cyk / parikh need single characters, since words "
                    "are read character by character")
    nonterminals: list[str] = Field(
        min_length=1, max_length=DEFAULT_LIMITS.max_nonterminals,
        description="nonterminal symbols, disjoint from the terminals")
    start: str = Field(min_length=1, max_length=32, description="the start nonterminal")
    rules: list[GrammarRule] = Field(
        min_length=1, max_length=DEFAULT_LIMITS.max_grammar_rules,
        description="productions [{lhs, rhs}]; several rules may share an lhs")


def _grammar_dict(g: Grammar) -> tuple[dict[str, Any], list[str]]:
    """Plain lib grammar dict, with ε tokens in a rhs dropped (unless declared).
    Returns (grammar, notes)."""
    declared = set(g.terminals) | set(g.nonterminals)
    eps = EPSILON_TOKENS - declared
    notes: list[str] = []
    rules = []
    for r in g.rules:
        rhs = [s for s in r.rhs if s not in eps]
        if len(rhs) != len(r.rhs):
            notes.append(f"{r.lhs} -> {' '.join(r.rhs)}: read {sorted(set(r.rhs) & eps)} as ε")
        rules.append({"lhs": r.lhs, "rhs": rhs})
    grammar = {"terminals": list(g.terminals), "nonterminals": list(g.nonterminals),
               "start": g.start, "rules": rules}
    check_grammar_size(grammar)
    return grammar, notes


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def ir_digest(obj: Any) -> str:
    """First 16 hex digits of SHA-256 over the canonical JSON of *obj* (an IR or
    an artifact), so a report can say which IR / grammar a check was run against."""
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()[:16]


#: ``data.evidence_role`` of a result that refutes a submitted artifact for
#: being invalid (VERDICT_POLICY §1: "невалидные символы грамматики" is a
#: ``refuted`` signal). The ll and dcfl groups use the same rule.
INVALID_ARTIFACT = "invalid_artifact"


class InvalidGrammar(ToolInputError):
    """The submitted grammar is well-shaped JSON but not a valid grammar
    (undeclared / overlapping symbols, a start symbol that is no
    nonterminal). The tools report it as ``refuted``, not as a failure."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("invalid grammar: " + "; ".join(errors[:20]))
        self.errors = errors


def _invalid_grammar_result(exc: InvalidGrammar, grammar: Mapping[str, Any]) -> ToolResult:
    return ToolResult.success(
        {"valid": False, "validation_errors": exc.errors, "grammar_sha256": ir_digest(grammar),
         "evidence_role": INVALID_ARTIFACT},
        trust="refuted",
        reason="the submitted grammar is invalid (VERDICT_POLICY §1: invalid grammar symbols): "
               + "; ".join(exc.errors[:5]))


def _convert_to_cnf(grammar: dict[str, Any]) -> dict[str, Any]:
    try:
        return _cnf.to_cnf(grammar)
    except ValueError as exc:      # cnf._validate: symbols, overlap, start
        raise InvalidGrammar([str(exc)]) from None
    except (KeyError, TypeError) as exc:
        raise ToolInputError(f"invalid grammar: {exc}") from None


def _cnf_for(grammar: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """(CNF grammar, converted?) -- an input already in CNF is used as is."""
    if _cnf.is_cnf(grammar):
        return grammar, False
    return _convert_to_cnf(grammar), True


def _require_single_char_terminals(grammar: Mapping[str, Any], tool: str) -> None:
    bad = [t for t in grammar["terminals"] if len(t) != 1]
    if bad:
        raise ToolUnsupported(
            f"{tool} reads words character by character, so every terminal must be one "
            f"character; got {bad}. Rename the terminals to single characters.")


def _check_expected(expected: Mapping[str, bool] | None, words: list[str]) -> None:
    if expected:
        missing = sorted(set(expected) - set(words))
        if missing:
            raise ToolInputError(f"expected names words that are not in words: {missing[:10]}")


def _dedup(words: list[str]) -> list[str]:
    return list(dict.fromkeys(words))


# ---------------------------------------------------------------------------
# Membership oracle for an IR (pump_check_cfl)
# ---------------------------------------------------------------------------

#: Classes tried, in this order, when pump_check_cfl gets no ``cls``.
AUTO_ORACLE_CLASSES = ("cfl", "dcfl", "ll", "reg")


def _never(_: str) -> None:
    return None


def _answer(oracle: ClassOracle) -> Callable[[str], bool | None]:
    """``word -> True | False | None`` for the libs (``ClassOracle.query``:
    an error, a bound or the work limit is ``None``, never "not in L")."""
    return lambda word: oracle.query(word)[0]


def language_oracle(ir: Mapping[str, Any], cls: str | None = None) -> tuple[ClassOracle | None, list[str]]:
    """The IR's membership oracle through the shared dispatch
    ``words.oracle_for`` (TOOL_INVENTORY §10). With *cls* only that class is
    used, and an IR that is invalid for it raises ``ToolInputError``; without
    it the classes :data:`AUTO_ORACLE_CLASSES` are tried in order and the
    first one for which the IR is valid and has an oracle wins. Returns
    (oracle or None, notes on what was tried).

    The grammar-only oracle of a CFL natural-language filter accepts a
    superset of L, so it is refused (its answers are not the IR's language).
    Other approximate oracles (``exact=False``) are returned; the caller must
    not let them refute."""
    notes: list[str] = []
    for c in ((cls,) if cls else AUTO_ORACLE_CLASSES):
        try:
            oracle = oracle_for(c, ir)
        except ToolUnsupported as exc:
            notes.append(f"{c}: {exc}")
            continue
        except ToolInputError as exc:
            if cls:
                raise
            notes.append(f"{c}: not a valid {c} IR ({str(exc)[:200]})")
            continue
        if getattr(oracle.fn, "approximation_reason", None) == "natural_language_filter":
            notes.append(f"{c}: the oracle checks only the grammar of a natural-language filter (a "
                         "superset of L); not used")
            continue
        return oracle, notes
    return None, notes


def _check_ir(ir: Mapping[str, Any]) -> None:
    if not isinstance(ir.get("language_spec"), Mapping) and not isinstance(ir.get("grammar"), Mapping):
        raise ToolInputError("ir needs a language_spec object (or, for LL Format 3, a top-level grammar)")


# ---------------------------------------------------------------------------
# to_cnf
# ---------------------------------------------------------------------------

class ToCnfArgs(ToolArgs):
    grammar: Grammar = Field(description="the grammar to convert")


@tfl_tool(group="cfl", max_trust="well_formed", timeout_s=20)
def to_cnf(args: ToCnfArgs) -> ToolResult:
    """Convert a context-free grammar to Chomsky normal form (rules A -> BC, A -> a, and
    S -> ε only for a start symbol that occurs on no right-hand side). Useful context for
    cyk or a CYK table by hand. Trust is at most well_formed: the conversion is done by
    trusted code, but a CNF grammar says nothing about the task language by itself."""
    grammar, notes = _grammar_dict(args.grammar)
    try:
        out = _convert_to_cnf(grammar)
    except InvalidGrammar as exc:
        return _invalid_grammar_result(exc, grammar)
    was_cnf = _cnf.is_cnf(grammar)
    nullable_start = any(r["lhs"] == out["start"] and not r["rhs"] for r in out["rules"])
    return ToolResult.success(
        {
            "cnf": out,
            "input_was_cnf": was_cnf,
            "output_is_cnf": _cnf.is_cnf(out),
            "rules_before": len(grammar["rules"]),
            "rules_after": len(out["rules"]),
            "generates_epsilon": nullable_start,
            "language_empty": not out["rules"],
            "grammar_sha256": ir_digest(grammar),
            "notes": notes + ["fresh nonterminals are named <prefix>_<n>; unproductive and "
                              "unreachable symbols are removed"],
        },
        trust="well_formed",
        reason="CNF built by cfl_system.lib.cnf.to_cnf (a construction, not evidence about "
               "the task language)",
    )


# ---------------------------------------------------------------------------
# cyk
# ---------------------------------------------------------------------------

class CykArgs(ToolArgs):
    grammar: Grammar = Field(description="the grammar G; it is converted to CNF first unless "
                                         "it already is in CNF")
    words: list[Word] = Field(
        min_length=1, max_length=MAX_CYK_WORDS,
        description=f"words to test for membership in L(G), at most {MAX_CYK_WORDS}, each at "
                    f"most {DEFAULT_LIMITS.max_word_len} characters; '' is ε")
    expected: dict[str, bool] | None = Field(
        default=None,
        description="optional claimed answers {word: in L(G)?} for some of the words; one "
                    "that CYK contradicts makes the result refuted")
    table_word: Word | None = Field(
        default=None, max_length=CYK_TABLE_MAX_LEN,
        description=f"optional word (at most {CYK_TABLE_MAX_LEN} characters) whose CYK table "
                    "over the CNF grammar is returned")


@tfl_tool(group="cfl", max_trust="verified", timeout_s=30)
def cyk(args: CykArgs) -> ToolResult:
    """Decide exactly whether each word is in L(G) for the GIVEN grammar G (CYK over its
    Chomsky normal form). Trust verified for the statements 'w in L(G)' / 'w not in L(G)'
    about this grammar only -- whether G describes the task language is a separate question
    (check that against the IR with an oracle tool). With expected answers, a contradicted
    one gives refuted."""
    grammar, notes = _grammar_dict(args.grammar)
    _require_single_char_terminals(grammar, "cyk")
    words = _dedup(args.words)
    _check_expected(args.expected, words)
    try:
        cnf_g, converted = _cnf_for(grammar)
    except InvalidGrammar as exc:
        return _invalid_grammar_result(exc, grammar)
    terminals = set(grammar["terminals"])
    deadline = Deadline(25.0)
    membership: dict[str, bool] = {}
    unchecked: list[str] = []
    for idx, w in enumerate(words):
        if deadline.expired():
            unchecked = words[idx:]
            break
        if not cnf_g["rules"]:
            membership[w] = False
        else:
            membership[w] = bool(_cyk.cyk_parse(cnf_g, w))
    if not membership:
        raise ToolError("no word could be checked within the time limit", kind="timeout")
    foreign = sorted({w for w in membership if set(w) - terminals})
    if foreign:
        notes.append(f"{len(foreign)} word(s) use symbols outside the terminals and are not in "
                     f"L(G): {foreign[:5]}")
    mismatches = [
        {"word": w, "expected": exp, "actual": membership[w]}
        for w, exp in (args.expected or {}).items() if w in membership and membership[w] != exp
    ]
    data: dict[str, Any] = {
        "membership": membership,
        "accepted": [w for w, v in membership.items() if v],
        "rejected": [w for w, v in membership.items() if not v],
        "mismatches": mismatches,
        "unchecked": unchecked,
        "converted_to_cnf": converted,
        "cnf_rules": len(cnf_g["rules"]),
        "grammar_sha256": ir_digest(grammar),
        "notes": notes,
    }
    if args.table_word is not None:
        tw = check_word(args.table_word, max_len=CYK_TABLE_MAX_LEN, what="table_word")
        table = _cyk.cyk_parse_table(cnf_g, tw) if cnf_g["rules"] else []
        data["table"] = {
            "word": tw,
            "start_symbol": cnf_g["start"],
            "cells": [
                {"start": i, "length": l + 1, "substring": tw[i:i + l + 1],
                 "nonterminals": sorted(table[i][l])}
                for l in range(len(tw)) for i in range(len(tw) - l) if table[i][l]
            ],
            "note": "cells use the CNF grammar's nonterminals (see to_cnf); empty cells omitted",
        }
    bounds = {"n_words": len(membership), "max_len": max(map(len, membership)),
              "truncated": bool(unchecked)} if unchecked else None
    if mismatches:
        return ToolResult.success(
            data, trust="refuted", bounds=bounds,
            reason=f"CYK contradicts {len(mismatches)} claimed answer(s), e.g. "
                   f"{mismatches[0]['word']!r} in L(G) is {mismatches[0]['actual']}")
    unchecked_expected = [w for w in (args.expected or {}) if w not in membership]
    if unchecked_expected:
        data["unchecked_expected"] = unchecked_expected
        return ToolResult.success(
            data, trust="not_verified", bounds=bounds,
            reason=f"the claimed answers are not verified: {len(unchecked_expected)} of them were not "
                   f"checked (time limit), e.g. {unchecked_expected[0]!r}; the {len(membership)} decided "
                   "word(s) in data are exact CYK answers for this grammar")
    reason = f"exact CYK membership in L(G) for {len(membership)} word(s) of this grammar"
    if unchecked:
        reason += f"; {len(unchecked)} word(s) not checked (time limit)"
    return ToolResult.success(data, trust="verified", bounds=bounds, reason=reason)


# ---------------------------------------------------------------------------
# pda_run
# ---------------------------------------------------------------------------

class PdaRunArgs(ToolArgs):
    pda: dict[str, Any] = Field(
        description="a PDA: {states, input_alphabet (single characters), stack_alphabet, "
                    "start_state, start_stack, accept_states (for final-state acceptance), "
                    "accept_mode: 'final_state' | 'empty_stack', transitions: [{from, input "
                    "(a symbol, or null for ε), stack_top, to, push: [stack symbols]}]}. "
                    "Each transition pops stack_top and pushes push.")
    words: list[Word] = Field(
        min_length=1, max_length=MAX_PDA_WORDS,
        description=f"words to run, at most {MAX_PDA_WORDS}; '' is ε")
    push_order: Literal["top_first", "top_last"] = Field(
        default="top_first",
        description="how to read push lists: 'top_first' (textbook / pda_builder contract: "
                    "push ['A','Z'] leaves A on top) or 'top_last' (the simulator's own "
                    "order: the last symbol ends on top)")
    acceptance_mode: Literal["final_state", "empty_stack"] | None = Field(
        default=None,
        description="acceptance mode when the PDA has no accept_mode key; default: "
                    "final_state if accept_states is given, else empty_stack")
    expected: dict[str, bool] | None = Field(
        default=None,
        description="optional claimed answers {word: accepted?}; one that the run "
                    "contradicts makes the result refuted")


def _pda_shape_checks(pda: Mapping[str, Any]) -> None:
    states = pda.get("states")
    transitions = pda.get("transitions")
    if isinstance(states, list) and len(states) > MAX_PDA_STATES:
        raise InputTooLarge(f"PDA has {len(states)} states, limit {MAX_PDA_STATES}")
    if isinstance(transitions, list) and len(transitions) > MAX_PDA_TRANSITIONS:
        raise InputTooLarge(f"PDA has {len(transitions)} transitions, limit {MAX_PDA_TRANSITIONS}")
    if transitions is not None and (
            not isinstance(transitions, list)
            or not all(isinstance(t, Mapping) for t in transitions)):
        raise ToolInputError("pda.transitions must be a list of objects")
    for i, t in enumerate(transitions or []):
        push = t.get("push", [])
        if push is not None and not isinstance(push, list):
            raise ToolInputError(f"pda.transitions[{i}].push must be a list of stack symbols")


@tfl_tool(group="cfl", max_trust="verified", timeout_s=30)
def pda_run(args: PdaRunArgs) -> ToolResult:
    """Run words through a (nondeterministic) pushdown automaton by breadth-first search over
    its configurations. 'Accepted' is exact (an accepting run was found): verified for this
    PDA. 'Rejected' is verified only when the search explored every reachable configuration;
    when stacks may have been cut at the simulator's depth bound it is bounded_pass with
    those bounds. A word whose search exceeds the step budget is unknown. With expected
    answers, a contradicted one gives refuted. Statements are about this PDA, not the task
    language."""
    _pda_shape_checks(args.pda)
    if args.push_order == "top_first":
        pda = normalize_agent_pda(dict(args.pda), args.acceptance_mode)
    else:
        pda = dict(args.pda)
        pda.setdefault("accept_mode", args.acceptance_mode
                       or ("final_state" if pda.get("accept_states") else "empty_stack"))
    try:
        errors = _pda.validate_pda(pda)
    except (TypeError, AttributeError, KeyError) as exc:
        raise ToolInputError(f"invalid PDA: {type(exc).__name__}: {exc}") from None
    if any(e.startswith("Missing required keys") for e in errors):
        # a shape problem of the call, not a statement about an automaton
        raise ToolError("invalid PDA: " + "; ".join(errors[:10]), kind="invalid_input",
                        data={"validation_errors": errors})
    if errors:
        return ToolResult.success(
            {"valid": False, "validation_errors": errors, "pda_sha256": ir_digest(args.pda),
             "evidence_role": INVALID_ARTIFACT},
            trust="refuted",
            reason="the submitted PDA is invalid (undeclared states / symbols; VERDICT_POLICY §1): "
                   + "; ".join(errors[:5]))
    bad_inputs = [s for s in pda["input_alphabet"] if not isinstance(s, str) or len(s) != 1]
    if bad_inputs:
        raise ToolUnsupported(f"the simulator reads words character by character; input symbols "
                              f"must be single characters, got {bad_inputs}")
    words = _dedup(args.words)
    _check_expected(args.expected, words)
    max_push = max((len(t.get("push") or []) for t in pda["transitions"]), default=0)
    deadline = Deadline(25.0)
    runs: dict[str, dict[str, Any]] = {}
    for w in words:
        if deadline.expired():
            runs[w] = {"accepted": None, "exact": False, "why": "not run: tool time limit"}
            continue
        try:
            r = _pda.pda_accepts_detailed(pda, w)
        except TimeoutError:
            runs[w] = {"accepted": None, "exact": False,
                       "why": f"search exceeded {_pda.MAX_STEPS} steps"}
            continue
        if r["accepted"]:
            runs[w] = {"accepted": True, "exact": True, "steps": r["steps_used"],
                       "accepting_config": r["accepting_config"]}
            continue
        # A configuration is skipped only when its stack would exceed MAX_STACK_DEPTH;
        # the largest stack a move can build is (deepest explored stack - 1) + longest push.
        complete = r["max_stack_depth"] - 1 + max_push <= _pda.MAX_STACK_DEPTH
        runs[w] = {"accepted": False, "exact": complete, "steps": r["steps_used"],
                   "configs_explored": r["configs_explored"],
                   "max_stack_depth": r["max_stack_depth"]}
    decided = {w: r["accepted"] for w, r in runs.items() if r["accepted"] is not None}
    unknown = [w for w, r in runs.items() if r["accepted"] is None]
    inexact = [w for w, r in runs.items() if r["accepted"] is False and not r["exact"]]
    mismatches = [
        {"word": w, "expected": exp, "actual": decided[w]}
        for w, exp in (args.expected or {}).items() if w in decided and decided[w] != exp
        # an inexact rejection cannot contradict a claimed acceptance
        and not (decided[w] is False and w in inexact)
    ]
    data = {
        "accepted": decided,
        "unknown": unknown,
        "runs": runs,
        "mismatches": mismatches,
        "accept_mode": pda["accept_mode"],
        "push_order": args.push_order,
        "pda_sha256": ir_digest(args.pda),
    }
    bounds = {"n_words": len(decided), "n_unknown": len(unknown),
              "max_len": max(map(len, words)), "max_steps": _pda.MAX_STEPS,
              "max_stack_depth": _pda.MAX_STACK_DEPTH, "truncated": bool(unknown or inexact)}
    if mismatches:
        m = mismatches[0]
        return ToolResult.success(
            data, trust="refuted", bounds=bounds,
            reason=f"the PDA run contradicts {len(mismatches)} claimed answer(s), e.g. "
                   f"{m['word']!r} accepted = {m['actual']}")
    if not decided:
        return ToolResult.success(data, trust="not_verified", bounds=bounds,
                                  reason="no word was decided within the simulator's step budget")
    # A claimed answer is open when its word is unknown, or when it claims an
    # acceptance and the only rejection found is inexact (stacks were capped).
    open_expected = [w for w, exp in (args.expected or {}).items()
                     if w in unknown or (exp is True and w in inexact)]
    if open_expected:
        data["open_expected"] = open_expected
        return ToolResult.success(
            data, trust="not_verified", bounds=bounds,
            reason=f"the claimed answers are not verified: {len(open_expected)} of them could not be "
                   f"decided (step budget or a capped stack), e.g. {open_expected[0]!r}; the other "
                   "runs in data are as reported")
    if inexact:
        return ToolResult.success(
            data, trust="bounded_pass", bounds=bounds,
            reason=f"{len(inexact)} rejection(s) come from a search whose stacks were capped at "
                   f"depth {_pda.MAX_STACK_DEPTH}; acceptances are exact")
    reason = f"exact PDA runs for {len(decided)} word(s) (complete configuration search)"
    if unknown:
        reason += f"; {len(unknown)} word(s) unknown (step budget)"
    return ToolResult.success(data, trust="verified", bounds=bounds, reason=reason)


# ---------------------------------------------------------------------------
# parikh
# ---------------------------------------------------------------------------

class ParikhArgs(ToolArgs):
    words: list[Word] | None = Field(
        default=None, max_length=MAX_PARIKH_WORDS,
        description="mode 1: words whose Parikh vectors (letter counts) to compute")
    alphabet: list[Symbol] | None = Field(
        default=None, min_length=1, max_length=DEFAULT_LIMITS.max_alphabet,
        description="mode 1: the ordered alphabet; vector component i counts alphabet[i] "
                    "(default: the sorted letters of the words)")
    grammar: Grammar | None = Field(
        default=None,
        description="mode 2: a grammar whose Parikh image is sampled from words derived up "
                    "to max_length")
    ir: dict[str, Any] | None = Field(
        default=None,
        description="mode 3: a CFL IR (language_spec kind grammar, grammar_filter or "
                    "repeated_subword) analysed by cfl_system.lib.parikh.analyze_parikh "
                    "(fixed max_length 15)")
    max_length: int = Field(
        default=10, ge=1, le=PARIKH_MAX_LENGTH,
        description=f"mode 2: longest derived word (1..{PARIKH_MAX_LENGTH})")

    @model_validator(mode="after")
    def _one_mode(self) -> ParikhArgs:
        modes = [m for m, v in (("words", self.words), ("grammar", self.grammar),
                                ("ir", self.ir)) if v is not None]
        if len(modes) != 1:
            raise ValueError("give exactly one of words, grammar or ir")
        if self.alphabet is not None and self.words is None:
            raise ValueError("alphabet is used only with words")
        return self


def _parikh_grammar_worker(grammar: dict[str, Any], max_length: int) -> dict[str, Any]:
    """Module level, so it can run in a killable spawn process."""
    alphabet = sorted(grammar["terminals"])
    vectors = _parikh.parikh_image_from_grammar(grammar, max_length=max_length)
    semi = _parikh.check_semilinearity(vectors, len(alphabet))
    return {"alphabet": alphabet, "vectors": sorted(vectors), "semilinearity": semi}


def _parikh_ir_worker(ir: dict[str, Any]) -> dict[str, Any]:
    return _parikh.analyze_parikh(ir)


_THEOREM_NOTE = ("a finite sample of vectors is always semilinear; looks_semilinear is a "
                 "heuristic, never a basis for a CFL / non-CFL verdict (VERDICT_POLICY §4 "
                 "cfl/parikh)")


@tfl_tool(group="cfl", max_trust="well_formed", timeout_s=45)
def parikh(args: ParikhArgs) -> ToolResult:
    """Parikh vectors (letter-count vectors). Three modes: (1) words + optional ordered
    alphabet -> the vector of each word; (2) grammar + max_length -> a finite sample of its
    Parikh image with semilinearity diagnostics; (3) ir -> cfl_system's Parikh analysis of
    the IR. Trust is at most well_formed: a finite sample is always semilinear, so this is
    diagnostics only and never a basis for a CFL / non-CFL verdict."""
    if args.words is not None:
        words = _dedup(args.words)
        alphabet = list(args.alphabet) if args.alphabet else sorted({c for w in words for c in w})
        if not alphabet:
            raise ToolInputError("no alphabet: give alphabet or at least one non-empty word")
        if any(len(s) != 1 for s in alphabet):
            raise ToolUnsupported("Parikh vectors count single characters; alphabet symbols "
                                  "must be one character each")
        if len(set(alphabet)) != len(alphabet):
            raise ToolInputError("alphabet has duplicate symbols")
        outside = sorted({c for w in words for c in w} - set(alphabet))
        if outside:
            raise ToolInputError(f"words use symbols outside the alphabet: {outside}")
        return ToolResult.success(
            {"mode": "words", "alphabet": alphabet,
             "vectors": {w: list(_parikh.parikh_vector(w, alphabet)) for w in words}},
            trust="well_formed", reason="exact letter counts of the given words")

    if args.grammar is not None:
        grammar, notes = _grammar_dict(args.grammar)
        _require_single_char_terminals(grammar, "parikh")
        try:
            _convert_to_cnf(grammar)  # validates symbols
        except InvalidGrammar as exc:
            return _invalid_grammar_result(exc, grammar)
        out = run_with_timeout(_parikh_grammar_worker, grammar, args.max_length,
                               timeout_s=PARIKH_PROCESS_TIMEOUT_S, mode="process")
        semi = out["semilinearity"]
        return ToolResult.success(
            {"mode": "grammar", "alphabet": out["alphabet"], "max_length": args.max_length,
             "vectors_sampled": out["vectors"], "n_vectors": len(out["vectors"]),
             "looks_semilinear": semi.get("looks_semilinear"),
             "sample_is_semilinear": semi.get("sample_is_semilinear"),
             "explanation": semi.get("explanation"),
             "sample_complete": False,
             "notes": notes + ["sampled by breadth-first derivation with a sentential-form cap; "
                               "words up to max_length may be missing",
                               "the full image of any CFG is semilinear (Parikh's theorem)"],
             "grammar_sha256": ir_digest(grammar)},
            trust="well_formed", bounds={"max_len": args.max_length},
            reason="finite Parikh-image sample; " + _THEOREM_NOTE)

    ir = dict(args.ir or {})
    _check_ir(ir)
    out = run_with_timeout(_parikh_ir_worker, ir, timeout_s=PARIKH_PROCESS_TIMEOUT_S,
                           mode="process")
    if out.get("evidence_scope") == "not_verified":
        raise ToolUnsupported(f"no Parikh analysis for this IR: {out.get('commutative_image')} "
                              f"{out.get('explanation', '')}".strip())
    data = {"mode": "ir", "ir_sha256": ir_digest(ir), **out}
    data["conclusion"] = None  # never a verdict
    return ToolResult.success(data, trust="well_formed", bounds={"max_len": 15},
                              reason=f"Parikh analysis ({out.get('evidence_scope')}); "
                                     + _THEOREM_NOTE)


# ---------------------------------------------------------------------------
# pump_check_cfl
# ---------------------------------------------------------------------------

class PumpSplit(BaseModel):
    """One decomposition z = u v w x y."""

    model_config = ConfigDict(extra="forbid")

    u: Word = Field(default="", description="prefix before v")
    v: Word = Field(default="", description="first pumped part")
    w: Word = Field(default="", description="middle part (not pumped)")
    x: Word = Field(default="", description="second pumped part")
    y: Word = Field(default="", description="suffix after x")


class PumpCheckCflArgs(ToolArgs):
    ir: dict[str, Any] = Field(
        description="the task IR; the membership oracle is built from it (results are "
                    "relative to this IR)")
    cls: TFLClass | None = Field(
        default=None,
        description="the IR's class, for its membership oracle. Default: the first of cfl, dcfl, ll, "
                    "reg for which the IR is valid and has an oracle. " + CLASS_DESCRIPTION)
    claim: dict[str, Any] | None = Field(
        default=None,
        description="mode 1: the prover's pumping or Ogden evidence JSON (word_chosen, cases "
                    "[{case, why_not_in_L}], all_cases_covered, word_instances {'3': z3, "
                    "'4': z4} with concrete words for p = 3 and 4, and for Ogden "
                    "marked_positions {'3': [...], '4': [...]}); a whole agent output with an "
                    "'evidence' object is accepted too")
    split: PumpSplit | None = Field(
        default=None,
        description="mode 2: one concrete split z = u v w x y to pump as u v^i w x^i y")
    method: Literal["auto", "pumping", "ogden"] = Field(
        default="auto",
        description="pumping lemma or Ogden's lemma; auto picks ogden when the claim's agent "
                    "is 'ogden' or marked positions are given")
    p: int | None = Field(
        default=None, ge=1, le=MAX_PUMP_P,
        description="mode 2: the pumping constant the split is meant for; checks |z| >= p and "
                    "|vwx| <= p (pumping) or the marked-position bounds (Ogden)")
    marked_positions: list[int] | None = Field(
        default=None, max_length=DEFAULT_LIMITS.max_word_len,
        description="mode 2, Ogden: 0-based marked positions in z (at least p of them; vx must "
                    "contain a marked position and vwx at most p)")
    i_values: list[int] = Field(
        default_factory=lambda: [0, 2, 3], min_length=1, max_length=8,
        description=f"mode 2: exponents i to try (0..{MAX_PUMP_I}); default [0, 2, 3]")
    claimed_exit_i: list[int] | None = Field(
        default=None, max_length=8,
        description="mode 2: exponents for which the argument claims u v^i w x^i y is NOT in "
                    "L; if the oracle says one of those words IS in L, the result is refuted")

    @model_validator(mode="after")
    def _one_mode(self) -> PumpCheckCflArgs:
        if (self.claim is None) == (self.split is None):
            raise ValueError("give exactly one of claim (a whole argument) or split (one split)")
        for name in ("i_values", "claimed_exit_i"):
            for i in getattr(self, name) or []:
                if not 0 <= i <= MAX_PUMP_I:
                    raise ValueError(f"{name}: exponents must be in 0..{MAX_PUMP_I}, got {i}")
        for m in self.marked_positions or []:
            if m < 0:
                raise ValueError("marked_positions must be non-negative")
        if self.claim is not None and (self.p is not None or self.marked_positions is not None
                                       or self.claimed_exit_i is not None):
            raise ValueError("p, marked_positions and claimed_exit_i belong to split mode; "
                             "in claim mode put them into the claim JSON")
        return self


def _lib_trust(value: Any) -> str:
    return value if value in ("refuted", "well_formed", "not_verified") else "not_verified"


def _oracle_info(oracle: ClassOracle | None) -> dict[str, Any] | None:
    return {**oracle.describe(), "class": oracle.cls} if oracle is not None else None


def _pump_claim(args: PumpCheckCflArgs, ir: dict[str, Any], oracle: ClassOracle | None,
                oracle_notes: list[str]) -> ToolResult:
    claim = dict(args.claim or {})
    evidence = claim
    if "word_chosen" not in claim and isinstance(claim.get("evidence"), Mapping):
        evidence = dict(claim["evidence"])
    method = args.method
    if method == "auto":
        method = "ogden" if (claim.get("agent") == "ogden"
                             or isinstance(evidence.get("marked_positions"), Mapping)) else "pumping"
    instances = evidence.get("word_instances")
    if isinstance(instances, Mapping):
        for key, word in instances.items():
            if isinstance(word, str):
                check_word(word, what=f"word_instances[{key}]")
    agent = "ogden" if method == "ogden" else "pumping_cfl"
    res = verify_pumping_claim(evidence, ir, agent=agent,
                               oracle=_answer(oracle) if oracle is not None else _never)
    bc = (res.get("details") or {}).get("bounded_check") or {}
    trust = _lib_trust(res.get("trust"))
    approximate_refutation = trust == "refuted" and oracle is not None and not oracle.exact
    if approximate_refutation:
        trust = "not_verified"
    data = {
        "mode": "claim", "method": method, "ir_sha256": ir_digest(ir),
        "oracle": _oracle_info(oracle), "oracle_notes": oracle_notes,
        "verification_status": res.get("verification_status"),
        "checks_passed": res.get("checks_passed"), "checks_total": res.get("checks_total"),
        "issues": res.get("issues", []),
        "bounded_check": bc,
        "destructive_witnesses": (res.get("details") or {}).get("destructive_witnesses", []),
    }
    bounds = {"p_values": bc.get("p_values") or [], "i_values": bc.get("i_values") or [0, 2, 3],
              "n_unknown": bc.get("unknown_answers", 0),
              "n_splits": bc.get("splits_checked", 0),
              "splits_closed": bc.get("splits_closed", 0),
              "unresolved_splits": bc.get("unresolved_splits", 0)}
    if trust == "refuted":
        reason = "a concrete premise of the argument is false by the oracle: " + "; ".join(
            i for i in data["issues"] if "NOT in L" in i)[:600]
    elif approximate_refutation:
        reason = (f"the approximate oracle ({oracle.note}) contradicts a premise, which is not a "
                  "refutation; the argument is not verified")
    elif trust == "well_formed":
        reason = ("structure checked" + (f"; instances for p = {bounds['p_values']} "
                                         f"({bc.get('status')})" if bounds["p_values"] else "")
                  + "; finite checks never prove the claim for all p (VERDICT_POLICY §4)")
    else:
        reason = "argument incomplete or not checkable: " + "; ".join(data["issues"][:3])
    if oracle is None:
        reason += "; no membership oracle for this IR, so nothing was checked semantically"
    return ToolResult.success(data, trust=trust, bounds=bounds, reason=reason)


def _pump_split(args: PumpCheckCflArgs, ir: dict[str, Any], oracle: ClassOracle) -> ToolResult:
    s = args.split
    assert s is not None
    z = s.u + s.v + s.w + s.x + s.y
    check_word(z, what="z = uvwxy")
    method = args.method
    if method == "auto":
        method = "ogden" if args.marked_positions is not None else "pumping"
    if not s.v and not s.x:
        raise ToolInputError("|vx| >= 1 is required: v and x are both empty")
    checks: list[str] = []
    p = args.p
    lo, hi = len(s.u), len(s.u) + len(s.v) + len(s.w) + len(s.x)   # vwx = z[lo:hi]
    v_end, x_start = lo + len(s.v), hi - len(s.x)
    if p is not None:
        if len(z) < p:
            raise ToolInputError(f"|z| = {len(z)} < p = {p}: the lemma needs |z| >= p")
        checks.append(f"|z| = {len(z)} >= p = {p}")
    if method == "pumping":
        if args.marked_positions is not None:
            raise ToolInputError("marked_positions are for method ogden")
        if p is not None:
            if hi - lo > p:
                raise ToolInputError(f"|vwx| = {hi - lo} > p = {p}: not a split the pumping "
                                     "lemma allows")
            checks.append(f"|vwx| = {hi - lo} <= p")
        else:
            checks.append("p not given: |vwx| <= p not checked")
    else:
        marked = sorted(set(args.marked_positions or []))
        if not marked:
            raise ToolInputError("Ogden's lemma needs marked_positions")
        if marked[-1] >= len(z):
            raise ToolInputError(f"marked position {marked[-1]} is outside z (|z| = {len(z)})")
        in_vx = [m for m in marked if lo <= m < v_end or x_start <= m < hi]
        in_vwx = [m for m in marked if lo <= m < hi]
        if not in_vx:
            raise ToolInputError("Ogden: vx must contain at least one marked position")
        checks.append(f"vx contains {len(in_vx)} marked position(s)")
        if p is not None:
            if len(marked) < p:
                raise ToolInputError(f"Ogden: {len(marked)} marked position(s) < p = {p}")
            if len(in_vwx) > p:
                raise ToolInputError(f"Ogden: vwx contains {len(in_vwx)} marked positions > p = {p}")
            checks.append(f"{len(marked)} marked >= p; vwx contains {len(in_vwx)} <= p")
        else:
            checks.append("p not given: the marked-position bounds were not checked")

    exps = sorted(set(args.i_values) | set(args.claimed_exit_i or []))
    for i in exps:
        n = len(z) + (i - 1) * (len(s.v) + len(s.x))
        if n > DEFAULT_LIMITS.max_word_len:
            raise InputTooLarge(f"u v^{i} w x^{i} y has length {n}, limit "
                                f"{DEFAULT_LIMITS.max_word_len}")
    ask = _answer(oracle)
    z_in = ask(z)
    pumped = []
    for i in exps:
        word = s.u + s.v * i + s.w + s.x * i + s.y
        pumped.append({"i": i, "word": word, "in_language": ask(word)})
    by_i = {r["i"]: r["in_language"] for r in pumped}
    refutations: list[str] = []
    if z_in is False:
        refutations.append(f"z = {z!r} is NOT in L (oracle): the chosen word is invalid")
    claim_results = []
    for i in sorted(set(args.claimed_exit_i or [])):
        ans = by_i[i]
        status = {True: "contradicted", False: "confirmed", None: "unknown"}[ans]
        claim_results.append({"i": i, "status": status})
        if ans is True:
            refutations.append(f"the argument says u v^{i} w x^{i} y ∉ L, but "
                               f"{pumped[exps.index(i)]['word']!r} IS in L (oracle)")
    answers = [z_in] + [r["in_language"] for r in pumped]
    n_decisive = sum(a is not None for a in answers)
    n_unknown = len(answers) - n_decisive
    data = {
        "mode": "split", "method": method, "ir_sha256": ir_digest(ir),
        "evidence_role": "diagnostic",
        "oracle": _oracle_info(oracle), "z": z, "z_in_language": z_in,
        "split": s.model_dump(), "p": p, "admissibility_checks": checks,
        "pumped": pumped,
        "closed_at": [r["i"] for r in pumped if r["in_language"] is False],
        "survived": [r["i"] for r in pumped if r["in_language"] is True],
        "unknown": [r["i"] for r in pumped if r["in_language"] is None],
        "claim_results": claim_results,
        "refutations": refutations,
    }
    bounds = {"p_values": [p] if p is not None else [], "i_values": exps,
              "n_words": n_decisive, "n_unknown": n_unknown,
              "max_len": max(len(r["word"]) for r in pumped + [{"word": z}])}
    # One split is a diagnostic, never a destructive argument: at most
    # not_verified, plus refuted for a concrete false premise (§4 cfl/pumping).
    diag = "one split is a diagnostic, not a pumping argument (VERDICT_POLICY §4)"
    if refutations and oracle.exact:
        return ToolResult.success(data, trust="refuted", bounds=bounds,
                                  reason="; ".join(refutations))
    if refutations:
        return ToolResult.success(
            data, trust="not_verified", bounds=bounds,
            reason=f"the approximate oracle ({oracle.note}) says: {'; '.join(refutations)} -- not a "
                   f"refutation; {diag}")
    if all(r["in_language"] is None for r in pumped):
        reason = "no pumped word could be decided by the oracle"
        if z_in is not None:
            reason += f" (z in L = {z_in})"
        return ToolResult.success(data, trust="not_verified", bounds=bounds, reason=f"{reason}; {diag}")
    if z_in is None:
        return ToolResult.success(data, trust="not_verified", bounds=bounds,
                                  reason=f"the oracle could not decide z; {diag}")
    parts = []
    if claim_results:
        st = {c["i"]: c["status"] for c in claim_results}
        conf = [i for i, v in st.items() if v == "confirmed"]
        unk = [i for i, v in st.items() if v == "unknown"]
        if conf:
            parts.append(f"claimed exits confirmed at i = {conf}")
        if unk:
            parts.append(f"claimed exits undecided at i = {unk}")
    if data["closed_at"]:
        parts.append(f"closed at i = {data['closed_at']}")
    if data["survived"]:
        parts.append(f"survived i = {data['survived']} (not a refutation: the exit may lie at a larger "
                     "i or in another split)")
    if data["unknown"]:
        parts.append(f"undecided at i = {data['unknown']}")
    return ToolResult.success(
        data, trust="not_verified", bounds=bounds,
        reason=f"z in L; for this one split: {'; '.join(parts)}; {diag}, so it proves nothing about all "
               "splits or all p")


@tfl_tool(group="cfl", max_trust="well_formed", timeout_s=30)
def pump_check_cfl(args: PumpCheckCflArgs) -> ToolResult:
    """Check a pumping-lemma or Ogden argument for non-context-freeness against the IR's
    membership oracle. Mode 'claim': the prover's whole evidence JSON -- the structure is
    checked, and the concrete word_instances for p = 3, 4 are pumped over every admissible
    split with i in {0, 2, 3}; trust at most well_formed (finite checks never prove the claim
    for all p). Mode 'split': one concrete z = u v w x y; the tool checks the split is
    admissible for p, whether z is in L, and pumps u v^i w x^i y for the given i. One split is
    a diagnostic (data.evidence_role 'diagnostic'), never a pumping argument: trust
    not_verified. Both modes: refuted when z is not in L or a word the argument declares
    outside L is in L (exact oracle only). A split that survives the tried i values is not a
    refutation. The oracle comes from the IR (cls, or the first of cfl, dcfl, ll, reg the IR
    is valid for)."""
    ir = dict(args.ir)
    _check_ir(ir)
    oracle, notes = language_oracle(ir, args.cls)
    if args.claim is not None:
        return _pump_claim(args, ir, oracle, notes)
    if oracle is None:
        raise ToolUnsupported("no membership oracle for this IR (" + "; ".join(notes)[:500]
                              + "); a split cannot be checked")
    return _pump_split(args, ir, oracle)


# ---------------------------------------------------------------------------
# grammar_vs_ir (R2: a constructed grammar against the task language)
# ---------------------------------------------------------------------------

GRAMMAR_VS_IR_SAMPLE_MAX_LEN = 30


class GrammarVsIrArgs(ToolArgs):
    grammar: Grammar = Field(description="the constructed grammar G to check against the IR's language")
    ir: dict[str, Any] = Field(description="the task IR; its membership oracle is the reference "
                                           "(results are relative to this IR)")
    cls: TFLClass = Field(default="cfl", description=CLASS_DESCRIPTION)
    sample_max_len: int = Field(
        default=15, ge=0, le=GRAMMAR_VS_IR_SAMPLE_MAX_LEN,
        description="longest word taken from the class's word generator, on top of the full "
                    "enumeration of short words")
    time_budget_s: float = Field(default=40.0, gt=0, le=50,
                                 description="stop comparing after this many seconds (partial result)")


def _cyk_runner(cnf_g: dict[str, Any]) -> Callable[[str], bool | None]:
    def run(word: str) -> bool | None:
        if not cnf_g["rules"]:
            return False
        try:
            return bool(_cyk.cyk_parse(cnf_g, word))
        except Exception:  # noqa: BLE001 - a CYK failure is "unknown" for this word
            return None
    return run


@tfl_tool(group="cfl", max_trust="bounded_pass", timeout_s=60)
def grammar_vs_ir(args: GrammarVsIrArgs) -> ToolResult:
    """Compare a constructed grammar G with the task language (VERDICT_POLICY R2): exact CYK for
    G against the IR's membership oracle on EVERY word over the IR's alphabet up to the length k
    where Σ_{i≤k}|Σ|^i ≤ 3000 (k ≤ 10), then on words from the class's word generator up to
    sample_max_len. Trust: refuted when the oracle (exact) and G disagree on a decided word
    (counterexamples in data); bounded_pass when at least 30 decided words agree (bounds: the
    fully enumerated length, the number of decided / undecided words); well_formed for fewer words
    or an approximate oracle; refuted also for an invalid grammar. A bounded check, never a proof
    that L(G) equals the language. Unsupported when the IR has no oracle."""
    ir = require_valid(args.cls, args.ir)
    oracle = oracle_for(args.cls, ir, validated=True)
    grammar, notes = _grammar_dict(args.grammar)
    _require_single_char_terminals(grammar, "grammar_vs_ir")
    try:
        cnf_g, converted = _cnf_for(grammar)
    except InvalidGrammar as exc:
        return _invalid_grammar_result(exc, grammar)
    outside = sorted(set(grammar["terminals"]) - set(oracle.alphabet))
    if outside:
        notes.append(f"terminals {outside} are not in the IR's alphabet {list(oracle.alphabet)}; words "
                     "are enumerated over the IR's alphabet only")
    extra, gen_notes = generator_candidates(args.cls, ir, oracle.alphabet, max_len=args.sample_max_len)
    cmp = compare_with_oracle(oracle, _cyk_runner(cnf_g), deadline=Deadline(args.time_budget_s),
                              extra_words=extra)
    fp = ir_fingerprint(ir)
    data = {
        "ir_fingerprint": fp, "class": args.cls, "grammar_sha256": ir_digest(grammar),
        "evidence_role": "artifact_check", "oracle": _oracle_info(oracle),
        "converted_to_cnf": converted, "comparison": cmp, "notes": notes + gen_notes,
    }
    return comparison_result(cmp, oracle, data, subject="the grammar", fingerprint=fp)


# ---------------------------------------------------------------------------
# input_grammar_cfg (R2-CFG)
# ---------------------------------------------------------------------------

class InputGrammarCfgArgs(ToolArgs):
    ir: dict[str, Any] = Field(description="a CFL task IR (cfl class)")


@tfl_tool(group="cfl", max_trust="verified", timeout_s=10, allow_refuted=False)
def input_grammar_cfg(args: InputGrammarCfgArgs) -> ToolResult:
    """R2-CFG (VERDICT_POLICY): is the CFL task's language DEFINED by an explicit, valid
    context-free grammar (language_spec kind 'grammar': declared single-character terminals,
    disjoint nonterminals, start among them, one nonterminal on each left-hand side, right-hand
    sides over declared symbols)? Then the language is L(G) by definition and context-free:
    trust verified. Otherwise not_verified. Does NOT apply to grammar_filter (the extra filter can
    destroy context-freeness) or to any other kind."""
    ir = require_valid("cfl", args.ir)
    spec = ir.get("language_spec") if isinstance(ir.get("language_spec"), Mapping) else {}
    kind = spec.get("kind")
    certified = bool(input_grammar_is_cfg(ir))
    fp = ir_fingerprint(ir)
    data = {"ir_fingerprint": fp, "kind": kind, "certified": certified,
            "evidence_role": "input_grammar", "statement": "the task language is context-free"}
    if certified:
        return ToolResult.success(
            data, trust="verified",
            reason=f"R2-CFG: the task language is by definition L(G) of a valid explicit CFG, so it is "
                   f"context-free (IR {fp})")
    if kind == "grammar_filter":
        why = "R2-CFG does not apply to grammar_filter: the filter can destroy context-freeness"
    elif kind == "grammar":
        why = "the IR's grammar is not a valid explicit CFG (symbols, start or rules)"
    else:
        why = f"the language is not given by a grammar (kind {kind!r})"
    return ToolResult.success(data, trust="not_verified", reason=f"{why} (IR {fp})")
