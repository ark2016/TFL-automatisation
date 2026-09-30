"""Tests for docs/VERDICT_POLICY.md — trust taxonomy and verdict gate
(TODO §1, R6 for agent_system).

Covers the §6 policy scenarios that apply to the regularity pipeline:
  1. constructive artifact refuted, no destructive proof -> inconclusive,
     confidence <= 0.40, downgrades non-empty.
  2. constructive bounded_pass + destructive well_formed both present ->
     contradiction: true, confidence <= 0.50.
  3. Lean verified (status=="valid", sorry_count==0) -> confidence may be 0.98.
  4. destructive proof with well_formed trust only (no oracle) -> verdict
     stands, confidence <= 0.55.
  5. test_result.fail on the correct non_regular verdict, but a destructive
     proof with oracle-verified words (bounded_pass) exists -> success
     capped at 0.85, not "failure 0.0" (R6).

Plus focused unit tests for the claim_verifier step-2 semantic checks
(pumping word-family instantiation + partition search, Myhill-Nerode
pair/context instantiation) and the destructive-trust combinator.
"""

import unittest
from unittest.mock import patch

from agent_system.config import MAX_CALLS_PER_AGENT
from agent_system.graph import assemble_result_node, run_retry_planner_node, run_specialist_node
from agent_system.lib.claim_verifier import (
    CONFIDENCE_CAPS,
    closure_trust_from_verification,
    compute_destructive_trust,
    detect_pattern_param,
    instantiate_word_pattern,
    verify_nerode_claim,
    verify_pumping_claim,
)


def _state(**overrides):
    """Minimal state dict — assemble_result_node only reads these keys."""
    state = {
        "evidence": {},
        "errors": [],
        "reasoning_output": None,
        "test_result": None,
        "hypothesis": {},
        "closure_verification": None,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Oracles used by the fixtures below
# ---------------------------------------------------------------------------

def _anbn_oracle(word: str) -> bool:
    """L = {a^n b^n | n >= 0}."""
    n_a = 0
    while n_a < len(word) and word[n_a] == "a":
        n_a += 1
    rest = word[n_a:]
    return rest == "b" * n_a and n_a + len(rest) == len(word)


def _all_words_oracle(word: str) -> bool:
    """Trivial regular oracle: everything is in L (used for a bogus,
    refutable pumping proof)."""
    return True


# ---------------------------------------------------------------------------
# §6 scenario 1 — constructive refuted, no destructive proof -> inconclusive
# ---------------------------------------------------------------------------

class TestScenario1RefutedConstructiveOnly(unittest.TestCase):

    def test_inconclusive_not_failure_zero(self):
        state = _state(
            test_result={
                "status": "fail",
                "counterexample": {"word": "ab", "oracle_says": True, "automaton_says": False},
            },
        )
        result = assemble_result_node(state)["result"]

        self.assertEqual(result["status"], "partial")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["not_verified"])
        self.assertFalse(result["verdict_gate"]["contradiction"])
        self.assertTrue(result["verdict_gate"]["downgrades"])
        self.assertEqual(result["evidence"]["basis"], "constructive_failure_only")


# ---------------------------------------------------------------------------
# §6 scenario 2 — contradiction: constructive bounded_pass + destructive
# well_formed both present -> contradiction True, confidence <= 0.50
# ---------------------------------------------------------------------------

class TestScenario2Contradiction(unittest.TestCase):

    def test_contradiction_caps_at_050(self):
        state = _state(
            test_result={"status": "pass", "tested": 200},
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "well_formed", "reason": "no oracle"},
            },
        )
        result = assemble_result_node(state)["result"]

        self.assertTrue(result["verdict_gate"]["contradiction"])
        self.assertEqual(result["status"], "partial")
        self.assertLessEqual(result["confidence"], 0.50)


# ---------------------------------------------------------------------------
# §6 scenario 2b — contradiction where one side is `verified`: a Lean-checked
# non_regular proof (no `sorry`) next to a constructive artifact that still
# passes its (sample-based, bounded_pass) oracle_test. docs/VERDICT_POLICY.md
# R3: the `verified` side still wins, at the full 0.98 `verified` ceiling
# (superseded R3 wording that capped it below `verified`).
# ---------------------------------------------------------------------------

class TestScenario2VerifiedWins(unittest.TestCase):

    def test_lean_verified_opposite_direction_flips_verdict_at_098(self):
        """docs/VERDICT_POLICY.md R-Lean: a Lean `proved` result for the
        OPPOSITE direction of `reasoning_verdict` outranks a passing (but
        only sample-based, bounded_pass) oracle_test -- the verdict flips
        to the proven direction at the full 0.98 `verified` ceiling, not
        a capped contradiction (superseded R3 wording: a machine-checked
        proof is never capped below `verified`)."""
        state = _state(
            test_result={"status": "pass", "tested": 200},
            formalization={"status": "proved", "direction": "non_regular", "axioms": []},
            reasoning_output={"evidence": {"verdict": "regular", "confidence": 0.99}},
        )
        result = assemble_result_node(state)["result"]

        self.assertFalse(result["verdict_gate"]["contradiction"])
        self.assertTrue(any("lean proof of 'non_regular' overrides reasoning verdict"
                            in d for d in result["verdict_gate"]["downgrades"]))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["confidence"], 0.98)
        self.assertEqual(result["evidence"]["reasoning"]["verdict"], "non_regular")


