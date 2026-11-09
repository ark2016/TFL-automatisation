"""Tool base for the tfl_deep_agent tool layer (P1).

Every TFL tool the agent sees is a thin, pydantic-typed wrapper over a function
of ``agent_system/lib``, ``cfl_system/lib``, ``dcfl_system/lib`` or
``ll_system/lib`` (inventory: ``tfl_deep_agent/docs/TOOL_INVENTORY.md``). This
module gives the group modules (``tools/ir.py``, ``tools/words.py``,
``tools/reg.py`` ...) four things:

* :class:`ToolResult` -- the one result shape. It carries ``trust`` (one of
  ``refuted`` / ``verified`` / ``bounded_pass`` / ``well_formed`` /
  ``not_verified``, docs/VERDICT_POLICY.md §1), a ``trust_reason`` and, for
  bounded checks, the actual ``bounds`` of the search.
* input size limits (:class:`Limits`, ``check_*`` helpers and the annotated
  pydantic types :data:`Word`, :data:`Symbol`, :data:`Alphabet`, ...).
* wall-clock timeouts (:class:`Deadline` for cooperative loops,
  :func:`run_with_timeout` for a whole call, in a thread or a killable process).
* :class:`ToolRegistry` with the module-level :data:`REGISTRY` and the
  :func:`tfl_tool` decorator, which turn a registered function into a LangChain
  ``StructuredTool`` (``response_format="content_and_artifact"``).

Trust is computed by wrapper code, never taken from model input:

* every args schema derives from :class:`ToolArgs` (``extra="forbid"``) and may
  not declare a field named ``trust`` / ``confidence`` / ... anywhere, nested
  models included (checked at registration), so the model has no channel to
  pass a trust value in;
* the group function computes ``trust`` from what the lib function returned;
* :meth:`ToolRegistry.invoke` then clamps it to the tool's declared
  ``max_trust`` (and to its group's ceiling), turns every exception into a
  ``not_verified`` failure, and stamps the tool name and elapsed time.

The LangChain tool returns ``(content, artifact)``: ``content`` is the JSON the
model reads (shortened when large), ``artifact`` is the full
``ToolResult.model_dump(mode="json")`` on the ``ToolMessage``. EvidenceMiddleware
(P3) should read ``ToolMessage.artifact``; a validation failure rejected by
LangChain before the tool runs has no artifact, and its ``content`` is the same
ToolResult JSON (``ok: false``, ``error_kind: "invalid_input"``).

Writing a group module::

    from tfl_deep_agent.tools.base import ToolArgs, ToolResult, Bounds, Word, tfl_tool

    class RunDfaArgs(ToolArgs):
        dfa: dict[str, Any]
        word: Word

    @tfl_tool(group="reg", max_trust="verified", timeout_s=10)
    def run_dfa(args: RunDfaArgs) -> ToolResult:
        \"\"\"Run one word through a DFA (exact).\"\"\"
        accepted = dfa_runner.run_dfa(args.dfa, args.word)
        return ToolResult.success({"accepted": accepted}, trust="verified",
                                  reason="exact DFA run on the given DFA")

The decorator returns the function unchanged (so it stays picklable for
``timeout_mode="process"``); the registered tool runs only through
``REGISTRY.invoke`` / ``REGISTRY.langchain_tools()``.
"""

from __future__ import annotations

import contextvars
import dataclasses
import enum
import importlib
import inspect
import json
import math
import multiprocessing
import re
import threading
import time
import typing
from collections.abc import Callable, Iterable, Mapping
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from agent_system.lib.claim_verifier import CONFIDENCE_CAPS as _POLICY_CONFIDENCE_CAPS

__all__ = [
    "Trust", "TRUST_LEVELS", "CONFIDENCE_CAPS", "trust_rank", "trust_at_least",
    "clamp_trust", "strongest_trust", "confidence_cap",
    "ErrorKind", "ToolError", "ToolInputError", "InputTooLarge", "ToolTimeout",
    "ToolUnsupported", "ToolBusy",
    "Bounds", "ToolResult", "to_jsonable",
    "Limits", "DEFAULT_LIMITS", "json_size", "check_json_size", "check_word", "check_words",
    "check_alphabet", "check_grammar_size", "enumeration_size", "check_enumeration",
    "max_len_within",
    "Symbol", "Word", "WordList", "Alphabet", "ToolArgs", "RESERVED_ARG_NAMES",
    "Deadline", "run_with_timeout", "TimeoutMode",
    "GROUPS", "GROUP_MAX_TRUST", "GROUP_MODULES", "ToolSpec", "ToolRegistry",
    "REGISTRY", "tfl_tool", "load_groups",
]


# ---------------------------------------------------------------------------
# Trust taxonomy (docs/VERDICT_POLICY.md §1-§2)
# ---------------------------------------------------------------------------

Trust = Literal["refuted", "not_verified", "well_formed", "bounded_pass", "verified"]

#: Policy order: refuted < not_verified < well_formed < bounded_pass < verified.
#: ``refuted`` is never the "strongest" evidence (it is checked for, not compared).
TRUST_LEVELS: tuple[str, ...] = get_args(Trust)
_TRUST_RANK: dict[str, int] = {name: i for i, name in enumerate(TRUST_LEVELS)}

