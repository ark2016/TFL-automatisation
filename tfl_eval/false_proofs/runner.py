"""Run the false-proof corpus and summarise how far false proofs get.

    python -m tfl_eval.false_proofs [--pipeline reg|cfl|dcfl|ll] [--json] [--no-counterexamples]

Per pipeline it reports how many false proofs the verifiers refuted, how many
reached each trust level, how many reached trust ABOVE ``well_formed``
(``bounded_pass`` / ``verified``), and what the verdict gate made of them: how
often the final verdict is wrong, and how often it is wrong with confidence
>= 0.6 (tfl-eval's "false confident" threshold). Cases whose verdict is right
but whose proof is false are counted in the trust columns and shown in the
table, but cannot produce a wrong verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from tfl_eval.false_proofs.corpus import CASES, PIPELINES, FalseProofCase
from tfl_eval.false_proofs.replay import (
    TRUST_ORDER,
    ReplayResult,
    check_counterexample,
    replay,
)


def run_cases(cases: list[FalseProofCase] | tuple[FalseProofCase, ...] = CASES) -> list[ReplayResult]:
    return [replay(case) for case in cases]


def summarize(results: list[ReplayResult]) -> dict[str, dict[str, int]]:
    """Counts per pipeline plus an ``"all"`` row."""
    rows: dict[str, dict[str, int]] = {}
    for key in (*PIPELINES, "all"):
        selected = [r for r in results if key == "all" or r.pipeline == key]
        row: dict[str, int] = {"cases": len(selected)}
        for trust in TRUST_ORDER:
            row[trust] = sum(1 for r in selected if r.agent_trust == trust)
        row["above_well_formed"] = sum(1 for r in selected if r.above_well_formed)
        row["wrong_verdict_cases"] = sum(1 for r in selected if r.claimed_verdict != r.true_verdict)
        row["wrong_verdict"] = sum(1 for r in selected if r.wrong_verdict)
        row["confident_wrong"] = sum(1 for r in selected if r.confident_wrong)
        rows[key] = row
    return rows


_COLUMNS = ("cases", "refuted", "not_verified", "well_formed", "bounded_pass", "verified",
            "above_well_formed", "wrong_verdict", "confident_wrong")


def _table(summary: dict[str, dict[str, int]]) -> str:
    header = "pipeline".ljust(9) + "".join(c.rjust(len(c) + 2) for c in _COLUMNS)
    lines = [header, "-" * len(header)]
    for key, row in summary.items():
        lines.append(key.ljust(9) + "".join(str(row[c]).rjust(len(c) + 2) for c in _COLUMNS))
    return "\n".join(lines)


def _case_lines(results: list[ReplayResult], checks: dict[str, tuple[bool, str]]) -> str:
    lines = []
    for r in results:
        mark = "WRONG" if r.wrong_verdict else ("ok" if r.gate_verdict == r.true_verdict else "-")
        ce = checks.get(r.case_id)
        ce_text = "" if ce is None else ("  counterexample confirmed" if ce[0] else f"  COUNTEREXAMPLE FAILED: {ce[1]}")
        lines.append(
            f"{r.case_id:38} trust={r.agent_trust!s:12} gate={r.gate_verdict!s:12} "
            f"conf={r.gate_confidence:.2f} truth={r.true_verdict:12} {mark}{ce_text}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tfl_eval.false_proofs", description=__doc__.split("\n\n")[0])
    parser.add_argument("--pipeline", choices=PIPELINES)
    parser.add_argument("--json", action="store_true", help="print the per-case results and summary as JSON")
    parser.add_argument("--no-counterexamples", action="store_true",
                        help="skip the independent counterexample checks")
    args = parser.parse_args(argv)

    cases = [c for c in CASES if args.pipeline is None or c.pipeline == args.pipeline]
    results = run_cases(cases)
    checks = {} if args.no_counterexamples else {c.id: check_counterexample(c) for c in cases}
    summary = summarize(results)
    if args.pipeline:
        summary = {k: v for k, v in summary.items() if k in (args.pipeline, "all")}

    if args.json:
        payload: dict[str, Any] = {
            "cases": [{**r.as_dict(), **({"counterexample_ok": checks[r.case_id][0]} if checks else {})}
                      for r in results],
            "summary": summary,
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(_case_lines(results, checks))
        print()
        print(_table(summary))
    return 0 if all(ok for ok, _ in checks.values()) else 1
