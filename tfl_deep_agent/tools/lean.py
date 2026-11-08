"""Lean 4 tools of the ``lean`` group (P6): ``lean_statement`` and ``lean_check``.

docs/VERDICT_POLICY.md R-Lean: the Lean file is always composed by code. The
formulation (alphabet type, language definition, theorem statement) is
generated deterministically from the IR; the model writes only the proof body.
This module does no Lean work of its own, it wires the existing pieces:

* ``lean_statement(ir, direction)`` renders the statement with the same
  renderer the pipelines' formalization uses (``formalize.py`` ->
  ``lib.lean_ir.render_statement_verbose``): ``agent_system.lib.lean_ir`` for
  ``regular`` / ``non_regular``, ``cfl_system.lib.lean_ir`` for ``cfl`` /
  ``non_cfl``, ``dcfl_system.lib.lean_ir`` for ``dcfl`` / ``non_dcfl``. LL(k) is
  not formalized (R-Lean). The rendered ``LeanStatement`` is kept in a
  process-local cache under a ``statement_id`` (a hash of the statement text and
  the direction), so the model can refer to it but never send one in. Two IRs
  that differ only outside the language (for example ``source_text``) render
  the same statement; the cache holds only the statement, never "the" IR.
* ``lean_check(statement_id, proof_body)`` looks the statement up (or re-renders
  it from ``ir`` + ``direction`` after a restart, and then the id must match),
  runs the lexical guard (``type_check.scan_proof_body`` plus the checks below),
  composes the file with ``type_check.compose_lean_file`` (independent kernel
  replay + ``#print axioms tfl_main`` appended) and type-checks it with
  ``type_check.check_lean_file`` in the ``tfl-lean4`` Docker image.

Trust (computed here, never taken from the model):

* ``lean_check``: ``verified`` only for status ``proved`` whose axioms are a
  subset of ``type_check.ALLOWED_AXIOMS`` (``compute_lean_proof_trust`` gives
  the R-Lean mapping; the axiom subset is re-checked here as a second line).
  ``has_sorry`` / ``error`` / ``timeout`` are ``not_verified`` (R1: not evidence
  either way). A rejected body and an unavailable checker are failures.
* ``lean_statement``: ``not_verified`` with ``data.evidence_role =
  "reference"``. A rendered statement checks no claim about the language.
  This deviates from TOOL_INVENTORY §8 (``well_formed``); the reason is the
  same as for ``sample_words`` (P1 fix round, finding F8). See DEV_NOTES P6.

Per run (not per process): the attempt counter of each statement and the IR
it was last rendered from in this run live in a run scope keyed by the
LangGraph ``thread_id`` of the call (``hitl`` uses the run id), or by an
explicit :func:`lean_run` block; outside both, one ``"process"`` scope. So two
runs in one process (TFL Lab, a ``tfl-eval`` batch) never share attempts or a
fingerprint.

What a proved result says: "the language of this IR is ``direction``"
(``data.cls`` / ``data.side`` / ``data.claim``), for the IR whose fingerprint
is ``data.ir_fingerprint``: the ``ir`` passed to this call, else the IR this
run rendered the statement from (``data.ir_fingerprint_source`` says which).
"""

from __future__ import annotations

import collections
import contextlib
import contextvars
import dataclasses
import hashlib
import importlib
import json
import re
import threading
import time
from collections.abc import Iterator
from typing import Any, Literal

from pydantic import Field

from agent_system.lib import type_check
from agent_system.lib.claim_verifier import compute_lean_proof_trust
from tfl_deep_agent import config
from tfl_deep_agent.tools.base import (
    Deadline,
    ToolArgs,
    ToolBusy,
    ToolInputError,
    ToolResult,
    ToolUnsupported,
    tfl_tool,
)
from tfl_deep_agent.tools.ir import ir_fingerprint

__all__ = [
    "DIRECTIONS", "Direction", "LeanStatementArgs", "LeanCheckArgs",
    "lean_statement", "lean_check", "render_for", "statement_id_of", "statement_text",
    "guard_proof_body", "compose", "reset_store", "lean_run", "run_key", "attempts_used",
    "MAX_CHECKS_PER_STATEMENT", "MAX_PARALLEL_CHECKS", "MAX_PROOF_BODY_CHARS",
]

