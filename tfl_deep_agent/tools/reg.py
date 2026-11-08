"""REG tool group (P1): DFA construction, DFA runs, Nerode probes, DFA vs IR.

Thin wrappers over ``agent_system.lib`` (inventory:
``tfl_deep_agent/docs/TOOL_INVENTORY.md`` §3). Every tool returns a
:class:`~tfl_deep_agent.tools.base.ToolResult` whose trust is computed here
from what the lib returned (docs/VERDICT_POLICY.md):

* ``regex_to_dfa`` -- ``verified`` for "this DFA recognises exactly L(regex) /
  L(grammar)" (a complete construction by trusted code). That is a statement
  about the given regex or linear grammar, not about the task language.
* ``run_dfa`` -- ``verified`` (an exact run on the given DFA).
* ``nerode_probe`` -- reg/nerode (§4): concrete pairs separated by a found or
  claimed suffix are ``bounded_pass``; a claimed suffix that does not separate
  its pair is ``refuted``; "no suffix up to L" is ``not_verified``; the class
  estimate is a heuristic (``well_formed`` at most). Finitely many pairs never
  prove an infinite index.
* ``dfa_vs_ir`` -- R6: agreement with the IR oracle on every word up to a length
  is ``bounded_pass`` (bounds recorded); a decisive disagreement is ``refuted``.
* ``regex_to_dfa(ir=...)`` -- the task's OWN regex or right-/left-linear grammar,
  read from the IR by the tool: ``verified`` for "the task language is regular"
  (VERDICT_POLICY §1 "регулярность заданного regex/DFA"); ``data.task_definition``
  and ``data.ir_fingerprint`` bind it to that IR, and
  ``data.definition_in_source_text`` says whether the definition occurs in the
  IR's ``source_text``. The verdict gate counts it as ``verified`` only for an IR
  a human approved at HITL 1 whose source text holds the definition; otherwise
  it is a bounded check like ``dfa_vs_ir`` (an IR the model wrote may encode a
  natural-language task as a regex of its own).
* ``pump_check_reg`` -- reg/pumping (§4, R6): a pumping argument for
  non-regularity instantiated at p = 2, 3, 4 (p_min..p_min+2 when p_min > 2) plus
  the extra p_values >= p_min, and checked split by split with the IR's oracle:
  at most ``well_formed``; ``refuted`` for a concrete false premise by an exact
  oracle: |w(p)| < p, w(p) ∉ L, or a split on which x y^i z ∈ L for EVERY i in
  ``exit_i`` (the argument's own claim "some i in exit_i leaves L" is false at
  that p). Unknown answers and approximate oracles never refute. ``exit_i`` is
  fixed for every p and |y|: an exit that depends on them cannot be stated.

The oracle of the IR comes from the shared dispatch
:func:`tfl_deep_agent.tools.words.oracle_for` (inventory §0, §10), which also
validates the IR. An oracle answer ``None`` (unknown) is never "not in L": it is
counted in ``bounds.n_unknown`` and left out of the decisive words. An
approximate oracle (``ClassOracle.exact`` false) gives neither ``bounded_pass``
nor ``refuted``: such results stay ``well_formed``.
"""

from __future__ import annotations

import ast
import re
from collections import deque
from collections.abc import Iterable, Mapping
from itertools import product
from typing import Any, Literal

from pydantic import Field, model_validator

from agent_system.lib import congruence, dfa_builder, dfa_runner, grammar_utils
from agent_system.lib.word_generator import generate_exhaustive

from .base import (
    DEFAULT_LIMITS,
    Alphabet,
    Deadline,
    InputTooLarge,
    ToolArgs,
    ToolInputError,
    ToolResult,
    ToolUnsupported,
    Word,
    WordList,
    check_enumeration,
    check_grammar_size,
    check_word,
    check_words,
    enumeration_size,
    max_len_within,
    tfl_tool,
)
from .ir import CLASS_DESCRIPTION, IR_DESCRIPTION, TFLClass, alphabet_for, ir_fingerprint, require_valid
from .words import ClassOracle, oracle_for, word_alphabet

__all__ = [
    "MAX_DFA_STATES",
    "MAX_PUMP_I",
    "MAX_REGEX_LEN",
    "MIN_DECISIVE_WORDS",
    "DfaSpec",
    "DfaVsIrArgs",
    "GrammarRule",
    "GrammarSpec",
    "NerodeFamily",
    "NerodePair",
    "NerodeProbeArgs",
    "PumpCheckRegArgs",
    "RegexToDfaArgs",
    "RunDfaArgs",
    "dfa_vs_ir",
    "nerode_probe",
    "pump_check_reg",
    "regex_to_dfa",
    "run_dfa",
]

# ---------------------------------------------------------------------------
# Limits (tighter than base.DEFAULT_LIMITS where the REG tools need it)
# ---------------------------------------------------------------------------

#: Largest DFA a tool accepts or returns. A 500-state binary DFA is about 20 KB
#: of JSON, so it still fits the 64 KiB argument limit when passed back in.
MAX_DFA_STATES = 500
MAX_REGEX_LEN = 200
#: dfa_vs_ir: fewer decisive words than this is only well_formed (same floor as
#: the dcfl R2' DPDA check, VERDICT_POLICY §4).
MIN_DECISIVE_WORDS = 30
#: dfa_vs_ir default enumeration budget (|Σ|=2 -> all words up to length 10).
DFA_VS_IR_BUDGET = 4000
DFA_VS_IR_MAX_WORDS = 20_000
DFA_VS_IR_TIMEOUT_S = 60.0
#: nerode_probe: suffixes tried per pair (sum |Σ|^i for i ≤ max_suffix_len).
NERODE_SUFFIX_BUDGET = 4096
NERODE_DEFAULT_SUFFIX_BUDGET = 1024
NERODE_TIMEOUT_S = 30.0
NERODE_MAX_PAIRS = 80
#: The inner deadline stops loops a little before the registry's own timeout,
#: so a long search returns a truncated result instead of a timeout failure.
_DEADLINE_MARGIN_S = 3.0

_CLS_DESCRIPTION = CLASS_DESCRIPTION + " Its membership oracle is the reference."
_IR_DESCRIPTION = (IR_DESCRIPTION + " It must declare its alphabet (single characters). "
                   "Results are relative to this IR (data.ir_fingerprint).")


# ---------------------------------------------------------------------------
# Argument models
# ---------------------------------------------------------------------------

class DfaSpec(ToolArgs):
    """A DFA in the agent_system format. Every symbol is ONE character, because
    words are read character by character. Missing transitions mean an implicit
    rejecting dead state."""

    states: list[str] = Field(min_length=1, max_length=MAX_DFA_STATES,
                              description="state names, e.g. ['q0', 'q1']")
    alphabet: list[str] = Field(max_length=16,
                                description="input symbols, each a single character")
    transitions: dict[str, dict[str, str]] = Field(
        description="transitions[state][symbol] = next state")
    start: str = Field(description="start state (one of states)")
    accept: list[str] = Field(description="accepting states (subset of states)")


class GrammarRule(ToolArgs):
    lhs: str = Field(min_length=1, description="one nonterminal")
    rhs: list[str] = Field(max_length=24,
                           description="symbols of the right-hand side; [] is ε")


class GrammarSpec(ToolArgs):
    """A grammar in the shared format (terminals and nonterminals disjoint)."""

    kind: Literal["grammar"] | None = Field(default=None, description="optional, ignored")
    terminals: list[str] = Field(min_length=1, max_length=16,
                                 description="terminal symbols, each a single character")
    nonterminals: list[str] = Field(min_length=1, max_length=120)
    start: str
    rules: list[GrammarRule] = Field(min_length=1, max_length=300)


class RegexToDfaArgs(ToolArgs):
    regex: str | None = Field(
        default=None, max_length=MAX_REGEX_LEN,
        description=(
            "Regular expression. Syntax: single-character literals, '|' union, '*' star, "
            "'+' one or more, '?' optional, '(...)' grouping, '\\\\x' escapes x. '()' is ε. "
            "No character classes [..], no '.', no {m,n}, no back-references, no spaces, "
            "no 'ε' symbol."))
    grammar: GrammarSpec | None = Field(
        default=None,
        description="A right-linear or left-linear grammar instead of a regex.")
    ir: dict[str, Any] | None = Field(
        default=None,
        description=("The task IR (class reg) when the task language IS a regex "
                     "(language_spec.kind 'regex') or a right-/left-linear grammar (kind "
                     "'grammar'). The tool reads that definition from the IR itself, so the result "
                     "is about the task language: data.task_definition is true and "
                     "data.ir_fingerprint names the IR. Pass the IR unchanged. Default alphabet: "
                     "the IR's alphabet."))
    alphabet: Alphabet | None = Field(
        default=None,
        description=("Alphabet to build the DFA over (default: the symbols that occur). "
                     "Symbols that do not occur go to a rejecting dead state, so the DFA "
                     "is complete over this alphabet; needed before complementing."))

    @model_validator(mode="after")
    def _one_source(self) -> RegexToDfaArgs:
        if sum(x is not None for x in (self.regex, self.grammar, self.ir)) != 1:
            raise ValueError("give exactly one of regex, grammar or ir")
        return self