# ---------------------------------------------------------------------------
# §6 scenario 3 — Lean verified (status=="valid" and sorry_count==0)
# ---------------------------------------------------------------------------

class TestScenario3LeanVerified(unittest.TestCase):

    def test_lean_proved_matching_direction_reaches_098(self):
        state = _state(
            formalization={"status": "proved", "direction": "non_regular", "axioms": ["propext"]},
            reasoning_output={"evidence": {"verdict": "non_regular", "confidence": 0.99}},
        )
        result = assemble_result_node(state)["result"]

        self.assertEqual(result["status"], "success")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["verified"])
        self.assertAlmostEqual(result["confidence"], CONFIDENCE_CAPS["verified"])
        basis_agents = {b["agent"]: b for b in result["verdict_gate"]["basis"]}
        self.assertEqual(basis_agents["formalizer"]["trust"], "verified")
        self.assertEqual(basis_agents["formalizer"]["basis"], "lean_proof")
        # The Lean proof itself (statement/proof_body/axioms/attempts) must
        # be readable off the final result too, not just implied by the
        # gate's basis entry -- otherwise a report built from `result` alone
        # has no way to show what was actually proved.
        self.assertEqual(result["evidence"]["formalization"]["status"], "proved")
        self.assertEqual(result["evidence"]["formalization"]["axioms"], ["propext"])

    def test_lean_proved_matching_direction_low_reasoning_confidence_still_098(self):
        """R-Lean: `proved` earns the full 0.98 ceiling outright -- it must
        NOT be further bounded by a low reasoning_confidence (regression for
        the gate asymmetry where the matching-direction branch used
        ``min(reasoning_confidence, 0.98)`` while the opposite-direction
        branch used 0.98 unconditionally; both directions must behave the
        same, per docs/VERDICT_POLICY.md R-Lean)."""
        state = _state(
            formalization={"status": "proved", "direction": "non_regular", "axioms": []},
            reasoning_output={"evidence": {"verdict": "non_regular", "confidence": 0.6}},
        )
        result = assemble_result_node(state)["result"]

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["confidence"], CONFIDENCE_CAPS["verified"])
        self.assertAlmostEqual(result["confidence"], 0.98)

    def test_lean_proved_opposite_direction_branch_reachable(self):
        """The verdict-flip branch (a machine-checked `proved` result for
        the OPPOSITE direction of `reasoning_verdict`) must be reachable on
        its own -- not only as a side effect of the constructive/destructive
        contradiction set up in TestScenario2VerifiedWins. Confidence must
        reach the full 0.98 ceiling regardless of reasoning_confidence."""
        state = _state(
            formalization={"status": "proved", "direction": "regular", "axioms": []},
            reasoning_output={"evidence": {"verdict": "non_regular", "confidence": 0.6}},
        )
        result = assemble_result_node(state)["result"]

        self.assertFalse(result["verdict_gate"]["contradiction"])
        self.assertTrue(any("lean proof of 'regular' overrides reasoning verdict"
                            in d for d in result["verdict_gate"]["downgrades"]))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["confidence"], CONFIDENCE_CAPS["verified"])
        self.assertEqual(result["evidence"]["reasoning"]["verdict"], "regular")

    def test_lean_with_sorry_is_not_verified(self):
        """`has_sorry` must NOT reach the `verified` cap (TODO §1 ⚪, R1:
        not evidence either way)."""
        state = _state(
            formalization={"status": "has_sorry", "direction": "non_regular", "axioms": []},
        )
        result = assemble_result_node(state)["result"]

        self.assertNotEqual(result["confidence"], CONFIDENCE_CAPS["verified"])

    def test_lean_error_is_not_verified(self):
        state = _state(
            formalization={"status": "error", "direction": "non_regular", "errors": ["boom"]},
        )
        result = assemble_result_node(state)["result"]

        self.assertNotEqual(result["confidence"], CONFIDENCE_CAPS["verified"])

    def test_lean_not_formalizable_does_not_change_gate(self):
        """Missing/`not_formalizable` formalization must behave exactly
        like no formalization at all -- falls through to whatever the
        rest of the evidence supports (here: nothing -> partial)."""
        state = _state(
            formalization={"status": "not_formalizable", "direction": None,
                            "reason": "no statement for this ir/direction"},
        )
        result = assemble_result_node(state)["result"]

        self.assertEqual(result["status"], "partial")
        self.assertNotEqual(result["confidence"], CONFIDENCE_CAPS["verified"])
        # Still surfaced (any non-None formalization dict is, whatever its
        # status) -- a `not_formalizable` reason is useful in a report too.
        self.assertEqual(
            result["evidence"]["formalization"]["status"], "not_formalizable",
        )

    def test_no_formalization_leaves_evidence_untouched(self):
        """`state["formalization"]` missing/`None` (formalize_node disabled
        or skipped) must not add an `evidence["formalization"]` key at all --
        distinct from an explicit `not_formalizable` status above."""
        state = _state()
        result = assemble_result_node(state)["result"]

        self.assertNotIn("formalization", result["evidence"])


# ---------------------------------------------------------------------------
# §6 scenario 4 — destructive proof with well_formed trust only (no oracle)
# -> verdict stands, confidence <= 0.55
# ---------------------------------------------------------------------------

class TestScenario4WellFormedOnly(unittest.TestCase):

    def test_well_formed_destructive_caps_at_055(self):
        state = _state(
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "well_formed", "reason": "no oracle available"},
            },
        )
        result = assemble_result_node(state)["result"]

        self.assertEqual(result["status"], "success")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["well_formed"])


