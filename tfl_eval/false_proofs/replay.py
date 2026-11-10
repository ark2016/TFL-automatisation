"""Replay a false-proof case through its pipeline's own verification tail.

Each ``replay_<pipeline>`` builds the pipeline state the real graph would
have right after the specialists ran (the case's agent outputs, nothing
else) and calls the pipeline's own nodes in graph order -- oracle build,
claim verification, oracle test / FIRST-FOLLOW oracle -- then the verdict
gate with the retry budget exhausted, so the gate has to decide on this
evidence alone. No LLM is called and no verifier is re-implemented here.

``check_counterexample`` independently confirms each case's recorded
counterexample with the task's membership oracle, so the corpus cannot
silently contain a "false" proof that is actually right.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from tfl_eval.false_proofs.corpus import REPO_ROOT, FalseProofCase

TRUST_ORDER = ("refuted", "not_verified", "well_formed", "bounded_pass", "verified")
CONFIDENT = 0.6  # tfl-eval's "confident" threshold (tfl_eval.metrics)


def trust_rank(trust: str | None) -> int:
    return TRUST_ORDER.index(trust) if trust in TRUST_ORDER else -1


@dataclass
class ReplayResult:
    case_id: str
    pipeline: str
    claimed_verdict: str
    true_verdict: str
    agent_trust: str | None
    gate_verdict: str | None
    gate_confidence: float
    downgrades: list[str]

    @property
    def above_well_formed(self) -> bool:
        return trust_rank(self.agent_trust) > trust_rank("well_formed")

    @property
    def accepted_claim(self) -> bool:
        """The gate kept the false proof's verdict."""
        return self.gate_verdict == self.claimed_verdict

    @property
    def wrong_verdict(self) -> bool:
        """The gate's final verdict is a definite verdict that is false."""
        return self.gate_verdict not in (None, "uncertain", "inconclusive") \
            and self.gate_verdict != self.true_verdict

    @property
    def confident_wrong(self) -> bool:
        return self.wrong_verdict and self.gate_confidence >= CONFIDENT

    def as_dict(self) -> dict:
        d = asdict(self)
        d.update(above_well_formed=self.above_well_formed, accepted_claim=self.accepted_claim,
                 wrong_verdict=self.wrong_verdict, confident_wrong=self.confident_wrong)
        return d


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


# ---------------------------------------------------------------------------
# Per-pipeline replays
# ---------------------------------------------------------------------------

def replay_reg(case: FalseProofCase) -> ReplayResult:
    from agent_system import graph as g

    state: dict[str, Any] = {
        "ir": _copy(case.ir), "evidence": _copy(case.agents), "retry_round": 0,
        "dispatch": {}, "verbose": False, "hypothesis": {},
        "reasoning_output": _copy(case.reasoning),
    }
    for node in (g.build_oracle_node, g.verify_claims_node, g.oracle_test_node):
        state.update(node(state))
    result = g.assemble_result_node(state)["result"]

    evidence = state["evidence"]
    agent_trust = None
    if state.get("test_result") is not None:
        agent_trust = "bounded_pass" if state["test_result"].get("status") == "pass" else "refuted"
    for key in ("pumping_verification", "nerode_verification"):
        if key in evidence:
            agent_trust = evidence[key]["trust"]
    gate = result.get("verdict_gate") or {}
    return ReplayResult(
        case.id, case.pipeline, case.claimed_verdict, case.true_verdict, agent_trust,
        result.get("verdict"), float(result.get("confidence") or 0.0),
        list(gate.get("downgrades") or []),
    )


def replay_cfl(case: FalseProofCase) -> ReplayResult:
    from cfl_system import orchestrator as o

    state: dict[str, Any] = {
        "ir": _copy(case.ir), "agent_results": _copy(case.agents),
        "reasoning_output": _copy(case.reasoning), "retry_round": o.MAX_RETRIES,
        "verbose": False,
    }
    for node in (o.build_oracle_node, o.verify_claims_node, o.oracle_test_node):
        state.update(node(state))
    gate = o.apply_verdict_gate(state)
    reasoning = gate["reasoning_output"]
    trust_map = gate.get("trust") or {}
    agent = next(iter(case.agents), None)
    return ReplayResult(
        case.id, case.pipeline, case.claimed_verdict, case.true_verdict,
        trust_map.get(agent) if agent else None, reasoning.get("verdict"),
        float(reasoning.get("confidence") or 0.0),
        list(gate["verdict_gate"].get("downgrades") or []),
    )


