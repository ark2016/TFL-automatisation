"""Eval-set oracle-buildability coverage (root CLAUDE.md item 4,
docs/VERDICT_POLICY.md §4).

For every CFL/DCFL task in ``tfl_eval/manifest.json``, a membership oracle
must build (not ``None``/not raise ``UnsupportedOracleKindError``) and must
produce at least one positive word up to length 8 -- otherwise the task's
own oracle-based semantic checks (grammar CYK, exponent-notation parsing,
set_builder segment matching, DPDA simulation against it, ...) can never
fire and trust stays stuck at the structural-only ``well_formed`` tier
(docs/VERDICT_POLICY.md §1-2).

Known gaps are listed explicitly in ``_KNOWN_EXCEPTIONS`` with a one-line
reason each -- anything NOT listed there must pass, so a silent regression
(or a genuinely new gap that needs a real fix, not a skip) fails this test
instead of being missed.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir, UnsupportedOracleKindError
from dcfl_system.lib.word_sampler import build_membership_oracle_from_ir

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MANIFEST_PATH = _REPO_ROOT / "tfl_eval" / "manifest.json"

_MAX_WORD_LEN = 8

# task_id -> reason the oracle can't (yet) be built/decisive for this task.
# These are genuine notation gaps (not exponent/block notation, or notation
# this module intentionally doesn't attempt), not bugs -- see
# cfl_system/lib/exponent_pattern.py and dcfl_system/lib/word_sampler.py
# module docstrings for what IS supported.
_KNOWN_EXCEPTIONS: dict[str, str] = {
    "cfl-03": "{ww | w in {a,b}*} -- a repeated-WORD pattern (w then w "
              "again), not exponent/block notation over letters.",
    "cfl-10": "{a^(2^n) | n>=0} -- nested exponent (2^n as an exponent's "
              "own exponent); exponent_pattern's arithmetic grammar has "
              "no '^' operator.",
    "cfl-16": "{w in {a,b}* | #a(w) = 2*#b(w)} -- a symbol-count predicate "
              "over an unconstrained string, not expressible as "
              "letter/word exponent blocks.",
    "dcfl-02": "{w c reverse(w) | w in {a,b}*} -- a reverse-pattern "
               "set_builder (needs a `variables` IR with a 'w^R' word "
               "pattern, like task_wvaavRwR.json, not exponent notation).",
    "dcfl-03": "{w reverse(w) | w in {a,b}*} -- same reverse-pattern gap "
               "as dcfl-02.",
    "dcfl-10": "task_anb_cnbn.json's word_pattern mixes Kleene star and "
               "alternation with exponents ('a^n b* (c^n|b^n) a c*') -- "
               "outside exponent_pattern's block grammar.",
    "dcfl-11": "'{$ a^n b^n c^m} union {d a^m b^n c^n}, n,m >= 1' -- the "
               "domain declaration trails OUTSIDE both union braces, so "
               "neither branch parses as a self-contained group.",
    "dcfl-19": "{w in {a,b}* | w != x x for every decomposition w = x x} "
               "-- a universally-quantified 'for every decomposition' "
               "condition, not a single block/exponent pattern.",
}


def _load_manifest() -> list[dict]:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def _load_ir(rel_path: str) -> dict:
    return json.loads((_REPO_ROOT / rel_path).read_text(encoding="utf-8"))


def _has_positive_word(oracle, alphabet: list[str], max_len: int = _MAX_WORD_LEN) -> bool:
    if not alphabet:
        return False
    for length in range(0, max_len + 1):
        for combo in itertools.product(alphabet, repeat=length):
            word = "".join(combo)
            try:
                if oracle(word) is True:
                    return True
            except Exception:
                continue
    return False


def _cfl_entries() -> list[dict]:
    return [e for e in _load_manifest() if e.get("system") == "cfl"]


def _dcfl_entries() -> list[dict]:
    return [e for e in _load_manifest() if e.get("system") == "dcfl"]


@pytest.mark.parametrize("entry", _cfl_entries(), ids=lambda e: e["id"])
def test_cfl_oracle_builds_and_finds_positive_word(entry):
    task_id = entry["id"]
    ir = _load_ir(entry["path"])
    spec = ir.get("language_spec", {}) or {}
    alphabet = ir.get("alphabet") or spec.get("alphabet") or ["a", "b"]

    if task_id in _KNOWN_EXCEPTIONS:
        pytest.skip(_KNOWN_EXCEPTIONS[task_id])

    try:
        oracle = cfl_oracle_from_ir(ir)
    except UnsupportedOracleKindError as exc:
        pytest.fail(
            f"{task_id}: oracle did not build ({exc}) and is not in "
            "_KNOWN_EXCEPTIONS -- either fix the oracle/IR, or add an "
            "explicit, reasoned exception."
        )
        return

    assert oracle is not None, f"{task_id}: cfl_oracle_from_ir returned None"
    assert _has_positive_word(oracle, alphabet), (
        f"{task_id}: oracle built but found no positive word up to "
        f"length {_MAX_WORD_LEN}"
    )


@pytest.mark.parametrize("entry", _dcfl_entries(), ids=lambda e: e["id"])
def test_dcfl_oracle_builds_and_finds_positive_word(entry):
    task_id = entry["id"]
    ir = _load_ir(entry["path"])
    alphabet = ir.get("alphabet") or ["a", "b"]

    if task_id in _KNOWN_EXCEPTIONS:
        pytest.skip(_KNOWN_EXCEPTIONS[task_id])

    oracle = build_membership_oracle_from_ir(ir)
    assert oracle is not None, (
        f"{task_id}: build_membership_oracle_from_ir returned None and is "
        "not in _KNOWN_EXCEPTIONS -- either fix the oracle/IR, or add an "
        "explicit, reasoned exception."
    )
    assert _has_positive_word(oracle, alphabet), (
        f"{task_id}: oracle built but found no positive word up to "
        f"length {_MAX_WORD_LEN}"
    )


def test_known_exceptions_are_still_exceptional():
    """Guards against a stale exception list: every id in
    _KNOWN_EXCEPTIONS really does still fail today -- if one starts
    passing (e.g. exponent_pattern grows support for it), it must be
    removed from the list, not left as dead documentation."""
    manifest = _load_manifest()
    by_id = {e["id"]: e for e in manifest}
    for task_id in _KNOWN_EXCEPTIONS:
        assert task_id in by_id, f"{task_id} no longer in manifest.json"
        entry = by_id[task_id]
        ir = _load_ir(entry["path"])
        alphabet = ir.get("alphabet") or ir.get("language_spec", {}).get("alphabet") or ["a", "b"]
        if entry["system"] == "cfl":
            try:
                oracle = cfl_oracle_from_ir(ir)
            except UnsupportedOracleKindError:
                continue  # still fails to build, as documented
        else:
            oracle = build_membership_oracle_from_ir(ir)
            if oracle is None:
                continue  # still fails to build, as documented
        assert not _has_positive_word(oracle, alphabet), (
            f"{task_id} is listed in _KNOWN_EXCEPTIONS but now builds an "
            "oracle with a positive word -- remove it from the list."
        )


# Sanity check: the specific traps this round's live recheck flagged
# (2026-09-27 scratchpad/eval_recheck) must be resolved, not merely
# "not regressed".
@pytest.mark.parametrize(
    "task_id", ["dcfl-04", "dcfl-15", "dcfl-17", "cfl-12"],
)
def test_specific_2026_09_27_traps_resolved(task_id):
    entry = next(e for e in _load_manifest() if e["id"] == task_id)
    ir = _load_ir(entry["path"])
    alphabet = ir.get("alphabet") or ir.get("language_spec", {}).get("alphabet") or ["a", "b"]
    if entry["system"] == "cfl":
        oracle = cfl_oracle_from_ir(ir)
    else:
        oracle = build_membership_oracle_from_ir(ir)
    assert oracle is not None
    assert _has_positive_word(oracle, alphabet)