# ---------------------------------------------------------------------------
# §6 scenario 5 (R6) — test_result.fail on a correct non_regular verdict,
# but destructive proof oracle-verified (bounded_pass) -> success <= 0.85,
# never "failure 0.0"
# ---------------------------------------------------------------------------

class TestScenario5RegFailWithDestructiveProof(unittest.TestCase):

    def test_fail_with_verified_destructive_proof_is_not_failure_zero(self):
        state = _state(
            test_result={
                "status": "fail",
                "counterexample": {"word": "ab", "oracle_says": True, "automaton_says": False},
            },
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "bounded_pass", "checked_p": [2, 3, 4]},
            },
        )
        result = assemble_result_node(state)["result"]

        self.assertEqual(result["status"], "success")
        self.assertNotEqual(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["bounded_pass"])
        self.assertTrue(
            any("R6" in d for d in result["verdict_gate"]["downgrades"]),
        )


# ---------------------------------------------------------------------------
# claim_verifier: pumping step-2 semantic check
# ---------------------------------------------------------------------------

class TestPumpingStep2Check(unittest.TestCase):

    def test_structural_only_without_oracle_is_well_formed(self):
        pumping_output = {
            "status": "success",
            "evidence": {"verdict": "non_regular", "word_family": "a^n b^n"},
        }
        result = verify_pumping_claim(pumping_output, oracle=None)
        self.assertEqual(result["trust"], "well_formed")

    def test_valid_anbn_finite_pumping_checks_remain_well_formed(self):
        pumping_output = {
            "status": "success",
            "evidence": {"verdict": "non_regular", "word_family": "a^p b^p"},
        }
        result = verify_pumping_claim(pumping_output, oracle=_anbn_oracle)
        self.assertEqual(result["trust"], "well_formed")
        self.assertEqual(result["checked_p"], [2, 3, 4])
        self.assertTrue(all(d["escaping_partitions"] == d["sampled_partitions"] for d in result["diagnostics"]))

    def test_surviving_sampled_exponents_are_inconclusive(self):
        """The finite oracle interface cannot establish all exponents."""
        pumping_output = {
            "status": "success",
            "evidence": {"verdict": "non_regular", "word_family": "a^p b^p"},
        }
        result = verify_pumping_claim(pumping_output, oracle=_all_words_oracle)
        self.assertEqual(result["trust"], "well_formed")
        self.assertNotIn("counterexample", result)
        self.assertIsNotNone(result["diagnostics"][0]["surviving_sampled_partition"])

    def test_finite_singleton_can_close_all_sampled_partitions(self):
        proof = {"status": "success", "evidence": {"verdict": "non_regular", "word_family": "a^(0p+10)"}}
        result = verify_pumping_claim(proof, oracle=lambda word: word == "a" * 10)
        self.assertEqual(result["trust"], "well_formed")
        self.assertEqual(result["checked_p"], [2, 3, 4])
        self.assertTrue(all(d["escaping_partitions"] == d["sampled_partitions"] for d in result["diagnostics"]))

    def test_cofinite_unary_survivor_can_escape_at_an_unsampled_exponent(self):
        proof = {"status": "success", "evidence": {"verdict": "non_regular", "word_family": "a^p"}}
        result = verify_pumping_claim(proof, oracle=lambda word: len(word) != 4)
        # At p=2, x='', y='a', z='a' survives 0 and 2 but escapes at 3.
        first = result["diagnostics"][0]
        self.assertEqual(first["surviving_sampled_partition"], {"x": "", "y": "a", "z": "a"})
        self.assertEqual(first["tested_exponents"], [0, 2])
        # The actual refutation is the explicit witness at p=4, not this split.
        self.assertEqual(result["trust"], "refuted")
        self.assertEqual(result["counterexample"]["p"], 4)

    def test_unknown_witness_membership_is_not_refuted(self):
        proof = {"status": "success", "evidence": {"verdict": "non_regular", "word_family": "a^p"}}
        result = verify_pumping_claim(proof, oracle=lambda word: None)
        self.assertEqual(result["trust"], "well_formed")
        self.assertEqual(result["witnesses"], [])

    def test_unknown_pumped_membership_is_not_recorded_as_nonmembership(self):
        proof = {"status": "success", "evidence": {"verdict": "non_regular", "word_family": "a^p b^p"}}
        result = verify_pumping_claim(proof, oracle=lambda word: True if _anbn_oracle(word) else None)
        self.assertEqual(result["trust"], "well_formed")
        self.assertTrue(all(w["expected_in_l"] is True for w in result["witnesses"]))
        self.assertTrue(all(d["unknown_memberships"] > 0 for d in result["diagnostics"]))

    def test_short_witness_refutes_explicit_length_requirement(self):
        proof = {"status": "success", "evidence": {"verdict": "non_regular", "word_family": "a^(p-1)"}}
        result = verify_pumping_claim(proof, oracle=lambda word: True)
        self.assertEqual(result["trust"], "refuted")
        self.assertIn("shorter", result["reason"])

    def test_two_letter_family_falls_back_to_well_formed(self):
        """Patterns with more than one free variable are out of scope for
        the minimal step-2 parser -> well_formed, never refuted/bounded_pass."""
        pumping_output = {
            "status": "success",
            "evidence": {"verdict": "non_regular", "word_family": "a^n b^m"},
        }
        result = verify_pumping_claim(pumping_output, oracle=_anbn_oracle)
        self.assertEqual(result["trust"], "well_formed")

    def test_failed_agent_is_not_verified(self):
        pumping_output = {"status": "failure", "evidence": {}}
        result = verify_pumping_claim(pumping_output, oracle=_anbn_oracle)
        self.assertEqual(result["trust"], "not_verified")