class RunDfaArgs(ToolArgs):
    dfa: DfaSpec
    words: WordList = Field(min_length=1, description="words to run; '' is ε")


class NerodePair(ToolArgs):
    u: Word = Field(description="first prefix")
    v: Word = Field(description="second prefix")
    suffix: Word | None = Field(
        default=None,
        description=("Claimed distinguishing suffix z (exactly one of uz, vz in L). Omit it "
                     "to let the tool search for the shortest one."))


class NerodeFamily(ToolArgs):
    prefix: str = Field(
        min_length=1, max_length=80,
        description=("Word pattern of the family w_n, e.g. 'a^n', 'a^(2n+1) b', '(ab)^n c'. "
                     "Blocks: a symbol or a parenthesised group of symbols, optionally "
                     "'^' exponent; an exponent is an integer formula in ONE variable with "
                     "+, -, *, implicit multiplication and a constant power ('a^2n', 'b^(n+1)', "
                     "'c^{3n-1}', 'a^(n^2)')."))
    suffix: str | None = Field(
        default=None, max_length=80,
        description=("Pattern of the claimed separating suffix z_n in the same variable, "
                     "e.g. 'b^n'. For a pair i < j the tool checks z at the value given by "
                     "suffix_at. Omit it to let the tool search a suffix for every pair."))
    values: list[int] = Field(
        min_length=2, max_length=12,
        description="values of the variable to instantiate (0..50); all pairs i < j are checked")
    suffix_at: Literal["smaller", "larger"] = Field(
        default="smaller",
        description="instantiate the suffix at the smaller (default) or larger value of the pair")

    @model_validator(mode="after")
    def _values_ok(self) -> NerodeFamily:
        if any(x < 0 or x > 50 for x in self.values):
            raise ValueError("values must be in 0..50")
        if len(set(self.values)) < 2:
            raise ValueError("values need at least two distinct numbers")
        return self


class NerodeProbeArgs(ToolArgs):
    cls: TFLClass = Field(default="reg", description=_CLS_DESCRIPTION)
    ir: dict[str, Any] = Field(description=_IR_DESCRIPTION)
    pairs: list[NerodePair] = Field(
        default_factory=list, max_length=NERODE_MAX_PAIRS,
        description="explicit prefix pairs, each with an optional claimed suffix")
    family: NerodeFamily | None = Field(
        default=None,
        description="a parametrised family claimed to be pairwise Nerode-distinguishable")
    max_suffix_len: int | None = Field(
        default=None, ge=0, le=12,
        description=("longest suffix tried when searching (default: about 1000 suffixes, "
                     "e.g. 9 for |Σ|=2); at most 4096 suffixes per pair"))
    estimate_depth: int | None = Field(
        default=None, ge=1, le=6,
        description=("also run the heuristic class estimate over all prefixes up to this "
                     "length (congruence.compute_nerode_classes). A heuristic, never a proof."))

    @model_validator(mode="after")
    def _something_to_do(self) -> NerodeProbeArgs:
        if not self.pairs and self.family is None and self.estimate_depth is None:
            raise ValueError("give pairs, a family, or estimate_depth")
        return self


class DfaVsIrArgs(ToolArgs):
    cls: TFLClass = Field(default="reg", description=_CLS_DESCRIPTION)
    ir: dict[str, Any] = Field(description=_IR_DESCRIPTION)
    dfa: DfaSpec
    max_len: int | None = Field(
        default=None, ge=0, le=40,
        description=("check every word up to this length (default: as long as about 4000 "
                     "words allow, e.g. 10 for |Σ|=2, 7 for |Σ|=3; 40 for |Σ|=1); "
                     "at most 20000 words"))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _OracleUnknown(Exception):
    """Raised by a strict oracle view on an unknown answer (estimate mode)."""


class _Stop(Exception):
    """Raised by a strict oracle view when the deadline has passed."""


class _CachedOracle:
    """Memoised view of a :class:`ClassOracle`: True / False / None.

    ``ClassOracle.query`` already turns a raising oracle (length limit, CYK
    guard, step budget) into None -- unknown, never "not in L"; the first few
    reasons are kept for the result."""

    def __init__(self, oracle: ClassOracle) -> None:
        self.oracle = oracle
        self.cache: dict[str, bool | None] = {}
        self.notes: dict[str, str] = {}

    def __call__(self, word: str) -> bool | None:
        if word in self.cache:
            return self.cache[word]
        value, note = self.oracle.query(word)
        if value is None and note and len(self.notes) < 5:
            self.notes[word] = note
        self.cache[word] = value
        return value

    def stats(self) -> tuple[int, int, int]:
        """(decisive words, unknown words, longest word asked)."""
        decisive = sum(1 for v in self.cache.values() if v is not None)
        unknown = len(self.cache) - decisive
        longest = max((len(w) for w in self.cache), default=0)
        return decisive, unknown, longest


def _dfa_problems(dfa: DfaSpec) -> tuple[list[str], list[str]]:
    """(errors, partial-transition notes). Missing transitions are allowed:
    they reject (implicit dead state), exactly as dfa_runner.run_dfa does."""
    errors: list[str] = []
    partial: list[str] = []
    states = set(dfa.states)
    if len(states) != len(dfa.states):
        errors.append("states has duplicates")
    alphabet = set(dfa.alphabet)
    if len(alphabet) != len(dfa.alphabet):
        errors.append("alphabet has duplicates")
    long = [s for s in dfa.alphabet if len(s) != 1]
    if long:
        errors.append(f"alphabet symbols must be single characters, got {long}")
    for state, row in dfa.transitions.items():
        if state not in states:
            errors.append(f"transitions has unknown state {state!r}")
        for sym in row:
            if sym not in alphabet:
                errors.append(f"transition ({state}, {sym!r}) uses a symbol outside the alphabet")
    for msg in dfa_runner.validate_dfa(dfa.model_dump()):
        if msg.startswith(("Missing transition", "No transitions defined")):
            partial.append(msg)
        else:
            errors.append(msg)
    return errors, partial


def _require_valid_dfa(dfa: DfaSpec) -> list[str]:
    errors, partial = _dfa_problems(dfa)
    if errors:
        raise ToolInputError("invalid DFA: " + "; ".join(errors[:10]))
    return partial


def _final_state(dfa: dict[str, Any], word: str) -> str | None:
    state = dfa["start"]
    for ch in word:
        state = dfa["transitions"].get(state, {}).get(ch)
        if state is None:
            return None
    return state


def _complete(dfa: dict[str, Any], alphabet: list[str]) -> dict[str, Any]:
    """The same language over *alphabet*, with every transition defined (missing
    ones go to a rejecting dead state). minimize_dfa needs a complete DFA."""
    symbols = list(dict.fromkeys([*dfa["alphabet"], *alphabet]))
    states = list(dfa["states"])
    trans = {q: dict(dfa["transitions"].get(q, {})) for q in states}
    missing = any(sym not in trans[q] for q in states for sym in symbols)
    if missing:
        sink = "q_dead"
        while sink in trans:
            sink += "_"
        states.append(sink)
        trans[sink] = {}
        for q in states:
            for sym in symbols:
                trans[q].setdefault(sym, sink)
    return {"states": states, "alphabet": symbols, "transitions": trans,
            "start": dfa["start"], "accept": list(dfa["accept"])}


def _shortest_accepted(dfa: dict[str, Any]) -> str | None:
    seen = {dfa["start"]: ""}
    queue = deque([dfa["start"]])
    accept = set(dfa["accept"])
    while queue:
        q = queue.popleft()
        if q in accept:
            return seen[q]
        for sym in dfa["alphabet"]:
            nxt = dfa["transitions"].get(q, {}).get(sym)
            if nxt is not None and nxt not in seen:
                seen[nxt] = seen[q] + sym
                queue.append(nxt)
    return None


# ---------------------------------------------------------------------------
# regex_to_dfa
# ---------------------------------------------------------------------------

_REGEX_META = set("|*+?()\\")
_REGEX_REJECT = {
    ".": "'.' (any symbol) is not supported; write the union, e.g. (a|b)",
    "[": "character classes are not supported; write the union, e.g. (a|b)",
    "]": "character classes are not supported; write the union, e.g. (a|b)",
    "{": "counted repetition {m,n} is not supported; repeat the block explicitly",
    "}": "counted repetition {m,n} is not supported; repeat the block explicitly",
    "^": "anchors are not supported (the whole word is matched)",
    "$": "anchors are not supported (the whole word is matched)",
    "ε": "write the empty word as ()",
    "λ": "write the empty word as ()",
    "∅": "the empty language has no regex here; use a DFA with no accepting state",
}


