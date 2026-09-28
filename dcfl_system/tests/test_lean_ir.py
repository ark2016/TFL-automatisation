"""Tests for dcfl_system.lib.lean_ir -- the DCFL-direction wrapper around
agent_system.lib.lean_ir (docs/VERDICT_POLICY.md R-Lean).

See agent_system/tests/test_lean_ir.py for the shared-machinery tests. These
focus on what this module adds: the ``is_DCF`` predicate/imports, the
letter-domain ``set_builder`` shape real DCFL examples use, and the grammar
shape (exam_04).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_system.lib.lean_ir import LeanStatement
from agent_system.lib.type_check import check_lean, is_docker_available
from dcfl_system.lib.lean_ir import render_statement, render_statement_verbose

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


class TestGoldenSetBuilder(unittest.TestCase):
    """{a^n b^n c^m | n, m >= 1} -- task_anbncm.json, dcfl."""

    def test_anbncm_dcfl(self):
        ir = _load("task_anbncm.json")
        stmt = render_statement(ir, "dcfl")
        self.assertIsInstance(stmt, LeanStatement)
        self.assertEqual(
            stmt.imports,
            ["import TflLean", "import Langlib.Classes.DeterministicContextFree.Definition"],
        )
        self.assertEqual(
            stmt.alphabet_decl,
            "inductive Sym\n  | a | b | c\n  deriving DecidableEq, Fintype, Repr",
        )
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Sym := {w : List Sym | ∃ n_u n_v n_w : ℕ, "
            "w = List.replicate n_u Sym.a ++ List.replicate n_v Sym.b ++ List.replicate n_w Sym.c "
            "∧ n_u ≥ 1 ∧ n_v ≥ 1 ∧ n_w ≥ 1 ∧ n_u = n_v}",
        )
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : is_DCF L")

    def test_non_dcfl_negates(self):
        ir = _load("task_anbncm.json")
        stmt = render_statement(ir, "non_dcfl")
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ is_DCF L")


class TestGoldenGrammarExam04(unittest.TestCase):
    """S -> aSSb | ba | Ab, A -> aAb | a -- dcfl_exam_04, DCFL
    (docs/THEORY.md §1.10)."""

    def test_exam04_dcfl(self):
        ir = _load("task_grammar_aSSb.json")
        stmt = render_statement(ir, "dcfl")
        self.assertEqual(
            stmt.imports,
            [
                "import TflLean",
                "import Langlib.Classes.DeterministicContextFree.Definition",
                "import Mathlib.Computability.ContextFreeGrammar",
            ],
        )
        self.assertIn("inductive NT\n  | S | A\n  deriving DecidableEq, Fintype, Repr", stmt.language_decl)
        self.assertIn(
            "⟨NT.S, [Symbol.terminal Sym.a, Symbol.nonterminal NT.S, "
            "Symbol.nonterminal NT.S, Symbol.terminal Sym.b]⟩",
            stmt.language_decl,
        )
        self.assertTrue(stmt.language_decl.endswith("def L : Language Sym := g.language"))
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : is_DCF L")


class TestUnsupportedReturnsNone(unittest.TestCase):

    def test_too_complex_set_builder_is_none(self):
        """{a^n b* (c^n|b^n) a c* | n > 0} -- disjunction branches, not the
        letter-domain shape, and not plain exponent notation either."""
        ir = _load("task_anb_cnbn.json")
        stmt, reason = render_statement_verbose(ir, "dcfl")
        self.assertIsNone(stmt)
        self.assertTrue(reason)

    def test_reg_and_cfl_directions_are_not_this_modules_job(self):
        ir = _load("task_anbncm.json")
        for direction in ("regular", "non_regular", "cfl", "non_cfl", "ll"):
            stmt, reason = render_statement_verbose(ir, direction)
            self.assertIsNone(stmt)
            self.assertTrue(reason)

    def test_never_raises_on_garbage_input(self):
        for ir in (None, 42, "oops", [], {"language_spec": "nope"}):
            self.assertIsNone(render_statement(ir, "dcfl"))


class TestIdempotent(unittest.TestCase):

    def test_same_ir_same_output(self):
        ir = _load("task_grammar_aSSb.json")
        first = render_statement(ir, "dcfl").render("sorry")
        again = render_statement(ir, "dcfl").render("sorry")
        self.assertEqual(first, again)


@unittest.skipUnless(is_docker_available(), "Docker with tfl-lean4 image not available")
class TestDeclsCompileWithoutLanglibImports(unittest.TestCase):
    """``is_DCF`` itself needs langlib (no Mathlib alternative exists -- see
    agent_system.lib.lean_ir's module docstring), so this checks only the
    Sym/NT/grammar/set-builder declarations parse, against a trivial `L = L`
    goal instead of the real theorem."""

    def _assert_decls_compile(self, ir_name: str) -> None:
        ir = _load(ir_name)
        stmt = render_statement(ir, "dcfl")
        imports = [i for i in stmt.imports if "Langlib" not in i]
        trivial = LeanStatement(stmt.alphabet_decl, stmt.language_decl, "theorem tfl_main : L = L", imports)
        result = check_lean(trivial.render("rfl"), timeout=180)
        self.assertEqual(result["status"], "valid", result)

    def test_setbuilder_decls_compile(self):
        self._assert_decls_compile("task_anbncm.json")

    def test_grammar_decls_compile(self):
        self._assert_decls_compile("task_grammar_aSSb.json")


if __name__ == "__main__":
    unittest.main()
