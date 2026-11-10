"""The budget stop: before the main agent's call that would enter wrap-up or be
refused, the run asks a human for more money or to finish with what is left.

The interrupt has the ``HITLRequest`` shape, point ``budget``. On the wire the
decisions are the usual ones: ``edit`` with ``{"add_usd": X}`` adds money and is
recorded as ``extended``; ``approve`` finishes with what is left and is recorded as
``declined``. The answer is stored in ``run.json`` (``budget_limit_usd``,
``budget_extensions``, ``budget_declined``), so a later process builds its ledger
with it.

Whether a run asks is ``run.json`` ``budget_stop``, fixed when the run starts and the
authority on it. A run started with ``auto_approve`` has review mode ``auto`` and no
budget stop; review mode ``auto`` chosen on its own and ``approve_rest`` concern reviews,
not money, so they still ask, and neither of the two switches is taken at this stop
(:func:`review_switch_refusal`). A run with no human to ask (review mode ``auto`` on a
terminal with no dialog) waits here too: the CLI exits 3.

A follow-up on a run that holds an accepted report, when the DAY's remainder is what is
short (user decision 2026-11-10, CA-015), asks the same question about the day limit
(``binds`` ``day`` on the card): ``edit`` raises ``per_day_usd`` of this run by ``add_usd``
(``budget_day_limit_usd`` in ``run.json``; the settings file is not touched), ``approve``
leaves the report, and the raise holds only on the day it was given. With ``--auto-approve``
(no budget stop) the call is skipped (below), never wrapped up; with the stop on and no
dialog the run waits (exit 3) as at the task stop.

A follow-up on a run that holds an accepted report does not spend money the human did
not give: declining, or a top-up too small for the call, ends the message without a
model call (:func:`declining_skips_the_call`). The report stays. A follow-up with no
accepted report keeps the wrap-up, because that is how it gets its first report.

Imports without the ``[deep]`` extra; what needs LangGraph or the ledger is imported
where it is used.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .run_store import DecisionError, first_decision, get_field, now

if TYPE_CHECKING:
    from .middleware.budget import BudgetLow, DailySpendLog

#: the interrupt's action name and the stop's point
BUDGET = "budget"
#: what ``resume_value`` records for a decision at this stop
EXTENDED, DECLINED = "extended", "declined"
#: ``BudgetLow.binds`` / the card's ``binds`` when the day's limit is what is short
DAY = "day"


def format_usd(value: Any) -> str:
    x = float(value or 0.0)
    return f"${x:.2f}" if abs(x) >= 0.1 else f"${x:.4f}"


def budget_request(low: BudgetLow) -> dict[str, Any]:
    """The interrupt value of the budget stop."""
    args = {k: (None if v is None else round(float(v), 6)) for k, v in dataclasses.asdict(low).items()
            if k not in ("binds", "per_day_usd")}
    if low.binds == DAY:
        args.update(binds=DAY, per_day_usd=round(float(low.per_day_usd or 0.0), 6))
        text = (f"Дневной лимит заканчивается: сегодня потрачено {format_usd(args['per_day_usd'] - args['day_left_usd'])} "
                f"из {format_usd(args['per_day_usd'])}, осталось {format_usd(args['day_left_usd'])}. "
                "Сообщение без денег не будет отвечено, принятый отчёт останется. "
                "Поднять дневной лимит на сумму или оставить отчёт как есть?")
        return {"action_requests": [{"name": BUDGET, "args": args, "description": text}],
                "review_configs": [{"action_name": BUDGET, "allowed_decisions": ["approve", "edit"]}]}
    day = "" if args["day_left_usd"] is None else f" На сегодня осталось {format_usd(args['day_left_usd'])}."
    text = (f"Бюджет задачи заканчивается: потрачено {format_usd(args['spent_usd'])} из {format_usd(args['limit_usd'])}, "
            f"доступно {format_usd(args['available_usd'])}.{day} Добавить денег или завершить с тем, что есть?")
    return {"action_requests": [{"name": BUDGET, "args": args, "description": text}],
            "review_configs": [{"action_name": BUDGET, "allowed_decisions": ["approve", "edit"]}]}


def request_budget_extension(low: BudgetLow) -> tuple[str, float] | None:
    """``BudgetMiddleware.request_budget_extension`` of a run with the budget stop: a
    LangGraph interrupt. Returns ``(kind, new absolute limit)`` with ``kind`` ``"day"`` or
    ``"task"``, or ``None`` (finish).

    The kind is the one the decision carries, not ``low.binds``: when a node asks twice
    (one answer for the day, then a question about the task, or the other way round) the
    re-run node gets the stored answer of the EARLIER question back from ``interrupt``.
    Applying that raise again is harmless (limits only grow); the call is then admitted
    or skipped and the next message asks again.

    A resumed node runs again from its start. The resume has raised the limit in
    ``run.json`` by then, so the condition is false and this is not reached; the
    same process's dialog applies it to the ledger just the same. If the new limit is
    still too low, ``interrupt`` returns this same answer and the call goes on to
    wrap-up: no second question in one node execution."""
    from langgraph.types import interrupt

    decision = first_decision(interrupt(budget_request(low)))
    if decision.get("type") != "edit":
        return None
    args = get_field(decision.get("edited_action"), "args")
    if args.get("new_day_limit_usd") is not None:
        return DAY, float(args["new_day_limit_usd"])
    return "task", float(args["new_limit_usd"])


def budget_stop_on(run: Mapping[str, Any]) -> bool:
    """Whether this run asks for money at all. A run written before ``budget_stop`` existed
    asks unless it started in review mode ``auto`` (``initial_review_mode``; what a later
    switch left in ``review_mode`` concerns reviews)."""
    stop = run.get("budget_stop")
    return run.get("initial_review_mode") != "auto" if stop is None else bool(stop)


def budget_stop_enabled(run: Mapping[str, Any]) -> bool:
    """Whether this run's next segment may ask for money: a human who chose to finish
    is not asked again in a later process of the same message."""
    return budget_stop_on(run) and not run.get("budget_declined")


def declining_skips_the_call(run: Mapping[str, Any]) -> bool:
    """Whether a main-agent call the money does not cover ends this segment with no
    model call, instead of wrap-up: a follow-up of a run that holds an accepted report,
    whether or not the run has a budget stop (user decision 2026-11-10, also for
    ``--auto-approve``): the accepted report is never replaced by a truncated one."""
    record = run.get("followup_in_progress")
    return bool(record) and record.get("previous_status") == "submitted"


def review_switch_refusal(point: str | None, switch: str = "approve_rest") -> str | None:
    """Why ``switch`` (``approve_rest``, ``auto_approve``) cannot be taken at ``point``,
    or ``None``: the switches concern reviews, and this stop is about money."""
    if point == BUDGET:
        return f"{switch} is not for the budget stop: it concerns reviews, not money"
    return None


def parse_add_usd(args: Mapping[str, Any] | None) -> float:
    """The dollars of an ``edit`` decision; :class:`DecisionError` when they do not fit."""
    fields = dict(args or {})
    unknown = sorted(set(fields) - {"add_usd"})
    if unknown:
        raise DecisionError(f"budget stop: unknown field(s) {unknown}; an edit takes only add_usd")
    raw = fields.get("add_usd")
    try:
        if isinstance(raw, bool):
            raise TypeError
        add = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise DecisionError(f"budget stop: add_usd must be a number of dollars, not {raw!r}") from None
    if not math.isfinite(add) or add <= 0:
        raise DecisionError(f"budget stop: add_usd must be greater than 0, not {raw!r}")
    return add


def budget_decision_response(action: Any, decision: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(HITLResponse, review record)`` of a decision at the budget stop."""
    args = dict(get_field(action, "args") or {})
    limit = float(args.get("limit_usd") or 0.0)
    review = {"point": BUDGET, "edited_fields": [], "comment": decision.comment, "source": decision.source,
              "reviewer": decision.reviewer, "at": now(), "action": [BUDGET], "limit_usd": limit}
    if decision.type == "reject":
        raise DecisionError("'reject' is not allowed at the budget stop: add money (edit with add_usd) "
                            "or finish with what is left (approve)")
    if decision.type == "approve":
        binds = {"binds": DAY} if args.get("binds") == DAY else {}  # the card that was declined
        return {"decisions": [{"type": "approve"}]}, {**review, **binds, "decision": DECLINED, "add_usd": 0.0,
                                                      "new_limit_usd": limit}
    add = parse_add_usd(decision.args)
    if args.get("binds") == DAY:  # the day limit is what is raised: the remainder is no cap
        new_day = round(float(args.get("per_day_usd") or 0.0) + add, 6)
        edited = {"name": BUDGET, "args": {**args, "add_usd": add, "new_day_limit_usd": new_day}}
        return {"decisions": [{"type": "edit", "edited_action": edited}]}, {
            **review, "decision": EXTENDED, "binds": DAY, "edited_fields": ["add_usd"], "add_usd": add,
            "new_day_limit_usd": new_day}
    day = args.get("day_left_usd")
    if day is not None and add > float(day) + 1e-9:
        raise DecisionError(f"budget stop: ${add:.2f} does not fit in the day's remainder "
                            f"${float(day):.2f} (budget.per_day_usd)")
    new_limit = round(limit + add, 6)
    edited = {"name": BUDGET, "args": {**args, "add_usd": add, "new_limit_usd": new_limit}}
    return {"decisions": [{"type": "edit", "edited_action": edited}]}, {
        **review, "decision": EXTENDED, "edited_fields": ["add_usd"], "add_usd": add, "new_limit_usd": new_limit}


