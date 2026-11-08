"""`tfl-eval` — run the eval-set (`docs/EVAL_SET.md`) through the four TFL
pipelines and report accuracy / confidence calibration.

    tfl-eval --systems cfl,ll [--live] [--ids cfl-01,cfl-02] [--out DIR]
    tfl-eval --systems deep [--ids reg-04,ll-17]      # tfl_deep_agent, mock replays

Without `--live`, each task runs through its pipeline's `MockRunner` when the
manifest names an existing mock fixture (`mock_task`); otherwise it is
reported as `skipped` rather than guessed at (TODO.md §7 / root CLAUDE.md:
"Никаких --live и вызовов Anthropic API" unless explicitly asked for).

`--systems deep` (opt-in, not in the default set) runs `tfl_deep_agent` on
every selected manifest entry, whatever its class: the agent gets only the
entry's `source_text`, every HITL stop is auto-approved, and the verdict is the
policy token from its `result.json` (`runners.extract_deep`). Mock mode uses
the replay `tfl_deep_agent/examples/mock/eval/<id>.json`; entries without one
are skipped. Run directories go to `<out>/deep_runs/<id>/` (`<id>-2`, ... when
that directory is already used, e.g. a second eval with the same `--out`).
`--live` with deep entries is refused when their worst case
(min(entries x per_task_usd, per_day_usd)) is above `--deep-max-usd`
(default $1.00, the per-session budget of root CLAUDE.md).

Metrics implemented in `tfl_eval.metrics`: accuracy overall / per system / on
trap tasks, a Brier score for confidence calibration, the inconclusive rate,
and the "false confident wrong" rate (confidence >= 0.6 but incorrect).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tfl_eval import metrics
from tfl_eval.runners import (
    ALL_SYSTEMS,
    DEEP_SYSTEM,
    REPO_ROOT,
    SYSTEMS,
    DeepAdapterError,
    deep_mock_script,
    deep_task_text,
    extract,
    extract_deep,
    has_mocks,
    load_deep_settings,
    make_live_runner,
    make_mock_runner,
    run_deep,
    run_pipeline,
    set_live_model_env,
    validate,
)

DEFAULT_MANIFEST = Path(__file__).resolve().parent / "manifest.json"


def load_manifest(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_ir(entry: dict) -> dict:
    ir_path = REPO_ROOT / entry["path"]
    return json.loads(ir_path.read_text(encoding="utf-8"))


def _usage_delta(after: dict | None, before: dict | None) -> dict | None:
    """Return *after* minus *before*, recursively, over the shape of
    ``UsageTracker.as_dict()`` (TODO.md §3 / reviewer finding).

    ``_run_entry`` shares one ``LiveRunner``/``LLMRunner`` (and its
    ``UsageTracker``) across every manifest entry for a system via
    ``live_runner_cache``, so ``run_pipeline``'s ``result["usage"]`` is a
    *cumulative* snapshot as of that call, not what this one entry alone
    used. Summing those cumulative snapshots (as ``_aggregate_usage`` does)
    silently multiplies out every earlier entry's usage into every later
    one (3 tasks x 10 calls each -> per-record 10/20/30, total 60 instead
    of 30). Subtracting the before/after snapshot per entry gives the
    actual per-entry delta; a missing/unknown side of a leaf (e.g. cost
    when no model in the tracker yet has a known price) is treated as 0 so
    the delta still comes out as the other side's real value.
    """
    if after is None:
        return None
    if before is None:
        return after

    def _sub(a: Any, b: Any) -> Any:
        if isinstance(a, dict) or isinstance(b, dict):
            a = a if isinstance(a, dict) else {}
            b = b if isinstance(b, dict) else {}
            return {k: _sub(a.get(k), b.get(k)) for k in (set(a) | set(b))}
        if isinstance(a, (int, float)) and not isinstance(a, bool):
            b_num = b if isinstance(b, (int, float)) and not isinstance(b, bool) else 0
            return a - b_num
        return a

    return _sub(after, before)


_API_KEY_ENV = "ANTHROPIC_API_KEY"

# Default cap for `--live --systems deep` (root CLAUDE.md: "order of $1 per session").
DEFAULT_DEEP_MAX_USD = 1.00


def _run_entry(entry: dict, live: bool, live_runner_cache: dict[str, Any]) -> dict:
    """Run one manifest entry and return its result record (never raises)."""
    record: dict[str, Any] = {
        "id": entry["id"],
        "system": entry["system"],
        "trap": bool(entry.get("trap", False)),
        "expected": entry.get("expected"),
        "source": entry.get("source"),
        "path": entry["path"],
    }

    try:
        ir = _load_ir(entry)
    except (OSError, json.JSONDecodeError) as exc:
        record.update(status="error", error=f"could not load IR: {exc}")
        return record

    errors = validate(entry["system"], ir)
    if errors:
        record.update(status="invalid_ir", error="; ".join(errors))
        return record

    system = entry["system"]
    mock_runner = None
    agent_runner = None

    if live:
        agent_runner = live_runner_cache.setdefault(system, make_live_runner(system))
    else:
        mock_task = entry.get("mock_task")
        if not mock_task or not has_mocks(system, mock_task):
            record.update(status="skipped", reason="no --live and no mock fixtures for this task")
            return record
        mock_runner = make_mock_runner(system, mock_task)

    # Snapshot the (possibly shared, cached-across-entries) usage tracker
    # before this entry's run, so its contribution can be isolated as a
    # delta afterwards instead of reported as the tracker's running total.
    tracker = getattr(agent_runner, "usage_tracker", None)
    usage_before = tracker.as_dict() if tracker is not None else None

    start = time.perf_counter()
    try:
        result = run_pipeline(system, ir, mock_runner=mock_runner, agent_runner=agent_runner)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the whole run
        record.update(status="error", error=f"{type(exc).__name__}: {exc}",
                      elapsed_s=time.perf_counter() - start)
        return record
    elapsed = time.perf_counter() - start

    extracted = extract(system, result)
    expected = entry.get("expected") or {}
    verdict = extracted["verdict"]
    verdict_correct = (verdict is not None and expected.get("verdict") is not None
                        and verdict == expected["verdict"])
    # When the manifest names a specific expected k (several Format-3 traps
    # test that a wrong-but-plausible k is rejected: ll-08 is LL(2) but not
    # SLL(2), ll-09 is not LL(2) but is LL(3), ll-15's true k is 12 (the
    # k > 10 trap), ll-18 is LL(2)) a verdict match alone is not "correct" --
    # any k would otherwise inflate trap accuracy. `expected.k == None`
    # (e.g. ll-10: "not LL(k) for all k") means no specific k is expected,
    # so k is not checked.
    expected_k = expected.get("k")
    k_correct = expected_k is None or extracted["k"] == expected_k
    correct = verdict_correct and k_correct

    record.update(
        status="ran",
        verdict=verdict,
        confidence=extracted["confidence"],
        k=extracted["k"],
        basis_trust=extracted["basis_trust"],
        verdict_gate=extracted["verdict_gate"],
        usage=_usage_delta(result.get("usage"), usage_before),
        elapsed_s=elapsed,
        correct=correct,
        k_correct=k_correct,
    )
    return record


def _fresh_run_dir(runs_dir: Path, entry_id: str) -> Path:
    """`<runs_dir>/<entry_id>`, or `<entry_id>-2`, `-3`, ... when that path is
    already taken: `hitl.start_run` refuses a directory that holds a run, so a
    second eval into the same `--out` must not reuse the first one's dirs."""
    candidate = runs_dir / entry_id
    n = 2
    while candidate.exists():
        candidate = runs_dir / f"{entry_id}-{n}"
        n += 1
    return candidate


