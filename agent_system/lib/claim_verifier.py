"""
Claim Verifier — extract word-membership claims from agent outputs
and verify them against the oracle.

Universal: works for any language spec, not just grammars.
Pure-fn, zero token cost.

Also implements the trust taxonomy and step-2 semantic checks from
``docs/VERDICT_POLICY.md`` §1/§4: a purely structural pass (fields present,
JSON parsed) is ``well_formed``, never ``verified``; ``verified`` is
reserved for deterministic full checks; a bounded, oracle-backed
instantiation of the pumping/Myhill-Nerode proof is ``bounded_pass``; and
an oracle counterexample to the proof itself is ``refuted``.
"""

from __future__ import annotations

import re
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Trust taxonomy (docs/VERDICT_POLICY.md §1-§2)
# ---------------------------------------------------------------------------

#: Ordering used to pick the *strongest* non-refuted evidence. ``refuted``
#: is deliberately excluded — per the policy it is never compared, only
#: checked for.
_TRUST_RANK = {"not_verified": 0, "well_formed": 1, "bounded_pass": 2, "verified": 3}

#: Confidence ceiling per trust level (docs/VERDICT_POLICY.md §2).
#: well_formed capped at 0.55 (not 0.60): without a machine check, confidence
#: must not reach 0.6, the threshold at which tfl-eval treats a verdict as
#: "confident" (docs/VERDICT_POLICY.md §2, 2026-09-27).
CONFIDENCE_CAPS: dict[str, float] = {
    "verified": 0.98,
    "bounded_pass": 0.85,
    "well_formed": 0.55,
    "not_verified": 0.40,
}


def verify_claims(
    evidence: dict[str, Any],
    oracle: Callable[[str], bool],
    alphabet: list[str] | None = None,
) -> dict[str, Any]:
    """Extract membership claims from all agent outputs and verify via oracle.

    Scans agent outputs for patterns like:
    - "a^n b^n ∈ L"
    - "aabb ∈ L"
    - "aaaabb NOT in L"
    - "aaaabb ∉ L"

    Returns dict with verified/disproved claims and counterexamples.
    """
    if alphabet is None:
        alphabet = ["a", "b"]

    all_claims: list[dict] = []

    for agent_name in ("pumping", "nerode", "closure", "re_builder",
                        "dfa_builder", "grammar_analyzer", "reasoning"):
        agent_out = evidence.get(agent_name)
        if not agent_out:
            continue
        text = _extract_text(agent_out)
        claims = _extract_claims(text, alphabet)
        for claim in claims:
            claim["agent"] = agent_name
            all_claims.append(claim)

    # Verify each claim
    verified = []
    disproved = []
    errors_found = []

    for claim in all_claims:
        word = claim["word"]
        claimed_in_L = claim["claimed_in_L"]

        try:
            actual = oracle(word)
        except (ValueError, Exception):
            continue  # word too long for oracle

        claim["oracle_says"] = actual
        claim["correct"] = (claimed_in_L == actual)

        if claim["correct"]:
            verified.append(claim)
        else:
            disproved.append(claim)
            errors_found.append(
                f"Agent '{claim['agent']}' claims "
                f"'{word}' {'∈' if claimed_in_L else '∉'} L, "
                f"but oracle says {'∈' if actual else '∉'} L"
            )

    # Trust label for this structural pass (VERDICT_POLICY.md §1): finding
    # and checking free-text word claims is still just a well_formed pass —
    # it is not a substitute for the step-2 semantic checks below.
    if disproved:
        trust = "refuted"
    elif all_claims:
        trust = "well_formed"
    else:
        trust = "not_verified"

    return {
        "total_claims": len(all_claims),
        "verified": len(verified),
        "disproved": len(disproved),
        "errors": errors_found,
        "disproved_claims": disproved[:10],  # cap for prompt size
        "verified_claims": verified[:10],
        "trust": trust,
    }