# ---------------------------------------------------------------------------
# Directions and renderers
# ---------------------------------------------------------------------------

#: direction -> (task class, side of the verdict, renderer module, claim text).
_DIRECTION_INFO: dict[str, tuple[str, str, str, str]] = {
    "regular": ("reg", "constructive", "agent_system.lib.lean_ir", "the language is regular"),
    "non_regular": ("reg", "destructive", "agent_system.lib.lean_ir", "the language is not regular"),
    "cfl": ("cfl", "constructive", "cfl_system.lib.lean_ir", "the language is context-free"),
    "non_cfl": ("cfl", "destructive", "cfl_system.lib.lean_ir", "the language is not context-free"),
    "dcfl": ("dcfl", "constructive", "dcfl_system.lib.lean_ir",
             "the language is deterministic context-free"),
    "non_dcfl": ("dcfl", "destructive", "dcfl_system.lib.lean_ir",
                 "the language is not deterministic context-free"),
}
DIRECTIONS: tuple[str, ...] = tuple(_DIRECTION_INFO)
Direction = Literal["regular", "non_regular", "cfl", "non_cfl", "dcfl", "non_dcfl", "ll"]

DIRECTION_DESCRIPTION = (
    "What the theorem states about the task language: 'regular' / 'non_regular' (REG), "
    "'cfl' / 'non_cfl' (CFL), 'dcfl' / 'non_dcfl' (DCFL). 'll' is accepted and refused: "
    "LL(k) is a property of a grammar's parser and is not formalized.")
IR_DESCRIPTION = (
    "The task IR (JSON object) the statement is generated from: the same IR the other "
    "tools use. At most 64 KiB as JSON.")

#: Limits (decisions, DEV_NOTES P6).
MAX_PROOF_BODY_CHARS = 40_000
MAX_CHECKS_PER_STATEMENT = 6
MAX_PARALLEL_CHECKS = 2
STORE_SIZE = 128
MAX_RUN_SCOPES = 256
DAEMON_RETRIES = 2
DAEMON_RETRY_SLEEP_S = 2.0
TOOL_TIMEOUT_S = 600.0
LEAN_TIMEOUT_S = int(config.LEAN_TIMEOUT)
_MAX_ERRORS_SHOWN = 10
_MAX_ERROR_CHARS = 2000

PROOF_BODY_RULES: tuple[str, ...] = (
    "Write only the tactic block: no leading `by` (the statement already ends with `:= by`).",
    "The first line gets its indentation from the statement; indent every further line by at "
    "least two spaces. A line at column 0 would end the `by` block and is rejected.",
    "No `sorry`, `admit`, `sorryAx`, `native_decide`, `ofReduceBool`, no new `axiom` or other "
    "declaration (`theorem`, `lemma`, `def`, `instance`, `import`, ...), no reference to "
    "`tfl_main` itself.",
    "No metaprogramming (`run_tac`, `run_cmd`, `run_meta`, `elab`, `macro`, `syntax`, "
    "`set_option`, `unsafe`, `Lean.*`), no `#`-commands, no tab characters.",
    "Axioms allowed in the result: " + ", ".join(sorted(type_check.ALLOWED_AXIOMS)) + ".",
)


def render_for(ir: Any, direction: str) -> tuple[Any, str | None]:
    """``(LeanStatement, None)`` or ``(None, reason)`` from the pipeline
    renderer for *direction*. Never raises."""
    if direction == "ll":
        from agent_system.lib.lean_ir import render_statement_verbose
        return render_statement_verbose(ir if isinstance(ir, dict) else {}, "ll")
    info = _DIRECTION_INFO.get(direction)
    if info is None:
        return None, f"unknown direction {direction!r}; one of {list(DIRECTIONS)}"
    try:
        mod = importlib.import_module(info[2])
        return mod.render_statement_verbose(ir, direction)
    except Exception as exc:  # noqa: BLE001 - the renderers never raise; defensive
        return None, f"renderer {info[2]} raised: {type(exc).__name__}: {exc}"


