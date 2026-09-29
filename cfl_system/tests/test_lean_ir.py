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
from agent_system.tests.lean_eval import check_against_oracle
from cfl_system.lib.cfl_oracle import _eval_filter_predicate, cfl_oracle_from_ir
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


# ---------------------------------------------------------------------------
# Extended kinds (2026-09-28): grammar / grammar_filter / exists_decomposition
# / word templates / union & intersection composites / decidable companions.
# ---------------------------------------------------------------------------

def _natural_ir(description: str, alphabet=("a", "b")) -> dict:
    return {"language_spec": {"kind": "natural", "alphabet": list(alphabet), "description": description}}


def _cnt(symbol: str, var: str = "w") -> dict:
    return {"kind": "count_symbol", "symbol": symbol, "in_var": var}


def _const(value: int) -> dict:
    return {"kind": "constant", "value": value}


def _length(var: str = "w") -> dict:
    return {"kind": "length", "of_var": var}


_WWVVR = {
    "kind": "exists_decomposition",
    "parts": ["w", "v"],
    "concat_pattern": ["w", "w", "v", "rev(v)"],
    "alphabets": {"w": ["a", "b"], "v": ["a", "b"]},
    "constraints": [],
}
# u v uᴿ with u over {a,b}, v over {c}
_UVUR = {
    "kind": "exists_decomposition",
    "parts": ["u", "v"],
    "concat_pattern": ["u", "v", "rev(u)"],
    "alphabets": {"u": ["a", "b"], "v": ["c"]},
    "constraints": [],
}
# u uᴿ where the *whole* word (`w`) has >= 4 letters and u has exactly one a
_UUR_CONSTRAINED = {
    "kind": "exists_decomposition",
    "parts": ["u"],
    "concat_pattern": ["u", "rev(u)"],
    "constraints": [
        {"op": "geq", "left": _length("w"), "right": _const(4)},
        {"op": "eq", "left": _cnt("a", "u"), "right": _const(1)},
    ],
}
# a part called `w` shadows the whole word, as in the oracle's {"w": word, **bindings}
_WW_SHADOW = {
    "kind": "exists_decomposition",
    "parts": ["w"],
    "concat_pattern": ["w", "w"],
    "constraints": [{"op": "geq", "left": _cnt("a", "w"), "right": _const(1)}],
}


class TestGrammarStatements(unittest.TestCase):
    """IR grammar -> Mathlib ContextFreeGrammar; ε-rules and unit rules are
    ordinary rules."""

    def test_grammar_with_epsilon_and_unit_rules_under_cfl(self):
        ir = _load("task_grammar_filter_49.json")["language_spec"]["grammar"]
        stmt = render_statement({"language_spec": ir}, "cfl")
        self.assertIsNotNone(stmt)
        self.assertIn("⟨NT.S, []⟩", stmt.language_decl)  # S -> ε
        self.assertIn("⟨NT.A, [Symbol.terminal Letter.a]⟩", stmt.language_decl)
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := g.language"))
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : L.IsContextFree")
        self.assertIsNone(stmt.decidable_decl)

    def test_dcfl_exam04_grammar_under_cfl(self):
        ir = json.loads(
            (EXAMPLES_DIR.parent.parent / "dcfl_system" / "examples" / "task_grammar_aSSb.json").read_text(encoding="utf-8")
        )
        stmt = render_statement(ir, "non_cfl")
        self.assertEqual(stmt.alphabet_decl, "inductive Letter\n  | a | b\n  deriving DecidableEq, Repr")
        self.assertIn(
            "⟨NT.S, [Symbol.terminal Letter.a, Symbol.nonterminal NT.S, "
            "Symbol.nonterminal NT.S, Symbol.terminal Letter.b]⟩",
            stmt.language_decl,
        )
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ L.IsContextFree")


