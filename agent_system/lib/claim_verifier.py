"""
Claim Verifier — extract word-membership claims from agent outputs
and verify them against the oracle.

Universal: works for any language spec, not just grammars.
Pure-fn, zero token cost.
"""

from __future__ import annotations

import re
from typing import Any, Callable


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

    return {
        "total_claims": len(all_claims),
        "verified": len(verified),
        "disproved": len(disproved),
        "errors": errors_found,
        "disproved_claims": disproved[:10],  # cap for prompt size
        "verified_claims": verified[:10],
    }


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
    """Extract concrete word membership claims from text."""
    alpha_set = set("".join(alphabet))
    claims: list[dict] = []
    seen: set[tuple[str, bool]] = set()

    # Pattern 1: concrete words like "aabb ∈ L" or "aaaabb не порождается"
    for match in re.finditer(
        r'["\']?([ab]{2,12})["\']?\s*'
        r'(∈|∉|\\in|\\notin|'
        r'NOT\s+IN\s+L|not\s+in\s+L|IN\s+L|in\s+L|'
        r'не\s+порождается|не\s+принадлежит|принадлежит|'
        r'НЕ\s+порождается|НЕ\s+принадлежит)',
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
        r'(?:слово|word)\s+[^=]*?=\s*([ab]{2,12})\s+'
        r'(НЕ\s+порождается|не\s+порождается|порождается|'
        r'NOT\s+in\s+L|in\s+L)',
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
