"""Claim verifier for ll_system specialist agents.

Dispatches to method-specific verifiers and returns structured results.

Trust taxonomy (docs/VERDICT_POLICY.md §1): a verifier result carries a
`trust` level, not a binary verified/not-verified flag:
    refuted < not_verified < well_formed < bounded_pass < verified
A purely *structural* pass (required fields present, internally consistent)
is `well_formed` — it is NOT `verified`. `verified` is reserved for a
deterministic, *complete* check (e.g. a full LL(k)-table test of the exact
claimed grammar). `bounded_pass` is a deterministic but *approximate* check
(sample equivalence against the task language, or an oracle-checked
concrete instantiation). `refuted` means a deterministic counterexample was
found. `not_verified` means the claim could not be checked at all (missing
fields, unparsable proof_sketch).
"""
from __future__ import annotations

import itertools
import re
from typing import Any

# Defensive import — may not be available in test environment
try:
    from ll_system.lib.ll_table_builder import check_ll_k
    _HAS_TABLE_BUILDER = True
except ImportError:
    _HAS_TABLE_BUILDER = False

try:
    from ll_system.lib.grammar_transforms import is_grammar_equivalent_sample, _generate_words
    _HAS_GRAMMAR_TRANSFORMS = True
except ImportError:
    _HAS_GRAMMAR_TRANSFORMS = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    agent: str,
    trust: str,
    checks_passed: int,
    checks_total: int,
    issues: list[str],
    details: dict | None = None,
) -> dict:
    """Create standard verification result dict.

    `trust` is one of the docs/VERDICT_POLICY.md §1 levels:
    "refuted" | "verified" | "bounded_pass" | "well_formed" | "not_verified"
    (plus "error" for a genuine exception, outside the taxonomy).
    """
    return {
        "agent": agent,
        "trust": trust,                 # docs/VERDICT_POLICY.md §1 trust level
        "verification_status": trust,   # legacy alias, same value as `trust`
        "status": trust,                # alias used by reasoning/formalizer prompts
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "issues": issues,
        "details": details or {},
    }


def _validate_grammar_structure(grammar: Any) -> list[str]:
    """Return list of structural issues with the grammar dict (empty = OK)."""
    issues: list[str] = []
    if not isinstance(grammar, dict):
        issues.append("grammar must be a dict")
        return issues
    for field in ("nonterminals", "terminals", "start", "rules"):
        if field not in grammar:
            issues.append(f"grammar missing required field '{field}'")
    if "rules" in grammar and not isinstance(grammar["rules"], list):
        issues.append("grammar['rules'] must be a list")
    return issues


def _try_word_oracle(ir: dict):
    """Best-effort membership oracle for the task's language, from `ir`.

    Returns a Callable[[str], bool] or None if no oracle can be built.
    Tries `cfl_system`'s oracle first (it handles every CFL `language_spec`
    kind, including `grammar`/`predicate` when Format 2/3 IRs reuse them),
    then falls back to `ll_system.lib.word_oracle.oracle_from_ll_ir` for the
    `set_builder` (Format 1) kind it doesn't know
    (docs/VERDICT_POLICY.md §4). Best-effort: the structural checks never
    depend on either succeeding.
    """
    try:
        from cfl_system.lib.cfl_oracle import cfl_oracle_from_ir
    except ImportError:
        cfl_oracle_from_ir = None
    if cfl_oracle_from_ir is not None:
        try:
            oracle = cfl_oracle_from_ir(ir)
        except Exception:
            oracle = None
        if oracle is not None:
            return oracle

    try:
        from ll_system.lib.word_oracle import oracle_from_ll_ir
    except ImportError:
        return None
    try:
        return oracle_from_ll_ir(ir)
    except Exception:
        return None


def _looks_concrete(word: Any) -> bool:
    """True if `word` is a plain terminal string (no template variables like n, k)."""
    if not isinstance(word, str) or not word:
        return False
    return all(ch.isalpha() for ch in word)


# ---------------------------------------------------------------------------
# Step 2 (docs/VERDICT_POLICY.md §4): real semantic checks for constructive
# claims — validate_grammar_symbols against the task alphabet, and sample
# equivalence of the candidate grammar's language against the task language.
# ---------------------------------------------------------------------------

def _task_grammar_terminals(ir: dict) -> set[str] | None:
    """Best-effort task alphabet — terminals the candidate grammar must stay
    inside of. Returns None when the task alphabet cannot be determined."""
    if ir.get("task_type") == "ll_check_grammar":
        g = ir.get("grammar")
        if isinstance(g, dict) and isinstance(g.get("terminals"), list):
            return set(g["terminals"])
    spec = ir.get("language_spec")
    if isinstance(spec, dict):
        alphabet = spec.get("alphabet")
        if isinstance(alphabet, list):
            return set(alphabet)
        if spec.get("kind") == "grammar" and isinstance(spec.get("terminals"), list):
            return set(spec["terminals"])
    return None


def _grammar_terminals_outside_task_alphabet(grammar: dict, ir: dict) -> list[str]:
    """terminals(grammar) \\ alphabet(task) — non-empty means the candidate
    grammar invents symbols the task never mentioned (TODO §1: `S→ba|bc`
    with `terminals=['a']` must not be accepted). Returns [] when the task
    alphabet is unknown (cannot refute without it)."""
    task_alphabet = _task_grammar_terminals(ir)
    if not task_alphabet:
        return []
    if not isinstance(grammar, dict):
        return []
    grammar_terminals = set(t for t in grammar.get("terminals", []) if isinstance(t, str))
    return sorted(grammar_terminals - task_alphabet)