def _alphabet_charclass(alphabet: list[str] | None) -> str:
    """Build a `[...]` regex character class from the IR's terminal
    alphabet, instead of a hardcoded ``[ab]`` -- a proof over any other
    terminal set (e.g. {a,b,c}) is then scanned too. Falls back to 'ab'
    when the alphabet is missing or every symbol is multi-character (a
    `[...]` class can only hold single characters; REG/CFL alphabets here
    are single lowercase letters in practice)."""
    chars = sorted({s for s in (alphabet or []) if isinstance(s, str) and len(s) == 1})
    if not chars:
        chars = ["a", "b"]
    return "".join(re.escape(c) for c in chars)


def _extract_text(agent_output: dict) -> str:
    """Recursively extract all text from an agent output dict."""
    parts: list[str] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, str):
            parts.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(agent_output)
    return "\n".join(parts)


# Patterns for membership claims
_WORD_PATTERN = re.compile(
    r"""
    (?:^|[\s(,])                    # boundary
    ([ab]{2,12})                    # concrete word (2-12 chars of a/b)
    \s*
    (?:
        (?:∈|\\in|IN\s+L|in\s+L|belongs\s+to\s+L|∈\s*L|принадлежит)  # positive
        |
        (?:∉|\\notin|NOT\s+IN\s+L|not\s+in\s+L|∉\s*L|не\s+принадлежит|не\s+порождается)  # negative
    )
    """,
    re.VERBOSE | re.IGNORECASE | re.MULTILINE,
)

_POSITIVE_MARKERS = {"∈", "\\in", "in l", "in L", "belongs", "принадлежит", "∈ l", "∈ L"}
_NEGATIVE_MARKERS = {"∉", "\\notin", "not in", "NOT IN", "∉ l", "∉ L",
                      "не принадлежит", "не порождается"}


def _extract_claims(text: str, alphabet: list[str]) -> list[dict]:
    """Extract concrete word membership claims from text.

    The word charclass and the ``alpha_set`` filter are both derived from
    *alphabet* (the IR's actual terminal set) instead of a hardcoded
    ``[ab]``, and every marker is word-bounded (``\\b``) on both sides so
    e.g. "in L" doesn't fire inside "in length", and "in L" doesn't fire
    on the tail of "within L" either.
    """
    charclass = _alphabet_charclass(alphabet)
    alpha_set = set(alphabet) if alphabet else {"a", "b"}
    claims: list[dict] = []
    seen: set[tuple[str, bool]] = set()

    word_group = rf"[{charclass}]{{2,12}}"

    # Pattern 1: concrete words like "aabb ∈ L" or "aaaabb не порождается"
    for match in re.finditer(
        rf'["\']?({word_group})["\']?\s*'
        r'(∈|∉|\\in|\\notin|'
        r'\bNOT\s+IN\s+L\b|\bnot\s+in\s+L\b|\bIN\s+L\b|\bin\s+L\b|'
        r'\bне\s+порождается\b|\bне\s+принадлежит\b|\bпринадлежит\b|'
        r'\bНЕ\s+порождается\b|\bНЕ\s+принадлежит\b)',
        text,
        re.IGNORECASE,
    ):
        word = match.group(1)
        marker = match.group(2).lower().strip()

        if not all(c in alpha_set for c in word):
            continue

        claimed_in = not any(neg in marker for neg in
                             ["∉", "notin", "not in", "не пор", "не при"])

        key = (word, claimed_in)
        if key not in seen:
            seen.add(key)
            claims.append({
                "word": word,
                "claimed_in_L": claimed_in,
                "context": match.group(0).strip(),
            })

    # Pattern 2: "слово a⁴b² = aaaabb НЕ порождается"
    for match in re.finditer(
        rf'(?:слово|word)\s+[^=]*?=\s*({word_group})\s+'
        r'(НЕ\s+порождается|не\s+порождается|порождается|'
        r'NOT\s+in\s+L\b|in\s+L\b)',
        text,
        re.IGNORECASE,
    ):
        word = match.group(1)
        marker = match.group(2).lower()
        claimed_in = "не" not in marker and "not" not in marker

        key = (word, claimed_in)
        if key not in seen:
            seen.add(key)
            claims.append({
                "word": word,
                "claimed_in_L": claimed_in,
                "context": match.group(0).strip(),
            })

    return claims


