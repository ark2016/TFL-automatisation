"""Executable artifact of a closure_reduction proof (R2″ policy, user decision
2026-11-10; same trust rules as stack_strategy's ``dpda``, docs/VERDICT_POLICY.md R2').

A closure_reduction proof states an equality of languages. This module turns
the proof's ``artifact`` field into functions the oracle verifier can run on
words, so that equality is checked on every short word against the task's
membership oracle.

The proof's claim, by ``direction`` (Σ = task alphabet, Δ = ``source.alphabet``,
S = the source language, R = the regular language, h = the homomorphism):

==================  =============================  ===============================
operation           constructive (L is DCFL)       destructive (L is not DCFL)
==================  =============================  ===============================
complement          L = Σ* \\ S,  S a known DCFL    Σ* \\ L = S,  S a known non-DCFL
reg_intersection    L = S ∩ R,  S a known DCFL      L ∩ R = S,  S a known non-DCFL
inv_homomorphism    L = h⁻¹(S),  h: Σ → Δ*          h⁻¹(L) = S,  h: Δ → Σ*
==================  =============================  ===============================

Each side is a membership predicate: the side with L uses the task's own
oracle (never the proof's claims), the other side uses the artifact. Both
sides are compared on all words over the domain alphabet up to a bounded
length (``oracle_verifier._verify_closure_reduction_artifact``).

The equality alone never shows the class of S: S = L with R = Σ* (or h the
identity) satisfies it for every L, even a non-CFL one. So a constructive
reduction reaches ``bounded_pass`` only when S is given as ``source.dpda``,
whose determinism is checked as in R2′ (S is then a DCFL by construction);
with S as a ``pattern`` or a ``grammar`` the check can still refute the
equality, but agreement leaves trust at most ``well_formed``.

Artifact format (``proof_sketch.artifact``, prompts/closure_reduction.md)::

    {
      "source": {"alphabet": [...],
                 "pattern": "{a^n b^n | n >= 1}" | null,      # exponent notation
                 "grammar": {"start": "S", "rules": [{"lhs", "rhs"}]} | null,
                 "dpda": {...stack_strategy dpda format...} | null},
      "regular": {"dfa": {"states", "alphabet", "start", "accept",
                          "transitions": [{"from", "read", "to"}]} | null,
                  "regex": "(aa)*(bb)*" | null} | null,
      "homomorphism": [{"letter": "a", "image": "aa"}, ...] | null
    }

Exactly one of ``source.pattern`` / ``grammar`` / ``dpda`` is set.
``regular`` is required for ``reg_intersection`` and ``homomorphism`` for
``inv_homomorphism``; ``complement`` needs only ``source``.

The DFA is simulated with ``agent_system.lib.dfa_runner.run_dfa`` (a missing
transition rejects, i.e. it goes to an implicit dead state); a regex is
compiled with ``agent_system.lib.dfa_builder.build_dfa_from_regex`` (literal
letters, ``|``, ``*``, ``+``, ``?``, parentheses).

Two kinds of failure are kept apart:

- ``ArtifactMissing``: the part the operation needs is absent. Like a missing
  DPDA, this leaves trust at ``well_formed``.
- ``ArtifactError``: the part is present but is not a valid automaton, homomorphism
  or language description (unknown state, two transitions on one letter, a letter
  with no image, two source descriptions at once, a ``dpda`` that is not
  deterministic). Like an invalid DPDA, this is ``refuted``.

A source given as a pattern or grammar that the oracle layer cannot parse is
neither: the artifact may be right, but nothing can check it, so the caller
leaves trust at ``well_formed``, as for a task without an oracle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent_system.lib.dfa_builder import build_dfa_from_regex
from agent_system.lib.dfa_runner import run_dfa
from dcfl_system.lib.closure_table import get_required_artifact
from dcfl_system.lib.dpda import dpda_run, prepare_dpda
from dcfl_system.lib.word_sampler import build_membership_oracle_from_ir

Oracle = Callable[[str], "bool | None"]

# What each artifact part is, for the "missing" message (the parts each
# operation needs come from closure_table.CLOSURE_TABLE's `artifact` field).
_PART_MEANING = {
    "source": "the known language S, as a pattern, grammar or dpda",
    "regular": "a DFA or a regex for R",
    "homomorphism": "the image of every letter",
}

# The structured-output contract's spelling, plus closure_table.py's own name
# for the same operation.
_OPERATION_ALIASES = {
    "complement": "complement",
    "reg_intersection": "reg_intersection",
    "inv_homomorphism": "inv_homomorphism",
    "inverse_homomorphism": "inv_homomorphism",
}


class ArtifactMissing(ValueError):
    """The part of the artifact the operation needs is absent."""


class ArtifactError(ValueError):
    """The artifact is present but malformed: a definite defect (refuted)."""


def normalize_operation(operation: Any) -> str | None:
    """Canonical operation name, or ``None`` for an operation with no artifact check."""
    return _OPERATION_ALIASES.get(operation) if isinstance(operation, str) else None


def _letters(raw: Any, what: str) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ArtifactError(f"{what} must be a non-empty list of letters")
    out: list[str] = []
    for ch in raw:
        if not isinstance(ch, str) or len(ch) != 1:
            raise ArtifactError(f"{what}: {ch!r} is not a single letter")
        if ch not in out:
            out.append(ch)
    return out


# ---------------------------------------------------------------------------
# Regular language R
# ---------------------------------------------------------------------------

def build_regular(regular: Any) -> tuple[Callable[[str], bool], list[str], str]:
    """``(accepts, alphabet, kind)`` for ``artifact.regular``.

    Raises ``ArtifactMissing`` if neither ``dfa`` nor ``regex`` is given,
    ``ArtifactError`` if both are given or the given one is malformed.
    """
    if not isinstance(regular, dict):
        raise ArtifactMissing("reg_intersection needs artifact.regular (a DFA or a regex for R)")
    dfa_raw, regex = regular.get("dfa"), regular.get("regex")
    if dfa_raw is None and regex in (None, ""):
        raise ArtifactMissing("artifact.regular has neither a dfa nor a regex")
    if dfa_raw is not None and regex not in (None, ""):
        raise ArtifactError("artifact.regular gives both a dfa and a regex; give exactly one")
    if dfa_raw is not None:
        dfa = _dfa_from_transition_list(dfa_raw)
        kind = "dfa"
    else:
        if not isinstance(regex, str):
            raise ArtifactError("artifact.regular.regex must be a string")
        try:
            dfa = build_dfa_from_regex(regex)
        except Exception as exc:  # the regex parser raises plain ValueError/IndexError
            raise ArtifactError(f"artifact.regular.regex {regex!r} does not parse: {exc}") from exc
        kind = "regex"
    return (lambda word: run_dfa(dfa, word)), list(dfa["alphabet"]), kind


def _dfa_from_transition_list(raw: Any) -> dict[str, Any]:
    """The artifact's DFA (``transitions`` as an array of ``{from, read, to}``,
    the form a closed output schema can describe) -> the
    ``agent_system.lib.dfa_runner`` dict format. Missing transitions are
    allowed (implicit dead state); two different targets for one
    ``(from, read)`` are not: that automaton is not deterministic."""
    if not isinstance(raw, dict):
        raise ArtifactError("artifact.regular.dfa must be an object")
    states = raw.get("states")
    if not isinstance(states, list) or not states or not all(isinstance(s, str) for s in states):
        raise ArtifactError("dfa.states must be a non-empty list of state names")
    state_set = set(states)
    alphabet = _letters(raw.get("alphabet"), "dfa.alphabet")
    start = raw.get("start")
    if start not in state_set:
        raise ArtifactError(f"dfa.start {start!r} is not one of dfa.states")
    accept = raw.get("accept")
    if not isinstance(accept, list) or any(a not in state_set for a in accept):
        raise ArtifactError("dfa.accept must be a list of states from dfa.states")
    transitions = raw.get("transitions")
    if not isinstance(transitions, list):
        raise ArtifactError("dfa.transitions must be a list of {from, read, to}")
    table: dict[str, dict[str, str]] = {s: {} for s in states}
    for t in transitions:
        if not isinstance(t, dict):
            raise ArtifactError(f"dfa transition {t!r} is not an object")
        src, letter, dst = t.get("from"), t.get("read"), t.get("to")
        if src not in state_set or dst not in state_set:
            raise ArtifactError(f"dfa transition {t!r} uses a state outside dfa.states")
        if letter not in alphabet:
            raise ArtifactError(f"dfa transition {t!r} reads {letter!r}, not a letter of dfa.alphabet")
        previous = table[src].get(letter)
        if previous is not None and previous != dst:
            raise ArtifactError(
                f"dfa is not deterministic: ({src}, {letter}) goes to both {previous} and {dst}"
            )
        table[src][letter] = dst
    return {
        "states": list(states), "alphabet": alphabet, "transitions": table,
        "start": start, "accept": list(accept),
    }


# ---------------------------------------------------------------------------
# Homomorphism h
# ---------------------------------------------------------------------------

def build_homomorphism(raw: Any, domain: list[str]) -> dict[str, str]:
    """``{letter: image}`` for ``artifact.homomorphism``, total on ``domain``.
    An empty image (erasing) is allowed."""
    if raw is None:
        raise ArtifactMissing("inv_homomorphism needs artifact.homomorphism (the image of every letter)")
    if not isinstance(raw, list) or not raw:
        raise ArtifactError("artifact.homomorphism must be a non-empty list of {letter, image}")
    h: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise ArtifactError(f"homomorphism entry {entry!r} is not an object")
        letter, image = entry.get("letter"), entry.get("image")
        if not isinstance(letter, str) or len(letter) != 1:
            raise ArtifactError(f"homomorphism entry {entry!r}: letter must be a single letter")
        if image in ("ε", "eps"):
            image = ""
        if not isinstance(image, str):
            raise ArtifactError(f"homomorphism entry {entry!r}: image must be a string")
        if letter in h and h[letter] != image:
            raise ArtifactError(f"homomorphism maps {letter!r} to both {h[letter]!r} and {image!r}")
        h[letter] = image
    missing = [ch for ch in domain if ch not in h]
    if missing:
        raise ArtifactError(
            "homomorphism is not defined on letter(s) " + ", ".join(repr(c) for c in missing)
            + " of its domain alphabet"
        )
    return h


def apply_homomorphism(h: dict[str, str], word: str) -> str:
    return "".join(h[ch] for ch in word)


# ---------------------------------------------------------------------------
# Source language S
# ---------------------------------------------------------------------------

@dataclass
class Source:
    oracle: Oracle | None          # None: given, but the oracle layer cannot parse it
    alphabet: list[str]
    kind: str                      # "pattern" / "grammar" / "dpda"
    details: dict[str, Any] = field(default_factory=dict)


def build_source(raw: Any, task_alphabet: list[str], direction: str) -> Source:
    """The source language's membership oracle. ``source.alphabet``
    defaults to the task alphabet; a letter outside it is never in S."""
    if not isinstance(raw, dict):
        raise ArtifactMissing("artifact.source is missing (the known language, as pattern/grammar/dpda)")
    given = [k for k in ("pattern", "grammar", "dpda") if raw.get(k) not in (None, "")]
    if not given:
        raise ArtifactMissing("artifact.source has no pattern, grammar or dpda")
    if len(given) > 1:
        raise ArtifactError(f"artifact.source gives {', '.join(given)}; give exactly one")
    alphabet = (
        _letters(raw.get("alphabet"), "source.alphabet")
        if raw.get("alphabet") is not None else list(task_alphabet)
    )
    kind = given[0]

    if kind == "dpda":
        if direction == "destructive":
            # A deterministic automaton for S would itself show S is a DCFL,
            # the opposite of what a destructive reduction needs.
            raise ArtifactError(
                "artifact.source.dpda in a destructive reduction: the known language must be "
                "non-DCFL, so give it as a pattern or a grammar"
            )
        dpda, details, problem = prepare_dpda(raw["dpda"])
        if dpda is None:
            raise ArtifactError(f"artifact.source.{problem}")
        return Source(_guarded(lambda w: dpda_run(dpda, w), alphabet), alphabet, kind, details)

    if kind == "pattern":
        if not isinstance(raw["pattern"], str):
            raise ArtifactError("artifact.source.pattern must be a string")
        ir = {
            "input_format": "set_builder", "alphabet": alphabet,
            "language_spec": {"kind": "set_builder", "word_pattern": raw["pattern"]},
        }
    else:
        grammar = raw["grammar"]
        if not isinstance(grammar, dict) or not isinstance(grammar.get("rules"), list):
            raise ArtifactError("artifact.source.grammar must be {start, rules: [{lhs, rhs}]}")
        rules = grammar["rules"]
        for r in rules:
            if not (isinstance(r, dict) and isinstance(r.get("lhs"), str)
                    and isinstance(r.get("rhs"), list)
                    and all(isinstance(s, str) for s in r["rhs"])):
                raise ArtifactError(f"artifact.source.grammar rule {r!r} is not {{lhs, rhs: [symbols]}}")
        ir = {
            "input_format": "grammar", "alphabet": alphabet,
            "language_spec": {
                "kind": "grammar", "start": grammar.get("start"), "rules": rules,
                "terminals": alphabet,
                "nonterminals": sorted({r["lhs"] for r in rules}),
            },
        }
    oracle = build_membership_oracle_from_ir(ir)
    return Source(_guarded(oracle, alphabet) if oracle is not None else None, alphabet, kind)


def _guarded(oracle: Oracle, alphabet: list[str]) -> Oracle:
    """``oracle``, answering ``False`` for a word with a letter outside ``alphabet``."""
    letters = set(alphabet)

    def guarded(word: str) -> bool | None:
        if any(ch not in letters for ch in word):
            return False
        return oracle(word)

    return guarded


def guarded_task_oracle(oracle: Oracle, alphabet: list[str]) -> Oracle:
    """The task's oracle with the same convention: L ⊆ Σ*."""
    return _guarded(oracle, alphabet)


