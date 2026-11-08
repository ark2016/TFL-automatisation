"""Human in the loop for the TFL Deep Agent (P3; architecture.html "Точки HITL").

Two stops, both on by default (``settings.hitl``):

* **HITL 1, formalization** -- after an ir-builder answer (or an accepted
  ``validate_ir`` call of the main agent itself, so building the IR without
  the ir-builder does not skip the review) the main agent is interrupted
  *before its next model call* (:class:`FormalizationReviewMiddleware`, a
  LangGraph ``interrupt`` in ``before_model``). The human sees the
  transcription (the intake answer, or the task text), the IR the agent will
  use, its code-made restatement and oracle-labelled words (``validate_ir`` and
  ``ir_to_text`` run by code on that IR, not the model's own paraphrase), and
  approves, edits (text / IR) or rejects with a comment. The decision reaches
  the model as a message starting with "HITL 1", and it binds the verdict by
  code (:func:`formalization_binding` -> ``agent.verdict_gate``): only oracle
  results for the approved / edited IR count, whatever fingerprint the report
  names; a rejected or never-reviewed formalization leaves the verdict
  ``not_verified``.
* **HITL 2, report** -- ``submit_report`` is interrupted before it runs
  (deepagents ``interrupt_on``, i.e. langchain's ``HumanInTheLoopMiddleware``,
  with a ``when`` predicate: only a report whose fields validate is shown; an
  invalid one goes back to the model as a validation error). Approve, edit
  fields (validated again; the edited fields are marked in the report) or
  reject with a comment, after which the agent continues with the comment as
  the tool's error result.
* ``execute`` (off by default): the coder's sandbox calls.

Both requests have the ``HITLRequest`` shape (``action_requests`` /
``review_configs``) and both take a ``HITLResponse`` (``{"decisions": [...]}``),
so the CLI, the TFL Lab UI and the eval adapter handle them the same way.

Runs (:func:`start_run`, :func:`resume_run`): the state lives in a SQLite
checkpointer in the run directory. At an interrupt the process writes
``run.json`` with ``status: awaiting_human`` and the pending request and
returns (the CLI exits with code 3); a NEW process resumes it with
``Command(resume=...)`` (``tfl-deep --resume RUN_DIR --decision ...``). What
must survive the process boundary is stored in the run directory:

====================  =====================================================
``run.json``          status, task, settings, mode, HITL history, pending
                      request, budget spent + call records, call counters
``checkpoints.sqlite`` LangGraph checkpoints (``thread_id`` = run id)
``evidence.jsonl``    the evidence ledger (continued on resume)
``mock_script.json``  mock mode: a copy of the script ...
``mock_cursors.json`` ... and the replies used per role
``input/task.*``      the task file as the human gave it
``hitl.md``           the pending HITL card (what the human decides on)
``result.*``          the rendered report (a draft while HITL 2 waits)
====================  =====================================================

Review modes (``review_mode``, per run, stored in ``run.json``): ``all``
(default) -- every enabled point waits for a human; ``formalization`` -- HITL
1 (and ``execute``) wait, HITL 2 is auto-approved; ``auto`` -- every stop is
answered with "approve" in the same process (``auto_approve=True`` /
``--auto-approve`` is an alias). An auto-approved stop is recorded with source
``auto`` and decision ``auto_approved``; an auto-approved report is marked
"без проверки человеком", and an auto-approved formalization is not a human
approval for the verdict gate (:func:`run_binding`). ``resume_run(...,
approve_rest=True)`` approves the pending stop as the human's decision and
switches the run to ``auto``. A ``dialog`` callable (the CLI's TTY dialog)
asks in the same process; ``None`` from it leaves the run awaiting.

While a run waits, ``run.json`` ``pending.card`` holds the short card of the
stop (:func:`pending_card`; TFL Lab shows it as ``pending_point``).
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import re
import shutil
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, NotRequired

try:  # langchain is the optional [deep] extra
    from langchain.agents.middleware import AgentMiddleware as _AgentMiddleware
    from langchain.agents.middleware.types import AgentState as _AgentState
    _HAVE_LANGCHAIN = True
except ImportError:  # pragma: no cover - exercised only without [deep]
    _AgentMiddleware = object  # type: ignore[assignment,misc]
    _AgentState = dict  # type: ignore[assignment,misc]
    _HAVE_LANGCHAIN = False

FORMALIZATION = "formalization"
REPORT = "report"
EXECUTE = "execute"
SUBMIT_REPORT = "submit_report"
IR_BUILDER = "ir-builder"
INTAKE = "intake"
DECISIONS = ("approve", "edit", "reject")

STATE_KEY = "tfl_hitl"
STATUS_AWAITING = "awaiting_human"
RUN_FILE = "run.json"
CHECKPOINT_FILE = "checkpoints.sqlite"
MOCK_SCRIPT_FILE = "mock_script.json"
MOCK_CURSORS_FILE = "mock_cursors.json"
CARD_FILE = "hitl.md"
INPUT_DIR = "input"
RUN_SCHEMA = 1

#: exit codes of a run (the CLI's): 0 report accepted, 1 ended without a
#: report, 3 waiting for a human decision (2 is the CLI's usage error)
EXIT_SUBMITTED, EXIT_NO_REPORT, EXIT_AWAITING = 0, 1, 3

AUTO_APPROVED_LABEL = "auto-approved (review mode or --auto-approve): without human review"
FORMALIZATION_EDIT_FIELDS = ("transcription", "ir", "class")
_CLASSES = ("reg", "cfl", "dcfl", "ll")

#: per-run review modes (user decision 2026-11-08): ``all`` every enabled
#: point waits for a human; ``formalization`` HITL 1 (and ``execute``) wait,
#: HITL 2 is auto-approved; ``auto`` every point is auto-approved (the old
#: ``auto_approve``). An auto-approved stop is recorded with source ``auto``
#: and decision ``auto_approved``, exactly like ``--auto-approve``.
REVIEW_ALL, REVIEW_FORMALIZATION, REVIEW_AUTO = "all", "formalization", "auto"
REVIEW_MODES = (REVIEW_ALL, REVIEW_FORMALIZATION, REVIEW_AUTO)


def resolve_review_mode(review_mode: str | None = None, auto_approve: bool = False) -> str:
    """The review mode of a new run: ``review_mode`` (one of :data:`REVIEW_MODES`),
    ``auto_approve=True`` being an alias of ``auto``; default ``all``."""
    if review_mode is None:
        return REVIEW_AUTO if auto_approve else REVIEW_ALL
    mode = str(review_mode).strip().lower()
    if mode not in REVIEW_MODES:
        raise RunError(f"unknown review mode {review_mode!r}; one of {list(REVIEW_MODES)}")
    if auto_approve and mode != REVIEW_AUTO:
        raise RunError(f"auto_approve is the review mode 'auto'; it conflicts with review mode {mode!r}")
    return mode


def review_mode_of(run: Mapping[str, Any]) -> str:
    """The run's effective review mode (``run.json`` ``review_mode``; a run
    written before it existed: ``auto`` when ``auto_approve``, else ``all``)."""
    mode = run.get("review_mode")
    if mode in REVIEW_MODES:
        return str(mode)
    return REVIEW_AUTO if run.get("auto_approve") else REVIEW_ALL


def auto_approves(mode: str, point: str) -> bool:
    """Whether a stop at ``point`` is approved without a human in ``mode``:
    ``auto`` every point; ``formalization`` only the report (HITL 2), never
    HITL 1 or ``execute``; ``all`` none."""
    if mode == REVIEW_AUTO:
        return True
    if mode == REVIEW_FORMALIZATION:
        return point == REPORT
    return False


class DecisionError(ValueError):
    """A human decision that does not fit the pending request (wrong type, an
    edit that does not validate, a rejection without a comment)."""


class RunError(ValueError):
    """A run directory that cannot be started or resumed as asked."""


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _text_of(msg: Any) -> str:
    text = getattr(msg, "text", None)
    if isinstance(text, str):
        return text
    content = _get(msg, "content", "")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# State: the review records of this run (tfl_hitl)
# ---------------------------------------------------------------------------

def merge_reviews(left: Sequence[Mapping[str, Any]] | None,
                  right: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Reducer for ``tfl_hitl``: append, keyed by ``(point, id)``; a later
    record for the same key replaces the earlier one."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for seq in (left or [], right or []):
        if isinstance(seq, Mapping):
            seq = [seq]
        for rec in seq:
            if isinstance(rec, Mapping):
                key = (str(rec.get("point") or ""), str(rec.get("id") or ""))
                out.pop(key, None)
                out[key] = dict(rec)
    return list(out.values())


class HitlState(_AgentState):  # type: ignore[misc,valid-type]
    tfl_hitl: NotRequired[Annotated[list[dict[str, Any]], merge_reviews]]


# ---------------------------------------------------------------------------
# HITL 1: the formalization request (built by code)
# ---------------------------------------------------------------------------

def _calls_named(messages: Sequence[Any], name: str) -> dict[str, dict[str, Any]]:
    """``tool_call_id -> args`` of every ``name`` call in the thread."""
    from langchain_core.messages import AIMessage

    out: dict[str, dict[str, Any]] = {}
    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls:
                if tc.get("name") == name and tc.get("id"):
                    out[tc["id"]] = dict(tc.get("args") or {})
    return out


def _task_calls(messages: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """``tool_call_id -> args`` of every ``task`` call in the thread."""
    return _calls_named(messages, "task")


def _accepted_validation(msg: Any) -> bool:
    """A ``validate_ir`` ToolMessage whose ToolResult accepted the IR."""
    art = getattr(msg, "artifact", None)
    if not isinstance(art, Mapping) or not art.get("ok"):
        return False
    data = art.get("data")
    return isinstance(data, Mapping) and bool(data.get("valid"))


def _trailing_tool_messages(messages: Sequence[Any]) -> list[Any]:
    from langchain_core.messages import ToolMessage

    out = []
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            break
        out.append(msg)
    return list(reversed(out))


def _first_human_text(messages: Sequence[Any]) -> str:
    from langchain_core.messages import HumanMessage

    for msg in messages:
        if isinstance(msg, HumanMessage):
            return _text_of(msg)
    return ""


def _subagent_answers(messages: Sequence[Any], calls: Mapping[str, Mapping[str, Any]],
                      subagent: str) -> list[Any]:
    from langchain_core.messages import ToolMessage

    return [m for m in messages if isinstance(m, ToolMessage) and m.name == "task"
            and (calls.get(m.tool_call_id) or {}).get("subagent_type") == subagent]


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n(.*?)\n```", re.DOTALL)


def _json_blocks(text: str) -> list[Any]:
    out = []
    for m in _FENCE_RE.finditer(text or ""):
        try:
            out.append(json.loads(m.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def _looks_like_ir(obj: Any) -> bool:
    return isinstance(obj, dict) and ("task_type" in obj or "language_spec" in obj or "grammar" in obj)


def _class_hint(*texts: str) -> str | None:
    for text in texts:
        m = re.search(r"(?<![A-Za-z])(dcfl|cfl|reg|ll)(?![A-Za-z(])", text or "", re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return None


def _seq_window(recs: Sequence[Mapping[str, Any]], call_id: str) -> list[Mapping[str, Any]]:
    """The records made during the ir-builder call ``call_id`` in a ledger
    without ``parent_call`` (written before it existed): those after the
    previous ir-builder ``task`` record and before this call's own one."""
    def is_ib_task(r: Mapping[str, Any]) -> bool:
        args = r.get("args") if isinstance(r.get("args"), Mapping) else {}
        return r.get("tool") == "task" and args.get("subagent_type") == IR_BUILDER

    own = [int(r.get("seq") or 0) for r in recs if is_ib_task(r) and r.get("tool_call_id") == call_id]
    if not own:
        return []
    hi = own[-1]
    lo = max((int(r.get("seq") or 0) for r in recs if is_ib_task(r) and int(r.get("seq") or 0) < hi),
             default=0)
    return [r for r in recs if lo < int(r.get("seq") or 0) < hi]