def _task_language_equivalence_trust(grammar: dict, ir: dict, max_len: int = 8) -> tuple[str | None, dict]:
    """Best-effort check that `grammar` generates the task's language.

    Format 2 (`ll_check_grammar_lang`, language_spec.kind == "grammar"):
    real grammar-vs-grammar sample equivalence via
    `grammar_transforms.is_grammar_equivalent_sample`.

    Otherwise (Format 1, e.g. `set_builder`): falls back to a membership
    oracle for the task language if one is available (docs/VERDICT_POLICY.md
    §4 — "если есть генератор слов языка … если недоступно — well_formed").
    No oracle exists for `set_builder` in this codebase yet, so this path
    returns (None, {}) for it today, and the caller keeps trust at
    `well_formed`.

    Returns (trust, details) with trust in {"bounded_pass", "refuted", None}.
    """
    if not _HAS_GRAMMAR_TRANSFORMS or not isinstance(grammar, dict):
        return None, {}

    spec = ir.get("language_spec")
    if isinstance(spec, dict) and spec.get("kind") == "grammar":
        try:
            equal, mismatches = is_grammar_equivalent_sample(grammar, spec, max_len=max_len)
        except Exception:
            return None, {}
        if equal:
            return "bounded_pass", {"max_len": max_len, "method": "grammar_equivalent_sample"}
        return "refuted", {"max_len": max_len, "mismatches": mismatches}

    oracle = _try_word_oracle(ir)
    if oracle is None:
        return None, {}
    try:
        words = _generate_words(grammar, max_len)
    except Exception:
        return None, {}
    if not words:
        return None, {}
    mismatches: list[str] = []
    for w in sorted(words):
        try:
            if not oracle(w):
                mismatches.append(w)
        except Exception:
            return None, {}
        if len(mismatches) >= 5:
            break
    if mismatches:
        return "refuted", {"max_len": max_len, "mismatches": mismatches, "method": "word_oracle"}

    # L(G) ⊆ L checked above; docs/VERDICT_POLICY.md §1/§4 requires SAMPLE
    # EQUIVALENCE, not mere inclusion — a grammar for a strict subset of L
    # (e.g. S -> ab for {a^n b^n}) would otherwise sail through with only
    # the first direction checked (the reviewer's finding). Enumerate L up
    # to max_len via the oracle (brute force over the task alphabet — only
    # attempted when that space is small enough to be cheap) and check every
    # one of those words is also generated by the grammar (L ⊆ L(G)).
    task_alphabet = _task_grammar_terminals(ir)
    if not task_alphabet or len(task_alphabet) > 4:
        # Can't (cheaply) enumerate L itself -- inclusion alone is not
        # equivalence, so this stays well_formed rather than bounded_pass.
        return None, {}
    generated = set(words)
    missing: list[str] = []
    alphabet_sorted = sorted(task_alphabet)
    try:
        for length in range(0, max_len + 1):
            for combo in itertools.product(alphabet_sorted, repeat=length):
                w = "".join(combo)
                try:
                    in_l = oracle(w)
                except Exception:
                    continue
                if in_l and w not in generated:
                    missing.append(w)
                    if len(missing) >= 5:
                        break
            if len(missing) >= 5:
                break
    except Exception:
        return None, {}
    if missing:
        return "refuted", {
            "max_len": max_len, "missing_from_grammar": missing,
            "method": "word_oracle_equivalence",
        }
    return "bounded_pass", {"max_len": max_len, "checked_words": len(words), "method": "word_oracle_equivalence"}


def _apply_constructive_step2(grammar: dict, ir: dict) -> tuple[str, list[str], dict]:
    """Run the step-2 semantic checks shared by all constructive claims.

    Returns (trust, extra_issues, extra_details); trust is one of
    "refuted" | "bounded_pass" | "well_formed" (never a fields-missing
    status — the caller already established the grammar is structurally
    valid before calling this).
    """
    issues: list[str] = []
    details: dict = {}

    bad_terminals = _grammar_terminals_outside_task_alphabet(grammar, ir)
    if bad_terminals:
        issues.append(
            f"grammar terminals {bad_terminals} are not in the task alphabet "
            "(docs/VERDICT_POLICY.md step 2: validate_grammar_symbols)"
        )
        details["invalid_terminals"] = bad_terminals
        return "refuted", issues, details

    lang_trust, lang_details = _task_language_equivalence_trust(grammar, ir)
    if lang_details:
        details["language_equivalence"] = lang_details
    if lang_trust == "refuted":
        issues.append(
            "grammar does not generate the task language (sample equivalence check, "
            "docs/VERDICT_POLICY.md step 2)"
        )
        return "refuted", issues, details
    if lang_trust == "bounded_pass":
        return "bounded_pass", issues, details

    return "well_formed", issues, details


# ---------------------------------------------------------------------------
# Step 2 for the substitution method (docs/VERDICT_POLICY.md §4 "ll / substitution"):
# instantiate branch_words at n = k + 2 for k in {1, 2}, check both concrete
# words are in the task language (word oracle) and that they still share a
# long common run just before the claimed branch point — a necessary
# precondition for the "FIRST_k equal" claim, which otherwise stays an
# unverified (LLM) part of the argument.
# ---------------------------------------------------------------------------

_EXP_TOKEN_RE = re.compile(r"([A-Za-z])\^\{?([^\s\^}]+)\}?")
_SAFE_EXPR_RE = re.compile(r"^[0-9nk()+\-*\s]+$")
_EXP_DEPENDS_ON_N_RE = re.compile(r"\bn\b")


def _tail_token_depends_on_n(template: Any) -> bool:
    """Whether a branch-word template's LAST <terminal>^<exponent> token has
    an exponent expression that actually depends on n (e.g. "n", "n+1"),
    rather than a bare constant (e.g. "1", "3") or an expression only in k.

    The branch-point/substitution argument (docs/THEORY.md §3.3 (C)) needs
    the discriminating tail (the part after the shared a-run, e.g. b^n vs
    c^n) to encode a count tied to n: that is what lets the pigeonhole step
    substitute a mismatched n' in and land outside the language. A tail that
    doesn't depend on n at all (b^1 vs c^1, or b^n vs c^1) can never produce
    that contradiction, no matter how large n grows — such a pair is not a
    valid witness even if it happens to pass the oracle/FIRST_k checks below.
    """
    if not isinstance(template, str) or not template.strip():
        return False
    tokens = _EXP_TOKEN_RE.findall(template)
    if not tokens:
        return False
    _, exp = tokens[-1]
    return bool(_EXP_DEPENDS_ON_N_RE.search(exp))


def _eval_exponent(expr: str, n: int, k: int) -> int | None:
    """Evaluate a small arithmetic expression in n/k (only +, -, *, digits,
    parens — never anything else). Returns None if unparsable/unsafe."""
    expr = expr.strip()
    if not expr or not _SAFE_EXPR_RE.match(expr):
        return None
    try:
        value = eval(expr, {"__builtins__": {}}, {"n": n, "k": k})  # noqa: S307 — sandboxed
    except Exception:
        return None
    if not isinstance(value, int) or value < 0:
        return None
    return value


def _instantiate_word_template(template: Any, n: int, k: int) -> str | None:
    """Best-effort instantiation of an 'a^n b^n'-style template at concrete
    n, k. Returns None if the template isn't a recognizable sequence of
    <terminal>^<exponent-in-n,k> tokens (best-effort — never raises)."""
    if not isinstance(template, str) or not template.strip():
        return None
    tokens = _EXP_TOKEN_RE.findall(template)
    if not tokens:
        return None
    out: list[str] = []
    for sym, exp in tokens:
        val = _eval_exponent(exp, n, k)
        if val is None:
            return None
        out.append(sym * val)
    return "".join(out)