def deep_live_worst_case_usd(n_entries: int, settings: Any) -> tuple[float, float, float]:
    """(worst case, per_task_usd, per_day_usd) of a live deep eval over
    *n_entries*: each task may spend up to its `per_task_usd`, and all of them
    together at most what the day allows (`per_day_usd`)."""
    budget = settings.budget
    per_task = float(budget.per_task_usd)
    per_day = float(budget.per_day_usd)
    return min(n_entries * per_task, per_day), per_task, per_day


def _run_deep_entry(entry: dict, live: bool, runs_dir: Path, settings: Any = None) -> dict:
    """Run one manifest entry through `tfl_deep_agent` (never raises).

    The record has `system: "deep"` and the entry's class in `task_system`;
    scoring is the same as for the pipelines (verdict and, when the manifest
    names one, k). A report for another class than the entry's
    (`class_mismatch`) is scored wrong."""
    record: dict[str, Any] = {
        "id": entry["id"],
        "system": DEEP_SYSTEM,
        "task_system": entry["system"],
        "trap": bool(entry.get("trap", False)),
        "expected": entry.get("expected"),
        "source": entry.get("source"),
        "path": entry["path"],
    }

    try:
        ir = _load_ir(entry)
        task_text = deep_task_text(ir)
    except (OSError, json.JSONDecodeError) as exc:
        record.update(status="error", error=f"could not load IR: {exc}")
        return record
    except DeepAdapterError as exc:
        record.update(status="error", error=str(exc))
        return record

    script = None
    if not live:
        script = deep_mock_script(entry["id"])
        if script is None:
            record.update(status="skipped", reason="no --live and no deep mock script for this task")
            return record

    run_dir = _fresh_run_dir(runs_dir, entry["id"])
    record["run_dir"] = str(run_dir)
    start = time.perf_counter()
    try:
        result = run_deep(task_text, run_dir, mock_script=script, live=live, settings=settings)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the whole run
        record.update(status="error", error=f"{type(exc).__name__}: {exc}",
                      elapsed_s=time.perf_counter() - start)
        return record
    elapsed = time.perf_counter() - start

    extracted = extract_deep(result, entry["system"])
    expected = entry.get("expected") or {}
    verdict = extracted["verdict"]
    verdict_correct = (verdict is not None and expected.get("verdict") is not None
                       and verdict == expected["verdict"]
                       and not extracted["class_mismatch"])
    expected_k = expected.get("k")
    k_correct = expected_k is None or extracted["k"] == expected_k

    # Only a live run spends money; a mock run's budget numbers are token
    # estimates of the scripted replies, so they are not reported as usage.
    usage = None
    if live and isinstance(extracted["budget_spent_usd"], (int, float)):
        usage = {"calls": (result.get("budget") or {}).get("calls", 0) or 0,
                 "estimated_cost_usd": extracted["budget_spent_usd"]}

    record.update(
        status="ran",
        verdict=verdict,
        confidence=extracted["confidence"],
        k=extracted["k"],
        basis_trust=extracted["basis_trust"],
        verdict_gate=extracted["verdict_gate"],
        deep_status=extracted["deep_status"],
        language_class=extracted["language_class"],
        class_mismatch=extracted["class_mismatch"],
        trust=extracted["trust"],
        human_review=extracted["human_review"],
        usage=usage,
        elapsed_s=elapsed,
        correct=verdict_correct and k_correct,
        k_correct=k_correct,
    )
    return record