def _regex_literals(regex: str) -> list[str]:
    """Literal symbols of *regex*; raises ToolInputError on syntax the lib would
    silently misread (it treats '.', '[' ... as literal characters)."""
    literals: list[str] = []
    i = 0
    while i < len(regex):
        ch = regex[i]
        if ch == "\\":
            if i + 1 >= len(regex):
                raise ToolInputError("regex ends with a lone backslash")
            nxt = regex[i + 1]
            if nxt.isdigit():
                raise ToolInputError("back-references (\\1, \\2, ...) are not regular syntax "
                                     "and are not supported")
            literals.append(nxt)
            i += 2
            continue
        if ch.isspace():
            raise ToolInputError("the regex contains whitespace; spaces are not symbols here")
        if ch in _REGEX_REJECT:
            raise ToolInputError(f"unsupported regex syntax {ch!r}: {_REGEX_REJECT[ch]}")
        if ch not in _REGEX_META:
            literals.append(ch)
        i += 1
    return literals


@tfl_tool(group="reg", max_trust="verified", timeout_s=30, timeout_mode="process")
def regex_to_dfa(args: RegexToDfaArgs) -> ToolResult:
    """Build the minimal complete DFA of a regular expression, or of a right-/left-linear grammar.

    Use it to get an exact automaton for a regex or linear grammar you wrote, then check it
    against the task with dfa_vs_ir, or run words with run_dfa. When the task language IS a
    regex or a right-/left-linear grammar, pass the task IR as `ir` instead: the tool then
    compiles the IR's own definition. The DFA is returned in the format the other REG tools
    take; every symbol is one character.

    Trust: verified for "this DFA recognises exactly L(regex) / L(grammar)" (a complete
    construction: Thompson, subset construction, Hopcroft minimisation). With `regex` or
    `grammar` that is a statement about the expression you passed, not about the task language:
    tie it to the task with dfa_vs_ir. With `ir` the expression is the task's own definition,
    so the result says the task language is regular (VERDICT_POLICY §1 "регулярность заданного
    regex/DFA"): data.task_definition is true, data.ir_fingerprint names the IR, and
    data.definition_in_source_text says whether the regex / the grammar's rules occur in the IR's
    source_text. The verdict gate counts it as the verified basis of "regular" only when a human
    approved that IR at HITL 1 and the source text holds the definition; otherwise (HITL 1 off or
    auto-approved, or a natural-language task re-encoded as a regex) it counts like dfa_vs_ir,
    bounded_pass. Before answering, the tool
    also compares the DFA with the IR's oracle on all short words (data.oracle_cross_check); a
    disagreement is an error, not a result. A regex IR with back-references or syntax the
    construction does not take, and a grammar that is neither right- nor left-linear, are
    `unsupported` (that alone does not make the language non-regular). n_states is the number
    of states of the minimal complete DFA over the alphabet used. Limits: regex <= 200 chars,
    result <= 500 states, 30 s.
    """
    task_ir: dict[str, Any] | None = None
    regex, grammar = args.regex, args.grammar.model_dump(exclude_none=True) if args.grammar else None
    requested_alphabet = list(args.alphabet) if args.alphabet is not None else None
    if args.ir is not None:
        task_ir, regex, grammar, ir_alphabet = _task_definition(args.ir)
        if requested_alphabet is None:
            requested_alphabet = ir_alphabet
    if regex is not None:
        try:
            literals = _regex_literals(regex)
        except ToolInputError as exc:
            if task_ir is None:
                raise
            raise ToolUnsupported(
                f"the task's regex uses syntax the DFA construction does not take ({exc}); write an "
                "equivalent regex in the tool syntax and check it against the IR with dfa_vs_ir "
                "(that alone does not make the language non-regular)") from None
        if requested_alphabet is not None:
            outside = sorted(set(literals) - set(requested_alphabet))
            if outside:
                raise ToolInputError(f"regex uses symbols outside the alphabet: {outside}")
        try:
            raw = dfa_builder.build_dfa_from_regex(regex)
        except ValueError as exc:
            if task_ir is not None:
                raise ToolUnsupported(f"the task's regex does not parse in the tool syntax: {exc}") from None
            raise ToolInputError(f"regex does not parse: {exc}") from None
        source = {"regex": regex}
    else:
        assert grammar is not None
        g = dict(grammar)
        g.pop("kind", None)
        g["rules"] = [{"lhs": r["lhs"], "rhs": list(r["rhs"])} for r in g["rules"]]
        check_grammar_size(g)
        _check_grammar_symbols(g)
        if requested_alphabet is not None:
            outside = sorted(set(g["terminals"]) - set(requested_alphabet))
            if outside:
                raise ToolInputError(f"grammar terminals outside the alphabet: {outside}")
        right = grammar_utils.is_right_linear(g)
        left = grammar_utils.is_left_linear(g)
        if not (right or left):
            raise ToolUnsupported(
                "the grammar is neither right-linear (A -> w B | w) nor left-linear "
                "(A -> B w | w); no DFA construction applies (that alone does not make "
                "its language non-regular)")
        raw = grammar_utils.grammar_to_dfa(g)
        if raw is None:
            raise ToolUnsupported("grammar_to_dfa could not build a DFA for this grammar")
        source = {"grammar": "right-linear" if right else "left-linear"}

    alphabet = requested_alphabet if requested_alphabet is not None else list(raw.get("alphabet") or [])
    if not alphabet:
        # The regex has no literal (ε or ∅ only): there is no alphabet to complete over.
        dfa = raw
    else:
        dfa = dfa_builder.minimize_dfa(_complete(raw, alphabet))
    n_states = len(dfa["states"])
    if n_states > MAX_DFA_STATES:
        raise InputTooLarge(
            f"the minimal DFA has {n_states} states, limit {MAX_DFA_STATES}; "
            "split the regex or reason about it by hand")
    shortest = _shortest_accepted(dfa)
    data = {
        **source,
        "source": "ir.language_spec" if task_ir is not None else ("regex" if regex is not None else "grammar"),
        "task_definition": task_ir is not None,
        "dfa": dfa,
        "n_states": n_states,
        "alphabet": dfa["alphabet"],
        "minimal_complete": bool(alphabet),
        "accepts_empty_word": dfa["start"] in set(dfa["accept"]),
        "language_is_empty": shortest is None,
        "shortest_accepted": shortest,
    }
    what = "L(regex)" if regex is not None else "L(grammar)"
    if task_ir is None:
        return ToolResult.success(
            data, trust="verified",
            reason=(f"exact construction: the DFA recognises exactly {what}; this is about the "
                    "given expression, not the task language (use dfa_vs_ir for that)"))
    fp = ir_fingerprint(task_ir)
    data["ir_fingerprint"] = fp
    data["cls"] = "reg"
    data["oracle_cross_check"] = _cross_check_with_ir(dfa, task_ir)
    data["definition_in_source_text"] = _definition_in_source_text(task_ir, regex, grammar)
    kind = "regex" if regex is not None else f"{source['grammar']} grammar"
    return ToolResult.success(
        data, trust="verified",
        reason=(f"exact construction from the task's own definition (IR {fp}, {kind}): the DFA "
                "recognises exactly the task language, so the task language is regular "
                "(VERDICT_POLICY §1: регулярность заданного regex/DFA)"))


#: regex_to_dfa(ir=...): words compared with the IR's oracle before answering.
TASK_DFA_CROSS_CHECK_WORDS = 2000


def _task_definition(ir_in: dict[str, Any]) -> tuple[dict[str, Any], str | None, dict[str, Any] | None,
                                                      list[str] | None]:
    """``(ir, regex, grammar, alphabet)`` of a reg IR whose language_spec IS a
    regular definition: a regex without back-references, or a grammar (the
    caller checks that it is right- or left-linear)."""
    ir = require_valid("reg", ir_in)
    spec = ir.get("language_spec")
    if not isinstance(spec, dict):
        raise ToolUnsupported("the IR has no language_spec, so there is no definition to compile")
    kind = spec.get("kind")
    declared = alphabet_for("reg", ir) or None
    if kind == "regex":
        if spec.get("has_backreferences"):
            raise ToolUnsupported(
                "the task's regex is declared with back-references: it is not regular syntax, and that "
                "alone decides nothing ((a)\\1* = aa*); re-encode the language or argue by hand")
        pattern = spec.get("pattern")
        if not isinstance(pattern, str):
            raise ToolInputError("language_spec.pattern must be a string")
        if len(pattern) > MAX_REGEX_LEN:
            raise InputTooLarge(f"the task's regex has {len(pattern)} characters, limit {MAX_REGEX_LEN}")
        return ir, pattern, None, declared
    if kind == "grammar":
        grammar = {k: spec.get(k) for k in ("terminals", "nonterminals", "start", "rules")}
        try:
            grammar = GrammarSpec.model_validate(grammar).model_dump(exclude_none=True)
        except ValueError as exc:
            raise ToolInputError(f"the IR's grammar does not fit the tool's grammar format: {exc}") from None
        return ir, None, grammar, declared
    raise ToolUnsupported(
        f"the IR's language_spec.kind is {kind!r}; only a regex (kind 'regex') or a right-/left-linear "
        "grammar (kind 'grammar') is a regular definition this tool can compile")