class TestGrammarFilter(unittest.TestCase):

    def test_filter_49_golden(self):
        stmt = render_statement(_load("task_grammar_filter_49.json"), "cfl")
        self.assertEqual(
            stmt.imports,
            ["import TflLean", "import Mathlib.Computability.ContextFreeGrammar"],
        )
        self.assertEqual(stmt.alphabet_decl, "inductive Letter\n  | a | b\n  deriving DecidableEq, Repr")
        self.assertIn("inductive NT\n  | S | A\n  deriving DecidableEq, Repr", stmt.language_decl)
        self.assertTrue(
            stmt.language_decl.endswith(
                "def L : Language Letter := g.language ⊓ "
                "({w : List Letter | w.count Letter.a = w.count Letter.b} : Language Letter)"
            )
        )
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : L.IsContextFree")

    def test_companion_covers_only_the_filter(self):
        stmt = render_statement(_load("task_grammar_filter_49.json"), "cfl")
        self.assertEqual(stmt.decidable_name, "Fb")
        self.assertEqual(stmt.decidable_covers, "filter_only")
        self.assertEqual(
            stmt.decidable_decl,
            "def Fb (w : List Letter) : Bool := decide (w.count Letter.a = w.count Letter.b)",
        )

    def test_modular_filter_cfl17(self):
        stmt = render_statement(_load("eval/cfl-17.json"), "non_cfl")
        self.assertIn("{w : List Letter | (w.length) % 4 = 0}", stmt.language_decl)
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ L.IsContextFree")

    def test_natural_language_filter_is_none(self):
        for filt in (
            {"kind": "natural_language_filter", "description": "w is balanced"},
            {"natural_language_filter": "w is balanced"},
        ):
            ir = _load("task_grammar_filter_49.json")
            ir["language_spec"]["filter"] = filt
            stmt, reason = render_statement_verbose(ir, "cfl")
            self.assertIsNone(stmt)
            self.assertIn("grammar_filter", reason)

    def test_unsupported_filter_or_grammar_is_none(self):
        base = _load("task_grammar_filter_49.json")
        for mutate in (
            lambda s: s.update(filter={"op": "eq", "left": _cnt("z"), "right": _const(1)}),   # z not a terminal
            lambda s: s.update(filter={"op": "eq", "left": _cnt("a", "u"), "right": _const(1)}),  # only `w` is bound
            lambda s: s.update(filter=None),
            lambda s: s["grammar"]["rules"].append({"lhs": "S", "rhs": ["q"]}),
            lambda s: s.pop("grammar"),
        ):
            ir = json.loads(json.dumps(base))
            mutate(ir["language_spec"])
            self.assertIsNone(render_statement(ir, "cfl"))


class TestExistsDecomposition(unittest.TestCase):

    def test_wwvvR_golden(self):
        stmt = render_statement(_load("task_wwvvR.json"), "non_cfl")
        self.assertEqual(stmt.alphabet_decl, "inductive Letter\n  | a | b\n  deriving DecidableEq, Repr")
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Letter := {x : List Letter | ∃ p_w p_v : List Letter, "
            "x = p_w ++ p_w ++ p_v ++ p_v.reverse}",
        )
        self.assertEqual(stmt.decidable_name, "Lb")
        self.assertIn("def Cand (x : List Letter) : List (List Letter) :=", stmt.decidable_decl)
        self.assertIn(
            "def Lb (x : List Letter) : Bool := decide (∃ p_w ∈ Cand x, ∃ p_v ∈ Cand x, "
            "x = p_w ++ p_w ++ p_v ++ p_v.reverse)",
            stmt.decidable_decl,
        )

    def test_alphabet_restrictions_and_constraints(self):
        ir = {"alphabet": ["a", "b", "c"], "language_spec": _UVUR}
        stmt = render_statement(ir, "cfl")
        self.assertIn(
            "x = p_u ++ p_v ++ p_u.reverse ∧ (∀ ch ∈ p_u, ch = Letter.a ∨ ch = Letter.b) "
            "∧ (∀ ch ∈ p_v, ch = Letter.c)",
            stmt.language_decl,
        )
        stmt = render_statement({"alphabet": ["a", "b"], "language_spec": _UUR_CONSTRAINED}, "cfl")
        self.assertIn(
            "x = p_u ++ p_u.reverse ∧ (x.length ≥ 4) ∧ (p_u.count Letter.a = 1)", stmt.language_decl,
        )

    def test_a_part_named_w_shadows_the_word(self):
        stmt = render_statement({"alphabet": ["a", "b"], "language_spec": _WW_SHADOW}, "cfl")
        self.assertIn("x = p_w ++ p_w ∧ (p_w.count Letter.a ≥ 1)", stmt.language_decl)

    def test_alphabet_is_the_union_of_the_part_alphabets_when_undeclared(self):
        stmt = render_statement({"language_spec": _UVUR}, "cfl")
        self.assertEqual(stmt.alphabet_decl, "inductive Letter\n  | a | b | c\n  deriving DecidableEq, Repr")

    def test_malformed_is_none(self):
        base = {"alphabet": ["a", "b", "c"]}

        def mk(**kw):
            return {**base, "language_spec": {**_UVUR, **kw}}

        for ir in (
            mk(parts=["u", "v", "z"]),                        # z never occurs in the pattern
            mk(concat_pattern=["u", "q", "rev(u)"]),          # q is not a part
            mk(concat_pattern=["u", "v", "rev(q)"]),
            mk(concat_pattern=[]),
            mk(parts=["u", "u"]),
            mk(alphabets={"u": ["z"], "v": ["c"]}),           # nothing of it is in the alphabet
            mk(alphabets={"zz": ["a"]}),                      # entry for an unknown part
            mk(constraints=[{"op": "eq", "left": _cnt("z", "u"), "right": _const(1)}]),
            mk(constraints="nope"),
        ):
            self.assertIsNone(render_statement(ir, "cfl"), ir["language_spec"])
        no_alphabet = {"language_spec": {**_UVUR, "alphabets": {}}}
        self.assertIsNone(render_statement(no_alphabet, "cfl"))