def _verify_branch_words_by_oracle(bw: dict, ir: dict) -> tuple[str | None, dict]:
    """Best-effort semantic check of substitution branch_words.

    Instantiates word_1/word_2 at n = k + 2 for k in {1, 2} and checks, via
    the task's word oracle (docs/VERDICT_POLICY.md §4 "ll / substitution"):

    1. **Refutation is ONLY an oracle counterexample**: if either
       instantiated word is not in L, the whole instantiated example is
       wrong regardless of anything else — `refuted`.
    2. Otherwise, a candidate confirmation at a given k requires the literal
       substrings w1[boundary:boundary+k] and w2[boundary:boundary+k] (with
       boundary = n - k) to be equal — this checks that the shared literal
       prefix reaches at least length n (a common-prefix-length test, NOT a
       comparison of FIRST_k as *sets* the way earlier revisions of this
       docstring claimed: with the words agreeing up to `boundary`, equality
       of that window is exactly the statement "the words still agree once
       more up through position n"). It is a real precondition of the
       branch-point argument (THEORY.md §3.3 (C): the sentential form a^j·δ
       shared by both derivations, for j in (n-k, n]), but not sufficient on
       its own — see point 4.
    3. A short common run (the two words' actual literal agreement doesn't
       even reach position n - k, i.e. they diverge earlier than the claimed
       branch point) is NOT a refutation — it is simply not enough data to
       confirm the claim, so it stays `well_formed` (returned as None here,
       same as "no oracle" — the caller only ever upgrades trust on an
       explicit bounded_pass).
    4. `bounded_pass` additionally requires, per docs/VERDICT_POLICY.md §4,
       that point 2 be confirmed at **both** instantiated k in {1, 2} (`all`,
       not `any` — a template that only survives at one k, e.g. a constant
       tail confirmed by chance at k=1 but not k=2, is not a valid witness),
       AND that the discriminating tail of *both* templates actually depends
       on n (`_tail_token_depends_on_n`): a claim like "a^n b^1" vs "a^n c^1"
       (or "a^3 b^1" vs "a^3 c^1") can pass the literal-prefix check above by
       sheer coincidence — both words happen to still agree through position
       n because the tail is a fixed-length constant unrelated to n — without
       the tail encoding any real count tied to n, so the pigeonhole/
       substitution step that would derive a contradiction from it can never
       go through, no matter how large n grows. Such a pair is rejected here
       even when every individual k's oracle/prefix check passes.

    Returns (trust, details); trust in {"bounded_pass", "refuted", None}.
    None means "insufficient data to confirm the claim, or not instantiable /
    no oracle / tail doesn't depend on n" — caller keeps well_formed. It is
    never a refutation on its own.
    """
    oracle = _try_word_oracle(ir)
    if oracle is None:
        return None, {}

    word_1_t = bw.get("word_1")
    word_2_t = bw.get("word_2")
    template_depends_on_n = (
        _tail_token_depends_on_n(word_1_t) and _tail_token_depends_on_n(word_2_t)
    )
    checked: list[dict] = []
    data_count = 0
    confirmed_count = 0
    for k in (1, 2):
        n = k + 2
        w1 = _instantiate_word_template(word_1_t, n, k)
        w2 = _instantiate_word_template(word_2_t, n, k)
        if w1 is None or w2 is None:
            continue
        try:
            w1_in_l = bool(oracle(w1))
            w2_in_l = bool(oracle(w2))
        except Exception:
            continue
        if not (w1_in_l and w2_in_l):
            checked.append({
                "k": k, "n": n, "word_1": w1, "word_2": w2,
                "word_1_in_l": w1_in_l, "word_2_in_l": w2_in_l,
            })
            return "refuted", {"checked": checked}

        actual_common = 0
        for a, b in zip(w1, w2):
            if a != b:
                break
            actual_common += 1
        boundary = max(n - k, 0)
        sufficient_data = (
            actual_common >= boundary
            and len(w1) >= boundary + k
            and len(w2) >= boundary + k
        )
        if sufficient_data:
            first_k_1 = w1[boundary:boundary + k]
            first_k_2 = w2[boundary:boundary + k]
            first_k_equal = first_k_1 == first_k_2
        else:
            first_k_1 = first_k_2 = None
            first_k_equal = False
        entry = {
            "k": k, "n": n, "word_1": w1, "word_2": w2,
            "word_1_in_l": w1_in_l, "word_2_in_l": w2_in_l,
            "common_run": actual_common, "boundary": boundary,
            "first_k_remainder_1": first_k_1, "first_k_remainder_2": first_k_2,
            "sufficient_data": sufficient_data, "first_k_equal": first_k_equal,
        }
        checked.append(entry)
        if sufficient_data:
            data_count += 1
            if first_k_equal:
                confirmed_count += 1

    if not checked:
        return None, {}
    # Require BOTH instantiated k in {1, 2} to have sufficient data and
    # confirm (docs/VERDICT_POLICY.md §4), and the tails to genuinely depend
    # on n -- otherwise this is not evidence AGAINST the claim (no oracle
    # counterexample was found), just not enough to confirm it.
    if data_count == 2 and confirmed_count == 2 and template_depends_on_n:
        return "bounded_pass", {"checked": checked}
    return None, {"checked": checked}


# ---------------------------------------------------------------------------
# Step 2 for the prefix_classes method (docs/VERDICT_POLICY.md §4 "ll /
# prefix_classes. Как shallit nerode_classes"): mirrors
# dcfl_system.lib.oracle_verifier's nerode_classes semantic check — instantiate
# the claimed distinguishing_suffix against concrete class representatives
# (the proof's own `representative_pairs`, if given, else random samples from
# the task language via the word oracle) and check it separates them; and
# sample short words to check the dead class isn't obviously infinite. Only
# runs for `set_builder` IRs with a word oracle available; otherwise trust
# stays at well_formed, same fallback as everywhere else in this module.
# ---------------------------------------------------------------------------

class _SearchBudgetExceededLL(Exception):
    """Internal signal: `_continuable_ll`'s search ran out of budget or hit
    an inconclusive oracle answer -- caller must treat the result as None,
    never as evidence either way."""


def _continuable_ll(
    oracle, word: str, alphabet: list[str],
    max_extra: int = 6, node_budget: int = 20_000,
) -> bool | None:
    """Whether some extension of `word` (up to `max_extra` more symbols) is
    in L, decided by an EXHAUSTIVE depth-first search over that bound (mirrors
    dcfl_system.lib.oracle_verifier._continuable: every node up to depth
    `max_extra` is visited unless cut short by `node_budget` or an
    inconclusive oracle answer).

    Returns True (a continuation into L was found), False (the exhaustive
    search visited every extension up to the bound and found none -- a
    decisive negative WITHIN this bound, per docs/VERDICT_POLICY.md §4), or
    None (the search could not be completed -- genuinely inconclusive, never
    treated as evidence of an infinite dead class)."""
    if not alphabet:
        return None
    budget = [node_budget]

    def dfs(w: str, depth: int) -> bool:
        budget[0] -= 1
        if budget[0] <= 0:
            raise _SearchBudgetExceededLL()
        try:
            verdict = oracle(w)
        except Exception:
            verdict = None
        if verdict is None:
            raise _SearchBudgetExceededLL()
        if verdict:
            return True
        if depth >= max_extra:
            return False
        return any(dfs(w + ch, depth + 1) for ch in alphabet)

    try:
        return dfs(word, 0)
    except _SearchBudgetExceededLL:
        return None