_ARROWS = ("::=", "->", "=>", "→", "⟶", "⇒")


def _definition_in_source_text(ir: Mapping[str, Any], regex: str | None,
                               grammar: Mapping[str, Any] | None) -> bool:
    """Whether the IR's definition occurs in its ``source_text`` (whitespace
    ignored): the whole regex as one substring, or, for a grammar, every rule's
    left side and every non-empty right side. A regex the model wrote for a
    natural-language task ({w | #a(w) = #b(w)} encoded as (a|b)*) fails this.
    It is a necessary condition only; the verdict gate also needs a human
    approval at HITL 1 (``agent._classify``)."""
    text = ir.get("source_text")
    if not isinstance(text, str) or not text.strip():
        return False
    norm = re.sub(r"\s+", "", text)
    if regex is not None:
        pattern = re.sub(r"\s+", "", regex)
        return bool(pattern) and pattern in norm
    if grammar is None:
        return False
    for arrow in _ARROWS:
        norm = norm.replace(arrow, "→")
    for rule in grammar.get("rules") or []:
        lhs, rhs = str(rule.get("lhs")), "".join(str(s) for s in rule.get("rhs") or [])
        if lhs not in norm or (rhs and rhs not in norm):
            return False
    return True


def _cross_check_with_ir(dfa: dict[str, Any], ir: dict[str, Any]) -> dict[str, Any]:
    """Compare the DFA with the IR's own oracle on every word up to a length.

    The regex oracle is a Python full match and the DFA comes from
    dfa_builder's own parser; a disagreement means the two read the pattern
    differently, so no ``verified`` result is given."""
    class_oracle = oracle_for("reg", ir, validated=True)
    alphabet = list(class_oracle.alphabet)
    max_len = max_len_within(len(alphabet), TASK_DFA_CROSS_CHECK_WORDS, 10 if len(alphabet) > 1 else 30)
    decisive = unknown = 0
    for w in generate_exhaustive(alphabet, max_len):
        says, _note = class_oracle.query(w)
        if says is None:
            unknown += 1
            continue
        decisive += 1
        if dfa_runner.run_dfa(dfa, w) != says:
            raise ToolUnsupported(
                f"the DFA built from the task's definition and the IR's oracle ({class_oracle.source}) "
                f"disagree on {w!r} (oracle: {'in' if says else 'not in'} L): they read the definition "
                "differently, so no result is given; write the regex in the tool syntax and use dfa_vs_ir")
    return {"max_len": max_len, "n_words": decisive, "n_unknown": unknown, "oracle": class_oracle.source}


def _check_grammar_symbols(g: dict[str, Any]) -> None:
    terms, nts = g["terminals"], g["nonterminals"]
    if set(terms) & set(nts):
        raise ToolInputError(f"terminals and nonterminals overlap: {sorted(set(terms) & set(nts))}")
    long = [t for t in terms if len(t) != 1]
    if long:
        raise ToolInputError(f"terminals must be single characters for a DFA, got {long}")
    if g["start"] not in nts:
        raise ToolInputError(f"start symbol {g['start']!r} is not a nonterminal")
    known = set(terms) | set(nts)
    for i, r in enumerate(g["rules"]):
        if r["lhs"] not in nts:
            raise ToolInputError(f"rules[{i}].lhs {r['lhs']!r} is not a nonterminal")
        bad = [s for s in r["rhs"] if s not in known]
        if bad:
            raise ToolInputError(f"rules[{i}].rhs uses unknown symbols {bad} (ε is rhs: [])")


# ---------------------------------------------------------------------------
# run_dfa
# ---------------------------------------------------------------------------

@tfl_tool(group="reg", max_trust="verified", timeout_s=10)
def run_dfa(args: RunDfaArgs) -> ToolResult:
    """Run words through a DFA you give and report, per word, accepted or not and the final state.

    Exact: the answer is about THIS DFA (trust verified), not about the task language. A missing
    transition rejects (implicit dead state); such DFAs are flagged as partial. Words with
    symbols outside the DFA alphabet are rejected and listed in foreign_symbol_words.
    Up to 500 words of length <= 100.
    """
    partial = _require_valid_dfa(args.dfa)
    words = check_words(args.words)
    dfa = args.dfa.model_dump()
    alphabet = set(dfa["alphabet"])
    results = []
    foreign = []
    for w in words:
        accepted = dfa_runner.run_dfa(dfa, w)
        results.append({"word": w, "accepted": accepted, "final_state": _final_state(dfa, w)})
        if any(ch not in alphabet for ch in w):
            foreign.append(w)
    data: dict[str, Any] = {
        "results": results,
        "accepted": [r["word"] for r in results if r["accepted"]],
        "rejected": [r["word"] for r in results if not r["accepted"]],
        "partial_dfa": bool(partial),
    }
    if partial:
        data["partial_notes"] = partial[:20]
    if foreign:
        data["foreign_symbol_words"] = foreign
    return ToolResult.success(data, trust="verified",
                              reason="exact run of each word on the given DFA")


# ---------------------------------------------------------------------------
# Word patterns for nerode_probe families
# ---------------------------------------------------------------------------

_EXP_BARE_RE = re.compile(r"\d+[A-Za-z]?|[A-Za-z]")
_MAX_PATTERN_COUNT = 200


def _parse_exponent(text: str, pos: int) -> tuple[str, int]:
    """Exponent text after '^' starting at *pos*: '{...}', '(...)' or a bare token."""
    if pos >= len(text):
        raise ToolInputError("pattern ends with '^'")
    opener = text[pos]
    if opener in "({":
        closer = ")" if opener == "(" else "}"
        depth = 0
        for k in range(pos, len(text)):
            if text[k] == opener:
                depth += 1
            elif text[k] == closer:
                depth -= 1
                if depth == 0:
                    return text[pos + 1:k], k + 1
        raise ToolInputError(f"unbalanced {opener!r} in an exponent")
    m = _EXP_BARE_RE.match(text, pos)
    if not m:
        raise ToolInputError(f"cannot read the exponent at {text[pos:pos + 10]!r}; "
                             "use parentheses, e.g. a^(n+1)")
    return m.group(0), m.end()


def _exponent_vars(expr: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_]\w*", re.sub(r"(\d)([A-Za-z])", r"\1*\2", expr)))