class TestWordTemplatesCfl(unittest.TestCase):

    def test_ww_is_now_translatable(self):
        stmt = render_statement(_load("eval/cfl-03.json"), "non_cfl")  # {ww | w in {a,b}*}
        self.assertEqual(
            stmt.language_decl,
            "def L : Language Letter := {w : List Letter | ∃ u : List Letter, w = u ++ u}",
        )
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ L.IsContextFree")

    def test_reversal_and_middle_letter(self):
        stmt = render_statement(_natural_ir("{w c wᴿ | w ∈ {a,b}*}", "abc"), "cfl")
        self.assertIn("w = u ++ [Letter.c] ++ u.reverse ∧ ∀ ch ∈ u, ch = Letter.a ∨ ch = Letter.b", stmt.language_decl)

    def test_exponent_notation_still_wins(self):
        stmt = render_statement(_load("eval/cfl-01.json"), "non_cfl")
        self.assertIn("List.replicate n Letter.a", stmt.language_decl)

    def test_prose_and_extra_clauses_are_none(self):
        for desc in ("{w wᴿ | |w| >= 2}", "words that are their own mirror image", "{w wᴿ w}"):
            self.assertIsNone(render_statement(_natural_ir(desc), "cfl"), desc)


class TestCompositesCfl(unittest.TestCase):

    def test_exponent_union_keeps_its_golden_or_form(self):
        stmt = render_statement(_load("eval/cfl-18.json"), "cfl")
        self.assertNotIn("⊔", stmt.language_decl)
        self.assertIn("∨", stmt.language_decl)

    def test_union_kind_of_exponent_and_decomposition(self):
        ir = {
            "alphabet": ["a", "b"],
            "language_spec": {
                "kind": "union",
                "branches": ["{a^n b^n | n >= 0}", {"kind": "exists_decomposition", **{k: v for k, v in _WWVVR.items() if k != "kind"}}],
            },
        }
        stmt, reason = render_statement_verbose(ir, "cfl")
        self.assertIsNotNone(stmt, reason)
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := L_1 ⊔ L_2"))
        self.assertIn("def L_2 : Language Letter := {x : List Letter | ∃ p_w p_v", stmt.language_decl)
        self.assertIn("def Cand_2 (x : List Letter)", stmt.decidable_decl)
        self.assertIn("def Lb (w : List Letter) : Bool := Lb_1 w || Lb_2 w", stmt.decidable_decl)

    def test_natural_text_intersection(self):
        stmt = render_statement(
            _natural_ir("{a^i b^j c^k | i = j or j = k} ∩ {a^n b^m c^m | n, m >= 0}", "abc"), "non_cfl"
        )
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := L_1 ⊓ L_2"))
        self.assertEqual(stmt.theorem_decl, "theorem tfl_main : ¬ L.IsContextFree")

    def test_grammar_filter_inside_a_union(self):
        gf = _load("task_grammar_filter_49.json")["language_spec"]
        ir = {"language_spec": {"kind": "union", "branches": [gf, "{a^n b^n | n >= 0}"]}}
        stmt, reason = render_statement_verbose(ir, "cfl")
        self.assertIsNotNone(stmt, reason)
        self.assertIn("def L_1 : Language Letter := g_1.language ⊓ (", stmt.language_decl)
        self.assertIn("inductive NT_1", stmt.language_decl)
        self.assertIsNone(stmt.decidable_decl)  # filter_only branch: no whole-language companion

    def test_nested_composites(self):
        ir = {
            "alphabet": ["a", "b"],
            "language_spec": {
                "kind": "union",
                "branches": [
                    "{a^n | n >= 0}",
                    {"kind": "intersection", "branches": ["{a^n b^m | n, m >= 0}", "{w wᴿ}"]},
                ],
            },
        }
        stmt, reason = render_statement_verbose(ir, "cfl")
        self.assertIsNotNone(stmt, reason)
        self.assertIn("def L_2 : Language Letter := L_2_1 ⊓ L_2_2", stmt.language_decl)
        self.assertTrue(stmt.language_decl.endswith("def L : Language Letter := L_1 ⊔ L_2"))

    def test_composite_with_untranslatable_branch_is_none(self):
        ir = {"alphabet": ["a", "b"], "language_spec": {"kind": "union", "branches": ["{a^n | n >= 0}", "prose"]}}
        self.assertIsNone(render_statement(ir, "cfl"))

    def test_dcfl_and_reg_directions_still_refuse(self):
        ir = {"alphabet": ["a", "b"], "language_spec": {"kind": "union", "branches": ["{a^n}", "{b^n}"]}}
        for direction in ("regular", "dcfl", "ll"):
            self.assertIsNone(render_statement(ir, direction))


