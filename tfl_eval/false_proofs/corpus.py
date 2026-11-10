"""The false-proof corpus: plausible but wrong agent outputs, one per case.

Every case is a proof (or a constructive artifact) that an agent could
plausibly emit and that is mathematically WRONG: either the verdict it
argues for is false, or the verdict is right but the argument behind it is
not a proof (``verdict_correct=True``; recorded separately, since such a
case still says how much a false argument can earn).

Each case carries:

- ``ir`` -- the task IR in the pipeline's own format (repo eval/example IRs
  where one exists, otherwise a small inline IR with an exact oracle);
- ``agents`` -- the agent output(s) exactly as the pipeline stores them in
  its state (``evidence`` for REG, ``agent_results`` for CFL/DCFL/LL);
- ``reasoning`` -- the reasoning agent's (overconfident) proposal, which the
  verdict gate then checks;
- ``true_verdict`` / ``claimed_verdict`` and ``why_false`` -- the truth and
  the concrete reason the proof is wrong;
- ``counterexample`` -- a mechanically checkable witness of the flaw (a
  surviving decomposition, a word the artifact gets wrong, two words the
  proof calls distinguishable that are Nerode-equivalent, ...), checked by
  ``tfl_eval.false_proofs.replay.check_counterexample``.

No case calls an LLM: the outputs are fixed data replayed through the
pipelines' own verification code (``tfl_eval.false_proofs.replay``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

PIPELINES = ("reg", "cfl", "dcfl", "ll")


@dataclass(frozen=True)
class FalseProofCase:
    id: str
    pipeline: str
    title: str
    ir: dict
    agents: dict
    reasoning: dict
    claimed_verdict: str
    true_verdict: str
    flaw: str
    why_false: str
    counterexample: dict
    precedent: str = ""

    @property
    def verdict_correct(self) -> bool:
        return self.claimed_verdict == self.true_verdict


def _load(rel_path: str) -> dict:
    with open(REPO_ROOT / rel_path, encoding="utf-8") as fh:
        return json.load(fh)


def _rules(text: str) -> list[dict]:
    """``"S -> aSb | ; T -> a"`` -> rule dicts; one-letter symbols, uppercase
    = nonterminal, an empty alternative = epsilon."""
    rules: list[dict] = []
    for chunk in text.split(";"):
        lhs, _, rhs = chunk.partition("->")
        for alt in rhs.split("|"):
            rules.append({"lhs": lhs.strip(), "rhs": list(alt.strip())})
    return rules


def _grammar(text: str, start: str = "S") -> dict:
    rules = _rules(text)
    nts = sorted({r["lhs"] for r in rules})
    terms = sorted({s for r in rules for s in r["rhs"] if not s.isupper()})
    return {"terminals": terms, "nonterminals": nts, "start": start, "rules": rules}


def _named_grammar(rules: list[tuple[str, list[str]]], start: str) -> dict:
    """Grammar with multi-character nonterminal names (terminals: a, b, c)."""
    nts = sorted({lhs for lhs, _ in rules})
    terms = sorted({s for _, rhs in rules for s in rhs if s not in nts})
    return {
        "terminals": terms, "nonterminals": nts, "start": start,
        "rules": [{"lhs": lhs, "rhs": list(rhs)} for lhs, rhs in rules],
    }


def _len_eq(x: str, y: str) -> dict:
    return {"op": "eq", "left": {"kind": "length", "of_var": x},
            "right": {"kind": "length", "of_var": y}}


# ---------------------------------------------------------------------------
# IRs
# ---------------------------------------------------------------------------

# REG: (#a - #b) = 0 (mod 3) -- regular (3-state DFA), right-linear grammar.
_REG_MOD3_IR = {
    "task_type": "classify_and_prove",
    "source_text": "{w in {a,b}* | #a(w) - #b(w) = 0 (mod 3)}",
    "language_spec": {"kind": "grammar", **_named_grammar([
        ("S0", []), ("S0", ["a", "S1"]), ("S0", ["b", "S2"]),
        ("S1", ["a", "S2"]), ("S1", ["b", "S0"]),
        ("S2", ["a", "S0"]), ("S2", ["b", "S1"]),
    ], start="S0")},
}

# REG: words of even length (= #a = #b (mod 2), reg-03) -- regular.
_REG_EVEN_IR = {
    "task_type": "classify_and_prove",
    "source_text": "{w in {a,b}* | #a(w) = #b(w) (mod 2)}",
    "language_spec": {"kind": "grammar", **_grammar("S -> aT | bT | ; T -> aS | bS")},
}

# CFL: {a^i b^j c^k | i = j or j = k} (cfl-02) with an EXACT oracle
# (exists_decomposition; the eval IR's exponent-notation oracle is
# approximate, so the claim verifier would skip every semantic check).
_CFL_OR_IR = {
    "task_type": "classify_and_prove_cfl",
    "source_text": "{a^i b^j c^k | i = j or j = k}",
    "language_spec": {
        "kind": "exists_decomposition",
        "parts": ["x", "y", "z"],
        "concat_pattern": ["x", "y", "z"],
        "alphabets": {"x": ["a"], "y": ["b"], "z": ["c"]},
        "constraints": [{"op": "or", "operands": [_len_eq("x", "y"), _len_eq("y", "z")]}],
    },
}

# CFL: {a^n b^n} with an exact oracle.
_CFL_ANBN_IR = {
    "task_type": "classify_and_prove_cfl",
    "source_text": "{a^n b^n | n >= 0}",
    "language_spec": {
        "kind": "exists_decomposition",
        "parts": ["x", "y"],
        "concat_pattern": ["x", "y"],
        "alphabets": {"x": ["a"], "y": ["b"]},
        "constraints": [_len_eq("x", "y")],
    },
}

# DCFL: {a^(2n)} -- regular, hence a DCFL; every word is continuable, so its
# dead class is empty (the premise Shallit's nerode_classes technique checks).
# (A two-letter language with D = ∅, e.g. #a = #b, only has a CYK oracle here,
# and the dead-class search over it takes minutes.)
_DCFL_EVEN_A_IR = {
    "task_id": "false_even_a",
    "source_text": "{a^(2n) | n >= 0}",
    "input_format": "set_builder",
    "task_type": "classify_and_prove_dcfl",
    "language_spec": {"kind": "set_builder", "word_pattern": "{a^(2n) | n >= 0}",
                      "variables": [], "constraints": []},
    "alphabet": ["a"],
}

# LL Format 3 copy of ll-18 asking about k = 1 (S -> aS | a is LL(2), not LL(1)).
_LL18_K1_IR = {**_load("ll_system/examples/eval/ll-18.json"), "k": 1,
               "source_text": "Format 3: S -> aS | a. Is this grammar LL(1)?"}


# ---------------------------------------------------------------------------
# Artifacts built in code
# ---------------------------------------------------------------------------

def _dpda_anbn_plus_small_double() -> dict:
    """A genuinely deterministic PDA for {a^n b^n | n >= 0} u {a^n b^2n | 1 <= n <= 3}.

    The finite control remembers min(n, 4); the stack matches the first b^n;
    after that, for n <= 3 the control counts the second b^n without the
    stack.  It agrees with L = {a^n b^n} u {a^n b^2n} (dcfl-05, not a DCFL)
    on every word of length <= 11 -- the first word it gets wrong is
    a^4 b^8 (length 12) -- so the bounded simulation (length <= 10) passes.
    """
    states = ["q0"] + [f"s{k}" for k in range(1, 5)] + [f"p{k}" for k in range(1, 5)] \
        + [f"m{k}" for k in range(1, 5)] + [f"r{k}_{j}" for k in range(1, 4) for j in range(1, k + 1)]
    t: list[dict] = [{"from": "q0", "read": "a", "top": "Z0", "to": "s1", "push": ["A1", "Z0"]}]
    for k in range(1, 5):
        nxt = f"s{min(k + 1, 4)}"
        t += [
            {"from": f"s{k}", "read": "a", "top": "A1", "to": nxt, "push": ["A", "A1"]},
            {"from": f"s{k}", "read": "a", "top": "A", "to": nxt, "push": ["A", "A"]},
            {"from": f"s{k}", "read": "b", "top": "A", "to": f"p{k}", "push": []},
            {"from": f"s{k}", "read": "b", "top": "A1", "to": f"m{k}", "push": []},
            {"from": f"p{k}", "read": "b", "top": "A", "to": f"p{k}", "push": []},
            {"from": f"p{k}", "read": "b", "top": "A1", "to": f"m{k}", "push": []},
        ]
    for k in range(1, 4):
        t.append({"from": f"m{k}", "read": "b", "top": "Z0", "to": f"r{k}_1", "push": ["Z0"]})
        for j in range(1, k):
            t.append({"from": f"r{k}_{j}", "read": "b", "top": "Z0", "to": f"r{k}_{j + 1}", "push": ["Z0"]})
    accept = ["q0", "m1", "m2", "m3", "m4"] + [f"r{k}_{k}" for k in range(1, 4)]
    return {
        "states": states, "start": "q0", "accept_states": accept,
        "accept_mode": "final_state", "stack_alphabet": ["Z0", "A1", "A"],
        "initial_stack": ["Z0"], "transitions": t,
    }


def _dpda_dcfl04_with_stray_b() -> dict:
    """The dcfl-04 mock DPDA ({a^n b^n c^m}) plus the live run's stray
    ``q0 --b--> q_c`` transition: deterministic, but accepts "b"."""
    dpda = json.loads(json.dumps(
        _load("dcfl_system/examples/mock/dcfl-04_stack_strategy.json")["proof_sketch"]["dpda"]
    ))
    dpda["transitions"].append({"from": "q0", "read": "b", "top": "Z0", "to": "qc", "push": ["Z0"]})
    return dpda


def _dpda_exam04_tree_only() -> dict:
    """A deterministic PDA for {a^n b^n | n >= 1} u {ba} -- what an agent gets
    from reading only S -> ba and S -> Ab, A -> aAb | a of exam_04 and
    forgetting S -> aSSb (so it rejects a·ba·ba·b = "ababab" in L)."""
    return {
        "states": ["q0", "qa", "qb", "qacc", "qB", "qBA"],
        "start": "q0", "accept_states": ["qacc", "qBA"], "accept_mode": "final_state",
        "stack_alphabet": ["Z0", "A1", "A"], "initial_stack": ["Z0"],
        "transitions": [
            {"from": "q0", "read": "a", "top": "Z0", "to": "qa", "push": ["A1", "Z0"]},
            {"from": "q0", "read": "b", "top": "Z0", "to": "qB", "push": ["Z0"]},
            {"from": "qB", "read": "a", "top": "Z0", "to": "qBA", "push": ["Z0"]},
            {"from": "qa", "read": "a", "top": "A1", "to": "qa", "push": ["A", "A1"]},
            {"from": "qa", "read": "a", "top": "A", "to": "qa", "push": ["A", "A"]},
            {"from": "qa", "read": "b", "top": "A", "to": "qb", "push": []},
            {"from": "qa", "read": "b", "top": "A1", "to": "qacc", "push": []},
            {"from": "qb", "read": "b", "top": "A", "to": "qb", "push": []},
            {"from": "qb", "read": "b", "top": "A1", "to": "qacc", "push": []},
        ],
    }


def _finite_ll1_grammar_ai_bj_i_ge_j(max_len: int = 8) -> dict:
    """Right-linear (hence LL(1)) grammar for the FINITE language
    {a^i b^j | i >= j, i + j <= max_len}: equal to {a^i b^j | i >= j}
    (ll-07, not LL(k) for any k) on every word of length <= max_len."""
    rules: list[tuple[str, list[str]]] = []
    for i in range(max_len + 1):
        a_i = f"A{i}"
        rules.append((a_i, []))
        if i + 1 <= max_len:
            rules.append((a_i, ["a", f"A{i + 1}"]))
        if i >= 1 and i + 1 <= max_len:
            rules.append((a_i, ["b", f"B{i}_1"]))
        for j in range(1, i + 1):
            if i + j > max_len:
                break
            b_ij = f"B{i}_{j}"
            rules.append((b_ij, []))
            if j + 1 <= i and i + j + 1 <= max_len:
                rules.append((b_ij, ["b", f"B{i}_{j + 1}"]))
    return _named_grammar(rules, start="A0")


_DCFL04_MOCK_DPDA = "dcfl_system/examples/mock/dcfl-04_stack_strategy.json"

_ARG = "Подробное рассуждение (подставлено корпусом ложных доказательств)."


def _subst_sketch(word_1: str, word_2: str) -> dict:
    return {
        "method": "substitution",
        "for_all_k": True,
        "branch_words": {
            "common_prefix": "a^n",
            "word_1": word_1,
            "word_2": word_2,
            "lookahead_equal_because": "оба слова начинаются с длинного блока a, FIRST_k совпадают",
        },
        "common_form_argument": "обе левосторонние выводимости проходят через общую форму a^j S δ",
        "deciding_nonterminal_argument": "нетерминал S должен выбрать правило, не видя конца блока a",
        "pigeonhole_argument": "по принципу Дирихле две разные длины блока дают одну и ту же форму",
        "proof_explanation": _ARG,
    }


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------

CASES: tuple[FalseProofCase, ...] = (
    # ----------------------------------------------------------------- REG
    FalseProofCase(
        id="reg-nerode-mod3",
        pipeline="reg",
        title="Nerode family a^i with context b^i for a mod-3 counter language",
        ir=_REG_MOD3_IR,
        agents={"nerode": {
            "module": "nerode", "status": "success",
            "proof": {
                "word_family": {"family": "a^i", "parameter": "i", "domain": "i >= 0",
                                "examples": ["a", "aa", "aaa"]},
                "distinguishing_contexts": [{
                    "pair": ["a^i", "a^j"], "context": "b^i",
                    "which_in": "a^i b^i", "in_language": "a^i b^i",
                    "not_in_language": "a^j b^i", "condition": "i != j",
                }],
                "argument": "a^i b^i в L, а a^j b^i при i != j не в L, значит все a^i попарно различимы",
                "conclusion": "бесконечно много классов Нероуда -- L нерегулярен",
            },
        }},
        reasoning={"evidence": {"action": "done", "verdict": "non_regular", "confidence": 0.95}},
        claimed_verdict="non_regular",
        true_verdict="regular",
        flaw="finite_instance_check",
        why_false=(
            "a^j b^i is in L whenever j = i (mod 3), so a^i and a^(i+3) are NOT distinguished; "
            "L has exactly 3 Nerode classes. The step-2 check instantiates only the pairs "
            "(2,3), (2,4), (3,4), whose differences 1, 2, 1 are never multiples of 3."
        ),
        counterexample={"kind": "nerode_equivalent", "u": "aa", "v": "aaaaa", "max_suffix_len": 7},
    ),
    FalseProofCase(
        id="reg-pumping-fixed-split",
        pipeline="reg",
        title="Pumping proof that argues only y = a^k for a regular even-length language",
        ir=_REG_EVEN_IR,
        agents={"pumping": {
            "module": "pumping", "status": "success",
            "evidence": {
                "verdict": "non_regular", "word_family": "a^p b^p",
                "argument": "Возьмём y = a (|xy| <= p). Тогда xy^2z = a^(p+1) b^p имеет нечётную "
                            "длину и не лежит в L -- противоречие.",
            },
        }},
        reasoning={"evidence": {"action": "done", "verdict": "non_regular", "confidence": 0.9}},
        claimed_verdict="non_regular",
        true_verdict="regular",
        flaw="picks_decomposition",
        why_false=(
            "The proof fixes y = a instead of quantifying over every split with |xy| <= p; "
            "the split x = eps, y = aa pumps for every i (length stays even)."
        ),
        counterexample={"kind": "pumpable_split", "parts": ["", "aa", "aabb"]},
    ),
    FalseProofCase(
        id="reg-regex-finite-anbn",
        pipeline="reg",
        title="Regex ab|aabb|aaabbb for a^n b^n (n >= 1), passes exhaustive check up to length 7",
        ir=_load("agent_system/examples/eval/reg-13.json"),
        agents={"re_builder": {
            "module": "re_builder", "status": "success",
            "evidence": {"regex": "ab|aabb|aaabbb",
                         "explanation": "S -> aSb | ab порождает ровно эти слова"},
        }},
        reasoning={"evidence": {"action": "done", "verdict": "regular", "confidence": 0.95}},
        claimed_verdict="regular",
        true_verdict="non_regular",
        flaw="bounded_equivalence",
        why_false=(
            "L = {a^n b^n | n >= 1} is infinite and non-regular; the regex is a finite "
            "subset that coincides with L on every word of length <= 7 (the oracle test's "
            "exhaustive bound), and first differs on a^4 b^4."
        ),
        counterexample={"kind": "artifact_disagrees", "artifact": "regex",
                        "regex": "ab|aabb|aaabbb", "word": "aaaabbbb", "in_language": True},
        precedent="reg-13",
    ),
    FalseProofCase(
        id="reg-nerode-mod2",
        pipeline="reg",
        title="Nerode family a^i with context b^i for the mod-2 language (reg-03 trap)",
        ir=_REG_EVEN_IR,
        agents={"nerode": {
            "module": "nerode", "status": "success",
            "proof": {
                "word_family": {"family": "a^i", "parameter": "i", "domain": "i >= 0",
                                "examples": ["a", "aa"]},
                "distinguishing_contexts": [{
                    "pair": ["a^i", "a^j"], "context": "b^i", "condition": "i != j",
                }],
                "argument": "a^i b^i в L, a^j b^i нет", "conclusion": "L нерегулярен",
            },
        }},
        reasoning={"evidence": {"action": "done", "verdict": "non_regular", "confidence": 0.9}},
        claimed_verdict="non_regular",
        true_verdict="regular",
        flaw="false_distinguishability",
        why_false="a^2 b^2 and a^4 b^2 both have even length: a^2 and a^4 are Nerode-equivalent.",
        counterexample={"kind": "nerode_equivalent", "u": "aa", "v": "aaaa", "max_suffix_len": 7},
        precedent="reg-03",
    ),

    # ----------------------------------------------------------------- CFL
    FalseProofCase(
        id="cfl-closure-wrong-intersection",
        pipeline="cfl",
        title="L ∩ a*b*c* claimed to be {a^n b^n c^n} for {a^i b^j c^k | i = j or j = k}",
        ir=_CFL_OR_IR,
        agents={"closure_reduction": {
            "status": "success", "verdict": "non_cfl",
            "evidence": {
                "regular_language_regex": "a*b*c*",
                "intersection_description": "L ∩ a*b*c* = {a^n b^n c^n | n >= 0}",
                "intersection_not_cfl_proof": {
                    "method": "pumping",
                    "word_chosen": "a^p b^p c^p",
                    "cases": [
                        {"case": "vwx внутри одного блока", "why_not_in_L": "блоки перестают быть равными"},
                        {"case": "vwx на стыке двух блоков", "why_not_in_L": "третий блок не меняется"},
                    ],
                    "all_cases_covered": True,
                },
                "intersection_examples": ["abc", "aabbcc", "aaabbbccc"],
                "intersection_non_examples": ["aab", "abbc"],
            },
        }},
        reasoning={"action": "done", "verdict": "non_cfl", "confidence": 0.95,
                   "primary_evidence": "closure_reduction"},
        claimed_verdict="non_cfl",
        true_verdict="cfl",
        flaw="wrong_intersection",
        why_false=(
            "L ⊆ a*b*c*, so L ∩ a*b*c* = L, which is a union of two CFLs; the claimed "
            "{a^n b^n c^n} misses e.g. abcc. The examples/non-examples the proof supplies "
            "happen to be consistent with both descriptions."
        ),
        counterexample={"kind": "in_language_not_in_claim", "word": "abcc",
                        "regex": "a*b*c*", "claimed": "{a^n b^n c^n | n >= 0}"},
        precedent="cfl-02",
    ),
    FalseProofCase(
        id="cfl-w1w2w1w3-wrong-intersection",
        pipeline="cfl",
        title="THEORY.md §2: L ∩ a+b+a+c+ claimed to be {a^n b^m a^n c^k}",
        ir=_load("cfl_system/examples/task_w1w2w1w3.json"),
        agents={"closure_reduction": {
            "status": "success", "verdict": "non_cfl",
            "evidence": {
                "regular_language_regex": "a+b+a+c+",
                "intersection_description": "L ∩ a+b+a+c+ = {a^n b^m a^n c^k | n, m, k >= 1}",
                "intersection_not_cfl_proof": {
                    "method": "pumping",
                    "word_chosen": "a^p b^p a^p c^p",
                    "cases": [{"case": "vwx касается первого блока a",
                               "why_not_in_L": "первый и третий блоки a расходятся"}],
                    "all_cases_covered": True,
                },
                "intersection_examples": ["abac", "aabaac", "abbacc"],
                "intersection_non_examples": ["aabac", "aaabaac"],
            },
        }},
        reasoning={"action": "done", "verdict": "non_cfl", "confidence": 0.95,
                   "primary_evidence": "closure_reduction"},
        claimed_verdict="non_cfl",
        true_verdict="non_cfl",
        flaw="wrong_intersection",
        why_false=(
            "The verdict is right but the proof is not: L ∩ a+b+a+c+ = {a^n b^m a^j c^k | j >= n}, "
            "a CFL (docs/THEORY.md §2), so its non-CFL-ness proves nothing. abaac (w1 = a, "
            "w2 = b, w3 = ac) is in L ∩ R but not of the claimed form."
        ),
        counterexample={"kind": "in_language_not_in_claim", "word": "abaac",
                        "regex": "a+b+a+c+", "claimed": "{a^n b^m a^n c^k | n, m, k >= 1}"},
        precedent="cfl-06 (live run 2026-09-27)",
    ),
    FalseProofCase(
        id="cfl-pumping-fixed-split",
        pipeline="cfl",
        title="Bar-Hillel pumping that only considers vwx inside one block, for a^n b^n",
        ir=_CFL_ANBN_IR,
        agents={"pumping_cfl": {
            "status": "success", "verdict": "non_cfl",
            "evidence": {
                "word_chosen": "a^p b^p",
                "cases": [
                    {"case": "vwx внутри блока a", "why_not_in_L": "число a меняется, число b нет"},
                    {"case": "vwx внутри блока b", "why_not_in_L": "число b меняется, число a нет"},
                ],
                "all_cases_covered": True,
                "word_instances": {"3": "aaabbb", "4": "aaaabbbb"},
            },
        }},
        reasoning={"action": "done", "verdict": "non_cfl", "confidence": 0.9,
                   "primary_evidence": "pumping_cfl"},
        claimed_verdict="non_cfl",
        true_verdict="cfl",
        flaw="picks_decomposition",
        why_false=(
            "The case split omits vwx straddling the a/b border: v = a, x = b pumps for every i."
        ),
        counterexample={"kind": "pumpable_split", "parts": ["aa", "a", "", "b", "bb"]},
    ),
    FalseProofCase(
        id="cfl-ogden-invalid-marking",
        pipeline="cfl",
        title="Ogden proof for {a^i b^j c^k | i = j or j = k} that marks a single position",
        ir=_CFL_OR_IR,
        agents={"ogden": {
            "status": "success", "verdict": "non_cfl",
            "evidence": {
                "word_chosen": "a^p b^p c^(p+p!)",
                "marked": "одна позиция в блоке b",
                "cases": [{"case": "vx содержит отмеченную b",
                           "why_not_in_L": "i = j и j = k одновременно нарушаются"}],
                "all_cases_covered": True,
                "word_instances": {"3": "aaabbbccccccccc", "4": "aaaabbbbcccccccccccccccccccccccccccc"},
                "marked_positions": {"3": [4], "4": [5]},
            },
        }},
        reasoning={"action": "done", "verdict": "non_cfl", "confidence": 0.9,
                   "primary_evidence": "ogden"},
        claimed_verdict="non_cfl",
        true_verdict="cfl",
        flaw="invalid_marking",
        why_false=(
            "Ogden's lemma needs at least p marked positions; with one mark the lemma says "
            "nothing about this word. L is the union of two CFLs and has the CFG "
            "S -> XC | AY, X -> aXb | eps, C -> cC | eps, A -> aA | eps, Y -> bYc | eps."
        ),
        counterexample={"kind": "cfg_matches_language", "max_len": 7,
                        "grammar": _grammar("S -> XC | AY ; X -> aXb | ; C -> cC | ; "
                                            "A -> aA | ; Y -> bYc | ")},
        precedent="cfl-12 (Ogden trap)",
    ),

    # ---------------------------------------------------------------- DCFL
    FalseProofCase(
        id="dcfl-shallit-nerode-even-a",
        pipeline="dcfl",
        title="Shallit nerode_classes for {a^(2n)} with literal suffix a and three short pairs",
        ir=_DCFL_EVEN_A_IR,
        agents={"shallit": {
            "agent_name": "shallit", "status": "success", "verdict": "non_dcfl",
            "proof_sketch": {
                "technique": "nerode_classes",
                "dead_class_status": "empty",
                "distinguishing_suffix": "a",
                "representative_pairs": [{"u": "a", "v": "aa"}, {"u": "aaa", "v": "aa"},
                                         {"u": "a", "v": "aaaa"}],
                "separation_argument": "ua ∈ L, va ∉ L для каждой пары",
                "argument": "все классы Нероуда конечны, D = ∅ ⇒ по теореме 4.7.4 L не DCFL",
            },
        }},
        reasoning={"action": "done", "verdict": "non_dcfl", "confidence": 0.95,
                   "primary_evidence": "shallit"},
        claimed_verdict="non_dcfl",
        true_verdict="dcfl",
        flaw="finite_instance_check",
        why_false=(
            "The theorem needs EVERY Nerode class to be finite; L has two classes "
            "(even / odd length), both infinite: eps ~ aa ~ aaaa ... Separating three "
            "chosen pairs says nothing about all classes. L is regular, hence a DCFL."
        ),
        counterexample={"kind": "nerode_equivalent", "u": "", "v": "aa", "max_suffix_len": 10},
    ),
    FalseProofCase(
        id="dcfl-shallit-prefix-continuation",
        pipeline="dcfl",
        title="prefix_continuation for a^n b^n c^m with a wrong derived language",
        ir=_load("dcfl_system/examples/eval/dcfl-04.json"),
        agents={"shallit": {
            "agent_name": "shallit", "status": "success", "verdict": "non_dcfl",
            "proof_sketch": {
                "technique": "prefix_continuation",
                "derived_language": "aⁿbⁿ$cⁿ",
                "regular_filter": "a*b*$c*",
                "non_cfl_argument": "L_$ ∩ a*b*$c* = {aⁿbⁿ$cⁿ} не КС (накачка)",
                "argument": "DCFL замкнут относительно L_$ и ∩ REG, значит L не DCFL",
            },
        }},
        reasoning={"action": "done", "verdict": "non_dcfl", "confidence": 0.95,
                   "primary_evidence": "shallit"},
        claimed_verdict="non_dcfl",
        true_verdict="dcfl",
        flaw="wrong_intersection",
        why_false=(
            "L_$ ∩ a*b*$c* = {a^n b^n $ c^m}, which is context-free: x = ab ∈ L, "
            "xy = abcc ∈ L, so ab$cc is in it but not of the claimed form. The step-2 "
            "check only instantiates the claim's own family a^n b^n $ c^n."
        ),
        counterexample={"kind": "prefix_pair_outside_claim", "x": "ab", "y": "cc",
                        "claimed": "{a^n b^n c^n | n >= 0}"},
        precedent="dcfl-04",
    ),
    FalseProofCase(
        id="dcfl-shallit-dead-class-infinite",
        pipeline="dcfl",
        title="exam_04: Shallit with dead class D infinite, still returned as success",
        ir=_load("dcfl_system/examples/task_grammar_aSSb.json"),
        agents={"shallit": {
            "agent_name": "shallit", "status": "success", "verdict": "non_dcfl",
            "proof_sketch": {
                "technique": "nerode_classes",
                "dead_class_status": "infinite",
                "distinguishing_suffix": "b^n",
                "separation_argument": "a^n b^n ∈ L, a^m b^n ∉ L",
                "argument": "D = {a^k ...} бесконечен, но остальные классы конечны",
            },
        }},
        reasoning={"action": "done", "verdict": "non_dcfl", "confidence": 0.6,
                   "primary_evidence": "shallit"},
        claimed_verdict="non_dcfl",
        true_verdict="dcfl",
        flaw="inapplicable_theorem",
        why_false=(
            "Theorem 4.7.4 [Sh] requires the dead class D to be finite (hence empty); "
            "here bb has no continuation, so D ⊇ bb·Σ* is infinite and the theorem says "
            "nothing. exam_04 is a DCFL (docs/THEORY.md §1.10)."
        ),
        counterexample={"kind": "dead_prefix", "word": "bb", "max_extra": 10},
        precedent="dcfl-21 (live run 2026-09-27)",
    ),
    FalseProofCase(
        id="dcfl-shallit-wrong-dead-class",
        pipeline="dcfl",
        title="Shallit nerode_classes for a^n b^n c^m claiming the dead class is empty",
        ir=_load("dcfl_system/examples/eval/dcfl-04.json"),
        agents={"shallit": {
            "agent_name": "shallit", "status": "success", "verdict": "non_dcfl",
            "proof_sketch": {
                "technique": "nerode_classes",
                "dead_class_status": "empty",
                "distinguishing_suffix": "b",
                "representative_pairs": [{"u": "a", "v": "aa"}, {"u": "a", "v": "aaa"}],
                "separation_argument": "ab ∈ L, a^k b ∉ L при k >= 2",
                "argument": "D = ∅, все классы конечны ⇒ L не DCFL",
            },
        }},
        reasoning={"action": "done", "verdict": "non_dcfl", "confidence": 0.9,
                   "primary_evidence": "shallit"},
        claimed_verdict="non_dcfl",
        true_verdict="dcfl",
        flaw="inapplicable_theorem",
        why_false=(
            "No word of L starts with b, so b is dead and D ⊇ b·Σ* is infinite: the "
            "technique does not apply. The dead-class search cannot prove deadness from a "
            "bounded search, so it leaves the premise unresolved instead of refuting it."
        ),
        counterexample={"kind": "dead_prefix", "word": "b", "max_extra": 10},
        precedent="dcfl-21",
    ),
    FalseProofCase(
        id="dcfl-pumping-small-p-closure",
        pipeline="dcfl",
        title="exam_04: Yu pumping whose word_instances close conditions (1)/(2) at p = 2, 3",
        ir=_load("dcfl_system/examples/task_grammar_aSSb.json"),
        agents={"dcfl_pumping": {
            "agent_name": "dcfl_pumping", "status": "success", "verdict": "non_dcfl",
            "proof_sketch": {
                "kind": "dcfl_pumping", "pumping_length": "p",
                "word_w": "aababb", "word_w_prime": "aabababbab",
                "common_prefix_x": "aaba", "suffix_y": "bb", "suffix_z": "babbab",
                "first_letters_match": "y и z начинаются с b",
                "condition1_argument": "никакая пара (x2, x4) не накачивается синхронно",
                "condition2_argument": "никакое x2 в последних p символах x не сохраняет оба слова",
                "word_instances": {
                    "2": {"w": "aababb", "w_prime": "aabababbab", "x_length": 4},
                    "3": {"w": "aaabbabb", "w_prime": "aaabbabbab", "x_length": 6},
                },
            },
        }},
        reasoning={"action": "done", "verdict": "non_dcfl", "confidence": 0.9,
                   "primary_evidence": "dcfl_pumping"},
        claimed_verdict="non_dcfl",
        true_verdict="dcfl",
        flaw="small_p_closure",
        why_false=(
            "Yu's lemma quantifies over the language's own (large) constant p; closing every "
            "decomposition at p = 2, 3 proves nothing. exam_04 is a DCFL (docs/THEORY.md "
            "§1.10), so no counterexample to the lemma exists at its real p."
        ),
        counterexample={"kind": "dpda_matches_language", "max_len": 8,
                        "dpda_file": "dcfl_system/examples/mock/dcfl_exam_04_stack_strategy.json"},
        precedent="dcfl-21 (live run 2026-09-27)",
    ),
    FalseProofCase(
        id="dcfl-dpda-finite-patch",
        pipeline="dcfl",
        title="Deterministic PDA for a^n b^n u a^n b^2n that handles the second branch only for n <= 3",
        ir=_load("dcfl_system/examples/eval/dcfl-05.json"),
        agents={"stack_strategy": {
            "agent_name": "stack_strategy", "status": "success", "verdict": "dcfl",
            "proof_sketch": {
                "kind": "stack_strategy",
                "phases": [
                    {"name": "push a^n", "action": "push", "what": "A на каждую a", "trigger": "a"},
                    {"name": "pop b^n", "action": "pop", "what": "снять A на каждую b", "trigger": "b"},
                    {"name": "second b^n", "action": "skip", "what": "досчитать b в состоянии",
                     "trigger": "стек пуст"},
                ],
                "separator": "смена a -> b",
                "finite_control": "состояние помнит min(n, 4)",
                "determinism_argument": "для каждой пары (состояние, вершина) не более одного перехода",
                "regex_in_states": [],
                "dpda": _dpda_anbn_plus_small_double(),
            },
        }},
        reasoning={"action": "done", "verdict": "dcfl", "confidence": 0.95,
                   "primary_evidence": "stack_strategy"},
        claimed_verdict="dcfl",
        true_verdict="non_dcfl",
        flaw="bounded_equivalence",
        why_false=(
            "dcfl-05 is not a DCFL (docs/THEORY.md §1.1/§1.3). The DPDA counts the second "
            "b-block in its finite control, which works only for n <= 3; every word up to "
            "length 11 agrees with L, and a^4 b^8 (length 12) is rejected."
        ),
        counterexample={"kind": "artifact_disagrees", "artifact": "dpda", "word": "aaaabbbbbbbb",
                        "in_language": True},
        precedent="dcfl-05",
    ),
    FalseProofCase(
        id="dcfl-dpda-stray-transition",
        pipeline="dcfl",
        title="dcfl-04 DPDA with the live run's stray q0 --b--> transition",
        ir=_load("dcfl_system/examples/eval/dcfl-04.json"),
        agents={"stack_strategy": {
            **{k: v for k, v in _load(_DCFL04_MOCK_DPDA).items() if k != "proof_sketch"},
            "proof_sketch": {**_load(_DCFL04_MOCK_DPDA)["proof_sketch"],
                             "dpda": _dpda_dcfl04_with_stray_b()},
        }},
        reasoning={"action": "done", "verdict": "dcfl", "confidence": 0.95,
                   "primary_evidence": "stack_strategy"},
        claimed_verdict="dcfl",
        true_verdict="dcfl",
        flaw="wrong_artifact",
        why_false="Verdict right, artifact wrong: the DPDA accepts 'b', which is not in L.",
        counterexample={"kind": "artifact_disagrees", "artifact": "dpda", "word": "b",
                        "in_language": False},
        precedent="dcfl-04 (live run)",
    ),
    FalseProofCase(
        id="dcfl-dpda-exam04-missing-rule",
        pipeline="dcfl",
        title="exam_04: DPDA that ignores S -> aSSb",
        ir=_load("dcfl_system/examples/task_grammar_aSSb.json"),
        agents={"stack_strategy": {
            "agent_name": "stack_strategy", "status": "success", "verdict": "dcfl",
            "proof_sketch": {
                "kind": "stack_strategy",
                "phases": [{"name": "aⁿbⁿ", "action": "push", "what": "A на a", "trigger": "a"}],
                "separator": "первая буква", "finite_control": "6 состояний",
                "determinism_argument": "по одному переходу на пару", "regex_in_states": [],
                "dpda": _dpda_exam04_tree_only(),
            },
        }},
        reasoning={"action": "done", "verdict": "dcfl", "confidence": 0.9,
                   "primary_evidence": "stack_strategy"},
        claimed_verdict="dcfl",
        true_verdict="dcfl",
        flaw="wrong_artifact",
        why_false="Verdict right, artifact wrong: ababab = a·ba·ba·b is in L, the DPDA rejects it.",
        counterexample={"kind": "artifact_disagrees", "artifact": "dpda", "word": "ababab",
                        "in_language": True},
        precedent="dcfl-21",
    ),
    FalseProofCase(
        id="dcfl-closure-union-inverted",
        pipeline="dcfl",
        title="'DCFL is closed under union' used backwards for a^n b^n c^m",
        ir=_load("dcfl_system/examples/eval/dcfl-04.json"),
        agents={"closure_reduction": {
            "agent_name": "closure_reduction", "status": "success", "verdict": "non_dcfl",
            "proof_sketch": {
                "operation": "union",
                "direction": "destructive",
                "source_language": "{a^i b^j c^k | i = j or j = k} (не DCFL, dcfl-06)",
                "transformation": "M = L ∪ {a^i b^j c^j}; {a^i b^j c^j} — DCFL, M не DCFL, "
                                  "значит L не DCFL",
                "argument": _ARG,
            },
        }},
        reasoning={"action": "done", "verdict": "non_dcfl", "confidence": 0.9,
                   "primary_evidence": "closure_reduction"},
        claimed_verdict="non_dcfl",
        true_verdict="dcfl",
        flaw="inverted_closure",
        why_false=(
            "DCFL is NOT closed under union, so 'L ∪ L2 is not a DCFL while L2 is a DCFL' says nothing "
            "about L. L = {a^n b^n c^m} is a DCFL (the dcfl-04 DPDA)."
        ),
        counterexample={"kind": "dpda_matches_language", "max_len": 8, "dpda_file": _DCFL04_MOCK_DPDA},
        precedent="dcfl-04",
    ),

    # ------------------------------------------------------------------ LL
    FalseProofCase(
        id="ll-substitution-concrete-words",
        pipeline="ll",
        title="Branch-point (substitution) proof for a^n b^n with two literal words of L",
        ir=_load("ll_system/examples/eval/ll-01.json"),
        agents={"substitution_agent": {
            "verdict": "not_ll", "confidence": 0.9,
            "proof_sketch": _subst_sketch("aabb", "aaabbb"),
            "artifacts": {},
        }},
        reasoning={"action": "done", "verdict": "not_ll", "confidence": 0.95,
                   "primary_agent": "substitution_agent"},
        claimed_verdict="not_ll",
        true_verdict="ll",
        flaw="membership_only",
        why_false=(
            "Both literal words are in L, which is all the step-2 check tests; the "
            "branch-point argument itself is wrong: S -> aSb | eps is LL(1)."
        ),
        counterexample={"kind": "ll_grammar", "k": 1, "max_len": 8,
                        "grammar": _grammar("S -> aSb | ")},
        precedent="ll-01",
    ),
    FalseProofCase(
        id="ll-substitution-template",
        pipeline="ll",
        title="Branch-point proof for a^i b^j (i <= j) with n-dependent templates",
        ir=_load("ll_system/examples/eval/ll-06.json"),
        agents={"substitution_agent": {
            "verdict": "not_ll", "confidence": 0.9,
            "proof_sketch": _subst_sketch("a^n b^n", "a^n b^(n+1)"),
            "artifacts": {},
        }},
        reasoning={"action": "done", "verdict": "not_ll", "confidence": 0.95,
                   "primary_agent": "substitution_agent"},
        claimed_verdict="not_ll",
        true_verdict="ll",
        flaw="template_instance_check",
        why_false=(
            "The instantiated words are in L and share the claimed prefix, but the parser "
            "never has to decide anything at the a-block: S -> TB, T -> aTb | eps, "
            "B -> bB | eps is LL(1) (ll-06)."
        ),
        counterexample={"kind": "ll_grammar", "k": 1, "max_len": 8,
                        "grammar": _grammar("S -> TB ; T -> aTb | ; B -> bB | ")},
        precedent="ll-06",
    ),
    FalseProofCase(
        id="ll-builder-first-follow-conflict",
        pipeline="ll",
        title="LL(1) claim for S -> aS | T, T -> aTb | eps (FIRST/FIRST conflict)",
        ir=_load("ll_system/examples/eval/ll-07.json"),
        agents={"ll_grammar_builder": {
            "verdict": "ll", "confidence": 0.9,
            "proof_sketch": {"method": "ll_grammar_construction", "k": 1,
                             "grammar": _grammar("S -> aS | T ; T -> aTb | "),
                             "explanation": "таблица LL(1) без конфликтов"},
            "artifacts": {},
        }},
        reasoning={"action": "done", "verdict": "ll", "confidence": 0.95,
                   "primary_agent": "ll_grammar_builder"},
        claimed_verdict="ll",
        true_verdict="not_ll",
        flaw="false_ll_table",
        why_false="FIRST_1(aS) and FIRST_1(T) both contain a; ll-07 is not LL(k) for any k.",
        counterexample={"kind": "ll_conflict", "k": 1,
                        "grammar": _grammar("S -> aS | T ; T -> aTb | ")},
        precedent="ll-07",
    ),
    FalseProofCase(
        id="ll-builder-finite-approximation",
        pipeline="ll",
        title="LL(1) right-linear grammar equal to a^i b^j (i >= j) up to length 8",
        ir=_load("ll_system/examples/eval/ll-07.json"),
        agents={"ll_grammar_builder": {
            "verdict": "ll", "confidence": 0.9,
            "proof_sketch": {"method": "ll_grammar_construction", "k": 1,
                             "grammar": _finite_ll1_grammar_ai_bj_i_ge_j(8),
                             "explanation": "праволинейная грамматика по ДКА"},
            "artifacts": {},
        }},
        reasoning={"action": "done", "verdict": "ll", "confidence": 0.95,
                   "primary_agent": "ll_grammar_builder"},
        claimed_verdict="ll",
        true_verdict="not_ll",
        flaw="bounded_equivalence",
        why_false=(
            "The grammar generates a finite language that coincides with "
            "{a^i b^j | i >= j} on every word of length <= 8 (the equivalence bound); "
            "a^9 is in L but not generated. ll-07 is not LL(k) (docs/THEORY.md §3.4)."
        ),
        counterexample={"kind": "artifact_disagrees", "artifact": "grammar", "word": "aaaaaaaaa",
                        "in_language": True},
        precedent="ll-07",
    ),
    FalseProofCase(
        id="ll-format3-false-ll1",
        pipeline="ll",
        title="Format 3: reasoning claims S -> aS | a is LL(1)",
        ir=_LL18_K1_IR,
        agents={},
        reasoning={"action": "done", "verdict": "ll", "confidence": 0.95,
                   "primary_agent": "first_follow_oracle"},
        claimed_verdict="ll",
        true_verdict="not_ll",
        flaw="false_ll_table",
        why_false="FIRST_1(aS) = FIRST_1(a) = {a}: an LL(1) conflict (the grammar is LL(2)).",
        counterexample={"kind": "ll_conflict", "k": 1, "grammar": _LL18_K1_IR["grammar"]},
        precedent="ll-18",
    ),
)


def cases_for(pipeline: str | None = None) -> list[FalseProofCase]:
    return [c for c in CASES if pipeline is None or c.pipeline == pipeline]
