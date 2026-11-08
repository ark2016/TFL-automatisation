"""``tfl-deep`` command line.

    tfl-deep (TEXT | --file task.{txt,md,pdf,png,jpg,jpeg,webp}) [--save RUN_DIR]
             [--template T] [--mock-script FILE | --live]
             [--review-mode all|formalization|auto] [--auto-approve]
             [--no-dialog] [--settings JSON]
    tfl-deep --resume RUN_DIR --decision approve|edit|reject
             [--payload JSON] [--comment TEXT] [--approve-rest] [--live] [--auto-approve] [--no-dialog]

Mock mode is the default: the agent runs its real loop and its real tools, but
every model reply is replayed from ``--mock-script`` (a JSON replay, see
``tfl_deep_agent/examples/mock/``); no request reaches the Anthropic API. A run
without ``--live`` and without a script is refused with a clear message.

``--live`` builds real ``ChatAnthropic`` models and spends money. It needs
``ANTHROPIC_API_KEY`` in the environment (this command never loads ``.env``).
Per the repository rules it is used only on the user's explicit request, with
a budget agreed beforehand. Resuming a live run needs ``--live`` again.

Human in the loop (``tfl_deep_agent.hitl``): the run stops after the
formalization (HITL 1) and at the report (HITL 2), as ``settings.hitl`` says.
On a terminal the stop is an interactive dialog; otherwise (``--no-dialog``,
a pipe, a test) the process writes ``run.json`` with ``status:
awaiting_human`` and the card ``hitl.md``, prints the resume commands and
exits with code 3; ``--resume RUN_DIR --decision ...`` continues it in a new
process. ``--review-mode`` picks which stops wait: ``all`` (default),
``formalization`` (HITL 1 waits, the report is auto-approved) or ``auto``
(every stop auto-approved; ``--auto-approve`` is its alias). An auto-approved
report is marked "без проверки человеком". ``--resume ... --decision approve
--approve-rest`` approves the pending stop and auto-approves every later one.

The run directory (``--save``, default ``<runs dir>/<run id>``) holds
``run.json``, ``checkpoints.sqlite``, ``evidence.jsonl``, the input file and
``result.{json,md,html}`` (a draft while HITL 2 waits).

Exit codes: 0 report accepted, 1 the run ended without a report (partial
result written), 2 usage / setup error, 3 waiting for a human decision.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import uuid
from pathlib import Path
from typing import Any

TEMPLATES = ["full_proof", "exam_solution", "short_verdict", "lean_appendix"]
#: the per-run review modes (``hitl.REVIEW_MODES``; kept here so the parser needs no [deep] import)
REVIEW_MODES = ["all", "formalization", "auto"]
TEXT_SUFFIXES = {".txt", ".md"}
FILE_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tfl-deep",
        description="TFL Deep Agent: solve a Theory of Formal Languages task (REG / CFL / DCFL / LL(k)).",
    )
    p.add_argument("text", nargs="?", help="task statement as text")
    p.add_argument("--file", help="task file: .txt / .md, or .pdf / .png / .jpg / .jpeg / .webp (transcribed by "
                                  "the intake role, checked by the human at HITL 1)")
    p.add_argument("--save", metavar="RUN_DIR",
                   help="run directory (run.json, checkpoints, evidence, result.{json,md,html}); "
                        "default: <runs dir>/<run id> ($TFL_DEEP_RUNS_DIR or .tfl_lab_runs)")
    p.add_argument("--template", default=None, choices=TEMPLATES,
                   help="report template (default: the mock script's, else full_proof)")
    p.add_argument("--live", action="store_true",
                   help="call the real Anthropic API (spends money; default is mock mode)")
    p.add_argument("--mock-script", metavar="FILE",
                   help="scripted model replay for a mock run (the default mode when --live is absent); "
                        "see tfl_deep_agent/examples/mock/")
    p.add_argument("--review-mode", choices=list(REVIEW_MODES), default=None,
                   help="which HITL stops wait for a human: all (default; every enabled point), formalization "
                        "(HITL 1 waits, the report is auto-approved), auto (every stop auto-approved); "
                        "auto-approved stops are marked in the report")
    p.add_argument("--auto-approve", action="store_true",
                   help="alias of --review-mode auto: approve every HITL stop without asking (eval / tests; "
                        "marked in the report); with --resume, auto-approve every later stop")
    p.add_argument("--no-dialog", action="store_true",
                   help="never ask on the terminal: stop with status awaiting_human (exit 3) instead")
    p.add_argument("--settings", metavar="JSON", help="settings file (default: tfl_deep_agent/settings.json)")
    r = p.add_argument_group("resume a run waiting for a human decision")
    r.add_argument("--resume", metavar="RUN_DIR", help="the run directory to resume")
    r.add_argument("--decision", choices=["approve", "edit", "reject"], help="the decision on the pending stop")
    r.add_argument("--payload", metavar="JSON",
                   help="edit: a JSON object of the fields to change (HITL 1: transcription / ir / class; "
                        "HITL 2: report fields); reject: the comment (a JSON string or {\"comment\": ...}); "
                        "@FILE reads it from a file")
    r.add_argument("--comment", metavar="TEXT", help="the comment for the agent (required with reject)")
    r.add_argument("--approve-rest", action="store_true",
                   help="with --decision approve: approve this stop (as your decision) and auto-approve every "
                        "later stop of the run (review mode auto from here on)")
    return p


def _fail(msg: str) -> int:
    print(f"tfl-deep: error: {msg}", file=sys.stderr)
    return EXIT_USAGE


def _read_task_file(path: str) -> Any:
    """A ``TaskInput`` for ``path`` (``intake.files.from_path``)."""
    from .intake import files as intake_files

    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in TEXT_SUFFIXES | FILE_SUFFIXES:
        raise ValueError(f"{p.name}: unsupported task file type {suffix or '(none)'}; "
                         f"use {', '.join(sorted(TEXT_SUFFIXES | FILE_SUFFIXES))}")
    if not p.is_file():
        raise ValueError(f"task file not found: {path}")
    try:
        return intake_files.from_path(p)
    except intake_files.IntakeError as exc:
        raise ValueError(f"{p.name}: {exc} ({exc.code})") from None


def _load_settings(path: str | None) -> Any:
    """``settings.load_settings`` (defaults merged under the file; a missing
    default file means the defaults). An explicit ``--settings`` must exist."""
    from . import settings as settings_mod

    if path is not None and not Path(path).is_file():
        raise ValueError(f"settings file not found: {path}")
    try:
        return settings_mod.load_settings(path)
    except settings_mod.SettingsError as exc:
        problems = "; ".join(getattr(exc, "problems", None) or [])
        raise ValueError(f"{exc}{': ' + problems if problems else ''}") from None


def new_run_id() -> str:
    return "deep-" + _dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _dialog(args: argparse.Namespace) -> Any:
    """The TTY dialog, only when both ends are a terminal and not disabled."""
    if args.no_dialog or args.auto_approve or args.review_mode == "auto":
        return None
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
    except (AttributeError, ValueError):
        return None
    from .hitl import TTYDialog

    return TTYDialog()


def _report(outcome: Any) -> int:
    """Print the outcome of a run segment and return its exit code."""
    from . import hitl

    if outcome.awaiting:
        info = outcome.pending
        print(f"tfl-deep: waiting for a human decision at {info.point} (HITL); run dir {outcome.run_dir}",
              file=sys.stderr)
        print(f"tfl-deep: the card is {outcome.run_dir / hitl.CARD_FILE}; resume with:", file=sys.stderr)
        print(hitl.resume_hint(outcome.run_dir, info.point), file=sys.stderr)
        print(f"awaiting_human: {info.point}")
        return outcome.exit_code
    result = outcome.result or {}
    if outcome.paths:
        print("tfl-deep: wrote " + ", ".join(str(p) for p in outcome.paths.values()), file=sys.stderr)
    if result.get("status") == "submitted":
        report = result["report"]
        print(f"verdict: {report['verdict']} ({report['language_class']}); evidence trust: "
              f"{result['trust']}; confidence cap {result['confidence_cap']}; review: {result.get('human_review')}")
        return outcome.exit_code
    print(f"no report: {result.get('status')}: {result.get('reason', '')}", file=sys.stderr)
    return outcome.exit_code


def _payload(text: str | None) -> Any:
    if text is None:
        return None
    if text.startswith("@"):
        path = Path(text[1:])
        if not path.is_file():
            raise ValueError(f"--payload file not found: {path}")
        return path.read_text(encoding="utf-8")
    return text


def _resume(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from . import hitl

    if (args.text or args.file or args.mock_script or args.template or args.settings or args.save
            or args.review_mode):
        parser.error("--resume takes only --decision / --payload / --comment / --approve-rest / --live / "
                     "--no-dialog / --auto-approve; the run keeps its task, script, template, settings and "
                     "review mode")
    if not args.decision:
        parser.error("--resume needs --decision approve|edit|reject")
    if args.approve_rest and args.decision != "approve":
        parser.error("--approve-rest goes only with --decision approve")
    try:
        run = hitl.load_run(args.resume)
        if run.get("mode") == "live" and args.live and not os.environ.get("ANTHROPIC_API_KEY"):
            return _fail("--live needs ANTHROPIC_API_KEY in the environment (tfl-deep never loads .env)")
        decision = hitl.parse_decision(args.decision, _payload(args.payload), comment=args.comment)
        if args.auto_approve and not args.approve_rest:
            # this decision is the human's; every later stop is auto-approved
            run.setdefault("initial_review_mode", hitl.review_mode_of(run))
            run["review_mode"] = hitl.REVIEW_AUTO
            run["auto_approve"] = True
            hitl.save_run(args.resume, run)
        if run.get("mode") == "live":
            print("tfl-deep: LIVE mode: real Anthropic API calls, this spends money.", file=sys.stderr)
        outcome = hitl.resume_run(args.resume, decision, live=args.live, dialog=_dialog(args),
                                  approve_rest=args.approve_rest)
    except (hitl.RunError, hitl.DecisionError, ValueError) as exc:
        return _fail(str(exc))
    return _report(outcome)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.resume:
        return _resume(args, parser)
    if args.decision or args.payload or args.comment or args.approve_rest:
        parser.error("--decision / --payload / --comment / --approve-rest go with --resume RUN_DIR")
    if args.auto_approve and args.review_mode not in (None, "auto"):
        parser.error(f"--auto-approve is --review-mode auto; it conflicts with --review-mode {args.review_mode}")
    if args.text and args.file:
        parser.error("give either TEXT or --file, not both")
    if args.live and args.mock_script:
        parser.error("--live and --mock-script exclude each other")
    if not args.text and not args.file and not args.mock_script:
        parser.error("give the task as TEXT or --file")

    if not args.live and not args.mock_script:
        return _fail(
            "mock mode (the default) needs --mock-script FILE: a scripted replay of the model's replies "
            "(examples: tfl_deep_agent/examples/mock/*.json). Nothing is sent to the API without --live; "
            "pass --live only with an agreed budget.")

    try:
        task = _read_task_file(args.file) if args.file else (args.text or "")
        settings = _load_settings(args.settings)
    except ValueError as exc:
        return _fail(str(exc))

    from . import agent as agent_mod
    from . import hitl
    from .intake import files as intake_files

    script = None
    if args.mock_script:
        try:
            script = agent_mod.load_mock_script(args.mock_script)
        except agent_mod.ScriptError as exc:
            return _fail(str(exc))
        if not task:
            task = str(script.get("task") or "").strip()
        if not task:
            return _fail("no task: give TEXT / --file, or put a 'task' into the mock script")
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        return _fail("--live needs ANTHROPIC_API_KEY in the environment (tfl-deep never loads .env)")
    if isinstance(task, str):
        try:
            task = intake_files.from_text(task)
        except intake_files.IntakeError as exc:
            return _fail(f"{exc} ({exc.code})")

    if args.save:
        run_dir = Path(args.save)
        run_id = None
    else:
        from .middleware.budget import default_runs_dir

        run_id = new_run_id()
        run_dir = default_runs_dir() / run_id
    if args.live:
        print("tfl-deep: LIVE mode: real Anthropic API calls, this spends money.", file=sys.stderr)
    try:
        outcome = hitl.start_run(
            run_dir, task, settings=settings, mock_script=script, live=args.live, template=args.template,
            auto_approve=args.auto_approve, review_mode=args.review_mode, dialog=_dialog(args), run_id=run_id)
    except (hitl.RunError, hitl.DecisionError, ValueError, TypeError) as exc:
        return _fail(str(exc))
    return _report(outcome)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
