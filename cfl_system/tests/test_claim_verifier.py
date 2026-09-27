"""Tests for the claim verifier module."""

from __future__ import annotations

import pytest

from cfl_system.lib.claim_verifier import (
    verify_agent_claims,
    verify_closure_claim,
    verify_decomposition_claim,
    verify_parikh_claim,
    verify_pumping_claim,
    _enumerate_ogden_splits,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# A simple grammar for a^n b^n (n >= 0): S -> a S b | eps
_ANBN_GRAMMAR = {
    "nonterminals": ["S"],
    "terminals": ["a", "b"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S", "b"]},
        {"lhs": "S", "rhs": []},
    ],
}

_IR_ANBN = {
    "language_spec": {
        "kind": "grammar",
        **_ANBN_GRAMMAR,
    },
}

# Dummy IR that won't build an oracle (for tests that don't need one)
_IR_DUMMY = {"language_spec": {"kind": "predicate", "alphabet": ["a"], "variable": "w",
                                "predicate": {"op": "eq",
                                              "left": {"kind": "length", "of_var": "w"},
                                              "right": {"kind": "constant", "value": 0}}}}


# ---------------------------------------------------------------------------
# verify_pumping_claim
# ---------------------------------------------------------------------------

class TestVerifyPumpingClaim:
    def test_valid_proof_well_formed(self):
        # VERDICT_POLICY.md §1: a structural-only pass is "well_formed", not
        # "verified" — "verified" is reserved for deterministic full proofs
        # (LL(k) tables, Lean certs), which claim_verifier never produces.
        evidence = {
            "word_chosen": "aabb",
            "cases": [
                {"case": "v in a*, x in a*", "why_not_in_L": "more a's than b's"},
                {"case": "v in a*, x in b*", "why_not_in_L": "count mismatch"},
            ],
            "all_cases_covered": True,
        }
        result = verify_pumping_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "well_formed"
        assert result["trust"] == "well_formed"
        assert result["checks_passed"] == result["checks_total"]
        assert result["issues"] == []

    def test_word_not_in_language_refuted(self):
        evidence = {
            "word_chosen": "aab",  # not in a^n b^n
            "cases": [{"case": "any", "why_not_in_L": "reason"}],
            "all_cases_covered": True,
        }
        result = verify_pumping_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "refuted"
        assert any("NOT in L" in i for i in result["issues"])

    def test_missing_cases_not_verified(self):
        evidence = {
            "word_chosen": "aabb",
            "cases": [],
            "all_cases_covered": False,
        }
        result = verify_pumping_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "not_verified"
        assert any("No cases" in i for i in result["issues"])

    def test_parametric_word_skips_oracle(self):
        """Parametric words like 'a^n b^n' should not be oracle-checked."""
        evidence = {
            "word_chosen": "a^p b^p",
            "cases": [{"case": "general", "why_not_in_L": "mismatch"}],
            "all_cases_covered": True,
        }
        result = verify_pumping_claim(evidence, _IR_ANBN)
        # Should still pass structural checks
        assert result["verification_status"] == "well_formed"

    def test_case_missing_fields(self):
        evidence = {
            "word_chosen": "aabb",
            "cases": [{"description": "incomplete case"}],
            "all_cases_covered": True,
        }
        result = verify_pumping_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "not_verified"
        assert any("missing" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# verify_closure_claim
# ---------------------------------------------------------------------------

class TestVerifyClosureClaim:
    def test_valid_claim_well_formed(self):
        evidence = {
            "regular_language_regex": "a*b*",
            "intersection_description": "L ∩ R = {a^n b^n}",
            "intersection_not_cfl_proof": {
                "word_chosen": "aabb",
                "cases": [{"case": "v in a*", "why_not_in_L": "mismatch"}],
                "all_cases_covered": True,
            },
        }
        result = verify_closure_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "well_formed"
        assert result["trust"] == "well_formed"
        assert result["issues"] == []

    def test_invalid_regex_reported(self):
        evidence = {
            "regular_language_regex": "[invalid((",
            "intersection_description": "some description",
            "intersection_not_cfl_proof": {
                "word_chosen": "aabb",
                "cases": [{"case": "c", "why_not_in_L": "r"}],
                "all_cases_covered": True,
            },
        }
        result = verify_closure_claim(evidence, _IR_ANBN)
        assert any("invalid" in i for i in result["issues"])

    def test_missing_intersection_proof(self):
        evidence = {
            "regular_language_regex": "a*b*",
            "intersection_description": "some desc",
        }
        result = verify_closure_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "not_verified"
        assert any("No proof" in i for i in result["issues"])

    def test_semantic_examples_upgrade_to_bounded_pass(self):
        """docs/VERDICT_POLICY.md §4: concrete intersection examples checked
        against R (regex) and L (oracle) upgrade well_formed -> bounded_pass."""
        evidence = {
            "regular_language_regex": "a*b*",
            "intersection_description": "L ∩ R = {a^n b^n}",
            "intersection_examples": ["", "ab", "aabb"],
            "intersection_non_examples": ["a", "b"],
            "intersection_not_cfl_proof": {
                "word_chosen": "aabb",
                "cases": [{"case": "v in a*", "why_not_in_L": "mismatch"}],
                "all_cases_covered": True,
            },
        }
        result = verify_closure_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "well_formed"
        assert result["trust"] == "bounded_pass"

    def test_nested_pumping_proof_checked_against_L_cap_R_not_L_alone(self):
        """docs/VERDICT_POLICY.md fix: the nested intersection_not_cfl_proof
        is about L ∩ R, not L. R here excludes n=4 (a{2,3}b{2,3}), so
        pumping 'aaabbb' (n=3) via the cross-boundary split v='a', x='b' at
        i=2 produces 'aaaabbbb' (n=4): still in L (equal counts) but OUTSIDE
        R -- i.e. outside L ∩ R, correctly disqualifying the split. Checking
        against L alone (the old bug) would see i=2's word as "still in the
        language" and falsely refute this otherwise-fine proof."""
        evidence = {
            "regular_language_regex": "a{2,3}b{2,3}",
            "intersection_description": "L ∩ R = {a^n b^n | 2 <= n <= 3}",
            "intersection_not_cfl_proof": {
                "word_chosen": "aaabbb",
                "cases": [{"case": "cross-boundary v=a,x=b", "why_not_in_L": "leaves L ∩ R"}],
                "all_cases_covered": True,
                "word_instances": {"3": "aaabbb"},
            },
        }
        result = verify_closure_claim(evidence, _IR_ANBN)
        assert result["verification_status"] != "refuted", (
            "nested proof was checked against L alone instead of L ∩ R "
            f"(issues: {result['issues']})"
        )
        assert result["trust"] != "refuted"

    def test_semantic_examples_refute_wrong_intersection(self):
        """Precedent: live run 2026-09-27 claimed a wrong L ∩ R description
        (docs/THEORY.md §2). A non-example actually in L must be caught."""
        evidence = {
            "regular_language_regex": "a*b*",
            "intersection_description": "L ∩ R = {a^n b^n}",
            "intersection_examples": ["", "ab", "aabb"],
            # "aabbb" matches a*b* but is NOT in a^n b^n (correctly not in L) —
            # use a genuinely-in-L word as a bogus "non-example" to trigger refute.
            "intersection_non_examples": ["aabb", "ab"],
            "intersection_not_cfl_proof": {
                "word_chosen": "aabb",
                "cases": [{"case": "v in a*", "why_not_in_L": "mismatch"}],
                "all_cases_covered": True,
            },
        }
        result = verify_closure_claim(evidence, _IR_ANBN)
        assert result["verification_status"] == "refuted"
        assert result["trust"] == "refuted"


# ---------------------------------------------------------------------------
# verify_decomposition_claim
# ---------------------------------------------------------------------------

class TestVerifyDecompositionClaim:
    def test_valid_concat_well_formed(self):
        evidence = {
            "components": [
                {"name": "L1", "is_cfl": True},
                {"name": "L2", "is_cfl": True},
            ],
            "operation": "concatenation",
        }
        result = verify_decomposition_claim(evidence, _IR_DUMMY)
        assert result["verification_status"] == "well_formed"
        assert result["issues"] == []

    def test_non_cfl_closed_operation(self):
        evidence = {
            "components": [
                {"name": "L1", "is_cfl": True},
                {"name": "L2", "is_cfl": True},
            ],
            "operation": "intersection",
        }
        result = verify_decomposition_claim(evidence, _IR_DUMMY)
        assert result["verification_status"] == "not_verified"
        assert any("may not preserve CFL" in i for i in result["issues"])

    def test_no_components(self):
        evidence = {"operation": "union"}
        result = verify_decomposition_claim(evidence, _IR_DUMMY)
        assert any("No components" in i for i in result["issues"])

    def test_component_not_claimed_cfl(self):
        evidence = {
            "components": [{"name": "L1", "is_cfl": False}],
            "operation": "union",
        }
        result = verify_decomposition_claim(evidence, _IR_DUMMY)
        assert any("not claimed CFL" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# verify_parikh_claim
# ---------------------------------------------------------------------------

class TestVerifyParikhClaim:
    def test_not_semilinear_correct_conclusion(self):
        evidence = {
            "conclusion": "Language is not CFL (non-semilinear Parikh image)",
            "is_semilinear": False,
        }
        result = verify_parikh_claim(evidence, _IR_DUMMY)
        assert result["verification_status"] == "well_formed"
        assert result["issues"] == []

    def test_semilinear_undetermined(self):
        evidence = {"conclusion": "unknown"}
        result = verify_parikh_claim(evidence, _IR_DUMMY)
        assert result["verification_status"] == "not_verified"
        assert any("Semilinearity" in i for i in result["issues"])

    def test_not_semilinear_wrong_conclusion(self):
        evidence = {
            "conclusion": "Language is CFL",
            "is_semilinear": False,
        }
        result = verify_parikh_claim(evidence, _IR_DUMMY)
        assert any("doesn't say not CFL" in i for i in result["issues"])


# ---------------------------------------------------------------------------
# verify_agent_claims (dispatcher)
# ---------------------------------------------------------------------------

class TestVerifyAgentClaims:
    def test_dispatch_pumping(self):
        agent_output = {
            "agent": "pumping_cfl",
            "status": "success",
            "evidence": {
                "word_chosen": "aabb",
                "cases": [{"case": "c", "why_not_in_L": "r"}],
                "all_cases_covered": True,
            },
        }
        result = verify_agent_claims(agent_output, _IR_ANBN)
        assert result["agent"] == "pumping_cfl"
        assert result["verification_status"] == "well_formed"

    def test_failed_agent_not_verified(self):
        agent_output = {
            "agent": "pumping_cfl",
            "status": "error",
            "evidence": {},
        }
        result = verify_agent_claims(agent_output, _IR_ANBN)
        assert result["verification_status"] == "not_verified"
        assert result["trust"] == "not_verified"
        assert any("error" in i for i in result["issues"])

    def test_unknown_agent_not_verified(self):
        agent_output = {
            "agent": "cfg_builder",
            "status": "success",
            "evidence": {},
        }
        result = verify_agent_claims(agent_output, _IR_DUMMY)
        assert result["verification_status"] == "not_verified"
        assert any("No specific verifier" in i for i in result["issues"])

    def test_ogden_dispatches_to_pumping(self):
        agent_output = {
            "agent": "ogden",
            "status": "success",
            "evidence": {
                "word_chosen": "aabb",
                "cases": [{"case": "c", "why_not_in_L": "r"}],
                "all_cases_covered": True,
            },
        }
        result = verify_agent_claims(agent_output, _IR_ANBN)
        # ogden reuses pumping's structural checks, but must report its own
        # agent name (docs/VERDICT_POLICY.md fix) -- a claim_verification
        # entry mislabeled "pumping_cfl" for an ogden claim was a real bug,
        # not a feature: the two use different semantic checks (marked
        # positions vs. plain length-bounded splits), so callers reading
        # `result["agent"]` must see which one actually ran.
        assert result["agent"] == "ogden"
        assert result["verification_status"] == "well_formed"

    def test_pumping_word_instances_bounded_pass(self):
        """docs/VERDICT_POLICY.md §4: word_instances at p=3 checked exhaustively
        against the oracle upgrades well_formed -> bounded_pass.

        Uses a genuinely non-CFL language (a^n b^n c^n via a counting
        predicate oracle, not a grammar — a^n b^n itself is CFL, so no split
        of any of its words can ever be fully disqualified, which is the
        correct behavior, not a bug: see test_pumping_word_instances_refutes_bad_witness).
        """
        ir_anbncn = {
            "language_spec": {
                "kind": "predicate",
                "alphabet": ["a", "b", "c"],
                "variable": "w",
                "predicate": {
                    "op": "and",
                    "operands": [
                        {
                            "op": "eq",
                            "left": {"kind": "count_symbol", "in_var": "w", "symbol": "a"},
                            "right": {"kind": "count_symbol", "in_var": "w", "symbol": "b"},
                        },
                        {
                            "op": "eq",
                            "left": {"kind": "count_symbol", "in_var": "w", "symbol": "b"},
                            "right": {"kind": "count_symbol", "in_var": "w", "symbol": "c"},
                        },
                    ],
                },
            },
        }
        agent_output = {
            "agent": "pumping_cfl",
            "status": "success",
            "evidence": {
                "word_chosen": "a^p b^p c^p",
                "cases": [{"case": "c", "why_not_in_L": "r"}],
                "all_cases_covered": True,
                "word_instances": {"3": "aaabbbccc"},
            },
        }
        result = verify_agent_claims(agent_output, ir_anbncn)
        assert result["verification_status"] == "well_formed"
        assert result["trust"] == "bounded_pass"

    def test_ogden_semantic_check_skipped_without_marked_positions(self):
        """docs/VERDICT_POLICY.md fix: ogden's word_instances alone (no
        marked_positions) must NOT fall back to the plain length-bounded
        pumping enumeration -- that enumeration is unsound for Ogden's
        lemma (it bounds |vwx| by p, but Ogden's p bounds the number of
        MARKED positions in vwx). Trust stays well_formed."""
        agent_output = {
            "agent": "ogden",
            "status": "success",
            "evidence": {
                "word_chosen": "aaabbb",
                "cases": [{"case": "c", "why_not_in_L": "r"}],
                "all_cases_covered": True,
                "word_instances": {"3": "aaabbb"},
            },
        }
        result = verify_agent_claims(agent_output, _IR_ANBN)
        assert result["agent"] == "ogden"
        assert result["verification_status"] == "well_formed"
        assert result["trust"] == "well_formed"

    def test_enumerate_ogden_splits_bounds_marked_positions_not_length(self):
        """docs/VERDICT_POLICY.md fix, unit-level: Ogden's p bounds the
        number of MARKED positions inside vwx, not |vwx| itself -- with a
        single marked position, a split spanning the WHOLE word (length 6,
        far beyond p=3) must still be produced, as long as vx touches the
        marked position and vwx contains only that one marked position."""
        word = "aaabbb"
        splits = list(_enumerate_ogden_splits(word, marked={0}, p=3))
        assert splits, "no splits produced at all"
        assert any(len(v) + len(w) + len(x) > 3 for (_, v, w, x, _) in splits), (
            "a split with |vwx| > p was wrongly excluded -- the plain, "
            "length-bounded pumping enumerator would exclude it, but "
            "Ogden's lemma only bounds marked positions, not length"
        )
        # Every yielded split must actually touch the marked position (0)
        # somewhere in v or x, and must not touch it in neither.
        for (u, v, w, x, y) in splits:
            i = len(u)
            j = i + len(v)
            k = j + len(w)
            end = k + len(x)
            in_v = i <= 0 < j
            in_x = k <= 0 < end
            assert in_v or in_x, f"split ({u!r},{v!r},{w!r},{x!r},{y!r}) has no marked position in vx"

    def test_enumerate_ogden_splits_respects_marked_count_bound(self):
        """A window containing MORE marked positions than p must never be
        enumerated, however short it is."""
        word = "aaaaaa"
        # Every position marked -- with p=2, no window of length > 2 can
        # ever qualify (it would contain > 2 marked positions).
        splits = list(_enumerate_ogden_splits(word, marked=set(range(len(word))), p=2))
        for (u, v, w, x, _y) in splits:
            assert len(v) + len(w) + len(x) <= 2

    def test_ogden_semantic_check_bounded_pass_with_marked_positions(self):
        """docs/VERDICT_POLICY.md §4: a genuine Ogden proof against a truly
        non-CFL language (a^n b^n c^n) with concrete marked_positions
        upgrades well_formed -> bounded_pass, same as the plain pumping
        check does for its own word_instances (test_pumping_word_instances_bounded_pass)."""
        ir_anbncn = {
            "language_spec": {
                "kind": "predicate",
                "alphabet": ["a", "b", "c"],
                "variable": "w",
                "predicate": {
                    "op": "and",
                    "operands": [
                        {
                            "op": "eq",
                            "left": {"kind": "count_symbol", "in_var": "w", "symbol": "a"},
                            "right": {"kind": "count_symbol", "in_var": "w", "symbol": "b"},
                        },
                        {
                            "op": "eq",
                            "left": {"kind": "count_symbol", "in_var": "w", "symbol": "b"},
                            "right": {"kind": "count_symbol", "in_var": "w", "symbol": "c"},
                        },
                    ],
                },
            },
        }
        agent_output = {
            "agent": "ogden",
            "status": "success",
            "evidence": {
                "word_chosen": "a^p b^p c^p",
                "cases": [{"case": "c", "why_not_in_L": "r"}],
                "all_cases_covered": True,
                "word_instances": {"3": "aaabbbccc"},
                # Mark every position -- forces vwx to stay within a
                # length-p window (>p marked positions would appear in any
                # longer window, since every character is marked), matching
                # the classic a^n b^n c^n Ogden/pumping argument.
                "marked_positions": {"3": list(range(len("aaabbbccc")))},
            },
        }
        result = verify_agent_claims(agent_output, ir_anbncn)
        assert result["agent"] == "ogden"
        assert result["verification_status"] == "well_formed"
        assert result["trust"] == "bounded_pass"

    def test_pumping_word_instances_refutes_bad_witness(self):
        """A word for which some split can never be disqualified (i in {0,2})
        is not a valid pumping witness — must be refuted, not well_formed."""
        # L = a*b* (union, both counts unconstrained): a^n b^n pumped at i=0/2
        # both stay in a*b*, so NO split of "aaabbb" at p=3 is disqualified.
        ir_astar_bstar = {
            "language_spec": {
                "kind": "grammar",
                "nonterminals": ["S", "A", "B"],
                "terminals": ["a", "b"],
                "start": "S",
                "rules": [
                    {"lhs": "S", "rhs": ["A", "B"]},
                    {"lhs": "A", "rhs": ["a", "A"]},
                    {"lhs": "A", "rhs": []},
                    {"lhs": "B", "rhs": ["b", "B"]},
                    {"lhs": "B", "rhs": []},
                ],
            },
        }
        agent_output = {
            "agent": "pumping_cfl",
            "status": "success",
            "evidence": {
                "word_chosen": "a^p b^p",
                "cases": [{"case": "c", "why_not_in_L": "r"}],
                "all_cases_covered": True,
                "word_instances": {"3": "aaabbb"},
            },
        }
        result = verify_agent_claims(agent_output, ir_astar_bstar)
        assert result["verification_status"] == "refuted"
        assert result["trust"] == "refuted"