# ---------------------------------------------------------------------------
# Parametric word-pattern instantiation (docs/VERDICT_POLICY.md §4)
# ---------------------------------------------------------------------------
#
# Word families are written like "a^(3p+2) b^p" or "a^n b a^n": a sequence
# of `letter^expr` exponent tokens (expr may use one free variable, e.g.
# "3p+2") interleaved with bare literal letters (e.g. the "b" separator in
# "a^n b a^n"). This is a deliberately minimal parser -- single free
# variable, `+ - *` arithmetic only -- matching the "минимальный шаг 2"
# scope of the policy; anything else falls back to `well_formed`.

_PARAM_TOKEN_RE = re.compile(r"([A-Za-z])\^(\([^()]*\)|[^\s()]+)|([A-Za-z])")


def _normalize_arith(expr: str) -> str:
    """Insert an explicit '*' for implicit multiplication: '3p' -> '3*p'."""
    return re.sub(r"(\d)([A-Za-z])", r"\1*\2", expr)


def _safe_eval_count(expr: str, var_name: str, value: int) -> int | None:
    """Evaluate a small arithmetic expression with *var_name* bound to
    *value*. Returns None if the expression is not a safe integer formula
    (guards against instantiating garbage into an oracle call)."""
    expr = expr.strip()
    if expr.startswith("(") and expr.endswith(")"):
        expr = expr[1:-1]
    expr = _normalize_arith(expr)
    substituted = re.sub(
        rf"(?<![A-Za-z0-9_]){re.escape(var_name)}(?![A-Za-z0-9_])",
        str(value),
        expr,
    )
    if not re.fullmatch(r"[0-9+\-*\s]+", substituted):
        return None
    try:
        count = eval(substituted, {"__builtins__": {}}, {})  # noqa: S307 — vetted charset above
    except Exception:
        return None
    if not isinstance(count, int) or count < 0:
        return None
    return count


def detect_pattern_param(pattern: str) -> str | None:
    """Return the single free variable letter used in a word pattern like
    'a^(3p+2) b^p' (-> 'p'), or None if zero or more than one are found."""
    letters: set[str] = set()
    for m in _PARAM_TOKEN_RE.finditer(pattern):
        if m.group(1) is not None:
            for ch in m.group(2):
                if ch.isalpha():
                    letters.add(ch)
    if len(letters) == 1:
        return next(iter(letters))
    return None


def instantiate_word_pattern(pattern: str, var_name: str, value: int) -> str | None:
    """Instantiate a word pattern at a concrete integer value of its free
    variable. Returns None if the pattern cannot be parsed at all (the
    caller should then fall back to `well_formed` — no oracle check)."""
    out: list[str] = []
    matched_any = False
    for m in _PARAM_TOKEN_RE.finditer(pattern):
        if m.group(1) is not None:
            matched_any = True
            count = _safe_eval_count(m.group(2), var_name, value)
            if count is None:
                return None
            out.append(m.group(1) * count)
        elif m.group(3) is not None:
            out.append(m.group(3))
    if not matched_any:
        return None
    return "".join(out)


def _witness_collector(cap: int = 30) -> tuple[list[dict], Any]:
    """Return (witnesses, add_fn) -- a small dedup-by-word accumulator for
    the concrete words a pumping/nerode step-2 check actually instantiates,
    reused by docs/VERDICT_POLICY.md R3' -- graph.py's `assemble_result_node`
    cross-checks these against the DFA behind `test_result` when a
    contradiction arises, mirroring cfl_system.lib.claim_verifier's
    `_witness_collector`/`destructive_witnesses` and cfl_system.orchestrator.
    `_cross_check_r3prime`."""
    witnesses: list[dict] = []
    seen: set[str] = set()

    def _add(word: str, expected_in_l: bool, source: str) -> None:
        if word in seen or len(witnesses) >= cap:
            return
        seen.add(word)
        witnesses.append({"word": word, "expected_in_l": expected_in_l, "source": source})

    return witnesses, _add