def _aggregate_usage(records: list[dict]) -> dict:
    """Sum ``record["usage"]`` (a pipeline's ``UsageTracker.as_dict()``,
    TODO.md §3) across every record that ran with one -- pure aggregation,
    no new accounting: totals for calls/tokens/structured-output counters,
    and estimated cost when every contributing record's cost is known."""
    total_calls = 0
    input_tokens = output_tokens = 0
    cache_read_tokens = cache_write_tokens = 0
    total_tokens = 0
    structured_output_calls = 0
    extraction_fallback_calls = 0
    total_cost = 0.0
    cost_known = True
    n_with_usage = 0

    for r in records:
        usage = r.get("usage")
        if not usage:
            continue
        n_with_usage += 1
        total_calls += usage.get("calls", 0) or 0
        input_tokens += usage.get("input_tokens", 0) or 0
        output_tokens += usage.get("output_tokens", 0) or 0
        cache_read_tokens += usage.get("cache_read_input_tokens", 0) or 0
        cache_write_tokens += usage.get("cache_creation_input_tokens", 0) or 0
        total_tokens += usage.get("total_tokens", 0) or 0
        structured_output_calls += usage.get("structured_output_calls", 0) or 0
        extraction_fallback_calls += usage.get("extraction_fallback_calls", 0) or 0
        cost = usage.get("estimated_cost_usd")
        if cost is None:
            cost_known = False
        else:
            total_cost += cost
    cost_known = cost_known and n_with_usage > 0

    return {
        "records_with_usage": n_with_usage,
        "calls": total_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": cache_write_tokens,
        "total_tokens": total_tokens,
        "structured_output_calls": structured_output_calls,
        "extraction_fallback_calls": extraction_fallback_calls,
        "estimated_cost_usd": round(total_cost, 6) if cost_known else None,
    }