def _snapshot(stmt: Any) -> dict[str, Any]:
    return {
        "imports": list(getattr(stmt, "imports", None) or []),
        "alphabet_decl": str(getattr(stmt, "alphabet_decl", "") or ""),
        "language_decl": str(getattr(stmt, "language_decl", "") or ""),
        "theorem_decl": str(getattr(stmt, "theorem_decl", "") or ""),
        "name": str(getattr(stmt, "name", "") or "tfl_main"),
    }


def statement_text(stmt: Any) -> str:
    """The statement as a report shows it: imports, declarations and the bare
    ``theorem tfl_main : ...`` (no ``:= by``), as in the goldens' result.md."""
    s = _snapshot(stmt)
    parts = ["\n".join(s["imports"]), s["alphabet_decl"], s["language_decl"], s["theorem_decl"]]
    return "\n\n".join(p for p in parts if p.strip()) + "\n"


def statement_id_of(stmt: Any, direction: str) -> str:
    """Deterministic id of a rendered statement: the same IR and direction give
    the same id in any process (so ``lean_check`` can re-render after a resume)."""
    payload = json.dumps({"direction": direction, "statement": _snapshot(stmt)},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "lean-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Statement cache (process-local; ids are deterministic, see statement_id_of)
# and per-run scopes (attempt counters, the IR of this run)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _Stored:
    """A rendered statement. ``ir_fingerprint`` is the IR that rendered it
    most recently in any run: only the last resort of ``lean_check``."""

    statement: Any
    direction: str
    statement_id: str
    ir_fingerprint: str


@dataclasses.dataclass
class _RunUse:
    """One run's use of one statement."""

    checks: int = 0
    ir_fingerprint: str | None = None


_STORE: collections.OrderedDict[str, _Stored] = collections.OrderedDict()
_RUNS: collections.OrderedDict[str, dict[str, _RunUse]] = collections.OrderedDict()
_STORE_LOCK = threading.Lock()
_CHECK_SEMAPHORE = threading.BoundedSemaphore(MAX_PARALLEL_CHECKS)
_RUN_SCOPE: contextvars.ContextVar[str | None] = contextvars.ContextVar("tfl_lean_run", default=None)
PROCESS_SCOPE = "process"


@contextlib.contextmanager
def lean_run(key: str) -> Iterator[str]:
    """Calls inside this block count attempts in the run scope ``key``
    (it takes precedence over the LangGraph ``thread_id``)."""
    token = _RUN_SCOPE.set(f"run:{key}")
    try:
        yield f"run:{key}"
    finally:
        _RUN_SCOPE.reset(token)


def run_key() -> str:
    """The run scope of the current call: a :func:`lean_run` block, else the
    ``thread_id`` of the LangGraph config the tool runs under (inherited by
    subagents and workflow steps), else :data:`PROCESS_SCOPE`."""
    explicit = _RUN_SCOPE.get()
    if explicit:
        return explicit
    try:
        from langgraph.config import get_config

        cfg = get_config()
    except Exception:  # noqa: BLE001 - outside a runnable, or langgraph absent
        cfg = None
    conf = cfg.get("configurable") if isinstance(cfg, dict) else None
    tid = conf.get("thread_id") if isinstance(conf, dict) else None
    return f"thread:{tid}" if tid not in (None, "") else PROCESS_SCOPE


def _use(statement_id: str, key: str | None = None) -> _RunUse:
    """This run's record of ``statement_id`` (created; call with the lock held)."""
    key = key or run_key()
    scope = _RUNS.get(key)
    if scope is None:
        scope = _RUNS[key] = {}
        while len(_RUNS) > MAX_RUN_SCOPES:
            _RUNS.popitem(last=False)
    else:
        _RUNS.move_to_end(key)
    return scope.setdefault(statement_id, _RunUse())


def _remember(entry: _Stored) -> _Stored:
    with _STORE_LOCK:
        old = _STORE.get(entry.statement_id)
        if old is not None:
            _STORE.move_to_end(entry.statement_id)
            old.ir_fingerprint = entry.ir_fingerprint
            return old
        _STORE[entry.statement_id] = entry
        while len(_STORE) > STORE_SIZE:
            _STORE.popitem(last=False)
        return entry


def reset_store() -> None:
    """Forget every rendered statement and every run's attempt counts (tests)."""
    with _STORE_LOCK:
        _STORE.clear()
        _RUNS.clear()


def attempts_used(statement_id: str, key: str | None = None) -> int:
    """Checks of ``statement_id`` counted in the run scope ``key`` (default: the current one)."""
    with _STORE_LOCK:
        use = (_RUNS.get(key or run_key()) or {}).get(statement_id)
        return use.checks if use is not None else 0


def _render_or_raise(ir: Any, direction: str) -> _Stored:
    if direction == "ll":
        _stmt, reason = render_for(ir, "ll")
        raise ToolUnsupported(f"LL(k) is not formalized in Lean: {reason}")
    stmt, reason = render_for(ir, direction)
    if stmt is None:
        mod = _DIRECTION_INFO[direction][2]
        raise ToolUnsupported(
            f"no Lean statement for this IR and direction {direction!r}: "
            f"{reason or 'the renderer returned nothing'} ({mod}.render_statement_verbose)",
            data={"direction": direction, "renderer": mod,
                  "ir_fingerprint": ir_fingerprint(ir)})
    return _Stored(statement=stmt, direction=direction,
                   statement_id=statement_id_of(stmt, direction),
                   ir_fingerprint=ir_fingerprint(ir))


# ---------------------------------------------------------------------------
# Proof-body guard
# ---------------------------------------------------------------------------

_EXTRA_FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.MULTILINE))
    for label, pattern in (
        ("sorry", r"\bsorry\b"),
        ("sorryAx", r"\bsorryAx\b"),
        ("admit", r"\badmit\b"),
        ("ofReduceBool", r"\bofReduceBool\b"),
        ("axiom declaration", r"\baxiom\b"),
        ("reference to the theorem itself (tfl_main)", r"\btfl_main\b"),
        ("declaration keyword at the start of a line (statement tampering)",
         r"^[ \t]*(?:@\[[^\]]*\][ \t]*)?(?:import|theorem|lemma|def|abbrev|instance|inductive|"
         r"structure|class|example|opaque|namespace|section|mutual|universe|variable|"
         r"attribute|noncomputable|private|protected)\b"),
    )
)