def _eval_exponent(expr: str, var: str | None, value: int) -> int:
    """Evaluate +, -, *, integers, the one variable and a power with a constant
    exponent 0..3 ('n^2' or 'n**2', e.g. a^(p^2)) -- no other powers, no names
    other than *var*, every intermediate value bounded (no eval())."""
    src = re.sub(r"(\d)([A-Za-z(])", r"\1*\2", expr.strip())
    src = re.sub(r"\)\s*([A-Za-z0-9(])", r")*\1", src)
    src = src.replace("**", "^").replace("^", "**")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise ToolInputError(f"exponent {expr!r} is not an integer formula") from None

    def ev(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            if node.value > 10_000:
                raise ToolInputError(f"constant {node.value} in an exponent is too large")
            return node.value
        if isinstance(node, ast.Name):
            if node.id != var:
                raise ToolInputError(f"exponent {expr!r} uses {node.id!r}; only one variable is allowed")
            return value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            a, b = ev(node.left), ev(node.right)
            out = a + b if isinstance(node.op, ast.Add) else a - b if isinstance(node.op, ast.Sub) else a * b
            if abs(out) > 1_000_000:
                raise InputTooLarge(f"exponent {expr!r} grows too large")
            return out
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            power = node.right
            if not (isinstance(power, ast.Constant) and type(power.value) is int and 0 <= power.value <= 3):
                raise ToolInputError(f"exponent {expr!r}: a power needs a constant exponent 0..3, "
                                     "e.g. n^2")
            out = ev(node.left) ** power.value
            if abs(out) > 1_000_000:
                raise InputTooLarge(f"exponent {expr!r} grows too large")
            return out
        raise ToolInputError(f"exponent {expr!r}: only +, -, *, a constant power (n^2), integers and "
                             "one variable are allowed")

    return ev(tree)


def _parse_pattern(pattern: str, alphabet: list[str]) -> tuple[list[tuple[str, str | None]], set[str]]:
    """Blocks (base word, exponent text or None) and the exponent variables."""
    allowed = set(alphabet)
    blocks: list[tuple[str, str | None]] = []
    variables: set[str] = set()
    text = pattern
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace() or ch in "·⋅":
            i += 1
            continue
        if ch == "(" and ch not in allowed:
            close = text.find(")", i)
            if close < 0:
                raise ToolInputError(f"unbalanced '(' in pattern {pattern!r}")
            base = "".join(c for c in text[i + 1:close] if not c.isspace())
            i = close + 1
        elif ch in allowed:
            base = ch
            i += 1
        else:
            raise ToolInputError(
                f"pattern {pattern!r}: {ch!r} is not an alphabet symbol (alphabet {alphabet})")
        bad = sorted({c for c in base if c not in allowed})
        if bad or not base:
            raise ToolInputError(f"pattern {pattern!r}: group {base!r} must be alphabet symbols")
        exponent = None
        if i < len(text) and text[i] == "^":
            exponent, i = _parse_exponent(text, i + 1)
            variables |= _exponent_vars(exponent)
        blocks.append((base, exponent))
    if not blocks:
        raise ToolInputError(f"pattern {pattern!r} is empty")
    return blocks, variables


def _instantiate(blocks: list[tuple[str, str | None]], var: str | None, value: int,
                 max_len: int) -> str:
    out: list[str] = []
    total = 0
    for base, exponent in blocks:
        count = 1 if exponent is None else _eval_exponent(exponent, var, value)
        if count < 0:
            raise ToolInputError(f"exponent {exponent!r} is negative at {var}={value}")
        total += len(base) * count
        if total > max_len:
            raise InputTooLarge(f"the instance at {var}={value} is longer than {max_len}")
        out.append(base * count)
    return "".join(out)


# ---------------------------------------------------------------------------
# nerode_probe
# ---------------------------------------------------------------------------

def _all_words(alphabet: list[str], max_len: int) -> Iterable[str]:
    for n in range(max_len + 1):
        for combo in product(alphabet, repeat=n):
            yield "".join(combo)


def _search_suffix(oracle: _CachedOracle, u: str, v: str, alphabet: list[str], max_len: int,
                   deadline: Deadline, word_limit: int) -> dict[str, Any]:
    """Shortest z with exactly one of uz, vz decisively in L (None-aware)."""
    unknown = 0
    tried = 0
    for z in _all_words(alphabet, max_len):
        if len(u) + len(z) > word_limit or len(v) + len(z) > word_limit:
            break
        if deadline.expired():
            return {"found": None, "unknown": unknown, "tried": tried, "truncated": True}
        tried += 1
        a, b = oracle(u + z), oracle(v + z)
        if a is None or b is None:
            unknown += 1
            continue
        if a != b:
            return {"found": z, "u_in_L": a, "v_in_L": b, "unknown": unknown, "tried": tried,
                    "truncated": False}
    return {"found": None, "unknown": unknown, "tried": tried, "truncated": False}


def _probe_pair(oracle: _CachedOracle, u: str, v: str, claimed: str | None, alphabet: list[str],
                max_suffix_len: int, deadline: Deadline, word_limit: int) -> dict[str, Any]:
    rec: dict[str, Any] = {"u": u, "v": v}
    if u == v:
        rec.update(status="same_word", note="u = v: equal prefixes are never distinguishable")
        if claimed is not None:
            rec.update(status="claimed_suffix_fails", suffix=claimed)
        return rec
    if claimed is not None:
        a, b = oracle(u + claimed), oracle(v + claimed)
        rec.update(suffix=claimed, u_suffix_in_L=a, v_suffix_in_L=b)
        if a is None or b is None:
            rec["status"] = "unknown"
            rec["note"] = "the oracle could not decide u·z or v·z"
            return rec
        if a != b:
            rec["status"] = "claimed_suffix_confirmed"
            return rec
        rec["status"] = "claimed_suffix_fails"
        rec["note"] = f"both u·z and v·z are {'in' if a else 'not in'} L"
    found = _search_suffix(oracle, u, v, alphabet, max_suffix_len, deadline, word_limit)
    rec["searched_up_to"] = max_suffix_len
    rec["suffixes_tried"] = found["tried"]
    if found["unknown"]:
        rec["suffixes_unknown"] = found["unknown"]
    if found.get("truncated"):
        rec["search_truncated"] = True
    if claimed is not None:
        # The claim stays refuted; a real separator (if any) helps fix the proof.
        rec["alternative_suffix"] = found["found"]
        return rec
    if found["found"] is not None:
        rec.update(status="distinguished", suffix=found["found"],
                   u_suffix_in_L=found["u_in_L"], v_suffix_in_L=found["v_in_L"])
    elif found.get("truncated"):
        rec["status"] = "unknown"
        rec["note"] = "the time limit cut the search short"
    elif found["unknown"]:
        rec["status"] = "unknown"
        rec["note"] = "no separating suffix among the decided ones; some answers were unknown"
    else:
        rec["status"] = "no_suffix_found"
        rec["note"] = (f"no suffix of length <= {max_suffix_len} separates u and v; this does "
                       "NOT prove u and v are Nerode-equivalent")
    return rec


def _family_pairs(family: NerodeFamily, alphabet: list[str],
                  word_limit: int) -> tuple[list[tuple[str, str, str | None, int, int]], str | None]:
    blocks, pvars = _parse_pattern(family.prefix, alphabet)
    sblocks, svars = (None, set()) if family.suffix is None else _parse_pattern(family.suffix, alphabet)
    variables = pvars | svars
    if len(variables) > 1:
        raise ToolInputError(f"the family uses several variables {sorted(variables)}; use one")
    var = next(iter(variables)) if variables else None
    if var is None or var not in pvars:
        raise ToolInputError(f"the prefix pattern {family.prefix!r} must depend on the variable")
    values = sorted(set(family.values))
    words = {n: _instantiate(blocks, var, n, word_limit) for n in values}
    out = []
    for x, i in enumerate(values):
        for j in values[x + 1:]:
            suffix = None
            if sblocks is not None:
                at = i if family.suffix_at == "smaller" else j
                suffix = _instantiate(sblocks, var, at, word_limit)
            out.append((words[i], words[j], suffix, i, j))
    return out, var


def _estimate(oracle: _CachedOracle, alphabet: list[str], depth: int,
              deadline: Deadline) -> dict[str, Any]:
    """congruence.compute_nerode_classes over a strict view of the oracle: an
    unknown answer or the deadline aborts the estimate (it cannot treat None)."""
    check_enumeration(len(alphabet), depth, limit=2000)

    def strict(word: str) -> bool:
        if deadline.expired():
            raise _Stop()
        value = oracle(word)
        if value is None:
            raise _OracleUnknown(word)
        return value

    try:
        res = congruence.compute_nerode_classes(strict, alphabet, max_depth=depth)
    except _Stop:
        return {"status": "timeout", "depth": depth}
    except _OracleUnknown as exc:
        return {"status": "oracle_unknown", "depth": depth, "word": str(exc.args[0])}
    classes = res["classes"]
    return {
        "status": "ok",
        "depth": depth,
        "context_len": min(depth, 6),
        "classes_found": res["num_classes"],
        "growth_pattern": res["growth_pattern"],
        "looks_infinite": res["likely_infinite"],
        "representatives": [c["representative"] for c in classes[:40]],
        "scope": ("heuristic: prefixes up to the depth, compared on suffixes up to context_len; "
                  "neither a lower nor an upper bound on the Nerode index is proved"),
    }


@tfl_tool(group="reg", max_trust="bounded_pass", timeout_s=NERODE_TIMEOUT_S)
def nerode_probe(args: NerodeProbeArgs) -> ToolResult:
    """Check Myhill-Nerode distinguishability of prefixes with the task's membership oracle.

    Three modes, combinable:
    * pairs: for each {u, v, suffix?}, confirm that the claimed suffix z separates u and v
      (exactly one of uz, vz in L), or, without a suffix, search the shortest one;
    * family: a pattern such as prefix 'a^n' with suffix 'b^n' at values [1..6]; every pair
      i < j is checked the same way (the usual "pairwise distinguishable infinite family"
      argument, instantiated at finitely many values);
    * estimate_depth: a heuristic count of classes over short prefixes.

    Trust (VERDICT_POLICY §4 reg/nerode): every requested pair separated -> bounded_pass for
    THESE pairs (finitely many pairs never prove an infinite index); a claimed suffix that
    does not separate its pair -> refuted; any pair left unseparated (no suffix up to the
    bound, or unknown oracle answers) -> not_verified for the request; the estimate alone ->
    well_formed at most. Results are relative to the IR (data.ir_fingerprint). An oracle
    answer of unknown is never treated as "not in L".
    """
    deadline = Deadline(NERODE_TIMEOUT_S - _DEADLINE_MARGIN_S)
    class_oracle = oracle_for(args.cls, args.ir)
    alphabet = list(class_oracle.alphabet)
    oracle = _CachedOracle(class_oracle)
    word_limit = 100
    if args.max_suffix_len is not None:
        max_suffix_len = args.max_suffix_len
        check_enumeration(len(alphabet), max_suffix_len, limit=NERODE_SUFFIX_BUDGET)
    else:
        max_suffix_len = max_len_within(len(alphabet), NERODE_DEFAULT_SUFFIX_BUDGET,
                                        12 if len(alphabet) > 1 else 40)

    requests: list[tuple[str, str, str | None, dict[str, Any]]] = []
    for k, p in enumerate(args.pairs):
        for w in (p.u, p.v) + ((p.suffix,) if p.suffix is not None else ()):
            check_word(w, alphabet=alphabet, what=f"pairs[{k}] word")
        requests.append((p.u, p.v, p.suffix, {"source": f"pairs[{k}]"}))
    family_var = None
    if args.family is not None:
        fam_pairs, family_var = _family_pairs(args.family, alphabet, word_limit)
        for u, v, s, i, j in fam_pairs:
            requests.append((u, v, s, {"source": "family", "i": i, "j": j}))
    if len(requests) > NERODE_MAX_PAIRS:
        raise InputTooLarge(f"{len(requests)} pairs to check, limit {NERODE_MAX_PAIRS}")

    results = []
    for u, v, s, meta in requests:
        if len(u) + (len(s) if s else 0) > word_limit or len(v) + (len(s) if s else 0) > word_limit:
            raise InputTooLarge(f"u·z or v·z is longer than {word_limit} for pair {meta}")
        rec = _probe_pair(oracle, u, v, s, alphabet, max_suffix_len, deadline, word_limit)
        results.append({**meta, **rec})

    estimate = None
    if args.estimate_depth is not None:
        estimate = _estimate(oracle, alphabet, args.estimate_depth, deadline)

    decisive, unknown, longest = oracle.stats()
    statuses = [r["status"] for r in results]
    separated = sum(s in ("distinguished", "claimed_suffix_confirmed") for s in statuses)
    failed = [r for r in results if r["status"] == "claimed_suffix_fails"]
    truncated = deadline.expired() or any(r.get("search_truncated") for r in results)
    data: dict[str, Any] = {
        "ir_fingerprint": ir_fingerprint(args.ir),
        "cls": args.cls,
        "oracle": class_oracle.describe(),
        "alphabet": alphabet,
        "max_suffix_len": max_suffix_len,
        "pairs_checked": len(results),
        "pairs_separated": separated,
        "results": results,
    }
    if family_var is not None:
        data["family_variable"] = family_var
    if estimate is not None:
        data["estimate"] = estimate
    if oracle.notes:
        data["unknown_reasons"] = oracle.notes
    bounds = {"max_len": longest, "n_words": decisive, "n_unknown": unknown,
              "truncated": truncated, "n_pairs": len(results), "max_suffix_len": max_suffix_len}

    if not class_oracle.exact and (failed or (results and separated == len(results))):
        return ToolResult.success(
            data, trust="well_formed", bounds=bounds,
            reason=(f"{separated} of {len(results)} pairs separated and {len(failed)} claimed "
                    "suffix(es) failing, but the oracle is approximate "
                    f"({class_oracle.note or class_oracle.source}); not evidence either way"))
    if failed:
        first = failed[0]
        return ToolResult.success(
            data, trust="refuted", bounds=bounds,
            reason=(f"{len(failed)} claimed suffix(es) do not separate their pair, e.g. "
                    f"z={first.get('suffix')!r} for u={first['u']!r}, v={first['v']!r}"))
    if results and separated == len(results):
        return ToolResult.success(
            data, trust="bounded_pass", bounds=bounds,
            reason=(f"all {len(results)} pairs are separated by an oracle-checked suffix; this "
                    "covers these finitely many pairs only, not an infinite index"))
    if results:
        return ToolResult.success(
            data, trust="not_verified", bounds=bounds,
            reason=(f"only {separated} of {len(results)} pairs were separated (others: no "
                    f"suffix up to length {max_suffix_len} or unknown oracle answers); the "
                    "request as a whole is not confirmed"))
    if estimate is not None and estimate["status"] == "ok":
        return ToolResult.success(data, trust="well_formed", bounds=bounds,
                                  reason="heuristic Nerode class estimate only; not a proof")
    return ToolResult.success(data, trust="not_verified", bounds=bounds,
                              reason="the class estimate did not finish (unknown oracle answer or time)")


# ---------------------------------------------------------------------------
# dfa_vs_ir
# ---------------------------------------------------------------------------

@tfl_tool(group="reg", max_trust="bounded_pass", timeout_s=DFA_VS_IR_TIMEOUT_S)
def dfa_vs_ir(args: DfaVsIrArgs) -> ToolResult:
    """Compare a DFA with the task language (the IR's membership oracle) on EVERY word up to a length.

    Use it after building a DFA (by hand or with regex_to_dfa) to tie it to the task. Status
    'pass' means the DFA and the oracle agree on all decided words up to max_len; 'fail' lists
    the shortest counterexamples (word, oracle_says, dfa_says).

    Trust (R6): pass with at least 30 decided words -> bounded_pass (a bounded check: bounds.max_len,
    n_words, n_unknown; never 'verified'); a decided disagreement -> refuted (the DFA does not
    recognise the task language); fewer than 30 decided words -> well_formed; nothing decided
    -> not_verified. Unknown oracle answers are skipped, never read as "not in L".
    Results are relative to the IR (data.ir_fingerprint). With an approximate oracle
    (data.oracle.exact false) every outcome is only well_formed.
    """
    deadline = Deadline(DFA_VS_IR_TIMEOUT_S - _DEADLINE_MARGIN_S)
    partial = _require_valid_dfa(args.dfa)
    class_oracle = oracle_for(args.cls, args.ir)
    alphabet = list(class_oracle.alphabet)
    oracle = _CachedOracle(class_oracle)
    dfa = args.dfa.model_dump()
    if args.max_len is not None:
        max_len = args.max_len
    else:
        max_len = max_len_within(len(alphabet), DFA_VS_IR_BUDGET, 10 if len(alphabet) > 1 else 40)
    check_enumeration(len(alphabet), max_len, limit=DFA_VS_IR_MAX_WORDS)

    counterexamples: list[dict[str, Any]] = []
    tested = decisive = unknown = 0
    unknown_examples: list[str] = []
    completed_len = -1
    truncated = stopped = False
    for w in generate_exhaustive(alphabet, max_len):
        completed_len = max(completed_len, len(w) - 1)
        if deadline.expired():
            truncated = True
            break
        tested += 1
        says = oracle(w)
        if says is None:
            unknown += 1
            if len(unknown_examples) < 5:
                unknown_examples.append(w)
            continue
        decisive += 1
        accepted = dfa_runner.run_dfa(dfa, w)
        if accepted != says:
            counterexamples.append({"word": w, "oracle_says": says, "dfa_says": accepted})
            if len(counterexamples) >= 5:
                stopped = True
                break
    else:
        completed_len = max_len
    if counterexamples:
        status = "fail"
    elif decisive:
        status = "pass"
    else:
        status = "inconclusive"
    dfa_extra = sorted(set(dfa["alphabet"]) - set(alphabet))
    data: dict[str, Any] = {
        "status": status,
        "ir_fingerprint": ir_fingerprint(args.ir),
        "cls": args.cls,
        "oracle": class_oracle.describe(),
        "alphabet": alphabet,
        "max_len": max_len,
        "complete_up_to_len": completed_len if not counterexamples else None,
        "tested": tested,
        "decisive": decisive,
        "unknown": unknown,
        "counterexamples": counterexamples,
        "total_words": enumeration_size(len(alphabet), max_len),
        "partial_dfa": bool(partial),
    }
    if unknown_examples:
        data["unknown_examples"] = unknown_examples
    if dfa_extra:
        data["dfa_symbols_outside_alphabet"] = dfa_extra
    if oracle.notes:
        data["unknown_reasons"] = oracle.notes
    covered = completed_len if (truncated or stopped) else max_len
    bounds = {"max_len": max(covered, 0), "n_words": decisive, "n_unknown": unknown,
              "truncated": truncated}

    if not class_oracle.exact and decisive:
        return ToolResult.success(
            data, trust="well_formed", bounds=bounds,
            reason=(f"status {status} on {decisive} decided words, but the oracle is approximate "
                    f"({class_oracle.note or class_oracle.source}); not evidence either way"))
    if counterexamples:
        ce = counterexamples[0]
        return ToolResult.success(
            data, trust="refuted", bounds=bounds,
            reason=(f"counterexample {ce['word']!r}: the IR oracle says "
                    f"{'in' if ce['oracle_says'] else 'not in'} L, the DFA "
                    f"{'accepts' if ce['dfa_says'] else 'rejects'}"))
    if decisive >= MIN_DECISIVE_WORDS:
        return ToolResult.success(
            data, trust="bounded_pass", bounds=bounds,
            reason=(f"the DFA agrees with the IR oracle on all {decisive} decided words up to "
                    f"length {bounds['max_len']}"
                    + (f" ({unknown} undecided skipped)" if unknown else "")
                    + "; a bounded check, not an equivalence proof"))
    if decisive:
        return ToolResult.success(
            data, trust="well_formed", bounds=bounds,
            reason=(f"no disagreement, but only {decisive} decided words "
                    f"(< {MIN_DECISIVE_WORDS}); raise max_len"))
    return ToolResult.success(
        data, trust="not_verified", bounds=bounds,
        reason="the oracle decided no word; nothing was compared")


# ---------------------------------------------------------------------------
# pump_check_reg (VERDICT_POLICY §4 reg/pumping, R6)
# ---------------------------------------------------------------------------

#: Largest exponent i tried; i = 1 is never an exit (x y^1 z = w).
MAX_PUMP_I = 6
#: Pumping lengths a claim may be instantiated at (VERDICT_POLICY §4: p ∈ {2, 3, 4} by default).
MAX_PUMP_P = 12
PUMP_DEFAULT_P = (2, 3, 4)
PUMP_TIMEOUT_S = 30.0
#: Problem splits listed per p (all are counted).
_PUMP_EXAMPLES = 5
_PUMP_WITNESSES = 30


class PumpCheckRegArgs(ToolArgs):
    ir: dict[str, Any] = Field(description=_IR_DESCRIPTION.replace(
        "Results are", "Class reg. Its membership oracle is the reference. Results are"))
    word: str = Field(
        min_length=1, max_length=80,
        description=("The word w(p) your argument picks for the pumping length p, as a pattern in ONE "
                     "variable (any letter; it stands for p): blocks of a symbol or a parenthesised "
                     "group, optionally '^' with an integer exponent formula using +, -, *, implicit "
                     "multiplication and a constant power, e.g. 'a^p b^p', 'a^(p^2)', 'a^p b a^p', "
                     "'(ab)^p c^(p+1)'."))
    exit_i: list[int] = Field(
        min_length=1, max_length=MAX_PUMP_I,
        description=(f"The exponents i (0 or 2..{MAX_PUMP_I}) your argument uses to leave L: the argument "
                     "claims that for EVERY split w = xyz with |xy| <= p and |y| >= 1, x y^i z is not in "
                     "L for at least one of these i (e.g. [2], or [0, 2] when the cases use different "
                     "i)."))
    p_values: list[int] = Field(
        default_factory=list, max_length=6,
        description=(f"EXTRA pumping lengths to try (1..{MAX_PUMP_P}). The checked p are p = 2, 3, 4 "
                     "(VERDICT_POLICY §4) when p_min <= 2, or p_min, p_min+1, p_min+2 when p_min > 2, plus "
                     "every p here that is >= p_min (with the default p_min = 1 that includes p = 1); a p "
                     "below p_min is skipped. Larger p find more splits, and every p you add is part of "
                     "your claim."))
    p_min: int = Field(
        default=1, ge=1, le=MAX_PUMP_P - 2,
        description=("The least pumping length your argument covers (wlog p >= p0; say so in the proof: a "
                     "larger p is also a pumping length). With p_min > 2 the tool checks p_min, p_min+1, "
                     "p_min+2 instead of 2, 3, 4, and skips every p below p_min. Without it, a split at "
                     "p = 2 that your argument does not cover refutes the claim as stated."))

    @model_validator(mode="after")
    def _ranges(self) -> PumpCheckRegArgs:
        for i in self.exit_i:
            if i == 1:
                raise ValueError("exit_i: i = 1 is never an exit, x y^1 z = w is in L")
            if not 0 <= i <= MAX_PUMP_I:
                raise ValueError(f"exit_i: exponents must be 0 or 2..{MAX_PUMP_I}, got {i}")
        for p in self.p_values:
            if not 1 <= p <= MAX_PUMP_P:
                raise ValueError(f"p_values must be in 1..{MAX_PUMP_P}, got {p}")
        return self

    def checked_p(self) -> list[int]:
        """The policy p (2, 3, 4, or p_min..p_min+2 when p_min > 2) plus the extra
        p_values >= p_min: the model can add p, never drop a policy one."""
        base = list(PUMP_DEFAULT_P) if self.p_min <= PUMP_DEFAULT_P[0] else \
            [self.p_min, self.p_min + 1, self.p_min + 2]
        return sorted(set(base) | {p for p in self.p_values if p >= self.p_min})


def _pump_oracle(ir: dict[str, Any]) -> tuple[ClassOracle | None, list[str]]:
    """The reg oracle of the IR (``words.oracle_for``); for a ``natural`` IR
    whose description is exponent notation, the exponent-notation oracle
    (``cfl_system.lib.exponent_pattern``, VERDICT_POLICY §4 "оракул
    экспоненциальной нотации": exact within its bounds, None beyond them).
    ``(None, notes)`` when there is none."""
    notes: list[str] = []
    try:
        return oracle_for("reg", ir, validated=True), notes
    except ToolUnsupported as exc:
        notes.append(str(exc))
    spec = ir.get("language_spec") if isinstance(ir.get("language_spec"), dict) else {}
    text = spec.get("description")
    if spec.get("kind") == "natural" and isinstance(text, str) and text.strip():
        from cfl_system.lib.exponent_pattern import parse_exponent_pattern

        fn = parse_exponent_pattern(text)
        if fn is not None:
            alphabet = word_alphabet("reg", ir)
            doubt = _exponent_oracle_doubt(text, fn, alphabet)
            if doubt:
                notes.append(doubt)
            note = ("exponent notation parsed from the description text; unknown beyond its length bound or "
                    "step budget")
            return ClassOracle(
                "reg", alphabet, "cfl_system.lib.exponent_pattern (language_spec.description)",
                doubt is None, fn, note=(note if doubt is None else f"{note}; APPROXIMATE: {doubt}"),
                max_word_len=getattr(fn, "max_word_len", None)), notes
        notes.append("the description is not exponent notation cfl_system.lib.exponent_pattern can "
                     "evaluate (e.g. a non-linear condition such as n^2, or prose)")
    return None, notes


#: words up to this length must contain one the fallback oracle accepts
_EXPONENT_SANITY_LEN = 8


def _exponent_oracle_doubt(text: str, fn: Any, alphabet: Iterable[str]) -> str | None:
    """Why the exponent-notation oracle's answers are not trusted as exact, or None.

    ``cfl_system.lib.exponent_pattern`` misreads a comma-separated list of
    conditions ("n != m, n >= 0, m >= 0" gives the empty language, "m >= 0,
    n != m" drops n > m); that bug lives in the read-only cfl_system, so here a
    description with a comma after '|' and a parse that accepts no short word
    make the oracle approximate: its "not in L" then never refutes."""
    cond = text.split("|", 1)[1] if "|" in text else ""
    if "," in cond:
        return ("the condition is a comma-separated list, which cfl_system.lib.exponent_pattern can misread; "
                "write it as one condition joined by 'and', or re-encode the IR as a predicate")
    letters = sorted(set(alphabet))
    checked = 0
    for n in range(_EXPONENT_SANITY_LEN + 1):
        for w in product(letters, repeat=n):
            try:
                if fn("".join(w)) is True:
                    return None
            except Exception:  # noqa: BLE001 - a failing oracle is no oracle
                return "the parsed description raised an error on a short word"
        checked = n
        if len(letters) ** (n + 1) > 4000:
            break
    return (f"the parsed description accepts no word up to length {checked} (a misparse, or an empty "
            "language): re-encode the IR as a predicate")


def _pump_one_p(word: str, p: int, exits: list[int], searched: list[int], oracle: _CachedOracle | None,
                deadline: Deadline, add_witness: Any) -> dict[str, Any]:
    """Every split w = xyz with |xy| <= p, |y| >= 1, pumped at the searched i."""
    rec: dict[str, Any] = {"p": p, "word": word, "length": len(word), "word_in_L": None,
                           "splits": 0, "closed": 0, "claimed_exit_fails": 0, "undecided": 0,
                           "examples": []}
    if oracle is None:
        m = min(p, len(word))
        rec["splits_not_checked"] = m * (m + 1) // 2
        return rec
    rec["word_in_L"] = oracle(word)
    if rec["word_in_L"] is not None:
        add_witness(word, rec["word_in_L"], f"w(p) at p={p}")
    if rec["word_in_L"] is not True:
        return rec  # refuted (False) or unknown (None): the splits are not checked
    for len_xy in range(1, min(p, len(word)) + 1):
        for len_x in range(len_xy):
            if deadline.expired():
                rec["truncated"] = True
                return rec
            x, y, z = word[:len_x], word[len_x:len_xy], word[len_xy:]
            rec["splits"] += 1
            answers: dict[int, bool | None] = {}
            for i in searched:
                pumped = x + y * i + z
                answers[i] = oracle(pumped) if len(pumped) <= DEFAULT_LIMITS.max_word_len else None
                if answers[i] is not None:
                    add_witness(pumped, answers[i], f"p={p} x={x!r} y={y!r} i={i}")
            claimed = [answers[i] for i in exits]
            if any(a is False for a in claimed):
                rec["closed"] += 1
                continue
            status = "claimed_exit_fails" if all(a is True for a in claimed) else "undecided"
            rec[status] += 1
            example = {"x": x, "y": y, "z": z, "status": status,
                       "in_L_at": [i for i in searched if answers[i] is True],
                       "exits_at": [i for i in searched if answers[i] is False],
                       "unknown_at": [i for i in searched if answers[i] is None]}
            if status == "claimed_exit_fails" and "first_failure" not in rec:
                rec["first_failure"] = example
            if len(rec["examples"]) < _PUMP_EXAMPLES:
                rec["examples"].append(example)
    return rec


@tfl_tool(group="reg", max_trust="well_formed", timeout_s=PUMP_TIMEOUT_S)
def pump_check_reg(args: PumpCheckRegArgs) -> ToolResult:
    """Check a pumping-lemma argument for NON-regularity (tz §1.5) against the task IR, split by split.

    You give the word family w(p) your argument picks, the exit exponents it uses and, if needed,
    extra pumping lengths or the least p your argument covers (p_min). For p = 2, 3, 4
    (p_min..p_min+2 when p_min > 2) plus every extra p >= p_min the tool instantiates w(p), checks
    |w(p)| >= p and, with the IR's membership oracle, w(p) in L; then it takes EVERY split
    w = xyz with |xy| <= p, |y| >= 1 and pumps x y^i z for i = 0, 2..6. A split is closed when one of
    your exit_i gives a word not in L; data.per_p lists the splits that are not closed, with the
    exponents at which they do leave L (exits_at), so you can fix the argument. exit_i is the same
    at every p and every |y|: an argument whose exit depends on p or |y| (i = p!/|y| + 1) cannot
    be stated here; give a word with a fixed exit where you can, or accept that this tool cannot
    check the argument.

    Trust (VERDICT_POLICY §4 reg/pumping, R6): at most well_formed, which is what the verdict gate
    needs for a destructive basis: non_regular at confidence <= 0.55. Finitely many p never prove
    the claim for all p; a regular language whose period exceeds the largest p tried ((a^5)* with
    w = a^(5p), exit 0) closes every split too. refuted when a concrete premise is false by an exact
    oracle: |w(p)| < p, w(p) not in L, or a split on which x y^i z is in L for EVERY i in exit_i --
    your claim says one of them leaves L, and the oracle shows none does at that p. A refuted claim
    is no basis, and a cited one blocks the non_regular verdict (R2). A split on which one of your
    exit_i is undecided (unknown oracle answer) is not closed and never refuted, and an
    approximate oracle never refutes (not_verified). Without an
    oracle for the IR (e.g. a natural description that is not exponent notation, such as
    reg-07's {a^(n^2)}) only the structure is checked (w(p) instantiates and |w(p)| >= p; data.
    semantic_check false): still well_formed, and the membership claims are yours to prove.
    Oracle: the IR's reg oracle, or for a natural IR whose description is exponent notation
    ({a^n b^n | n >= 0}) the exponent-notation oracle. Unknown answers are never "not in L".
    """
    deadline = Deadline(PUMP_TIMEOUT_S - _DEADLINE_MARGIN_S)
    ir = require_valid("reg", args.ir)
    alphabet = list(word_alphabet("reg", ir))
    blocks, variables = _parse_pattern(args.word, alphabet)
    if len(variables) != 1:
        raise ToolInputError(f"the word {args.word!r} must use exactly one variable (the pumping length), "
                             f"found {sorted(variables) or 'none'}")
    var = next(iter(variables))
    p_values = args.checked_p()
    exits = sorted(set(args.exit_i))
    searched = [i for i in range(MAX_PUMP_I + 1) if i != 1]
    class_oracle, notes = _pump_oracle(ir)
    oracle = _CachedOracle(class_oracle) if class_oracle is not None else None

    witnesses: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_witness(w: str, in_l: bool, source: str) -> None:
        if w not in seen and len(witnesses) < _PUMP_WITNESSES:
            seen.add(w)
            witnesses.append({"word": w, "in_L": in_l, "source": source})

    refutations: list[str] = []
    exit_failures: list[str] = []
    per_p: list[dict[str, Any]] = []
    for p in p_values:
        word = _instantiate(blocks, var, p, DEFAULT_LIMITS.max_word_len)
        if len(word) < p:
            refutations.append(f"|w(p)| = {len(word)} < p = {p} at p = {p}: the lemma needs |w| >= p")
            per_p.append({"p": p, "word": word, "length": len(word), "too_short": True})
            continue
        rec = _pump_one_p(word, p, exits, searched, oracle, deadline, add_witness)
        per_p.append(rec)
        if rec["word_in_L"] is False:
            refutations.append(f"w(p) = {word!r} at p = {p} is NOT in L (oracle): the chosen word is invalid")
        ex = rec.get("first_failure")
        if ex is not None:
            # The claim is "every split leaves L at some i in exit_i"; a split on which the
            # oracle puts x y^i z in L for every i in exit_i is a concrete false premise at this p
            # (as pump_check_cfl claim mode: a word declared outside L is in L).
            msg = (f"at p = {p}, {rec['claimed_exit_fails']} split(s) keep x y^i z in L for EVERY claimed "
                   f"exit i in {exits} (oracle), e.g. x = {ex['x']!r}, y = {ex['y']!r}, z = {ex['z']!r}: "
                   "the claim that each split leaves L at one of exit_i is false at this p"
                   + (f"; that split does leave L at i = {ex['exits_at']}" if ex["exits_at"] else
                      f"; that split stays in L at every tried i {searched}"))
            exit_failures.append(msg)
            refutations.append(msg)

    decisive, unknown, longest = oracle.stats() if oracle is not None else (0, 0, 0)
    n_splits = sum(r.get("splits", 0) for r in per_p)
    closed = sum(r.get("closed", 0) for r in per_p)
    failing = sum(r.get("claimed_exit_fails", 0) for r in per_p)
    undecided = sum(r.get("undecided", 0) for r in per_p)
    truncated = any(r.get("truncated") for r in per_p)
    data: dict[str, Any] = {
        "mode": "claim", "cls": "reg", "ir_fingerprint": ir_fingerprint(args.ir),
        "oracle": class_oracle.describe() if class_oracle is not None else None,
        "oracle_notes": notes,
        "semantic_check": class_oracle is not None,
        "word_pattern": args.word, "variable": var, "exit_i": exits, "p_values": p_values,
        "p_min": args.p_min, "extra_p_values": sorted(set(args.p_values)),
        "searched_i": searched, "split_condition": "|xy| <= p, |y| >= 1",
        "per_p": per_p, "refutations": refutations, "exit_failures": exit_failures, "witnesses": witnesses,
        "scope": ("finite p only: closing every split at these p does not prove the claim for all p (a "
                  "regular language whose period exceeds the largest p tried closes them too; VERDICT_POLICY "
                  "§4). exit_i is checked as stated: a split that stays in L at every claimed exit refutes "
                  "the claim at that p (exact oracle only); exit_i cannot depend on p or |y|"),
    }
    if oracle is not None and oracle.notes:
        data["unknown_reasons"] = oracle.notes
    bounds = {"p_values": p_values, "i_values": searched, "n_words": decisive, "n_unknown": unknown,
              "max_len": longest, "n_splits": n_splits, "splits_closed": closed,
              "unresolved_splits": failing + undecided, "truncated": truncated}

    exact = class_oracle is not None and class_oracle.exact
    structural = [r for r in refutations if r.startswith("|w(p)|")]
    hint = (" -- fix the word or the exits (data.per_p[*].examples: exits_at); exit_i is the same for every "
            "p and |y|, so an exit that depends on them cannot be checked here" if exit_failures else "")
    if structural or (refutations and exact):
        if not exact:  # an approximate oracle's answers never refute; only |w(p)| < p does
            hint = ""
        shown = refutations if exact else structural
        return ToolResult.success(data, trust="refuted", bounds=bounds,
                                  reason="; ".join(shown)[:900 - len(hint)] + hint)
    if refutations:
        return ToolResult.success(
            data, trust="not_verified", bounds=bounds,
            reason=(f"the approximate oracle ({class_oracle.note}) contradicts the argument: "
                    + "; ".join(refutations)[:600] + " -- not a refutation; the argument is not verified"))
    if class_oracle is None:
        return ToolResult.success(
            data, trust="well_formed", bounds=bounds,
            reason=(f"no membership oracle for this IR ({'; '.join(notes)[:300]}): checked only that w(p) "
                    f"instantiates with |w(p)| >= p at p = {p_values}; w(p) in L and x y^i z not in L were "
                    "NOT checked and are yours to prove; at most well_formed (VERDICT_POLICY §4)"))
    unknown_words = [r["p"] for r in per_p if r.get("word_in_L") is None]
    parts = [f"{closed} of {n_splits} splits closed at a claimed exit i in {exits} for p = {p_values}"]
    if undecided:
        parts.append(f"{undecided} split(s) undecided (unknown oracle answers; not a refutation)")
    if unknown_words:
        parts.append(f"w(p) undecided by the oracle at p = {unknown_words}, so its splits were not checked")
    if truncated:
        parts.append("the time limit cut the check short")
    return ToolResult.success(
        data, trust="well_formed", bounds=bounds,
        reason="; ".join(parts) + ("; finite p and i are diagnostics, never a proof for all p: at most "
                                   "well_formed (VERDICT_POLICY §4 reg/pumping, R6)"))