def replay_dcfl(case: FalseProofCase) -> ReplayResult:
    from dcfl_system import orchestrator as o
    from dcfl_system.lib.oracle_verifier import verify_agent_results

    agents = _copy(case.agents)
    ir = _copy(case.ir)
    verification = verify_agent_results(agents, ir)
    gated = o._apply_verdict_gate(
        _copy(case.reasoning), agents, verification,
        retry_count=o.MAX_RETRIES, max_retries=o.MAX_RETRIES,
    )
    agent = next(iter(case.agents), None)
    entry = verification.get(agent) or {}
    gate = gated.get("verdict_gate") or {}
    return ReplayResult(
        case.id, case.pipeline, case.claimed_verdict, case.true_verdict,
        entry.get("trust") or entry.get("verification_status"), gated.get("verdict"),
        float(gated.get("confidence") or 0.0), list(gate.get("downgrades") or []),
    )


def replay_ll(case: FalseProofCase) -> ReplayResult:
    from ll_system import orchestrator as o

    state: dict[str, Any] = {
        "ir": _copy(case.ir), "agent_results": _copy(case.agents),
        "reasoning_output": _copy(case.reasoning), "retry_round": o.MAX_RETRIES,
        "specialist_outputs": [], "verbose": False,
    }
    for node in (o.validate_ir_node, o.verify_claims_node, o.first_follow_oracle_node):
        state.update(node(state))
    gate = o.apply_verdict_gate(state)
    reasoning = gate["reasoning_output"]
    agent = next(iter(case.agents), None)
    if agent is not None:
        entry = state["claim_verification"].get(agent) or {}
        agent_trust = entry.get("trust") or entry.get("verification_status")
    else:
        ff = state.get("first_follow_result") or {}
        agent_trust = "refuted" if ff.get("is_ll_k") is False else (
            "verified" if ff.get("is_ll_k") else None)
    return ReplayResult(
        case.id, case.pipeline, case.claimed_verdict, case.true_verdict, agent_trust,
        reasoning.get("verdict"), float(reasoning.get("confidence") or 0.0),
        list(gate["verdict_gate"].get("downgrades") or []),
    )


REPLAYS: dict[str, Callable[[FalseProofCase], ReplayResult]] = {
    "reg": replay_reg, "cfl": replay_cfl, "dcfl": replay_dcfl, "ll": replay_ll,
}


def replay(case: FalseProofCase) -> ReplayResult:
    return REPLAYS[case.pipeline](case)


# ---------------------------------------------------------------------------
# Counterexample checks (independent of the verifiers under test)
# ---------------------------------------------------------------------------

def language_oracle(case: FalseProofCase) -> Callable[[str], bool | None]:
    """The task's membership oracle, built by the pipeline's own builder."""
    if case.pipeline == "reg":
        from agent_system.lib.oracle import oracle_from_ir
        return oracle_from_ir(case.ir)
    if case.pipeline == "cfl":
        from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir
        return cfl_oracle_from_ir(case.ir)
    if case.pipeline == "dcfl":
        from dcfl_system.lib.word_sampler import build_membership_oracle_from_ir
        return build_membership_oracle_from_ir(case.ir)
    from ll_system.lib.claim_verifier import _try_word_oracle
    if "grammar" in case.ir and "language_spec" not in case.ir:
        from cfl_system.lib.cfl_oracle import grammar_oracle
        return grammar_oracle(case.ir["grammar"])
    return _try_word_oracle(case.ir)


def alphabet_of(case: FalseProofCase) -> list[str]:
    spec = case.ir.get("language_spec") or {}
    for source in (spec.get("alphabet"), spec.get("terminals"), case.ir.get("alphabet"),
                   (case.ir.get("grammar") or {}).get("terminals")):
        if source:
            return list(source)
    alphabets = spec.get("alphabets")
    if isinstance(alphabets, dict):
        return sorted({ch for chars in alphabets.values() for ch in chars})
    return ["a", "b"]


def _words(alphabet: list[str], max_len: int):
    for n in range(max_len + 1):
        for combo in itertools.product(sorted(alphabet), repeat=n):
            yield "".join(combo)


def _artifact_acceptor(case: FalseProofCase, kind: str) -> Callable[[str], bool | None]:
    if kind == "regex":
        pattern = re.compile(case.counterexample["regex"])
        return lambda w: bool(pattern.fullmatch(w))
    output = next(iter(case.agents.values()))
    sketch = output.get("proof_sketch") or {}
    if kind == "dpda":
        from dcfl_system.lib.dpda import dpda_run, normalize_epsilon_accept_sinks
        dpda, _ = normalize_epsilon_accept_sinks(sketch["dpda"])
        return lambda w: dpda_run(dpda, w)
    if kind == "grammar":
        from cfl_system.lib.cfl_oracle import grammar_oracle
        return grammar_oracle(sketch["grammar"])
    raise ValueError(f"unknown artifact kind {kind!r}")