def _ledger_validations(evidence: Any, role: str = "ir_builder",
                        parent_call: str | None = None) -> list[dict[str, Any]]:
    """The ``validate_ir`` records of ``role`` (newest last) that accepted the
    IR; with ``parent_call``, only those made inside that ``task`` call (the
    ledger's ``parent_call``; an older ledger without it: the call's seq
    window), never an earlier or a parallel ir-builder call's."""
    if evidence is None:
        return []
    from .evidence.policy import as_records

    recs: list[Mapping[str, Any]] = list(as_records(evidence).values())
    if parent_call is not None:
        if any(r.get("parent_call") for r in recs):
            recs = [r for r in recs if r.get("parent_call") == parent_call]
        else:
            recs = _seq_window(recs, parent_call)
    out = []
    for rec in recs:
        if rec.get("tool") != "validate_ir" or (role and rec.get("role") not in (role, None)):
            continue
        data = rec.get("data") if isinstance(rec.get("data"), Mapping) else {}
        args = rec.get("args") if isinstance(rec.get("args"), Mapping) else {}
        if rec.get("ok") and data.get("valid") and isinstance(args.get("ir"), Mapping):
            out.append({"id": rec["id"], "cls": args.get("cls") or data.get("class"), "ir": dict(args["ir"])})
    return out


def find_ir(answer: str, *, task_args: Mapping[str, Any] | None = None,
            evidence: Any = None, call_id: str | None = None) -> dict[str, Any]:
    """The IR an ir-builder answer stands for: the IR JSON block of the
    answer, else the IR of the last accepted ``validate_ir`` call the
    ir-builder made **in this call** (``call_id``, the ``task`` tool-call id;
    the ledger's ``parent_call``) -- never one of an earlier (rejected) or a
    parallel ir-builder call; none there is "no IR". Without ``call_id`` the
    whole ledger is searched. Returns ``{"ir", "class", "source", "error"}``."""
    validations = _ledger_validations(evidence, parent_call=call_id)
    blocks = [b for b in _json_blocks(answer) if _looks_like_ir(b)]
    hint = _class_hint(str((task_args or {}).get("description") or ""), answer)
    if blocks:
        ir = blocks[-1]
        same = [v for v in validations if v["ir"] == ir]
        cls = (same[-1]["cls"] if same else None) or hint
        return {"ir": ir, "class": cls, "source": "the IR JSON block of the ir-builder answer", "error": None}
    if validations:
        v = validations[-1]
        return {"ir": v["ir"], "class": v["cls"] or hint,
                "source": f"the last accepted validate_ir call of this ir-builder run ({v['id']}); the answer "
                          "has no IR JSON block", "error": None}
    return {"ir": None, "class": hint, "source": None,
            "error": "no IR found: the ir-builder answer has no IR JSON block and this ir-builder call made no "
                     "accepted validate_ir call"}