def _find_pumpable_partition(
    word: str,
    p: int,
    oracle: Callable[[str], bool],
    iters: tuple[int, ...] = (0, 2),
    add_witness: Callable[[str, bool, str], None] | None = None,
) -> tuple[str, str, str] | None:
    """Brute-force every xyz split of *word* with |xy| <= p, |y| >= 1.

    Returns the first split where pumping to every i in *iters* stays in L
    (i.e. the proof's claim that some i escapes L is wrong for that
    split), or None if all splits are closed (the proof holds). When
    *add_witness* is given, every pumped word found NOT in L along the way
    is recorded as a destructive witness (docs/VERDICT_POLICY.md R3') --
    these are the concrete words that "close" the proof at each split.
    """
    n = len(word)
    max_xy = min(p, n)
    for len_xy in range(1, max_xy + 1):
        for len_x in range(len_xy):
            x, y, z = word[:len_x], word[len_x:len_xy], word[len_xy:]
            if not y:
                continue
            try:
                in_l = {i: oracle(x + y * i + z) for i in iters}
            except Exception:
                continue
            if all(in_l.values()):
                return (x, y, z)
            if add_witness is not None:
                for i, was_in_l in in_l.items():
                    if not was_in_l:
                        add_witness(x + y * i + z, False, f"reg pumping p={p} x={x!r} y={y!r} i={i}")
    return None


# ---------------------------------------------------------------------------
# Pumping-lemma step-2 check (docs/VERDICT_POLICY.md §4, R6)
# ---------------------------------------------------------------------------

def verify_pumping_claim(
    pumping_output: dict | None,
    oracle: Callable[[str], bool] | None,
    alphabet: list[str] | None = None,
) -> dict[str, Any]:
    """Semantic step-2 check for a pumping-lemma proof.

    Instantiates the claimed word family at p in {2, 3, 4}, checks that
    the chosen word is in L via the oracle, and brute-forces every xyz
    split with |xy| <= p: if every split has some i in {0, 2} that leaves
    L, the proof is `bounded_pass` at those p; if some split pumps to L at
    both i=0 and i=2, the proof is `refuted`; if there is no oracle or the
    word pattern can't be parsed, it stays `well_formed` (structural only).
    """
    if not pumping_output or not isinstance(pumping_output, dict):
        return {"trust": "not_verified", "reason": "no pumping output"}

    ev = pumping_output.get("evidence", pumping_output)
    if not isinstance(ev, dict):
        return {"trust": "not_verified", "reason": "no evidence"}

    if pumping_output.get("status") != "success" or ev.get("verdict") != "non_regular":
        return {
            "trust": "not_verified",
            "reason": "not a successful non_regular pumping proof",
        }

    word_family = ev.get("word_family") or ev.get("word_pattern") or ""
    if not word_family:
        return {"trust": "well_formed", "reason": "no parametric word_family field"}

    if oracle is None:
        return {"trust": "well_formed", "reason": "no oracle available for this language"}

    var_name = detect_pattern_param(word_family)
    if var_name is None:
        return {"trust": "well_formed", "reason": "could not detect a single free variable"}

    witnesses, add_witness = _witness_collector()
    checked_p: list[int] = []
    for p in (2, 3, 4):
        word = instantiate_word_pattern(word_family, var_name, p)
        if word is None:
            return {"trust": "well_formed", "reason": "could not instantiate word pattern"}
        try:
            in_l = oracle(word)
        except Exception as exc:
            return {"trust": "well_formed", "reason": f"oracle error: {exc}"}
        if not in_l:
            return {
                "trust": "refuted",
                "reason": f"chosen word is not in L for p={p}",
                "counterexample": {"p": p, "word": word},
                "witnesses": witnesses,
            }
        add_witness(word, True, f"word_family p={p}")
        pumpable = _find_pumpable_partition(word, p, oracle, add_witness=add_witness)
        if pumpable is not None:
            x, y, z = pumpable
            return {
                "trust": "refuted",
                "reason": f"partition x={x!r} y={y!r} z={z!r} pumps within L at p={p}",
                "counterexample": {"p": p, "word": word, "x": x, "y": y, "z": z},
                "witnesses": witnesses,
            }
        checked_p.append(p)

    # docs/VERDICT_POLICY.md R3': `witnesses` are the concrete instantiated/
    # pumped words this check actually tested and their oracle-confirmed
    # membership -- reusable by a cross-check against a constructive
    # artifact (DFA) instead of re-deriving them, the same way
    # cfl_system.orchestrator._cross_check_r3prime reuses cfl_system.lib.
    # claim_verifier's `destructive_witnesses`.
    return {"trust": "bounded_pass", "checked_p": checked_p, "witnesses": witnesses}