def _strip_comments(text: str) -> str:
    strip = getattr(type_check, "_strip_lean_comments", None)
    return strip(text) if callable(strip) else text


def guard_proof_body(proof_body: Any) -> list[str]:
    """Why *proof_body* may not be compiled (empty list = it may).

    ``type_check.scan_proof_body`` (metaprogramming, ``native_decide``,
    ``#``-commands, a line at column 0) plus: ``sorry`` / ``admit`` /
    ``sorryAx`` / ``ofReduceBool``, a new ``axiom`` or other declaration
    (statement tampering), a reference to ``tfl_main``, a leading ``by``, tab
    characters, an empty or oversized body. Comments are ignored, as in
    ``scan_proof_body``."""
    if not isinstance(proof_body, str):
        return ["proof body is not a string"]
    out = list(type_check.scan_proof_body(proof_body))
    if not proof_body.strip():
        out.append("empty proof body")
    if len(proof_body) > MAX_PROOF_BODY_CHARS:
        out.append(f"proof body longer than {MAX_PROOF_BODY_CHARS} characters")
    code = _strip_comments(proof_body)
    out += [label for label, pat in _EXTRA_FORBIDDEN if pat.search(code)]
    if "\t" in proof_body:
        out.append("tab character (Lean rejects tabs)")
    if re.match(r"\s*by\b", code):
        out.append("leading `by` (the statement already ends with `:= by`)")
    return list(dict.fromkeys(out))


def compose(statement: Any, proof_body: str) -> str:
    """The Lean file for *statement* and *proof_body*:
    ``type_check.compose_lean_file`` (independent kernel replay and
    ``#print axioms tfl_main`` appended). CRLF line ends become LF."""
    return type_check.compose_lean_file(statement, proof_body.replace("\r\n", "\n"))