# ---------------------------------------------------------------------------
# claim_verifier: Myhill-Nerode step-2 semantic check
# ---------------------------------------------------------------------------

class TestNerodeStep2Check(unittest.TestCase):

    def _proof(self):
        return {
            "status": "success",
            "proof": {
                "word_sequence": {"family": "a^i", "parameter": "i"},
                "distinguishing_contexts": [
                    {"pair": ["a^i", "a^j"], "condition": "i < j", "context": "b^i"},
                ],
            },
        }

    def test_valid_anbn_distinguishability_is_bounded_pass(self):
        result = verify_nerode_claim(self._proof(), oracle=_anbn_oracle)
        self.assertEqual(result["trust"], "bounded_pass")
        self.assertEqual(result["checked_pairs"], [[2, 3], [2, 4], [3, 4]])

    def test_non_distinguishing_context_is_refuted(self):
        proof = self._proof()
        # A context that never distinguishes anything under a trivial
        # regular oracle -- both words are always in L.
        result = verify_nerode_claim(proof, oracle=_all_words_oracle)
        self.assertEqual(result["trust"], "refuted")

    def test_without_oracle_is_well_formed(self):
        result = verify_nerode_claim(self._proof(), oracle=None)
        self.assertEqual(result["trust"], "well_formed")

    def test_unknown_membership_on_either_side_is_inconclusive(self):
        for oracle in (lambda word: None, lambda word: True if _anbn_oracle(word) else None):
            result = verify_nerode_claim(self._proof(), oracle=oracle)
            self.assertEqual(result["trust"], "well_formed")
            self.assertEqual(result["witnesses"], [])

    def test_empty_distinguishing_suffix_is_valid(self):
        proof = self._proof()
        proof["proof"]["distinguishing_contexts"][0]["context"] = ""
        result = verify_nerode_claim(proof, oracle=lambda word: word == "aa")
        self.assertEqual(result["trust"], "refuted")
        self.assertEqual(result["counterexample"]["context"], "")
        self.assertEqual(result["counterexample"]["i"], 3)
        self.assertTrue(any(w["word"] == "aa" and w["expected_in_l"] for w in result["witnesses"]))

    def test_context_using_the_second_pair_variable_is_matched_by_name(self):
        """docs/VERDICT_POLICY.md fix (reviewer finding): the context always
        used to be instantiated at pair[0]'s index (m), even when it's
        written in pair[1]'s variable (j) -- silently mis-evaluating a
        correct proof. L = {a^p b^q : q <= p}; context 'b^j' (correctly
        instantiated at n, pair[1]'s value) distinguishes a^i from a^j for
        every (i,j): a^i b^j has q=j > p=i (False), a^j b^j has q=j <= p=j
        (True). Instantiating the context at m instead (the old bug) gives
        a^i b^i (True) vs a^j b^i (True, since i<j) -- both True, a false
        'does not distinguish' -> refuted."""
        def oracle_b_le_a(word: str) -> bool:
            i = 0
            while i < len(word) and word[i] == "a":
                i += 1
            p = i
            rest = word[i:]
            if any(ch != "b" for ch in rest):
                return False
            return len(rest) <= p

        proof = {
            "status": "success",
            "proof": {
                "word_sequence": {"family": "a^i", "parameter": "i"},
                "distinguishing_contexts": [
                    {"pair": ["a^i", "a^j"], "condition": "i < j", "context": "b^j"},
                ],
            },
        }
        result = verify_nerode_claim(proof, oracle=oracle_b_le_a)
        self.assertEqual(result["trust"], "bounded_pass")
        self.assertEqual(result["checked_pairs"], [[2, 3], [2, 4], [3, 4]])


# ---------------------------------------------------------------------------
# claim_verifier: word-pattern instantiation helpers
# ---------------------------------------------------------------------------

class TestWordPatternInstantiation(unittest.TestCase):

    def test_detect_param_single_variable(self):
        self.assertEqual(detect_pattern_param("a^(3p+2) b^p"), "p")
        self.assertEqual(detect_pattern_param("a^n b a^n"), "n")

    def test_detect_param_rejects_multiple_variables(self):
        self.assertIsNone(detect_pattern_param("a^n b^m"))

    def test_instantiate_with_literal_letters(self):
        self.assertEqual(instantiate_word_pattern("a^n b a^n", "n", 3), "aaabaaa")

    def test_instantiate_with_arithmetic_expression(self):
        self.assertEqual(instantiate_word_pattern("a^(3p+2) b^p", "p", 2), "a" * 8 + "b" * 2)

    def test_instantiate_unparseable_pattern_is_none(self):
        self.assertIsNone(instantiate_word_pattern("some free-form description", "p", 2))


# ---------------------------------------------------------------------------
# claim_verifier: destructive-trust combinator (R1/R3/R6)
# ---------------------------------------------------------------------------