#: Confidence ceilings (VERDICT_POLICY §2): verified 0.98, bounded_pass 0.85,
#: well_formed 0.55, not_verified 0.40. The very object of
#: ``agent_system.lib.claim_verifier`` -- reused, not copied.
CONFIDENCE_CAPS: Mapping[str, float] = _POLICY_CONFIDENCE_CAPS


def trust_rank(trust: str | None) -> int:
    """Rank in the policy order; an unknown label or ``None`` ranks as ``not_verified``."""
    return _TRUST_RANK.get(trust or "", _TRUST_RANK["not_verified"])


def trust_at_least(trust: str | None, threshold: str) -> bool:
    """``trust >= threshold`` in the policy order; ``refuted`` never reaches a threshold."""
    if trust == "refuted":
        return False
    return trust_rank(trust) >= _TRUST_RANK[threshold]


def clamp_trust(trust: str, ceiling: str, *, allow_refuted: bool = True) -> str:
    """Lower *trust* to *ceiling* when it ranks above it.

    ``refuted`` is a deterministic counterexample, not a strength, so the
    ceiling leaves it alone -- unless the tool may not refute anything
    (``allow_refuted=False``), in which case it becomes ``not_verified``."""
    if trust not in _TRUST_RANK:
        return "not_verified"
    if trust == "refuted":
        return "refuted" if allow_refuted else "not_verified"
    if ceiling == "refuted":  # a ceiling below every positive level
        return "not_verified"
    return trust if _TRUST_RANK[trust] <= _TRUST_RANK[ceiling] else ceiling


def strongest_trust(trusts: Iterable[str | None]) -> str:
    """The strongest non-refuted label (VERDICT_POLICY §1); ``not_verified`` if none."""
    best = "not_verified"
    for t in trusts:
        if t in _TRUST_RANK and t != "refuted" and _TRUST_RANK[t] > _TRUST_RANK[best]:
            best = t
    return best


def confidence_cap(trust: str | None) -> float:
    """VERDICT_POLICY §2 ceiling for a basis of this trust; ``refuted`` / unknown -> 0.40."""
    return CONFIDENCE_CAPS.get(trust or "", CONFIDENCE_CAPS["not_verified"])


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

ErrorKind = Literal[
    "invalid_input",    # arguments do not validate / are inconsistent
    "input_too_large",  # a size limit (Limits) was exceeded
    "timeout",          # the wall-clock limit was hit
    "unsupported",      # the lib has no oracle / construction for this input
    "busy",             # too many runaway calls still running
    "lib_error",        # the wrapped lib function raised
    "internal",         # a bug in the wrapper itself
]


class ToolError(Exception):
    """Raised by group code; :meth:`ToolRegistry.invoke` turns it into a failed
    ``ToolResult`` with this ``kind`` (trust ``not_verified``)."""

    kind: str = "lib_error"

    def __init__(self, message: str, *, kind: str | None = None, data: Any = None) -> None:
        super().__init__(message)
        if kind is not None:
            self.kind = kind
        self.data = data


class ToolInputError(ToolError):
    kind = "invalid_input"


class InputTooLarge(ToolInputError):
    kind = "input_too_large"


class ToolTimeout(ToolError):
    kind = "timeout"


class ToolUnsupported(ToolError):
    kind = "unsupported"


class ToolBusy(ToolError):
    kind = "busy"


_ERROR_CLASSES: dict[str, type[ToolError]] = {
    "invalid_input": ToolInputError, "input_too_large": InputTooLarge, "timeout": ToolTimeout,
    "unsupported": ToolUnsupported, "busy": ToolBusy,
}


# ---------------------------------------------------------------------------
# JSON conversion
# ---------------------------------------------------------------------------

def _sort_key(x: Any) -> tuple[str, str]:
    return (type(x).__name__, repr(x))


def to_jsonable(obj: Any, _depth: int = 0) -> Any:
    """Convert lib output to plain JSON values: sets/frozensets -> sorted lists,
    tuples -> lists, non-string dict keys -> ``str``, pydantic models and
    dataclasses -> dicts, enums -> values, non-finite floats -> ``None``,
    anything else -> ``str(obj)``."""
    if _depth > 60:
        return "<too deep>"
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, enum.Enum):
        return to_jsonable(obj.value, _depth + 1)
    if isinstance(obj, BaseModel):
        return to_jsonable(obj.model_dump(mode="json"), _depth + 1)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(dataclasses.asdict(obj), _depth + 1)
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key = k if isinstance(k, str) else (
                ",".join(map(str, k)) if isinstance(k, tuple) else str(k))
            out[key] = to_jsonable(v, _depth + 1)
        return out
    if isinstance(obj, (set, frozenset)):
        return [to_jsonable(x, _depth + 1) for x in sorted(obj, key=_sort_key)]
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x, _depth + 1) for x in obj]
    return str(obj)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class Bounds(BaseModel):
    """The actual limits of a bounded check (VERDICT_POLICY §4: the bounds of the
    search are recorded separately from trust). Extra keys are allowed for
    group-specific limits (``max_steps``, ``n_splits``, ...)."""

    model_config = ConfigDict(extra="allow", frozen=True)

    max_len: int | None = Field(default=None, ge=0, description="longest word checked")
    n_words: int | None = Field(default=None, ge=0, description="words with a decisive answer")
    n_unknown: int | None = Field(default=None, ge=0, description="words the oracle could not decide")
    max_k: int | None = Field(default=None, ge=0, description="largest k checked (LL)")
    p_values: list[int] | None = Field(default=None, description="pumping constants instantiated")
    i_values: list[int] | None = Field(default=None, description="pumping exponents tried")
    timeout_s: float | None = Field(default=None, ge=0)
    truncated: bool = Field(default=False, description="a budget cut the search short")


