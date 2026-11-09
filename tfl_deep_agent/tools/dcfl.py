"""DCFL tools wrapped from ``dcfl_system.lib`` (P1, group ``dcfl``).

Four tools (inventory: ``tfl_deep_agent/docs/TOOL_INVENTORY.md`` §5):

* ``dpda_run`` -- the R2' steps that concern the automaton itself
  (docs/VERDICT_POLICY.md R2'): canonical normalization
  (``dpda.normalize_epsilon_accept_sinks``), the complete syntactic
  determinism check (``dpda.check_determinism``), the structural check
  (``dpda.to_cfl_pda``, compatibility only), and exact runs of the
  deterministic simulator (``dpda.dpda_run``) on the given words. The same
  order and the same verdicts as ``oracle_verifier._verify_stack_strategy_dpda``
  steps (0), (a) and the structure check; step (b) -- simulation against the
  task's own language oracle -- is NOT done here (that is ``dpda_vs_ir``).
  Trust: ``refuted`` when the automaton is not deterministic after
  normalization or is structurally invalid; otherwise ``verified`` -- for the
  property "deterministic" and for "this DPDA accepts / rejects w" only, never
  for "this DPDA recognises the task's language".
* ``dpda_vs_ir`` -- the whole R2' check: the steps above, then step (b), the
  deterministic simulator against the IR's membership oracle
  (``words.oracle_for``) on every word up to ``max_len_within(|Σ|, 3000, 10)``
  plus the class sampler's words. ``bounded_pass`` with at least 30 decided
  agreeing words, ``refuted`` on a counterexample (exact oracle only),
  otherwise ``well_formed``.
* ``closure_lookup`` -- DCFL closure facts: ``closure_table.CLOSURE_TABLE``
  (``is_closed`` / ``get_implication`` / ``get_proof_direction``) plus the
  operations that only docs/THEORY.md §1.4 lists, each with a verbatim quote
  of its statement; ``closure_table.closure_scan`` for an IR. Reference only:
  ``not_verified``.
* ``pattern_match`` -- ``pattern_db.match_patterns`` / ``get_pattern`` /
  ``list_patterns``. A heuristic hint (dcfl_system/CLAUDE.md: the classifier
  is ADVISORY ONLY): ``not_verified``.

Theory text: the closure statements are quoted from docs/THEORY.md §1.4,
never paraphrased; ``tests/test_tools_dcfl.py`` pins every quote against the
file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dcfl_system.lib import closure_table as _closure
from dcfl_system.lib import dpda as _dpda
from dcfl_system.lib import pattern_db as _patterns
from dcfl_system.lib.dcfl_ir_schema import validate_dcfl_ir
from tfl_deep_agent.tools.base import (
    Deadline,
    ToolArgs,
    ToolInputError,
    ToolResult,
    Word,
    tfl_tool,
)
from tfl_deep_agent.tools.ir import CLASS_DESCRIPTION, TFLClass, ir_fingerprint, require_valid
from tfl_deep_agent.tools.words import (
    compare_with_oracle,
    comparison_result,
    generator_candidates,
    oracle_for,
)

__all__ = [
    "CLOSURE_OPERATIONS",
    "DPDA_LIMITS",
    "PATTERN_CODES",
    "THEORY_CLOSURE_FACTS",
    "ClosureLookupArgs",
    "DPDASpec",
    "DPDATransition",
    "DpdaRunArgs",
    "DpdaVsIrArgs",
    "PatternMatchArgs",
    "closure_lookup",
    "dpda_run",
    "dpda_vs_ir",
    "pattern_match",
    "theory_section_1_4",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _digest(obj: Any) -> str:
    """Short, stable id of a JSON object (sorted keys), so a report can say
    which automaton / IR a result is about."""
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _require_language_spec(ir: dict[str, Any]) -> dict[str, Any]:
    spec = ir.get("language_spec")
    if not isinstance(spec, dict):
        raise ToolInputError(
            "ir.language_spec must be an object (a DCFL IR: input_format 'set_builder' or "
            "'grammar', see dcfl_system/tz_dcfl_agent_system.md)")
    return spec


def _ir_errors(ir: dict[str, Any]) -> list[str]:
    try:
        return list(validate_dcfl_ir(ir))
    except Exception as exc:  # noqa: BLE001 - the validator should not raise; be safe
        return [f"validate_dcfl_ir raised {type(exc).__name__}: {exc}"]


IR_DESCRIPTION = (
    "A DCFL task IR: {task_id?, source_text, input_format: 'set_builder'|'grammar', task_type, "
    "language_spec: {kind: 'set_builder', word_pattern, variables: [{name, domain, quantifier}], "
    "constraints: [{kind, args}]} or {kind: 'grammar', terminals, nonterminals, start, "
    "rules: [{lhs, rhs: [...]}]}, alphabet: [...]}. IRs of other classes are accepted, but "
    "their validation errors against the DCFL schema are reported.")


# ---------------------------------------------------------------------------
# dpda_run
# ---------------------------------------------------------------------------

DPDA_LIMITS = {
    "max_states": 300,
    "max_stack_symbols": 300,
    "max_transitions": 2000,
    "max_push": 16,
    "max_initial_stack": 8,
    "max_name_len": 64,
    "max_words": 200,
}

_EPSILON_SPELLINGS = {"", "ε", "eps", "epsilon"}


def _is_epsilon(read: Any) -> bool:
    """The epsilon spellings dcfl ``dpda._normalize_read`` maps to ``None``."""
    return read is None or (isinstance(read, str) and read.strip().lower() in _EPSILON_SPELLINGS)

Name = Annotated[str, Field(min_length=1, max_length=DPDA_LIMITS["max_name_len"])]


class DPDATransition(BaseModel):
    """One transition of the ``dpda`` contract (tz_dcfl_agent_system.md §5.2)."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True,
                              serialize_by_alias=True)

    from_: Name = Field(alias="from", description="source state")
    read: str | None = Field(
        default=None,
        description="the input letter read (one character), or null for an epsilon move "
                    "(\"\", \"ε\", \"eps\", \"epsilon\" also mean epsilon)")
    top: Name = Field(description="the stack top this transition requires; it is popped")
    to: Name = Field(description="target state")
    push: list[Name] = Field(
        default_factory=list, max_length=DPDA_LIMITS["max_push"],
        description="symbols pushed in place of the popped top, TOPMOST FIRST "
                    "([] pops; [\"A\", \"Z0\"] on top Z0 pushes A above Z0)")

    @field_validator("read")
    @classmethod
    def _one_letter_or_epsilon(cls, v: str | None) -> str | None:
        if _is_epsilon(v):
            return v
        if len(v) != 1:
            raise ValueError(
                f"read must be a single character or null (epsilon), got {v!r}; words are "
                "read one character at a time")
        return v