class TestComputeDestructiveTrust(unittest.TestCase):

    def test_no_destructive_evidence_is_none(self):
        self.assertIsNone(compute_destructive_trust({})["trust"])

    def test_picks_strongest_non_refuted(self):
        evidence = {
            "pumping_verification": {"trust": "well_formed"},
            "nerode_verification": {"trust": "bounded_pass"},
        }
        result = compute_destructive_trust(evidence)
        self.assertEqual(result["trust"], "bounded_pass")
        self.assertEqual(result["best_agent"], "nerode")

    def test_all_refuted_is_refuted(self):
        evidence = {"pumping_verification": {"trust": "refuted"}}
        self.assertEqual(compute_destructive_trust(evidence)["trust"], "refuted")

    def test_closure_empirical_index_estimate_stays_well_formed(self):
        """Finite-depth class growth cannot establish infinite index."""
        trust = closure_trust_from_verification(
            {"status": "success"}, {"status": "verified", "confidence": 0.95},
        )
        self.assertEqual(trust, "well_formed")

    def test_closure_without_verification_is_well_formed(self):
        trust = closure_trust_from_verification({"status": "success"}, None)
        self.assertEqual(trust, "well_formed")

    def test_legacy_empirical_disproved_status_is_not_a_counterexample(self):
        trust = closure_trust_from_verification(
            {"status": "success"}, {"status": "disproved"},
        )
        self.assertEqual(trust, "well_formed")


# ---------------------------------------------------------------------------
# docs/VERDICT_POLICY.md R1/R2 fix (reviewer finding, verdict-gate branch):
# assemble_result_node used to grant success<=0.55 to ANY reasoning verdict
# with no deterministic evidence at all, and separately ignored which
# DIRECTION the reasoning verdict claimed — a destructive proof supporting
# non_regular was silently treated as backing whatever verdict reasoning
# proposed, even "regular".
# ---------------------------------------------------------------------------

class TestReasoningVerdictDirectionIsChecked(unittest.TestCase):

    def test_non_regular_with_refuted_destructive_and_no_dfa_is_partial(self):
        """Probe 1: DFA never built, pumping_verification refuted, reasoning
        claims non_regular 0.92 -> must NOT be success 0.55 (nor any success)."""
        state = _state(
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "refuted", "counterexample": "aabb"},
            },
            reasoning_output={"evidence": {"verdict": "non_regular", "confidence": 0.92}},
        )
        result = assemble_result_node(state)["result"]
        self.assertEqual(result["status"], "partial")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["not_verified"])
        self.assertEqual(result["evidence"]["basis"], "constructive_failure_only")

    def test_no_evidence_at_all_with_reasoning_verdict_is_partial(self):
        """Probe 2: zero evidence anywhere, reasoning still proposes a
        verdict -> must NOT be an unconditional success 0.55."""
        state = _state(
            reasoning_output={"evidence": {"verdict": "non_regular", "confidence": 0.9}},
        )
        result = assemble_result_node(state)["result"]
        self.assertNotEqual(result["status"], "success")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["not_verified"])

    def test_regular_verdict_not_backed_by_refuted_dfa_is_partial(self):
        """Probe 3 (R2 violation): DFA refuted, reasoning says 'regular',
        pumping well_formed (argues non_regular) -> the 'regular' claim must
        downgrade, not ride along UNCHANGED on the (wrongly-directed)
        destructive evidence as a 'regular' success 0.55. docs/VERDICT_POLICY.md
        R4' (this node always runs post-retry, i.e. with the budget already
        exhausted): the gate does not just null the verdict here -- a
        well_formed destructive proof IS the strongest admissible basis
        still standing, so it flips the verdict itself to non_regular
        (capped at well_formed's own 0.55 ceiling), not 'regular'."""
        state = _state(
            test_result={
                "status": "fail",
                "counterexample": {"word": "ab", "oracle_says": True, "automaton_says": False},
            },
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "well_formed", "reason": "no oracle available"},
            },
            reasoning_output={"evidence": {"verdict": "regular", "confidence": 0.9}},
        )
        result = assemble_result_node(state)["result"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["evidence"]["reasoning"]["verdict"], "non_regular")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["well_formed"])
        self.assertTrue(
            any("strongest admissible basis" in d for d in result["verdict_gate"]["downgrades"])
        )


# ---------------------------------------------------------------------------
# docs/VERDICT_POLICY.md R4' — retry budget exhausted (assemble_result_node
# always runs post-retry): the gate picks the strongest admissible basis
# instead of defaulting straight to inconclusive/`failure`. Precedent:
# live-run eval 2026-09-27, cfl-07/cfl-12 ended `failure 0.0` despite a
# well_formed destructive proof on record.
# ---------------------------------------------------------------------------

class TestR4PrimeStrongestAdmissibleBasis(unittest.TestCase):

    def test_destructive_well_formed_rescues_unsupported_regular_proposal(self):
        """reasoning proposes 'regular' with no passing oracle_test at all
        (test_result never ran); pumping is well_formed and argues
        non_regular -> non_regular <= 0.55, not inconclusive/`failure`."""
        state = _state(
            test_result=None,
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "well_formed", "reason": "no oracle available"},
            },
            reasoning_output={"evidence": {"verdict": "regular", "confidence": 0.9}},
        )
        result = assemble_result_node(state)["result"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["evidence"]["reasoning"]["verdict"], "non_regular")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["well_formed"])
        self.assertTrue(
            any("strongest admissible basis" in d for d in result["verdict_gate"]["downgrades"])
        )

    def test_no_destructive_evidence_stays_partial_not_failure(self):
        """Same unsupported 'regular' proposal, but with no destructive proof
        of any kind on record -> partial/inconclusive, confidence <= 0.40,
        never `failure`."""
        state = _state(
            test_result=None,
            evidence={},
            reasoning_output={"evidence": {"verdict": "regular", "confidence": 0.9}},
        )
        result = assemble_result_node(state)["result"]
        self.assertEqual(result["status"], "partial")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["not_verified"])

    def test_constructive_bounded_pass_rescues_unsupported_non_regular_proposal(self):
        """Symmetric case: reasoning proposes 'non_regular' with no
        destructive proof at all, but oracle_test passed -> the gate falls
        back to 'regular', not inconclusive."""
        state = _state(
            test_result={"status": "pass", "tested": 200},
            evidence={},
            reasoning_output={"evidence": {"verdict": "non_regular", "confidence": 0.9}},
        )
        result = assemble_result_node(state)["result"]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["evidence"]["reasoning"]["verdict"], "regular")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["bounded_pass"])
        self.assertTrue(
            any("strongest admissible basis" in d for d in result["verdict_gate"]["downgrades"])
        )


