"""Metrics for an eval-set run: accuracy, Brier score, inconclusive rate,
false-confident-wrong rate — overall, per system, and on trap tasks.

Each metric is computed over *records*: dicts with at least
``{"system", "trap", "status", "correct", "confidence"}`` (see
`cli.py::_run_entry` for how a record is built from one manifest entry).
"""

from __future__ import annotations

from typing import Any

CONFIDENT_THRESHOLD = 0.6

# `status` values meaning "the pipeline ran and produced a definite verdict"
# vs. "no verdict could be scored" (skipped, error, or inconclusive/uncertain).
_INCONCLUSIVE_VERDICTS = {None, "inconclusive", "uncertain", "partial", "failure", "success"}


def is_attempted(record: dict) -> bool:
    """True if *record* actually ran a pipeline (not skipped/errored)."""
    return record.get("status") == "ran"


def is_inconclusive(record: dict) -> bool:
    """True if the run produced no definite constructive/destructive verdict."""
    if not is_attempted(record):
        return False
    return record.get("verdict") in _INCONCLUSIVE_VERDICTS


def is_scoreable(record: dict) -> bool:
    """True if *record* ran and returned a definite (non-inconclusive) verdict."""
    return is_attempted(record) and not is_inconclusive(record)


def accuracy(records: list[dict]) -> dict[str, Any]:
    """Accuracy = correct / scoreable, over *records* (already filtered)."""
    scoreable = [r for r in records if is_scoreable(r)]
    correct = [r for r in scoreable if r.get("correct")]
    n = len(scoreable)
    return {
        "n_attempted": sum(1 for r in records if is_attempted(r)),
        "n_scoreable": n,
        "n_correct": len(correct),
        "accuracy": (len(correct) / n) if n else None,
    }


def brier_score(records: list[dict]) -> dict[str, Any]:
    """Brier score over scoreable records with a numeric confidence.

    outcome = 1.0 if correct else 0.0; probability = confidence (already in
    [0, 1] — the pipelines' confidence is always a probability-like float,
    see docs/VERDICT_POLICY.md §2).
    """
    scored = [
        r for r in records
        if is_scoreable(r) and isinstance(r.get("confidence"), (int, float))
    ]
    if not scored:
        return {"n": 0, "brier": None}
    total = sum((float(r["confidence"]) - (1.0 if r.get("correct") else 0.0)) ** 2 for r in scored)
    return {"n": len(scored), "brier": total / len(scored)}


def inconclusive_rate(records: list[dict]) -> dict[str, Any]:
    attempted = [r for r in records if is_attempted(r)]
    inconclusive = [r for r in attempted if is_inconclusive(r)]
    n = len(attempted)
    return {
        "n_attempted": n,
        "n_inconclusive": len(inconclusive),
        "rate": (len(inconclusive) / n) if n else None,
    }


def false_confident_wrong(records: list[dict]) -> dict[str, Any]:
    """Count/rate of confident (>=0.6) but wrong verdicts among scoreable records.

    docs/EVAL_SET.md: "ложный уверенный вердикт (confidence >= 0.6) — как
    грубая ошибка", i.e. this is the headline calibration-failure metric.
    """
    scoreable = [r for r in records if is_scoreable(r)]
    wrong_confident = [
        r for r in scoreable
        if not r.get("correct")
        and isinstance(r.get("confidence"), (int, float))
        and r["confidence"] >= CONFIDENT_THRESHOLD
    ]
    n = len(scoreable)
    return {
        "n_scoreable": n,
        "n_false_confident": len(wrong_confident),
        "rate": (len(wrong_confident) / n) if n else None,
        "ids": [r.get("id") for r in wrong_confident],
    }


def summarize(records: list[dict]) -> dict[str, Any]:
    """Full metrics report: overall, per-system, and on trap tasks."""
    systems = sorted({r["system"] for r in records})
    traps = [r for r in records if r.get("trap")]

    report: dict[str, Any] = {
        "n_total": len(records),
        "overall": {
            "accuracy": accuracy(records),
            "brier": brier_score(records),
            "inconclusive": inconclusive_rate(records),
            "false_confident_wrong": false_confident_wrong(records),
        },
        "traps": {
            "accuracy": accuracy(traps),
            "brier": brier_score(traps),
            "inconclusive": inconclusive_rate(traps),
            "false_confident_wrong": false_confident_wrong(traps),
        },
        "by_system": {},
    }
    for system in systems:
        sub = [r for r in records if r["system"] == system]
        report["by_system"][system] = {
            "accuracy": accuracy(sub),
            "brier": brier_score(sub),
            "inconclusive": inconclusive_rate(sub),
            "false_confident_wrong": false_confident_wrong(sub),
        }
    return report