def _invoke(name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    from .tools import base

    base.load_groups(["ir", "words"])
    return base.REGISTRY.invoke(name, dict(args)).model_dump(mode="json")


def ir_checks(cls: str | None, ir: Mapping[str, Any] | None, *, n_examples: int = 8) -> dict[str, Any]:
    """``validate_ir`` + ``ir_to_text`` run by code on ``ir`` (for the human,
    not recorded as evidence): validity, errors, warnings, fingerprint, the
    restatement and oracle-labelled words in / not in L."""
    if ir is None:
        return {"valid": False, "errors": ["no IR"], "restatement": None, "examples": None}
    classes = [cls] if cls in _CLASSES else list(_CLASSES)
    val: dict[str, Any] = {}
    for c in classes:
        val = _invoke("validate_ir", {"cls": c, "ir": ir, "check_tools": False})
        if (val.get("data") or {}).get("valid"):
            cls = c
            break
    data = val.get("data") or {}
    out: dict[str, Any] = {
        "class": cls if cls in _CLASSES else None,
        "valid": bool(data.get("valid")),
        "errors": list(data.get("errors") or ([val.get("error")] if val.get("error") else [])),
        "warnings": list(data.get("warnings") or []),
        "ir_fingerprint": data.get("ir_fingerprint"),
        "restatement": None, "examples": None,
    }
    if out["valid"]:
        txt = _invoke("ir_to_text", {"cls": cls, "ir": ir, "n_examples": n_examples, "example_max_len": 8})
        tdata = txt.get("data") or {}
        out["restatement"] = tdata.get("text") if txt.get("ok") else f"ir_to_text failed: {txt.get('error')}"
        ex = tdata.get("examples") or {}
        if ex.get("available"):
            out["examples"] = {"in_language": ex.get("in_language") or [],
                               "not_in_language": ex.get("not_in_language") or [],
                               "max_len": ex.get("max_len"), "labels_from": ex.get("labels_from")}
    return out


def formalization_request(state: Mapping[str, Any], target: Any, *, evidence: Any = None,
                          source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The HITL 1 request for ``target`` (a ToolMessage): an ir-builder
    answer, or an accepted ``validate_ir`` call of the main agent (the IR is
    that call's argument)."""
    messages = list(state.get("messages") or [])
    calls = _task_calls(messages)
    intake = _subagent_answers(messages, calls, INTAKE)
    if target.name == "validate_ir":
        trigger, answer = "validate_ir", ""
        vargs = _calls_named(messages, "validate_ir").get(target.tool_call_id) or {}
        ir = vargs.get("ir") if isinstance(vargs.get("ir"), Mapping) else None
        found = {"ir": dict(ir) if ir is not None else None, "class": vargs.get("cls"),
                 "source": (f"the main agent's own validate_ir call ({target.tool_call_id}): the IR was not "
                            "built by the ir-builder"),
                 "error": None if ir is not None else "the validate_ir call has no IR object"}
    else:
        trigger, answer = IR_BUILDER, _text_of(target)
        found = find_ir(answer, task_args=calls.get(target.tool_call_id), evidence=evidence,
                        call_id=target.tool_call_id)
    checks = ir_checks(found["class"], found["ir"])
    args = {
        "task_text": _first_human_text(messages)[:20000],
        "source": dict(source) if source else {"kind": "text"},
        "transcription": _text_of(intake[-1])[:20000] if intake else None,
        "trigger": trigger,
        "reviewed_call_id": target.tool_call_id,
        "ir_builder_call_id": target.tool_call_id if trigger == IR_BUILDER else None,
        "ir_builder_answer": answer[:8000] if trigger == IR_BUILDER else None,
        "class": checks.get("class") or found["class"],
        "ir": found["ir"],
        "ir_source": found["source"],
        "ir_error": found["error"],
        "ir_fingerprint": checks.get("ir_fingerprint"),
        "valid": checks["valid"],
        "errors": checks["errors"],
        "warnings": checks.get("warnings") or [],
        "restatement": checks["restatement"],
        "examples": checks["examples"],
    }
    return {
        "action_requests": [{"name": FORMALIZATION, "args": args,
                             "description": formalization_card(args)}],
        "review_configs": [{"action_name": FORMALIZATION, "allowed_decisions": list(DECISIONS)}],
    }


def _words(ws: Sequence[str] | None) -> str:
    return ", ".join(w if w else "ε" for w in ws or []) or "—"


def formalization_card(args: Mapping[str, Any]) -> str:
    """Markdown text of a HITL 1 request (what the human decides on)."""
    src = args.get("source") or {}
    lines = ["## HITL 1: transcription and IR", ""]
    if src.get("kind") in ("pdf", "image"):
        extra = f", {src['pages']} p." if src.get("pages") else ""
        where = src.get("stored_as") or src.get("input_path")
        lines += [f"Source: {src.get('kind')} «{src.get('name')}»{extra} ({where}).", ""]
    if args.get("transcription"):
        lines += ["### Transcription (intake)", "", str(args["transcription"]), ""]
    else:
        lines += ["### Task text", "", str(args.get("task_text") or "—"), ""]
    head = f"### IR (class {args.get('class') or '?'}"
    head += f", {args['ir_fingerprint']}" if args.get("ir_fingerprint") else ""
    head += ", valid)" if args.get("valid") else ", NOT valid)"
    lines += [head, ""]
    if args.get("ir") is not None:
        lines += ["```json", json.dumps(args["ir"], ensure_ascii=False, indent=2), "```", ""]
        lines += [f"Taken from {args.get('ir_source')}.", ""]
    else:
        lines += [str(args.get("ir_error") or "no IR"), ""]
    if args.get("errors") and not args.get("valid"):
        lines += ["Validator errors: " + "; ".join(str(e) for e in args["errors"]), ""]
    if args.get("warnings"):
        lines += ["Warnings: " + "; ".join(str(w) for w in args["warnings"]), ""]
    if args.get("restatement"):
        lines += ["### Restatement of the IR (ir_to_text, code)", "", str(args["restatement"]), ""]
    ex = args.get("examples") or {}
    if ex:
        lines += [f"### Words (labels: {ex.get('labels_from')}, length ≤ {ex.get('max_len')})", "",
                  f"- in L: {_words(ex.get('in_language'))}",
                  f"- not in L: {_words(ex.get('not_in_language'))}", ""]
    if args.get("trigger") == "validate_ir":
        lines += ["### Where the IR comes from", "",
                  ("The main agent validated this IR itself (no ir-builder answer reviewed it before); "
                   f"call {args.get('reviewed_call_id')}."), ""]
    else:
        lines += ["### ir-builder answer", "", str(args.get("ir_builder_answer") or "—"), ""]
    lines += [("Every oracle check after this point is relative to this IR: only results for the IR you approve "
               "here count for the verdict. Approve it, edit the transcription / IR, or reject it with a comment.")]
    return "\n".join(lines)


class FormalizationReviewMiddleware(_AgentMiddleware):  # type: ignore[misc,valid-type]
    """HITL 1 on the main agent: before the model call that follows an
    ir-builder answer -- or an accepted ``validate_ir`` call of the main agent
    itself (an IR built without the ir-builder is reviewed too) -- interrupt
    with :func:`formalization_request` and turn the human's decision into a
    "HITL 1" message for the model.

    The interrupt sits in ``before_model``: on resume only this hook runs
    again (cheap, deterministic), never the ir-builder subagent or a model
    call. Each ir-builder answer is reviewed once (``tfl_hitl`` in the state);
    an answer whose IR fingerprint was already approved is passed without a
    new stop."""

    state_schema = HitlState

    def __init__(self, *, evidence: Any = None, source: Mapping[str, Any] | None = None) -> None:
        if not _HAVE_LANGCHAIN:  # pragma: no cover
            raise ImportError("FormalizationReviewMiddleware needs the [deep] extra (langchain)")
        super().__init__()
        self.evidence = evidence
        self.source = dict(source) if source else None
        self.tools = []

    @property
    def name(self) -> str:
        return "TFLFormalizationReview"

    def _pending(self, state: Mapping[str, Any]) -> list[Any]:
        messages = list(state.get("messages") or [])
        trailing = _trailing_tool_messages(messages)
        if not trailing:
            return []
        calls = _task_calls(messages)
        done = {r.get("id") for r in state.get(STATE_KEY) or [] if r.get("point") == FORMALIZATION}
        out = []
        for m in trailing:
            if m.status == "error" or m.tool_call_id in done:
                continue
            ir_builder = m.name == "task" and (calls.get(m.tool_call_id) or {}).get("subagent_type") == IR_BUILDER
            own_ir = m.name == "validate_ir" and _accepted_validation(m)  # the main agent built the IR itself
            if ir_builder or own_ir:
                out.append(m)
        return out

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ARG002
        pending = self._pending(state)
        if not pending:
            return None
        target = pending[-1]
        superseded = [{"id": m.tool_call_id, "point": FORMALIZATION, "decision": "superseded",
                       "at": _now()} for m in pending[:-1]]
        request = formalization_request(state, target, evidence=self.evidence, source=self.source)
        args = request["action_requests"][0]["args"]
        approved = {r.get("ir_fingerprint") for r in state.get(STATE_KEY) or []
                    if r.get("point") == FORMALIZATION and r.get("decision") in ("approved", "edited")}
        if args.get("ir_fingerprint") and args["ir_fingerprint"] in approved:
            rec = {"id": target.tool_call_id, "point": FORMALIZATION, "decision": "unchanged",
                   "ir_fingerprint": args["ir_fingerprint"], "at": _now()}
            return {STATE_KEY: [*superseded, rec]}
        from langgraph.types import interrupt

        response = interrupt(request)
        decision = _first_decision(response)
        message, rec = self._apply(decision, args, target.tool_call_id)
        from langchain_core.messages import HumanMessage

        return {"messages": [HumanMessage(content=message)], STATE_KEY: [*superseded, rec]}

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    @staticmethod
    def _apply(decision: Mapping[str, Any], args: Mapping[str, Any], call_id: str) -> tuple[str, dict[str, Any]]:
        kind = decision.get("type")
        fp = args.get("ir_fingerprint")
        rec: dict[str, Any] = {"id": call_id, "point": FORMALIZATION, "ir_fingerprint": fp,
                               "class": args.get("class"), "at": _now()}
        if kind == "approve":
            rec["decision"] = "approved"
            text = ("HITL 1 (formalization): the human APPROVED the transcription and the IR"
                    f"{f' {fp}' if fp else ''}. Continue with this IR; every oracle check is relative to it.")
            return text, rec
        if kind == "reject":
            comment = str(decision.get("message") or "").strip() or "(no comment)"
            rec.update(decision="rejected", comment=comment)
            text = ("HITL 1 (formalization): the human REJECTED the transcription / IR with this comment:\n\n"
                    f"«{comment}»\n\nFix the formalization (call intake and / or ir-builder again and give "
                    "them the comment). Do not start the proof until a new formalization is approved.")
            return text, rec
        if kind == "edit":
            edited = dict((decision.get("edited_action") or {}).get("args") or {})
            parts, fields = [], []
            if edited.get("transcription") is not None and edited["transcription"] != args.get("transcription"):
                fields.append("transcription")
                parts.append(f"The task text, as corrected by the human:\n\n{edited['transcription']}")
            if edited.get("ir") is not None and edited["ir"] != args.get("ir"):
                cls = edited.get("class") or args.get("class")
                problems = _ir_problems(cls, edited["ir"])
                if problems:  # the runner validates first; this is the last line of defence
                    rec.update(decision="rejected", comment="edited IR is not valid: " + "; ".join(problems))
                    text = ("HITL 1 (formalization): the human's edited IR does not pass validate_ir "
                            f"({'; '.join(problems)}). Treat the formalization as rejected and rebuild it.")
                    return text, rec
                from .tools.ir import ir_fingerprint

                fp = ir_fingerprint(edited["ir"])
                fields.append("ir")
                parts.append(f"The IR to use (class {cls}, fingerprint {fp}), replacing the ir-builder's:\n\n"
                             f"```json\n{json.dumps(edited['ir'], ensure_ascii=False, indent=2)}\n```")
            if not fields:
                rec["decision"] = "approved"
                text = ("HITL 1 (formalization): the human APPROVED the transcription and the IR"
                        f"{f' {fp}' if fp else ''}. Continue with this IR.")
                return text, rec
            rec.update(decision="edited", edited_fields=fields, ir_fingerprint=fp)
            return ("HITL 1 (formalization): the human APPROVED the formalization WITH EDITS. From now on use "
                    "exactly this, not the earlier version:\n\n" + "\n\n".join(parts)
                    + "\n\nOracle results for the old IR do not count for the new one: re-run the checks you "
                      "rely on against this IR.", rec)
        raise ValueError(f"HITL 1: unknown decision type {kind!r}; one of {list(DECISIONS)}")


def _ir_problems(cls: str | None, ir: Any) -> list[str]:
    if not isinstance(ir, Mapping):
        return ["the IR must be a JSON object"]
    if cls not in _CLASSES:
        return [f"unknown class {cls!r}; one of {list(_CLASSES)}"]
    from .tools.ir import validation_errors

    try:
        return [str(e) for e in validation_errors(cls, dict(ir))]
    except Exception as exc:  # noqa: BLE001 - a validator crash is a problem with the IR
        return [f"{type(exc).__name__}: {exc}"]


def _first_decision(response: Any) -> dict[str, Any]:
    if isinstance(response, Mapping) and isinstance(response.get("decisions"), list) and response["decisions"]:
        d = response["decisions"][0]
        if isinstance(d, Mapping):
            return dict(d)
    if isinstance(response, Mapping) and response.get("type") in DECISIONS:
        return dict(response)
    raise ValueError(f"HITL: a resume value must be {{'decisions': [{{'type': ...}}]}}, got {response!r}")


# ---------------------------------------------------------------------------
# HITL 2 (submit_report) and execute: deepagents interrupt_on configs
# ---------------------------------------------------------------------------

def _valid_report(args: Any) -> dict[str, Any] | None:
    from .tools.report import validate_report

    try:
        return validate_report(dict(args or {}))
    except Exception:  # noqa: BLE001 - invalid fields: the tool reports them to the model
        return None


def formalization_binding(records: Sequence[Mapping[str, Any]] | None, *, on: bool,
                          auto: bool = False) -> dict[str, Any] | None:
    """What HITL 1 decided, as the verdict gate reads it (``None`` when HITL 1
    is off). ``records``: the ``tfl_hitl`` state records. ``status``:

    * ``approved`` -- the last decision approved or edited the formalization
      (or an answer repeated an approved IR); ``ir_fingerprint`` is the IR every
      oracle result must refer to;
    * ``rejected`` -- the last decision rejected it and nothing was approved
      after it;
    * ``no_ir`` -- approved, but there was no IR to approve;
    * ``not_held`` -- HITL 1 is on and no formalization reached it.

    ``auto``: the run answers every stop automatically (``--auto-approve``)."""
    if not on:
        return None
    recs = [r for r in records or [] if _get(r, "point") == FORMALIZATION and _get(r, "decision") != "superseded"]
    if not recs:
        return {"status": "not_held", "decision": None, "ir_fingerprint": None, "review_id": None,
                "auto": bool(auto)}
    last = recs[-1]
    decision = _get(last, "decision")
    fp = _get(last, "ir_fingerprint")
    status = "approved" if decision in ("approved", "edited", "unchanged") else "rejected"
    if status == "approved" and not fp:
        status = "no_ir"
    return {"status": status, "decision": decision, "ir_fingerprint": fp, "review_id": _get(last, "id"),
            "auto": bool(auto)}


def formalization_note(binding: Mapping[str, Any] | None, report: Mapping[str, Any] | None = None,
                       excluded: Sequence[str] | None = None) -> str | None:
    """One line on HITL 1 for the HITL 2 card (``None`` when HITL 1 is off)."""
    if binding is None:
        return None
    status, fp = binding.get("status"), binding.get("ir_fingerprint")
    if status == "not_held":
        return ("Formalization (HITL 1): NOT REVIEWED -- no formalization reached HITL 1 (no ir-builder answer "
                "or validate_ir call); no human-approved IR, so the verdict is not_verified.")
    if status == "rejected":
        return (f"Formalization (HITL 1): the last decision REJECTED the formalization ({fp or 'no IR'}) and no "
                "IR was approved after it; the verdict is not_verified.")
    if status == "no_ir":
        return "Formalization (HITL 1): approved without an IR; no IR to bind the oracle results to: not_verified."
    how = "auto-approved (no human review)" if binding.get("auto") else str(binding.get("decision"))
    line = f"Formalization (HITL 1): {how}, IR {fp}; only oracle results for this IR count."
    rfp = (report or {}).get("ir_fingerprint")
    if rfp and rfp != fp:
        line += f" The report names a DIFFERENT IR ({rfp})."
    if excluded:
        line += f" Results for another IR excluded: {list(excluded)}."
    return line


def report_review_text(report: Mapping[str, Any], evidence: Any = None,
                       state: Mapping[str, Any] | None = None, *, formalization_on: bool = False) -> str:
    """The HITL 2 description: the verdict and the trust labels computed by
    code from the evidence ledger (the model's words do not set them), and
    what HITL 1 decided (``formalization_on``: whether it is on; a report
    whose IR no human saw says so)."""
    from .evidence.policy import evaluate_report

    records = evidence if evidence is not None else list((state or {}).get("tfl_evidence") or [])
    binding = formalization_binding((state or {}).get(STATE_KEY), on=formalization_on)
    lines = ["## HITL 2: report draft", "",
             f"Verdict: «{report.get('verdict')}» (class {report.get('language_class')}"
             + (f", answer {report.get('answer')}" if report.get("answer") else "") + f"). "
             f"Method: {report.get('method')}.", ""]
    try:
        ev = evaluate_report(report, records, formalization=binding)
    except Exception as exc:  # noqa: BLE001 - the review must still be shown
        lines += [f"(trust could not be computed: {type(exc).__name__}: {exc})"]
        return "\n".join(lines)
    lines.append(f"Code-computed (docs/VERDICT_POLICY.md, evidence ledger): verdict trust `{ev['trust']}`, "
                 f"confidence ceiling {ev['confidence_cap']:.2f}; verdict token `{ev['verdict_token']}`.")
    lines.append("Basis: " + (", ".join(ev["basis"]) if ev["basis"] else "no admissible basis") + ".")
    for g in ev["verdict_gate"]:
        lines.append(f"- downgrade: {g}")
    lines += ["", "Steps (trust of the cited evidence, by code):"]
    for i, s in enumerate(ev["steps"], 1):
        title = s.get("title") or s.get("claim") or ""
        note = f"; ids not in the ledger: {s['unknown_ids']}" if s.get("unknown_ids") else ""
        lines.append(f"{i}. {str(title)[:120]} — `{s['trust']}`{note}")
    for i, c in enumerate(ev.get("claims") or [], 1):
        if c.get("unknown_ids"):
            lines.append(f"- claim {i}: ids not in the ledger {c['unknown_ids']} — `{c['trust']}`")
    if ev["unknown_ids"]:
        lines += ["", (f"Cited ids with no record in the evidence ledger: {ev['unknown_ids']} — the claims "
                       "citing them are not_verified.")]
    note = formalization_note(binding, report, ev.get("excluded_ir_mismatch"))
    if note:
        lines += ["", note]
    lines += ["", "Approve it, edit fields, or return it with a comment."]
    return "\n".join(lines)


def report_interrupt_config(evidence: Any = None, *, formalization_on: bool = False) -> dict[str, Any]:
    """``interrupt_on["submit_report"]``: approve / edit / reject, only for a
    report whose fields validate and (with ``evidence``) whose evidence ids are
    all in the ledger (``when``), with the code-made review text
    as the description (``formalization_on``: HITL 1 is on, the card states
    its decision or that none was held)."""

    def when(request: Any) -> bool:
        call = _get(request, "tool_call") or {}
        report = _valid_report(_get(call, "args"))
        if report is None:
            return False
        if evidence is not None:
            from .tools.report import unknown_evidence_ids

            # a report citing ids that are not in the ledger is refused by the tool itself
            # (the model gets the list of valid ids): no stop for it
            return not unknown_evidence_ids(report, evidence)
        return True

    def describe(tool_call: Any, state: Any, runtime: Any) -> str:  # noqa: ARG001
        report = _valid_report(_get(tool_call, "args")) or dict(_get(tool_call, "args") or {})
        return report_review_text(report, evidence, state if isinstance(state, Mapping) else None,
                                  formalization_on=formalization_on)

    return {"allowed_decisions": list(DECISIONS), "when": when, "description": describe}


def execute_interrupt_config() -> dict[str, Any]:
    """``interrupt_on["execute"]`` (``settings.hitl.execute``)."""
    return {"allowed_decisions": list(DECISIONS),
            "description": "The coder asks to run this command in the network-less sandbox."}


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Decision:
    """A human (or automatic) decision on one pending request."""

    type: str                                   # approve | edit | reject
    args: Mapping[str, Any] | None = None       # edit: the fields to change
    comment: str | None = None                  # reject (required), approve / edit (optional)
    source: str = "human"                       # human | auto | dialog
    reviewer: str | None = None

    def __post_init__(self) -> None:
        if self.type not in DECISIONS:
            raise DecisionError(f"unknown decision {self.type!r}; one of {list(DECISIONS)}")


def parse_decision(decision: str, payload: Any = None, *, comment: str | None = None,
                   source: str = "human", reviewer: str | None = None) -> Decision:
    """A :class:`Decision` from the CLI / API form: ``decision`` is approve /
    edit / reject; ``payload`` (a JSON text or an object) is, for edit, the
    fields to change (``{"verdict": ...}`` at HITL 2, ``{"transcription":
    ..., "ir": {...}}`` at HITL 1) and, for reject, the comment (a JSON string
    or ``{"comment": ...}``)."""
    kind = str(decision or "").strip().lower()
    if kind not in DECISIONS:
        raise DecisionError(f"--decision must be one of {list(DECISIONS)}, not {decision!r}")
    if isinstance(payload, str):
        text = payload.strip()
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                if kind != "reject":
                    raise DecisionError(f"--payload is not valid JSON: {exc}") from None
                payload = text  # a plain-text comment
        else:
            payload = None
    args: dict[str, Any] | None = None
    if kind == "edit":
        if not isinstance(payload, Mapping) or not payload:
            raise DecisionError("edit needs --payload with a JSON object of the fields to change")
        args = dict(payload)
        comment = comment or (str(args.pop("comment")) if "comment" in args else None)
    elif isinstance(payload, Mapping):
        comment = comment or payload.get("comment") or payload.get("message")
    elif isinstance(payload, str):
        comment = comment or payload
    if kind == "reject" and not str(comment or "").strip():
        raise DecisionError("reject needs a comment (--comment TEXT or --payload '{\"comment\": ...}'): "
                            "the agent continues with it")
    return Decision(kind, args, str(comment).strip() if comment else None, source, reviewer)


def request_point(request: Any) -> str:
    """``formalization`` / ``report`` / ``execute`` / ``other`` for an interrupt value."""
    names = {_get(a, "name") for a in (_get(request, "action_requests") or [])}
    if FORMALIZATION in names:
        return FORMALIZATION
    if SUBMIT_REPORT in names:
        return REPORT
    if EXECUTE in names:
        return EXECUTE
    return "other"


def _edit_report(original: Mapping[str, Any], changes: Mapping[str, Any],
                 evidence: Any = None) -> tuple[dict[str, Any], list[str]]:
    from pydantic import ValidationError

    from .tools.report import _error_text, evidence_id_error, unknown_evidence_ids, validate_report

    merged = {**dict(original), **dict(changes)}
    try:
        report = validate_report(merged)
    except ValidationError as exc:
        raise DecisionError(f"the edited report does not validate: {_error_text(exc)[:1500]}") from None
    if evidence is not None:
        # submit_report refuses a report citing ids that are not in the ledger; say so now,
        # before the edit is applied, instead of losing the human's edit to that refusal
        unknown = unknown_evidence_ids(report, evidence)
        if unknown:
            raise DecisionError("the edited report " + evidence_id_error(unknown, evidence)["error"]
                                .replace("report NOT accepted (nothing was recorded): ", "is not accepted: ", 1))
    base = _valid_report(original) or dict(original)
    fields = sorted(k for k in changes if base.get(k) != report.get(k))
    return report, fields


def _edit_formalization(original: Mapping[str, Any], changes: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    unknown = sorted(set(changes) - set(FORMALIZATION_EDIT_FIELDS))
    if unknown:
        raise DecisionError(f"HITL 1 edit: unknown field(s) {unknown}; editable: {list(FORMALIZATION_EDIT_FIELDS)}")
    out: dict[str, Any] = {}
    fields: list[str] = []
    if changes.get("transcription") is not None:
        text = str(changes["transcription"]).strip()
        if not text:
            raise DecisionError("HITL 1 edit: the transcription is empty")
        if text != (original.get("transcription") or original.get("task_text")):
            out["transcription"], fields = text, [*fields, "transcription"]
    ir = changes.get("ir")
    if isinstance(ir, str):
        try:
            ir = json.loads(ir)
        except json.JSONDecodeError as exc:
            raise DecisionError(f"HITL 1 edit: the IR is not valid JSON: {exc}") from None
    cls = changes.get("class") or original.get("class")
    if ir is not None:
        problems = _ir_problems(cls, ir)
        if problems:
            raise DecisionError(f"HITL 1 edit: the edited IR does not pass validate_ir ({cls}): "
                                + "; ".join(problems[:10]))
        if ir != original.get("ir"):
            out["ir"], fields = dict(ir), [*fields, "ir"]
    elif changes.get("class") and changes["class"] != original.get("class"):
        raise DecisionError("HITL 1 edit: changing the class needs the IR for it as well")
    if out:
        out["class"] = cls
    return out, fields


def resume_value(request: Mapping[str, Any], decision: Decision, *,
                 evidence: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(HITLResponse, review record)`` for ``decision`` on ``request``.
    Raises :class:`DecisionError` before anything is resumed when the decision
    does not fit (not allowed, an edit that does not validate; with
    ``evidence``, the run's ledger, a report edit citing ids not in it)."""
    actions = list(_get(request, "action_requests") or [])
    if not actions:
        raise DecisionError("the pending interrupt has no action to decide on")
    configs = {_get(c, "action_name"): c for c in _get(request, "review_configs") or []}
    point = request_point(request)
    if decision.type == "edit" and len(actions) != 1:
        raise DecisionError(f"edit needs exactly one pending action, there are {len(actions)}")
    decisions: list[dict[str, Any]] = []
    fields: list[str] = []
    kind = decision.type
    for act in actions:
        name = _get(act, "name")
        allowed = list(_get(configs.get(name), "allowed_decisions") or ["approve", "reject"])
        if kind not in allowed:
            raise DecisionError(f"{kind!r} is not allowed for {name!r} (allowed: {allowed})")
        if kind == "approve":
            decisions.append({"type": "approve"})
        elif kind == "reject":
            decisions.append({"type": "reject", "message": reject_message(point, decision.comment or "")})
        else:
            orig = dict(_get(act, "args") or {})
            if point == REPORT:
                new_args, fields = _edit_report(orig, decision.args or {}, evidence)
            elif point == FORMALIZATION:
                new_args, fields = _edit_formalization(orig, decision.args or {})
            else:
                new_args = {**orig, **dict(decision.args or {})}
                fields = sorted(k for k in (decision.args or {}) if orig.get(k) != new_args.get(k))
            if not fields:
                kind = "approve"  # nothing changed: an approval
                decisions.append({"type": "approve"})
            else:
                decisions.append({"type": "edit", "edited_action": {"name": name, "args": new_args}})
    label = {"approve": "approved", "edit": "edited", "reject": "rejected"}[kind]
    if decision.source == "auto" and kind == "approve":
        label = "auto_approved"
    review = {"point": point, "decision": label, "edited_fields": fields, "comment": decision.comment,
              "source": decision.source, "reviewer": decision.reviewer, "at": _now(),
              "action": [_get(a, "name") for a in actions]}
    if point == FORMALIZATION:
        args0 = dict(_get(actions[0], "args") or {})
        review["ir_fingerprint"] = args0.get("ir_fingerprint")
        edited_args = (decisions[0].get("edited_action") or {}).get("args") or {}
        if edited_args.get("transcription"):
            review["transcription"] = edited_args["transcription"]
        if edited_args.get("ir") is not None:
            from .tools.ir import ir_fingerprint

            review["ir_fingerprint"] = ir_fingerprint(edited_args["ir"])
    return {"decisions": decisions}, review


def reject_message(point: str, comment: str) -> str:
    """The text the model gets for a rejection. HITL 1's middleware frames the
    comment itself; the report's rejection reaches the model as the
    ``submit_report`` error ("User rejected ... with reason: <this>")."""
    comment = comment.strip()
    if point == FORMALIZATION:
        return comment
    if point == REPORT:
        return (f"HITL 2: the human returned the report for revision with this comment: «{comment}». Revise "
                "the report (address every point; re-check with the tools whatever changes) and call "
                "submit_report again.")
    return f"the human rejected this call: «{comment}»"


# ---------------------------------------------------------------------------
# The run directory
# ---------------------------------------------------------------------------

def load_run(run_dir: str | Path) -> dict[str, Any]:
    p = Path(run_dir) / RUN_FILE
    if not p.is_file():
        raise RunError(f"{run_dir}: not a deep-agent run directory (no {RUN_FILE})")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RunError(f"{p}: unreadable: {exc}") from None


def save_run(run_dir: str | Path, run: Mapping[str, Any]) -> Path:
    p = Path(run_dir) / RUN_FILE
    data = {**dict(run), "updated_at": _now()}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    # Windows: replacing a file another process has open for reading (TFL Lab
    # polling the run, an editor, an antivirus scan) fails with a transient
    # sharing violation (WinError 5 / 32); retry briefly instead of failing the run
    for attempt in range(40):
        try:
            tmp.replace(p)
            break
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)
    return p


@dataclasses.dataclass
class PendingInfo:
    """What the human decides on: the interrupt, its point and the card text."""

    run_dir: Path
    interrupt_id: str
    point: str
    request: dict[str, Any]
    card: str


@dataclasses.dataclass
class RunOutcome:
    """The end of one process segment of a run."""

    run_dir: Path
    run_id: str
    status: str                       # awaiting_human | submitted | incomplete | budget_exhausted | error
    exit_code: int
    result: dict[str, Any] | None = None
    pending: PendingInfo | None = None
    paths: dict[str, Path] = dataclasses.field(default_factory=dict)

    @property
    def awaiting(self) -> bool:
        return self.status == STATUS_AWAITING


Dialog = Callable[[PendingInfo], "Decision | None"]


def resume_hint(run_dir: str | Path, point: str) -> str:
    edit = ('\'{"transcription": "...", "ir": {...}}\'' if point == FORMALIZATION
            else '\'{"verdict": "...", "steps": [...]}\'' if point == REPORT else "'{...}'")
    return (f"tfl-deep --resume {run_dir} --decision approve\n"
            f"tfl-deep --resume {run_dir} --decision edit --payload {edit}\n"
            f"tfl-deep --resume {run_dir} --decision reject --comment \"...\"")


def describe_pending(run_dir: str | Path, interrupt_id: str, request: Mapping[str, Any]) -> PendingInfo:
    point = request_point(request)
    parts = [str(_get(a, "description") or "") for a in _get(request, "action_requests") or []]
    if point == REPORT:
        parts.append(f"Draft report: {Path(run_dir) / 'result.md'}")
    elif point not in (FORMALIZATION, REPORT):
        parts += [f"```json\n{json.dumps(_get(a, 'args'), ensure_ascii=False, indent=2, default=str)}\n```"
                  for a in _get(request, "action_requests") or []]
    parts.append("Decide (in a new process):\n\n```\n" + resume_hint(run_dir, point) + "\n```")
    return PendingInfo(Path(run_dir), interrupt_id, point, dict(request), "\n\n".join(p for p in parts if p))


# ---------------------------------------------------------------------------
# The short stop card (TFL Lab: run.pending_point)
# ---------------------------------------------------------------------------

#: a card line is shorter than this
CARD_LINE_MAX = 90
CARD_TITLES = {FORMALIZATION: "HITL 1 · формализация: условие и IR",
               REPORT: "HITL 2 · черновик отчёта",
               EXECUTE: "Запуск кода в песочнице (execute)"}
_CLASS_LABELS = {"reg": "REG", "cfl": "CFL", "dcfl": "DCFL", "ll": "LL(k)"}


def _short(text: Any, limit: int = CARD_LINE_MAX - 1) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def _word_line(label: str, words: Sequence[str] | None, limit: int = CARD_LINE_MAX - 1,
               most: int = 5) -> str | None:
    """``label: w1, w2, ...`` with the shortest words first, at most ``most``
    of them and as many as fit."""
    if not words:
        return None
    ordered = sorted(dict.fromkeys(str(w) for w in words), key=lambda w: (len(w), w))
    line = f"{label}:"
    taken = 0
    for w in ordered[:most]:
        nxt = f"{line}{',' if taken else ''} {w or 'ε'}"
        if len(nxt) > limit:
            break
        line, taken = nxt, taken + 1
    return line if taken else _short(f"{label}: {ordered[0] or 'ε'}", limit)


def _language_line(cls: str | None, ir: Any, restatement: str | None) -> str | None:
    """``Язык: ...`` from the IR itself (a regex compactly), else from the
    ``Язык по IR:`` line of the code-made restatement."""
    if isinstance(ir, Mapping):
        spec = ir.get("language_spec") if isinstance(ir.get("language_spec"), Mapping) else {}
        if spec.get("kind") == "regex" and isinstance(spec.get("pattern"), str):
            try:
                from .tools.ir import alphabet_for

                alphabet = alphabet_for(cls or "reg", ir)
            except Exception:  # noqa: BLE001 - a card line, not a check
                alphabet = []
            over = f" над {{{', '.join(alphabet)}}}" if alphabet else ""
            return _short(f"Язык: regex {spec['pattern']}{over}")
    for line in str(restatement or "").splitlines():
        if line.startswith("Язык по IR:"):
            return _short("Язык: " + line[len("Язык по IR:"):].strip().rstrip("."))
    return None


def _formalization_summary(args: Mapping[str, Any]) -> list[str]:
    cls = args.get("class")
    out = [f"Класс: {_CLASS_LABELS.get(str(cls), cls or '?')}"]
    if args.get("ir") is None:
        out.append(_short(f"IR нет: {args.get('ir_error') or 'no IR'}"))
        return out
    if not args.get("valid"):
        errs = args.get("errors") or []
        out.append(_short("IR не проходит validate_ir: " + (str(errs[0]) if errs else "?")))
    lang = _language_line(cls, args.get("ir"), args.get("restatement"))
    if lang:
        out.append(lang)
    ex = args.get("examples") or {}
    for label, key in (("в L", "in_language"), ("не в L", "not_in_language")):
        line = _word_line(label, ex.get(key))
        if line:
            out.append(line)
    return out[:4]


def _report_summary(args: Mapping[str, Any], draft: Mapping[str, Any] | None) -> list[str]:
    draft = draft or {}
    out = [_short(f"Ответ: {args.get('verdict') or '?'}")]
    if draft.get("trust"):
        cap = draft.get("confidence_cap")
        out.append(_short(f"Доверие: {draft['trust']} · потолок "
                          + (f"{float(cap):.2f}" if isinstance(cap, (int, float)) else "?")))
    out.append(f"Шагов: {len(args.get('steps') or [])} · утверждений: {len(args.get('claims') or [])}")
    if draft.get("unknown_ids"):
        out.append(_short(f"Ссылки без записи в журнале: {len(draft['unknown_ids'])}"))
    return out[:4]


def card_details(request: Mapping[str, Any], point: str | None = None) -> str:
    """The full card of a stop in Markdown (the request's own sections, as in
    ``hitl.md`` without the CLI resume commands)."""
    point = point or request_point(request)
    acts = list(_get(request, "action_requests") or [])
    parts = [str(_get(a, "description") or "") for a in acts]
    if point not in (FORMALIZATION, REPORT):
        parts += [f"```json\n{json.dumps(_get(a, 'args'), ensure_ascii=False, indent=2, default=str)}\n```"
                  for a in acts]
    return "\n\n".join(p for p in parts if p)


def pending_card(request: Mapping[str, Any], *, draft: Mapping[str, Any] | None = None,
                 point: str | None = None) -> dict[str, Any]:
    """The short card of a pending stop, built by code from the request (no
    model call): ``{"point", "title", "summary": [<= 4 lines, each < 90 chars],
    "details_md", "editable": [fields an edit decision accepts]}``. ``draft``:
    the HITL 2 draft's code-computed rating (``run.json`` ``draft``);
    ``point``: the stop's point when known (``run.json`` ``pending.point``)."""
    point = point if point in CARD_TITLES else request_point(request)
    acts = list(_get(request, "action_requests") or [])
    first = dict(_get(acts[0], "args") or {}) if acts else {}
    configs = {_get(c, "action_name"): c for c in _get(request, "review_configs") or []}
    allowed = [d for d in DECISIONS
               if all(d in list(_get(configs.get(_get(a, "name")), "allowed_decisions") or ["approve", "reject"])
                      for a in acts)] if acts else []
    if point == FORMALIZATION:
        summary, editable = _formalization_summary(first), list(FORMALIZATION_EDIT_FIELDS)
    elif point == REPORT:
        from .tools.report import SubmitReportArgs

        summary, editable = _report_summary(first, draft), list(SubmitReportArgs.model_fields)
    else:
        cmd = first.get("command") if isinstance(first.get("command"), str) else None
        summary = [_short(f"Команда: {cmd}")] if cmd else [_short(f"Вызов: {_get(a, 'name')}") for a in acts][:4]
        editable = sorted(first)
    if "edit" not in allowed or len(acts) != 1:
        editable = []
    title = CARD_TITLES.get(point) or _short(f"Остановка: {', '.join(str(_get(a, 'name')) for a in acts)}")
    return {"point": point, "title": title, "summary": [s for s in summary if s][:4],
            "details_md": card_details(request, point), "editable": editable}


def _task_message(task: Any) -> tuple[str, dict[str, Any] | None]:
    """The first message of the run and the state files for a file task."""
    if task.kind == "text":
        return task.text or "", None
    what = "PDF" if task.kind == "pdf" else "image"
    pages = f", {task.pages} page(s)" if task.pages else ""
    text = (f"The task is given as a file: `{task.input_path}` ({what}{pages}, original name «{task.name}»). "
            f"Do not read the file yourself: delegate the transcription to the `{INTAKE}` role (task tool, "
            f"subagent_type \"{INTAKE}\", give it the path). Then formalize the transcribed task with "
            f"`{IR_BUILDER}`; the human reviews the transcription and the IR together (HITL 1). If the file holds "
            "several tasks, ask for nothing: formalize the first one and say so; the human picks at HITL 1.")
    return text, {task.input_path: task.to_file_data()}


def _store_input(run_dir: Path, task: Any) -> str | None:
    if task.kind == "text":
        return None
    ext = Path(task.input_path).suffix
    d = run_dir / INPUT_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"task{ext}").write_bytes(task.data)
    return f"{INPUT_DIR}/task{ext}"


def start_run(
    run_dir: str | Path,
    task: Any,
    *,
    settings: Any = None,
    mock_script: str | Path | Mapping[str, Any] | None = None,
    model_factory: Callable[[str, str], Any] | None = None,
    live: bool = False,
    template: str | None = None,
    auto_approve: bool = False,
    dialog: Dialog | None = None,
    hitl: Any = True,
    run_id: str | None = None,
    review_mode: str | None = None,
) -> RunOutcome:
    """Start a run in ``run_dir`` (created; it must not hold a run yet).

    ``task``: a text or an ``intake.files.TaskInput``. The model comes from
    ``mock_script`` (mock mode, the default way), ``model_factory`` (a custom
    factory; pass it again to :func:`resume_run`) or ``live=True``
    (``agent.default_model_factory``; real API calls). ``hitl``: ``True`` =
    ``settings.hitl``, a mapping of points, or ``False``. ``review_mode``
    (:data:`REVIEW_MODES`; ``auto_approve=True`` is an alias of ``auto``):
    which enabled points wait for a human. Returns at the end of the run or at
    the first stop nobody answered here."""
    from . import agent as agent_mod
    from .intake import files as intake_files

    d = Path(run_dir)
    if (d / RUN_FILE).exists():
        raise RunError(f"{d} already holds a run ({RUN_FILE}); resume it with --resume or use a new directory")
    modes = [mock_script is not None, model_factory is not None, bool(live)]
    if sum(modes) != 1:
        raise RunError("give exactly one of mock_script, model_factory or live=True")
    mode_of_review = resolve_review_mode(review_mode, auto_approve)
    task_input = task if hasattr(task, "kind") and hasattr(task, "input_path") else intake_files.from_text(str(task))
    cfg = agent_mod.settings_dict(settings)
    points = agent_mod.hitl_points(cfg, hitl)
    d.mkdir(parents=True, exist_ok=True)
    rid = run_id or d.name or uuid.uuid4().hex[:12]

    # a live factory passed as model_factory is a live run: the per-day budget,
    # the shared spend log and --live on resume apply to it as well
    live_factory = model_factory is not None and agent_mod.is_live_factory(model_factory)
    mode = "live" if live or live_factory else ("mock" if mock_script is not None else "custom")
    script_copy = None
    if mock_script is not None:
        script = agent_mod.load_mock_script(mock_script)
        (d / MOCK_SCRIPT_FILE).write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
        script_copy = MOCK_SCRIPT_FILE
        template = template or script.get("template")
    template = template or "full_proof"
    text, files = _task_message(task_input)
    stored = _store_input(d, task_input)
    source = {**task_input.metadata(), "stored_as": stored} if task_input.kind != "text" else None
    run: dict[str, Any] = {
        "v": RUN_SCHEMA, "run_id": rid, "thread_id": rid, "created_at": _now(), "status": "running",
        "mode": mode, "factory": "custom" if model_factory is not None else None,
        "mock_script": script_copy, "template": template,
        "task_text": task_input.text if task_input.kind == "text" else None,
        "message": text, "input": source, "settings": cfg, "hitl": points,
        "review_mode": mode_of_review, "initial_review_mode": mode_of_review,
        "auto_approve": mode_of_review == REVIEW_AUTO,
        "reviews": [], "pending": None, "budget": {"spent_usd": 0.0, "records": []},
        "limits": {"model_calls": 0, "tool_calls": 0}, "segments": [], "result": None, "error": None,
    }
    save_run(d, run)
    from langchain_core.messages import HumanMessage

    inputs: dict[str, Any] = {"messages": [HumanMessage(content=text)]}
    if files:
        inputs["files"] = files
    return _segment(d, run, inputs, model_factory=model_factory, dialog=dialog, resumed_with=None)


def resume_run(
    run_dir: str | Path,
    decision: Decision,
    *,
    model_factory: Callable[[str, str], Any] | None = None,
    live: bool = False,
    dialog: Dialog | None = None,
    approve_rest: bool = False,
) -> RunOutcome:
    """Resume a run waiting at a HITL stop with ``decision``
    (``Command(resume=...)``; usually in a new process). The decision is
    checked against the pending request first; a misfit raises
    :class:`DecisionError` and leaves the run as it was. A live run needs
    ``live=True`` again (it spends money); a mock run refuses it.

    ``approve_rest`` (only with the decision ``approve``): this stop is approved
    as the human's decision, and the run switches to review mode ``auto``, so
    every later stop is auto-approved (recorded as ``auto``). It is stored in
    ``run.json`` (``review_mode``, ``approve_rest``), so a later process keeps it."""
    from langgraph.types import Command

    if approve_rest and decision.type != "approve":
        raise DecisionError(f"approve_rest goes only with the decision 'approve', not {decision.type!r}")
    d = Path(run_dir)
    run = load_run(d)
    if run.get("status") != STATUS_AWAITING or not run.get("pending"):
        raise RunError(f"{d}: the run is {run.get('status')!r}, not {STATUS_AWAITING!r}; nothing to resume")
    mode = run.get("mode")
    if mode == "live" and not live:
        raise RunError(f"{d} is a live run: resuming it makes real API calls; pass --live to confirm")
    if mode != "live" and live:
        raise RunError(f"{d} is a {mode} run; --live does not apply")
    if (mode == "custom" or run.get("factory") == "custom") and model_factory is None:
        raise RunError(f"{d} was started with a custom model factory; pass it to resume_run again")
    if mode != "live" and model_factory is not None:
        from . import agent as agent_mod

        if agent_mod.is_live_factory(model_factory):
            raise RunError(f"{d} is a {mode} run; a live model factory cannot resume it")
    pending = run["pending"]
    value, review = resume_value(pending["request"], decision, evidence=_run_evidence(d))
    review["interrupt_id"] = pending["interrupt_id"]
    if approve_rest:
        review["approve_rest"] = True
        run.setdefault("initial_review_mode", review_mode_of(run))
        run["review_mode"] = REVIEW_AUTO
        run["auto_approve"] = True
        run["approve_rest"] = {"at": review["at"], "point": review["point"],
                               "interrupt_id": pending["interrupt_id"], "source": decision.source,
                               "reviewer": decision.reviewer}
    run["reviews"].append(review)
    run["pending"] = None
    run["status"] = "running"
    save_run(d, run)
    return _segment(d, run, Command(resume={pending["interrupt_id"]: value}), model_factory=model_factory,
                    dialog=dialog, resumed_with=review)


def _budget_ledger(run: Mapping[str, Any], settings_obj: Any, factory: Any = None) -> Any:
    from . import agent as agent_mod
    from .middleware import budget as budget_mod

    prev = run.get("budget") or {}
    if run.get("mode") == "live" or agent_mod.is_live_factory(factory):
        # the current segment is already in the list: an earlier one means a resume
        try:
            led = budget_mod.BudgetLedger.for_task(settings_obj, task_id=run["run_id"],
                                                   resume=len(run.get("segments") or []) > 1)
        except budget_mod.BudgetExceeded as exc:
            raise RunError(f"budget: {exc}") from None
    else:
        b = settings_obj.budget
        led = budget_mod.BudgetLedger(
            b.per_task_usd, reserve_for_report_usd=b.report_reserve_usd,
            per_subagent_max_usd=b.per_subagent_max_usd, spend_log=None, task_id=run["run_id"],
            already_spent_usd=float(prev.get("spent_usd") or 0.0))
    fields = {f.name for f in dataclasses.fields(budget_mod.CallRecord)}
    led.records.extend(budget_mod.CallRecord(**{k: v for k, v in r.items() if k in fields})
                       for r in prev.get("records") or [])
    return led


def _factory(d: Path, run: Mapping[str, Any], model_factory: Any) -> Any:
    from . import agent as agent_mod

    if run.get("mode") == "mock":
        return agent_mod.scripted_model_factory(d / str(run["mock_script"]), state_path=d / MOCK_CURSORS_FILE)
    if run.get("mode") == "live":
        return model_factory if model_factory is not None else agent_mod.default_model_factory
    return model_factory


def _open_checkpointer(d: Path) -> tuple[Any, Any]:
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(d / CHECKPOINT_FILE), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver, conn


def run_state(run_dir: str | Path) -> dict[str, Any]:
    """The main thread's state values at the run's latest checkpoint, read by
    a fresh agent on ``checkpoints.sqlite`` (no model is called: the models
    are empty replays, the budget has no spend log). For the UI and tests."""
    from . import agent as agent_mod
    from . import settings as settings_mod
    from .evidence.ledger import EvidenceLedger
    from .middleware import budget as budget_mod

    d = Path(run_dir)
    run = load_run(d)
    settings_obj = settings_mod.validate_settings(json.loads(json.dumps(run["settings"])))
    cls = agent_mod.scripted_model_class()

    def factory(role_key: str, model_id: str) -> Any:
        return cls(model=model_id, role=role_key, replies=[], defs={})

    b = settings_obj.budget
    budget = budget_mod.BudgetLedger(b.per_task_usd, reserve_for_report_usd=b.report_reserve_usd,
                                     spend_log=None, task_id=run["run_id"])
    saver, conn = _open_checkpointer(d)
    try:
        graph = agent_mod.create_tfl_agent(
            settings_obj, model_factory=factory, template=run["template"], checkpointer=saver, budget=budget,
            evidence=EvidenceLedger.open(d, run_id=run["run_id"]), hitl=run["hitl"],
            task_source=run.get("input"))
        return dict(graph.get_state({"configurable": {"thread_id": run["thread_id"]}}).values)
    finally:
        conn.close()


def _pending_interrupts(graph: Any, config: Mapping[str, Any]) -> list[Any]:
    snap = graph.get_state(config)
    found = list(getattr(snap, "interrupts", ()) or ())
    if not found:
        for t in getattr(snap, "tasks", ()) or ():
            found.extend(getattr(t, "interrupts", ()) or ())
    return found


def _segment(d: Path, run: dict[str, Any], value: Any, *, model_factory: Any, dialog: Dialog | None,
             resumed_with: Mapping[str, Any] | None) -> RunOutcome:
    """One process segment: build the agent on the run's checkpoint, run
    until the end or an unanswered stop, write ``run.json`` (and the report)."""
    from langgraph.types import Command

    from . import agent as agent_mod
    from . import settings as settings_mod
    from .evidence.ledger import EvidenceLedger

    cfg = run["settings"]
    settings_obj = settings_mod.validate_settings(json.loads(json.dumps(cfg)))
    segment = {"started_at": _now(), "resumed_with": dict(resumed_with) if resumed_with else None}
    run["segments"].append(segment)
    factory = _factory(d, run, model_factory)
    try:
        budget = _budget_ledger(run, settings_obj, factory)
    except RunError as exc:
        run["segments"].pop()
        run["status"], run["error"] = "error", str(exc)
        save_run(d, run)
        raise
    evidence = EvidenceLedger.open(d, run_id=run["run_id"])
    from .progress import open_emitter  # progress.jsonl for the TFL Lab live view (never raises)

    # the checkpointer first: if it cannot be opened, the segment raises before the emitter
    # writes `segment_start` and attaches its listeners (both are closed only in the finally below)
    saver, conn = _open_checkpointer(d)
    progress = open_emitter(d, run, segment, budget=budget, evidence=evidence)
    config = {"configurable": {"thread_id": run["thread_id"]}}
    error: str | None = None
    pending: PendingInfo | None = None
    graph = None
    try:
        graph = agent_mod.create_tfl_agent(
            settings_obj, model_factory=factory, template=run["template"], checkpointer=saver, budget=budget,
            evidence=evidence, hitl=run["hitl"], task_source=run.get("input"))
        limits = getattr(graph, "tfl_limits", None)
        if limits is not None:
            limits.model_calls = int(run["limits"].get("model_calls") or 0)
            limits.tool_calls = int(run["limits"].get("tool_calls") or 0)
        while True:
            try:
                graph.invoke(value, config)
            except Exception as exc:  # noqa: BLE001 - any agent failure still yields a partial result
                prefix = "mock script" if isinstance(exc, agent_mod.ScriptError) else type(exc).__name__
                error = f"{prefix}: {exc}"
                break
            interrupts = _pending_interrupts(graph, config)
            if not interrupts:
                break
            intr = interrupts[0]
            request = intr.value if isinstance(intr.value, Mapping) else {"value": intr.value}
            info = describe_pending(d, intr.id, request)
            auto = auto_approves(review_mode_of(run), info.point)
            decision = Decision("approve", source="auto") if auto else None
            if decision is None:
                _write_awaiting(d, run, info, graph, config, budget, evidence)
                progress.hitl_awaiting(info, run)
                decision = _ask(dialog, info, evidence=evidence)
                if decision is None:
                    pending = info
                    break
            resume, review = resume_value(request, decision, evidence=evidence)
            review["interrupt_id"] = intr.id
            run["reviews"].append(review)
            run["pending"] = None
            run["status"] = "running"
            save_run(d, run)
            progress.hitl_decision(review)
            value = Command(resume={intr.id: resume})
    except Exception as exc:  # noqa: BLE001 - a build / checkpoint failure ends the segment as an error
        error = f"{type(exc).__name__}: {exc}"
    finally:
        limits = getattr(graph, "tfl_limits", None) if graph is not None else None
        if limits is not None:
            run["limits"] = {"model_calls": limits.model_calls, "tool_calls": limits.tool_calls}
        run["budget"] = {"spent_usd": budget.spent_usd, "limit_usd": budget.root.limit,
                         "records": [r.to_dict() for r in budget.records]}
        segment["ended_at"] = _now()

    paths: dict[str, Path] = {}
    try:
        if pending is not None:
            run["status"] = STATUS_AWAITING
            segment["status"] = STATUS_AWAITING
            save_run(d, run)
            progress.segment_end(STATUS_AWAITING)
            return RunOutcome(d, run["run_id"], STATUS_AWAITING, EXIT_AWAITING, pending=pending,
                              result=run.get("draft"), paths={"run": d / RUN_FILE, "card": d / CARD_FILE})
        state: dict[str, Any] = {"messages": []}
        if graph is not None:
            try:
                state = dict(graph.get_state(config).values)
            except Exception as exc:  # noqa: BLE001 - the result is still written from an empty state
                error = error or f"cannot read the final state: {type(exc).__name__}: {exc}"
        result = final_result(d, run, state, budget, evidence, error=error)
        paths = _write_result(d, run, result, budget, evidence)
        run["status"] = result["status"]
        run["error"] = error
        run["result"] = {"status": result["status"], "paths": {k: str(v) for k, v in paths.items()},
                         "verdict": (result.get("report") or {}).get("verdict"), "trust": result.get("trust"),
                         "confidence_cap": result.get("confidence_cap")}
        segment["status"] = result["status"]
        run.pop("draft", None)
        save_run(d, run)
        progress.done(result, error)
        code = EXIT_SUBMITTED if result["status"] == "submitted" else EXIT_NO_REPORT
        return RunOutcome(d, run["run_id"], result["status"], code, result=result, paths=paths)
    finally:
        progress.detach()
        conn.close()


def _run_evidence(d: Path) -> Any:
    """The run's evidence ledger (``evidence.jsonl``; read, nothing written)."""
    from .evidence.ledger import EvidenceLedger

    try:
        return EvidenceLedger.open(d)
    except Exception:  # noqa: BLE001 - an unreadable ledger: ids are checked again by the tool
        return None


def _ask(dialog: Dialog | None, info: PendingInfo, *, evidence: Any = None) -> Decision | None:
    """Ask ``dialog`` until it gives a decision that fits (or ``None``)."""
    if dialog is None:
        return None
    while True:
        decision = dialog(info)
        if decision is None:
            return None
        try:
            resume_value(info.request, decision, evidence=evidence)
        except DecisionError as exc:
            notify = getattr(dialog, "notify", None)
            if callable(notify):
                notify(f"not accepted: {exc}")
                continue
            raise
        return decision


def review_summary(run: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(human_review label, review mapping for the renderer)`` from the run's
    HITL 2 history."""
    reports = [r for r in run.get("reviews") or [] if r.get("point") == REPORT]
    if not reports:
        if not (run.get("hitl") or {}).get(REPORT):
            return "not reviewed (HITL 2 is off in settings)", {"decision": "not_reviewed"}
        return "not reviewed", {"decision": "not_reviewed"}
    last = reports[-1]
    mapping = {"decision": last.get("decision"), "edited_fields": list(last.get("edited_fields") or []),
               "comment": last.get("comment"), "reviewer": last.get("reviewer"), "at": last.get("at")}
    if last.get("decision") == "auto_approved":
        return AUTO_APPROVED_LABEL, mapping
    if last.get("decision") == "approved":
        return "approved by a human at HITL 2", mapping
    if last.get("decision") == "edited":
        return ("approved with edits by a human at HITL 2 (fields: "
                + ", ".join(mapping["edited_fields"]) + ")"), mapping
    return f"not reviewed: the last draft was {last.get('decision')} at HITL 2", mapping


def run_binding(run: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any] | None:
    """:func:`formalization_binding` of a run (its points, its state records).

    ``auto`` is set from the run's review history, not from the run's mode: an
    approved IR counts as human-approved only when a human (source ``human`` /
    ``dialog``) approved or edited that IR fingerprint at HITL 1. An
    auto-approved formalization (review mode ``auto``, ``--auto-approve``) is
    ``auto`` even after a later ``approve_rest``; a human approval stays human
    in review mode ``formalization``."""
    binding = formalization_binding(state.get(STATE_KEY), on=bool((run.get("hitl") or {}).get(FORMALIZATION)),
                                    auto=review_mode_of(run) == REVIEW_AUTO)
    if binding is not None and binding.get("status") == "approved":
        fp = binding.get("ir_fingerprint")
        human = any(r.get("point") == FORMALIZATION and r.get("source") != "auto"
                    and r.get("decision") in ("approved", "edited") and r.get("ir_fingerprint") == fp
                    for r in run.get("reviews") or [])
        binding["auto"] = not human
    return binding


def _hitl_block(run: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
    reviews = list(run.get("reviews") or [])
    form = [r for r in reviews if r.get("point") == FORMALIZATION]
    held = [r for r in state.get(STATE_KEY) or [] if r.get("point") == FORMALIZATION]
    return {
        "points": dict(run.get("hitl") or {}),
        "auto_approve": bool(run.get("auto_approve")),
        "review_mode": review_mode_of(run),
        "initial_review_mode": run.get("initial_review_mode") or review_mode_of(run),
        "approve_rest": run.get("approve_rest"),
        "reviews": reviews,
        "formalization": (form[-1].get("decision") if form
                          else ("not held (no ir-builder answer or validate_ir reached HITL 1)"
                                if (run.get("hitl") or {}).get(FORMALIZATION) else "off")),
        "formalization_records": held,
        "report_rejections": sum(1 for r in reviews if r.get("point") == REPORT and r.get("decision") == "rejected"),
    }


def _task_text(run: Mapping[str, Any], state: Mapping[str, Any]) -> str:
    """The task as the report states it: the text as the human corrected it at
    HITL 1, else the given text, else (a file) the intake transcription."""
    for r in reversed(run.get("reviews") or []):
        if r.get("point") == FORMALIZATION and r.get("transcription"):
            return str(r["transcription"])
    if run.get("task_text"):
        return str(run["task_text"])
    messages = list(state.get("messages") or [])
    intake = _subagent_answers(messages, _task_calls(messages), INTAKE) if messages else []
    if intake:
        return _text_of(intake[-1])
    src = run.get("input") or {}
    return f"(file {src.get('name')}: {src.get('kind')}; no transcription)"


def _result_common(d: Path, run: Mapping[str, Any], result: dict[str, Any], budget: Any,
                   state: Mapping[str, Any]) -> dict[str, Any]:
    result["run_id"] = run["run_id"]
    result["mode"] = "mock" if run.get("mode") == "mock" else run.get("mode")
    result["template"] = run.get("template")
    result["task"] = _task_text(run, state)
    if run.get("input"):
        result["input"] = run["input"]
    result["budget_refusal"] = result.pop("budget", None)
    if not result["budget_refusal"]:
        result.pop("budget_refusal")
    result["budget"] = {**budget.summary(), "note": (
        "live: charged to the per-day log" if run.get("mode") == "live"
        else "mock: token estimates of the scripted replies; no money spent")}
    result["hitl"] = _hitl_block(run, state)
    result["run_dir"] = str(d)
    return result


def final_result(d: Path, run: Mapping[str, Any], state: Mapping[str, Any], budget: Any, evidence: Any, *,
                 error: str | None = None) -> dict[str, Any]:
    """The run's result dict (``agent.collect_result`` over the evidence
    ledger, plus run / HITL / budget fields)."""
    from . import agent as agent_mod

    label, _ = review_summary(run)
    result = agent_mod.collect_result(state, human_review=label, ledger=budget, evidence=evidence,
                                      formalization=run_binding(run, state))
    result = _result_common(d, run, result, budget, state)
    if error:
        result["status"] = "error"
        result["reason"] = error
        if result.get("partial_report"):
            result["partial_report"]["status"] = "error"
            result["partial_report"]["reason"] = error
    return result


def _budget_view(budget: Any) -> dict[str, Any]:
    return {**budget.summary(), "records": [r.to_dict() for r in budget.records]}


def _write_result(d: Path, run: Mapping[str, Any], result: Mapping[str, Any], budget: Any,
                  evidence: Any) -> dict[str, Path]:
    from .reports.render import write_report

    _, review = review_summary(run)
    return write_report(d, result, evidence=evidence, budget=_budget_view(budget), task_text=result.get("task"),
                        human_review=review)


def _write_awaiting(d: Path, run: dict[str, Any], info: PendingInfo, graph: Any, config: Mapping[str, Any],
                    budget: Any, evidence: Any) -> None:
    """``run.json`` with the pending request, ``hitl.md``, and at HITL 2 the
    draft report (``result.*`` rendered with status ``awaiting_human``)."""
    run["status"] = STATUS_AWAITING
    run["pending"] = {"interrupt_id": info.interrupt_id, "point": info.point, "request": info.request,
                      "asked_at": _now()}
    run["budget"] = {"spent_usd": budget.spent_usd, "limit_usd": budget.root.limit,
                     "records": [r.to_dict() for r in budget.records]}
    limits = getattr(graph, "tfl_limits", None)
    if limits is not None:
        run["limits"] = {"model_calls": limits.model_calls, "tool_calls": limits.tool_calls}
    (d / CARD_FILE).write_text(info.card + "\n", encoding="utf-8")
    if info.point == REPORT:
        from . import agent as agent_mod
        from .reports.render import write_report

        state = dict(graph.get_state(config).values)
        args = (info.request.get("action_requests") or [{}])[0].get("args") or {}
        draft = agent_mod.collect_result(state, human_review="awaiting human review (HITL 2)", ledger=budget,
                                         evidence=evidence, pending_report=_valid_report(args) or dict(args),
                                         formalization=run_binding(run, state))
        draft = _result_common(d, run, draft, budget, state)
        write_report(d, draft, evidence=evidence, budget=_budget_view(budget), task_text=draft.get("task"),
                     human_review={"decision": "awaiting"}, status=STATUS_AWAITING)
        run["draft"] = {"verdict": (draft.get("report") or {}).get("verdict"), "trust": draft.get("trust"),
                        "confidence_cap": draft.get("confidence_cap"), "unknown_ids": draft.get("unknown_ids")}
    try:
        run["pending"]["card"] = pending_card(info.request, draft=run.get("draft") if info.point == REPORT else None,
                                              point=info.point)
    except Exception as exc:  # noqa: BLE001 - the long card (hitl.md) is still there
        run["pending"]["card"] = {"point": info.point, "title": CARD_TITLES.get(info.point, info.point),
                                  "summary": [_short(f"(card not built: {type(exc).__name__})")],
                                  "details_md": card_details(info.request), "editable": []}
    save_run(d, run)


# ---------------------------------------------------------------------------
# Interactive dialog (CLI, attached to a TTY)
# ---------------------------------------------------------------------------

class TTYDialog:
    """The CLI's interactive dialog: show the card, ask approve / edit /
    reject / later. ``input_fn`` / ``print_fn`` are injectable for tests."""

    def __init__(self, *, input_fn: Callable[[str], str] = input, print_fn: Callable[[str], None] = print,
                 reviewer: str | None = None) -> None:
        self.input = input_fn
        self.print = print_fn
        self.reviewer = reviewer

    def notify(self, text: str) -> None:
        self.print(text)

    def _multiline(self, prompt: str) -> str:
        self.print(prompt + " (finish with a line holding only '.'; an empty first line keeps it)")
        lines: list[str] = []
        while True:
            line = self.input("")
            if line.strip() == "." or (not lines and line == ""):
                break
            lines.append(line)
        return "\n".join(lines).strip()

    def _json_or_file(self, prompt: str) -> Any:
        text = self.input(prompt).strip()
        if not text:
            return None
        if text.startswith("@"):
            text = Path(text[1:]).read_text(encoding="utf-8")
        return json.loads(text)

    def __call__(self, info: PendingInfo) -> Decision | None:
        self.print(info.card)
        while True:
            choice = self.input("[a]pprove / [e]dit / [r]eject / [l]ater: ").strip().lower()[:1]
            if choice == "a":
                return Decision("approve", source="dialog", reviewer=self.reviewer)
            if choice == "l" or choice == "q":
                return None
            if choice == "r":
                comment = self._multiline("Comment for the agent")
                if comment:
                    return Decision("reject", comment=comment, source="dialog", reviewer=self.reviewer)
                self.print("a rejection needs a comment")
                continue
            if choice == "e":
                try:
                    if info.point == FORMALIZATION:
                        changes: dict[str, Any] = {}
                        text = self._multiline("Corrected task text")
                        if text:
                            changes["transcription"] = text
                        ir = self._json_or_file("IR JSON (one line, or @file.json; empty keeps it): ")
                        if ir is not None:
                            changes["ir"] = ir
                    else:
                        changes = self._json_or_file("Fields to change as a JSON object (or @file.json): ") or {}
                except (OSError, json.JSONDecodeError) as exc:
                    self.print(f"not read: {exc}")
                    continue
                if not changes:
                    self.print("nothing changed")
                    continue
                return Decision("edit", args=changes, source="dialog", reviewer=self.reviewer)


def copy_input(path: str | Path, run_dir: str | Path) -> Path:  # pragma: no cover - convenience for the UI
    """Copy a task file into ``run_dir/input`` (the UI keeps the original next to the run)."""
    d = Path(run_dir) / INPUT_DIR
    d.mkdir(parents=True, exist_ok=True)
    dst = d / Path(path).name
    shutil.copyfile(path, dst)
    return dst


__all__ = [
    "AUTO_APPROVED_LABEL", "CARD_FILE", "CHECKPOINT_FILE", "DECISIONS", "EXIT_AWAITING", "EXIT_NO_REPORT",
    "EXIT_SUBMITTED", "FORMALIZATION", "REPORT", "REVIEW_ALL", "REVIEW_AUTO", "REVIEW_FORMALIZATION",
    "REVIEW_MODES", "RUN_FILE", "STATUS_AWAITING", "auto_approves", "card_details", "pending_card",
    "resolve_review_mode", "review_mode_of",
    "Decision", "DecisionError", "FormalizationReviewMiddleware", "HitlState", "PendingInfo", "RunError",
    "RunOutcome", "TTYDialog", "describe_pending", "execute_interrupt_config", "final_result", "find_ir",
    "formalization_binding", "formalization_card", "formalization_note", "formalization_request", "ir_checks", "load_run", "merge_reviews", "parse_decision",
    "report_interrupt_config", "report_review_text", "request_point", "resume_run", "resume_value",
    "review_summary", "run_binding", "save_run", "start_run",
]