# ---------------------------------------------------------------------------
# docs/VERDICT_POLICY.md R7 — the retry planner reads trust + counterexamples
# ---------------------------------------------------------------------------

def _retry_state(**overrides):
    """Minimal state dict for run_retry_planner_node."""
    state = {
        "reasoning_output": None,
        "dispatch": {"pumping": True, "nerode": True, "re_builder": False,
                     "dfa_builder": False, "closure": False},
        "evidence": {},
        "test_result": None,
        "hypothesis": {"hypothesis": "non_regular"},
        "retry_round": 0,
        "mock_runner": None,
        "agent_runner": None,
        "verbose": False,
    }
    state.update(overrides)
    return state


class TestRetryPlannerReadsTrustAndCounterexamples(unittest.TestCase):

    @patch("agent_system.graph.run_agent")
    def test_planner_input_carries_trust_per_agent(self, mock_run_agent):
        mock_run_agent.return_value = {"evidence": {"agents_to_retry": ["pumping"]}}

        state = _retry_state(
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "bounded_pass", "checked_p": [2, 3, 4]},
                "nerode": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "nerode_verification": {"trust": "well_formed", "reason": "no oracle"},
            },
        )
        run_retry_planner_node(state)

        planner_input = mock_run_agent.call_args[0][2]
        self.assertEqual(
            planner_input["specialist_results"]["pumping"]["trust"], "bounded_pass",
        )
        self.assertEqual(
            planner_input["specialist_results"]["nerode"]["trust"], "well_formed",
        )

    @patch("agent_system.graph.run_agent")
    def test_refuted_pumping_proof_carries_partition_hint(self, mock_run_agent):
        """R7: a refuted pumping proof's oracle counterexample reaches the
        planner as a Russian hint 'слово ... при p=... накачивается
        разбиением ...', not just a bare status/verdict pair."""
        mock_run_agent.return_value = {"evidence": {"agents_to_retry": ["pumping"]}}

        state = _retry_state(
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {
                    "trust": "refuted",
                    "reason": "partition x='' y='a' z='b' pumps within L at p=2",
                    "counterexample": {"p": 2, "word": "ab", "x": "", "y": "a", "z": "b"},
                },
            },
        )
        run_retry_planner_node(state)

        planner_input = mock_run_agent.call_args[0][2]
        self.assertEqual(
            planner_input["specialist_results"]["pumping"]["trust"], "refuted",
        )
        hint = planner_input["counterexamples"]["pumping"]["hint"]
        self.assertIn("слово", hint)
        self.assertIn("p=2", hint)
        self.assertIn("накачивается разбиением", hint)

    @patch("agent_system.graph.run_agent")
    def test_refuted_nerode_proof_carries_hint(self, mock_run_agent):
        mock_run_agent.return_value = {"evidence": {"agents_to_retry": ["nerode"]}}

        state = _retry_state(
            evidence={
                "nerode": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "nerode_verification": {
                    "trust": "refuted",
                    "reason": "context 'b' does not distinguish 'aa' from 'aaa'",
                    "counterexample": {"i": 2, "j": 3, "w_i": "aa", "w_j": "aaa", "context": "b"},
                },
            },
        )
        run_retry_planner_node(state)

        planner_input = mock_run_agent.call_args[0][2]
        self.assertIn("nerode", planner_input["counterexamples"])
        self.assertIn("hint", planner_input["counterexamples"]["nerode"])

    @patch("agent_system.graph.run_agent")
    def test_oracle_test_failure_reaches_counterexamples(self, mock_run_agent):
        mock_run_agent.return_value = {"evidence": {"agents_to_retry": []}}

        state = _retry_state(
            evidence={},
            test_result={
                "status": "fail",
                "counterexample": {"word": "ab", "oracle_says": True, "automaton_says": False},
            },
        )
        run_retry_planner_node(state)

        planner_input = mock_run_agent.call_args[0][2]
        self.assertEqual(
            planner_input["counterexamples"]["oracle_test"]["word"], "ab",
        )
        # Back-compat: the old top-level field is still populated too.
        self.assertEqual(planner_input["oracle_counterexample"]["word"], "ab")

    @patch("agent_system.graph.run_agent")
    def test_no_refuted_evidence_leaves_counterexamples_empty(self, mock_run_agent):
        mock_run_agent.return_value = {"evidence": {"agents_to_retry": []}}

        state = _retry_state(
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "bounded_pass", "checked_p": [2, 3, 4]},
            },
        )
        run_retry_planner_node(state)

        planner_input = mock_run_agent.call_args[0][2]
        self.assertEqual(planner_input["counterexamples"], {})