class DPDASpec(BaseModel):
    """The ``dpda`` field of a stack_strategy proof_sketch (R2')."""

    model_config = ConfigDict(extra="forbid")

    states: list[Name] = Field(min_length=1, max_length=DPDA_LIMITS["max_states"])
    start: Name = Field(description="start state")
    accept_states: list[Name] = Field(
        default_factory=list, max_length=DPDA_LIMITS["max_states"],
        description="accepting states (final-state acceptance, the default)")
    accept_mode: Literal["final_state", "empty_stack"] | None = Field(
        default=None, description="'final_state' (default when omitted) or 'empty_stack'")
    stack_alphabet: list[Name] = Field(min_length=1, max_length=DPDA_LIMITS["max_stack_symbols"])
    initial_stack: list[Name] = Field(
        min_length=1, max_length=DPDA_LIMITS["max_initial_stack"],
        description="initial stack, TOPMOST FIRST (usually one bottom marker, e.g. [\"Z0\"])")
    transitions: list[DPDATransition] = Field(max_length=DPDA_LIMITS["max_transitions"])
    accept_configs: list[tuple[Name, Name]] = Field(
        default_factory=list, max_length=DPDA_LIMITS["max_transitions"],
        description="optional accepting (state, stack_top) pairs; normally leave empty -- the "
                    "canonical normalization fills it")


class DpdaRunArgs(ToolArgs):
    dpda: DPDASpec = Field(description=(
        "The DPDA, in the stack_strategy `dpda` contract: {states, start, accept_states, "
        "accept_mode?, stack_alphabet, initial_stack (topmost first), transitions: "
        "[{from, read (one letter or null = epsilon), top, to, push (topmost first)}]}."))
    words: list[Word] = Field(
        default_factory=list, max_length=DPDA_LIMITS["max_words"],
        description="words to run (plain strings, one character per letter, \"\" = ε); "
                    "may be empty to check determinism only")


