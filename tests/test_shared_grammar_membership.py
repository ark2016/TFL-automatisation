"""cfl / dcfl / ll decide explicit-grammar membership through one shared oracle."""
from __future__ import annotations

from cfl_system.lib.cfl_oracle import grammar_oracle
from cfl_system.lib.grammar_membership import grammar_membership
from dcfl_system.lib.word_sampler import build_grammar_membership_oracle
from ll_system.lib.grammar_transforms import _grammar_membership
from ll_system.lib.word_oracle import oracle_from_ll_ir

# S -> a S b | epsilon (spelled "ε")
G_EPS = {
    "terminals": ["a", "b"],
    "nonterminals": ["S"],
    "start": "S",
    "rules": [
        {"lhs": "S", "rhs": ["a", "S", "b"]},
        {"lhs": "S", "rhs": ["ε"]},
    ],
}
WORDS = ["", "ab", "aabb", "a", "ba", "aab", "abab", "aaabbb"]


def _expected(w: str) -> bool:
    n = len(w) // 2
    return w == "a" * n + "b" * n and len(w) % 2 == 0


def test_all_systems_agree_on_epsilon_grammar():
    shared = grammar_membership(G_EPS)
    dcfl = build_grammar_membership_oracle(G_EPS, ["a", "b"])
    ll = _grammar_membership(G_EPS)
    ll_ir = oracle_from_ll_ir({"language_spec": dict(G_EPS, kind="grammar")})
    for w in WORDS:
        assert shared(w) == _expected(w)
        assert dcfl(w) == _expected(w)
        assert ll(w) == _expected(w)
        assert ll_ir(w) == _expected(w)


def test_shared_matches_cfl_oracle_on_plain_grammar():
    g = dict(G_EPS, rules=[{"lhs": "S", "rhs": ["a", "S", "b"]}, {"lhs": "S", "rhs": []}])
    cfl = grammar_oracle(g)
    shared = grammar_membership(g)
    assert [cfl(w) for w in WORDS] == [shared(w) for w in WORDS]
