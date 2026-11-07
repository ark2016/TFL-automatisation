"""Trust levels and confidence caps per docs/VERDICT_POLICY.md (P3).

Everything here is computed by code from the evidence ledger
(``evidence/ledger.py``); the model's words and its own confidence never raise
a trust level or a ceiling (VERDICT_POLICY §2: self-assessment can only lower
confidence).

* :func:`claim_trust` -- one claim (a report step) with its ``evidence_ids``:
  the strongest non-refuted trust among the cited records that carry a tool
  result (VERDICT_POLICY §1). **An id that is not in the ledger makes the
  claim ``not_verified``** (architecture.html, "Журнал свидетельств": a
  non-existent id makes the statement not_verified), whatever else it cites:
  a claim that leans on a fabricated record is not machine-checked. A cited
  record that is ``refuted`` marks the claim ``contradiction`` and caps it at
  0.50 (the R3 ceiling); a record without a tool result (a subagent's text
  answer, a built-in tool, a failed call) is listed as ``not_evidence``.
* :func:`evaluate_report` -- the whole report: per-step ratings, and the
  verdict's trust / ceiling from ``agent.verdict_gate`` (R1-R6: the basis is the
  strongest admissible check against the task among **all** the run's records,
  subagents included; citations only lower it), plus the final confidence
  ``min(cap, model confidence)``.
* :func:`published_verdict` -- the verdict token the result publishes; for REG
  it is read through ``agent_system/lib/result_verdict.result_verdict`` (the
  REG pipeline's own reader: ``null`` for a partial result or an unresolved
  contradiction, VERDICT_POLICY §5).

The trust taxonomy and the caps are the tool layer's (``tools/base.py``), which
imports them from ``agent_system.lib.claim_verifier``: one table for the whole
repository.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..tools.base import CONFIDENCE_CAPS, TRUST_LEVELS, confidence_cap, strongest_trust
from .ledger import EvidenceLedger, EvidenceRecord

#: VERDICT_POLICY §2: the ceiling while a contradiction stands.
CONTRADICTION_CAP = 0.50

__all__ = [
    "CONFIDENCE_CAPS",
    "CONTRADICTION_CAP",
    "TRUST_LEVELS",
    "as_records",
    "claim_trust",
    "evaluate_report",
    "final_confidence",
    "published_verdict",
    "rate_claims",
]


def _as_dict(r: Any) -> dict[str, Any]:
    if isinstance(r, EvidenceRecord):
        return r.to_dict()
    if isinstance(r, Mapping):
        return dict(r)
    raise TypeError(f"an evidence record must be a mapping or EvidenceRecord, not {type(r).__name__}")


def as_records(source: Any) -> dict[str, dict[str, Any]]:
    """``id -> record dict`` from a ledger, a sequence of records (ledger
    records, their state view ``tfl_evidence``, or plain dicts) or a mapping
    ``id -> record``. Records without an ``id`` are ignored."""
    if source is None:
        return {}
    if isinstance(source, EvidenceLedger):
        return {r.id: r.to_dict() for r in source.records()}
    if isinstance(source, Mapping):
        items = [(k, _as_dict(v)) for k, v in source.items()]
        return {str(v.get("id") or k): v for k, v in items}
    out: dict[str, dict[str, Any]] = {}
    for r in source:
        d = _as_dict(r)
        if d.get("id"):
            out.setdefault(str(d["id"]), d)
    return out


def _has_tool_result(rec: Mapping[str, Any]) -> bool:
    """The record carries a validated ToolResult (a plain dict without the
    flag counts when it has a trust label)."""
    if "has_tool_result" in rec:
        return bool(rec["has_tool_result"])
    return rec.get("trust") in TRUST_LEVELS


def _is_evidence(rec: Mapping[str, Any]) -> bool:
    """A record that can support a claim: a successful call with a tool result."""
    return bool(rec.get("ok")) and _has_tool_result(rec)


def claim_trust(evidence_ids: Iterable[str] | None, records: Any) -> dict[str, Any]:
    """Trust of one claim from the records it cites.

    Returns ``trust``, ``confidence_cap``, ``evidence_ids`` (deduplicated, in
    order), ``supporting`` (the ids whose trust gave ``trust``),
    ``unknown_ids`` (not in the ledger), ``refuted_ids``, ``not_evidence``
    (in the ledger but no tool result / failed), ``contradiction`` and
    ``reasons`` (why the trust is what it is)."""
    by_id = records if isinstance(records, dict) and all(isinstance(v, dict) for v in records.values()) \
        else as_records(records)
    ids = [str(i) for i in dict.fromkeys(evidence_ids or [])]
    unknown = [i for i in ids if i not in by_id]
    found = [by_id[i] for i in ids if i in by_id]
    refuted = [r["id"] for r in found if _is_evidence(r) and r.get("trust") == "refuted"]
    not_evidence = [r["id"] for r in found if not _is_evidence(r)]
    usable = [r for r in found if _is_evidence(r) and r.get("trust") != "refuted"]
    reasons: list[str] = []

    trust = strongest_trust(r.get("trust") for r in usable) if usable else "not_verified"
    supporting = [r["id"] for r in usable if r.get("trust") == trust] if trust != "not_verified" else []
    if not ids:
        reasons.append("no evidence cited")
    if unknown:
        reasons.append(f"cited id(s) not in the evidence ledger {unknown}: the claim is not_verified")
        trust, supporting = "not_verified", []
    if not_evidence:
        reasons.append(f"cited record(s) without a tool result {not_evidence} (subagent answer, "
                       "built-in tool or failed call) are not evidence")
    cap = confidence_cap(trust)
    contradiction = bool(refuted)
    if contradiction:
        reasons.append(f"cited record(s) {refuted} are refuted: ceiling {CONTRADICTION_CAP}")
        cap = min(cap, CONTRADICTION_CAP)
    return {"trust": trust, "confidence_cap": cap, "evidence_ids": ids, "supporting": supporting,
            "unknown_ids": unknown, "refuted_ids": refuted, "not_evidence": not_evidence,
            "contradiction": contradiction, "reasons": reasons}


def rate_claims(claims: Sequence[Mapping[str, Any]] | None, records: Any) -> list[dict[str, Any]]:
    """Each claim (``{"claim", "justification", "evidence_ids", ...}``) with
    its :func:`claim_trust` rating merged in. Fields a claim brings itself
    named like the rating (a model-supplied ``trust`` / ``confidence``) are
    overwritten: the model does not rate its own claims."""
    by_id = as_records(records)
    out = []
    for c in claims or []:
        rating = claim_trust(c.get("evidence_ids") or [], by_id)
        out.append({**{k: v for k, v in c.items() if k not in ("trust", "confidence", "confidence_cap")},
                    **rating})
    return out


def _number(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    return min(max(x, 0.0), 1.0)


def final_confidence(cap: float, model_confidence: Any = None) -> float:
    """``min(cap, model_confidence)``: the model's own confidence can only
    lower the ceiling (VERDICT_POLICY §2). A missing or unusable model value
    (not a number, NaN) leaves the ceiling."""
    cap_n = _number(cap)
    if cap_n is None:
        cap_n = CONFIDENCE_CAPS["not_verified"]
    own = _number(model_confidence)
    return cap_n if own is None else min(cap_n, own)


def published_verdict(language_class: str, gate: Mapping[str, Any]) -> str | None:
    """The verdict token the result publishes.

    REG goes through the REG pipeline's reader
    (``agent_system.lib.result_verdict.result_verdict``): ``regular`` /
    ``non_regular`` for an accepted verdict, ``None`` for ``partial`` (an
    inconclusive or unresolved one). A contradiction that R3 resolved in favour
    of a ``verified`` side keeps its verdict (``agent.verdict_gate`` leaves the
    token), so only an unresolved one is passed on as ``contradiction``. The
    other classes publish the gate's token (cfl / dcfl ``None``, ll
    ``uncertain`` when inconclusive)."""
    token = gate.get("verdict_token")
    if language_class != "reg":
        return token
    from agent_system.lib.result_verdict import result_verdict

    unresolved = bool(gate.get("contradiction")) and token == "partial"
    return result_verdict({
        "status": "success" if token in ("regular", "non_regular") else "partial",
        "verdict": token,
        "verdict_gate": {"contradiction": unresolved},
    })


def evaluate_report(report: Mapping[str, Any], records: Any, *,
                    model_confidence: Any = None,
                    formalization: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Rate a submitted report against the run's evidence ledger.

    ``records``: an :class:`EvidenceLedger` or full records (the ledger file's
    lines). The verdict gate needs each record's ``args`` / ``data``, so the
    compact state view (``tfl_evidence``) is enough for the steps but gives the
    verdict no basis.

    ``formalization``: the HITL 1 binding of the run (``hitl.formalization_binding``:
    ``status`` approved / rejected / not_held / no_ir and the approved
    ``ir_fingerprint``), passed to ``agent.verdict_gate``. With it the IR the
    human approved, not the report's own ``ir_fingerprint``, decides which
    oracle results count; without an approved IR the verdict has no basis.
    ``None``: HITL 1 is off (the report's fingerprint rule only).

    Every citation counts: the steps', the key ``claims``', the Lean block's
    (``lean.evidence_ids``) and the verdict-level ``evidence_ids``; an id not in
    the ledger anywhere is in ``unknown_ids`` and lowers the verdict.

    Returns ``steps`` and ``claims`` (each with :func:`claim_trust`), ``trust`` /
    ``confidence_cap`` of the verdict (``agent.verdict_gate``), ``confidence``
    (:func:`final_confidence` with ``model_confidence``, by default the
    report's own ``confidence`` field when it has one), ``verdict_token``,
    ``verdict`` (:func:`published_verdict`), ``basis``, ``contradiction``,
    ``verdict_gate`` (the downgrade reasons), ``unknown_ids`` /
    ``refuted_ids`` over all citations, and ``excluded_ir_mismatch``."""
    from ..agent import verdict_gate

    by_id = as_records(records)
    steps = rate_claims(report.get("steps") or [], by_id)
    claims = rate_claims(report.get("claims") or [], by_id)
    lean = report.get("lean") if isinstance(report.get("lean"), Mapping) else {}
    cited = list(dict.fromkeys([
        *(str(i) for i in report.get("evidence_ids") or []),
        *(i for s in steps for i in s["evidence_ids"]),
        *(i for c in claims for i in c["evidence_ids"]),
        *(str(i) for i in lean.get("evidence_ids") or []),
    ]))
    entries = [_gate_entry(r) for r in by_id.values() if _has_tool_result(r)]
    gate = verdict_gate(report, entries, cited, formalization=formalization)
    if model_confidence is None:
        model_confidence = report.get("confidence")
    cap = gate["confidence_cap"]
    every = claim_trust(cited, by_id)
    lang = str(report.get("language_class") or "")
    return {
        "steps": steps,
        "claims": claims,
        "trust": gate["trust"],
        "confidence_cap": cap,
        "confidence": final_confidence(cap, model_confidence),
        "model_confidence": _number(model_confidence),
        "verdict_direction": gate["direction"],
        "verdict_token": gate["verdict_token"],
        "verdict": published_verdict(lang, gate),
        "basis": gate["basis"],
        "contradiction": gate["contradiction"],
        "verdict_gate": gate["verdict_gate"],
        "excluded_ir_mismatch": gate["excluded_ir_mismatch"],
        "required_ir_fingerprint": gate.get("required_ir_fingerprint"),
        "formalization": gate.get("formalization"),
        "unknown_ids": every["unknown_ids"],
        "refuted_ids": every["refuted_ids"],
        "not_evidence": every["not_evidence"],
        "trust_source": "code (docs/VERDICT_POLICY.md) from the evidence ledger; the model's confidence "
                        "can only lower the ceiling",
    }


def _gate_entry(rec: Mapping[str, Any]) -> dict[str, Any]:
    data, args = rec.get("data"), rec.get("args")
    return {"id": rec["id"], "tool": rec.get("tool"), "ok": rec.get("ok"), "trust": rec.get("trust"),
            "trust_reason": rec.get("trust_reason"), "bounds": rec.get("bounds"), "error": rec.get("error"),
            "data": data if isinstance(data, Mapping) else {},
            "args": args if isinstance(args, Mapping) else {}}
