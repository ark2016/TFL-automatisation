"""Executable LL(k) parse-table artifact check (docs/VERDICT_POLICY.md R3′ (1)).

A constructive claim "the grammar / the language is LL(k)" carries the LL(k)
parse table it relies on. The pipeline runs that table as a table-driven
predictive parser (``ll_system.lib.ll_parser.LLParser.from_table``) on every
word over the task alphabet up to a bounded length and compares each answer
with the task's exact membership oracle (``word_oracle`` / CYK), the same way
the DPDA of dcfl ``stack_strategy`` is checked (R2′):

The table must be the strong LL(k) director table of its grammar G: the
claimed grammar, or, when the claim carries none (a marker claim), G_T, the
grammar whose rules are the table's own productions. An incomplete table is
not an LL(k) certificate: omitting a cell encodes a priority choice, and a
priority parser can recognize a language that is not LL. Example, k = 1:
S: a -> aSB, b -> ε, $ -> ε; B: b -> b, $ -> ε has no conflict cell, halts,
and accepts exactly {a^i b^j | i >= j}, a DCFL that is not LL
(docs/THEORY.md §3.4); its G_T (S -> aSB | ε, B -> b | ε) is not LL(1):
the director cell (B, b) holds both B -> b and B -> ε, and the table keeps
only the first. The comparison is made on the reduced grammar
(``remove_useless_symbols``): rows of the nonterminals it removes are
ignored; every non-empty director-table cell must be in the table with the
same right-hand side. A table cell that the director table leaves empty is
ignored (listed in ``details.director_table.ignored_extra_cells``): every
expansion is a rule of G, so the parser accepts only words of L(G); on a
word of L(G) the unique leftmost derivation (G is strong LL(k), hence
unambiguous) uses at each step a rule whose director set holds the current
lookahead, a non-empty cell that the table matches; so the parser
recognizes exactly L(G) whatever the extra cells hold (the usual
"ε-production as default" table, or a FOLLOW set computed over rules of
unreachable nonterminals).

- no table (absent, or with no cells)                  -> ``missing``
  (the caller caps the claim at ``well_formed``, like a missing DPDA);
- the claimed grammar, reduced, is not strong LL(k) for the table's k
                                                       -> ``not_applicable``
  (the table is not run, the caller caps the claim at ``well_formed``: if
  the grammar is LL(k), a table built from its rules rejects at least one
  word of L(G), possibly longer than the enumeration bound, so the artifact
  can neither certify nor refute the claim; a grammar that is not LL(k) is
  refuted by the caller's ``check_ll_k`` when the claim names k, and is
  capped here, conservatively, when it does not);
- malformed table, a table inconsistent with the claimed grammar (a row
  that is not one of its nonterminals, another start symbol, a production
  that is not one of its rules), a symbol used both as terminal and
  nonterminal (or "$" used as a symbol), a conflict cell (two different
  right-hand sides in one (nonterminal, lookahead) cell), a table for a
  larger k than claimed, a cell that contradicts the director table (the
  director cell is non-empty and the table holds another right-hand side
  there), a word on
  which the table-driven parser never halts (``LLParser.parse`` detects the
  loop exactly; a correct LL(k) parser halts on every input), or a word on
  which parser and oracle disagree                     -> ``refuted``;
- every word decided by the oracle was decided by the parser, at least
  ``PARSE_TABLE_MIN_WORDS`` of them, all agree, and the table is the
  director table of its grammar, which is strong LL(k) -> ``bounded_pass``
  (confidence cap 0.85, as for the DPDA);
- otherwise (no oracle, no alphabet, unknown k, too few decided words, a
  word on which the parser was still running after
  ``PARSE_TABLE_MAX_STEPS`` steps without a loop, a director-table cell the
  table omits, or G_T not strong LL(k))                -> ``well_formed``.

Table formats accepted (both appear in prompts):

- nested dict ``{A: {lookahead: rhs}}`` -- ``rhs`` a list of symbols
  (``[]`` or ``["ε"]`` = ε); a conflict cell is written as a list of right-hand
  sides ``{A: {lookahead: [rhs1, rhs2]}}``; optionally wrapped as
  ``{"k": 1, "start": "S", "table": {...}}``;
- entry list ``{"k": 1, "start": "S", "entries": [{"nonterminal": A,
  "lookahead": x, "rhs": [...]}, ...]}`` (closed structured-output schemas
  cannot express the dict keyed by the grammar's own symbols); two entries
  with the same (A, x) and different right-hand sides are a conflict cell.

A lookahead is a string over the task alphabet of length at most k; "$"
pads the end of input (FOLLOW_k is seeded with "$"^k, docs/THEORY.md §3.1),
and a string shorter than k means the input ends after it, so "a" for k = 2
is the cell "a$", and "" / "ε" is "$"^k. The table is one global
(nonterminal, lookahead) -> rhs map, i.e. a strong LL(k) table; every LL(k)
language has a strong LL(k) grammar (docs/THEORY.md §3.1, [Ni, Thm 10.4]), so
this does not narrow the class of languages the claim can be about.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent_system.lib.word_enumeration import full_enumeration_max_len, words_up_to
from ll_system.lib.first_follow import compute_all
from ll_system.lib.grammar_transforms import remove_useless_symbols
from ll_system.lib.ll_parser import LLParser
from ll_system.lib.ll_table_builder import director_cells

# Same coverage threshold as the dcfl DPDA simulation (_DPDA_MIN_WORDS).
PARSE_TABLE_MIN_WORDS = 30
# Steps per word. A loop is detected exactly (refuted) long before this; a
# run that halts on a word of length <= 10 needs far fewer steps with any
# realistic table. A run still going at the limit leaves its word unchecked
# and the table cannot reach bounded_pass.
PARSE_TABLE_MAX_STEPS = 2000
_MAX_REPORTED = 5

_EPSILON = "ε"
_END = "$"


class ParseTableFormatError(ValueError):
    """The artifact cannot be read as an LL(k) parse table."""


@dataclass
class NormalizedTable:
    k: int
    start: str
    nonterminals: set[str]
    # A -> lookahead (padded to length k) -> distinct right-hand sides
    cells: dict[str, dict[str, list[list[str]]]] = field(default_factory=dict)

    def productions(self) -> list[tuple[str, list[str]]]:
        seen: list[tuple[str, list[str]]] = []
        for nt in sorted(self.cells):
            for la in sorted(self.cells[nt]):
                for rhs in self.cells[nt][la]:
                    if (nt, rhs) not in seen:
                        seen.append((nt, rhs))
        return seen

    def conflicts(self) -> list[dict]:
        return [
            {"nonterminal": nt, "lookahead": la, "alternatives": alts}
            for nt in sorted(self.cells)
            for la, alts in sorted(self.cells[nt].items())
            if len(alts) > 1
        ]

    def parser_table(self) -> dict[str, dict[str, list[str]]]:
        """The single-valued table LLParser runs (only for a conflict-free table)."""
        return {nt: {la: alts[0] for la, alts in row.items()} for nt, row in self.cells.items()}


def is_missing(raw: Any) -> bool:
    """True when no parse-table artifact was supplied (None, or no cells)."""
    if raw is None:
        return True
    if isinstance(raw, dict):
        if isinstance(raw.get("entries"), list):
            return not raw["entries"]
        if isinstance(raw.get("table"), dict):
            return not raw["table"]
        return not raw
    if isinstance(raw, list):
        return not raw
    return False


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _norm_rhs(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
        raise ParseTableFormatError(f"{where}: right-hand side must be a list of symbols, got {value!r}")
    return [s for s in value if s not in (_EPSILON, "")]


def _norm_lookahead(value: Any, k: int, terminals: set[str], where: str) -> str:
    if not isinstance(value, str):
        raise ParseTableFormatError(f"{where}: lookahead must be a string, got {value!r}")
    s = value.strip()
    if s == _EPSILON:
        s = ""
    if len(s) > k:
        raise ParseTableFormatError(f"{where}: lookahead {value!r} is longer than k={k}")
    ended = False
    for ch in s:
        if ch == _END:
            ended = True
        elif ended:
            raise ParseTableFormatError(f"{where}: lookahead {value!r} has a symbol after the end marker '$'")
        elif ch not in terminals:
            raise ParseTableFormatError(f"{where}: lookahead {value!r} uses {ch!r}, not a task terminal")
    return s + _END * (k - len(s))


def _cell_alternatives(value: Any, where: str) -> list[list[str]]:
    """A nested-dict cell: one rhs (list of symbols) or several (list of lists)."""
    if isinstance(value, list) and value and all(isinstance(v, list) for v in value):
        return [_norm_rhs(v, where) for v in value]
    return [_norm_rhs(value, where)]


def _raw_cells(raw: Any) -> tuple[list[tuple[str, str, list[list[str]]]], Any, Any]:
    """Flatten either format into (nonterminal, raw lookahead, alternatives)."""
    meta_k = meta_start = None
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        meta_k, meta_start = raw.get("k"), raw.get("start")
        out = []
        for i, entry in enumerate(raw["entries"]):
            where = f"entries[{i}]"
            if not isinstance(entry, dict):
                raise ParseTableFormatError(f"{where}: entry must be an object")
            nt = entry.get("nonterminal")
            if not isinstance(nt, str) or not nt:
                raise ParseTableFormatError(f"{where}: missing nonterminal")
            out.append((nt, entry.get("lookahead"), [_norm_rhs(entry.get("rhs"), where)]))
        return out, meta_k, meta_start
    if isinstance(raw, dict) and isinstance(raw.get("table"), dict):
        meta_k, meta_start = raw.get("k"), raw.get("start")
        raw = raw["table"]
    if not isinstance(raw, dict):
        raise ParseTableFormatError(f"parse table must be an object, got {type(raw).__name__}")
    out = []
    for nt, row in raw.items():
        if not isinstance(row, dict):
            raise ParseTableFormatError(f"row {nt!r}: must map lookaheads to right-hand sides")
        for la, value in row.items():
            out.append((nt, la, _cell_alternatives(value, f"cell ({nt!r}, {la!r})")))
    return out, meta_k, meta_start


def normalize_parse_table(
    raw: Any, *, k: int | None, grammar: dict | None, terminals: list[str],
) -> NormalizedTable:
    """Read a parse-table artifact. ``k`` is the claimed k (None if the claim
    names none); ``grammar`` the claimed grammar (None: the table's own
    productions are the grammar). Raises ``ParseTableFormatError``."""
    raw_cells, meta_k, meta_start = _raw_cells(raw)
    terminal_set = set(terminals)
    if _END in terminal_set:
        raise ParseTableFormatError("'$' is reserved for the end of input and cannot be a terminal")

    table_k = _as_int(meta_k) if meta_k is not None else None
    if meta_k is not None and (table_k is None or table_k < 1):
        raise ParseTableFormatError(f"table k must be a positive integer, got {meta_k!r}")
    if table_k is None:
        table_k = k
    if table_k is None:
        raise ParseTableFormatError("lookahead length k unknown (neither the claim nor the table names it)")
    if k is not None and table_k > k:
        raise ParseTableFormatError(
            f"table is for k={table_k}, which does not certify the claimed LL({k})"
        )

    start = meta_start if isinstance(meta_start, str) and meta_start else None
    grammar_start = grammar.get("start") if isinstance(grammar, dict) else None
    if start is not None and grammar_start is not None and start != grammar_start:
        raise ParseTableFormatError(f"table start {start!r} differs from the grammar start {grammar_start!r}")
    start = start or grammar_start
    if not isinstance(start, str) or not start:
        raise ParseTableFormatError("parse table needs a start symbol")

    if isinstance(grammar, dict):
        nonterminals = {n for n in grammar.get("nonterminals", []) if isinstance(n, str)}
        for nt, _, _ in raw_cells:
            if nt not in nonterminals:
                raise ParseTableFormatError(f"row {nt!r} is not a nonterminal of the claimed grammar")
    else:
        nonterminals = {nt for nt, _, _ in raw_cells}
        for _, _, alts in raw_cells:
            for rhs in alts:
                nonterminals.update(s for s in rhs if s not in terminal_set)
        nonterminals.add(start)
    if start not in nonterminals:
        raise ParseTableFormatError(f"start symbol {start!r} is not a nonterminal")
    bad = sorted((nonterminals & terminal_set) | ({_END} & nonterminals))
    if bad:
        raise ParseTableFormatError(f"symbols {bad} are used both as terminals and nonterminals")

    cells: dict[str, dict[str, list[list[str]]]] = {}
    for nt, la, alts in raw_cells:
        where = f"cell ({nt!r}, {la!r})"
        key = _norm_lookahead(la, table_k, terminal_set, where)
        for rhs in alts:
            unknown = [s for s in rhs if s not in terminal_set and s not in nonterminals]
            if unknown:
                raise ParseTableFormatError(f"{where}: symbols {unknown} are neither terminals nor nonterminals")
            slot = cells.setdefault(nt, {}).setdefault(key, [])
            if rhs not in slot:
                slot.append(rhs)
    return NormalizedTable(k=table_k, start=start, nonterminals=nonterminals, cells=cells)


def _grammar_rules(grammar: dict) -> set[tuple[str, tuple[str, ...]]]:
    rules = set()
    for rule in grammar.get("rules", []) or []:
        if not isinstance(rule, dict) or not isinstance(rule.get("rhs"), list):
            continue
        rhs = tuple(s for s in rule["rhs"] if s not in (_EPSILON, ""))
        rules.add((rule.get("lhs"), rhs))
    return rules


def _table_grammar(table: NormalizedTable, grammar: dict | None, terminals: list[str]) -> dict:
    """The grammar the table must be the director table of: the claimed
    grammar's rules, or (no grammar) G_T, the table's own productions; over
    the task alphabet, ε as ``[]``."""
    if isinstance(grammar, dict):
        rules = sorted(
            (lhs, list(rhs)) for lhs, rhs in _grammar_rules(grammar)
            if isinstance(lhs, str) and lhs in table.nonterminals
            and all(isinstance(s, str) for s in rhs)
        )
    else:
        rules = table.productions()
    return {
        "nonterminals": sorted(table.nonterminals),
        "terminals": list(terminals),
        "start": table.start,
        "rules": [{"lhs": lhs, "rhs": rhs} for lhs, rhs in rules],
    }


def _director_comparison(table: NormalizedTable, grammar: dict) -> dict:
    """Compare a conflict-free table with the strong LL(k) director table of
    ``grammar`` reduced (``remove_useless_symbols``; rows of the nonterminals
    it removes are ignored).

    Returns ``strong`` (the reduced grammar is strong LL(k): no director cell
    holds two rules), ``director_conflicts`` (those cells), ``contradictions``
    (table cells where the director cell is non-empty and the table holds a
    right-hand side that is not in it), ``missing`` (director cells the table
    omits), ``ignored_extra_cells`` (table cells the director table leaves
    empty: never used on a word of L(G), see the module docstring) and
    ``ignored_rows``.
    """
    reduced = remove_useless_symbols(grammar)
    nullable, first_k, follow_k = compute_all(reduced, table.k)
    director = director_cells(reduced, table.k, nullable, first_k, follow_k)
    useful = {rule["lhs"] for rule in reduced["rules"]}
    contradictions: list[dict] = []
    ignored_extra: list[dict] = []
    for nt in sorted(table.cells):
        if nt not in useful:
            continue
        for la, alts in sorted(table.cells[nt].items()):
            expected = director[nt].get(la, [])
            if not expected:
                ignored_extra.append({"nonterminal": nt, "lookahead": la, "alternatives": alts})
            else:
                wrong = [rhs for rhs in alts if rhs not in expected]
                if wrong:
                    contradictions.append({"nonterminal": nt, "lookahead": la, "rhs": wrong[0], "director": expected})
    missing = [
        {"nonterminal": nt, "lookahead": la, "alternatives": alts}
        for nt in sorted(useful)
        for la, alts in sorted(director[nt].items())
        if la not in table.cells.get(nt, {})
    ]
    conflicts = [
        {"nonterminal": nt, "lookahead": la, "alternatives": alts}
        for nt in sorted(useful)
        for la, alts in sorted(director[nt].items())
        if len(alts) > 1
    ]
    return {
        "strong": not conflicts,
        "director_conflicts": conflicts,
        "contradictions": contradictions,
        "missing": missing,
        "ignored_extra_cells": ignored_extra,
        "ignored_rows": sorted(nt for nt in table.cells if nt not in useful),
    }


def _show_rhs(rhs: list[str]) -> str:
    return " ".join(rhs) or "ε"


def _show_cells(cells: list[dict]) -> str:
    """Cells with their ``alternatives``, for an issue text."""
    return "; ".join(
        f"({c['nonterminal']}, {c['lookahead']}) -> " + " | ".join(_show_rhs(r) for r in c["alternatives"])
        for c in cells[:_MAX_REPORTED]
    )


def check_parse_table(
    raw: Any, *, k: int | None, grammar: dict | None, terminals: list[str] | None,
    oracle_factory: Callable[[], Callable[[str], bool | None] | None],
) -> dict:
    """Run the R3′ (1) check. Returns ``{"trust", "issues", "details"}`` with
    trust in {"missing", "not_applicable", "refuted", "well_formed",
    "bounded_pass"} (see the module docstring)."""
    if is_missing(raw):
        return {
            "trust": "missing",
            "issues": [
                "no parse_table artifact: an LL(k) claim must carry its parse table "
                "(docs/VERDICT_POLICY.md R3′), trust capped at well_formed"
            ],
            "details": {"status": "missing"},
        }
    if not terminals or any(not isinstance(t, str) or len(t) != 1 for t in terminals):
        return {
            "trust": "well_formed",
            "issues": ["parse table not run: task alphabet unknown or not single-character"],
            "details": {"status": "not_run", "reason": "alphabet"},
        }
    if k is None and not (isinstance(raw, dict) and raw.get("k") is not None):
        return {
            "trust": "well_formed",
            "issues": ["parse table not run: lookahead length k unknown"],
            "details": {"status": "not_run", "reason": "k"},
        }
    try:
        table = normalize_parse_table(raw, k=k, grammar=grammar, terminals=terminals)
    except ParseTableFormatError as exc:
        return {
            "trust": "refuted",
            "issues": [f"malformed parse_table: {exc}"],
            "details": {"status": "malformed", "error": str(exc)},
        }
    details: dict = {"k": table.k, "start": table.start}

    director = _director_comparison(table, _table_grammar(table, grammar, terminals))
    if isinstance(grammar, dict) and not director["strong"]:
        return {
            "trust": "not_applicable",
            "issues": [
                f"the claimed grammar is not strong LL({table.k}) (director-table conflict(s): "
                f"{_show_cells(director['director_conflicts'])}): if it is LL({table.k}), "
                "a conflict-free table built from its rules rejects at least one word of L(G), possibly "
                "longer than the enumeration bound, so the parse_table artifact is not run; trust capped "
                "at well_formed (docs/VERDICT_POLICY.md R3′ (1))"
            ],
            "details": {"status": "not_strong", "k": table.k},
        }

    conflicts = table.conflicts()
    if conflicts:
        shown = "; ".join(
            f"({c['nonterminal']}, {c['lookahead']}) -> {c['alternatives']}" for c in conflicts[:_MAX_REPORTED]
        )
        return {
            "trust": "refuted",
            "issues": [f"parse table has conflict cell(s), so it is not an LL({table.k}) table: {shown}"],
            "details": {**details, "status": "conflict", "conflicts": conflicts},
        }

    if isinstance(grammar, dict):
        rules = _grammar_rules(grammar)
        foreign = [(nt, rhs) for nt, rhs in table.productions() if (nt, tuple(rhs)) not in rules]
        if foreign:
            shown = "; ".join(f"{nt} -> {' '.join(rhs) or 'ε'}" for nt, rhs in foreign[:_MAX_REPORTED])
            return {
                "trust": "refuted",
                "issues": [f"parse table uses production(s) that are not rules of the claimed grammar: {shown}"],
                "details": {**details, "status": "foreign_production",
                            "foreign_productions": [{"lhs": nt, "rhs": rhs} for nt, rhs in foreign]},
            }

    grammar_name = "the claimed grammar" if isinstance(grammar, dict) else "the table's own productions"
    if director["contradictions"]:
        shown = "; ".join(
            f"({c['nonterminal']}, {c['lookahead']}) -> {_show_rhs(c['rhs'])}, director table: "
            + (" | ".join(_show_rhs(r) for r in c["director"]) or "no rule")
            for c in director["contradictions"][:_MAX_REPORTED]
        )
        return {
            "trust": "refuted",
            "issues": [
                f"parse table is not the strong LL({table.k}) director table of {grammar_name}: "
                f"cell(s) contradicting it: {shown}"
            ],
            "details": {**details, "status": "director_contradiction",
                        "contradictions": director["contradictions"]},
        }
    director_cap: str | None = None
    if not director["strong"]:
        director_cap = (
            f"the table's own productions do not form a strong LL({table.k}) grammar (director-table "
            f"conflict(s): {_show_cells(director['director_conflicts'])}); the table "
            "keeps at most one rule where the grammar allows several, a priority choice that is not an LL("
            f"{table.k}) certificate; trust capped at well_formed (docs/VERDICT_POLICY.md R3′ (1))"
        )
    elif director["missing"]:
        director_cap = (
            f"parse table omits cell(s) of the strong LL({table.k}) director table of {grammar_name}: "
            f"{_show_cells(director['missing'])}; an incomplete table is not an LL("
            f"{table.k}) certificate, trust capped at well_formed (docs/VERDICT_POLICY.md R3′ (1))"
        )
    details["director_table"] = {
        "grammar": "claimed" if isinstance(grammar, dict) else "table_productions",
        "strong": director["strong"],
        "missing": director["missing"][:_MAX_REPORTED],
        "director_conflicts": director["director_conflicts"][:_MAX_REPORTED],
        "ignored_extra_cells": director["ignored_extra_cells"][:_MAX_REPORTED],
        "ignored_rows": director["ignored_rows"],
    }

    oracle = oracle_factory()
    parser = LLParser.from_table(
        {"nonterminals": sorted(table.nonterminals), "terminals": list(terminals), "start": table.start},
        table.parser_table(), table.k,
    )
    max_len = full_enumeration_max_len(len(terminals))
    details["max_len"] = max_len
    checked = undecided = unfinished = 0
    mismatches: list[dict] = []
    for word in words_up_to(sorted(terminals), max_len):
        # The parser runs on every word, with or without an oracle answer:
        # a loop refutes the table whatever the word's membership.
        run = parser.parse(word, record_steps=False, max_steps=PARSE_TABLE_MAX_STEPS)
        if run.get("non_halting"):
            loop = run["loop"]
            return {
                "trust": "refuted",
                "issues": [
                    f"parse table never halts on {word or 'ε'!r}: {loop['nonterminal']} comes back on top "
                    f"with lookahead {loop['lookahead']!r} before its own expansion finished "
                    "(left recursion), so it is not an LL(k) parser"
                ],
                "details": {**details, "status": "non_halting", "loop": {"word": word, **loop},
                            "counterexamples": [word]},
            }
        if oracle is None:
            continue
        try:
            expected = oracle(word)
        except Exception:
            expected = None
        if expected is None:
            undecided += 1
            continue
        if run.get("step_limit_exceeded"):
            unfinished += 1
            continue
        checked += 1
        accepted = bool(run.get("accepted"))
        if accepted != bool(expected):
            mismatches.append({"word": word, "in_language": bool(expected), "parser_accepts": accepted})
            if len(mismatches) >= _MAX_REPORTED:
                break

    if oracle is None:
        return {
            "trust": "well_formed",
            "issues": ["parse table not checked against the language: no membership oracle for the task language"],
            "details": {**details, "status": "no_oracle"},
        }
    details.update({"words_checked": checked, "words_undecided": undecided, "words_unfinished": unfinished})

    if mismatches:
        shown = "; ".join(
            f"{m['word'] or 'ε'!r}: in L={m['in_language']}, parser accepts={m['parser_accepts']}"
            for m in mismatches
        )
        return {
            "trust": "refuted",
            "issues": [f"parse table disagrees with the task's membership oracle: {shown}"],
            "details": {**details, "status": "mismatch", "mismatches": mismatches,
                        "counterexamples": [m["word"] for m in mismatches]},
        }
    if unfinished:
        return {
            "trust": "well_formed",
            "issues": [
                f"parse table still running after {PARSE_TABLE_MAX_STEPS} steps on {unfinished} word(s) "
                "the oracle decides; coverage incomplete"
            ],
            "details": {**details, "status": "step_limit"},
        }
    if checked < PARSE_TABLE_MIN_WORDS:
        return {
            "trust": "well_formed",
            "issues": [
                f"parse table agreed with the oracle on only {checked} decided words "
                f"(< {PARSE_TABLE_MIN_WORDS}); coverage incomplete"
            ],
            "details": {**details, "status": "low_coverage"},
        }
    if director_cap is not None:
        return {
            "trust": "well_formed",
            "issues": [director_cap],
            "details": {**details, "status": "not_director_table"},
        }
    return {"trust": "bounded_pass", "issues": [], "details": {**details, "status": "bounded_pass"}}
