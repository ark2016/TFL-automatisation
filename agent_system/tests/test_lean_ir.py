"""Tests for agent_system.lib.lean_ir -- the deterministic IR -> Lean 4
statement translator (docs/VERDICT_POLICY.md R-Lean).

Golden-string tests pin the exact generated Lean text for a handful of real
example IRs (agent_system/examples/**), so a change in the translator's
output is visible in a diff rather than only in a much-harder-to-read
Docker-compile pass/fail. The syntax-only Docker-compile tests at the bottom
(skipped without the tfl-lean4 image) are the ones that actually confirm the
text is well-formed Lean; they were used to validate every golden string
below before it was pinned (`docker run tfl-lean4 ... lake env lean`, see
this module's own dev history / the module docstring's provenance section --
every one of the golden strings pinned here compiled clean with a `sorry`
proof body, warnings-only, while writing this test).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_system.lib.lean_ir import (
    LeanStatement,
    alphabet_decl,
    grammar_decl,
    pattern_body,
    predicate_to_lean,
    render_statement,
    render_statement_verbose,
)
from agent_system.lib.oracle import oracle_from_ir
from agent_system.lib.type_check import (
    check_lean,
    compose_lean_file,
    is_docker_available,
)
from agent_system.tests.lean_eval import check_against_oracle

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Golden strings
# ---------------------------------------------------------------------------

class TestGoldenNaturalExponent(unittest.TestCase):
    """{a^n b^n | n >= 0}, reg-01 -- non_regular."""

    def test_anbn_non_regular(self):
        ir = _load("eval/reg-01.json")
        stmt = render_statement(ir, "non_regular")
        self.assertIsInstance(stmt, LeanStatement)
        self.assertEqual(stmt.imports, ["import TflLean"])
        self.assertEqual(
            stmt.alphabet_decl,
            "inductive Letter\n  | a | b\n  deriving DecidableEq, Repr",
        )
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Letter := {w : List Letter | ∃ n : ℕ, "
            "w = List.replicate n Letter.a ++ List.replicate n Letter.b ∧ n ≥ 0}",
        )
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ L.IsRegular")
        self.assertEqual(stmt.name, "tfl_main")

    def test_render_appends_by_sorry(self):
        ir = _load("eval/reg-01.json")
        stmt = render_statement(ir, "non_regular")
        text = stmt.render("sorry")
        self.assertIn("theorem tfl_main : ¬ L.IsRegular := by\n  sorry", text)
        self.assertTrue(text.endswith("\n"))


class TestGoldenGrammar(unittest.TestCase):
    """S -> SaSb | eps | A, A -> bb | aa | bSb -- non_regular (reg-14's grammar)."""

    def test_grammar_non_regular(self):
        ir = _load("task2_grammar_sasb.json")
        stmt = render_statement(ir, "non_regular")
        self.assertEqual(
            stmt.imports,
            ["import TflLean", "import Mathlib.Computability.ContextFreeGrammar"],
        )
        self.assertEqual(
            stmt.alphabet_decl,
            "inductive Letter\n  | a | b\n  deriving DecidableEq, Repr",
        )
        self.assertIn("inductive NT\n  | S | A\n  deriving DecidableEq, Repr", stmt.language_decl)
        self.assertIn("def g : ContextFreeGrammar Letter :=", stmt.language_decl)
        self.assertIn(
            "⟨NT.S, [Symbol.nonterminal NT.S, Symbol.terminal Letter.a, "
            "Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩",
            stmt.language_decl,
        )
        self.assertIn("⟨NT.S, []⟩", stmt.language_decl)
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := g.language"))
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ L.IsRegular")

    def test_grammar_regular_direction_no_negation(self):
        ir = _load("task2_grammar_sasb.json")
        stmt = render_statement(ir, "regular")
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : L.IsRegular")


class TestGoldenRegex(unittest.TestCase):
    """(ab)*a | b -- regular (reg-04)."""

    def test_regex_regular(self):
        ir = _load("eval/reg-04.json")
        stmt = render_statement(ir, "regular")
        self.assertEqual(stmt.imports, ["import TflLean"])
        self.assertIn("def re : RegularExpression Letter :=", stmt.language_decl)
        self.assertIn("RegularExpression.star", stmt.language_decl)
        self.assertIn("RegularExpression.char Letter.a", stmt.language_decl)
        self.assertIn("RegularExpression.char Letter.b", stmt.language_decl)
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := re.matches'"))
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : L.IsRegular")


class TestGoldenPalindrome(unittest.TestCase):
    """{w in {a,b}* | w = reverse(w)} -- non_regular (reg-06)."""

    def test_palindrome_via_list_reverse(self):
        ir = _load("eval/reg-06.json")
        stmt = render_statement(ir, "non_regular")
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Letter := {w : List Letter | w = w.reverse}",
        )
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ L.IsRegular")


# ---------------------------------------------------------------------------
# None cases (docs/VERDICT_POLICY.md R-Lean: "не формализуется")
# ---------------------------------------------------------------------------

