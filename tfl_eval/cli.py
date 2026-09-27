"""`tfl-eval` — run the eval-set (`docs/EVAL_SET.md`) through the four TFL
pipelines and report accuracy / confidence calibration.

    tfl-eval --systems cfl,ll [--live] [--ids cfl-01,cfl-02] [--out DIR]

Without `--live`, each task runs through its pipeline's `MockRunner` when the
manifest names an existing mock fixture (`mock_task`); otherwise it is
reported as `skipped` rather than guessed at (TODO.md §7 / root CLAUDE.md:
"Никаких --live и вызовов Anthropic API" unless explicitly asked for).

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
    REPO_ROOT,
    SYSTEMS,
    extract,
    has_mocks,
    make_live_runner,
    make_mock_runner,
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


def _aggregate_usage(records: list[dict]) -> dict:
    """Sum ``record["usage"]`` (a pipeline's ``UsageTracker.as_dict()``,
    TODO.md §3) across every record that ran with one -- pure aggregation,
    no new accounting: totals for calls/tokens/structured-output counters,
    and estimated cost when every contributing record's cost is known."""
    total_calls = 0
    input_tokens = output_tokens = 0
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
        expected_v = (r.get("expected") or {}).get("verdict")
        conf = r.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) else ""
        elapsed = r.get("elapsed_s")
        elapsed_s = f"{elapsed:.2f}" if isinstance(elapsed, (int, float)) else ""
        lines.append(
            f"| {r['id']} | {r['system']} | {'yes' if r.get('trap') else ''} | {r['status']} | "
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
        help=f"Comma-separated subset of {SYSTEMS} (default: all).",
    )
    parser.add_argument("--ids", default=None, help="Comma-separated eval-set ids to restrict to.")
    parser.add_argument("--live", action="store_true", help="Call the real pipelines (Anthropic API).")
    parser.add_argument(
        "--model", default=None,
        help="Explicit TFL_MODEL_OVERRIDE for --live (default: claude-haiku-4-5, see root CLAUDE.md).",
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to manifest.json.")
    parser.add_argument(
        "--out", default=None,
        help="Output directory (default: .tfl_lab_runs/evals/<timestamp>/).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    for s in systems:
        if s not in SYSTEMS:
            print(f"Unknown system: {s!r} (expected one of {SYSTEMS})", file=sys.stderr)
            return 2

    ids = None
    if args.ids:
        ids = {i.strip() for i in args.ids.split(",") if i.strip()}

    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = REPO_ROOT / manifest_path
    manifest = load_manifest(manifest_path)

    entries = [e for e in manifest if e["system"] in systems]
    if ids is not None:
        entries = [e for e in entries if e["id"] in ids]

    if args.live:
        set_live_model_env(args.model, os.environ)

    live_runner_cache: dict[str, Any] = {}
    records = [_run_entry(entry, args.live, live_runner_cache) for entry in entries]

    report = metrics.summarize(records)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else REPO_ROOT / ".tfl_lab_runs" / "evals" / timestamp
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

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
