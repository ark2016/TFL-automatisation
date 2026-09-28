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
    render_statement,
    render_statement_verbose,
)
from agent_system.lib.type_check import check_lean, is_docker_available

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
            "inductive Sym\n  | a | b\n  deriving DecidableEq, Fintype, Repr",
        )
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Sym := {w : List Sym | ∃ n : ℕ, "
            "w = List.replicate n Sym.a ++ List.replicate n Sym.b ∧ n ≥ 0}",
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
            "inductive Sym\n  | a | b\n  deriving DecidableEq, Fintype, Repr",
        )
        self.assertIn("inductive NT\n  | S | A\n  deriving DecidableEq, Fintype, Repr", stmt.language_decl)
        self.assertIn("def g : ContextFreeGrammar Sym :=", stmt.language_decl)
        self.assertIn(
            "⟨NT.S, [Symbol.nonterminal NT.S, Symbol.terminal Sym.a, "
            "Symbol.nonterminal NT.S, Symbol.terminal Sym.b]⟩",
            stmt.language_decl,
        )
        self.assertIn("⟨NT.S, []⟩", stmt.language_decl)
        self.assertTrue(stmt.language_decl.endswith("def L : Language Sym := g.language"))
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
        self.assertIn("def re : RegularExpression Sym :=", stmt.language_decl)
        self.assertIn("RegularExpression.star", stmt.language_decl)
        self.assertIn("RegularExpression.char Sym.a", stmt.language_decl)
        self.assertIn("RegularExpression.char Sym.b", stmt.language_decl)
        self.assertTrue(stmt.language_decl.endswith("def L : Language Sym := re.matches'"))
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : L.IsRegular")


class TestGoldenPalindrome(unittest.TestCase):
    """{w in {a,b}* | w = reverse(w)} -- non_regular (reg-06)."""

    def test_palindrome_via_list_reverse(self):
        ir = _load("eval/reg-06.json")
        stmt = render_statement(ir, "non_regular")
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Sym := {w : List Sym | w = w.reverse}",
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
        self.assertEqual(decl, "inductive Sym\n  | a | b | c\n  deriving DecidableEq, Fintype, Repr")
        self.assertIn("deriving DecidableEq, Fintype, Repr", decl)

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
    never an error -- for every kind of statement this module can produce
    without a langlib dependency (see the module docstring's "Known gap":
    cfl/dcfl statements that reference langlib imports are out of scope for
    this Docker check until langlib is wired into agent_system/docker/tfl_lean)."""

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


if __name__ == "__main__":
    unittest.main()
