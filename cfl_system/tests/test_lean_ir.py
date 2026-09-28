"""Tests for cfl_system.lib.lean_ir -- the CFL-direction wrapper around
agent_system.lib.lean_ir (docs/VERDICT_POLICY.md R-Lean).

See agent_system/tests/test_lean_ir.py for the shared-machinery tests
(alphabet_decl, grammar_decl, palindrome, the general None/idempotency
behavior). These tests focus on what this module adds: the
``Language.IsContextFree`` predicate/imports and the *full* exponent-notation
parser (unions, richer arithmetic) via
``cfl_system.lib.exponent_pattern.parse_exponent_pattern``.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_system.lib.lean_ir import LeanStatement
from agent_system.lib.type_check import check_lean, is_docker_available
from cfl_system.lib.lean_ir import render_statement, render_statement_verbose

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


class TestGoldenAnBnCn(unittest.TestCase):
    """{a^n b^n c^n} -- non_cfl (cfl-01, Bar-Hillel pumping)."""

    def test_anbncn_non_cfl(self):
        ir = _load("eval/cfl-01.json")
        stmt = render_statement(ir, "non_cfl")
        self.assertIsInstance(stmt, LeanStatement)
        self.assertEqual(
            stmt.imports,
            [
                "import TflLean",
                "import Mathlib.Computability.ContextFreeGrammar",
                "import Langlib.Classes.ContextFree.Pumping.Pumping",
                "import Langlib.Classes.ContextFree.Basics.Ogden",
            ],
        )
        self.assertEqual(
            stmt.alphabet_decl,
            "inductive Sym\n  | a | b | c\n  deriving DecidableEq, Fintype, Repr",
        )
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Sym := {w : List Sym | ∃ n : ℕ, "
            "w = List.replicate n Sym.a ++ List.replicate n Sym.b "
            "++ List.replicate n Sym.c ∧ n ≥ 0}",
        )
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ L.IsContextFree")

    def test_cfl_direction_no_negation_no_proof_imports(self):
        ir = _load("eval/cfl-01.json")
        stmt = render_statement(ir, "cfl")
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : L.IsContextFree")
        self.assertEqual(
            stmt.imports,
            ["import TflLean", "import Mathlib.Computability.ContextFreeGrammar"],
        )


class TestGoldenUnion(unittest.TestCase):
    """{a^n b^n} union {a^n b^(2n)} -- exercises the real parser's union
    support, which agent_system's own minimal parser does not have."""

    def test_union_body_is_disjunction(self):
        ir = _load("eval/cfl-18.json")
        stmt = render_statement(ir, "non_cfl")
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Sym := {w : List Sym | "
            "(∃ n : ℕ, w = List.replicate n Sym.a ++ List.replicate n Sym.b ∧ n ≥ 0) "
            "∨ (∃ n : ℕ, w = List.replicate n Sym.a ++ List.replicate (2 * n) Sym.b ∧ n ≥ 0)}",
        )


class TestUnsupportedReturnsNone(unittest.TestCase):

    def test_natural_without_notation_is_none(self):
        ir = _load("task11_ai_bj_between.json")
        stmt, reason = render_statement_verbose(ir, "non_cfl")
        self.assertIsNone(stmt)
        self.assertIn("exponent_pattern", reason)

    def test_reg_and_dcfl_directions_are_not_this_modules_job(self):
        ir = _load("eval/cfl-01.json")
        for direction in ("regular", "non_regular", "dcfl", "non_dcfl", "ll"):
            stmt, reason = render_statement_verbose(ir, direction)
            self.assertIsNone(stmt)
            self.assertTrue(reason)

    def test_never_raises_on_garbage_input(self):
        for ir in (None, 42, "oops", [], {"language_spec": "nope"}):
            self.assertIsNone(render_statement(ir, "non_cfl"))


class TestIdempotent(unittest.TestCase):

    def test_same_ir_same_output(self):
        ir = _load("eval/cfl-01.json")
        first = render_statement(ir, "non_cfl").render("sorry")
        again = render_statement(ir, "non_cfl").render("sorry")
        self.assertEqual(first, again)


@unittest.skipUnless(is_docker_available(), "Docker with tfl-lean4 image not available")
class TestStatementCompilesWithoutLanglibImports(unittest.TestCase):
    """The theorem *statement* itself (``Language.IsContextFree`` needs only
    Mathlib) type-checks with `sorry`; the langlib pumping/Ogden imports a
    real ``non_cfl`` proof body needs are a separate, not-yet-wired-in
    dependency (see agent_system.lib.lean_ir's module docstring "Known gap")
    -- so this strips them before compiling, same as validated manually
    while writing this translator."""

    def test_anbncn_statement_compiles(self):
        ir = _load("eval/cfl-01.json")
        stmt = render_statement(ir, "non_cfl")
        mathlib_only = LeanStatement(
            stmt.alphabet_decl,
            stmt.language_decl,
            stmt.theorem_decl,
            [i for i in stmt.imports if "Langlib" not in i],
        )
        result = check_lean(mathlib_only.render("sorry"), timeout=180)
        self.assertEqual(result["status"], "valid", result)


if __name__ == "__main__":
    unittest.main()