class TestUnsupportedReturnsNone(unittest.TestCase):

    def test_ll_direction_always_none(self):
        for ir in (
            {"language_spec": {"kind": "natural", "description": "anything"}},
            {},
            {"language_spec": {}},
        ):
            self.assertIsNone(render_statement(ir, "ll"))
            _stmt, reason = render_statement_verbose(ir, "ll")
            self.assertIn("LL(k)", reason)

    def test_regex_with_backreferences_is_none(self):
        ir = _load("task3_regex_backref.json")
        stmt, reason = render_statement_verbose(ir, "regular")
        self.assertIsNone(stmt)
        self.assertIn("backreference", reason)

    def test_natural_without_notation_is_none(self):
        """{w in {a,b}* | w contains 'aba' as a substring} -- an alphabet is
        declared, but the description is neither exponent notation nor a
        recognized palindrome shape."""
        ir = _load("eval/reg-08.json")
        stmt, reason = render_statement_verbose(ir, "regular")
        self.assertIsNone(stmt)
        self.assertIn("exponent", reason)

    def test_predicate_kind_is_none(self):
        ir = _load("task1_palindrome_prefix_suffix.json")
        stmt, reason = render_statement_verbose(ir, "non_regular")
        self.assertIsNone(stmt)
        self.assertIn("predicate", reason)

    def test_palindrome_with_extra_condition_is_none(self):
        """Code review (agent_system/lib/lean_ir.py, major): the old
        ``_PALINDROME_RE.search`` matched whenever "w = reverse(w)" (etc.)
        appeared *anywhere* in the description, so a strictly smaller
        language with an extra conjunct -- e.g. finite "w = w^R and |w| is
        even" -- was silently formalized as "all palindromes", changing the
        proved verdict's direction. ``fullmatch`` must reject every one of
        these; only the bare "all palindromes" shape (reg-06) is accepted."""
        for description in (
            "{w in {a,b}* | w = reverse(w) and |w| is even}",
            "{w | w = w^R, |w|_a = 2}",
            "{x | x = w w^R, w = w^R}",
        ):
            ir = {
                "language_spec": {
                    "kind": "natural",
                    "alphabet": ["a", "b"],
                    "description": description,
                }
            }
            stmt, reason = render_statement_verbose(ir, "non_regular")
            self.assertIsNone(stmt, f"{description!r} must not formalize as a bare palindrome")
            self.assertIn("exponent", reason)

    def test_cfl_and_dcfl_directions_delegate_elsewhere(self):
        ir = _load("eval/reg-01.json")
        for direction, owner in (("cfl", "cfl_system"), ("non_cfl", "cfl_system"),
                                  ("dcfl", "dcfl_system"), ("non_dcfl", "dcfl_system")):
            stmt, reason = render_statement_verbose(ir, direction)
            self.assertIsNone(stmt)
            self.assertIn(owner, reason)

    def test_unknown_direction_is_none(self):
        stmt, reason = render_statement_verbose({"language_spec": {}}, "maybe_regular")
        self.assertIsNone(stmt)
        self.assertIn("unknown direction", reason)

    def test_never_raises_on_garbage_input(self):
        for ir in (None, 42, "oops", [], {"language_spec": "nope"}):
            self.assertIsNone(render_statement(ir, "regular"))


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotent(unittest.TestCase):

    def test_same_ir_same_output_every_time(self):
        ir = _load("eval/reg-01.json")
        first = render_statement(ir, "non_regular").render("sorry")
        for _ in range(5):
            again = render_statement(ir, "non_regular").render("sorry")
            self.assertEqual(first, again)

    def test_grammar_ir_is_idempotent(self):
        ir = _load("task2_grammar_sasb.json")
        first = render_statement(ir, "non_regular").render("sorry")
        again = render_statement(ir, "non_regular").render("sorry")
        self.assertEqual(first, again)


# ---------------------------------------------------------------------------
# Alphabet: every letter of the IR's alphabet appears in the inductive, even
# one the pattern itself never uses.
# ---------------------------------------------------------------------------

class TestAlphabetDecl(unittest.TestCase):

    def test_all_symbols_present_even_if_unused_by_the_pattern(self):
        decl, mapping = alphabet_decl(["a", "b", "c"])
        self.assertEqual(set(mapping), {"a", "b", "c"})
        self.assertEqual(decl, "inductive Letter\n  | a | b | c\n  deriving DecidableEq, Repr")
        self.assertIn("deriving DecidableEq, Repr", decl)

    def test_reg01_alphabet_has_both_letters(self):
        ir = _load("eval/reg-01.json")
        stmt = render_statement(ir, "non_regular")
        self.assertIn("| a | b", stmt.alphabet_decl)

    def test_none_on_empty_or_malformed_alphabet(self):
        self.assertIsNone(alphabet_decl([]))
        self.assertIsNone(alphabet_decl(["a", ""]))
        self.assertIsNone(alphabet_decl("ab"))  # a string, not a list
        self.assertIsNone(alphabet_decl(None))

    def test_keyword_letter_gets_a_safe_fallback_identifier(self):
        # Pathological but must not produce invalid Lean syntax.
        decl, mapping = alphabet_decl(["let", "a"])
        self.assertNotIn("| let |", decl)
        self.assertEqual(len(set(mapping.values())), 2)