class ToolResult(BaseModel):
    """What every TFL tool returns. Immutable; build it with :meth:`success`
    or :meth:`failure`.

    Invariants (enforced by validation):
    ``ok=False`` -> ``error`` is set and ``trust == "not_verified"``;
    ``ok=True`` -> no ``error``;
    ``trust == "bounded_pass"`` -> ``bounds`` is set;
    ``trust_reason`` is never empty; ``data`` is plain JSON."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = ""
    ok: bool
    data: Any = None
    trust: Trust
    trust_reason: str = Field(min_length=1)
    bounds: Bounds | None = None
    error: str | None = None
    error_kind: ErrorKind | None = None
    elapsed_s: float | None = None

    @field_validator("data", mode="before")
    @classmethod
    def _jsonable(cls, v: Any) -> Any:
        return to_jsonable(v)

    @model_validator(mode="after")
    def _invariants(self) -> ToolResult:
        if self.ok:
            if self.error is not None or self.error_kind is not None:
                raise ValueError("a successful ToolResult carries no error")
        else:
            if not self.error:
                raise ValueError("a failed ToolResult needs an error message")
            if self.trust != "not_verified":
                raise ValueError("a failed tool call is not evidence: trust must be not_verified")
        if self.trust == "bounded_pass" and self.bounds is None:
            raise ValueError("bounded_pass must state its bounds")
        return self

    @classmethod
    def success(
        cls, data: Any = None, *, trust: Trust, reason: str,
        bounds: Bounds | Mapping[str, Any] | None = None, tool: str = "",
    ) -> ToolResult:
        if isinstance(bounds, Mapping):
            bounds = Bounds(**bounds)
        return cls(tool=tool, ok=True, data=data, trust=trust, trust_reason=reason, bounds=bounds)

    @classmethod
    def failure(
        cls, error: str, *, kind: ErrorKind = "lib_error", data: Any = None,
        bounds: Bounds | Mapping[str, Any] | None = None, tool: str = "",
        reason: str | None = None,
    ) -> ToolResult:
        if isinstance(bounds, Mapping):
            bounds = Bounds(**bounds)
        return cls(
            tool=tool, ok=False, data=data, trust="not_verified",
            trust_reason=reason or f"tool call failed ({kind}); not evidence either way",
            bounds=bounds, error=error or kind, error_kind=kind,
        )

    def to_content(self, max_chars: int = 20_000) -> str:
        """The JSON string the model reads. When it is longer than *max_chars*,
        ``data`` is replaced by a preview (the full result stays in the
        ToolMessage artifact / evidence ledger)."""
        payload = self.model_dump(mode="json", exclude_none=True)
        text = _dumps(payload)
        if len(text) <= max_chars:
            return text
        data_text = _dumps(payload.get("data"))
        keep = max(0, max_chars // 2)
        payload["data"] = {
            "truncated": True, "chars": len(data_text), "preview": data_text[:keep],
            "note": "data shortened for the model; the full result is in the evidence ledger",
        }
        return _dumps(payload)


# ---------------------------------------------------------------------------
# Input size limits
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Limits:
    """Input / cost limits shared by all tools. Group code passes explicit
    values when a tool needs tighter ones (e.g. CYK over long words)."""

    max_input_bytes: int = 64 * 1024   # JSON size of one tool call's arguments
    max_word_len: int = 100            # cfl cyk_parse itself refuses > 100 with a 10 s guard
    max_words: int = 500               # words per call (check_membership, sample_words)
    max_alphabet: int = 16
    max_symbol_len: int = 8
    max_grammar_rules: int = 300
    max_rhs_len: int = 24
    max_nonterminals: int = 120
    max_enum_words: int = 20_000       # exhaustive enumeration (sum |Σ|^i, i ≤ L)
    max_k: int = 6                     # LL(k)
    default_timeout_s: float = 30.0
    max_timeout_s: float = 600.0       # check_lean_file's own default is 300 s
    max_content_chars: int = 20_000    # ToolMessage content for the model


DEFAULT_LIMITS = Limits()


def json_size(obj: Any) -> int:
    """UTF-8 byte length of *obj* as compact JSON."""
    return len(_dumps(to_jsonable(obj)).encode("utf-8"))


def check_json_size(obj: Any, *, limit: int | None = None, what: str = "input") -> int:
    limit = DEFAULT_LIMITS.max_input_bytes if limit is None else limit
    size = json_size(obj)
    if size > limit:
        raise InputTooLarge(f"{what} is {size} bytes as JSON, limit {limit}")
    return size


def check_alphabet(alphabet: Iterable[str], *, max_size: int | None = None,
                   what: str = "alphabet") -> list[str]:
    max_size = DEFAULT_LIMITS.max_alphabet if max_size is None else max_size
    symbols = list(alphabet)
    if not symbols:
        raise ToolInputError(f"{what} is empty")
    if len(symbols) > max_size:
        raise InputTooLarge(f"{what} has {len(symbols)} symbols, limit {max_size}")
    for s in symbols:
        if not isinstance(s, str) or not s:
            raise ToolInputError(f"{what} symbols must be non-empty strings, got {s!r}")
    if len(set(symbols)) != len(symbols):
        raise ToolInputError(f"{what} has duplicate symbols")
    return symbols


def check_word(word: str, *, max_len: int | None = None, alphabet: Iterable[str] | None = None,
               what: str = "word") -> str:
    """Length limit, plus (when *alphabet* is given) that the word is a
    concatenation of single-character alphabet symbols -- the convention of
    every oracle in the four libs (words are iterated character by character)."""
    max_len = DEFAULT_LIMITS.max_word_len if max_len is None else max_len
    if not isinstance(word, str):
        raise ToolInputError(f"{what} must be a string")
    if len(word) > max_len:
        raise InputTooLarge(f"{what} has length {len(word)}, limit {max_len}")
    if alphabet is not None:
        allowed = set(alphabet)
        bad = sorted({ch for ch in word if ch not in allowed})
        if bad:
            raise ToolInputError(f"{what} {word!r} uses symbols outside the alphabet: {bad}")
    return word


def check_words(words: Iterable[str], *, max_count: int | None = None, max_len: int | None = None,
                alphabet: Iterable[str] | None = None, what: str = "words") -> list[str]:
    max_count = DEFAULT_LIMITS.max_words if max_count is None else max_count
    out = list(words)
    if len(out) > max_count:
        raise InputTooLarge(f"{len(out)} {what}, limit {max_count}")
    alpha = list(alphabet) if alphabet is not None else None
    for i, w in enumerate(out):
        check_word(w, max_len=max_len, alphabet=alpha, what=f"{what}[{i}]")
    return out


def check_grammar_size(grammar: Mapping[str, Any], *, max_rules: int | None = None,
                       max_rhs_len: int | None = None, max_nonterminals: int | None = None) -> None:
    """Size only (rules, RHS length, nonterminals) of the shared grammar dict
    ``{terminals, nonterminals, start, rules: [{lhs, rhs: [...]}, ...]}``.
    Semantic validation stays with the libs (``cnf._validate``,
    ``ll utils.validate_grammar_symbols``)."""
    L = DEFAULT_LIMITS
    max_rules = L.max_grammar_rules if max_rules is None else max_rules
    max_rhs_len = L.max_rhs_len if max_rhs_len is None else max_rhs_len
    max_nonterminals = L.max_nonterminals if max_nonterminals is None else max_nonterminals
    if not isinstance(grammar, Mapping):
        raise ToolInputError("grammar must be an object")
    rules = grammar.get("rules") or []
    if not isinstance(rules, list):
        raise ToolInputError("grammar.rules must be a list")
    if len(rules) > max_rules:
        raise InputTooLarge(f"grammar has {len(rules)} rules, limit {max_rules}")
    for i, r in enumerate(rules):
        rhs = r.get("rhs", []) if isinstance(r, Mapping) else None
        if not isinstance(rhs, list):
            raise ToolInputError(f"grammar.rules[{i}] must be {{lhs, rhs: [...]}}")
        if len(rhs) > max_rhs_len:
            raise InputTooLarge(f"grammar.rules[{i}].rhs has {len(rhs)} symbols, limit {max_rhs_len}")
    nts = grammar.get("nonterminals") or []
    if isinstance(nts, list) and len(nts) > max_nonterminals:
        raise InputTooLarge(f"grammar has {len(nts)} nonterminals, limit {max_nonterminals}")


def enumeration_size(alphabet_size: int, max_len: int) -> int:
    """Number of words of length 0..max_len over an alphabet of this size."""
    if alphabet_size < 0 or max_len < 0:
        raise ValueError("sizes must be non-negative")
    if alphabet_size <= 1:
        return max_len + 1 if alphabet_size == 1 else 1
    return (alphabet_size ** (max_len + 1) - 1) // (alphabet_size - 1)


def check_enumeration(alphabet_size: int, max_len: int, *, limit: int | None = None) -> int:
    limit = DEFAULT_LIMITS.max_enum_words if limit is None else limit
    n = enumeration_size(alphabet_size, max_len)
    if n > limit:
        raise InputTooLarge(
            f"enumerating all words up to length {max_len} over {alphabet_size} symbols "
            f"is {n} words, limit {limit}")
    return n


def max_len_within(alphabet_size: int, budget: int, cap: int) -> int:
    """Largest L ≤ *cap* with ``enumeration_size(alphabet_size, L) ≤ budget``
    (at least 1). Same rule as dcfl ``oracle_verifier._full_enumeration_max_len``
    (|Σ| = 3, budget 3000 -> 6)."""
    if alphabet_size <= 1:
        return cap
    k = 0
    while k < cap and enumeration_size(alphabet_size, k + 1) <= budget:
        k += 1
    return max(k, 1)


# Annotated field types for args schemas. Limits from DEFAULT_LIMITS; a tool
# that needs other limits declares its own Field(...).
Symbol = Annotated[str, Field(min_length=1, max_length=DEFAULT_LIMITS.max_symbol_len,
                              description="one alphabet symbol (single characters in practice)")]
Word = Annotated[str, Field(max_length=DEFAULT_LIMITS.max_word_len,
                            description="a word as a plain string; the empty string is ε")]
WordList = Annotated[list[Word], Field(max_length=DEFAULT_LIMITS.max_words)]
Alphabet = Annotated[list[Symbol], Field(min_length=1, max_length=DEFAULT_LIMITS.max_alphabet)]


#: Field names an args schema may not declare anywhere (nested models included):
#: trust is the wrapper's output, and the rest collide with LangChain's own
#: injected tool parameters.
RESERVED_ARG_NAMES = frozenset({
    "trust", "trust_reason", "trust_level", "confidence", "verified", "evidence_trust",
    "config", "run_manager", "callbacks", "tool_call_id",
})


class ToolArgs(BaseModel):
    """Base for every tool's args schema: unknown keys are rejected, so a model
    cannot smuggle ``trust`` (or anything else) into a call."""

    model_config = ConfigDict(extra="forbid")


def _schema_field_names(schema: Any) -> set[str]:
    """All property names anywhere in a JSON schema (incl. $defs)."""
    names: set[str] = set()
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                names.update(props)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return names


# ---------------------------------------------------------------------------
# Wall-clock timeouts
# ---------------------------------------------------------------------------

class Deadline:
    """A cooperative wall-clock budget for loops in group code::

        dl = Deadline(5.0)
        for w in words:
            dl.check("membership")   # raises ToolTimeout once 5 s are gone
            ...

    ``Deadline(None)`` never expires."""

    def __init__(self, timeout_s: float | None) -> None:
        self.timeout_s = timeout_s
        self._start = time.monotonic()
        self._end = math.inf if timeout_s is None else self._start + max(0.0, timeout_s)

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def remaining(self) -> float:
        return max(0.0, self._end - time.monotonic())

    def expired(self) -> bool:
        return time.monotonic() >= self._end

    def check(self, what: str = "tool") -> None:
        if self.expired():
            raise ToolTimeout(f"{what}: wall-clock limit of {self.timeout_s} s exceeded")


TimeoutMode = Literal["thread", "process", "none"]

#: A timed-out thread cannot be killed; it keeps running in the background.
#: Once this many are still running, new thread-mode calls are refused (busy)
#: instead of piling up more CPU work.
MAX_ABANDONED_THREADS = 8
_abandoned_lock = threading.Lock()
_abandoned_threads = 0


def abandoned_thread_count() -> int:
    with _abandoned_lock:
        return _abandoned_threads


def _run_in_thread(fn: Callable[..., Any], args: tuple, kwargs: dict, timeout_s: float) -> Any:
    global _abandoned_threads
    with _abandoned_lock:
        if _abandoned_threads >= MAX_ABANDONED_THREADS:
            raise ToolBusy(
                f"{_abandoned_threads} earlier tool calls timed out and are still running; "
                "refusing new work until they finish")
    box: dict[str, Any] = {}
    state = {"abandoned": False}
    state_lock = threading.Lock()
    ctx = contextvars.copy_context()

    def target() -> None:
        global _abandoned_threads
        try:
            box["value"] = ctx.run(fn, *args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc
        finally:
            with state_lock:
                abandoned = state["abandoned"]
                state["done"] = True
            if abandoned:
                with _abandoned_lock:
                    _abandoned_threads -= 1

    t = threading.Thread(target=target, name=f"tfl-tool-{getattr(fn, '__name__', 'fn')}",
                         daemon=True)
    t.start()
    t.join(timeout_s)
    with state_lock:
        if not state.get("done"):
            state["abandoned"] = True
            with _abandoned_lock:
                _abandoned_threads += 1
            raise ToolTimeout(f"wall-clock limit of {timeout_s} s exceeded")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _process_entry(conn: Any, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
    """Child side of process mode (module level, so spawn can import it)."""
    try:
        value = fn(*args, **kwargs)
        msg: tuple = ("ok", value)
    except ToolError as exc:
        msg = ("err", exc.kind, str(exc))
    except BaseException as exc:  # noqa: BLE001
        msg = ("err", "lib_error", f"{type(exc).__name__}: {exc}")
    try:
        conn.send(msg)
    except Exception as exc:  # unpicklable value
        conn.send(("err", "internal", f"result could not be sent back: {type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def _run_in_process(fn: Callable[..., Any], args: tuple, kwargs: dict, timeout_s: float) -> Any:
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_process_entry, args=(child, fn, args, kwargs), daemon=True,
                       name=f"tfl-tool-{getattr(fn, '__name__', 'fn')}")
    proc.start()
    child.close()
    try:
        if not parent.poll(timeout_s):
            raise ToolTimeout(f"wall-clock limit of {timeout_s} s exceeded (worker process killed)")
        try:
            msg = parent.recv()
        except EOFError:
            proc.join(5)
            raise ToolError(f"worker process exited with code {proc.exitcode} without a result",
                            kind="lib_error") from None
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        parent.close()
    if msg[0] == "ok":
        return msg[1]
    _, kind, text = msg
    raise _ERROR_CLASSES.get(kind, ToolError)(text, kind=kind)


def run_with_timeout(fn: Callable[..., Any], /, *args: Any, timeout_s: float | None,
                     mode: TimeoutMode = "thread", **kwargs: Any) -> Any:
    """Call ``fn(*args, **kwargs)`` under a wall-clock limit; raise
    :class:`ToolTimeout` when it is exceeded.

    ``mode="thread"`` (default): the call runs in a daemon thread. Cheap, but a
    timed-out thread cannot be killed: it runs on in the background (counted;
    see :data:`MAX_ABANDONED_THREADS`). Fine for lib functions with their own
    step budgets (cyk, pda_simulator, dpda_run, check_ll_k's time_budget_s).

    ``mode="process"``: the call runs in a fresh ``spawn`` process that is
    killed on timeout. *fn* and its arguments must be picklable (a module-level
    function), and the limit includes the child's start-up and imports
    (about a second on Windows). Use it for searches with no budget of their own.

    ``mode="none"`` or ``timeout_s=None``: a direct call."""
    if timeout_s is None or mode == "none":
        return fn(*args, **kwargs)
    if timeout_s <= 0:
        raise ToolTimeout("no time left for this call")
    if mode == "thread":
        return _run_in_thread(fn, args, kwargs, timeout_s)
    if mode == "process":
        return _run_in_process(fn, args, kwargs, timeout_s)
    raise ValueError(f"unknown timeout mode {mode!r}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: Tool groups (architecture.html, "Инструменты агента").
GROUPS: tuple[str, ...] = (
    "ir", "words", "reg", "cfl", "dcfl", "ll", "theory", "lean", "orchestration", "report",
)

#: The highest trust any tool of a group may ever report (a registration-time
#: guard; each tool declares its own, usually lower, ``max_trust``):
#: IR tools only check structure; word sampling is bounded by construction;
#: class oracles can be complete for a *given* artifact (a DFA run, a CYK
#: parse, an LL(k) test, DPDA determinism); theory, orchestration and report
#: tools produce no evidence at all.
GROUP_MAX_TRUST: Mapping[str, str] = {
    "ir": "well_formed",
    "words": "bounded_pass",
    "reg": "verified",
    "cfl": "verified",
    "dcfl": "verified",
    "ll": "verified",
    "theory": "not_verified",
    "lean": "verified",
    "orchestration": "not_verified",
    "report": "not_verified",
}

#: Modules that register each tool group in tools/ (imported by load_groups).
GROUP_MODULES: Mapping[str, str] = {
    "ir": "tfl_deep_agent.tools.ir",
    "words": "tfl_deep_agent.tools.words",
    "reg": "tfl_deep_agent.tools.reg",
    "cfl": "tfl_deep_agent.tools.cfl",
    "dcfl": "tfl_deep_agent.tools.dcfl",
    "ll": "tfl_deep_agent.tools.ll",
    "theory": "tfl_deep_agent.tools.theory",
    "lean": "tfl_deep_agent.tools.lean",
    "report": "tfl_deep_agent.tools.report",
}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclasses.dataclass(frozen=True)
class ToolSpec:
    name: str
    group: str
    description: str
    args_schema: type[ToolArgs]
    func: Callable[[Any], ToolResult]
    max_trust: str
    timeout_s: float | None
    timeout_mode: TimeoutMode = "thread"
    allow_refuted: bool = True
    max_input_bytes: int = DEFAULT_LIMITS.max_input_bytes

    @property
    def ceiling(self) -> str:
        """The effective ceiling: the tool's own, never above its group's."""
        return clamp_trust(self.max_trust, GROUP_MAX_TRUST[self.group])


def _validation_message(exc: ValidationError, limit: int = 1500) -> str:
    parts = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(x) for x in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg')}")
    text = "; ".join(parts) or str(exc)
    return text if len(text) <= limit else text[: limit - 3] + "..."


class ToolRegistry:
    """Named TFL tools, each run through one execution path (:meth:`invoke`)."""

    def __init__(self, limits: Limits = DEFAULT_LIMITS) -> None:
        self.limits = limits
        self._specs: dict[str, ToolSpec] = {}
        self._lc_cache: dict[str, Any] = {}
        self._lock = threading.RLock()

    # -- registration -------------------------------------------------------

    def register(self, spec: ToolSpec) -> ToolSpec:
        if not _NAME_RE.match(spec.name):
            raise ValueError(f"tool name {spec.name!r} must match {_NAME_RE.pattern}")
        if spec.group not in GROUPS:
            raise ValueError(f"tool {spec.name!r}: unknown group {spec.group!r}; one of {GROUPS}")
        if spec.max_trust not in _TRUST_RANK or spec.max_trust == "refuted":
            raise ValueError(f"tool {spec.name!r}: max_trust must be a positive trust level")
        group_cap = GROUP_MAX_TRUST[spec.group]
        if _TRUST_RANK[spec.max_trust] > _TRUST_RANK[group_cap]:
            raise ValueError(
                f"tool {spec.name!r}: max_trust {spec.max_trust!r} is above the "
                f"{spec.group!r} group ceiling {group_cap!r}")
        if not (isinstance(spec.args_schema, type) and issubclass(spec.args_schema, ToolArgs)):
            raise TypeError(f"tool {spec.name!r}: args_schema must subclass ToolArgs")
        clash = _schema_field_names(spec.args_schema.model_json_schema()) & RESERVED_ARG_NAMES
        if clash:
            raise ValueError(
                f"tool {spec.name!r}: args schema declares reserved field(s) {sorted(clash)}; "
                "trust is computed by the wrapper, never taken from model input")
        if not spec.description.strip():
            raise ValueError(f"tool {spec.name!r}: description is empty")
        if spec.timeout_s is not None and not (0 < spec.timeout_s <= self.limits.max_timeout_s):
            raise ValueError(f"tool {spec.name!r}: timeout_s must be in (0, {self.limits.max_timeout_s}]")
        if spec.timeout_mode not in get_args(TimeoutMode):
            raise ValueError(f"tool {spec.name!r}: unknown timeout_mode {spec.timeout_mode!r}")
        with self._lock:
            old = self._specs.get(spec.name)
            if old is not None and not (
                old.func.__module__ == spec.func.__module__
                and old.func.__qualname__ == spec.func.__qualname__
            ):
                raise ValueError(
                    f"tool {spec.name!r} is already registered by "
                    f"{old.func.__module__}.{old.func.__qualname__}")
            # Same function re-registered (importlib.reload): replace it.
            self._specs[spec.name] = spec
            self._lc_cache.pop(spec.name, None)
        return spec

    def tool(
        self, *, group: str, max_trust: str, name: str | None = None,
        description: str | None = None, args_schema: type[ToolArgs] | None = None,
        timeout_s: float | None = DEFAULT_LIMITS.default_timeout_s,
        timeout_mode: TimeoutMode = "thread", allow_refuted: bool = True,
        max_input_bytes: int | None = None,
    ) -> Callable[[Callable[[Any], ToolResult]], Callable[[Any], ToolResult]]:
        """Decorator: register ``func(args: <ToolArgs subclass>) -> ToolResult``.

        *name* defaults to the function name, *description* to its docstring,
        *args_schema* to the annotation of its single parameter. The function
        itself is returned unchanged."""

        def deco(func: Callable[[Any], ToolResult]) -> Callable[[Any], ToolResult]:
            schema = args_schema
            if schema is None:
                params = list(inspect.signature(func).parameters)
                if len(params) != 1:
                    raise TypeError(f"{func.__qualname__}: a tool function takes exactly one "
                                    "argument (its ToolArgs model)")
                hints = typing.get_type_hints(func)
                schema = hints.get(params[0])
            desc = description if description is not None else inspect.getdoc(func) or ""
            self.register(ToolSpec(
                name=name or func.__name__, group=group, description=desc,
                args_schema=schema, func=func, max_trust=max_trust, timeout_s=timeout_s,
                timeout_mode=timeout_mode, allow_refuted=allow_refuted,
                max_input_bytes=max_input_bytes or self.limits.max_input_bytes,
            ))
            return func

        return deco

    # -- lookup -------------------------------------------------------------

    def get(self, name: str) -> ToolSpec:
        with self._lock:
            try:
                return self._specs[name]
            except KeyError:
                raise KeyError(f"unknown tool {name!r}") from None

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return name in self._specs

    def __len__(self) -> int:
        with self._lock:
            return len(self._specs)

    def specs(self, *, groups: Iterable[str] | None = None,
              names: Iterable[str] | None = None) -> list[ToolSpec]:
        with self._lock:
            specs = list(self._specs.values())
        if groups is not None:
            gs = set(groups)
            specs = [s for s in specs if s.group in gs]
        if names is not None:
            wanted = list(names)
            missing = [n for n in wanted if n not in {s.name for s in specs}]
            if missing:
                raise KeyError(f"unknown tool(s) {missing}")
            by_name = {s.name: s for s in specs}
            specs = [by_name[n] for n in wanted]
        return specs

    def names(self, group: str | None = None) -> list[str]:
        return [s.name for s in self.specs(groups=None if group is None else [group])]

    # -- execution ----------------------------------------------------------

    def invoke(self, name: str, args: Mapping[str, Any] | ToolArgs) -> ToolResult:
        """Validate *args*, run the tool under its timeout, and return a
        ToolResult whose trust is clamped to the tool's ceiling. Never raises
        for a tool-level problem: every error becomes ``ok=False``."""
        t0 = time.monotonic()
        try:
            spec = self.get(name)
        except KeyError as exc:
            return ToolResult.failure(str(exc.args[0]), kind="invalid_input", tool=name)

        def done(result: ToolResult) -> ToolResult:
            return result.model_copy(update={"tool": spec.name,
                                             "elapsed_s": round(time.monotonic() - t0, 4)})

        try:
            if isinstance(args, spec.args_schema):
                parsed = args
            elif isinstance(args, Mapping):
                plain = to_jsonable(args)
                check_json_size(plain, limit=spec.max_input_bytes, what=f"{name} arguments")
                parsed = spec.args_schema.model_validate(plain)
            else:
                raise ToolInputError(
                    f"arguments must be an object or {spec.args_schema.__name__}, "
                    f"got {type(args).__name__}")
        except ValidationError as exc:
            return done(ToolResult.failure(_validation_message(exc), kind="invalid_input"))
        except ToolError as exc:
            return done(ToolResult.failure(str(exc), kind=exc.kind, data=exc.data))

        try:
            raw = run_with_timeout(spec.func, parsed, timeout_s=spec.timeout_s,
                                   mode=spec.timeout_mode)
        except ToolError as exc:
            bounds = Bounds(timeout_s=spec.timeout_s) if exc.kind == "timeout" else None
            return done(ToolResult.failure(str(exc), kind=exc.kind, data=exc.data, bounds=bounds))
        except ValidationError as exc:  # the group built an invalid ToolResult
            return done(ToolResult.failure(f"tool built an invalid result: {_validation_message(exc)}",
                                           kind="internal"))
        except Exception as exc:  # noqa: BLE001 - every lib error becomes a failure
            text = f"{type(exc).__name__}: {exc}"
            return done(ToolResult.failure(text[:2000], kind="lib_error"))

        if not isinstance(raw, ToolResult):
            return done(ToolResult.failure(
                f"tool returned {type(raw).__name__}, not ToolResult", kind="internal"))
        return done(self._apply_ceiling(spec, raw))

    @staticmethod
    def _apply_ceiling(spec: ToolSpec, result: ToolResult) -> ToolResult:
        if not result.ok:
            return result
        new = clamp_trust(result.trust, spec.ceiling, allow_refuted=spec.allow_refuted)
        if new == "bounded_pass" and result.bounds is None:
            # Lowered from verified to bounded_pass, but no bounds were reported:
            # a bounded claim without its bounds is only well_formed.
            new = "well_formed"
        if new == result.trust:
            return result
        if result.trust == "refuted":
            note = f"{spec.name} may not refute claims; a counterexample from it is not evidence"
        else:
            note = f"lowered from {result.trust} to the {spec.name} ceiling {new}"
        return result.model_copy(update={"trust": new,
                                         "trust_reason": f"{result.trust_reason} [{note}]"})

    # -- LangChain ----------------------------------------------------------

    def as_langchain_tool(self, name: str) -> Any:
        """A ``StructuredTool`` that runs through :meth:`invoke` and returns
        ``(content, artifact)``: content is the JSON the model reads, artifact
        the full ToolResult dump (on ``ToolMessage.artifact``)."""
        from langchain_core.tools import StructuredTool

        with self._lock:
            cached = self._lc_cache.get(name)
        if cached is not None:
            return cached
        spec = self.get(name)
        max_chars = self.limits.max_content_chars

        def _run(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            result = self.invoke(spec.name, kwargs)
            return result.to_content(max_chars), result.model_dump(mode="json")

        def _on_validation_error(exc: Exception) -> str:
            msg = _validation_message(exc) if isinstance(exc, ValidationError) else str(exc)
            return ToolResult.failure(msg, kind="invalid_input", tool=spec.name).to_content(max_chars)

        _run.__name__ = spec.name
        tool = StructuredTool.from_function(
            func=_run, name=spec.name, description=spec.description,
            args_schema=spec.args_schema, infer_schema=False,
            response_format="content_and_artifact",
            handle_validation_error=_on_validation_error,
            metadata={"tfl_group": spec.group, "tfl_max_trust": spec.ceiling},
        )
        with self._lock:
            self._lc_cache[name] = tool
        return tool

    def langchain_tools(self, *, groups: Iterable[str] | None = None,
                        names: Iterable[str] | None = None) -> list[Any]:
        return [self.as_langchain_tool(s.name) for s in self.specs(groups=groups, names=names)]


#: The process-wide registry the group modules register into.
REGISTRY = ToolRegistry()
tfl_tool = REGISTRY.tool


def load_groups(groups: Iterable[str] | None = None) -> ToolRegistry:
    """Import the group modules (each registers its tools into :data:`REGISTRY`
    at import time) and return the registry."""
    for g in (GROUP_MODULES if groups is None else groups):
        if g not in GROUP_MODULES:
            raise KeyError(f"no tool module for group {g!r}; known: {sorted(GROUP_MODULES)}")
        importlib.import_module(GROUP_MODULES[g])
    return REGISTRY