def apply_budget_review(run: dict[str, Any], review: Mapping[str, Any], budget: Any = None) -> None:
    """Store a decision of the budget stop in ``run.json``; ``budget``: the ledger of
    the running segment (a new process builds it from the run)."""
    if review.get("decision") == EXTENDED and review.get("binds") == DAY:
        new_day = float(review["new_day_limit_usd"])
        run["budget_day_limit_usd"] = max(day_limit_raise(run), new_day)
        run["budget_day_limit_day"] = _today()  # the raise is for the day it was given
        run.setdefault("budget_extensions", []).append(
            {k: review.get(k) for k in ("binds", "add_usd", "new_day_limit_usd", "at", "source", "reviewer")})
        if budget is not None:
            budget.raise_day_limit(new_day)
    elif review.get("decision") == EXTENDED:
        new_limit = float(review["new_limit_usd"])
        run["budget_limit_usd"] = max(float(run.get("budget_limit_usd") or 0.0), new_limit)
        run.setdefault("budget_extensions", []).append(
            {k: review.get(k) for k in ("add_usd", "new_limit_usd", "at", "source", "reviewer")})
        if budget is not None:
            budget.raise_limit(new_limit)
    else:
        run["budget_declined"] = True


def _today() -> str:
    from .middleware.budget import DailySpendLog

    return DailySpendLog().today()


