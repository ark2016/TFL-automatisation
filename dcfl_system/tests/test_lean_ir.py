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
from agent_system.tests.lean_eval import check_against_oracle
from dcfl_system.lib.lean_ir import render_statement, render_statement_verbose
from dcfl_system.lib.word_sampler import build_membership_oracle_from_ir

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
            "inductive Letter\n  | a | b | c\n  deriving DecidableEq, Repr\n\n"
            "instance : Fintype Letter where\n"
            "  elems := {Letter.a, Letter.b, Letter.c}\n"
            "  complete := by intro x; cases x <;> decide",
        )
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Letter := {w : List Letter | ∃ n_u n_v n_w : ℕ, "
            "w = List.replicate n_u Letter.a ++ List.replicate n_v Letter.b ++ List.replicate n_w Letter.c "
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
        self.assertIn(
            "instance : Fintype Letter where\n"
            "  elems := {Letter.a, Letter.b}\n"
            "  complete := by intro x; cases x <;> decide",
            stmt.alphabet_decl,
        )
        self.assertIn("inductive NT\n  | S | A\n  deriving DecidableEq, Repr", stmt.language_decl)
        self.assertIn(
            "⟨NT.S, [Symbol.terminal Letter.a, Symbol.nonterminal NT.S, "
            "Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩",
            stmt.language_decl,
        )
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := g.language"))
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
class TestGoldenStringsCompile(unittest.TestCase):
    """The real statement -- real ``Langlib.Classes.DeterministicContextFree
    .Definition`` import, real ``is_DCF`` theorem, real ``Fintype Letter``
    instance -- with `sorry` for <PROOF> should type-check with a sorry
    warning only. langlib is a Lake dependency of ``agent_system/docker/
    tfl_lean`` and built into the pinned image (see agent_system.lib.lean_ir's
    module docstring, "langlib availability"), so this exercises the whole
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

    def test_setbuilder_compiles(self):
        self._assert_valid_with_sorry("task_anbncm.json", "dcfl")

    def test_setbuilder_non_dcfl_compiles(self):
        self._assert_valid_with_sorry("task_anbncm.json", "non_dcfl")

    def test_grammar_compiles(self):
        self._assert_valid_with_sorry("task_grammar_aSSb.json", "dcfl")


# ---------------------------------------------------------------------------
# Extended kinds (2026-09-28): decidable companions, word templates (dcfl-02/
# 03), union / intersection composites.
# ---------------------------------------------------------------------------

_FINTYPE = (
    "instance : Fintype Letter where\n"
    "  elems := {Letter.a, Letter.b}\n"
    "  complete := by intro x; cases x <;> decide"
)


def _eval_ir(name: str) -> dict:
    return _load(f"eval/{name}.json")


class TestDecidableCompanionsDcfl(unittest.TestCase):

    def test_letter_domain_companion(self):
        stmt = render_statement(_load("task_anbncm.json"), "dcfl")
        self.assertEqual(
            stmt.decidable_decl,
            "def Lb (w : List Letter) : Bool := decide (∃ n_u : ℕ, n_u < w.length + 1 ∧ "
            "∃ n_v : ℕ, n_v < w.length + 1 ∧ ∃ n_w : ℕ, n_w < w.length + 1 ∧ "
            "w = List.replicate n_u Letter.a ++ List.replicate n_v Letter.b ++ List.replicate n_w Letter.c "
            "∧ n_u ≥ 1 ∧ n_v ≥ 1 ∧ n_w ≥ 1 ∧ n_u = n_v)",
        )
        self.assertNotIn("Lb", stmt.render("sorry"))

    def test_exponent_fallback_companion(self):
        stmt = render_statement(_eval_ir("dcfl-05"), "dcfl")  # union: stays a disjunction
        self.assertNotIn("⊔", stmt.language_decl)
        self.assertTrue(stmt.decidable_decl.startswith("def Lb (w : List Letter) : Bool := decide ((∃ n : ℕ, n < w.length + 1"))

    def test_grammar_has_no_companion(self):
        self.assertIsNone(render_statement(_load("task_grammar_aSSb.json"), "dcfl").decidable_decl)


class TestWordTemplatesDcfl(unittest.TestCase):

    def test_w_reverse_w(self):
        stmt = render_statement(_eval_ir("dcfl-03"), "dcfl")  # {w reverse(w) | w in {a,b}*}
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Letter := {w : List Letter | ∃ u : List Letter, w = u ++ u.reverse}",
        )
        self.assertEqual(stmt.alphabet_decl, f"inductive Letter\n  | a | b\n  deriving DecidableEq, Repr\n\n{_FINTYPE}")
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : is_DCF L")

    def test_w_c_reverse_w(self):
        stmt = render_statement(_eval_ir("dcfl-02"), "non_dcfl")  # {w c reverse(w) | w in {a,b}*}
        self.assertIn("w = u ++ [Letter.c] ++ u.reverse ∧ ∀ ch ∈ u, ch = Letter.a ∨ ch = Letter.b", stmt.language_decl)
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ is_DCF L")

    def test_prose_is_still_none(self):
        stmt, reason = render_statement_verbose(_eval_ir("dcfl-19"), "dcfl")  # w != x x for every decomposition
        self.assertIsNone(stmt)
        self.assertIn("set_builder", reason)


class TestWordPatternFallbackKeepsDomainsAndConstraints(unittest.TestCase):
    """The word_pattern-only fallbacks (exponent notation, palindrome, word
    template) must not silently drop ``variables[].domain`` / ``constraints``:
    that would make the theorem about another language."""

    @staticmethod
    def _ir(word_pattern, variables, constraints, alphabet):
        return {
            "language_spec": {
                "kind": "set_builder",
                "word_pattern": word_pattern,
                "variables": variables,
                "constraints": constraints,
            },
            "alphabet": alphabet,
        }

    def test_template_with_domain_and_length_constraint_is_none(self):
        ir = self._ir(
            "w c reverse(w)",
            [{"name": "w", "domain": "a+", "quantifier": "forall"}],
            [{"kind": "length_cmp", "args": {"left": "w", "op": ">=", "right": "2"}}],
            ["a", "b", "c"],
        )
        for direction in ("dcfl", "non_dcfl"):
            stmt, reason = render_statement_verbose(ir, direction)
            self.assertIsNone(stmt)
            self.assertIn("set_builder", reason)

    def test_palindrome_with_domain_is_none(self):
        ir = self._ir(
            "w reverse(w)",
            [{"name": "w", "domain": "a+", "quantifier": "forall"}],
            [],
            ["a", "b"],
        )
        self.assertIsNone(render_statement(ir, "dcfl"))

    def test_exponent_pattern_with_integer_cmp_is_none(self):
        ir = self._ir(
            "a^n b^n c^m",
            [
                {"name": "n", "domain": "integer >= 0", "quantifier": "exists"},
                {"name": "m", "domain": "integer >= 0", "quantifier": "exists"},
            ],
            [{"kind": "integer_cmp", "args": {"left": "m", "op": ">", "right": "n"}}],
            ["a", "b", "c"],
        )
        for direction in ("dcfl", "non_dcfl"):
            self.assertIsNone(render_statement(ir, direction))

    def test_same_patterns_without_variables_still_render(self):
        ir = self._ir("w c reverse(w)", [], [], ["a", "b", "c"])
        self.assertIsNotNone(render_statement(ir, "dcfl"))
        ir = self._ir("a^n b^n", [], [], ["a", "b"])
        self.assertIsNotNone(render_statement(ir, "dcfl"))


class TestCompositesDcfl(unittest.TestCase):

    def _grammar(self) -> dict:
        return _load("task_grammar_aSSb.json")["language_spec"]

    def test_union_of_grammar_and_set_builder(self):
        ir = {
            "alphabet": ["a", "b"],
            "language_spec": {
                "kind": "union",
                "branches": [self._grammar(), _eval_ir("dcfl-01")["language_spec"]],
            },
        }
        stmt, reason = render_statement_verbose(ir, "dcfl")
        self.assertIsNotNone(stmt, reason)
        self.assertIn("inductive NT_1", stmt.language_decl)
        self.assertIn("def L_2 : Language Letter := {w : List Letter | ∃ n : ℕ,", stmt.language_decl)
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := L_1 ⊔ L_2"))
        self.assertIn("import Mathlib.Computability.ContextFreeGrammar", stmt.imports)
        self.assertIn(_FINTYPE, stmt.alphabet_decl)
        self.assertIsNone(stmt.decidable_decl)

    def test_intersection_of_set_builders_keeps_a_companion(self):
        ir = {
            "alphabet": ["a", "b", "c"],
            "language_spec": {
                "kind": "intersection",
                "branches": [_eval_ir("dcfl-06")["language_spec"], _load("task_anbncm.json")["language_spec"]],
            },
        }
        stmt, reason = render_statement_verbose(ir, "non_dcfl")
        self.assertIsNotNone(stmt, reason)
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := L_1 ⊓ L_2"))
        self.assertIn("def Lb (w : List Letter) : Bool := Lb_1 w && Lb_2 w", stmt.decidable_decl)
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ is_DCF L")

    def test_untranslatable_branch_or_missing_alphabet_is_none(self):
        good = _eval_ir("dcfl-01")["language_spec"]
        bad = _eval_ir("dcfl-19")["language_spec"]
        self.assertIsNone(render_statement({"alphabet": ["a", "b"], "language_spec": {"kind": "union", "branches": [good, bad]}}, "dcfl"))
        self.assertIsNone(render_statement({"language_spec": {"kind": "union", "branches": [good, good]}}, "dcfl"))

    def test_cfl_and_reg_directions_refuse(self):
        ir = {"alphabet": ["a", "b"], "language_spec": {"kind": "union", "branches": [self._grammar()] * 2}}
        for direction in ("cfl", "regular", "ll"):
            self.assertIsNone(render_statement(ir, direction))


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

def _need(oracle):
    """``build_membership_oracle_from_ir`` may answer ``None`` ("unknown"); the
    comparison must never coerce that to False."""
    assert oracle is not None, "no membership oracle for this IR"

    def call(word: str) -> bool:
        answer = oracle(word)
        assert answer is not None, f"oracle has no answer for {word!r}"
        return bool(answer)

    return call


@unittest.skipUnless(is_docker_available(), "Docker with tfl-lean4 image not available")
class TestNewKindsCompile(unittest.TestCase):

    def _assert_valid_with_sorry(self, ir: dict, direction: str) -> None:
        stmt, reason = render_statement_verbose(ir, direction)
        self.assertIsNotNone(stmt, reason)
        result = check_lean(stmt.render("sorry"), timeout=240)
        self.assertEqual(result["status"], "valid", result)
        self.assertTrue(result["warnings"] and any("sorry" in w.lower() for w in result["warnings"]), result)

    def test_grammar_non_dcfl(self):
        self._assert_valid_with_sorry(_load("task_grammar_aSSb.json"), "non_dcfl")

    def test_template_w_reverse_w(self):
        self._assert_valid_with_sorry(_eval_ir("dcfl-03"), "non_dcfl")

    def test_template_w_c_reverse_w(self):
        self._assert_valid_with_sorry(_eval_ir("dcfl-02"), "dcfl")

    def test_union_of_grammar_and_set_builder(self):
        ir = {
            "alphabet": ["a", "b"],
            "language_spec": {
                "kind": "union",
                "branches": [_load("task_grammar_aSSb.json")["language_spec"], _eval_ir("dcfl-01")["language_spec"]],
            },
        }
        self._assert_valid_with_sorry(ir, "non_dcfl")


@unittest.skipUnless(is_docker_available(), "Docker with tfl-lean4 image not available")
class TestDecidableMatchesOracle(unittest.TestCase):

    def _check(self, ir, direction, oracle, alphabet, max_len, **kw):
        stmt, reason = render_statement_verbose(ir, direction)
        self.assertIsNotNone(stmt, reason)
        return check_against_oracle(self, stmt, list(alphabet), oracle, max_len, **kw)

    def _check_ir_oracle(self, ir, direction, alphabet, max_len, **kw):
        self._check(ir, direction, _need(build_membership_oracle_from_ir(ir)), alphabet, max_len, **kw)

    def test_letter_domain_anbncm(self):
        self._check_ir_oracle(_load("task_anbncm.json"), "dcfl", "abc", 5)

    def test_exponent_union(self):
        self._check_ir_oracle(_eval_ir("dcfl-05"), "non_dcfl", "ab", 6)

    def test_disjunctive_condition(self):
        self._check_ir_oracle(_eval_ir("dcfl-06"), "dcfl", "abc", 5)

    def test_le_or_eq_with_lower_bounds(self):
        self._check_ir_oracle(_eval_ir("dcfl-12"), "dcfl", "abc", 5)

    def test_chain_ge(self):
        self._check_ir_oracle(_eval_ir("dcfl-14"), "dcfl", "ab", 6)

    def test_union_with_three_letters(self):
        self._check_ir_oracle(_eval_ir("dcfl-18"), "non_dcfl", "abc", 5)

    def test_not_equal_condition(self):
        self._check_ir_oracle(_eval_ir("dcfl-20"), "dcfl", "abc", 5)

    def test_template_w_reverse_w(self):
        n = lambda w: len(w) // 2
        self._check(
            _eval_ir("dcfl-03"), "dcfl",
            lambda w: len(w) % 2 == 0 and w[: n(w)] == w[n(w):][::-1], "ab", 6,
        )

    def test_template_w_c_reverse_w(self):
        def oracle(w):
            k = len(w) // 2
            return len(w) % 2 == 1 and w[k] == "c" and set(w[:k]) <= {"a", "b"} and w[:k] == w[k + 1:][::-1]

        self._check(_eval_ir("dcfl-02"), "dcfl", oracle, "abc", 5)

    def test_intersection_of_set_builders(self):
        o1 = _need(build_membership_oracle_from_ir(_eval_ir("dcfl-06")))
        o2 = _need(build_membership_oracle_from_ir(_load("task_anbncm.json")))
        ir = {
            "alphabet": ["a", "b", "c"],
            "language_spec": {
                "kind": "intersection",
                "branches": [_eval_ir("dcfl-06")["language_spec"], _load("task_anbncm.json")["language_spec"]],
            },
        }
        self._check(ir, "dcfl", lambda w: o1(w) and o2(w), "abc", 5)


if __name__ == "__main__":
    unittest.main()