def _render_markdown(records: list[dict], report: dict, meta: dict) -> str:
    usage = _aggregate_usage(records)
    cost = usage["estimated_cost_usd"]
    cost_str = f"${cost:.4f}" if cost is not None else "n/a"
    lines = [
        "# tfl-eval report",
        "",
        f"- Generated: {meta['generated_at']}",
        f"- Systems: {', '.join(meta['systems'])}",
        f"- Live: {meta['live']} (model: {meta.get('model') or 'n/a'})",
        f"- Entries: {len(records)}",
    ]
    if usage["records_with_usage"]:
        lines.append(
            f"- Usage: {usage['calls']} calls, {usage['total_tokens']} tokens, "
            f"estimated cost ≈ {cost_str}"
        )
    lines += [
        "",
        "## Summary",
        "",
        "| Scope | Accuracy | Brier | Inconclusive | False-confident-wrong |",
        "|---|---|---|---|---|",
    ]

    def _fmt(section: dict) -> str:
        acc = section["accuracy"]["accuracy"]
        brier = section["brier"]["brier"]
        inc = section["inconclusive"]["rate"]
        fcw = section["false_confident_wrong"]["rate"]
        fmt = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "n/a"
        return f"| {fmt(acc)} | {fmt(brier)} | {fmt(inc)} | {fmt(fcw)} |"

    lines.append(f"| overall {_fmt(report['overall'])}")
    lines.append(f"| traps {_fmt(report['traps'])}")
    for system, section in report["by_system"].items():
        lines.append(f"| {system} {_fmt(section)}")

    lines += ["", "## Tasks", "", "| id | system | trap | status | verdict | expected | correct | confidence | k | time (s) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        system_s = r["system"] if not r.get("task_system") else f"{r['system']} ({r['task_system']})"
        expected_v = (r.get("expected") or {}).get("verdict")
        conf = r.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else ""
        elapsed = r.get("elapsed_s")
        elapsed_s = f"{elapsed:.2f}" if isinstance(elapsed, (int, float)) else ""
        lines.append(
            f"| {r['id']} | {system_s} | {'yes' if r.get('trap') else ''} | {r['status']} | "
            f"{r.get('verdict', '')} | {expected_v} | {r.get('correct', '')} | {conf_s} | "
            f"{r.get('k', '')} | {elapsed_s} |"
        )
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tfl-eval",
        description="Run the TFL eval-set (docs/EVAL_SET.md) through the four pipelines.",
    )
    parser.add_argument(
        "--systems", default=",".join(SYSTEMS),
        help=(f"Comma-separated subset of {ALL_SYSTEMS} (default: {','.join(SYSTEMS)}). "
              f"'{DEEP_SYSTEM}' runs tfl_deep_agent on every selected entry of any class."),
    )
    parser.add_argument("--ids", default=None, help="Comma-separated eval-set ids to restrict to.")
    parser.add_argument("--live", action="store_true", help="Call the real pipelines (Anthropic API).")
    parser.add_argument(
        "--model", default=None,
        help="Explicit TFL_MODEL_OVERRIDE for --live (default: claude-haiku-5-5, see root CLAUDE.md).",
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to manifest.json.")
    parser.add_argument(
        "--deep-settings", default=None, metavar="JSON",
        help="tfl_deep_agent settings file for --systems deep (default: tfl_deep_agent/settings.json, "
             "else the built-in defaults); its budget limits apply to every deep task.",
    )
    parser.add_argument(
        "--deep-max-usd", type=float, default=DEFAULT_DEEP_MAX_USD, metavar="USD",
        help=(f"With --live and --systems deep: refuse to start when the deep entries' worst case "
              f"(min(entries x per_task_usd, per_day_usd)) is above USD (default: {DEFAULT_DEEP_MAX_USD:.2f}, "
              f"the per-session budget of root CLAUDE.md). Narrow with --ids or lower per_task_usd "
              f"in --deep-settings."),
    )
    parser.add_argument(
        "--out", default=None,
        help="Output directory (default: .tfl_lab_runs/evals/<timestamp>/).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    for s in systems:
        if s not in ALL_SYSTEMS:
            print(f"Unknown system: {s!r} (expected one of {ALL_SYSTEMS})", file=sys.stderr)
            return 2

    ids = None
    if args.ids:
        ids = {i.strip() for i in args.ids.split(",") if i.strip()}

    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = REPO_ROOT / manifest_path
    manifest = load_manifest(manifest_path)

    entries = [e for e in manifest if e["system"] in systems]
    deep_entries = list(manifest) if DEEP_SYSTEM in systems else []
    if ids is not None:
        entries = [e for e in entries if e["id"] in ids]
        deep_entries = [e for e in deep_entries if e["id"] in ids]

    deep_settings = None
    if deep_entries or args.deep_settings:
        if DEEP_SYSTEM not in systems:
            print("--deep-settings goes with --systems deep", file=sys.stderr)
            return 2
        try:
            deep_settings = load_deep_settings(args.deep_settings)
        except DeepAdapterError as exc:
            print(f"tfl-eval: {exc}", file=sys.stderr)
            return 2

    if args.live and deep_entries:
        worst, per_task, per_day = deep_live_worst_case_usd(len(deep_entries), deep_settings)
        print(f"tfl-eval: live deep run over {len(deep_entries)} entries: worst case ${worst:.2f} "
              f"(per_task_usd {per_task:.2f}, per_day_usd {per_day:.2f}), cap --deep-max-usd "
              f"${args.deep_max_usd:.2f}", file=sys.stderr)
        if worst > args.deep_max_usd:
            print("tfl-eval: refused: the worst case is above --deep-max-usd. Narrow the set with --ids, "
                  "lower budget.per_task_usd in --deep-settings, or raise --deep-max-usd to the budget "
                  "agreed for this session.", file=sys.stderr)
            return 2

    if args.live:
        set_live_model_env(args.model, os.environ)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else REPO_ROOT / ".tfl_lab_runs" / "evals" / timestamp
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # The pipelines' live runners load `.env` into os.environ (agent_system's
    # `_load_env`, the cfl/ll orchestrators' `load_dotenv`); `tfl-deep` never
    # reads `.env`, so the deep entries get the key as it was before them.
    key_before = os.environ.get(_API_KEY_ENV)
    live_runner_cache: dict[str, Any] = {}
    records = [_run_entry(entry, args.live, live_runner_cache) for entry in entries]
    if deep_entries:
        if key_before is None:
            os.environ.pop(_API_KEY_ENV, None)
        else:
            os.environ[_API_KEY_ENV] = key_before
    records += [_run_deep_entry(entry, args.live, out_dir / "deep_runs", deep_settings)
                for entry in deep_entries]

    report = metrics.summarize(records)

    meta = {
        "generated_at": timestamp,
        "systems": systems,
        "ids": sorted(ids) if ids else None,
        "live": args.live,
        "model": os.environ.get("TFL_MODEL_OVERRIDE") if args.live else None,
        "manifest": str(manifest_path),
    }

    usage_summary = _aggregate_usage(records)
    report_json = {"meta": meta, "metrics": report, "usage": usage_summary, "records": records}
    deep_records = [r for r in records if r["system"] == DEEP_SYSTEM]
    if deep_records:
        # the deep agent's metrics per task class (its records relabelled by class)
        report_json["deep_by_class"] = metrics.summarize(
            [{**r, "system": r["task_system"]} for r in deep_records])["by_system"]
    (out_dir / "report.json").write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_render_markdown(records, report, meta), encoding="utf-8")

    n_ran = sum(1 for r in records if r["status"] == "ran")
    n_skipped = sum(1 for r in records if r["status"] == "skipped")
    n_error = sum(1 for r in records if r["status"] in ("error", "invalid_ir"))
    acc = report["overall"]["accuracy"]["accuracy"]
    print(f"tfl-eval: {len(records)} tasks -> ran={n_ran} skipped={n_skipped} error={n_error}")
    print(f"overall accuracy: {acc if acc is not None else 'n/a'}")
    if usage_summary["records_with_usage"]:
        cost = usage_summary["estimated_cost_usd"]
        cost_str = f"${cost:.4f}" if cost is not None else "n/a"
        print(
            f"usage: calls={usage_summary['calls']} total_tokens={usage_summary['total_tokens']} "
            f"estimated_cost≈{cost_str}"
        )
    print(f"report written to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