class TestDecidableCompanionsCfl(unittest.TestCase):

    def test_exponent_union_companion_is_bounded_disjunction(self):
        stmt = render_statement(_load("eval/cfl-18.json"), "cfl")
        self.assertEqual(
            stmt.decidable_decl,
            "def Lb (w : List Letter) : Bool := decide ((∃ n : ℕ, n < w.length + 1 ∧ "
            "w = List.replicate n Letter.a ++ List.replicate n Letter.b ∧ n ≥ 0) ∨ "
            "(∃ n : ℕ, n < w.length + 1 ∧ w = List.replicate n Letter.a ++ "
            "List.replicate (2 * n) Letter.b ∧ n ≥ 0))",
        )

    def test_render_ignores_the_companion(self):
        stmt = render_statement(_load("eval/cfl-01.json"), "non_cfl")
        self.assertNotIn("Lb", stmt.render("sorry"))


# ---------------------------------------------------------------------------
# Docker: statements compile; decidable companions agree with the pipeline's
# own oracle (cfl_oracle_from_ir) on all short words.
# ---------------------------------------------------------------------------

def _filter_oracle(ir: dict):
    spec = ir["language_spec"]
    return lambda w: bool(_eval_filter_predicate(spec["filter"], w))


@unittest.skipUnless(is_docker_available(), "Docker with tfl-lean4 image not available")
class TestNewKindsCompile(unittest.TestCase):

    def _assert_valid_with_sorry(self, ir: dict, direction: str) -> None:
        stmt, reason = render_statement_verbose(ir, direction)
        self.assertIsNotNone(stmt, reason)
        result = check_lean(stmt.render("sorry"), timeout=240)
        self.assertEqual(result["status"], "valid", result)
        self.assertTrue(result["warnings"] and any("sorry" in w.lower() for w in result["warnings"]), result)

    def test_grammar_filter_49_cfl(self):
        self._assert_valid_with_sorry(_load("task_grammar_filter_49.json"), "cfl")

    def test_grammar_filter_49_non_cfl(self):
        self._assert_valid_with_sorry(_load("task_grammar_filter_49.json"), "non_cfl")

    def test_grammar_filter_modular_cfl17(self):
        self._assert_valid_with_sorry(_load("eval/cfl-17.json"), "cfl")

    def test_dcfl_exam04_grammar_cfl(self):
        ir = json.loads(
            (EXAMPLES_DIR.parent.parent / "dcfl_system" / "examples" / "task_grammar_aSSb.json").read_text(encoding="utf-8")
        )
        self._assert_valid_with_sorry(ir, "cfl")

    def test_ww_non_cfl(self):
        self._assert_valid_with_sorry(_load("eval/cfl-03.json"), "non_cfl")

    def test_exists_decomposition_wwvvR(self):
        self._assert_valid_with_sorry(_load("task_wwvvR.json"), "cfl")

    def test_union_of_grammar_filter_and_exponent(self):
        gf = _load("task_grammar_filter_49.json")["language_spec"]
        self._assert_valid_with_sorry(
            {"language_spec": {"kind": "union", "branches": [gf, "{a^n b^n | n >= 0}"]}}, "cfl"
        )

    def test_intersection_of_grammar_and_decomposition(self):
        g = _load("task_grammar_filter_49.json")["language_spec"]["grammar"]
        ir = {"alphabet": ["a", "b"], "language_spec": {"kind": "intersection", "branches": [g, _WWVVR]}}
        self._assert_valid_with_sorry(ir, "non_cfl")