# ---------------------------------------------------------------------------
# Docker-conditional: the golden strings above actually compile.
# ---------------------------------------------------------------------------

@unittest.skipUnless(is_docker_available(), "Docker with tfl-lean4 image not available")
class TestGoldenStringsCompile(unittest.TestCase):
    """`sorry` for <PROOF> should type-check with a sorry warning only --
    never an error -- for every kind of statement this module (the REG
    direction only; ``cfl``/``dcfl`` are ``cfl_system.lib.lean_ir``'s/
    ``dcfl_system.lib.lean_ir``'s own modules, with their own Docker-
    conditional golden-string-compile tests, langlib imports included --
    see the module docstring's "langlib availability") can produce."""

    def _assert_valid_with_sorry(self, ir_name: str, direction: str) -> None:
        ir = _load(ir_name)
        stmt = render_statement(ir, direction)
        self.assertIsNotNone(stmt, f"{ir_name}/{direction} did not render a statement")
        result = check_lean(stmt.render("sorry"), timeout=180)
        self.assertEqual(result["status"], "valid", f"{ir_name}/{direction}: {result}")
        self.assertTrue(
            result["warnings"] and any("sorry" in w.lower() for w in result["warnings"]),
            f"{ir_name}/{direction}: expected a sorry warning, got {result['warnings']!r}",
        )

    def test_natural_exponent_compiles(self):
        self._assert_valid_with_sorry("eval/reg-01.json", "non_regular")

    def test_grammar_compiles(self):
        self._assert_valid_with_sorry("task2_grammar_sasb.json", "non_regular")

    def test_regex_compiles(self):
        self._assert_valid_with_sorry("eval/reg-04.json", "regular")

    def test_palindrome_compiles(self):
        self._assert_valid_with_sorry("eval/reg-06.json", "non_regular")


# ---------------------------------------------------------------------------
# Extended kinds (2026-09-28): palindrome domains, word templates, composites
# (⊔/⊓), decidable companions, predicate translator, grammar variants.
# ---------------------------------------------------------------------------

def _natural_ir(description: str, alphabet=("a", "b")) -> dict:
    return {"language_spec": {"kind": "natural", "alphabet": list(alphabet), "description": description}}


def _lang(ir: dict, direction: str = "non_regular") -> str:
    stmt, reason = render_statement_verbose(ir, direction)
    assert stmt is not None, reason
    return stmt.language_decl


class TestPalindromeDomain(unittest.TestCase):
    """The ``w ∈ SET`` in front of the ``|`` is honoured, never dropped."""

    def test_full_alphabet_domain_is_the_bare_palindrome(self):
        for desc in (
            "{w in {a,b}* | w = reverse(w)}",
            "{w ∈ Σ* | w = w^R}",
            "{w ∈ {a, b}^* | w^R = w}",
            "{w | w = wᴿ}",
        ):
            self.assertEqual(
                _lang(_natural_ir(desc)),
                "def L : Language Letter := {w : List Letter | w = w.reverse}",
                desc,
            )

    def test_proper_subset_domain_restricts_the_letters(self):
        self.assertEqual(
            _lang(_natural_ir("{w ∈ {a}* | w = w^R}")),
            "def L : Language Letter := {w : List Letter | w = w.reverse ∧ ∀ ch ∈ w, ch = Letter.a}",
        )

    def test_unrecognized_or_foreign_domain_is_none(self):
        for desc in (
            "{w ∈ (a|b)* | w = w^R}",       # not a Σ*/{..}* domain
            "{w ∈ {a,z}* | w = w^R}",       # z is not in the alphabet
            "{x ∈ {a,b}* | w = w^R}",       # bound variable differs
        ):
            stmt, reason = render_statement_verbose(_natural_ir(desc), "non_regular")
            self.assertIsNone(stmt, desc)
            self.assertIn("exponent", reason)