def _all_words_ll(alphabet: list[str], max_len: int) -> list[str]:
    """All words over `alphabet` of length 0..max_len, shortest first --
    NOT filtered through the task's own oracle (unlike `generate_words`,
    which only returns words already IN L, and would therefore make every
    dead-class check trivially pass via the empty extension)."""
    words = [""]
    for length in range(1, max_len + 1):
        words.extend("".join(t) for t in itertools.product(alphabet, repeat=length))
    return words


def _check_dead_class_finite_ll(ir: dict) -> tuple[bool | None, list[str]]:
    """Dead-class part of the prefix_classes step 2 (mirrors
    dcfl_system.lib.oracle_verifier._check_dead_class_finite): enumerate ALL
    words over the task's alphabet up to length 4 -- not just words the task
    language itself generates, which are already in L and so always
    "continue" via the empty extension -- and check each is continuable into
    L within an EXHAUSTIVE bounded search (up to 6 more symbols).

    A failure here (a word with provably no continuation within the bound)
    means the dead class is not finite as claimed, so the caller must cap
    trust at well_formed rather than bounded_pass -- Theorem 4.7.4 is vacuous
    once the dead class is infinite (docs/VERDICT_POLICY.md §4)."""
    spec = ir.get("language_spec")
    if not isinstance(spec, dict) or spec.get("kind") != "set_builder":
        return None, []
    alphabet = spec.get("alphabet")
    if not isinstance(alphabet, list) or not alphabet:
        return None, []
    oracle = _try_word_oracle(ir)
    if oracle is None:
        return None, []
    short_words = _all_words_ll(alphabet, 4)
    if not short_words:
        return None, []
    issues: list[str] = []
    checked = 0
    for w in short_words[:200]:
        cont = _continuable_ll(oracle, w, alphabet)
        if cont is None:
            continue
        checked += 1
        if not cont:
            issues.append(
                f"dead_class_finite check: {w!r} has NO continuation into L "
                "within an exhaustive bounded search (up to 6 more symbols)"
            )
    if checked == 0:
        return None, []
    return (len(issues) == 0), issues


def _semantic_check_prefix_classes(proof_sketch: dict, ir: dict) -> tuple[str | None, dict]:
    """Returns (trust, details); trust in {"bounded_pass", "refuted", None}.

    None means insufficient data (no oracle, non-literal suffix, or IR isn't
    set_builder) — caller keeps well_formed.
    """
    distinguishing_suffix = proof_sketch.get("distinguishing_suffix")
    if not isinstance(distinguishing_suffix, str) or not distinguishing_suffix:
        return None, {}

    spec = ir.get("language_spec")
    if not isinstance(spec, dict) or spec.get("kind") != "set_builder":
        return None, {}
    alphabet = spec.get("alphabet")
    if not isinstance(alphabet, list) or not alphabet:
        return None, {}
    alphabet_set = set(alphabet)
    if any(ch not in alphabet_set for ch in distinguishing_suffix):
        # Contains variables/prose (e.g. "b a^N b"), not a literal we can
        # instantiate mechanically.
        return None, {}

    oracle = _try_word_oracle(ir)
    if oracle is None:
        return None, {}

    details: dict = {}
    status: str | None = None

    # Representative pairs supplied by the proof itself (its general
    # separation argument, instantiated at concrete words) — the only thing
    # that can refute the claim.
    raw_pairs = proof_sketch.get("representative_pairs")
    checked_pairs: list[tuple[str, str]] = []
    if isinstance(raw_pairs, list):
        for item in raw_pairs[:5]:
            if not isinstance(item, dict):
                continue
            u, v = item.get("u"), item.get("v")
            if not (isinstance(u, str) and isinstance(v, str) and u != v):
                continue
            if any(ch not in alphabet_set for ch in u + v):
                continue
            checked_pairs.append((u, v))

    bad: list[str] = []
    n_definite = 0
    for u, v in checked_pairs:
        try:
            in_u, in_v = bool(oracle(u + distinguishing_suffix)), bool(oracle(v + distinguishing_suffix))
        except Exception:
            continue
        n_definite += 1
        if not ((in_u and not in_v) or (in_v and not in_u)):
            bad.append(f"u={u!r}, v={v!r}: uw in L={in_u}, vw in L={in_v} (same class)")

    if bad:
        return "refuted", {
            "representative_pairs_checked": checked_pairs,
            "issue": (
                "distinguishing_suffix does not separate the proof's own "
                "representative pair(s): " + "; ".join(bad)
            ),
        }
    if n_definite > 0:
        status = "bounded_pass"
        details["representative_pairs_checked"] = checked_pairs

    if status is None:
        # No usable representative pairs — fall back to samples from the
        # task language itself, but ONLY to look for supporting evidence
        # (never to refute: a suffix failing to separate arbitrary unrelated
        # strings says nothing about the proof's actual classes).
        try:
            from ll_system.lib.word_oracle import generate_words
            candidate_words = generate_words(ir, 10)
        except Exception:
            candidate_words = []
        in_bucket: list[str] = []
        out_bucket: list[str] = []
        for w in sorted(candidate_words, key=len):
            try:
                verdict_w = oracle(w + distinguishing_suffix)
            except Exception:
                continue
            (in_bucket if verdict_w else out_bucket).append(w)
        if in_bucket and out_bucket:
            status = "bounded_pass"
            n_pairs = min(3, len(in_bucket) * len(out_bucket))
            details["sampled_pairs"] = [
                (in_bucket[i % len(in_bucket)], out_bucket[i % len(out_bucket)])
                for i in range(n_pairs)
            ]

    if status == "bounded_pass":
        dead_ok, dead_issues = _check_dead_class_finite_ll(ir)
        if dead_ok is False:
            details["dead_class_finite_issues"] = dead_issues
            # An EXHAUSTIVE bounded search found a word that provably cannot
            # be continued into L within it: the proof's "dead class is
            # finite" premise is false, so Theorem 4.7.4 is vacuous
            # (docs/VERDICT_POLICY.md §4) and the caller must not report a
            # deterministic oracle pass on it. Cap at well_formed rather than
            # refuted -- this only falsifies the dead-class premise, not
            # necessarily the distinguishing_suffix/representative_pairs
            # argument checked above.
            status = "well_formed"

    return status, details