def check_counterexample(case: FalseProofCase) -> tuple[bool, str]:
    """Confirm the case's recorded counterexample; (ok, explanation)."""
    ce = case.counterexample
    kind = ce["kind"]
    oracle = language_oracle(case)

    if kind == "nerode_equivalent":
        u, v = ce["u"], ce["v"]
        for s in _words(alphabet_of(case), ce["max_suffix_len"]):
            if oracle(u + s) != oracle(v + s):
                return False, f"suffix {s!r} separates {u!r} and {v!r}"
        return True, f"{u!r} and {v!r} agree on every suffix up to length {ce['max_suffix_len']}"

    if kind == "pumpable_split":
        parts = ce["parts"]
        pumped = [1] if len(parts) == 3 else [1, 3]  # x y^i z  |  u v^i w x^i y
        for i in range(5):
            word = "".join(p * i if idx in pumped else p for idx, p in enumerate(parts))
            if oracle(word) is not True:
                return False, f"pumped word {word!r} (i={i}) is not in L"
        return True, f"split {parts} pumps for i = 0..4"

    if kind == "artifact_disagrees":
        word = ce["word"]
        if oracle(word) is not ce["in_language"]:
            return False, f"oracle says {word!r} in L = {oracle(word)}"
        accepted = _artifact_acceptor(case, ce["artifact"])(word)
        if accepted is not (not ce["in_language"]):
            return False, f"artifact answers {accepted} on {word!r}"
        return True, f"{word!r}: in L = {ce['in_language']}, artifact accepted = {accepted}"

    if kind == "in_language_not_in_claim":
        from cfl_system.lib.exponent_pattern import parse_exponent_pattern
        word = ce["word"]
        claimed = parse_exponent_pattern(ce["claimed"])
        ok = oracle(word) is True and re.fullmatch(ce["regex"], word) and claimed(word) is False
        return bool(ok), f"{word!r} is in L ∩ {ce['regex']} but not in {ce['claimed']}"

    if kind == "prefix_pair_outside_claim":
        from cfl_system.lib.exponent_pattern import parse_exponent_pattern
        x, y = ce["x"], ce["y"]
        claimed = parse_exponent_pattern(ce["claimed"])
        ok = oracle(x) is True and oracle(x + y) is True and claimed(x + y) is False
        return ok, f"x={x!r} ∈ L, xy={x + y!r} ∈ L, so x$y ∈ L_$ but xy ∉ {ce['claimed']}"

    if kind == "dead_prefix":
        word = ce["word"]
        for s in _words(alphabet_of(case), ce["max_extra"]):
            if oracle(word + s):
                return False, f"{word!r} continues to {word + s!r} in L"
        return True, f"{word!r} has no continuation in L up to {ce['max_extra']} extra symbols"

    if kind in ("cfg_matches_language", "dpda_matches_language", "ll_grammar"):
        if kind == "dpda_matches_language":
            from dcfl_system.lib.dpda import dpda_run, normalize_epsilon_accept_sinks
            with open(REPO_ROOT / ce["dpda_file"], encoding="utf-8") as fh:
                dpda, _ = normalize_epsilon_accept_sinks(json.load(fh)["proof_sketch"]["dpda"])
            artifact = lambda w: dpda_run(dpda, w)
        else:
            from cfl_system.lib.cfl_oracle import grammar_oracle
            artifact = grammar_oracle(ce["grammar"])
        if kind == "ll_grammar":
            from ll_system.lib.ll_table_builder import check_ll_k
            if not check_ll_k(ce["grammar"], ce["k"]).get("is_ll_k"):
                return False, f"grammar is not LL({ce['k']})"
        for w in _words(alphabet_of(case), ce["max_len"]):
            if bool(artifact(w)) != bool(oracle(w)):
                return False, f"artifact and L differ on {w!r}"
        suffix = f" and is LL({ce['k']})" if kind == "ll_grammar" else ""
        return True, f"artifact equals L on every word up to length {ce['max_len']}{suffix}"

    if kind == "ll_conflict":
        from ll_system.lib.ll_table_builder import check_ll_k
        result = check_ll_k(ce["grammar"], ce["k"])
        return result.get("is_ll_k") is False, f"check_ll_k(k={ce['k']}) is_ll_k = {result.get('is_ll_k')}"

    raise ValueError(f"unknown counterexample kind {kind!r}")