# ---------------------------------------------------------------------------
# verify_nerode_claim — context depending on the shared pair variable
# ("i, j или обе" — both patterns using the same variable name)
# ---------------------------------------------------------------------------

class TestNerodeContextSharedVariable(unittest.TestCase):

    def test_context_sharing_the_pair_variable_name_is_instantiated_correctly(self):
        """Both pair patterns share the variable name 'i' (e.g. 'a^i' vs
        'b^i'); the context also uses 'i'. This is neither the pair[0]-only
        nor the pair[1]-only case -- it must still instantiate (at m/n
        respectively) instead of silently falling back to well_formed."""

        def oracle(word: str) -> bool:
            # L = {a^n b^n | n >= 0} again, phrased so pattern_i="a^i" and
            # pattern_j="b^i" both stay meaningful claims about L.
            n_a = 0
            while n_a < len(word) and word[n_a] == "a":
                n_a += 1
            rest = word[n_a:]
            return rest == "b" * n_a and n_a + len(rest) == len(word)

        proof = {
            "status": "success",
            "proof": {
                "distinguishing_contexts": [
                    {"pair": ["a^i", "a^j"], "condition": "i < j", "context": "b^i"},
                ],
            },
        }
        result = verify_nerode_claim(proof, oracle=oracle)
        self.assertEqual(result["trust"], "bounded_pass")
        self.assertEqual(result["checked_pairs"], [[2, 3], [2, 4], [3, 4]])

    def test_context_variable_matching_neither_pair_variable_is_well_formed(self):
        """R7 fix, item 2: an unrecognized context variable must never be
        treated as refuted -- it stays well_formed (structural-only)."""
        proof = {
            "status": "success",
            "proof": {
                "distinguishing_contexts": [
                    {"pair": ["a^i", "a^j"], "condition": "i < j", "context": "c^k"},
                ],
            },
        }
        result = verify_nerode_claim(proof, oracle=lambda w: True)
        self.assertEqual(result["trust"], "well_formed")


# ---------------------------------------------------------------------------
# Reviewer finding fix: an unresolved contradiction must not leak the
# reasoning agent's own proposed regular/non_regular verdict through as if
# it were the pipeline's actual verdict -- only its confidence used to be
# capped; the verdict field itself was left untouched in evidence.reasoning,
# and orchestrator._result_verdict / tfl_eval.runners.extract('reg') both
# read straight through to it.
# ---------------------------------------------------------------------------

class TestContradictionDoesNotLeakReasoningVerdict(unittest.TestCase):

    def test_unresolved_contradiction_nulls_reasoning_verdict(self):
        state = _state(
            test_result={"status": "pass", "tested": 200},
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "well_formed", "reason": "no oracle"},
                "reasoning": {"verdict": "regular", "confidence": 0.9},
            },
            reasoning_output={"verdict": "regular", "confidence": 0.9},
        )
        result = assemble_result_node(state)["result"]

        self.assertTrue(result["verdict_gate"]["contradiction"])
        self.assertEqual(result["status"], "partial")
        self.assertLessEqual(result["confidence"], 0.50)
        # The gate must null the verdict it just refused to confirm -- not
        # just cap confidence and leave "regular" sitting in evidence.reasoning.
        self.assertIsNone(result["evidence"]["reasoning"]["verdict"])


# ---------------------------------------------------------------------------
# docs/VERDICT_POLICY.md R3' for REG (reviewer finding): the destructive
# proof's own witness words (reused from claim_verifier's step-2 checks) run
# through the language oracle AND the DFA behind test_result before falling
# back to an unresolved R3 contradiction.
# ---------------------------------------------------------------------------

_ACCEPT_ALL_DFA = {
    "states": ["q0"], "alphabet": ["a", "b"],
    "transitions": {"q0": {"a": "q0", "b": "q0"}},
    "start": "q0", "accept": ["q0"],
}