# ---------------------------------------------------------------------------
# Method-specific verifiers
# ---------------------------------------------------------------------------

def verify_ll_grammar_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify an ll_grammar_construction claim.

    Checks:
    1. Grammar is provided in proof_sketch
    2. Grammar has required fields (nonterminals, terminals, start, rules)
    3. Proposed k is a positive integer
    4. If ll_table_builder available: run check_ll_k(grammar, k) and verify is_ll_k=True
    5. If check_ll_k says not LL(k): status = "refuted"

    proof_sketch fields expected:
    {
        "method": "ll_grammar_construction",
        "k": int,
        "grammar": dict,
        "explanation": str (optional)
    }
    """
    agent = "ll_grammar_builder"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    details: dict = {}

    # Check 1: grammar provided
    # Prompt uses "ll_grammar"; accept both field names.
    checks_total += 1
    grammar = proof_sketch.get("grammar") or proof_sketch.get("ll_grammar")
    if grammar is None:
        issues.append("No grammar provided in proof_sketch")
    else:
        checks_passed += 1

        # Check 2: grammar structure
        struct_issues = _validate_grammar_structure(grammar)
        checks_total += 1
        if struct_issues:
            issues.extend(struct_issues)
        else:
            checks_passed += 1

    # Check 3: k is a positive integer
    checks_total += 1
    k = proof_sketch.get("k")
    if not isinstance(k, int) or k < 1:
        issues.append(f"k must be a positive integer, got: {k!r}")
    else:
        checks_passed += 1

    grammar_ok = bool(grammar) and not _validate_grammar_structure(grammar)
    k_ok = isinstance(k, int) and k >= 1

    if not grammar_ok or not k_ok:
        # Fields missing/invalid — no claim to check semantically.
        return _make_result(
            agent=agent, trust="not_verified", checks_passed=checks_passed,
            checks_total=checks_total, issues=issues, details=details,
        )

    # Check 4: run check_ll_k if available — the candidate grammar must at
    # least satisfy its own claim before anything else is checked.
    if _HAS_TABLE_BUILDER:
        checks_total += 1
        try:
            result = check_ll_k(grammar, k)
            details["check_ll_k_result"] = result
            if result.get("is_ll_k"):
                checks_passed += 1
            else:
                issues.append(
                    f"check_ll_k reports grammar is NOT LL({k}): "
                    f"{len(result.get('conflicts', []))} conflict(s)"
                )
                details["conflicts"] = result.get("conflicts", [])
                return _make_result(
                    agent=agent, trust="refuted", checks_passed=checks_passed,
                    checks_total=checks_total, issues=issues, details=details,
                )
        except Exception as exc:
            issues.append(f"check_ll_k raised an error: {exc}")

    # Step 2 (docs/VERDICT_POLICY.md §4): task-alphabet + language-equivalence
    trust, step2_issues, step2_details = _apply_constructive_step2(grammar, ir)
    issues.extend(step2_issues)
    details.update(step2_details)

    return _make_result(
        agent=agent, trust=trust, checks_passed=checks_passed,
        checks_total=checks_total, issues=issues, details=details,
    )


def verify_substitution_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify a substitution ("branch-point argument") claim (not-LL proof).

    New contract (docs/THEORY.md §3.3 (C)):
    {
        "method": "substitution",
        "branch_words": {
            "common_prefix": str, "word_1": str, "word_2": str,
            "lookahead_equal_because": str
        },
        "common_form_argument": str,
        "deciding_nonterminal_argument": str,
        "pigeonhole_argument": str,
        "for_all_k": bool,
        "proof_explanation": str
    }

    Checks (structural):
    1. method == "substitution"
    2. for_all_k is True
    3. branch_words is a dict with common_prefix/word_1/word_2/lookahead_equal_because
       all non-empty
    4. common_form_argument non-empty
    5. deciding_nonterminal_argument non-empty
    6. pigeonhole_argument non-empty
    7. proof_explanation non-empty

    Fields from the old (pre-revision) contract — "witness", "w1", "lookahead",
    "suffix_1", "suffix_2", "substitution_result", "why_not_in_L", "why_not_ll" —
    are recognized and reported as obsolete (they no longer count towards
    verification either way).
    """
    agent = "substitution_agent"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    obsolete: list[str] = []
    details: dict = {}

    _OBSOLETE_TOP_LEVEL = ("witness", "k", "substitution_result")
    for name in _OBSOLETE_TOP_LEVEL:
        if name in proof_sketch:
            obsolete.append(
                f"obsolete field '{name}' from the pre-revision substitution contract; ignored"
            )
    old_witness = proof_sketch.get("witness")
    if isinstance(old_witness, dict):
        for name in ("w1", "lookahead", "lookahead_v", "suffix_1", "suffix_2",
                     "why_not_in_L", "why_not_ll"):
            if name in old_witness:
                obsolete.append(
                    f"obsolete field 'witness.{name}' from the pre-revision substitution "
                    f"contract; ignored"
                )

    # Check 1: method field
    checks_total += 1
    if proof_sketch.get("method") == "substitution":
        checks_passed += 1
    else:
        issues.append(
            f"Expected method='substitution', got {proof_sketch.get('method')!r}"
        )

    # Check 2: for_all_k must be True (proof must hold for all k, not just fixed k)
    checks_total += 1
    if proof_sketch.get("for_all_k") is True:
        checks_passed += 1
    else:
        issues.append(
            f"'for_all_k' must be True for a valid not-LL proof; "
            f"got {proof_sketch.get('for_all_k')!r}"
        )

    # Check 3: branch_words structure
    checks_total += 1
    bw = proof_sketch.get("branch_words")
    required_bw_fields = ("common_prefix", "word_1", "word_2", "lookahead_equal_because")
    if isinstance(bw, dict) and all(
        isinstance(bw.get(f), str) and bw.get(f, "").strip() for f in required_bw_fields
    ):
        checks_passed += 1
        details["branch_words"] = bw
    else:
        missing = [f for f in required_bw_fields if not (isinstance(bw, dict) and bw.get(f))] if isinstance(bw, dict) else list(required_bw_fields)
        issues.append(f"Missing or empty 'branch_words' fields: {missing}")

    # Check 4: common_form_argument
    checks_total += 1
    cfa = proof_sketch.get("common_form_argument", "")
    if isinstance(cfa, str) and cfa.strip():
        checks_passed += 1
    else:
        issues.append("Missing or empty 'common_form_argument'")

    # Check 5: deciding_nonterminal_argument
    checks_total += 1
    dna = proof_sketch.get("deciding_nonterminal_argument", "")
    if isinstance(dna, str) and dna.strip():
        checks_passed += 1
    else:
        issues.append("Missing or empty 'deciding_nonterminal_argument'")

    # Check 6: pigeonhole_argument
    checks_total += 1
    pha = proof_sketch.get("pigeonhole_argument", "")
    if isinstance(pha, str) and pha.strip():
        checks_passed += 1
    else:
        issues.append("Missing or empty 'pigeonhole_argument'")

    # Check 7: proof_explanation
    checks_total += 1
    pe = proof_sketch.get("proof_explanation", "")
    if isinstance(pe, str) and pe.strip():
        checks_passed += 1
    else:
        issues.append("Missing or empty 'proof_explanation'")

    if issues:
        # Missing/invalid structural fields — no claim to check semantically.
        return _make_result(
            agent=agent, trust="not_verified", checks_passed=checks_passed,
            checks_total=checks_total, issues=issues + obsolete, details=details,
        )

    # Step 2 (docs/VERDICT_POLICY.md §4 "ll / substitution"): if word_1/word_2
    # are already concrete literal words, check them directly against the
    # oracle; otherwise try instantiating an 'a^n b^n'-style template at
    # n = k + 2, k in {1, 2} and check the concrete instantiations. Both are
    # best-effort — absent an oracle, trust stays well_formed (the derivation
    # argument itself remains an unverified LLM part either way).
    trust = "well_formed"
    w1, w2 = bw.get("word_1"), bw.get("word_2")
    if _looks_concrete(w1) and _looks_concrete(w2):
        oracle = _try_word_oracle(ir)
        if oracle is not None:
            try:
                w1_in_l, w2_in_l = bool(oracle(w1)), bool(oracle(w2))
            except Exception as exc:
                w1_in_l = w2_in_l = None
                issues.append(f"word-oracle raised an error: {exc}")
            else:
                if w1_in_l and w2_in_l:
                    trust = "bounded_pass"
                    details["word_oracle_check"] = {"word_1_in_l": True, "word_2_in_l": True}
                else:
                    issues.append(
                        f"word-oracle: word_1={w1!r} in L={w1_in_l}, "
                        f"word_2={w2!r} in L={w2_in_l} (expected both True)"
                    )
                    return _make_result(
                        agent=agent, trust="refuted", checks_passed=checks_passed,
                        checks_total=checks_total, issues=issues + obsolete, details=details,
                    )
    if trust == "well_formed":
        inst_trust, inst_details = _verify_branch_words_by_oracle(bw, ir)
        if inst_details:
            details["branch_words_instantiation"] = inst_details
        if inst_trust == "refuted":
            issues.append(
                "instantiated branch_words (n = k + 2) fail the oracle/common-run "
                "check (docs/VERDICT_POLICY.md §4)"
            )
            return _make_result(
                agent=agent, trust="refuted", checks_passed=checks_passed,
                checks_total=checks_total, issues=issues + obsolete, details=details,
            )
        if inst_trust == "bounded_pass":
            trust = "bounded_pass"

    return _make_result(
        agent=agent, trust=trust, checks_passed=checks_passed,
        checks_total=checks_total, issues=issues + obsolete, details=details,
    )