def _prepare_dpda(spec: DPDASpec) -> tuple[dict[str, Any], dict[str, Any]]:
    """R2' steps (0), (a) and the structure check on a submitted DPDA.
    Returns (normalized automaton, data with dpda_id / normalization /
    conflicts / structure_errors / input_letters)."""
    submitted = spec.model_dump(by_alias=True)
    if submitted.get("accept_mode") is None:
        submitted.pop("accept_mode", None)
    if not submitted.get("accept_configs"):
        submitted.pop("accept_configs", None)
    else:
        submitted["accept_configs"] = [list(c) for c in submitted["accept_configs"]]
    dpda_id = _digest(submitted)

    normalized, notes = _dpda.normalize_epsilon_accept_sinks(submitted)
    conflicts = _dpda.check_determinism(normalized)
    try:
        _dpda.to_cfl_pda(normalized)
        structure_errors: list[str] = []
    except _dpda.DPDAFormatError as exc:
        structure_errors = [str(exc)]

    letters = sorted({t["read"] for t in normalized.get("transitions", [])
                      if not _is_epsilon(t.get("read"))})
    data: dict[str, Any] = {
        "dpda_id": dpda_id,
        "normalization": notes,
        "accept_configs": normalized.get("accept_configs", []),
        "deterministic": not conflicts,
        "conflicts": conflicts,
        "structure_errors": structure_errors,
        "input_letters": letters,
        "runs": [],
    }
    return normalized, data


def _not_a_dpda_result(data: dict[str, Any], what: str) -> ToolResult:
    why = []
    if data["conflicts"]:
        why.append(f"not deterministic after canonical normalization ({len(data['conflicts'])} "
                   "conflict(s); the determinism check is complete)")
    if data["structure_errors"]:
        why.append("structurally invalid: " + "; ".join(data["structure_errors"]))
    return ToolResult.success(
        data, trust="refuted",
        reason="R2': the automaton is " + " and ".join(why)
               + f"; {what} (a simulation of a non-DPDA is not meaningful)")


@tfl_tool(group="dcfl", max_trust="verified", timeout_s=30)
def dpda_run(args: DpdaRunArgs) -> ToolResult:
    """Check a DPDA and run words through it (docs/VERDICT_POLICY.md R2', automaton part).

    Steps, in the pipeline's order: (0) the canonical, language-preserving
    normalization (an epsilon move into an accepting sink state with no outgoing
    transitions becomes an accepting (state, top) configuration; listed in
    `normalization`); (a) the complete syntactic determinism check; the
    structural check (undeclared states / stack symbols, missing fields); then
    exact deterministic runs of each word (step budget 10000 per word; null =
    budget exhausted, unknown).

    Trust: `refuted` if the automaton is not deterministic after normalization or is
    structurally invalid (words are then not run); otherwise `verified` for
    "this automaton is deterministic" and for each accept/reject answer ON THIS
    AUTOMATON. It says nothing about whether the DPDA recognises the task's
    language: compare against the language oracle (dpda_vs_ir / check_membership)
    for that."""
    normalized, data = _prepare_dpda(args.dpda)
    letters = data["input_letters"]
    if data["conflicts"] or data["structure_errors"]:
        data["not_run"] = list(args.words)
        return _not_a_dpda_result(data, "words were not run")

    deadline = Deadline(25.0)
    unknown_letters = sorted({ch for w in args.words for ch in w} - set(letters))
    runs: list[dict[str, Any]] = []
    for w in args.words:
        deadline.check("dpda_run")
        try:
            accepted = _dpda.dpda_run(normalized, w)
        except _dpda.DPDAFormatError as exc:
            raise ToolInputError(f"the DPDA cannot be simulated: {exc}") from None
        runs.append({"word": w, "accepted": accepted})
    data["runs"] = runs
    data["accepted"] = [r["word"] for r in runs if r["accepted"] is True]
    data["rejected"] = [r["word"] for r in runs if r["accepted"] is False]
    data["unknown"] = [r["word"] for r in runs if r["accepted"] is None]
    if unknown_letters:
        data["warnings"] = [(
            f"letters {unknown_letters} occur in the words but in no transition; words with "
            "them are rejected as soon as they are read")]

    reason = ("R2': deterministic after canonical normalization (a complete syntactic check) "
              "and structurally valid")
    if runs:
        reason += (f"; {len(runs) - len(data['unknown'])} word(s) run exactly on THIS automaton "
                   "(not a check against the task language)")
    if data["unknown"]:
        reason += (f"; {len(data['unknown'])} word(s) unknown: the 10000-step budget ran out "
                   "(an epsilon cycle)")
    return ToolResult.success(data, trust="verified", reason=reason)