class TestWordTemplates(unittest.TestCase):
    """``{w wᴿ}``, ``{w c wᴿ}`` and the copy twins ``{w w}``, ``{w c w}``."""

    HALF = "w.take (w.length / 2)"

    def test_w_wR_golden(self):
        stmt = render_statement(_natural_ir("{w wᴿ | w ∈ {a,b}*}"), "non_regular")
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Letter := {w : List Letter | ∃ u : List Letter, w = u ++ u.reverse}",
        )
        self.assertEqual(
            stmt.decidable_decl,
            f"def Lb (w : List Letter) : Bool := decide (w = {self.HALF} ++ ({self.HALF}).reverse)",
        )
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ L.IsRegular")

    def test_reversal_spellings_agree(self):
        want = _lang(_natural_ir("{w wᴿ | w ∈ {a,b}*}"))
        for desc in (
            "{w w^R | w ∈ {a,b}*}", "{w w^{R} | w in Σ*}", "{w · w^R}", "{w rev(w) | w ∈ {a,b}*}",
            "L = {w reverse(w) | w ∈ Σ^*}", "wwᴿ",
        ):
            self.assertEqual(_lang(_natural_ir(desc)), want, desc)

    def test_w_c_wR_golden_with_restricted_domain(self):
        stmt = render_statement(_natural_ir("{w c wᴿ | w ∈ {a,b}*}", "abc"), "non_regular")
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Letter := {w : List Letter | ∃ u : List Letter, "
            "w = u ++ [Letter.c] ++ u.reverse ∧ ∀ ch ∈ u, ch = Letter.a ∨ ch = Letter.b}",
        )
        self.assertEqual(
            stmt.decidable_decl,
            f"def Lb (w : List Letter) : Bool := decide (w = {self.HALF} ++ [Letter.c] ++ "
            f"({self.HALF}).reverse ∧ ({self.HALF}).all (fun ch => decide (ch = Letter.a ∨ ch = Letter.b)) = true)",
        )

    def test_copy_templates(self):
        self.assertEqual(
            _lang(_natural_ir("{ww | w in {a,b}*}")),
            "def L : Language Letter := {w : List Letter | ∃ u : List Letter, w = u ++ u}",
        )
        self.assertEqual(
            _lang(_natural_ir("{w c w | w ∈ {a,b,c}*}", "abc")),
            "def L : Language Letter := {w : List Letter | ∃ u : List Letter, w = u ++ [Letter.c] ++ u}",
        )

    def test_extra_clauses_and_bad_shapes_are_none(self):
        for desc, alphabet in (
            ("{w wᴿ | |w| >= 1}", "ab"),
            ("{w wᴿ | w ∈ {a,b}*, w ≠ ε}", "ab"),
            ("{w wᴿ v}", "ab"),
            ("{w wᴿ | w ∈ (a|b)*}", "ab"),
            ("{w wᴿ | w ∈ {a,z}*}", "ab"),
            ("{w z wᴿ | w ∈ {a,b}*}", "ab"),     # z is not a letter
            ("{a aᴿ}", "ab"),                     # the variable would be a letter
            ("{w wᴿ", "ab"),                      # unbalanced brace
            ("w wᴿ}", "ab"),
            ("{w1 w2}", "ab"),
        ):
            stmt = render_statement(_natural_ir(desc, alphabet), "non_regular")
            self.assertIsNone(stmt, desc)

    def test_multi_character_alphabet_is_none(self):
        ir = {"language_spec": {"kind": "natural", "alphabet": ["ab", "c"], "description": "{w wᴿ}"}}
        self.assertIsNone(render_statement(ir, "non_regular"))