def verify_grammar_transformation_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify a grammar_transformation claim.

    Checks:
    1. Transformed grammar is provided
    2. Transformation steps listed
    3. If ll_table_builder available: check transformed grammar with check_ll_k

    proof_sketch fields expected:
    {
        "method": "grammar_transformation",
        "k": int,
        "original_grammar": dict,
        "transformed_grammar": dict,
        "transformation_steps": list[str],
        "conflicts_remaining": list (empty = success)
    }
    """
    agent = "grammar_transformer"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    details: dict = {}

    # Check 1: transformed_grammar provided
    checks_total += 1
    transformed_grammar = proof_sketch.get("transformed_grammar")
    if transformed_grammar is None:
        issues.append("No 'transformed_grammar' provided in proof_sketch")
    else:
        checks_passed += 1

        # Check 2: grammar structure
        struct_issues = _validate_grammar_structure(transformed_grammar)
        checks_total += 1
        if struct_issues:
            issues.extend(struct_issues)
        else:
            checks_passed += 1

    # Check 3: transformation_steps listed
    # Prompt uses "transformation_log" (list of step objects); accept both names.
    checks_total += 1
    transformation_steps = (
        proof_sketch.get("transformation_steps")
        or proof_sketch.get("transformation_log")
        or []
    )
    if transformation_steps and isinstance(transformation_steps, list) and len(transformation_steps) > 0:
        checks_passed += 1
        details["transformation_steps"] = transformation_steps
    else:
        issues.append("No 'transformation_steps' / 'transformation_log' listed")

    # Check 4: conflicts_remaining is empty (claim of success)
    # Prompt uses "conflicts"; accept both names.
    checks_total += 1
    conflicts_remaining = (
        proof_sketch.get("conflicts_remaining")
        if "conflicts_remaining" in proof_sketch
        else proof_sketch.get("conflicts", [])
    )
    if isinstance(conflicts_remaining, list) and len(conflicts_remaining) == 0:
        checks_passed += 1
    else:
        issues.append(f"'conflicts_remaining' / 'conflicts' is non-empty: {conflicts_remaining}")

    # Structural issues so far (missing grammar, bad structure, missing steps,
    # non-empty conflicts_remaining) — no claim to check semantically.
    if issues:
        return _make_result(
            agent=agent, trust="not_verified", checks_passed=checks_passed,
            checks_total=checks_total, issues=issues, details=details,
        )

    # Check 5: run check_ll_k on transformed grammar if available — the
    # transformed grammar must at least satisfy its own claim first.
    k = proof_sketch.get("k")
    if _HAS_TABLE_BUILDER and isinstance(k, int) and k >= 1:
        checks_total += 1
        try:
            result = check_ll_k(transformed_grammar, k)
            details["check_ll_k_result"] = result
            if result.get("is_ll_k"):
                checks_passed += 1
            else:
                issues.append(
                    f"Transformed grammar is NOT LL({k}): "
                    f"{len(result.get('conflicts', []))} conflict(s)"
                )
                return _make_result(
                    agent=agent, trust="refuted", checks_passed=checks_passed,
                    checks_total=checks_total, issues=issues, details=details,
                )
        except Exception as exc:
            issues.append(f"check_ll_k raised an error: {exc}")

    # Step 2 (docs/VERDICT_POLICY.md §4): task-alphabet + language-equivalence
    trust, step2_issues, step2_details = _apply_constructive_step2(transformed_grammar, ir)
    issues.extend(step2_issues)
    details.update(step2_details)

    return _make_result(
        agent=agent,
        trust=trust,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        details=details,
    )


def verify_marker_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify a marker_detection claim.

    Checks:
    1. marker symbol/string is identified
    2. grammar is provided
    3. If ll_table_builder available: check grammar is LL(k)

    proof_sketch fields expected:
    {
        "method": "marker_detection",
        "marker": str,
        "k": int,
        "grammar": dict (optional),
        "explanation": str
    }
    """
    agent = "marker_analyzer"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    details: dict = {}

    # Check 1: marker identified
    # Prompt uses "marker_symbol"; accept both names.
    checks_total += 1
    marker = proof_sketch.get("marker") or proof_sketch.get("marker_symbol")
    if marker and isinstance(marker, str) and len(marker.strip()) > 0:
        checks_passed += 1
        details["marker"] = marker
    else:
        issues.append("No 'marker' / 'marker_symbol' identified in proof_sketch")

    # Check 2: explanation provided
    # Prompt uses "marker_description" and "ll_usage"; accept any of the three.
    checks_total += 1
    explanation = (
        proof_sketch.get("explanation")
        or proof_sketch.get("marker_description")
        or proof_sketch.get("ll_usage")
        or ""
    )
    if explanation and isinstance(explanation, str) and len(explanation.strip()) > 0:
        checks_passed += 1
    else:
        issues.append("No 'explanation' / 'marker_description' / 'll_usage' provided in proof_sketch")

    if issues:
        # Missing marker/explanation — no claim to check semantically.
        return _make_result(
            agent=agent, trust="not_verified", checks_passed=checks_passed,
            checks_total=checks_total, issues=issues, details=details,
        )

    # Check 3: grammar provided (optional but preferred)
    # Prompt puts grammar in artifacts.ll_grammar; proof_sketch may also carry it.
    grammar = (
        proof_sketch.get("grammar")
        or proof_sketch.get("ll_grammar")
        or proof_sketch.get("_artifacts_ll_grammar")  # injected by verify_ll_claim
    )
    if grammar is None:
        # No artifact to check semantically — structural pass only.
        return _make_result(
            agent=agent, trust="well_formed", checks_passed=checks_passed,
            checks_total=checks_total, issues=issues, details=details,
        )

    checks_total += 1
    struct_issues = _validate_grammar_structure(grammar)
    if struct_issues:
        issues.extend(struct_issues)
        return _make_result(
            agent=agent, trust="not_verified", checks_passed=checks_passed,
            checks_total=checks_total, issues=issues, details=details,
        )
    checks_passed += 1

    # Check 4: run check_ll_k if available — the grammar must at least
    # satisfy its own claim before anything else is checked.
    # Prompt uses "suggested_k"; accept both names.
    k = proof_sketch.get("k") or proof_sketch.get("suggested_k")
    if _HAS_TABLE_BUILDER and isinstance(k, int) and k >= 1:
        checks_total += 1
        try:
            result = check_ll_k(grammar, k)
            details["check_ll_k_result"] = result
            if result.get("is_ll_k"):
                checks_passed += 1
            else:
                issues.append(
                    f"Grammar with marker is NOT LL({k}): "
                    f"{len(result.get('conflicts', []))} conflict(s)"
                )
                return _make_result(
                    agent=agent, trust="refuted", checks_passed=checks_passed,
                    checks_total=checks_total, issues=issues, details=details,
                )
        except Exception as exc:
            issues.append(f"check_ll_k raised an error: {exc}")

    # Step 2 (docs/VERDICT_POLICY.md §4): task-alphabet + language-equivalence
    trust, step2_issues, step2_details = _apply_constructive_step2(grammar, ir)
    issues.extend(step2_issues)
    details.update(step2_details)

    return _make_result(
        agent=agent,
        trust=trust,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        details=details,
    )


