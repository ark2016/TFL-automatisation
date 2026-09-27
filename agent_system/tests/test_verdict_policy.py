"""Tests for docs/VERDICT_POLICY.md — trust taxonomy and verdict gate
(TODO §1, R6 for agent_system).

Covers the §6 policy scenarios that apply to the regularity pipeline:
  1. constructive artifact refuted, no destructive proof -> inconclusive,
     confidence <= 0.40, downgrades non-empty.
  2. constructive bounded_pass + destructive well_formed both present ->
     contradiction: true, confidence <= 0.50.
  3. Lean verified (status=="valid", sorry_count==0) -> confidence may be 0.98.
  4. destructive proof with well_formed trust only (no oracle) -> verdict
     stands, confidence <= 0.60.
  5. test_result.fail on the correct non_regular verdict, but a destructive
     proof with oracle-verified words (bounded_pass) exists -> success
     capped at 0.85, not "failure 0.0" (R6).

Plus focused unit tests for the claim_verifier step-2 semantic checks
(pumping word-family instantiation + partition search, Myhill-Nerode
pair/context instantiation) and the destructive-trust combinator.
"""

import unittest

from agent_system.graph import assemble_result_node
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
# §6 scenario 3 — Lean verified (status=="valid" and sorry_count==0)
# ---------------------------------------------------------------------------

class TestScenario3LeanVerified(unittest.TestCase):

    def test_lean_no_sorry_reaches_098(self):
        state = _state(
            evidence={
                "formalization": {"status": "valid", "sorry_count": 0, "lean_verified": True},
            },
            reasoning_output={"evidence": {"verdict": "non_regular", "confidence": 0.99}},
        )
        result = assemble_result_node(state)["result"]

        self.assertEqual(result["status"], "success")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["verified"])
        self.assertAlmostEqual(result["confidence"], CONFIDENCE_CAPS["verified"])

    def test_lean_with_sorry_is_not_verified(self):
        """sorry_count > 0 must NOT reach the `verified` cap (TODO §1 ⚪)."""
        state = _state(
            evidence={
                "formalization": {"status": "valid", "sorry_count": 1, "lean_verified": True},
            },
        )
        result = assemble_result_node(state)["result"]

        self.assertNotEqual(result["confidence"], CONFIDENCE_CAPS["verified"])


# ---------------------------------------------------------------------------
# §6 scenario 4 — destructive proof with well_formed trust only (no oracle)
# -> verdict stands, confidence <= 0.60
# ---------------------------------------------------------------------------

class TestScenario4WellFormedOnly(unittest.TestCase):

    def test_well_formed_destructive_caps_at_060(self):
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

    def test_valid_anbn_proof_is_bounded_pass(self):
        pumping_output = {
            "status": "success",
            "evidence": {"verdict": "non_regular", "word_family": "a^p b^p"},
        }
        result = verify_pumping_claim(pumping_output, oracle=_anbn_oracle)
        self.assertEqual(result["trust"], "bounded_pass")
        self.assertEqual(result["checked_p"], [2, 3, 4])

    def test_bogus_proof_against_trivial_language_is_refuted(self):
        """Every word is in L (regular), so no partition of a^p b^p ever
        escapes -- the claimed pumping proof must be refuted."""
        pumping_output = {
            "status": "success",
            "evidence": {"verdict": "non_regular", "word_family": "a^p b^p"},
        }
        result = verify_pumping_claim(pumping_output, oracle=_all_words_oracle)
        self.assertEqual(result["trust"], "refuted")
        self.assertIn("counterexample", result)

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

    def test_closure_verified_status_is_only_bounded_pass(self):
        """The closure agent's empirical Nerode-index estimate is bounded
        (timeout + depth cutoff) -- it must never reach the full `verified`
        trust reserved for deterministic checks."""
        trust = closure_trust_from_verification(
            {"status": "success"}, {"status": "verified", "confidence": 0.95},
        )
        self.assertEqual(trust, "bounded_pass")

    def test_closure_without_verification_is_well_formed(self):
        trust = closure_trust_from_verification({"status": "success"}, None)
        self.assertEqual(trust, "well_formed")

    def test_closure_disproved_is_refuted(self):
        trust = closure_trust_from_verification(
            {"status": "success"}, {"status": "disproved"},
        )
        self.assertEqual(trust, "refuted")


# ---------------------------------------------------------------------------
# docs/VERDICT_POLICY.md R1/R2 fix (reviewer finding, verdict-gate branch):
# assemble_result_node used to grant success<=0.60 to ANY reasoning verdict
# with no deterministic evidence at all, and separately ignored which
# DIRECTION the reasoning verdict claimed — a destructive proof supporting
# non_regular was silently treated as backing whatever verdict reasoning
# proposed, even "regular".
# ---------------------------------------------------------------------------

class TestReasoningVerdictDirectionIsChecked(unittest.TestCase):

    def test_non_regular_with_refuted_destructive_and_no_dfa_is_partial(self):
        """Probe 1: DFA never built, pumping_verification refuted, reasoning
        claims non_regular 0.92 -> must NOT be success 0.6 (nor any success)."""
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
        verdict -> must NOT be an unconditional success 0.6."""
        state = _state(
            reasoning_output={"evidence": {"verdict": "non_regular", "confidence": 0.9}},
        )
        result = assemble_result_node(state)["result"]
        self.assertNotEqual(result["status"], "success")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["not_verified"])

    def test_regular_verdict_not_backed_by_refuted_dfa_is_partial(self):
        """Probe 3 (R2 violation): DFA refuted, reasoning says 'regular',
        pumping well_formed (argues non_regular) -> the 'regular' claim must
        downgrade, not ride along on the (wrongly-directed) destructive
        evidence as success 0.6."""
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
        self.assertNotEqual(result["status"], "success")
        self.assertLessEqual(result["confidence"], CONFIDENCE_CAPS["not_verified"])


if __name__ == "__main__":
    unittest.main()