# ---------------------------------------------------------------------------
# dpda_vs_ir (R2', simulation step (b))
# ---------------------------------------------------------------------------

#: Longest sampled word (dcfl ``oracle_verifier._DPDA_MAX_WORD_LEN``).
DPDA_SAMPLE_MAX_LEN = 10


class DpdaVsIrArgs(ToolArgs):
    dpda: DPDASpec = Field(description=(
        "The DPDA, in the stack_strategy `dpda` contract (as for dpda_run): {states, start, "
        "accept_states, accept_mode?, stack_alphabet, initial_stack (topmost first), transitions: "
        "[{from, read (one letter or null = epsilon), top, to, push (topmost first)}]}."))
    ir: dict[str, Any] = Field(description="the task IR; its membership oracle is the reference "
                                           "(results are relative to this IR)")
    cls: TFLClass = Field(default="dcfl", description=CLASS_DESCRIPTION)
    time_budget_s: float = Field(default=45.0, gt=0, le=50,
                                 description="stop comparing after this many seconds (partial result)")


@tfl_tool(group="dcfl", max_trust="bounded_pass", timeout_s=60)
def dpda_vs_ir(args: DpdaVsIrArgs) -> ToolResult:
    """The full R2' check of a DPDA against the task language (docs/VERDICT_POLICY.md R2'):
    canonical normalization, the complete determinism check and the structure check (as in
    dpda_run), then the deterministic simulator against the IR's membership oracle on EVERY word
    over the IR's alphabet up to the length k where Σ_{i≤k}|Σ|^i ≤ 3000 (k ≤ 10), plus the
    class word sampler's words up to length 10. Trust: refuted when the automaton is not
    deterministic or structurally invalid, or when it disagrees with the (exact) oracle on a
    decided word (counterexamples in data); bounded_pass when at least 30 decided words agree
    (bounds: the fully enumerated length, decided / undecided words); well_formed for fewer words
    or an approximate oracle. A bounded check, never a proof that the DPDA recognises the
    language. Unsupported when the IR has no oracle."""
    ir = require_valid(args.cls, args.ir)
    oracle = oracle_for(args.cls, ir, validated=True)
    normalized, data = _prepare_dpda(args.dpda)
    fp = ir_fingerprint(ir)
    data.update({"ir_fingerprint": fp, "class": args.cls, "evidence_role": "artifact_check",
                 "oracle": {**oracle.describe(), "class": oracle.cls}})
    del data["runs"]
    if data["conflicts"] or data["structure_errors"]:
        return _not_a_dpda_result(data, "it was not compared with the oracle")

    def run(word: str) -> bool | None:
        try:
            return _dpda.dpda_run(normalized, word)
        except _dpda.DPDAFormatError:
            return None

    extra, notes = generator_candidates(args.cls, ir, oracle.alphabet, max_len=DPDA_SAMPLE_MAX_LEN)
    outside = sorted(set(data["input_letters"]) - set(oracle.alphabet))
    if outside:
        notes.append(f"letters {outside} are read by the DPDA but are not in the IR's alphabet "
                     f"{list(oracle.alphabet)}; words are enumerated over the IR's alphabet only")
    cmp = compare_with_oracle(oracle, run, deadline=Deadline(args.time_budget_s), extra_words=extra)
    data["comparison"] = cmp
    data["notes"] = notes
    return comparison_result(cmp, oracle, data, subject="the DPDA", fingerprint=fp)


# ---------------------------------------------------------------------------
# closure_lookup
# ---------------------------------------------------------------------------

THEORY_PATH = Path(__file__).resolve().parents[2] / "docs" / "THEORY.md"
THEORY_SECTION = "1.4"
THEORY_SECTION_STATUS = "[источники и границы указаны отдельно]"