# ---------------------------------------------------------------------------
# Additional method verifiers
# ---------------------------------------------------------------------------

def verify_prefix_classes_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify a prefix_classes method claim (not-LL proof via Theorem 4.7.4 [Sh]).

    New contract (docs/THEORY.md §1.2, §3.3 (A)):
    {
        "method": "prefix_classes",
        "theorem": "Shallit 4.7.4 → not DCFL → not LL",
        "dead_class_finite": str,
        "distinguishing_suffix": str,
        "separation_argument": str,
        "for_all_k": bool,
        "conclusion": str,
        "proof_explanation": str
    }

    Checks (structural):
    1. method == "prefix_classes"
    2. for_all_k is True
    3. theorem non-empty
    4. dead_class_finite non-empty (the mandatory dead-class check)
    5. distinguishing_suffix non-empty
    6. separation_argument non-empty
    7. conclusion non-empty
    8. proof_explanation non-empty

    Fields from the old (pre-revision, "LL Nerode theorem") contract —
    "prefix_family", "distinguishability_argument" — are recognized and
    reported as obsolete (they no longer count towards verification either way).
    """
    agent = "prefix_classes_agent"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    obsolete: list[str] = []
    details: dict = {}

    for name in ("prefix_family", "distinguishability_argument"):
        if name in proof_sketch:
            obsolete.append(
                f"obsolete field '{name}' from the pre-revision prefix_classes contract "
                f"(false 'LL Nerode theorem'); ignored"
            )

    # Check 1: method
    checks_total += 1
    if proof_sketch.get("method") == "prefix_classes":
        checks_passed += 1
    else:
        issues.append(f"Expected method='prefix_classes', got {proof_sketch.get('method')!r}")

    # Check 2: for_all_k must be True
    checks_total += 1
    if proof_sketch.get("for_all_k") is True:
        checks_passed += 1
    else:
        issues.append(
            f"'for_all_k' must be True for a valid not-LL proof; "
            f"got {proof_sketch.get('for_all_k')!r}"
        )

    def _nonempty_str(field: str) -> bool:
        v = proof_sketch.get(field, "")
        return isinstance(v, str) and bool(v.strip())

    # Check 3: theorem
    checks_total += 1
    if _nonempty_str("theorem"):
        checks_passed += 1
    else:
        issues.append("Missing or empty 'theorem'")

    # Check 4: dead_class_finite — the mandatory dead-class argument
    checks_total += 1
    if _nonempty_str("dead_class_finite"):
        checks_passed += 1
        details["dead_class_finite"] = proof_sketch.get("dead_class_finite")
    else:
        issues.append(
            "Missing or empty 'dead_class_finite' — Theorem 4.7.4 is vacuous if the dead "
            "class is infinite, so this argument is mandatory"
        )

    # Check 5: distinguishing_suffix
    checks_total += 1
    if _nonempty_str("distinguishing_suffix"):
        checks_passed += 1
    else:
        issues.append("Missing or empty 'distinguishing_suffix'")

    # Check 6: separation_argument
    checks_total += 1
    if _nonempty_str("separation_argument"):
        checks_passed += 1
    else:
        issues.append("Missing or empty 'separation_argument'")

    # Check 7: conclusion
    checks_total += 1
    if _nonempty_str("conclusion"):
        checks_passed += 1
    else:
        issues.append("Missing or empty 'conclusion'")

    # Check 8: proof_explanation
    checks_total += 1
    if _nonempty_str("proof_explanation"):
        checks_passed += 1
    else:
        issues.append("Missing or empty 'proof_explanation'")

    trust = "well_formed" if not issues else "not_verified"

    # Step 2 (docs/VERDICT_POLICY.md §4 "ll / prefix_classes. Как shallit
    # nerode_classes"): instantiate distinguishing_suffix against class
    # representatives via the task's word oracle (set_builder only).
    if trust == "well_formed":
        sem_status, sem_details = _semantic_check_prefix_classes(proof_sketch, ir)
        if sem_details:
            details["distinguishing_suffix_check"] = sem_details
            checks_total += 1
            if sem_status == "refuted":
                issues.append(
                    "distinguishing_suffix does not separate the proof's class "
                    "representatives (word-oracle check, docs/VERDICT_POLICY.md §4)"
                )
                trust = "refuted"
            else:
                checks_passed += 1
                if sem_status == "bounded_pass":
                    trust = "bounded_pass"

    return _make_result(
        agent=agent,
        trust=trust,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues + obsolete,
        details=details,
    )


def verify_essential_ambiguity_claim(proof_sketch: dict, ir: dict) -> dict:
    """Verify an essential_ambiguity method claim (not-LL proof).

    Checks (structural):
    1. method == "essential_ambiguity"
    2. essentially_ambiguous == True
    3. witness_word non-empty
    4. two_parse_structures is a list with >= 2 entries
    5. why_every_grammar_ambiguous non-empty

    proof_sketch fields expected:
    {
        "method": "essential_ambiguity",
        "essentially_ambiguous": bool,
        "witness_word": str,
        "two_parse_structures": [{"structure_id":1, ...}, {"structure_id":2, ...}],
        "why_every_grammar_ambiguous": str,
        "proof_explanation": str
    }
    """
    agent = "ambiguity_detector"
    checks_passed = 0
    checks_total = 0
    issues: list[str] = []
    details: dict = {}

    # Check 1: method
    checks_total += 1
    if proof_sketch.get("method") == "essential_ambiguity":
        checks_passed += 1
    else:
        issues.append(f"Expected method='essential_ambiguity', got {proof_sketch.get('method')!r}")

    # Check 2: essentially_ambiguous flag
    checks_total += 1
    if proof_sketch.get("essentially_ambiguous") is True:
        checks_passed += 1
    else:
        issues.append(
            f"'essentially_ambiguous' must be True, got {proof_sketch.get('essentially_ambiguous')!r}"
        )

    # Check 3: witness_word
    checks_total += 1
    witness_word = proof_sketch.get("witness_word", "")
    if witness_word and isinstance(witness_word, str) and len(witness_word.strip()) > 0:
        checks_passed += 1
        details["witness_word"] = witness_word
    else:
        issues.append("'witness_word' is missing or empty")

    # Check 4: two_parse_structures with >= 2 entries
    checks_total += 1
    two_ps = proof_sketch.get("two_parse_structures")
    if isinstance(two_ps, list) and len(two_ps) >= 2:
        checks_passed += 1
        details["two_parse_structures"] = two_ps
    else:
        issues.append(
            f"'two_parse_structures' must have >= 2 entries, got: {two_ps!r}"
        )

    # Check 5: why_every_grammar_ambiguous
    checks_total += 1
    why = proof_sketch.get("why_every_grammar_ambiguous", "")
    if why and isinstance(why, str) and len(why.strip()) > 0:
        checks_passed += 1
    else:
        issues.append("'why_every_grammar_ambiguous' is missing or empty")

    # No mechanical semantic check for essential_ambiguity — structural pass
    # is well_formed, not verified.
    trust = "well_formed" if not issues else "not_verified"
    return _make_result(
        agent=agent,
        trust=trust,
        checks_passed=checks_passed,
        checks_total=checks_total,
        issues=issues,
        details=details,
    )


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

def verify_ll_claim(agent_result: dict, ir: dict) -> dict:
    """Verify a claim from any ll_system specialist agent.

    Dispatches based on agent_result["proof_sketch"]["method"]:
    - "ll_grammar_construction" → verify_ll_grammar_claim
    - "substitution" → verify_substitution_claim
    - "grammar_transformation" → verify_grammar_transformation_claim
    - "marker_detection" → verify_marker_claim
    - "prefix_classes" → verify_prefix_classes_claim
    - "essential_ambiguity" → verify_essential_ambiguity_claim
    - anything else → inconclusive

    Returns standard result dict (same structure as _make_result).

    agent_result structure:
    {
        "agent_name": str,
        "verdict": "ll" | "not_ll" | "uncertain",
        "confidence": float,
        "proof_sketch": dict | None,
        "artifacts": dict
    }
    """
    if agent_result.get("verdict") == "uncertain":
        return _make_result(
            agent=agent_result.get("agent_name", "unknown"),
            trust="not_verified",
            checks_passed=0,
            checks_total=0,
            issues=["Agent returned uncertain verdict"],
        )

    proof_sketch = agent_result.get("proof_sketch")
    if proof_sketch is None:
        return _make_result(
            agent=agent_result.get("agent_name", "unknown"),
            trust="not_verified",
            checks_passed=0,
            checks_total=0,
            issues=["No proof_sketch in agent result"],
        )

    # For marker_detection: prompt puts grammar in artifacts.ll_grammar.
    # Inject it into proof_sketch so verify_marker_claim can find it.
    method = proof_sketch.get("method", "")
    if method == "marker_detection":
        artifacts = agent_result.get("artifacts") or {}
        art_grammar = artifacts.get("ll_grammar")
        if art_grammar and not proof_sketch.get("grammar") and not proof_sketch.get("ll_grammar"):
            proof_sketch = {**proof_sketch, "_artifacts_ll_grammar": art_grammar}
    dispatch = {
        "ll_grammar_construction": verify_ll_grammar_claim,
        "substitution": verify_substitution_claim,
        "grammar_transformation": verify_grammar_transformation_claim,
        "marker_detection": verify_marker_claim,
        "prefix_classes": verify_prefix_classes_claim,
        "essential_ambiguity": verify_essential_ambiguity_claim,
    }
    verifier = dispatch.get(method)
    if verifier:
        return verifier(proof_sketch, ir)

    return _make_result(
        agent=agent_result.get("agent_name", "unknown"),
        trust="not_verified",
        checks_passed=0,
        checks_total=0,
        issues=[f"No verifier for method '{method}'"],
    )