class TestComposites(unittest.TestCase):
    """``L := L_1 ⊔ L_2`` / ``⊓`` -- Mathlib's Language lattice."""

    def test_natural_union_of_template_and_exponent(self):
        stmt = render_statement(_natural_ir("{w wᴿ | w ∈ {a,b}*} ∪ {a^n b^n | n >= 0}"), "non_regular")
        self.assertEqual(
            stmt.language_decl,
            "def L_1 : Language Letter := {w : List Letter | ∃ u : List Letter, w = u ++ u.reverse}\n\n"
            "def L_2 : Language Letter := {w : List Letter | ∃ n : ℕ, "
            "w = List.replicate n Letter.a ++ List.replicate n Letter.b ∧ n ≥ 0}\n\n"
            "def L : Language Letter := L_1 ⊔ L_2",
        )
        self.assertIn("def Lb (w : List Letter) : Bool := Lb_1 w || Lb_2 w", stmt.decidable_decl)
        self.assertEqual(stmt.imports, ["import TflLean"])

    def test_natural_intersection(self):
        stmt = render_statement(
            _natural_ir("{a^n b^n | n >= 0} intersect {w wᴿ | w ∈ {a,b}*}"), "non_regular"
        )
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := L_1 ⊓ L_2"))
        self.assertIn("def Lb (w : List Letter) : Bool := Lb_1 w && Lb_2 w", stmt.decidable_decl)

    def test_union_binds_looser_than_intersection(self):
        stmt = render_statement(
            _natural_ir("{a^n | n >= 0} ∪ {b^n | n >= 0} ∩ {w wᴿ}"), "non_regular"
        )
        decl = stmt.language_decl
        self.assertTrue(decl.endswith("def L : Language Letter := L_1 ⊔ L_2"))
        self.assertIn("def L_2 : Language Letter := L_2_1 ⊓ L_2_2", decl)

    def test_union_kind_with_regex_and_natural_branches(self):
        ir = {
            "alphabet": ["a", "b"],
            "language_spec": {
                "kind": "union",
                "branches": [
                    {"kind": "regex", "pattern": "(ab)*", "has_backreferences": False},
                    "{a^n b^n | n >= 0}",
                ],
            },
        }
        stmt, reason = render_statement_verbose(ir, "non_regular")
        self.assertIsNotNone(stmt, reason)
        self.assertIn("def re_1 : RegularExpression Letter :=", stmt.language_decl)
        self.assertIn("def L_1 : Language Letter := re_1.matches'", stmt.language_decl)
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := L_1 ⊔ L_2"))
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ L.IsRegular")

    def test_alphabet_derived_from_branches(self):
        ir = {
            "language_spec": {
                "kind": "intersection",
                "branches": [
                    {"kind": "regex", "alphabet": ["a", "b"], "pattern": "a*b*", "has_backreferences": False},
                    {"kind": "natural", "alphabet": ["a", "b"], "description": "{a^n b^n | n >= 0}"},
                ],
            }
        }
        stmt, reason = render_statement_verbose(ir, "non_regular")
        self.assertIsNotNone(stmt, reason)
        self.assertEqual(stmt.alphabet_decl, "inductive Letter\n  | a | b\n  deriving DecidableEq, Repr")

    def test_grammar_branches_get_suffixed_names_and_the_cfg_import(self):
        g = _load("task2_grammar_sasb.json")["language_spec"]
        ir = {"language_spec": {"kind": "union", "branches": [g, g]}}
        stmt, reason = render_statement_verbose(ir, "non_regular")
        self.assertIsNotNone(stmt, reason)
        for name in ("inductive NT_1", "inductive NT_2", "def g_1 :", "def g_2 :", "def L_1 :", "def L_2 :"):
            self.assertIn(name, stmt.language_decl)
        self.assertIn("NT := NT_2, initial := NT_2.", stmt.language_decl)
        self.assertIn("import Mathlib.Computability.ContextFreeGrammar", stmt.imports)
        self.assertIsNone(stmt.decidable_decl)  # grammars have no decidable form

    def test_bad_composites_are_none(self):
        base = {"alphabet": ["a", "b"]}
        for spec in (
            {"kind": "union", "branches": ["{a^n b^n | n >= 0}"]},                 # one branch
            {"kind": "union", "branches": ["{a^n b^n | n >= 0}", "some prose"]},  # untranslatable branch
            {"kind": "intersection", "branches": "nope"},
            {"kind": "union", "branches": [{"kind": "predicate"}, "{a^n}"]},
        ):
            stmt, reason = render_statement_verbose({**base, "language_spec": spec}, "non_regular")
            self.assertIsNone(stmt, spec)
            self.assertTrue(reason)

    def test_prose_containing_union_is_not_guessed(self):
        stmt = render_statement(_natural_ir("the union of all words with an even number of a"), "non_regular")
        self.assertIsNone(stmt)


class TestDecidableCompanion(unittest.TestCase):

    def test_exponent_pattern_gets_a_bounded_companion(self):
        stmt = render_statement(_load("eval/reg-01.json"), "non_regular")
        self.assertEqual(stmt.decidable_name, "Lb")
        self.assertEqual(stmt.decidable_covers, "language")
        self.assertEqual(
            stmt.decidable_decl,
            "def Lb (w : List Letter) : Bool := decide (∃ n : ℕ, n < w.length + 1 ∧ "
            "w = List.replicate n Letter.a ++ List.replicate n Letter.b ∧ n ≥ 0)",
        )

    def test_companion_is_never_part_of_the_theorem_file(self):
        stmt = render_statement(_load("eval/reg-01.json"), "non_regular")
        self.assertNotIn("Lb", stmt.render("sorry"))
        self.assertNotIn("Lb", compose_lean_file(stmt, "skip"))

    def test_regex_companion_is_rmatch(self):
        stmt = render_statement(_load("eval/reg-04.json"), "regular")
        self.assertEqual(stmt.decidable_decl, "def Lb (w : List Letter) : Bool := re.rmatch w")

    def test_grammar_has_no_companion(self):
        stmt = render_statement(_load("task2_grammar_sasb.json"), "non_regular")
        self.assertIsNone(stmt.decidable_decl)

    def test_variable_only_in_the_condition_has_no_companion(self):
        stmt, reason = render_statement_verbose(_natural_ir("{a^n | n >= m}"), "non_regular")
        self.assertIsNotNone(stmt, reason)          # the statement itself is fine ...
        self.assertIsNone(stmt.decidable_decl)      # ... but m is not bounded by |w|

    def test_boundedness_rules(self):
        var = lambda n: ("var", n)
        num = lambda k: ("num", k)
        sym = {"a": "a"}

        def dec(expr, unit="a"):
            return pattern_body([{"unit": unit, "expr": expr}], None, sym, decidable=True)

        self.assertIsNotNone(dec(var("n")))
        self.assertIsNotNone(dec(("bin", "*", num(2), var("n"))))
        self.assertIsNotNone(dec(("bin", "+", var("n"), num(3))))
        self.assertIsNotNone(dec(("bin", "+", var("n"), var("m"))))
        self.assertIsNotNone(dec(("bin", "*", var("n"), var("n"))))
        self.assertIsNone(dec(("bin", "*", var("n"), var("m"))))    # n * 0 = 0: n unbounded
        self.assertIsNone(dec(("bin", "*", num(0), var("n"))))      # 0 * n: n unbounded
        # the unbounded ∃ form is unaffected
        self.assertEqual(
            pattern_body([{"unit": "a", "expr": var("n")}], None, sym),
            "∃ n : ℕ, w = List.replicate n Letter.a",
        )