def day_limit_raise(run: Mapping[str, Any], today: str | None = None) -> float:
    """The day limit a human gave this run at the stop (``budget_day_limit_usd``), but
    only on the day it was given (``budget_day_limit_day``): the raise answers one day's
    shortage and is not carried to later days. 0.0 when none applies."""
    value = float(run.get("budget_day_limit_usd") or 0.0)
    if value <= 0.0 or run.get("budget_day_limit_day") != (today or _today()):
        return 0.0
    return value


def per_day_usd(run: Mapping[str, Any]) -> float | None:
    """The run's own ``budget.per_day_usd`` (raised by a day extension, ``budget_day_limit_usd``);
    ``None`` means no check against the day."""
    value = ((run.get("settings") or {}).get("budget") or {}).get("per_day_usd")
    if value is None:
        return None
    return max(float(value), day_limit_raise(run))


def with_todays_day_left(run: Mapping[str, Any], request: Mapping[str, Any], *,
                         spend_log: DailySpendLog | None = None) -> dict[str, Any]:
    """A live run's budget stop may have waited for a long time: the day's remainder a
    decision is checked against is today's, not the asked one. ``spend_log``: default,
    the log of ``$TFL_DEEP_RUNS_DIR``."""
    per_day = per_day_usd(run)
    if run.get("mode") != "live" or per_day is None:
        return dict(request)
    from .middleware.budget import DailySpendLog

    left = max(0.0, (spend_log or DailySpendLog()).remaining(per_day))
    acts = [{**dict(a), "args": {**dict(get_field(a, "args") or {}), "day_left_usd": left}}
            for a in get_field(request, "action_requests") or []]
    return {**dict(request), "action_requests": acts}


def budget_summary(args: Mapping[str, Any]) -> list[str]:
    """The lines of the stop's short card."""
    if args.get("binds") == DAY:
        spent = float(args.get("per_day_usd") or 0.0) - float(args.get("day_left_usd") or 0.0)
        return [f"Сегодня потрачено {format_usd(spent)} из {format_usd(args.get('per_day_usd'))}",
                f"Осталось на сегодня {format_usd(args.get('day_left_usd'))}"]
    out = [f"Потрачено {format_usd(args.get('spent_usd'))} из {format_usd(args.get('limit_usd'))} · "
           f"доступно {format_usd(args.get('available_usd'))}"]
    if args.get("day_left_usd") is not None:
        out.append(f"На сегодня осталось {format_usd(args['day_left_usd'])}")
    return out


__all__ = [
    "BUDGET", "DAY", "DECLINED", "EXTENDED", "apply_budget_review", "day_limit_raise", "parse_add_usd", "budget_request", "budget_decision_response",
    "budget_stop_enabled", "budget_stop_on", "budget_summary", "declining_skips_the_call", "per_day_usd", "request_budget_extension", "review_switch_refusal", "format_usd",
    "with_todays_day_left",
]