# ---------------------------------------------------------------------------
# Lean runner (tests replace _run_lean)
# ---------------------------------------------------------------------------

_DAEMON_ERROR_RE = re.compile(
    r"OCI runtime|Error response from daemon|Cannot connect to the Docker daemon|"
    r"error during connect|docker daemon|read init-p|failed to create (?:shim|task)",
    re.IGNORECASE)


def _run_lean(text: str, timeout: int, theorem_name: str) -> dict[str, Any]:
    """One ``type_check.check_lean_file`` run (tfl-lean4 image)."""
    return type_check.check_lean_file(text, timeout=timeout, theorem_name=theorem_name)


def _is_daemon_failure(res: dict[str, Any]) -> bool:
    """A ``docker run`` that failed before Lean started (see DEV_NOTES, P0
    Docker: flaky ``OCI runtime create failed``): status ``error`` whose only
    error has no source position and carries a Docker daemon message."""
    if res.get("status") != "error":
        return False
    errors = res.get("errors") or []
    if len(errors) != 1 or not isinstance(errors[0], dict) or errors[0].get("pos"):
        return False
    return bool(_DAEMON_ERROR_RE.search(str(errors[0].get("data") or "")))


def _trim_messages(msgs: Any) -> list[dict[str, Any]]:
    out = []
    for m in list(msgs or [])[:_MAX_ERRORS_SHOWN]:
        if not isinstance(m, dict):
            m = {"data": str(m)}
        item = {k: m[k] for k in ("severity", "pos", "endPos") if k in m}
        data = str(m.get("data") or "")
        item["data"] = data if len(data) <= _MAX_ERROR_CHARS else data[: _MAX_ERROR_CHARS - 3] + "..."
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class LeanStatementArgs(ToolArgs):
    ir: dict[str, Any] = Field(description=IR_DESCRIPTION)
    direction: Direction = Field(description=DIRECTION_DESCRIPTION)


class LeanCheckArgs(ToolArgs):
    statement_id: str = Field(
        min_length=5, max_length=64, pattern=r"^lean-[0-9a-f]{16}$",
        description="The statement_id returned by lean_statement.")
    proof_body: str = Field(
        max_length=MAX_PROOF_BODY_CHARS,
        description=("The Lean 4 tactic block that proves the statement. Only the body: no "
                     "leading `by`, no statement, further lines indented by two spaces."))
    ir: dict[str, Any] | None = Field(
        default=None,
        description=("Optional: the IR the statement was rendered from. Needed only when the "
                     "statement_id is no longer known (for example after a resume); the "
                     "statement is then re-rendered and must have the same id."))
    direction: Direction | None = Field(
        default=None, description="Optional, with ir: the direction passed to lean_statement.")


@tfl_tool(group="lean", max_trust="not_verified", timeout_s=30, allow_refuted=False)
def lean_statement(args: LeanStatementArgs) -> ToolResult:
    """Render the fixed Lean 4 theorem statement for the task IR and a direction (regular /
    non_regular / cfl / non_cfl / dcfl / non_dcfl), generated by code exactly as the pipelines'
    formalization does. Returns statement_id (pass it to lean_check), the statement text
    (imports, alphabet, language definition, `theorem tfl_main : ...`), a file template with
    <PROOF> where the proof body goes, and the proof-body rules. You never write or change the
    statement; you only write the proof body. Reference only (trust not_verified): the
    statement itself is not evidence about the language."""
    fresh = _render_or_raise(args.ir, args.direction)
    entry = _remember(fresh)
    fp = fresh.ir_fingerprint  # this call's IR, never the cached entry's
    with _STORE_LOCK:
        use = _use(entry.statement_id)
        use.ir_fingerprint = fp
        checks_used = use.checks
    stmt = entry.statement
    cls, side, module, claim = _DIRECTION_INFO[entry.direction]
    data = {
        "statement_id": entry.statement_id,
        "direction": entry.direction,
        "cls": cls,
        "side": side,
        "claim": claim,
        "name": _snapshot(stmt)["name"],
        "statement": statement_text(stmt),
        "template": stmt.render("<PROOF>"),
        **{k: v for k, v in _snapshot(stmt).items() if k != "name"},
        "proof_body_rules": list(PROOF_BODY_RULES),
        "renderer": f"{module}.render_statement_verbose",
        "ir_fingerprint": fp,
        "max_checks": MAX_CHECKS_PER_STATEMENT,
        "checks_used": checks_used,
        "evidence_role": "reference",
    }
    return ToolResult.success(
        data, trust="not_verified",
        reason=(f"statement {entry.statement_id} generated by code from IR {fp} "
                f"({module}); a statement checks no claim (reference)"))