class TestR3PrimeCrossCheckReg(unittest.TestCase):

    def test_cross_check_refutes_overgenerating_dfa_resolves_non_regular(self):
        """The DFA (accept-everything) over-generates: it wrongly accepts
        'aaabb', which the destructive proof's own witness correctly claims
        is NOT in L (oracle-confirmed) -> the DFA is refuted, not the proof."""
        witnesses = [
            {"word": "aabb", "expected_in_l": True, "source": "word_family p=2"},
            {"word": "aaabb", "expected_in_l": False, "source": "reg pumping p=2 x='a' i=2"},
        ]
        state = _state(
            oracle_fn=_anbn_oracle,
            dfa=_ACCEPT_ALL_DFA,
            test_result={"status": "pass", "tested": 50},
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "bounded_pass", "witnesses": witnesses},
            },
            reasoning_output={"verdict": "regular", "confidence": 0.9},
        )
        result = assemble_result_node(state)["result"]
        vg = result["verdict_gate"]

        # Resolved deterministically, not left standing (mirrors
        # cfl_system.orchestrator's R3' convention).
        self.assertFalse(vg["contradiction"])
        self.assertEqual(result["status"], "success")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["bounded_pass"])
        self.assertEqual(result["evidence"]["reasoning"]["verdict"], "non_regular")
        self.assertTrue(
            any("R3' cross-check refuted constructive artifact" in d for d in vg["downgrades"])
        )

    def test_cross_check_refutes_wrong_destructive_witness_resolves_regular(self):
        """The reverse: the destructive proof's own witness claim disagrees
        with the real oracle -> the proof itself is refuted, and the DFA
        (which never gets contradicted) wins instead."""
        witnesses = [
            # 'aaabb' is NOT in L = {a^n b^n} per the real oracle, but this
            # (synthetically wrong) proof claims it IS.
            {"word": "aaabb", "expected_in_l": True, "source": "synthetic bad witness"},
        ]
        state = _state(
            oracle_fn=_anbn_oracle,
            dfa=_ACCEPT_ALL_DFA,
            test_result={"status": "pass", "tested": 50},
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "bounded_pass", "witnesses": witnesses},
            },
            reasoning_output={"verdict": "non_regular", "confidence": 0.9},
        )
        result = assemble_result_node(state)["result"]
        vg = result["verdict_gate"]

        self.assertFalse(vg["contradiction"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["evidence"]["reasoning"]["verdict"], "regular")
        self.assertTrue(
            any("R3' cross-check refuted destructive claim" in d for d in vg["downgrades"])
        )

    def test_no_witnesses_falls_back_to_unresolved_contradiction(self):
        """Without witnesses (e.g. an older well_formed-only check that never
        ran step-2), R3' cannot run at all -- falls back to the plain R3
        rule: unresolved, verdict nulled, confidence <= 0.50."""
        state = _state(
            oracle_fn=_anbn_oracle,
            dfa=_ACCEPT_ALL_DFA,
            test_result={"status": "pass", "tested": 50},
            evidence={
                "pumping": {"status": "success", "evidence": {"verdict": "non_regular"}},
                "pumping_verification": {"trust": "bounded_pass"},  # no "witnesses" key
            },
            reasoning_output={"verdict": "regular", "confidence": 0.9},
        )
        result = assemble_result_node(state)["result"]
        vg = result["verdict_gate"]

        self.assertTrue(vg["contradiction"])
        self.assertEqual(result["status"], "partial")
        self.assertLessEqual(result["confidence"], 0.50)
        self.assertIsNone(result["evidence"]["reasoning"]["verdict"])


# ---------------------------------------------------------------------------
# Cost ceiling (TODO.md backlog round C2): config.MAX_CALLS_PER_AGENT — the
# orchestrator must never call the same specialist more than this many times
# for one task. Precedent: live cfl-12 eval run, cfg_builder alone was
# called 6 times across retries (130 706 output tokens, $0.80).
# ---------------------------------------------------------------------------

class _CountingRunner:
    """A mock runner that records every agent name it was actually asked to
    run — used to assert the call cap is enforced at the call site itself,
    not just in whatever the runner happens to return."""

    def __init__(self):
        self.calls: list[str] = []

    def run_agent(self, agent_name, input_data=None):
        self.calls.append(agent_name)
        return {
            "agent": agent_name, "status": "success", "evidence": {"verdict": "non_regular"},
        }


class TestCallCapAtSpecialistCallSite(unittest.TestCase):
    def _specialist_state(self, agent_name, specialist_outputs, runner):
        return {
            "_specialist_name": agent_name,
            "specialist_outputs": specialist_outputs,
            "mock_runner": runner,
            "agent_runner": None,
            "verbose": False,
            "ir": {},
            "hypothesis": {},
            "classifier_evidence": {},
            "retry_context": {},
        }

    def test_specialist_skipped_once_cap_reached(self):
        runner = _CountingRunner()
        history = [("pumping", {"status": "success"})] * MAX_CALLS_PER_AGENT
        state = self._specialist_state("pumping", history, runner)
        result = run_specialist_node(state)
        self.assertEqual(runner.calls, [])  # no LLM call made
        self.assertNotIn("specialist_outputs", result)
        self.assertTrue(any(
            "pumping" in note and "call cap reached" in note
            for note in result.get("call_cap_notes", [])
        ))

    def test_specialist_still_called_below_cap(self):
        runner = _CountingRunner()
        history = [("pumping", {"status": "success"})] * (MAX_CALLS_PER_AGENT - 1)
        state = self._specialist_state("pumping", history, runner)
        result = run_specialist_node(state)
        self.assertEqual(runner.calls, ["pumping"])
        self.assertNotIn("call_cap_notes", result)

    def test_mock_retry_scenario_never_exceeds_cap(self):
        """Simulate the retry planner asking for the SAME agent every round
        (the cfl-12 precedent) across more rounds than the cap allows — the
        runner must never see more than MAX_CALLS_PER_AGENT actual calls."""
        runner = _CountingRunner()
        specialist_outputs: list = []
        for _ in range(MAX_CALLS_PER_AGENT + 4):
            state = self._specialist_state("pumping", specialist_outputs, runner)
            result = run_specialist_node(state)
            specialist_outputs = specialist_outputs + list(result.get("specialist_outputs", []))
        self.assertEqual(runner.calls.count("pumping"), MAX_CALLS_PER_AGENT)

    def test_call_cap_note_surfaces_in_verdict_gate_downgrades(self):
        state = _state(
            test_result={
                "status": "fail",
                "counterexample": {"word": "ab", "oracle_says": True, "automaton_says": False},
            },
            call_cap_notes=[
                "agent pumping call cap reached (3 calls)",
                "agent pumping call cap reached (3 calls)",  # duplicate
            ],
        )
        result = assemble_result_node(state)["result"]
        downgrades = result["verdict_gate"]["downgrades"]
        matches = [d for d in downgrades if "pumping call cap reached" in d]
        self.assertEqual(len(matches), 1)


if __name__ == "__main__":
    unittest.main()
