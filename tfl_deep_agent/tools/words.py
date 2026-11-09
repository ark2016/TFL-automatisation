"""Word tools (P1, group ``words``, ceiling ``bounded_pass``).

* ``sample_words`` -- words in L and not in L up to a length bound, every label
  re-checked by the class membership oracle. Reference only (``not_verified``,
  ``data.evidence_role = "reference"``): no claim is checked.
* ``check_membership`` -- the oracle's answer per word, optionally against
  expected labels. Only a call with ``expected`` checks a claim; its trust is
  computed over the expected words alone, and an undecided expected word
  leaves the claim ``not_verified``.

Both are relative to the IR the agent built (its fingerprint is in ``data``).
:func:`compare_with_oracle` / :func:`comparison_result` run a constructed
artifact (a DPDA, a grammar) against the oracle for ``dpda_vs_ir`` and
``grammar_vs_ir`` (R2 / R2').

:func:`oracle_for` is the one class -> oracle dispatch (TOOL_INVENTORY.md §0,
§10) for every tool group: it hides the ``bool`` vs ``bool | None`` and the
raise vs ``None`` differences of the four builders behind
:meth:`ClassOracle.query`, which returns ``(True | False | None, note)``.
``None`` is "unknown", never "not in L" (docs/VERDICT_POLICY.md §4).

Deviations from the libs, on purpose:

* REG ``predicate`` IRs whose ``concat_pattern`` has literal elements (``a``,
  ``b`` in ``z1 w a w b z2``, ``agent_system/examples/task_z1wawbz2.json``):
  ``agent_system.lib.oracle`` binds such an element like one more free part, so
  it accepts ``aaaaaaa`` for that task. Here the decomposition search matches
  the literal text and delegates every other predicate node to the lib's
  ``_eval_predicate``. Without literals the lib oracle is used as is.
* CFL ``predicate`` IRs use the same REG path rather than
  ``cfl_oracle.predicate_oracle``, which turns evaluation errors into False.
* Approximate oracles (a CFL natural-language ``grammar_filter``, an LL
  ``set_builder`` with comment-only constraints the oracle ignores) never give
  ``bounded_pass`` or ``refuted``: their labels stay ``well_formed``.
* Split searches (decomposition / repeated-subword kinds) cost about
  ``(n+1)^parts`` per word, and one call cannot be interrupted; a word whose
  estimate exceeds :data:`MAX_SPLIT_WORK` gets ``None`` instead of being run.
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import Field

from tfl_deep_agent.tools.base import (
    DEFAULT_LIMITS,
    Deadline,
    InputTooLarge,
    ToolArgs,
    ToolInputError,
    ToolResult,
    ToolUnsupported,
    WordList,
    check_words,
    max_len_within,
    tfl_tool,
)
from tfl_deep_agent.tools.ir import (
    CLASS_DESCRIPTION,
    IR_DESCRIPTION,
    TFLClass,
    alphabet_for,
    decomposition_literals,
    ir_fingerprint,
    language_kind,
    ll_informal_constraints,
    require_valid,
)

__all__ = [
    "ClassOracle", "MAX_SPLIT_WORK", "MIN_DECISIVE_WORDS", "REFERENCE_ROLE", "oracle_for",
    "word_alphabet", "sample_language_words", "generator_candidates", "compare_with_oracle",
    "comparison_result", "sample_words", "check_membership",
]

#: Per-word work estimate above which a split-search oracle is not run.
MAX_SPLIT_WORK = 5_000_000
#: Longest word passed to the DCFL builders (their own default is 60).
_DCFL_MAX_WORD_LEN = DEFAULT_LIMITS.max_word_len


@dataclass(frozen=True)
class ClassOracle:
    """A membership oracle for one IR, with what is known about it."""

    cls: str
    alphabet: tuple[str, ...]
    source: str
    exact: bool
    fn: Callable[[str], Any]
    note: str | None = None
    cost: Callable[[int], float] | None = None
    max_word_len: int | None = None

    def query(self, word: str) -> tuple[bool | None, str | None]:
        """``(answer, note)``: answer ``True`` / ``False``, or ``None`` with the reason."""
        if self.max_word_len is not None and len(word) > self.max_word_len:
            return None, f"longer than the oracle bound {self.max_word_len}"
        if self.cost is not None and self.cost(len(word)) > MAX_SPLIT_WORK:
            return None, f"split search for length {len(word)} exceeds the work limit"
        try:
            answer = self.fn(word)
        except Exception as exc:  # noqa: BLE001 - an oracle error is "unknown", never "not in L"
            return None, f"oracle raised {type(exc).__name__}: {exc}"[:240]
        if answer is None:
            return None, "the oracle could not decide (bound or step budget)"
        if answer is True or answer is False:
            return answer, None
        if type(answer).__name__ in ("bool_", "bool8"):  # numpy booleans
            return bool(answer), None
        return None, f"non-boolean oracle answer {answer!r}"[:240]

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.source, "exact": self.exact,
                               "alphabet": list(self.alphabet)}
        if self.note:
            out["note"] = self.note
        if self.max_word_len is not None:
            out["max_word_len"] = self.max_word_len
        return out


# ---------------------------------------------------------------------------
# Split-search cost estimates
# ---------------------------------------------------------------------------

def _free_parts(node: Mapping[str, Any], *, literals_are_free: bool) -> int:
    parts = {p for p in node.get("parts") or [] if isinstance(p, str)}
    seen: set[str] = set()
    n_free = 0
    for elem in node.get("concat_pattern") or []:
        if not isinstance(elem, str):
            continue
        name = elem[4:-1] if elem.startswith("rev(") and elem.endswith(")") else elem
        if name not in parts and not literals_are_free:
            continue
        if name not in seen:
            seen.add(name)
            n_free += 1
    return n_free


def _predicate_cost(pred: Any, n: int, *, literals_are_free: bool) -> float:
    """Worst-case number of split-search calls for a word of length *n*
    (0 for a predicate without any decomposition)."""
    if isinstance(pred, list):
        return sum(_predicate_cost(p, n, literals_are_free=literals_are_free) for p in pred)
    if not isinstance(pred, Mapping):
        return 0.0
    if isinstance(pred.get("parts"), list) and isinstance(pred.get("concat_pattern"), list):
        inner = _predicate_cost(pred.get("constraints") or [], n, literals_are_free=literals_are_free)
        return float(n + 1) ** _free_parts(pred, literals_are_free=literals_are_free) * max(1.0, inner)
    if isinstance(pred.get("operands"), list):
        return sum(_predicate_cost(o, n, literals_are_free=literals_are_free) for o in pred["operands"])
    return 0.0


def _spec_cost(spec: Mapping[str, Any], *, literals_are_free: bool) -> Callable[[int], float] | None:
    kind = spec.get("kind")
    if kind == "predicate":
        pred = spec.get("predicate")
        return lambda n: _predicate_cost(pred, n, literals_are_free=literals_are_free)
    if kind in ("repeated_subword", "exists_decomposition"):
        k = _free_parts(spec, literals_are_free=True)
        return lambda n: float(n + 1) ** k
    return None


# ---------------------------------------------------------------------------
# REG predicate oracle that treats concat_pattern literals as literal text
# ---------------------------------------------------------------------------

def _literal_aware_predicate_oracle(spec: Mapping[str, Any]) -> Callable[[str], bool]:
    from agent_system.lib.oracle import _eval_predicate as base_eval

    alphabet = list(spec.get("alphabet") or [])
    variable = spec.get("variable", "w")
    predicate = spec["predicate"]

    def ev(pred: Mapping[str, Any], env: dict[str, str]) -> bool:
        op = pred.get("op")
        if op in ("and", "or", "not"):
            values = [ev(o, env) for o in pred["operands"]]
            if op == "and":
                return all(values)
            if op == "or":
                return any(values)
            return not values[0]
        if "parts" in pred and "concat_pattern" in pred:
            return decomposition(pred, env)
        return base_eval(pred, env, alphabet)

    def decomposition(pred: Mapping[str, Any], env: dict[str, str]) -> bool:
        # Same contract as agent_system.lib.oracle._eval_exists_decomposition:
        # the word is the first variable of env; parts may be any substrings.
        word = env[next(iter(env))]
        parts = set(pred["parts"])
        pattern = list(pred["concat_pattern"])
        constraints = pred.get("constraints") or []

        def go(pos: int, i: int, bindings: dict[str, str]) -> bool:
            if i == len(pattern):
                return pos == len(word) and all(ev(c, {**env, **bindings}) for c in constraints)
            elem = pattern[i]
            is_rev = elem.startswith("rev(") and elem.endswith(")")
            name = elem[4:-1] if is_rev else elem
            if name not in parts:
                if is_rev:
                    raise ValueError(f"rev({name}) refers to an unknown part")
                return word.startswith(elem, pos) and go(pos + len(elem), i + 1, bindings)
            if name in bindings:
                value = bindings[name][::-1] if is_rev else bindings[name]
                return word.startswith(value, pos) and go(pos + len(value), i + 1, bindings)
            for end in range(pos, len(word) + 1):
                seg = word[pos:end]
                if go(end, i + 1, {**bindings, name: seg[::-1] if is_rev else seg}):
                    return True
            return False

        return go(0, 0, {})

    def oracle(word: str) -> bool:
        return ev(predicate, {variable: word})

    return oracle


def _reg_predicate_oracle(ir: Mapping[str, Any], cls: str) -> tuple[Callable[[str], Any], str, Any]:
    spec = ir["language_spec"]
    if decomposition_literals(spec.get("predicate")):
        return (_literal_aware_predicate_oracle(spec),
                "tfl_deep_agent literal-aware split search + agent_system.lib.oracle._eval_predicate",
                _spec_cost(spec, literals_are_free=False))
    from agent_system.lib.oracle import oracle_from_ir

    try:
        fn = oracle_from_ir({"language_spec": dict(spec)})
    except (ValueError, KeyError, TypeError) as exc:
        raise ToolUnsupported(f"no {cls} predicate oracle: {exc}") from None
    return fn, "agent_system.lib.oracle.oracle_from_ir (predicate)", _spec_cost(spec, literals_are_free=True)


# ---------------------------------------------------------------------------
# The class -> oracle dispatch
# ---------------------------------------------------------------------------

def word_alphabet(cls: str, ir: Mapping[str, Any]) -> tuple[str, ...]:
    """The IR's alphabet if the word tools can use it: declared, non-empty,
    single-character symbols, at most ``DEFAULT_LIMITS.max_alphabet``."""
    alphabet = alphabet_for(cls, ir)
    if not alphabet:
        raise ToolUnsupported(
            f"the {cls} IR declares no alphabet (kind {language_kind(cls, ir)!r}); "
            "add language_spec.alphabet (dcfl: top-level alphabet)")
    multi = [s for s in alphabet if len(s) != 1]
    if multi:
        raise ToolUnsupported(f"multi-character symbols {multi}: the oracles read words character "
                              "by character")
    if len(alphabet) > DEFAULT_LIMITS.max_alphabet:
        raise InputTooLarge(f"alphabet has {len(alphabet)} symbols, limit {DEFAULT_LIMITS.max_alphabet}")
    return tuple(alphabet)


def _is_exponent_pattern(fn: Any) -> bool:
    return type(fn).__module__ == "cfl_system.lib.exponent_pattern"


def oracle_for(cls: str, ir: Mapping[str, Any], *, validated: bool = False) -> ClassOracle:
    """Build the membership oracle of a *cls* IR.

    Raises :class:`ToolInputError` for an invalid IR and
    :class:`ToolUnsupported` when the class has no oracle for it (a
    ``natural`` description that is not exponent notation, a regex with
    back-references, an unparseable DCFL pattern, ...)."""
    if not validated:
        ir = require_valid(cls, ir)
    alphabet = word_alphabet(cls, ir)
    spec = ir.get("language_spec") if isinstance(ir.get("language_spec"), Mapping) else {}
    kind = language_kind(cls, ir)

    if cls == "reg":
        if not spec:
            raise ToolUnsupported("the IR has no language_spec")
        if kind == "predicate":
            fn, source, cost = _reg_predicate_oracle(ir, cls)
            return ClassOracle(cls, alphabet, source, True, fn, cost=cost)
        from agent_system.lib.oracle import oracle_from_ir

        try:
            fn = oracle_from_ir(dict(ir))
        except (ValueError, KeyError, TypeError) as exc:
            raise ToolUnsupported(f"no REG oracle for kind {kind!r}: {exc}") from None
        source = {"grammar": "agent_system.lib.grammar_utils.cyk_parse",
                  "regex": "Python re full match (agent_system.lib.oracle)"}.get(kind, "agent_system oracle")
        return ClassOracle(cls, alphabet, source, True, fn)

    if cls == "cfl":
        if not spec:
            raise ToolUnsupported("the IR has no language_spec")
        if kind == "predicate":
            fn, source, cost = _reg_predicate_oracle(ir, cls)
            return ClassOracle(cls, alphabet, source, True, fn, cost=cost)
        from cfl_system.lib.cfl_oracle import UnsupportedOracleKindError, cfl_oracle_from_ir

        try:
            fn = cfl_oracle_from_ir(dict(ir))
        except UnsupportedOracleKindError as exc:
            raise ToolUnsupported(f"no CFL oracle: {exc}") from None
        except (ValueError, KeyError, TypeError) as exc:
            raise ToolInputError(f"the CFL oracle rejects this IR: {type(exc).__name__}: {exc}") from None
        if getattr(fn, "approximation_reason", None) == "natural_language_filter":
            return ClassOracle(cls, alphabet, "cfl_system.lib.cfl_oracle (grammar only)", False, fn,
                               note="natural-language filter is not evaluated: the oracle checks only "
                                    "the grammar, so 'in L' answers may be wrong")
        if _is_exponent_pattern(fn):
            return ClassOracle(cls, alphabet, "cfl_system.lib.exponent_pattern", True, fn,
                               note="exponent notation parsed from the description text; unknown "
                                    "beyond its length bound or step budget",
                               max_word_len=getattr(fn, "max_word_len", None))
        source = {"grammar": "cfl_system CNF + CYK", "grammar_filter": "cfl_system CYK + filter predicate",
                  "repeated_subword": "cfl_system split search",
                  "exists_decomposition": "cfl_system split search",
                  "regex": "Python re full match (agent_system.lib.oracle)"}.get(kind, "cfl_system oracle")
        return ClassOracle(cls, alphabet, source, True, fn, cost=_spec_cost(spec, literals_are_free=True))

    if cls == "dcfl":
        from dcfl_system.lib.word_sampler import (
            build_membership_oracle_from_ir,
            build_set_builder_membership_oracle,
        )

        fn = build_membership_oracle_from_ir(dict(ir), max_word_len=_DCFL_MAX_WORD_LEN)
        if fn is None:
            raise ToolUnsupported(
                "no DCFL oracle: the set_builder pattern is neither a concatenation of declared "
                "variables and letters nor parseable exponent notation")
        if ir.get("input_format") == "grammar" or kind == "grammar":
            return ClassOracle(cls, alphabet, "dcfl_system word_sampler grammar oracle (CYK)", True, fn,
                               max_word_len=_DCFL_MAX_WORD_LEN)
        if spec.get("variables") and build_set_builder_membership_oracle(
                dict(spec), list(alphabet), max_word_len=_DCFL_MAX_WORD_LEN) is not None:
            return ClassOracle(cls, alphabet, "dcfl_system word_sampler segment matching", True, fn,
                               note="unknown when the search step budget runs out",
                               max_word_len=_DCFL_MAX_WORD_LEN)
        return ClassOracle(cls, alphabet, "cfl_system.lib.exponent_pattern (via dcfl word_sampler)", True,
                           fn, note="exponent notation parsed from word_pattern; unknown beyond its length "
                                    "bound or step budget", max_word_len=_DCFL_MAX_WORD_LEN)

    if cls == "ll":
        from ll_system.lib.word_oracle import oracle_from_ll_ir, task_grammar_from_ir

        fn = oracle_from_ll_ir(dict(ir))
        if fn is None:
            raise ToolUnsupported(
                f"no LL oracle for kind {kind!r}: only an explicit grammar (Format 2/3) or a set_builder "
                "template the ll word_oracle understands")
        if task_grammar_from_ir(dict(ir)) is not None:
            return ClassOracle(cls, alphabet, "ll_system word_oracle grammar oracle (CYK)", True, fn)
        informal = ll_informal_constraints(ir)
        if informal:
            return ClassOracle(cls, alphabet, "ll_system word_oracle set_builder template", False, fn,
                               note=f"{len(informal)} comment-only constraint(s) are ignored by the oracle")
        return ClassOracle(cls, alphabet, "ll_system word_oracle set_builder template", True, fn,
                           note="unknown when the matcher's step budget runs out")

    raise ToolInputError(f"unknown task class {cls!r}")


# ---------------------------------------------------------------------------
# Candidate generation and labelling
# ---------------------------------------------------------------------------

def _generator_words(cls: str, ir: Mapping[str, Any], max_len: int, count: int,
                     seed: int) -> list[tuple[str, bool | None, str]]:
    """``(word, generator label, source)`` from the class's own word generator."""
    out: list[tuple[str, bool | None, str]] = []
    if cls == "cfl":
        from cfl_system.lib.cfl_word_generator import generate_test_words

        got = generate_test_words(dict(ir), max_positive=count, max_negative=count,
                                  max_length=max_len, seed=seed)
        out += [(w, True, "cfl_word_generator") for w in got.get("positive") or []]
        out += [(w, False, "cfl_word_generator") for w in got.get("negative") or []]
        out += [(w, None, "cfl_word_generator") for w in got.get("boundary") or []]
    elif cls == "dcfl":
        from dcfl_system.lib.word_sampler import sample_words as dcfl_sample

        for item in dcfl_sample(dict(ir), count=count, max_len=max_len):
            label = item.get("in_language")
            out.append((item.get("word", ""), label if isinstance(label, bool) else None,
                        f"dcfl word_sampler/{item.get('source')}"))
    elif cls == "ll":
        from ll_system.lib.word_oracle import generate_words

        out += [(w, True, "ll word_oracle.generate_words") for w in generate_words(dict(ir), max_len)]
    return out


