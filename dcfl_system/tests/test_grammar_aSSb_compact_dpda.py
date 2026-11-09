"""Tests for the compact task_grammar_aSSb (dcfl_exam_04) certificate DPDA
(docs/THEORY.md §1.10, "Компактный ДМПА"; docs/VERDICT_POLICY.md R2').

dcfl_system/tools/grammar_aSSb_compact_dpda.py builds it from the Dyck-forest
description of L, dcfl_system/examples/certificates/grammar_aSSb_dpda_compact.json is
the built artefact. Everything is checked against the grammar oracle
`grammar_aSSb_dpda.in_language`, never against the construction's own claims.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from dcfl_system.lib.dpda import _normalize_read, check_determinism, dpda_run
from dcfl_system.lib.oracle_verifier import _verify_stack_strategy_dpda
from dcfl_system.tools.grammar_aSSb_compact_dpda import (
    TYPE_FLAGS,
    build_certificate,
    children_dfa,
    dump_certificate,
    dyck_forest,
    forest_member,
    main,
    reachable_type_flags,
    root_dfa,
)
from dcfl_system.tools.grammar_aSSb_dpda import build_fast_index, dpda_fast_accepts, in_language

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CERT_PATH = EXAMPLES / "certificates" / "grammar_aSSb_dpda_compact.json"
TASK_PATH = EXAMPLES / "task_grammar_aSSb.json"


@pytest.fixture(scope="module")
def cert() -> dict:
    return json.loads(CERT_PATH.read_text(encoding="utf-8"))


def _words(max_len: int):
    for n in range(max_len + 1):
        for tup in itertools.product("ab", repeat=n):
            yield "".join(tup)


# ---------------------------------------------------------------------------
# (a) The structural description of L (the hand part of the construction).
# ---------------------------------------------------------------------------


class TestForestCharacterization:
    def test_exactly_five_tree_types_occur(self):
        assert reachable_type_flags() == set(TYPE_FLAGS.values())

    def test_dyck_forest_basics(self):
        assert dyck_forest("") == []
        assert dyck_forest("ab") == [[]]
        assert dyck_forest("abab") == [[], []]
        assert dyck_forest("aabb") == [[[]]]
        assert dyck_forest("ba") is None
        assert dyck_forest("aab") is None

    def test_matches_grammar_oracle_up_to_length_14(self):
        mismatches = [w for w in _words(14) if forest_member(w) != in_language(w)]
        assert mismatches == []

    @pytest.mark.parametrize("word", ["ab", "ba", "aabb", "aababb", "aabbab", "abaabb", "ababab"])
    def test_known_members(self, word):
        assert forest_member(word) and in_language(word)

    @pytest.mark.parametrize("word", ["", "abab", "bb", "aaababbb", "abba"])
    def test_known_non_members(self, word):
        # aaababbb: the subset construction keyed on state only accepts it
        # (docs/THEORY.md §1.10, step 3) -- a good adversarial word here too.
        assert not forest_member(word) and not in_language(word)


class TestTypeDFAs:
    def test_sizes(self):
        assert len(children_dfa().states) == 14
        assert len(root_dfa().states) == 5

    def test_root_accepts_one_tree_of_word_type(self):
        root = root_dfa()
        for t in ("LEAF", "PATH", "SI", "S"):
            assert root.states[root.delta[(root.start, t)]] is True
        assert root.states[root.delta[(root.start, "I")]] is None


# ---------------------------------------------------------------------------
# (b) The certificate file is the tool's output and is a clean DPDA.
# ---------------------------------------------------------------------------


class TestCertificateFile:
    def test_file_is_current_build(self, cert):
        assert cert == build_certificate()
        assert CERT_PATH.read_text(encoding="utf-8") == dump_certificate(build_certificate())

    def test_size(self, cert):
        assert len(cert["states"]) == 20
        assert len(cert["stack_alphabet"]) == 18
        assert len(cert["transitions"]) == 356

    def test_deterministic_without_epsilon(self, cert):
        assert check_determinism(cert) == []
        assert all(_normalize_read(t["read"]) is not None for t in cert["transitions"])

    def test_states_and_symbols_are_declared(self, cert):
        states = set(cert["states"])
        stack = set(cert["stack_alphabet"])
        assert cert["start"] in states
        assert set(cert["accept_states"]) <= states
        for t in cert["transitions"]:
            assert t["from"] in states and t["to"] in states
            assert t["top"] in stack and set(t["push"]) <= stack


# ---------------------------------------------------------------------------
# (c) The certificate agrees with the grammar oracle.
# ---------------------------------------------------------------------------


class TestCertificateMatchesGrammarOracle:
    def test_shared_simulator_up_to_length_10(self, cert):
        mismatches = [w for w in _words(10) if dpda_run(cert, w) != in_language(w)]
        assert mismatches == []

    def test_fast_simulator_up_to_length_14(self, cert):
        index = build_fast_index(cert)
        mismatches = [
            w for w in _words(14) if dpda_fast_accepts(cert, w, index) != in_language(w)
        ]
        assert mismatches == []

    @pytest.mark.slow
    def test_fast_simulator_up_to_length_18(self, cert):
        index = build_fast_index(cert)
        mismatches = [
            w for w in _words(18) if dpda_fast_accepts(cert, w, index) != in_language(w)
        ]
        assert mismatches == []

    def test_oracle_verifier_gives_bounded_pass(self, cert):
        """The same R2' check the pipeline runs on a stack_strategy `dpda`, against
        the task's own grammar oracle (CYK)."""
        task_ir = json.loads(TASK_PATH.read_text(encoding="utf-8"))
        status, _check, issue, details = _verify_stack_strategy_dpda(cert, task_ir)
        assert status == "bounded_pass", issue
        assert details["determinism"] == "verified"
        assert "normalization" not in details  # nothing to rewrite: no epsilon moves


class TestCli:
    def test_main_writes_and_checks(self, tmp_path, capsys):
        out = tmp_path / "compact.json"
        assert main(["--out", str(out), "--check", "8"]) == 0
        assert json.loads(out.read_text(encoding="utf-8")) == build_certificate()
        assert "mismatches=0" in capsys.readouterr().out
