"""LL(k) predictive parser using a precomputed parse table."""
from __future__ import annotations

from ll_system.lib.ll_table_builder import check_ll_k

_MAX_STEPS = 10_000


class LLParser:
    """LL(k) predictive parser backed by a precomputed parse table.

    The parser uses a stack-based simulation of the LL(k) algorithm.
    Each element on the stack is a grammar symbol (terminal or nonterminal)
    or the end-of-input sentinel ``"$"``.
    """

    def __init__(self, grammar: dict, k: int) -> None:
        """Build a parser for *grammar* with lookahead *k*.

        Raises ``ValueError`` if *grammar* is not LL(k).
        """
        result = check_ll_k(grammar, k)
        if not result["is_ll_k"]:
            raise ValueError(
                f"Grammar is not LL({k}). "
                f"Conflicts: {result['conflicts']}"
            )
        self._grammar = grammar
        self._k = k
        self._table: dict[str, dict[str, list[str]]] = result["parse_table"]
        self._nonterminals: set[str] = set(grammar["nonterminals"])
        self._terminals: set[str] = set(grammar["terminals"])

    # ------------------------------------------------------------------
    # Alternative constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_table(
        cls, grammar: dict, parse_table: dict, k: int
    ) -> "LLParser":
        """Create a parser from a precomputed *parse_table* (skips LL(k) check)."""
        obj = object.__new__(cls)
        obj._grammar = grammar
        obj._k = k
        obj._table = parse_table
        obj._nonterminals = set(grammar["nonterminals"])
        obj._terminals = set(grammar["terminals"])
        return obj

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse(
        self, word: str, *, record_steps: bool = True, max_steps: int = _MAX_STEPS,
    ) -> dict:
        """Parse *word* using the LL(k) algorithm.

        *word* is treated as a sequence of single-character terminals.
        ``record_steps=False`` leaves ``"steps"`` empty (bulk checks run the
        parser on thousands of words); ``max_steps`` bounds the run.

        Returns a dict with:
        - ``"accepted"`` (bool)
        - ``"word"`` (str)
        - ``"steps"`` (list[dict]) — parse steps for debugging
        - ``"error"`` (str | None)
        - ``"non_halting"`` (True, only when the run provably never halts:
          a nonterminal came back on top, with the same lookahead, before its
          own expansion finished; ``"loop"`` names it)
        - ``"step_limit_exceeded"`` (True, only when ``max_steps`` ran out
          without such a repetition)

        Non-halting detection is exact. Between two consumed input symbols
        the lookahead is fixed, so the run depends on the stack alone. An
        expansion of A at stack height h is *open* while the stack has not
        dropped below h (A's subtree is unfinished). If A comes back on top
        while its expansion is open, the stack above height h - 1 has gone
        from [A] to [..., A] without reading input or touching what lies
        below, so the same steps repeat forever (left recursion, possibly
        through nullable symbols, e.g. a cell A -> A x). Conversely, a run
        that never halts reads finitely many symbols and then expands forever;
        the lowest stack height it keeps returning to is never left below,
        so the expansions opened there stay open and, nonterminals being
        finitely many, one of them comes back on top. Every loop is caught.
        """
        k = self._k
        start = self._grammar["start"]
        eos = "$" * k  # end-of-input sentinel (length k)

        # Stack: list where the *last* element is the top.
        # Initialise with end sentinel first (bottom), then start symbol on top.
        stack: list[str] = [eos, start]
        pos: int = 0       # position in *word*
        steps: list[dict] = []
        # nonterminal -> stack height of its open expansion, since the last
        # consumed input symbol (see the docstring).
        open_expansions: dict[str, int] = {}

        def _lookahead() -> str:
            """Return the next k characters from the remaining input, padded with $."""
            remaining = word[pos:]
            padded = remaining + "$" * k
            return padded[:k]

        for step_no in range(max_steps):
            if not stack:
                # Empty stack — should not happen (we always have eos on bottom)
                return {
                    "accepted": False,
                    "word": word,
                    "steps": steps,
                    "error": "Stack underflow",
                }

            top = stack[-1]
            la = _lookahead()

            if record_steps:
                steps.append(
                    {
                        "step": step_no,
                        "stack_top": top,
                        "lookahead": la,
                        "pos": pos,
                    }
                )

            # ---- End sentinel on top of stack ----------------------------
            if top == "$" or (k > 1 and top == eos):
                if la == eos:
                    return {
                        "accepted": True,
                        "word": word,
                        "steps": steps,
                        "error": None,
                    }
                return {
                    "accepted": False,
                    "word": word,
                    "steps": steps,
                    "error": (
                        f"Expected end of input at position {pos}, "
                        f"got lookahead '{la}'"
                    ),
                }

            # ---- Terminal on top of stack ---------------------------------
            if top in self._terminals:
                if pos < len(word) and word[pos] == top:
                    stack.pop()
                    pos += 1
                    open_expansions.clear()
                    continue
                return {
                    "accepted": False,
                    "word": word,
                    "steps": steps,
                    "error": (
                        f"Expected terminal '{top}' at position {pos}, "
                        f"got '{word[pos] if pos < len(word) else '<eof>'}"
                    ),
                }

            # ---- Nonterminal on top of stack ------------------------------
            if top in self._nonterminals:
                rule_rhs = self._table.get(top, {}).get(la)
                if rule_rhs is None:
                    return {
                        "accepted": False,
                        "word": word,
                        "steps": steps,
                        "error": (
                            f"No parse table entry for ({top!r}, {la!r}) "
                            f"at position {pos}"
                        ),
                    }
                height = len(stack)
                if open_expansions:
                    open_expansions = {
                        nt: h for nt, h in open_expansions.items() if h <= height
                    }
                if top in open_expansions:
                    return {
                        "accepted": False,
                        "word": word,
                        "steps": steps,
                        "error": (
                            f"Parser never halts: {top!r} comes back on top with "
                            f"lookahead {la!r} at position {pos} before its own "
                            "expansion finished (left recursion)"
                        ),
                        "non_halting": True,
                        "loop": {"nonterminal": top, "lookahead": la, "pos": pos},
                    }
                open_expansions[top] = height
                stack.pop()
                # Push rhs right-to-left so the leftmost symbol ends up on top.
                for sym in reversed(rule_rhs):
                    if sym != "ε":
                        stack.append(sym)
                continue

            # ---- Unknown symbol -------------------------------------------
            return {
                "accepted": False,
                "word": word,
                "steps": steps,
                "error": f"Unknown stack symbol '{top}'",
            }

        # Neither a rejection nor a proven loop (loops return above): the run
        # was still making progress when the step budget ran out.
        return {
            "accepted": False,
            "word": word,
            "steps": steps,
            "error": f"Step limit ({max_steps}) exceeded",
            "step_limit_exceeded": True,
        }


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def parse_word(grammar: dict, k: int, word: str) -> dict:
    """Check that *grammar* is LL(k), then parse *word*.

    Returns the same dict as :py:meth:`LLParser.parse`.
    If the grammar is not LL(k) the function returns
    ``{"accepted": False, "word": word, "steps": [], "error": "Grammar is not LL(k)"}``.
    """
    try:
        parser = LLParser(grammar, k)
    except ValueError:
        return {
            "accepted": False,
            "word": word,
            "steps": [],
            "error": "Grammar is not LL(k)",
        }
    return parser.parse(word)