class TestPredicateToLean(unittest.TestCase):
    SYM = {"a": "a", "b": "b"}
    ENV = {"w": "w"}

    def _p(self, pred, env=None):
        return predicate_to_lean(pred, self.SYM, env or self.ENV)

    def test_comparisons_and_counts(self):
        cnt = lambda s: {"kind": "count_symbol", "symbol": s, "in_var": "w"}
        self.assertEqual(
            self._p({"op": "eq", "left": cnt("a"), "right": cnt("b")}),
            "w.count Letter.a = w.count Letter.b",
        )
        self.assertEqual(
            self._p({"op": "geq", "left": {"kind": "length", "of_var": "w"}, "right": {"kind": "constant", "value": 3}}),
            "w.length ≥ 3",
        )
        for op, sign in (("neq", "≠"), ("lt", "<"), ("leq", "≤"), ("gt", ">")):
            self.assertEqual(
                self._p({"op": op, "left": cnt("a"), "right": {"kind": "constant", "value": 1}}),
                f"w.count Letter.a {sign} 1",
            )

    def test_boolean_structure_and_modular(self):
        eq = {"op": "eq", "left": {"kind": "length", "of_var": "w"}, "right": {"kind": "constant", "value": 2}}
        self.assertEqual(self._p({"op": "not", "operands": [eq]}), "¬ (w.length = 2)")
        self.assertEqual(self._p({"op": "and", "operands": [eq, eq]}), "(w.length = 2) ∧ (w.length = 2)")
        self.assertEqual(self._p({"op": "or", "operands": [eq, eq]}), "(w.length = 2) ∨ (w.length = 2)")
        self.assertEqual(
            self._p({"expr": {"kind": "length", "of_var": "w"}, "modulus": 4, "remainder": 0}),
            "(w.length) % 4 = 0",
        )

    def test_word_predicates(self):
        self.assertEqual(self._p({"op": "is_palindrome", "var": "w"}), "w = w.reverse")
        self.assertEqual(self._p({"op": "is_not_palindrome", "var": "w"}), "¬ (w = w.reverse)")
        self.assertEqual(
            self._p({"op": "is_substring", "substring_expr": "ab", "in_var": "w"}),
            "[Letter.a, Letter.b] <:+: w",
        )
        self.assertEqual(
            self._p({"op": "not_starts_with", "prefix_expr": "b", "of_var": "w"}),
            "¬ ([Letter.b] <+: w)",
        )
        # an operand naming a variable of env is that variable (oracle: env.get(x, x))
        self.assertEqual(
            self._p({"op": "starts_with", "prefix_expr": "u", "of_var": "w"}, {"w": "x", "u": "p_u"}),
            "p_u <+: x",
        )
        self.assertEqual(
            self._p({"expr": {"kind": "count_subword", "in_var": "w", "subword": "ab"}, "modulus": 2, "remainder": 1}),
            "(((List.range w.length).countP (fun i => decide ([Letter.a, Letter.b] <+: w.drop i)))) % 2 = 1",
        )

    def test_env_renames_the_word(self):
        self.assertEqual(
            self._p({"op": "eq", "left": {"kind": "length", "of_var": "w"}, "right": {"kind": "length", "of_var": "u"}},
                    {"w": "x", "u": "p_u"}),
            "x.length = p_u.length",
        )

    def test_unsupported_is_none(self):
        cnt = {"kind": "count_symbol", "symbol": "a", "in_var": "w"}
        for bad in (
            None, [], {}, {"op": "eq"},
            {"op": "eq", "left": cnt, "right": {"kind": "constant", "value": -1}},      # ℕ: no negatives
            {"op": "eq", "left": {"kind": "count_symbol", "symbol": "z", "in_var": "w"}, "right": cnt},
            {"op": "eq", "left": {"kind": "count_symbol", "symbol": "a", "in_var": "q"}, "right": cnt},
            {"op": "eq", "left": {"kind": "sum"}, "right": cnt},
            {"op": "not", "operands": []},
            {"op": "not", "operands": [{"op": "is_palindrome", "var": "w"}] * 2},
            {"expr": {"kind": "length", "of_var": "w"}, "modulus": 0, "remainder": 0},
            {"op": "is_substring", "substring_expr": "az", "in_var": "w"},
            {"parts": ["u"], "concat_pattern": ["u"]},
        ):
            self.assertIsNone(self._p(bad), bad)
        self.assertIsNone(predicate_to_lean(cnt, {"ab": "ab"}, self.ENV))  # multi-char alphabet


