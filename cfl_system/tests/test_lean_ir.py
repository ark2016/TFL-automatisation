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
            "inductive Letter\n  | a | b | c\n  deriving DecidableEq, Repr",
        )
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Letter := {w : List Letter | ∃ n : ℕ, "
            "w = List.replicate n Letter.a ++ List.replicate n Letter.b "
            "++ List.replicate n Letter.c ∧ n ≥ 0}",
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
            "def L : Language Letter := {w : List Letter | "
            "(∃ n : ℕ, w = List.replicate n Letter.a ++ List.replicate n Letter.b ∧ n ≥ 0) "
            "∨ (∃ n : ℕ, w = List.replicate n Letter.a ++ List.replicate (2 * n) Letter.b ∧ n ≥ 0)}",
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
class TestGoldenStringsCompile(unittest.TestCase):
    """The real statement -- real langlib pumping/Ogden imports included --
    with `sorry` for <PROOF> should type-check with a sorry warning only.
    langlib is a Lake dependency of ``agent_system/docker/tfl_lean`` and
    built into the pinned image (see agent_system.lib.lean_ir's module
    docstring, "langlib availability"), so this exercises the whole
    pipeline, not a langlib-free substitute."""

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

    def test_anbncn_non_cfl_compiles(self):
        self._assert_valid_with_sorry("eval/cfl-01.json", "non_cfl")

    def test_anbncn_cfl_direction_compiles(self):
        self._assert_valid_with_sorry("eval/cfl-01.json", "cfl")

    def test_union_non_cfl_compiles(self):
        self._assert_valid_with_sorry("eval/cfl-18.json", "non_cfl")


if __name__ == "__main__":
    unittest.main()