# ---------------------------------------------------------------------------
# The claimed equality, as two predicates over one domain alphabet
# ---------------------------------------------------------------------------

@dataclass
class Equation:
    """``left(w) == right(w)`` for every word ``w`` over ``domain`` is the
    proof's claim; ``text`` states it for the report."""
    domain: list[str]
    left: Callable[[str], "bool | None"]
    right: Callable[[str], "bool | None"]
    left_name: str
    right_name: str
    text: str
    with_samples: bool          # the domain contains Σ, so the task sampler's words apply
    checkable: bool             # False: the source is given, but no oracle can be built for it
    details: dict[str, Any] = field(default_factory=dict)


def _both(a: bool | None, b: Callable[[], "bool | None"]) -> bool | None:
    """Three-valued AND with short-circuit (``False`` wins over unknown)."""
    if a is False:
        return False
    vb = b()
    if vb is False:
        return False
    if a is None or vb is None:
        return None
    return True


def _negate(a: bool | None) -> bool | None:
    return None if a is None else not a


def build_equation(
    artifact: Any, operation: str, direction: str,
    task_oracle: Oracle, task_alphabet: list[str],
) -> Equation:
    """The equality the proof claims, from its artifact. Raises
    ``ArtifactMissing`` / ``ArtifactError`` (see the module docstring)."""
    if not isinstance(artifact, dict):
        raise ArtifactMissing("proof_sketch.artifact is missing")
    for part in get_required_artifact(operation):
        if artifact.get(part) is None:
            raise ArtifactMissing(f"{operation} needs artifact.{part} ({_PART_MEANING[part]})")
    destructive = direction == "destructive"
    source = build_source(artifact.get("source"), task_alphabet, direction)
    in_l = guarded_task_oracle(task_oracle, task_alphabet)
    s = source.oracle
    checkable = s is not None
    details: dict[str, Any] = {"source_kind": source.kind, **source.details}

    def in_s(w: str) -> bool | None:
        return s(w) if s is not None else None

    def eq(domain, left, right, left_name, right_name, text, with_samples=True) -> Equation:
        return Equation(domain, left, right, left_name, right_name, text,
                        with_samples, checkable, details)

    if operation == "complement":
        if destructive:
            return eq(task_alphabet, lambda w: _negate(in_l(w)), in_s,
                      "in(Σ*∖L)", "in_S", "Σ* ∖ L = S")
        return eq(task_alphabet, in_l, lambda w: _negate(in_s(w)),
                  "in_L", "in(Σ*∖S)", "L = Σ* ∖ S")

    if operation == "reg_intersection":
        in_r, r_alphabet, r_kind = build_regular(artifact.get("regular"))
        details["regular_kind"] = r_kind
        # Words with letters outside Σ are enumerated too: L has none, so the
        # other side must have none either.
        domain = _union(task_alphabet, source.alphabet, r_alphabet)
        if destructive:
            return eq(domain, lambda w: _both(in_l(w), lambda: in_r(w)), in_s,
                      "in(L∩R)", "in_S", "L ∩ R = S")
        return eq(domain, in_l, lambda w: _both(in_r(w), lambda: in_s(w)),
                  "in_L", "in(S∩R)", "L = S ∩ R")

    if operation == "inv_homomorphism":
        if destructive:
            h = build_homomorphism(artifact.get("homomorphism"), source.alphabet)
            details["homomorphism"] = h
            # The domain is Δ, not Σ: the task sampler's words do not apply.
            return eq(source.alphabet, lambda w: in_l(apply_homomorphism(h, w)), in_s,
                      "in(h⁻¹(L))", "in_S", "h⁻¹(L) = S", with_samples=False)
        h = build_homomorphism(artifact.get("homomorphism"), task_alphabet)
        details["homomorphism"] = h
        return eq(task_alphabet, in_l, lambda w: in_s(apply_homomorphism(h, w)),
                  "in_L", "in(h⁻¹(S))", "L = h⁻¹(S)")

    raise ArtifactMissing(f"operation {operation!r} has no executable artifact check")


def _union(*alphabets: list[str]) -> list[str]:
    out: list[str] = []
    for alphabet in alphabets:
        for ch in alphabet:
            if ch not in out:
                out.append(ch)
    return out