class TestGrammarDeclVariants(unittest.TestCase):
    SPEC = {
        "kind": "grammar", "terminals": ["a", "b"], "nonterminals": ["S", "A"], "start": "S",
        "rules": [
            {"lhs": "S", "rhs": ["a", "S", "b"]},
            {"lhs": "S", "rhs": []},        # ε-rule
            {"lhs": "S", "rhs": ["A"]},     # unit rule
            {"lhs": "A", "rhs": ["a"]},
        ],
    }

    def _decl(self, spec=None, **kw):
        _decl, sym = alphabet_decl(["a", "b"])
        return grammar_decl(spec or self.SPEC, sym, **kw)

    def test_epsilon_and_unit_rules(self):
        text = self._decl()
        self.assertIn("⟨NT.S, []⟩", text)
        self.assertIn("⟨NT.S, [Symbol.nonterminal NT.A]⟩", text)

    def test_default_names_unchanged(self):
        text = self._decl()
        self.assertTrue(text.startswith("inductive NT\n  | S | A\n  deriving DecidableEq, Repr\n\ndef g : ContextFreeGrammar Letter :="))
        self.assertTrue(text.endswith("def L : Language Letter := g.language"))

    def test_suffix_renames_every_definition(self):
        text = self._decl(suffix="_3")
        self.assertIn("inductive NT_3", text)
        self.assertIn("def g_3 : ContextFreeGrammar Letter", text)
        self.assertIn("{ NT := NT_3, initial := NT_3.S,", text)
        self.assertTrue(text.endswith("def L_3 : Language Letter := g_3.language"))

    def test_filter_body_intersects_with_the_grammar_language(self):
        text = self._decl(filter_body="w.length = 2")
        self.assertTrue(
            text.endswith(
                "def L : Language Letter := g.language ⊓ ({w : List Letter | w.length = 2} : Language Letter)"
            )
        )

    def test_symbol_that_is_terminal_and_nonterminal_is_none(self):
        spec = {**self.SPEC, "nonterminals": ["S", "a"], "rules": [{"lhs": "S", "rhs": ["a"]}]}
        self.assertIsNone(self._decl(spec))


class TestLeanStatementFields(unittest.TestCase):

    def test_new_fields_default_to_no_companion(self):
        stmt = LeanStatement("inductive Letter\n  | a", "def L : Language Letter := ∅", "theorem tfl_main : True")
        self.assertIsNone(stmt.decidable_decl)
        self.assertEqual((stmt.decidable_name, stmt.decidable_covers), ("Lb", "language"))
        self.assertEqual(stmt.name, "tfl_main")

    def test_golden_statements_are_unchanged(self):
        """The pre-existing golden renderings must not move (their exact text
        is pinned in the classes above); spot-check the two natural ones."""
        self.assertEqual(
            render_statement(_load("eval/reg-06.json"), "non_regular").language_decl,
            "def L : Language Letter := {w : List Letter | w = w.reverse}",
        )
        self.assertEqual(
            render_statement(_load("eval/reg-01.json"), "non_regular").language_decl,
            "def L : Language Letter := {w : List Letter | ∃ n : ℕ, "
            "w = List.replicate n Letter.a ++ List.replicate n Letter.b ∧ n ≥ 0}",
        )


# ---------------------------------------------------------------------------
# Docker: the decidable companion, evaluated inside Lean, agrees with an
# independent oracle on every short word -- the mechanical evidence that the
# generated formulation means what the IR means (R-Lean).
# ---------------------------------------------------------------------------

def _is_anbn(w: str) -> bool:
    n = len(w) // 2
    return w == "a" * n + "b" * n