@unittest.skipUnless(is_docker_available(), "Docker with tfl-lean4 image not available")
class TestDecidableMatchesOracle(unittest.TestCase):

    def _check(self, ir, direction, oracle, alphabet, max_len, **kw):
        stmt, reason = render_statement_verbose(ir, direction)
        self.assertIsNotNone(stmt, reason)
        return check_against_oracle(self, stmt, list(alphabet), oracle, max_len, **kw)

    def _check_ir_oracle(self, ir, direction, alphabet, max_len, **kw):
        self._check(ir, direction, cfl_oracle_from_ir(ir), alphabet, max_len, **kw)

    # --- exponent notation (the full parser) ------------------------------
    def test_anbncn(self):
        self._check_ir_oracle(_load("eval/cfl-01.json"), "non_cfl", "abc", 5)

    def test_union_anbn_anb2n(self):
        self._check_ir_oracle(_load("eval/cfl-18.json"), "non_cfl", "ab", 6)

    def test_i_eq_j_or_j_eq_k(self):
        self._check_ir_oracle(_load("eval/cfl-02.json"), "non_cfl", "abc", 5)

    def test_chain_condition(self):
        self._check_ir_oracle(_load("eval/cfl-05.json"), "non_cfl", "abc", 5)

    def test_disjunction_over_four_letters(self):
        self._check_ir_oracle(_load("eval/cfl-12.json"), "cfl", "abcd", 4)

    def test_multiplied_exponent(self):
        ir = _load("eval/cfl-11.json")  # a^(6n) b^(6n) c^(6n): members are long
        extra = ["a" * 6 * n + "b" * 6 * n + "c" * 6 * n for n in (1,)]
        extra += ["a" * 6 + "b" * 6 + "c" * 5, "a" * 6 + "b" * 5 + "c" * 6, "a" * 12 + "b" * 6 + "c" * 6]
        self._check_ir_oracle(ir, "non_cfl", "abc", 3, extra_words=extra)

    # --- grammar_filter: the filter part -----------------------------------
    def test_grammar_filter_49_filter(self):
        ir = _load("task_grammar_filter_49.json")
        self._check(ir, "cfl", _filter_oracle(ir), "ab", 6)

    def test_grammar_filter_modular_filter(self):
        ir = _load("eval/cfl-17.json")
        self._check(ir, "cfl", _filter_oracle(ir), "ab", 6)

    def test_filter_with_words_predicates(self):
        ir = _load("task_grammar_filter_49.json")
        ir["language_spec"]["filter"] = {
            "op": "and",
            "operands": [
                {"op": "is_substring", "substring_expr": "ab", "in_var": "w"},
                {"op": "not", "operands": [{"op": "starts_with", "prefix_expr": "b", "of_var": "w"}]},
                {"op": "or", "operands": [
                    {"op": "is_palindrome", "var": "w"},
                    {"op": "geq", "left": {"kind": "count_subword", "in_var": "w", "subword": "aa"}, "right": _const(2)},
                ]},
            ],
        }
        self._check(ir, "cfl", _filter_oracle(ir), "ab", 6)

    # --- exists_decomposition ------------------------------------------------
    def test_wwvvR(self):
        ir = _load("task_wwvvR.json")
        self._check_ir_oracle(ir, "cfl", "ab", 5)

    def test_alphabet_restricted_parts(self):
        ir = {"alphabet": ["a", "b", "c"], "language_spec": _UVUR}
        self._check_ir_oracle(ir, "cfl", "abc", 5)

    def test_constraints_over_whole_word_and_part(self):
        ir = {"alphabet": ["a", "b"], "language_spec": _UUR_CONSTRAINED}
        self._check_ir_oracle(ir, "cfl", "ab", 6)

    def test_part_named_w_shadows_the_word(self):
        ir = {"alphabet": ["a", "b"], "language_spec": _WW_SHADOW}
        self._check_ir_oracle(ir, "cfl", "ab", 6)

    # --- word templates, cross-checked against the decomposition oracle ----
    def test_template_w_wR_vs_decomposition_oracle(self):
        oracle = cfl_oracle_from_ir({
            "language_spec": {"kind": "exists_decomposition", "parts": ["w"], "concat_pattern": ["w", "rev(w)"]}
        })
        self._check(_natural_ir("{w wᴿ | w ∈ {a,b}*}"), "cfl", oracle, "ab", 6)

    def test_template_ww_vs_decomposition_oracle(self):
        oracle = cfl_oracle_from_ir({
            "language_spec": {"kind": "exists_decomposition", "parts": ["w"], "concat_pattern": ["w", "w"]}
        })
        self._check(_load("eval/cfl-03.json"), "non_cfl", oracle, "ab", 6)

    def test_template_w_c_wR_restricted(self):
        oracle = cfl_oracle_from_ir({
            "language_spec": {
                "kind": "exists_decomposition", "parts": ["w", "z"], "concat_pattern": ["w", "z", "rev(w)"],
                "alphabets": {"w": ["a", "b"], "z": ["c"]},
            }
        })
        # z ∈ {c}* also allows "", cc, ...; the template has exactly one c
        self._check(
            _natural_ir("{w c wᴿ | w ∈ {a,b}*}", "abc"), "cfl",
            lambda w: bool(oracle(w)) and w.count("c") == 1,
            "abc", 5,
        )

    # --- composites ----------------------------------------------------------
    def test_union_kind(self):
        exp = {"language_spec": {"kind": "natural", "alphabet": ["a", "b"], "description": "{a^n b^n | n >= 0}"}}
        dec = {"alphabet": ["a", "b"], "language_spec": _WWVVR}
        o1, o2 = cfl_oracle_from_ir(exp), cfl_oracle_from_ir(dec)
        ir = {"alphabet": ["a", "b"], "language_spec": {
            "kind": "union",
            "branches": ["{a^n b^n | n >= 0}", {k: v for k, v in _WWVVR.items()}],
        }}
        self._check(ir, "cfl", lambda w: bool(o1(w)) or bool(o2(w)), "ab", 5)

    def test_intersection_of_two_exponent_patterns(self):
        o1 = cfl_oracle_from_ir(_natural_ir("{a^i b^j c^k | i = j or j = k}", "abc"))
        o2 = cfl_oracle_from_ir(_natural_ir("{a^n b^m c^m | n, m >= 0}", "abc"))
        self._check(
            _natural_ir("{a^i b^j c^k | i = j or j = k} ∩ {a^n b^m c^m | n, m >= 0}", "abc"),
            "non_cfl", lambda w: bool(o1(w)) and bool(o2(w)), "abc", 5,
        )


if __name__ == "__main__":
    unittest.main()