def _resolve(args: LeanCheckArgs) -> _Stored:
    with _STORE_LOCK:
        entry = _STORE.get(args.statement_id)
    if entry is not None:
        if args.direction is not None and args.direction != entry.direction:
            raise ToolInputError(
                f"statement {args.statement_id} was rendered for direction {entry.direction!r}, "
                f"not {args.direction!r}")
        if args.ir is not None:
            fresh = _render_or_raise(args.ir, entry.direction)
            if fresh.statement_id != entry.statement_id:
                raise ToolInputError(
                    f"the given ir renders statement {fresh.statement_id}, not {args.statement_id}")
        return entry
    if args.ir is None or args.direction is None:
        raise ToolInputError(
            f"unknown statement_id {args.statement_id!r}: call lean_statement first, or pass the "
            "ir and the direction so the statement can be re-rendered")
    fresh = _render_or_raise(args.ir, args.direction)
    if fresh.statement_id != args.statement_id:
        raise ToolInputError(
            f"the given ir and direction {args.direction!r} render statement "
            f"{fresh.statement_id}, not {args.statement_id}; the statement is generated by code "
            "and cannot be changed")
    return _remember(fresh)


@tfl_tool(group="lean", max_trust="verified", timeout_s=TOOL_TIMEOUT_S, allow_refuted=False)
def lean_check(args: LeanCheckArgs) -> ToolResult:
    """Type-check a Lean 4 proof body against the code-generated statement `statement_id`
    (from lean_statement) in the tfl-lean4 Docker image: the file is composed by code
    (statement + your body + an independent kernel replay + `#print axioms tfl_main`). A body
    with sorry / admit / axiom / native_decide / metaprogramming / a declaration / a line at
    column 0 is rejected before compiling. Returns status proved / has_sorry / error / timeout,
    the compiler errors, and the axioms. Trust `verified` only for `proved` with the standard
    axioms (propext, Classical.choice, Quot.sound); anything else is not evidence either way.
    Takes 30-300 s per call; at most a few attempts per statement."""
    entry = _resolve(args)
    with _STORE_LOCK:
        use = _use(entry.statement_id)
        if args.ir is not None:
            fp, fp_source = ir_fingerprint(args.ir), "call"
            use.ir_fingerprint = fp
        elif use.ir_fingerprint:
            fp, fp_source = use.ir_fingerprint, "run"
        else:  # not rendered in this run and no ir given: the cache's last IR
            fp, fp_source = entry.ir_fingerprint, "cache"
    stmt = entry.statement
    snap = _snapshot(stmt)
    cls, side, _module, claim = _DIRECTION_INFO[entry.direction]
    body = args.proof_body.replace("\r\n", "\n")
    base = {
        "statement_id": entry.statement_id, "direction": entry.direction, "cls": cls,
        "side": side, "claim": claim, "theorem_name": snap["name"],
        "ir_fingerprint": fp, "ir_fingerprint_source": fp_source, "statement": statement_text(stmt),
        "proof_body": body, "evidence_role": "claim_check",
    }

    violations = guard_proof_body(body)
    if violations:
        return ToolResult.failure(
            "proof body rejected before compiling (R-Lean guard): " + "; ".join(violations),
            kind="invalid_input",
            data={**base, "status": "rejected", "violations": violations,
                  "proof_body_rules": list(PROOF_BODY_RULES)})

    with _STORE_LOCK:
        if use.checks >= MAX_CHECKS_PER_STATEMENT:
            raise ToolBusy(
                f"attempt limit reached: {use.checks} checks of statement {entry.statement_id} "
                f"in this run (max {MAX_CHECKS_PER_STATEMENT}); stop and report the last error",
                data={**base, "status": "attempt_limit"})
        use.checks += 1
        attempt = use.checks

    text = compose(stmt, body)
    if text.lstrip().startswith("-- TFL_PROOF_BODY_REJECTED"):  # scan_proof_body is the same gate
        return ToolResult.failure("proof body rejected by type_check.compose_lean_file",
                                  kind="invalid_input", data={**base, "status": "rejected"})
    lean_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    deadline = Deadline(TOOL_TIMEOUT_S - 10)
    if not _CHECK_SEMAPHORE.acquire(timeout=max(1.0, deadline.remaining() - LEAN_TIMEOUT_S - 30)):
        with _STORE_LOCK:
            use.checks -= 1
        raise ToolBusy(f"{MAX_PARALLEL_CHECKS} Lean checks are already running; try again later")
    daemon_retries = 0
    try:
        res = _run_lean(text, LEAN_TIMEOUT_S, snap["name"])
        while (_is_daemon_failure(res) and daemon_retries < DAEMON_RETRIES
               and deadline.remaining() > LEAN_TIMEOUT_S + 30 + DAEMON_RETRY_SLEEP_S):
            daemon_retries += 1
            time.sleep(DAEMON_RETRY_SLEEP_S)
            res = _run_lean(text, LEAN_TIMEOUT_S, snap["name"])
    finally:
        _CHECK_SEMAPHORE.release()

    status = str(res.get("status") or "error")
    axioms = [str(a) for a in (res.get("axioms") or [])]
    info = {
        **base, "status": status, "axioms": axioms,
        "allowed_axioms": sorted(type_check.ALLOWED_AXIOMS),
        "errors": _trim_messages(res.get("errors")),
        "n_errors": len(res.get("errors") or []),
        "n_warnings": len(res.get("warnings") or []),
        "lean_elapsed_s": res.get("elapsed"), "lean_timeout_s": LEAN_TIMEOUT_S,
        "lean_file_sha256": lean_sha, "attempt": attempt,
        "attempts_left": max(0, MAX_CHECKS_PER_STATEMENT - attempt),
        "daemon_retries": daemon_retries,
    }

    if status in ("unavailable", "skipped") or _is_daemon_failure(res):
        with _STORE_LOCK:
            use.checks = max(0, use.checks - 1)  # the proof was never checked
        msg = res.get("message") or (info["errors"][0]["data"] if info["errors"] else "")
        return ToolResult.failure(
            f"Lean checker unavailable (Docker / the {type_check.DOCKER_IMAGE} image): {msg}",
            kind="unsupported", data={**info, "status": "unavailable"})

    verdict = compute_lean_proof_trust({"status": status, "direction": entry.direction})
    trust = verdict["trust"]
    if trust == "verified" and not set(axioms) <= type_check.ALLOWED_AXIOMS:
        trust = "not_verified"  # check_lean_file already enforces this; a second line
        info["status"] = status = "error"
        info["errors"].append({"severity": "error",
                               "data": f"disallowed axioms {sorted(set(axioms) - type_check.ALLOWED_AXIOMS)}"})
    if trust == "verified" and text.count(f"theorem {snap['name']}") != 1:
        trust = "not_verified"
        info["status"] = status = "error"
        info["errors"].append({"severity": "error", "data": "the composed file does not declare "
                               f"{snap['name']} exactly once"})

    if trust == "verified":
        reason = (f"Lean 4 proved the code-generated statement {entry.statement_id} "
                  f"({claim}; IR {fp}): no errors, no sorry, kernel replay ok, "
                  f"axioms {axioms or 'none'} within the standard set (R-Lean)")
    else:
        reason = (f"Lean status {status!r} for statement {entry.statement_id}: not evidence either "
                  "way (R1, R-Lean)")
    return ToolResult.success(info, trust=trust, reason=reason)