def _is_wwR(w: str) -> bool:
    n = len(w)
    return n % 2 == 0 and w[: n // 2] == w[n // 2:][::-1]


@unittest.skipUnless(is_docker_available(), "Docker with tfl-lean4 image not available")
class TestDecidableMatchesOracle(unittest.TestCase):

    def _check(self, ir, direction, oracle, alphabet=("a", "b"), max_len=6):
        stmt, reason = render_statement_verbose(ir, direction)
        self.assertIsNotNone(stmt, reason)
        return check_against_oracle(self, stmt, list(alphabet), oracle, max_len)

    def test_anbn(self):
        self._check(_load("eval/reg-01.json"), "non_regular", _is_anbn)

    def test_regex_uses_the_ir_oracle(self):
        ir = _load("eval/reg-04.json")
        self._check(ir, "regular", oracle_from_ir(ir))

    def test_palindromes(self):
        self._check(_load("eval/reg-06.json"), "non_regular", lambda w: w == w[::-1])

    def test_palindromes_over_a_sub_alphabet(self):
        ir = _natural_ir("{w ∈ {a}* | w = w^R}", "abc")
        self._check(ir, "non_regular", lambda w: set(w) <= {"a"}, alphabet="abc", max_len=4)

    def test_w_wR(self):
        self._check(_natural_ir("{w wᴿ | w ∈ {a,b}*}"), "non_regular", _is_wwR)

    def test_w_c_wR_with_a_restricted_domain(self):
        def oracle(w):
            n = len(w)
            return n % 2 == 1 and w[n // 2] == "c" and set(w[: n // 2]) <= {"a", "b"} and w[: n // 2] == w[n // 2 + 1:][::-1]

        self._check(_natural_ir("{w c wᴿ | w ∈ {a,b}*}", "abc"), "non_regular", oracle, alphabet="abc", max_len=5)

    def test_copy_template(self):
        self._check(
            _natural_ir("{ww | w in {a,b}*}"), "non_regular",
            lambda w: len(w) % 2 == 0 and w[: len(w) // 2] == w[len(w) // 2:],
        )

    def test_union_of_template_and_exponent(self):
        self._check(
            _natural_ir("{w wᴿ | w ∈ {a,b}*} ∪ {a^n b^n | n >= 0}"), "non_regular",
            lambda w: _is_wwR(w) or _is_anbn(w),
        )

    def test_intersection_of_exponent_and_template(self):
        self._check(
            _natural_ir("{a^n b^m | n >= 0} ∩ {w wᴿ | w ∈ {a,b}*}"), "non_regular",
            lambda w: _is_wwR(w) and w == "a" * w.count("a") + "b" * w.count("b"),
        )

    def test_union_kind_of_regexes_uses_the_ir_oracle(self):
        r1 = {"kind": "regex", "pattern": "(ab)*", "has_backreferences": False}
        r2 = {"kind": "regex", "pattern": "a*b", "has_backreferences": False}
        o1 = oracle_from_ir({"language_spec": {**r1, "alphabet": ["a", "b"]}})
        o2 = oracle_from_ir({"language_spec": {**r2, "alphabet": ["a", "b"]}})
        ir = {"alphabet": ["a", "b"], "language_spec": {"kind": "union", "branches": [r1, r2]}}
        self._check(ir, "regular", lambda w: o1(w) or o2(w))


@unittest.skipUnless(is_docker_available(), "Docker with tfl-lean4 image not available")
class TestNewKindsCompile(unittest.TestCase):

    def _assert_valid_with_sorry(self, ir, direction):
        stmt, reason = render_statement_verbose(ir, direction)
        self.assertIsNotNone(stmt, reason)
        result = check_lean(stmt.render("sorry"), timeout=180)
        self.assertEqual(result["status"], "valid", result)
        self.assertTrue(result["warnings"] and any("sorry" in w.lower() for w in result["warnings"]), result)

    def test_union_of_grammars_compiles(self):
        g = _load("task2_grammar_sasb.json")["language_spec"]
        self._assert_valid_with_sorry({"language_spec": {"kind": "union", "branches": [g, g]}}, "non_regular")

    def test_grammar_and_regex_intersection_compiles(self):
        g = _load("task2_grammar_sasb.json")["language_spec"]
        regex = {"kind": "regex", "pattern": "a*b*", "has_backreferences": False}
        ir = {"alphabet": ["a", "b"], "language_spec": {"kind": "intersection", "branches": [g, regex]}}
        self._assert_valid_with_sorry(ir, "regular")

    def test_restricted_palindrome_compiles(self):
        self._assert_valid_with_sorry(_natural_ir("{w ∈ {a}* | w = w^R}"), "non_regular")


class TestVariableCaseIsSignificant(unittest.TestCase):
    """``w`` and ``W`` are different variables: a backreference must not
    match case-insensitively (the keywords ``reverse``/``in``/``R`` may)."""

    def test_different_variables_are_not_a_template(self):
        for desc in ("{w W}", "{w Wᴿ}", "{w rev(W)}", "{w c W}"):
            self.assertIsNone(render_statement(_natural_ir(desc, "abc"), "non_regular"), desc)

    def test_different_variables_are_not_a_palindrome(self):
        self.assertIsNone(render_statement(_natural_ir("{w = reverse(W)}"), "non_regular"))
        self.assertIsNone(render_statement(_natural_ir("{w | W = w^R}"), "non_regular"))

    def test_keywords_stay_case_insensitive(self):
        want = _lang(_natural_ir("{w wᴿ | w ∈ {a,b}*}"))
        self.assertEqual(_lang(_natural_ir("{w REVERSE(w) | w IN {a,b}*}")), want)
        self.assertEqual(_lang(_natural_ir("{w w^r}")), want)
        self.assertEqual(
            _lang(_natural_ir("{w in {a,b}* | w = REVERSE(w)}")),
            "def L : Language Letter := {w : List Letter | w = w.reverse}",
        )


if __name__ == "__main__":
    unittest.main()