_NOT_CLOSED_QUOTE = ("Не замкнуты: ∪, ∩ (DCFL ∩ DCFL), конкатенация, звезда, обращение, "
                     "гомоморфизм (в т.ч. ε-свободный).")

#: Closure facts quoted verbatim from docs/THEORY.md §1.4 (whitespace and
#: markdown emphasis aside; pinned by tests/test_tools_dcfl.py). The first
#: eight operations are also in ``closure_table.CLOSURE_TABLE`` and must agree
#: with it; the others exist only in THEORY.md.
THEORY_CLOSURE_FACTS: dict[str, dict[str, Any]] = {
    "complement": {"closed": True, "quote": "Замкнуты: дополнение относительно фиксированного Σ*"},
    "reg_intersection": {"closed": True,
                         "quote": "∩ REG следует из произведения ДМПА и ДКА"},
    "inverse_homomorphism": {"closed": True,
                             "quote": "обратный гомоморфизм (включая стирающий)"},
    "union": {"closed": False, "quote": _NOT_CLOSED_QUOTE},
    "concatenation": {"closed": False, "quote": _NOT_CLOSED_QUOTE},
    "kleene_star": {"closed": False, "quote": _NOT_CLOSED_QUOTE},
    "reversal": {"closed": False, "quote": _NOT_CLOSED_QUOTE},
    "homomorphism": {"closed": False, "quote": _NOT_CLOSED_QUOTE},
    # THEORY.md §1.4 only:
    "intersection": {"closed": False, "quote": _NOT_CLOSED_QUOTE,
                     "note": "DCFL ∩ DCFL; for DCFL ∩ REG see reg_intersection"},
    "difference": {"closed": False,
                   "quote": "Это не применение замкнутости под разностью двух DCFL — "
                            "такой замкнутости нет."},
    "right_quotient_regular": {"closed": True,
                               "quote": "правое частное L/R = {x | ∃y ∈ R: xy ∈ L} при регулярном R"},
    "min": {"closed": True, "quote": "MIN(L) = {x ∈ L | ни один собственный префикс x не лежит в L}"},
    "max": {"closed": True,
            "quote": "MAX(L) = {x ∈ L | ни одно собственное продолжение x не лежит в L}"},
    "inverse_deterministic_gsm": {
        "closed": True,
        "quote": "обратное отображение детерминированного последовательного GSM, в том числе "
                 "со стирающим выходом"},
    "inverse_nondeterministic_transducer": {
        "closed": False,
        "quote": "Для недетерминированного многозначного трансдьюсера это утверждение об обратном "
                 "образе не переносится"},
    "haspref": {"closed": True, "quote": "haspref и L_$ (п. 1.3)"},
    "marked_continuation": {"closed": True, "quote": "haspref и L_$ (п. 1.3)",
                            "note": "L_$ = {x$y | x∈L, xy∈L}, THEORY.md §1.3"},
    "left_quotient_word": {"closed": True,
                           "quote": "Левое частное на фиксированное слово также сохраняет DCFL"},
    "left_quotient_regular": {
        "closed": False,
        "quote": "Левое частное на произвольный регулярный язык в общем случае не сохраняет DCFL"},
}

CLOSURE_OPERATIONS: tuple[str, ...] = tuple(THEORY_CLOSURE_FACTS)
ClosureOperation = Literal[
    "complement", "reg_intersection", "inverse_homomorphism", "union", "concatenation",
    "kleene_star", "reversal", "homomorphism", "intersection", "difference",
    "right_quotient_regular", "min", "max", "inverse_deterministic_gsm",
    "inverse_nondeterministic_transducer", "haspref", "marked_continuation",
    "left_quotient_word", "left_quotient_regular",
]