# ---------------------------------------------------------------------------
# Myhill-Nerode step-2 check (docs/VERDICT_POLICY.md §4, R6)
# ---------------------------------------------------------------------------

def verify_nerode_claim(
    nerode_output: dict | None,
    oracle: Callable[[str], bool] | None,
    alphabet: list[str] | None = None,
) -> dict[str, Any]:
    """Semantic step-2 check for a Myhill-Nerode (distinguishability) proof.

    Instantiates 3 pairs from the claimed word family + distinguishing
    context (i, j in {2,3,4}, i < j per the prompt's own "WLOG i<j"
    convention) and checks via the oracle that exactly one of w_i·ctx,
    w_j·ctx is in L for each pair.
    """
    if not nerode_output or not isinstance(nerode_output, dict):
        return {"trust": "not_verified", "reason": "no nerode output"}

    if nerode_output.get("status") != "success":
        return {"trust": "not_verified", "reason": "agent did not produce a proof"}

    proof = nerode_output.get("proof")
    if not isinstance(proof, dict):
        proof = nerode_output.get("evidence")
    if not isinstance(proof, dict):
        return {"trust": "not_verified", "reason": "no proof"}

    contexts = proof.get("distinguishing_contexts") or []
    if not contexts or oracle is None:
        return {"trust": "well_formed", "reason": "no distinguishing_contexts or no oracle"}

    ctx0 = contexts[0]
    pair = ctx0.get("pair") or []
    context_pattern = ctx0.get("context")
    if len(pair) != 2 or not context_pattern:
        return {"trust": "well_formed", "reason": "missing pair/context fields"}

    pattern_i, pattern_j = pair
    var_i = detect_pattern_param(pattern_i)
    var_j = detect_pattern_param(pattern_j)
    var_ctx = detect_pattern_param(context_pattern)
    if var_i is None or var_j is None or var_ctx is None:
        return {"trust": "well_formed", "reason": "could not detect pair/context variable"}

    # docs/VERDICT_POLICY.md fix (reviewer finding): the context can be
    # written in terms of EITHER pair variable (e.g. a proof distinguishing
    # a^i from a^j with a context using b^j, not b^i) — always instantiating
    # it at `m` (pair[0]'s index) silently mis-evaluates any proof whose
    # context uses pair[1]'s variable, wrongly refuting a correct proof.
    # Match by name; an ambiguous/unrelated variable name is left at
    # well_formed rather than guessed.
    if var_ctx == var_i:
        ctx_var_is_i = True
    elif var_ctx == var_j:
        ctx_var_is_i = False
    elif var_i == var_j:
        # Same variable name in both pair patterns (e.g. both "a^i b^i" vs
        # "a^i" style proofs reusing one letter) — instantiating at either
        # index is unambiguous only if the context also shares that name.
        ctx_var_is_i = True
    else:
        return {
            "trust": "well_formed",
            "reason": (
                f"context variable {var_ctx!r} matches neither pair variable "
                f"({var_i!r}, {var_j!r}) — cannot instantiate unambiguously"
            ),
        }

    witnesses, add_witness = _witness_collector()
    checked_pairs: list[list[int]] = []
    for m, n in ((2, 3), (2, 4), (3, 4)):
        w_i = instantiate_word_pattern(pattern_i, var_i, m)
        w_j = instantiate_word_pattern(pattern_j, var_j, n)
        ctx = instantiate_word_pattern(context_pattern, var_ctx, m if ctx_var_is_i else n)
        if w_i is None or w_j is None or ctx is None:
            return {"trust": "well_formed", "reason": "could not instantiate pair/context"}
        try:
            in_i = oracle(w_i + ctx)
            in_j = oracle(w_j + ctx)
        except Exception as exc:
            return {"trust": "well_formed", "reason": f"oracle error: {exc}"}
        if in_i == in_j:
            return {
                "trust": "refuted",
                "reason": (
                    f"context {ctx!r} does not distinguish {w_i!r} from {w_j!r} "
                    f"(both {'in' if in_i else 'not in'} L)"
                ),
                "counterexample": {"i": m, "j": n, "w_i": w_i, "w_j": w_j, "context": ctx},
                "witnesses": witnesses,
            }
        add_witness(w_i + ctx, in_i, f"nerode pair i={m} j={n} (w_i side)")
        add_witness(w_j + ctx, in_j, f"nerode pair i={m} j={n} (w_j side)")
        checked_pairs.append([m, n])

    # docs/VERDICT_POLICY.md R3' -- see the matching comment in
    # verify_pumping_claim above.
    return {"trust": "bounded_pass", "checked_pairs": checked_pairs, "witnesses": witnesses}


