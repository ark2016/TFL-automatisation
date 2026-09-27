"""Tests for the task_grammar_aSSb (dcfl_exam_04) certificate DPDA (docs/THEORY.md
§1.10, docs/VERDICT_POLICY.md R2'): dcfl_system/tools/grammar_aSSb_dpda.py builds it,
dcfl_system/examples/certificates/grammar_aSSb_dpda.json is the built artefact, and
dcfl_system/lib/dpda.py is the shared contract checker/simulator it must satisfy.
"""
from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

import pytest

from dcfl_system.lib.dpda import check_determinism, dpda_accepts
from dcfl_system.tools.grammar_aSSb_dpda import (
    build_fast_index,
    build_npda,
    dpda_fast_accepts,
    in_language,
    npda_run,
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
