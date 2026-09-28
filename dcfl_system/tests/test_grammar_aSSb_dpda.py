"""Tests for the task_grammar_aSSb (dcfl_exam_04) certificate DPDA (docs/THEORY.md
§1.10, docs/VERDICT_POLICY.md R2'): dcfl_system/tools/grammar_aSSb_dpda.py builds it,
dcfl_system/examples/certificates/grammar_aSSb_dpda.json is the built artefact, and
dcfl_system/lib/dpda.py is the shared contract checker/simulator it must satisfy.
"""
from __future__ import annotations

import functools
import itertools
import json
import random
from pathlib import Path

import pytest

from dcfl_system.lib.dpda import check_determinism, dpda_accepts
from dcfl_system.tools.grammar_aSSb_dpda import (
    QuotientDPDA,
    RawDPDA,
    build_fast_index,
    build_npda,
    check_quotient_reachable_pairs,
    determinize,
    dpda_fast_accepts,
    in_language,
    npda_run,
    quotient,
)

CERT_PATH = (
    Path(__file__).resolve().parent.parent / "examples" / "certificates" / "grammar_aSSb_dpda.json"
)


@pytest.fixture(scope="module")
def cert() -> dict:
    return json.loads(CERT_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# (a) The certificate loads and is syntactically deterministic.
# ---------------------------------------------------------------------------


class TestCertificateLoadsAndIsDeterministic:
    def test_certificate_file_exists_and_parses(self, cert):
        assert cert["states"]
        assert cert["stack_alphabet"]
        assert cert["transitions"]

    def test_expected_size(self, cert):
        # docs/THEORY.md §1.10: 345 states / 315 stack symbols / 5835 transitions
        # after the bisimulation quotient.
        assert len(cert["states"]) == 345
        assert len(cert["stack_alphabet"]) == 315
        assert len(cert["transitions"]) == 5835

    def test_check_determinism_is_clean(self, cert):
        assert check_determinism(cert) == []


# ---------------------------------------------------------------------------
# (b) dpda_accepts (the shared cfl_system.lib.pda_simulator-backed simulator)
#     agrees with in_language: exhaustively up to length 8 (~16 ms/word budget:
#     <= ~600 words), plus 40 random words of length 9-14 with a fixed seed.
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestDpdaAcceptsMatchesGrammarOracle:
    def test_exhaustive_up_to_length_8(self, cert):
        mismatches = []
        for n in range(1, 9):
            for tup in itertools.product("ab", repeat=n):
                w = "".join(tup)
                if dpda_accepts(cert, w) != in_language(w):
                    mismatches.append(w)
        assert mismatches == []

    def test_random_words_length_9_to_14(self, cert):
        rng = random.Random(20260927)
        words = ["".join(rng.choice("ab") for _ in range(rng.randint(9, 14))) for _ in range(40)]
        mismatches = [w for w in words if dpda_accepts(cert, w) != in_language(w)]
        assert mismatches == []


# ---------------------------------------------------------------------------
# (c) The tool's own fast simulator agrees with the grammar oracle up to
#     length 12 (exhaustively -- this one is cheap, no repo-simulator budget).
# ---------------------------------------------------------------------------


class TestFastSimulatorMatchesGrammarOracle:
    def test_exhaustive_up_to_length_12(self, cert):
        index = build_fast_index(cert)
        mismatches = []
        for n in range(1, 13):
            for tup in itertools.product("ab", repeat=n):
                w = "".join(tup)
                if dpda_fast_accepts(cert, w, index) != in_language(w):
                    mismatches.append(w)
        assert mismatches == []


# ---------------------------------------------------------------------------
# (d) The profile NPDA is height-deterministic (THEORY.md §1.10, step 2): on
#     every prefix of length <= 10, the set of stack heights across all live
#     nondeterministic configurations has size <= 1.
# ---------------------------------------------------------------------------


class TestNpdaIsHeightDeterministic:
    def test_all_prefixes_up_to_length_10(self):
        npda = build_npda()
        violations = []
        for n in range(11):
            for tup in itertools.product("ab", repeat=n):
                w = "".join(tup)
                _, heights = npda_run(npda, w)
                if heights is not None and len(heights) > 1:
                    violations.append((w, heights))
        assert violations == []


# ---------------------------------------------------------------------------
# (e) Quotient structural soundness (review finding, round C3): the
#     bisimulation quotient must never define a transition on a (class-of-
#     top, class-of-state) key unless SOME reachable raw pair backing that
#     key actually has one -- check_quotient_reachable_pairs is the
#     independent, non-word-based check for this.
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def _real_raw_and_quotient() -> tuple[RawDPDA, QuotientDPDA]:
    """determinize(build_npda()) + quotient(...) are deterministic/pure and
    otherwise get recomputed by every test below that needs the real
    pipeline -- cache the (non-trivial) build once per process."""
    raw = determinize(build_npda())
    return raw, quotient(raw)


class TestQuotientReachablePairsStructuralCheck:
    def test_real_pipeline_has_no_violations(self):
        raw, quot = _real_raw_and_quotient()
        assert check_quotient_reachable_pairs(raw, quot) == []

    def test_reachable_pairs_are_exposed_and_nonempty(self):
        raw, _quot = _real_raw_and_quotient()
        assert raw.reachable_pairs
        assert all(isinstance(p, tuple) and len(p) == 2 for p in raw.reachable_pairs)

    def test_detects_a_quotient_transition_absent_from_any_reachable_raw_pair(self):
        """A hand-built RawDPDA/QuotientDPDA pair where two states (s1, s2)
        share a class, a transition exists only via s1, and the pair (top,
        s2) is UNREACHABLE -- the check must flag the class-key transition
        as unsupported by any reachable raw pair on that (top, s2) side."""
        raw = RawDPDA(
            names={},
            transitions=[("s1", "a", "top", "pop", "s1")],
            accepting=set(),
            start="s1",
            # (top, s1) is reachable; (top, s2) never occurs together.
            reachable_pairs=frozenset({("top", "s1")}),
        )
        # s1 and s2 collapse to the same class (0); "top" is its own class (1).
        quot = QuotientDPDA(
            q0=0,
            acc=frozenset(),
            trans={(0, "a", 1): ("pop", 0)},
            classes={"s1": 0, "s2": 0, "top": 1},
        )
        violations = check_quotient_reachable_pairs(raw, quot)
        assert violations == []  # (top, s1) IS reachable and DOES have a raw move -- no violation from it

        # Now add s2 to reachable_pairs on the SAME (top, class) combination
        # but with NO raw transition for it -- this is the case the check
        # must catch: the quotient's class-key transition would apply to a
        # reachable pair raw itself leaves undefined.
        raw_with_gap = RawDPDA(
            names={}, transitions=raw.transitions, accepting=set(), start="s1",
            reachable_pairs=frozenset({("top", "s1"), ("top", "s2")}),
        )
        violations = check_quotient_reachable_pairs(raw_with_gap, quot)
        assert violations
        assert any("s2" in v and "top" in v for v in violations)