# ---------------------------------------------------------------------------
# Combine destructive-side trust for the final verdict gate (R6, R1, R3)
# ---------------------------------------------------------------------------

def closure_trust_from_verification(
    closure_output: dict | None,
    closure_verification: dict | None,
) -> str:
    """Map `_verify_closure_claim`'s empirical Nerode-index estimate onto
    the trust taxonomy. The estimate is always bounded (timeout + depth
    cutoff), so even a confirmed "verified" status is only `bounded_pass`,
    never the full `verified` level reserved for deterministic checks."""
    if not closure_output or closure_output.get("status") != "success":
        return "not_verified"
    if closure_verification is None:
        # Closure agent produced a proof but it was never oracle-checked
        # (e.g. no oracle available) -- structural pass only.
        return "well_formed"
    status = closure_verification.get("status")
    return {
        "verified": "bounded_pass",
        "plausible": "well_formed",
        "disproved": "refuted",
    }.get(status, "well_formed")


def compute_destructive_trust(
    evidence: dict,
    closure_verification: dict | None = None,
) -> dict[str, Any]:
    """Combine trust from the pumping/nerode/closure destructive
    specialists into a single verdict-gate basis (VERDICT_POLICY.md R1/R3/R6).

    Reads `evidence["pumping_verification"]` / `["nerode_verification"]`
    (as computed by `verify_pumping_claim` / `verify_nerode_claim`) and
    `evidence["closure"]` + *closure_verification*.

    Returns ``{"trust": str | None, "agents": [...], "best_agent": str}``.
    ``trust`` is ``None`` when no destructive specialist produced any
    usable evidence at all — distinct from ``"not_verified"``, which means
    at least one ran but nothing could be checked.
    """
    agents: list[dict[str, Any]] = []

    pumping_check = evidence.get("pumping_verification")
    if pumping_check is not None:
        agents.append({"agent": "pumping", "trust": pumping_check["trust"],
                        "details": pumping_check})

    nerode_check = evidence.get("nerode_verification")
    if nerode_check is not None:
        agents.append({"agent": "nerode", "trust": nerode_check["trust"],
                        "details": nerode_check})

    closure_output = evidence.get("closure")
    if closure_output is not None:
        trust = closure_trust_from_verification(closure_output, closure_verification)
        agents.append({"agent": "closure", "trust": trust,
                        "details": closure_verification or {}})

    if not agents:
        return {"trust": None, "agents": []}

    non_refuted = [a for a in agents if a["trust"] != "refuted"]
    if not non_refuted:
        return {"trust": "refuted", "agents": agents}

    usable = [a for a in non_refuted if a["trust"] != "not_verified"]
    if not usable:
        return {"trust": "not_verified", "agents": agents}

    best = max(usable, key=lambda a: _TRUST_RANK.get(a["trust"], 0))
    return {"trust": best["trust"], "agents": agents, "best_agent": best["agent"]}
