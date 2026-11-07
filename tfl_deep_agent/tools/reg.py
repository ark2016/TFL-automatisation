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
from collections.abc import Iterable
from itertools import product
from typing import Any, Literal

from pydantic import Field, model_validator

from agent_system.lib import congruence, dfa_builder, dfa_runner, grammar_utils
from agent_system.lib.word_generator import generate_exhaustive

from .base import (
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
from .ir import CLASS_DESCRIPTION, IR_DESCRIPTION, TFLClass, ir_fingerprint
from .words import ClassOracle, oracle_for

__all__ = [
    "MAX_DFA_STATES",
    "MAX_REGEX_LEN",
    "MIN_DECISIVE_WORDS",
    "DfaSpec",
    "DfaVsIrArgs",
    "GrammarRule",
    "GrammarSpec",
    "NerodeFamily",
    "NerodePair",
    "NerodeProbeArgs",
    "RegexToDfaArgs",
    "RunDfaArgs",
    "dfa_vs_ir",
    "nerode_probe",
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
    alphabet: Alphabet | None = Field(
        default=None,
        description=("Alphabet to build the DFA over (default: the symbols that occur). "
                     "Symbols that do not occur go to a rejecting dead state, so the DFA "
                     "is complete over this alphabet; needed before complementing."))

    @model_validator(mode="after")
    def _one_source(self) -> RegexToDfaArgs:
        if (self.regex is None) == (self.grammar is None):
            raise ValueError("give exactly one of regex or grammar")
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
                     "+, -, * and implicit multiplication ('a^2n', 'b^(n+1)', 'c^{3n-1}')."))
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

    Use it to get an exact automaton for a regex or linear grammar you wrote (or that the task
    gives), then check it against the task with dfa_vs_ir, or run words with run_dfa. The DFA is
    returned in the format the other REG tools take; every symbol is one character.

    Trust: verified for "this DFA recognises exactly L(regex) / L(grammar)" (a complete
    construction: Thompson, subset construction, Hopcroft minimisation). That says nothing about
    the TASK language unless the regex/grammar is the task's own definition; to tie it to the
    task, use dfa_vs_ir. n_states is the number of states of the minimal complete DFA over the
    alphabet used. Limits: regex <= 200 chars, result <= 500 states, 30 s.
    """
    if args.regex is not None:
        literals = _regex_literals(args.regex)
        if args.alphabet is not None:
            outside = sorted(set(literals) - set(args.alphabet))
            if outside:
                raise ToolInputError(f"regex uses symbols outside the alphabet: {outside}")
        try:
            raw = dfa_builder.build_dfa_from_regex(args.regex)
        except ValueError as exc:
            raise ToolInputError(f"regex does not parse: {exc}") from None
        source = {"regex": args.regex}
    else:
        g = args.grammar.model_dump(exclude_none=True)
        g.pop("kind", None)
        g["rules"] = [{"lhs": r["lhs"], "rhs": list(r["rhs"])} for r in g["rules"]]
        check_grammar_size(g)
        _check_grammar_symbols(g)
        if args.alphabet is not None:
            outside = sorted(set(g["terminals"]) - set(args.alphabet))
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

    alphabet = list(args.alphabet) if args.alphabet is not None else list(raw.get("alphabet") or [])
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
        "dfa": dfa,
        "n_states": n_states,
        "alphabet": dfa["alphabet"],
        "minimal_complete": bool(alphabet),
        "accepts_empty_word": dfa["start"] in set(dfa["accept"]),
        "language_is_empty": shortest is None,
        "shortest_accepted": shortest,
    }
    what = "L(regex)" if args.regex is not None else "L(grammar)"
    return ToolResult.success(
        data, trust="verified",
        reason=(f"exact construction: the DFA recognises exactly {what}; this is about the "
                "given expression, not the task language (use dfa_vs_ir for that)"))


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
    """Evaluate +, -, *, integers and the one variable -- no '**', no names
    other than *var*, every intermediate value bounded (no eval())."""
    src = re.sub(r"(\d)([A-Za-z(])", r"\1*\2", expr.strip())
    src = re.sub(r"\)\s*([A-Za-z0-9(])", r")*\1", src)
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
        raise ToolInputError(f"exponent {expr!r}: only +, -, *, integers and one variable are allowed")

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