def theory_section_1_4(path: Path = THEORY_PATH) -> dict[str, Any] | None:
    """The verbatim text of docs/THEORY.md §1.4 with its 1-based line range,
    or ``None`` when the file is not there (e.g. an installed wheel)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"### {THEORY_SECTION}.")), None)
    if start is None:
        return None
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("#")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return {"text": "\n".join(lines[start:end]), "lines": [start + 1, end]}


def _theory_ref() -> dict[str, Any]:
    return {"file": "docs/THEORY.md", "section": THEORY_SECTION, "status": THEORY_SECTION_STATUS}


def _closure_entry(op: str) -> dict[str, Any]:
    fact = THEORY_CLOSURE_FACTS[op]
    entry: dict[str, Any] = {"operation": op, "closed": fact["closed"],
                             "theory_quote": fact["quote"], "source": "docs/THEORY.md §1.4"}
    if "note" in fact:
        entry["note"] = fact["note"]
    try:
        lib_closed = _closure.is_closed(op)
    except KeyError:
        entry["in_lib_table"] = False
        return entry
    if lib_closed != fact["closed"]:  # the two sources disagree: never guess
        raise ToolInputError(
            f"closure_table and docs/THEORY.md §1.4 disagree on {op!r}; refusing to answer")
    lib_entry = next(e for e in _closure.CLOSURE_TABLE if e["operation"] == op)
    entry.update({
        "in_lib_table": True,
        "symbol": lib_entry.get("symbol"),
        "implication_if_closed": _closure.get_implication(op),
        "proof_direction": _closure.get_proof_direction(op),
    })
    if lib_entry.get("note"):
        entry["lib_note"] = lib_entry["note"]
    return entry


class ClosureLookupArgs(ToolArgs):
    operation: ClosureOperation | None = Field(
        default=None,
        description="one operation to look up; omit (and omit ir) to get the whole table. "
                    "'intersection' is DCFL ∩ DCFL, 'reg_intersection' is DCFL ∩ REG, "
                    "'marked_continuation' is L_$ (THEORY.md §1.3)")
    ir: dict[str, Any] | None = Field(
        default=None,
        description="optional DCFL IR: also scan it for closure-based proof strategies "
                    "(closure_scan; heuristic suggestions only). " + IR_DESCRIPTION)
    include_theory_text: bool = Field(
        default=False, description="also return the verbatim text of docs/THEORY.md §1.4")


@tfl_tool(group="dcfl", max_trust="not_verified", timeout_s=10, allow_refuted=False)
def closure_lookup(args: ClosureLookupArgs) -> ToolResult:
    """DCFL closure properties (reference, not evidence).

    With `operation`: whether DCFL is closed under it, the verbatim statement from
    docs/THEORY.md §1.4, and (for the table operations) the implication and proof
    direction from dcfl_system's closure table. Without `operation` and `ir`: the whole
    table. With `ir`: closure-based proof strategies suggested for that language
    (heuristic weights, not confidences). Trust is always `not_verified`: cite
    docs/THEORY.md §1.4 in a proof, and back any reduction with a checked artifact."""
    data: dict[str, Any] = {"theory_ref": _theory_ref()}
    if args.operation is not None:
        data["entry"] = _closure_entry(args.operation)
    if args.operation is None and args.ir is None:
        data["table"] = [_closure_entry(op) for op in CLOSURE_OPERATIONS]
    if args.ir is not None:
        _require_language_spec(args.ir)
        try:
            scan = _closure.closure_scan(args.ir)
        except (TypeError, AttributeError, ValueError) as exc:
            raise ToolInputError(
                f"closure_scan could not read this IR ({type(exc).__name__}: {exc}); "
                "use the DCFL IR format") from None
        data["ir_id"] = _digest(args.ir)
        data["ir_errors"] = _ir_errors(args.ir)
        data["scan"] = {
            "applicable_strategies": [
                {"operation": s.get("operation"), "direction": s.get("direction"),
                 "description": s.get("description"),
                 "heuristic_weight": s.get("confidence")}
                for s in scan.get("applicable_strategies", [])],
            "complement_structure": scan.get("complement_structure"),
            "warnings": scan.get("warnings", []),
        }
    if args.include_theory_text:
        section = theory_section_1_4()
        data["theory_text"] = section["text"] if section else None
        if section:
            data["theory_ref"]["lines"] = section["lines"]
    return ToolResult.success(
        data, trust="not_verified",
        reason="reference lookup (closure table + docs/THEORY.md §1.4); a closure fact is a "
               "theorem to cite, not evidence about this language")


# ---------------------------------------------------------------------------
# pattern_match
# ---------------------------------------------------------------------------

PATTERN_CODES: tuple[str, ...] = tuple(p["pattern"] for p in _patterns.list_patterns())
PatternCode = Literal[
    "single_palindrome_with_separator", "single_palindrome_no_separator",
    "nested_palindromes_with_separator", "nested_palindromes_no_separator",
    "single_count_match", "single_length_cmp", "dual_length_cmp_compatible",
    "dual_length_cmp_conflicting", "shared_var_disjunction", "regex_constrained_palindrome",
    "count_with_regex_body", "disjunction_count_parts", "triple_count", "complement_of_known",
    "reg_intersection", "grammar_lr_like", "grammar_ambiguous_union", "inverse_homomorphism",
    "reversal_of_dcfl",
]

_ADVISORY = ("advisory hint from a heuristic pattern database (dcfl_system/CLAUDE.md: the "
             "classifier is ADVISORY ONLY); suggested verdicts can be wrong and must be proved")


def _pattern_view(entry: dict[str, Any]) -> dict[str, Any]:
    return {"pattern": entry["pattern"], "description": entry.get("description"),
            "suggested_verdict": entry.get("verdict"), "suggested_method": entry.get("method"),
            "example": entry.get("example")}


class PatternMatchArgs(ToolArgs):
    ir: dict[str, Any] | None = Field(
        default=None, description="the DCFL IR to match against the pattern database. "
                                  + IR_DESCRIPTION)
    pattern: PatternCode | None = Field(
        default=None, description="look up one pattern by its code instead")
    max_results: int = Field(default=10, ge=1, le=20,
                             description="at most this many matches, best first")


@tfl_tool(group="dcfl", max_trust="not_verified", timeout_s=10, allow_refuted=False)
def pattern_match(args: PatternMatchArgs) -> ToolResult:
    """Match a DCFL IR against known structural patterns (heuristic hint, not evidence).

    With `ir`: ranked matches, each with a heuristic score, a suggested verdict
    (dcfl / non_dcfl), a suggested proof method and the reason it matched. With
    `pattern`: that pattern's entry. With neither: the whole database. Trust is
    always `not_verified`: a match only suggests where to start. The pattern rules
    are crude (for example they look at exponent notation in the text) and can
    suggest the wrong class; every verdict still needs a checked proof."""
    data: dict[str, Any] = {"advisory": _ADVISORY}
    if args.pattern is not None:
        entry = _patterns.get_pattern(args.pattern)
        if entry is None:  # the Literal is kept in sync with the DB by a test
            raise ToolInputError(f"unknown pattern {args.pattern!r}; known: {list(PATTERN_CODES)}")
        data["entry"] = _pattern_view(entry)
    if args.ir is not None:
        _require_language_spec(args.ir)
        try:
            hits = _patterns.match_patterns(args.ir)
        except (TypeError, AttributeError, ValueError) as exc:
            raise ToolInputError(
                f"pattern_db could not read this IR ({type(exc).__name__}: {exc}); it expects "
                "the DCFL IR format (string variable domains such as 'a+')") from None
        data["ir_id"] = _digest(args.ir)
        data["ir_errors"] = _ir_errors(args.ir)
        data["matches"] = [
            {**_pattern_view(_patterns.get_pattern(h["pattern"]) or {"pattern": h["pattern"]}),
             "score": h.get("score"), "reason": h.get("reason")}
            for h in hits[: args.max_results]]
        data["n_matches"] = len(hits)
    if args.ir is None and args.pattern is None:
        data["patterns"] = [_pattern_view(p) for p in _patterns.list_patterns()]
    return ToolResult.success(data, trust="not_verified", reason=_ADVISORY)


def _check_literals_in_sync() -> None:
    """Import-time guard: the schema enums must match the lib tables."""
    from typing import get_args
    if set(get_args(PatternCode)) != set(PATTERN_CODES):
        raise RuntimeError("tools/dcfl.py PatternCode is out of sync with pattern_db.PATTERN_DB")
    if set(get_args(ClosureOperation)) != set(CLOSURE_OPERATIONS):
        raise RuntimeError("tools/dcfl.py ClosureOperation is out of sync with THEORY_CLOSURE_FACTS")
    lib_ops = {e["operation"] for e in _closure.CLOSURE_TABLE}
    if not lib_ops <= set(CLOSURE_OPERATIONS):
        raise RuntimeError(f"closure_table has operations unknown to tools/dcfl.py: "
                           f"{sorted(lib_ops - set(CLOSURE_OPERATIONS))}")


_check_literals_in_sync()