def _spread(words: list[str], k: int) -> list[str]:
    """*k* words evenly spaced over *words* (already sorted by length)."""
    if len(words) <= k:
        return list(words)
    if k <= 0:
        return []
    if k == 1:
        return [words[0]]
    return [words[round(i * (len(words) - 1) / (k - 1))] for i in range(k)]


def _by_len(words: list[str]) -> list[str]:
    return sorted(words, key=lambda w: (len(w), w))


def sample_language_words(
    cls: str, ir: Mapping[str, Any], *, max_len: int, count: int, max_enumerated: int,
    random_words: int, use_generators: bool, seed: int, deadline: Deadline,
    validated: bool = False,
) -> dict[str, Any]:
    """Words in L and not in L up to *max_len* (shared by ``sample_words`` and
    ``ir_to_text``). Candidates: every word up to the length where at most
    *max_enumerated* words exist, the class generator's words, and
    *random_words* seeded random longer words. With an oracle every candidate
    is labelled by it; without one, only generator labels are available."""
    if not validated:
        ir = require_valid(cls, ir)
    alphabet = word_alphabet(cls, ir)
    oracle: ClassOracle | None
    try:
        oracle = oracle_for(cls, ir, validated=True)
        oracle_reason = None
    except ToolUnsupported as exc:
        oracle, oracle_reason = None, str(exc)

    notes: list[str] = []
    candidates: dict[str, str] = {}
    target_len = min(max_len, max_len_within(len(alphabet), max_enumerated, max_len))
    # max_len_within never returns less than 1, so the budget can run out
    # inside a length: exhaustive_len is the last length enumerated in full
    # (-1 when not even ε fits).
    exhaustive_len = -1
    budget = max_enumerated
    for length in range(target_len + 1):
        complete = True
        for combo in itertools.product(alphabet, repeat=length):
            if budget <= 0:
                complete = False
                break
            candidates.setdefault("".join(combo), "enumeration")
            budget -= 1
        if not complete:
            break
        exhaustive_len = length
    gen_labels: dict[str, bool] = {}
    if use_generators:
        try:
            for word, label, source in _generator_words(cls, ir, max_len, count, seed):
                if not isinstance(word, str) or len(word) > max_len or any(c not in alphabet for c in word):
                    continue
                candidates.setdefault(word, source)
                if label is not None:
                    gen_labels.setdefault(word, label)
        except Exception as exc:  # noqa: BLE001 - the generator is optional
            notes.append(f"class word generator failed: {type(exc).__name__}: {exc}"[:240])
    if random_words > 0 and max_len > exhaustive_len:
        rng = random.Random(seed)
        for _ in range(random_words):
            n = rng.randint(max(exhaustive_len + 1, 0), max_len)
            candidates.setdefault("".join(rng.choice(alphabet) for _ in range(n)), "random")

    in_l: list[str] = []
    out_l: list[str] = []
    unknown: list[dict[str, str]] = []
    truncated = False
    disagreements: list[str] = []
    if oracle is not None:
        labels_from = "oracle"
        for word in candidates:
            if deadline.expired():
                truncated = True
                break
            answer, why = oracle.query(word)
            if answer is True:
                in_l.append(word)
            elif answer is False:
                out_l.append(word)
            else:
                unknown.append({"word": word, "reason": why or "unknown"})
            if answer is not None and word in gen_labels and gen_labels[word] != answer:
                disagreements.append(word)
        if truncated:
            notes.append("time budget ran out before every candidate was labelled")
        if disagreements:
            notes.append(f"the class generator's own label differs from the oracle for "
                         f"{len(disagreements)} word(s), e.g. {disagreements[:3]}; oracle labels are used")
    else:
        # Without an oracle only constructed words are reported: the
        # generators' "not in L" labels are mutation heuristics (dcfl
        # generate_negative_examples: "words that likely do NOT belong").
        labels_from = "generator"
        in_l = [w for w, lab in gen_labels.items() if lab is True]
        if not in_l:
            raise ToolUnsupported(oracle_reason or "no oracle and no generator labels")
        notes.append(f"no oracle ({oracle_reason}); words in L come only from the class word generator "
                     "and no word is reported as not in L")

    in_sorted, out_sorted = _by_len(in_l), _by_len(out_l)
    decisive = len(in_l) + len(out_l)
    if oracle is not None and not in_l and not truncated and not unknown:
        if exhaustive_len >= 0:
            notes.append(f"no word of length <= {exhaustive_len} (and none of the other candidates) is in "
                         "L; this does not show that L is empty")
        else:
            notes.append("none of the candidates is in L; no length was enumerated in full "
                         "(raise max_enumerated)")
    return {
        "ir_fingerprint": ir_fingerprint(ir),
        "class": cls,
        "alphabet": list(alphabet),
        "oracle": oracle.describe() if oracle is not None else {"available": False, "reason": oracle_reason},
        "oracle_exact": bool(oracle is not None and oracle.exact),
        "labels_from": labels_from,
        "in_language": _spread(in_sorted, count),
        "not_in_language": _spread(out_sorted, count),
        "unknown": unknown[:20],
        "counts": {"candidates": len(candidates), "decisive": decisive, "in": len(in_l),
                   "out": len(out_l), "unknown": len(unknown)},
        "exhaustive_up_to": exhaustive_len if exhaustive_len >= 0 else None,
        "max_len": max_len,
        "longest_labelled": max((len(w) for w in in_l + out_l), default=0),
        "truncated": truncated,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# A constructed artifact against the IR's oracle (dpda_vs_ir, grammar_vs_ir)
# ---------------------------------------------------------------------------

#: Least number of words with a decided oracle answer *and* a decided artifact
#: answer for ``bounded_pass`` (dcfl ``oracle_verifier._DPDA_MIN_WORDS``, R2').
MIN_DECISIVE_WORDS = 30
#: Full enumeration of short words: every word up to the length k where
#: Σ_{i≤k}|Σ|^i ≤ 3000, k ≤ 10 (R2', ``max_len_within(|Σ|, 3000, 10)``).
ENUM_WORD_BUDGET = 3000
ENUM_MAX_LEN = 10
MAX_COUNTEREXAMPLES = 5


def generator_candidates(cls: str, ir: Mapping[str, Any], alphabet: tuple[str, ...], *, max_len: int,
                         count: int = 60, seed: int = 42) -> tuple[list[str], list[str]]:
    """Words from the class's own word generator (labels dropped: the oracle
    re-labels them), within *alphabet* and *max_len*. Returns (words, notes);
    a generator failure is a note, not an error."""
    try:
        got = _generator_words(cls, ir, max_len, count, seed)
    except Exception as exc:  # noqa: BLE001 - the generator is optional
        return [], [f"class word generator failed: {type(exc).__name__}: {exc}"[:240]]
    words = {w for w, _, _ in got
             if isinstance(w, str) and len(w) <= max_len and all(c in alphabet for c in w)}
    return sorted(words, key=lambda w: (len(w), w)), []


def compare_with_oracle(
    oracle: ClassOracle, run: Callable[[str], bool | None], *, deadline: Deadline,
    extra_words: list[str] | tuple[str, ...] = (), enum_budget: int = ENUM_WORD_BUDGET,
    enum_cap: int = ENUM_MAX_LEN,
) -> dict[str, Any]:
    """Run *run* (the artifact: ``True`` accept, ``False`` reject, ``None``
    unknown) and the oracle on every word up to ``max_len_within(|Σ|,
    enum_budget, enum_cap)`` and then on *extra_words*. A word counts only
    when both answers are decided. Stops at the deadline or after
    :data:`MAX_COUNTEREXAMPLES` disagreements. ``covered_len`` is the last
    length whose words were all run (-1: none)."""
    alphabet = oracle.alphabet
    target = max_len_within(len(alphabet), enum_budget, enum_cap)
    counterexamples: list[dict[str, Any]] = []
    oracle_unknown: list[str] = []
    artifact_unknown: list[str] = []
    checked = 0
    longest = 0
    covered_len = -1
    stopped = ""
    seen: set[str] = set()

    def one(word: str) -> bool:
        """False when the loop must stop."""
        nonlocal checked, longest, stopped
        if deadline.expired():
            stopped = "time budget"
            return False
        seen.add(word)
        expected, _ = oracle.query(word)
        if expected is None:
            oracle_unknown.append(word)
            return True
        got = run(word)
        if got is None:
            artifact_unknown.append(word)
            return True
        checked += 1
        longest = max(longest, len(word))
        if got != expected:
            counterexamples.append({"word": word, "in_language": expected, "artifact_accepts": got})
            if len(counterexamples) >= MAX_COUNTEREXAMPLES:
                stopped = "counterexample limit"
                return False
        return True

    going = True
    for length in range(target + 1):
        for combo in itertools.product(alphabet, repeat=length):
            if not one("".join(combo)):
                going = False
                break
        if not going:
            break
        covered_len = length
    if going:
        for word in sorted(set(extra_words) - seen, key=lambda w: (len(w), w)):
            if not one(word):
                break
    return {
        "checked": checked,
        "counterexamples": counterexamples,
        "oracle_unknown": len(oracle_unknown),
        "oracle_unknown_examples": oracle_unknown[:10],
        "artifact_unknown": len(artifact_unknown),
        "artifact_unknown_examples": artifact_unknown[:10],
        "enumerated_up_to": target,
        "covered_len": covered_len,
        "longest_checked": longest,
        "n_extra_words": len(set(extra_words)),
        "stopped": stopped or None,
        "truncated": stopped == "time budget",
    }


def comparison_result(cmp: Mapping[str, Any], oracle: ClassOracle, data: dict[str, Any], *,
                      subject: str, fingerprint: str) -> ToolResult:
    """Trust of :func:`compare_with_oracle` (R2 / R2'): a decided disagreement
    with an exact oracle -> ``refuted``; at least :data:`MIN_DECISIVE_WORDS`
    agreeing words with an exact oracle -> ``bounded_pass``; fewer, or an
    approximate oracle -> ``well_formed``; nothing decided -> ``not_verified``."""
    n_unknown = cmp["oracle_unknown"] + cmp["artifact_unknown"]
    bounds = {"max_len": max(cmp["covered_len"], 0), "n_words": cmp["checked"], "n_unknown": n_unknown,
              "truncated": cmp["truncated"], "exhaustive_up_to": cmp["covered_len"],
              "longest_checked": cmp["longest_checked"], "min_words": MIN_DECISIVE_WORDS}
    ces = cmp["counterexamples"]
    if ces and oracle.exact:
        c = ces[0]
        return ToolResult.success(
            data, trust="refuted", bounds=bounds,
            reason=f"{subject} disagrees with the {oracle.cls} oracle on {len(ces)} word(s), e.g. "
                   f"{c['word']!r}: in L = {c['in_language']}, {subject} accepts = {c['artifact_accepts']} "
                   f"(relative to IR {fingerprint})")
    if cmp["checked"] == 0:
        return ToolResult.success(
            data, trust="not_verified", bounds=bounds,
            reason=f"no word had a decided answer from both the oracle and {subject} (IR {fingerprint})")
    if not oracle.exact:
        extra = f"; {len(ces)} disagreement(s), not a refutation" if ces else ""
        return ToolResult.success(
            data, trust="well_formed", bounds=bounds,
            reason=f"approximate oracle ({oracle.note}){extra}; {cmp['checked']} word(s) compared "
                   f"(IR {fingerprint})")
    if cmp["checked"] < MIN_DECISIVE_WORDS:
        return ToolResult.success(
            data, trust="well_formed", bounds=bounds,
            reason=f"{subject} agrees with the oracle, but only {cmp['checked']} word(s) were decided "
                   f"(< {MIN_DECISIVE_WORDS}): coverage incomplete (IR {fingerprint})")
    cover = (f"every word up to length {cmp['covered_len']}" if cmp["covered_len"] >= 0
             else "no complete length")
    return ToolResult.success(
        data, trust="bounded_pass", bounds=bounds,
        reason=f"{subject} agrees with the {oracle.cls} oracle ({oracle.source}) on {cmp['checked']} decided "
               f"word(s): {cover}, plus {cmp['n_extra_words']} sampled word(s) up to length "
               f"{cmp['longest_checked']}; {n_unknown} undecided; a bounded check, not a proof of equality "
               f"(relative to IR {fingerprint})")


# ---------------------------------------------------------------------------
# Tool: sample_words
# ---------------------------------------------------------------------------

class SampleWordsArgs(ToolArgs):
    cls: TFLClass = Field(description=CLASS_DESCRIPTION)
    ir: dict[str, Any] = Field(description=IR_DESCRIPTION)
    max_len: int = Field(default=10, ge=0, le=20, description="Longest word to return (<= 20).")
    count: int = Field(default=10, ge=1, le=100,
                       description="How many words to return on each side (in L / not in L), "
                                   "spread over the lengths found.")
    max_enumerated: int = Field(
        default=2500, ge=1, le=DEFAULT_LIMITS.max_enum_words,
        description="Every word is enumerated up to the length where at most this many words exist.")
    random_words: int = Field(default=20, ge=0, le=200,
                              description="Extra seeded random words longer than the enumerated ones.")
    use_generators: bool = Field(default=True,
                                 description="Also use the class's own word generator for candidates.")
    seed: int = Field(default=42, ge=0, le=2**31 - 1, description="Seed for the random candidates.")
    time_budget_s: float = Field(default=20.0, gt=0, le=50,
                                 description="Stop labelling after this many seconds (partial result).")


#: ``data.evidence_role`` of a words result that checked no claim: the oracle
#: only labelled words (sample_words; check_membership without ``expected``).
#: Such a result is reference material for the agent, never a verdict basis.
REFERENCE_ROLE = "reference"


@tfl_tool(group="words", max_trust="not_verified", timeout_s=60, allow_refuted=False)
def sample_words(args: SampleWordsArgs) -> ToolResult:
    """Sample words in the language and words not in it, up to max_len, from a valid IR. Candidates are
    all short words, the class's word generator and random longer words; every returned label is
    re-checked by the class membership oracle. Use it to see what the IR's language looks like, to pick
    witnesses, and to show examples at HITL 1. '' is ε. Trust is always not_verified: the oracle only
    labels words here and no claim is checked (data.evidence_role = 'reference'); data.oracle_exact says
    whether the labels are exact. To check a claim, pass it to check_membership as expected labels.
    Fails (unsupported) when the IR has no oracle and no generator labels."""
    cls = args.cls
    ir = require_valid(cls, args.ir)
    res = sample_language_words(
        cls, ir, max_len=args.max_len, count=args.count, max_enumerated=args.max_enumerated,
        random_words=args.random_words, use_generators=args.use_generators, seed=args.seed,
        deadline=Deadline(args.time_budget_s), validated=True)
    res["evidence_role"] = REFERENCE_ROLE
    fp, c = res["ir_fingerprint"], res["counts"]
    bounds = {"max_len": res["longest_labelled"], "n_words": c["decisive"], "n_unknown": c["unknown"],
              "truncated": res["truncated"], "exhaustive_up_to": res["exhaustive_up_to"]}
    if c["decisive"] == 0:
        why = "no candidate could be decided"
    elif res["labels_from"] == "oracle" and res["oracle_exact"]:
        why = (f"labels by the exact {cls} oracle ({res['oracle']['source']}) on {c['decisive']} words up "
               f"to length {res['longest_labelled']}")
    elif res["labels_from"] == "oracle":
        why = "labels from an approximate oracle: " + str(res["oracle"].get("note"))
    else:
        why = "labels come only from the class word generator"
    return ToolResult.success(
        res, trust="not_verified", bounds=bounds,
        reason=f"reference only, no claim checked: {why}, relative to IR {fp}")


# ---------------------------------------------------------------------------
# Tool: check_membership
# ---------------------------------------------------------------------------

class CheckMembershipArgs(ToolArgs):
    cls: TFLClass = Field(description=CLASS_DESCRIPTION)
    ir: dict[str, Any] = Field(description=IR_DESCRIPTION)
    words: WordList = Field(
        default_factory=list,
        description="Words to check (at most 500, each at most 100 symbols of the IR's alphabet; "
                    "'' is ε, do not write 'ε').")
    expected: dict[str, bool] | None = Field(
        default=None,
        description="Optional claimed labels {word: true if in L}; these words are checked too. A "
                    "decided oracle answer that differs from a claim refutes the claim.")
    time_budget_s: float = Field(default=20.0, gt=0, le=50,
                                 description="Stop after this many seconds; remaining words are unknown.")


@tfl_tool(group="words", max_trust="bounded_pass", timeout_s=60)
def check_membership(args: CheckMembershipArgs) -> ToolResult:
    """Check whether each word is in the language of a valid IR, with the class membership oracle
    (CYK for grammars, split search for decompositions, template matching for set-builder specs,
    exponent-notation parsing for text patterns). Each answer is true, false or null (unknown: never
    read null as 'not in L'). Without expected labels the answers are reference only (trust
    not_verified, nothing claimed). With expected labels the trust is about those labels only: refuted
    when a decided answer contradicts one (a counterexample to that claim); not_verified when any expected
    word stays undecided; otherwise bounded_pass (well_formed for an approximate oracle). Results are
    relative to this IR: if the IR is wrong, so are they. Fails (unsupported) when the class has no
    oracle."""
    cls = args.cls
    ir = require_valid(cls, args.ir)
    oracle = oracle_for(cls, ir, validated=True)
    expected = dict(args.expected or {})
    if len(expected) > DEFAULT_LIMITS.max_words:
        raise InputTooLarge(f"{len(expected)} expected labels, limit {DEFAULT_LIMITS.max_words}")
    query: list[str] = []
    for w in list(args.words) + list(expected):
        if w not in query:
            query.append(w)
    if not query:
        raise ToolInputError("give at least one word (words or expected)")
    try:
        check_words(query, alphabet=oracle.alphabet)
    except InputTooLarge:
        raise
    except ToolInputError as exc:
        raise ToolInputError(f"{exc}; the alphabet is {list(oracle.alphabet)} and ε is ''") from None

    deadline = Deadline(args.time_budget_s)
    results: list[dict[str, Any]] = []
    truncated = False
    for w in query:
        if deadline.expired():
            truncated = True
            results.append({"word": w, "in_language": None, "note": "time budget ran out"})
            continue
        answer, why = oracle.query(w)
        row: dict[str, Any] = {"word": w, "in_language": answer}
        if why:
            row["note"] = why
        if w in expected:
            row["expected"] = expected[w]
        results.append(row)

    decided = [r for r in results if r["in_language"] is not None]
    mismatches = [{"word": r["word"], "expected": expected[r["word"]], "oracle": r["in_language"]}
                  for r in decided if r["word"] in expected and expected[r["word"]] != r["in_language"]]
    undecided_expected = [r["word"] for r in results if r["in_language"] is None and r["word"] in expected]
    decided_expected = [r for r in decided if r["word"] in expected]
    fp = ir_fingerprint(ir)
    data = {
        "ir_fingerprint": fp,
        "class": cls,
        "oracle": oracle.describe(),
        "results": results,
        "counts": {"words": len(results), "in": sum(1 for r in decided if r["in_language"]),
                   "out": sum(1 for r in decided if not r["in_language"]),
                   "unknown": len(results) - len(decided)},
        "mismatches": mismatches,
        "undecided_expected": undecided_expected,
        "truncated": truncated,
    }
    if not expected:
        # No claim: the oracle only answered (reference, like sample_words).
        data["evidence_role"] = REFERENCE_ROLE
        bounds = {"n_words": len(decided), "n_unknown": len(results) - len(decided),
                  "max_len": max((len(r["word"]) for r in decided), default=0), "truncated": truncated}
        kind = "exact" if oracle.exact else f"approximate ({oracle.note})"
        return ToolResult.success(
            data, trust="not_verified", bounds=bounds,
            reason=f"reference only, no claim checked (pass expected labels to check one): the {kind} "
                   f"{cls} oracle decided {len(decided)} of {len(results)} word(s), relative to IR {fp}")

    # With expected labels the trust is about the claim, i.e. the expected words only.
    data["evidence_role"] = "claim_check"
    bounds = {"n_words": len(decided_expected), "n_unknown": len(undecided_expected),
              "max_len": max((len(r["word"]) for r in decided_expected), default=0),
              "truncated": truncated}
    if mismatches and oracle.exact:
        m = mismatches[0]
        return ToolResult.success(
            data, trust="refuted", bounds=bounds,
            reason=f"the {cls} oracle contradicts {len(mismatches)} expected label(s), e.g. "
                   f"{m['word']!r}: expected {m['expected']}, oracle {m['oracle']} (IR {fp})")
    if undecided_expected:
        extra = (f"; {len(mismatches)} decided label(s) differ from an approximate oracle, not a "
                 "refutation" if mismatches else "")
        return ToolResult.success(
            data, trust="not_verified", bounds=bounds,
            reason=f"the claim is not verified: the oracle could not decide {len(undecided_expected)} of "
                   f"{len(expected)} expected word(s), e.g. {undecided_expected[0]!r}{extra} (IR {fp})")
    if not oracle.exact:
        extra = f"; {len(mismatches)} label(s) differ, not a refutation" if mismatches else ""
        return ToolResult.success(
            data, trust="well_formed", bounds=bounds,
            reason=f"approximate oracle ({oracle.note}){extra} (IR {fp})")
    return ToolResult.success(
        data, trust="bounded_pass", bounds=bounds,
        reason=f"all {len(decided_expected)} expected label(s) agree with the {cls} oracle "
               f"({oracle.source}), relative to IR {fp}")
